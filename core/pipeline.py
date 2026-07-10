# -*- coding: utf-8 -*-
"""
core.pipeline — 플랫폼 어댑터의 produce → review → publish 계약(골격).

엔진은 이 3단 계약으로만 어댑터를 다룬다. 플랫폼별 내부(스크립트 작성·렌더·업로드)는
어댑터 안에 갇히고, 엔진/관측성/HITL은 계약만 본다.

현재 구현 상태:
  - review  : ✅ adapters.youtube.review.ComplianceReviewer · adapters.research.review.CitationReviewer
  - produce : ✅ youtube(단계별 이식) · research(검색→페치→요약)
  - publish : 🔄 youtube(백엔드 주입) · research(예정)

**계약 정제 이력(2026-07-10).** 원래 `review(content, seo)`였다 — `seo`는 youtube 어휘이고,
"2번째 어댑터가 일반화를 강제할 때 정제한다"고 이 자리에 적어 두었다. research 어댑터가 그 시점을
가져왔다(리서치에 SEO는 없다). 그래서 `seo` → `context`로 넓혔다:

  - youtube  : context = SEO 메타 dict (제목·설명·태그)
  - research : context = 근거 본문(`{"bodies": [...]}`) 또는 None

의미는 "검증에 필요한 부가 자료이며, **해석은 어댑터의 몫**"이다. 엔진은 그 안을 들여다보지 않는다.
기존 호출은 전부 positional이라 동작은 불변이다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from core.trust.verify import Verdict


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

    proceed: bool      # True면 publish 진행 가능
    verdict: Verdict   # 근거가 된 검증 결과


def gate(adapter: PlatformAdapter, content: Any, context: Any = None) -> GateDecision:
    """엔진 발행 게이트: 어댑터의 review 결과로 발행 여부를 정한다.

    엔진은 어댑터 구체 타입(youtube/research/...)을 **모른다** — `PlatformAdapter` 포트로만 다룬다.
    `context`도 들여다보지 않고 어댑터에 그대로 넘긴다(youtube는 SEO 메타, research는 근거 본문).
    정책: `recommendation == "approve"`일 때만 진행. `"review"`(HITL 대기)·`"reject"`는 보류.
    (Trust ② HITL 게이트를 엔진 레벨에서 표현 — 애매하면 멈추고 사람에게.)
    """
    verdict = adapter.review(content, context)
    return GateDecision(proceed=verdict.recommendation == "approve", verdict=verdict)
