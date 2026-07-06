# -*- coding: utf-8 -*-
"""
core.routing — 복잡도 기반 비용 라우터 (`LLMGateway` 데코레이터).

`core.llm`이 "모든 LLM 호출을 통과시키는 단일 seam"을 깔았다면, 이 모듈은 그 seam 위에
**두 가지 cross-cutting 관심사**를 얹는다 — 지금까지 이식 내내 "나중에 여기 꽂는다"고
남긴 주입점의 payoff다:

  1. **비용 라우팅** — 요청의 *복잡도*를 추정해 모델 티어(저렴/균형/고성능)를 고른다.
     쉬운 일에 비싼 모델을 쓰지 않고, 어려운 일에 싼 모델로 실패하지 않게.
  2. **관측성** — 매 호출의 (모델·티어·토큰·비용·근거)를 `CallRecord`로 남긴다.
     비용은 한 곳(이 래퍼)에서만 계측되므로 어댑터 코드는 손대지 않는다.

설계의 핵심: **`CostRouter` 자신이 `LLMGateway`를 구현한다.** 즉 기존 게이트웨이를 감싸는
드롭인 데코레이터다 — 어댑터는 자기가 `GeminiGateway`를 쓰는지 `CostRouter(GeminiGateway())`를
쓰는지 모른 채 동일한 `complete` 포트로 동작한다(엔진/어댑터 코드 불변, 라우팅·계측만 추가).

정직성 메모:
  - 가격표(`DEFAULT_PRICING`)와 비용 공식은 운영 코드 `youtube-agent/agents/claude_runner.py`의
    `_PRICING`/`_estimate_cost`에서 **그대로 상속**했다(내가 지어낸 수치가 아님). 다만 공시가는
    바뀌므로 **최신 요금으로 검증·갱신 후 사용**해야 하며, 가격표·라우트는 전부 주입 가능하다
    (신모델 `claude-opus-4-8`/`claude-sonnet-5` 등은 현재가와 함께 설정으로 교체).
  - 토큰 수는 게이트웨이 포트가 provider usage를 노출하지 않는 현 단계에서 **문자 길이 기반
    근사**다(아래 `estimate_tokens`). 실측 usage 연동은 포트 확장(Phase 2+) 과제로 남긴다.
  - 복잡도 분류(`classify`)는 투명한 **v1 휴리스틱**(키워드·길이·구조화 여부)이다. 학습 기반
    추정으로 교체 가능하도록 순수 함수로 격리했다.
"""
from __future__ import annotations

import enum
import json
import logging
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from core.llm import LLMGateway

log = logging.getLogger("mint.core.routing")


class Tier(enum.Enum):
    """비용/성능 티어. 라우트 테이블이 이 티어를 구체 모델 id로 매핑한다."""

    CHEAP = "cheap"       # 기계적·짧은 작업(요약/분류/추출) → 최저가 모델
    BALANCED = "balanced"  # 기본값 — 대부분의 생성/대화
    DEEP = "deep"         # 분석/설계/디버그/추론 등 고난도 → 최상위 모델


# ── 가격표 · 비용 공식 (운영 claude_runner._PRICING / _estimate_cost 상속) ──────────
# USD per 1M tokens. 출처: youtube-agent/agents/claude_runner.py (주석 "Anthropic 공시").
# 공시가는 변동하므로 사용 전 최신 요금으로 검증할 것. 라우터에 주입해 교체 가능.
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5":          {"in": 1.00, "out": 5.00},
    "claude-haiku-4-5-20251001": {"in": 1.00, "out": 5.00},
    "claude-sonnet-4-6":         {"in": 3.00, "out": 15.00},
    "claude-opus-4-7":           {"in": 15.00, "out": 75.00},
}

# 가격표에 없는 모델의 보수적 폴백 단가(원본 _estimate_cost의 기본값과 동일: sonnet급).
_FALLBACK_PRICE = {"in": 3.00, "out": 15.00}

# 기본 라우트 — 위 가격표에 단가가 있는(=출처가 붙은) 모델로만 구성.
# 최신 세대(claude-opus-4-8 / claude-sonnet-5)로 올리려면 현재가와 함께 route·pricing을 주입.
DEFAULT_ROUTE: dict[Tier, str] = {
    Tier.CHEAP: "claude-haiku-4-5-20251001",
    Tier.BALANCED: "claude-sonnet-4-6",
    Tier.DEEP: "claude-opus-4-7",
}


