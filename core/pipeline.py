# -*- coding: utf-8 -*-
"""
core.pipeline — 플랫폼 어댑터의 produce → review → publish 계약 + 발행 게이트.

엔진은 이 3단 계약으로만 어댑터를 다룬다. 플랫폼별 내부(스크립트 작성·렌더·업로드)는
어댑터 안에 갇히고, 엔진/관측성/HITL은 계약만 본다.

현재 구현 상태:
  - produce : ✅ youtube(단계별 이식) · research(검색→페치→요약)
  - review  : ✅ adapters.youtube.review.ComplianceReviewer · adapters.research.review.CitationReviewer
  - publish : ✅ youtube(업로드 백엔드 주입) · research(다이제스트 sink 주입)

**계약 정제 이력(2026-07-10).** 원래 `review(content, seo)`였다 — `seo`는 youtube 어휘이고,
"2번째 어댑터가 일반화를 강제할 때 정제한다"고 이 자리에 적어 두었다. research 어댑터가 그 시점을
가져왔다(리서치에 SEO는 없다). 그래서 `seo` → `context`로 넓혔다:

  - youtube  : context = SEO 메타 dict (제목·설명·태그)
  - research : context = 근거 본문(`{"bodies": [...]}`) 또는 None

의미는 "검증에 필요한 부가 자료이며, **해석은 어댑터의 몫**"이다. 엔진은 그 안을 들여다보지 않는다.

**발행 전 가드레일(2026-07-10).** 게이트가 세 가지를 더 한다:

  1. **감사 로그** — 결정을 `AuditRecord` 호환 dict로 남긴다(`sink` 주입). 그전까지 게이트는
     아무 기록도 남기지 않았고, 그래서 `core.trust.eval`은 *원본 운영 로그*만 감사할 수 있었다.
     **우리가 만든 게이트는 감사 대상이 아니었다.** 이제 gate → jsonl → `eval.evaluate` →
     `observability.render_gate_report`로 루프가 닫힌다. 레코드는 위반 **카테고리**까지 남긴다
     (원본은 개수만 남겨서 "어떤 규칙이 터졌나"를 물을 수 없었다 — `audit_record` 참조).
  2. **HITL 지점 명시** — `requires_human`이 "사람이 봐야 한다"를 1급으로 표현한다.
     `proceed=False`만으로는 *거절*과 *보류*가 구분되지 않는다.
  3. **컨센서스 배선** — `gate_consensus`가 N개 리뷰어를 교차검증해 보수적으로 결합한다.
     그전까지 `core.trust.consensus`는 엔진에만 있고 발행 경로에서 아무도 부르지 않았다.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from core.trust.consensus import ConsensusVerdict
from core.trust.consensus import run as run_consensus
from core.trust.verify import Verdict

log = logging.getLogger("mint.core.pipeline")

#: 감사 레코드 sink — `AuditRecord` 호환 dict 한 건을 받는다.
AuditSink = Callable[[dict], None]


@runtime_checkable
class PlatformAdapter(Protocol):
    """플랫폼 어댑터 3단 계약. 구현: `YouTubeAdapter` · `ResearchAdapter`."""

    def produce(self, brief: Any) -> Any:
        """브리프/질의 → 플랫폼 자산(영상·다이제스트 등)."""
        ...

    def review(self, content: Any, context: Any = None) -> Verdict:
        """발행 전 검증 → Verdict. `context`는 검증에 필요한 부가 자료(해석은 어댑터의 몫)."""
        ...

    def publish(self, asset: Any) -> Any:
        """자산 → 플랫폼 게시(업로드·발행)."""
        ...


@dataclass(frozen=True)
class GateDecision:
    """발행 게이트 판정 — 엔진이 어댑터 review로부터 내린 진행/보류 결정."""

    proceed: bool           # True면 publish 진행 가능
    verdict: Verdict        # 근거가 된 검증 결과
    agreement: str = ""     # 컨센서스 경로에서만 채워짐("consensus" | "disagree")

    @property
    def requires_human(self) -> bool:
        """사람이 판단해야 하는가. **거절과 보류는 다르다** — 보류만 사람에게 올린다."""
        return self.verdict.recommendation == "review"

    @property
    def rejected(self) -> bool:
        """규칙이 명확히 거절했는가(사람을 부를 것도 없다)."""
        return self.verdict.recommendation == "reject"


def audit_record(decision: GateDecision, *, ts: str = "",
                 channel: str = "", title: str = "") -> dict[str, Any]:
    """게이트 결정 → `core.trust.eval.AuditRecord` 호환 dict (순수).

    필드는 `AuditRecord.from_dict`가 읽는 것과 정확히 짝을 이룬다 — 그래야 우리가 남긴 로그를
    우리 eval 하니스가 그대로 감사할 수 있다(모순 감사 포함).

    **`violations`는 카테고리·심각도만 남긴다.** 원본 로그는 `violation_count`만 남겨서
    "어떤 규칙이 터졌나"를 영영 물을 수 없었다(로그 40건 전수 확인). 이제 물을 수 있다.
    `detail`은 제외한다 — 위반 상세는 콘텐츠 조각을 담을 수 있고, 감사 로그는 콘텐츠 보관소가
    아니다. 규칙 후보 추출에 필요한 것은 *무엇이 몇 번 터졌나*이지 본문이 아니다.
    """
    v = decision.verdict
    return {
        "ts": ts,
        "channel": channel,
        "title": title,
        "passed": v.passed,
        "score": v.score,
        "recommendation": v.recommendation,
        "agreement": decision.agreement,
        "violation_count": len(v.violations),
        "violations": [{"category": x.category, "severity": x.severity} for x in v.violations],
    }


def jsonl_audit_sink(path: str | Path, *, clock: Callable[[], str]) -> AuditSink:
    """감사 레코드를 JSONL에 append 하는 sink. 시각은 `clock` 주입(결정적 테스트).

    **감사 기록 실패가 발행 판단을 깨지 않는다** — 쓰기 오류는 삼키고 로깅만 한다
    (`core.routing.jsonl_sink`와 같은 규약: 관측은 주 경로를 막지 않는다).
    """
    target = Path(path)

    def _write(row: dict[str, Any]) -> None:
        row = {**row, "ts": row.get("ts") or clock()}
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001 — 감사 실패가 게이트를 막지 않는다
            log.warning("감사 로그 기록 실패: %s", exc)

    return _write


def _emit(decision: GateDecision, sink: AuditSink | None,
          channel: str, title: str) -> None:
    """감사 sink 호출. sink가 터져도 게이트 결정은 그대로 반환된다."""
    if sink is None:
        return
    try:
        sink(audit_record(decision, channel=channel, title=title))
    except Exception as exc:  # noqa: BLE001
        log.warning("감사 sink 실패: %s", exc)


def _decide(verdict: Verdict, agreement: str = "") -> GateDecision:
    """정책 한 곳: `approve`만 진행. `review`(HITL 대기)·`reject`는 보류."""
    return GateDecision(proceed=verdict.recommendation == "approve",
                        verdict=verdict, agreement=agreement)


def gate(adapter: PlatformAdapter, content: Any, context: Any = None, *,
         sink: AuditSink | None = None, channel: str = "", title: str = "") -> GateDecision:
    """엔진 발행 게이트: 어댑터의 review 결과로 발행 여부를 정한다.

    엔진은 어댑터 구체 타입(youtube/research/...)을 **모른다** — `PlatformAdapter` 포트로만 다룬다.
    `context`도 들여다보지 않고 어댑터에 그대로 넘긴다(youtube는 SEO 메타, research는 근거 본문).
    정책: `recommendation == "approve"`일 때만 진행. `"review"`(HITL 대기)·`"reject"`는 보류.
    (Trust ② HITL 게이트를 엔진 레벨에서 표현 — 애매하면 멈추고 사람에게.)

    `sink`를 주면 결정이 감사 로그로 남는다 — `core.trust.eval`이 읽는 바로 그 형식이다.
    """
    decision = _decide(adapter.review(content, context))
    _emit(decision, sink, channel, title)
    return decision


def gate_consensus(reviewers: list[Callable[[], Verdict]], *,
                   sink: AuditSink | None = None, channel: str = "",
                   title: str = "") -> GateDecision:
    """N개 리뷰어를 교차검증해 보수적으로 결합한 뒤 발행 여부를 정한다.

    리뷰어는 **thunk**(`() -> Verdict`)로 받는다 — 엔진은 어떤 모델·규칙이 몇 개 도는지 모른다.
    한 리뷰어가 터져도 컨센서스는 멈추지 않고 그 표를 보수적 `review`로 대체한다
    (`core.trust.consensus.run`). 리뷰어들이 갈리면(`disagree`) 그 사실이 감사 로그에 남는다.

    빈 리뷰어 목록은 보수적으로 보류(`review`)한다 — 아무도 검증하지 않은 것을 통과시키지 않는다.
    """
    cv: ConsensusVerdict = run_consensus(reviewers)
    decision = _decide(_verdict_of(cv), agreement=cv.agreement)
    _emit(decision, sink, channel, title)
    return decision


def _verdict_of(cv: ConsensusVerdict) -> Verdict:
    """`ConsensusVerdict` → `Verdict` (순수). agreement는 `GateDecision`이 따로 들고 간다."""
    return Verdict(passed=cv.passed, score=cv.score,
                   violations=list(cv.violations), recommendation=cv.recommendation)


__all__ = [
    "AuditSink", "GateDecision", "PlatformAdapter",
    "audit_record", "gate", "gate_consensus", "jsonl_audit_sink",
]
