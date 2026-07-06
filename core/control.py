# -*- coding: utf-8 -*-
"""
core.control — 자연어 → 의도(intent) 라우팅 엔진 (플랫폼 무관).

`youtube-agent/agents/control.py`에서 **라우팅 메커니즘만** 엔진으로 추출한다. 원본은 라우팅
로직과 youtube intent 어휘·정규식 룰·파이프라인 배선(`dispatch`)이 한 파일에 섞여 있었다
(inventory 8장의 "경계 혼재"). 여기서는:

  - **엔진(core)** = "무엇을 라우팅하는지 모르는" 범용 라우터. 규칙 목록과 화이트리스트를 받아
    룰 매칭 → (미매칭 시) LLM 폴백 → 화이트리스트 검증으로 `Command`를 만든다. LLM 호출은
    `core.llm` 게이트웨이를 재사용(프로바이더 무관).
  - **어댑터(adapters/youtube/control.py)** = youtube의 intent 어휘·룰·폴백 프롬프트를 정의하고
    이 라우터를 구성한다.

원본과의 의도적 일반화: 룰 경로 동작은 동일(원본 parity). LLM 폴백의 `source`는 프로바이더에
묶이지 않도록 "gemini"→"llm"으로 바꾼다(이 경로는 비결정이라 특성화 대상).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from core.gateway import default_gemini
from core.llm import LLMGateway

log = logging.getLogger("mint.core.control")


@dataclass
class Command:
    """라우팅 결과. `source`: "rule"(결정적) | "llm"(폴백). 원본 `Command`와 동일 형태."""

    intent: str
    args: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    source: str = "rule"
    explain: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Rule:
    """단일 라우팅 규칙 — 패턴 매칭 시 intent를 배정하고, extract로 args를 뽑는다.

    extract(text)는 args dict를 돌려주거나, 매칭을 무효화하려면 `Command`(예: unknown)를 돌려준다.
    """

    pattern: re.Pattern
    intent: str
    extract: Callable[[str], Any] | None = None


class IntentRouter:
    """규칙 목록 + 화이트리스트로 자연어를 `Command`로 라우팅. LLM 폴백은 게이트웨이 주입."""

    def __init__(self, rules: list[Rule], known_intents: set[str], *,
                 fallback_system: str = "", fallback_model: str = "gemini-2.5-flash",
                 gateway: LLMGateway | None = None) -> None:
        self.rules = rules
        self.known_intents = known_intents
        self.fallback_system = fallback_system
        self.fallback_model = fallback_model
        self.gateway = gateway

    def parse_rule(self, text: str) -> Command | None:
        """룰 매칭(결정적). 미매칭이면 None. 원본 `parse_rule`과 동일 흐름."""
        text = (text or "").strip()
        if not text:
            return None
        for rule in self.rules:
            if rule.pattern.search(text):
                result = rule.extract(text) if rule.extract else {}
                if isinstance(result, Command):   # 매칭 무효화(예: 쿼리 비어있음 → unknown)
                    return result
                return Command(intent=rule.intent, args=result, confidence=0.95,
                               source="rule", explain=f"rule={rule.pattern.pattern}")
        return None

    def parse_llm(self, text: str) -> Command:
        """LLM 폴백 — 게이트웨이로 intent+args JSON 추출 후 화이트리스트 검증."""
        gw = self.gateway if self.gateway is not None else default_gemini()
        if not gw.available:
            return Command(intent="unknown", source="llm", explain="LLM 게이트웨이 미가용")
        try:
            raw = gw.complete(self.fallback_system, text,
                              model=self.fallback_model, temperature=0.1)
            data = json.loads(raw)
        except Exception as exc:
            log.warning(f"LLM parse 실패: {exc}")
            return Command(intent="unknown", source="llm", explain=f"파싱 실패: {exc}")

        intent = str(data.get("intent", "unknown"))
        if intent not in self.known_intents:
            return Command(intent="unknown", source="llm", confidence=0.0,
                           explain=f"알 수 없는 intent: {intent}")
        return Command(
            intent=intent,
            args=data.get("args") or {},
            confidence=float(data.get("confidence", 0.6)),
            source="llm",
            explain=str(data.get("explain", ""))[:200],
        )

    def parse(self, text: str, *, allow_llm: bool = True) -> Command:
        """룰 매칭 → 미매칭 시 LLM 폴백 → 그래도 안되면 unknown. 원본 `parse`와 동일 흐름."""
        rule = self.parse_rule(text)
        if rule and rule.intent != "unknown":
            return rule
        if not allow_llm:
            return rule or Command(intent="unknown", source="rule",
                                   explain="룰 매칭 실패 (LLM 폴백 비활성화)")
        return self.parse_llm(text)


def describe(cmd: Command) -> str:
    """사용자에게 보여줄 매핑 결과 1~2줄. 원본 `describe`와 동일(단 source 라벨 rule/llm)."""
    if cmd.intent == "unknown":
        return f"❓ 인식 실패 — `{cmd.explain}`. `/help` 또는 슬래시 명령을 직접 사용하세요."
    args_str = ", ".join(f"{k}={v!r}" for k, v in cmd.args.items()) or "(기본값)"
    src = {"rule": "🟢룰", "llm": "🟡AI"}.get(cmd.source, cmd.source)
    return f"{src} `{cmd.intent}({args_str})`  · 신뢰도 {cmd.confidence:.2f}"


# ── dispatch: intent → 액션 실행 (핸들러 주입) ─────────────────────────────────
# 핸들러 = Command를 받아 결과 dict를 돌려주는 콜러블. 엔진은 **어떤 intent가 무엇을 하는지
# 모른다** — youtube가 파이프라인을 핸들러로 주입한다. 원본 `dispatch`의 intent별 if/elif
# 배선을 "라우팅 메커니즘(엔진) ↔ 핸들러 맵(어댑터)"으로 가른 것.
Handler = Callable[[Command], dict]


def dispatch(cmd: Command, handlers: dict[str, Handler], *,
             unknown_msg: str = "unknown intent",
             unsupported_msg: str = "디스패처 미구현") -> dict:
    """`Command` → 주입된 핸들러 실행. 원본 `dispatch`의 반환 규약을 보존한다.

    - `unknown` intent → `{"ok": False, "msg": unknown_msg}` (원본과 동일)
    - 핸들러 없음(미이식 파이프라인 등) → `{"ok": False, "msg": f"{unsupported_msg}: {intent}"}`
      (원본의 "디스패처 미구현: …"과 동일 — 없는 것을 정직하게 표시)
    - 있음 → `handler(cmd)` 결과를 그대로 반환(핸들러가 `{"ok": …}` 형태를 책임진다)
    """
    if cmd.intent == "unknown":
        return {"ok": False, "msg": unknown_msg}
    handler = handlers.get(cmd.intent)
    if handler is None:
        return {"ok": False, "msg": f"{unsupported_msg}: {cmd.intent}"}
    return handler(cmd)