def estimate_cost(model: str, in_tokens: int, out_tokens: int, *,
                  pricing: dict[str, dict[str, float]] = DEFAULT_PRICING) -> float:
    """(입력·출력 토큰, 단가표) → USD 추정. 원본 `_estimate_cost` 공식과 동일."""
    price = pricing.get(model, _FALLBACK_PRICE)
    return (in_tokens * price["in"] + out_tokens * price["out"]) / 1_000_000


def estimate_tokens(text: str) -> int:
    """문자 길이 기반 토큰 근사(≈4자/토큰). 게이트웨이가 실측 usage를 주지 않는 현 단계용.

    실측이 아니라 **비용 추정용 근사**임을 분명히 한다 — provider usage 연동 시 대체된다.
    빈 문자열도 최소 0 토큰으로 처리한다.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


# ── 복잡도 분류 (v1 휴리스틱, 순수 함수) ────────────────────────────────────────
# 고난도 신호: 분석/설계/추론성 작업. 저난도 신호: 기계적 변환 작업.
_DEEP_HINTS = re.compile(
    r"분석|설계|아키텍|디버그|디버깅|추론|전략|리팩터|리팩토링|증명|평가|비교|검증|최적화|계획|"
    r"architect|design|debug|reason|analy|strateg|refactor|prove|evaluate|complex|optimi|plan\b",
    re.I,
)
_CHEAP_HINTS = re.compile(
    r"요약|분류|추출|태그|번역|포맷|라벨|목록|정리|"
    r"summar|classif|extract|\btag|translate|format|label|\blist\b",
    re.I,
)

# 길이 임계(문자 수, system+prompt 합산). 넘으면 상향, 밑돌면 하향 신호.
_LEN_HIGH = 2000
_LEN_LOW = 200

# 점수 → 티어 경계. |score|>=2에서 티어가 갈린다.
_UP = 2
_DOWN = -2


@dataclass(frozen=True)
class Classification:
    """복잡도 분류 결과. `score`와 `reasons`로 결정을 설명 가능하게 한다."""

    tier: Tier
    score: int
    reasons: list[str] = field(default_factory=list)


def classify(system: str, prompt: str, *, override: Tier | None = None,
             json_mode: bool = False) -> Classification:
    """(system, prompt) → 복잡도 티어. `override`가 주어지면 그것을 우선한다.

    투명한 가산 휴리스틱: 고난도 키워드는 +, 저난도 키워드·짧은 길이·구조화(json)는 −.
    최종 점수로 티어를 가른다. 근거(`reasons`)를 남겨 관측 로그에서 왜 그 티어인지 보이게 한다.
    """
    if override is not None:
        return Classification(tier=override, score=0, reasons=["override"])

    text = f"{system}\n{prompt}"
    score = 0
    reasons: list[str] = []

    n_deep = len(_DEEP_HINTS.findall(text))
    if n_deep:
        score += 2 * min(n_deep, 2)          # 최대 +4
        reasons.append(f"deep-kw x{n_deep}")

    n_cheap = len(_CHEAP_HINTS.findall(text))
    if n_cheap:
        score -= 2 * min(n_cheap, 2)          # 최대 −4
        reasons.append(f"cheap-kw x{n_cheap}")

    length = len(text)
    if length >= _LEN_HIGH:
        score += 1
        reasons.append("long")
    elif length <= _LEN_LOW:
        score -= 1
        reasons.append("short")

    if json_mode:
        score -= 1                            # 구조화 출력 = 기계적 성향
        reasons.append("json")

    if score >= _UP:
        tier = Tier.DEEP
    elif score <= _DOWN:
        tier = Tier.CHEAP
    else:
        tier = Tier.BALANCED
    return Classification(tier=tier, score=score, reasons=reasons)


# ── 관측 레코드 · sink ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CallRecord:
    """LLM 호출 1건의 계측 레코드(관측성). sink로 흘려보내 로깅·집계한다."""

    model: str
    tier: str
    complexity_score: int
    in_tokens: int
    out_tokens: int
    cost_usd: float
    temperature: float
    json_mode: bool
    reasons: list[str] = field(default_factory=list)


# sink = 레코드 소비자. 파일 로깅·메모리 수집·메트릭 전송 등 무엇이든 주입 가능.
Sink = Callable[[CallRecord], None]


def jsonl_sink(path: str | Path, *, clock: Callable[[], str]) -> Sink:
    """레코드를 JSONL 파일에 한 줄씩 append 하는 sink. 타임스탬프는 `clock` 주입(결정적).

    파일 append 실패가 LLM 호출을 깨지 않도록, 쓰기 오류는 삼키고 로깅만 한다
    (관측성은 부가기능 — 주 경로를 막지 않는다).
    """
    target = Path(path)

    def _write(rec: CallRecord) -> None:
        row = asdict(rec)
        row["ts"] = clock()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as exc:  # 디스크/권한 오류 등 — 주 경로를 막지 않는다
            log.warning("cost-router sink write 실패: %s", exc)

    return _write


def aggregate(records: list[CallRecord]) -> dict:
    """레코드 목록 → 비용 리포트(총합 + 모델별·티어별 분해). 관측성의 payoff."""
    report: dict = {
        "calls": len(records),
        "in_tokens": 0,
        "out_tokens": 0,
        "cost_usd": 0.0,
        "by_model": {},
        "by_tier": {},
    }
    for rec in records:
        report["in_tokens"] += rec.in_tokens
        report["out_tokens"] += rec.out_tokens
        report["cost_usd"] += rec.cost_usd
        for key, bucket in (("by_model", rec.model), ("by_tier", rec.tier)):
            slot = report[key].setdefault(bucket, {"calls": 0, "cost_usd": 0.0})
            slot["calls"] += 1
            slot["cost_usd"] += rec.cost_usd
    report["cost_usd"] = round(report["cost_usd"], 6)
    for key in ("by_model", "by_tier"):
        for slot in report[key].values():
            slot["cost_usd"] = round(slot["cost_usd"], 6)
    return report


# ── 라우터 (LLMGateway 데코레이터) ─────────────────────────────────────────────

# complete가 라우팅을 요청받는 신호(model 자리). 이 값이면 복잡도로 모델을 고른다.
AUTO = "auto"


class CostRouter:
    """복잡도 기반 라우팅 + 비용 계측을 더하는 `LLMGateway` 데코레이터.

    - `model=AUTO`(또는 빈 값)로 호출하면 `classify`로 티어를 정하고 `route`로 모델을 고른다.
    - 구체 모델을 명시하면 라우팅 없이 그대로 통과시키되, **계측은 동일하게** 남긴다.
    어느 경우든 실제 호출은 감싼 게이트웨이(`gateway`)에 위임하므로 프로바이더에 무관하다.

    라우트/가격/분류기/sink를 전부 주입받으므로, youtube를 모르는 것은 물론 **Claude조차 몰라도**
    동작한다(토이 게이트웨이 + 토이 라우트로 테스트 가능 = 엔진 범용성 증명).
    """

    def __init__(self, gateway: LLMGateway, *,
                 route: dict[Tier, str] = DEFAULT_ROUTE,
                 pricing: dict[str, dict[str, float]] = DEFAULT_PRICING,
                 classifier: Callable[..., Classification] = classify,
                 sink: Sink | None = None) -> None:
        self._gw = gateway
        self._route = route
        self._pricing = pricing
        self._classify = classifier
        self._sink = sink

    @property
    def available(self) -> bool:
        """감싼 게이트웨이의 가용성을 그대로 위임한다."""
        return self._gw.available

    def route_for(self, system: str, prompt: str, *, model: str = AUTO,
                  json_mode: bool = True) -> tuple[str, Classification | None]:
        """실제 호출 없이 '이 요청이 어떤 모델로 갈지'만 계산한다(설명·미리보기용)."""
        if not model or model == AUTO:
            cls = self._classify(system, prompt, json_mode=json_mode)
            return self._route[cls.tier], cls
        return model, None

    def complete(self, system: str, prompt: str, *, model: str = AUTO,
                 temperature: float = 0.4, json_mode: bool = True) -> str:
        chosen, cls = self.route_for(system, prompt, model=model, json_mode=json_mode)
        tier_label = cls.tier.value if cls else "explicit"
        score = cls.score if cls else 0
        reasons = cls.reasons if cls else ["explicit-model"]

        text = self._gw.complete(system, prompt, model=chosen,
                                 temperature=temperature, json_mode=json_mode)

        in_tokens = estimate_tokens(system) + estimate_tokens(prompt)
        out_tokens = estimate_tokens(text)
        rec = CallRecord(
            model=chosen, tier=tier_label, complexity_score=score,
            in_tokens=in_tokens, out_tokens=out_tokens,
            cost_usd=estimate_cost(chosen, in_tokens, out_tokens, pricing=self._pricing),
            temperature=temperature, json_mode=json_mode, reasons=reasons,
        )
        self._emit(rec)
        return text

    def _emit(self, rec: CallRecord) -> None:
        if self._sink is None:
            return
        try:
            self._sink(rec)
        except Exception as exc:  # 관측성 실패가 LLM 호출을 깨서는 안 됨
            log.warning("cost-router sink 실패: %s", exc)
