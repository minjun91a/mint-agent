# -*- coding: utf-8 -*-
"""
core.pipeline — 플랫폼 어댑터의 produce → review → publish 계약(골격).

엔진은 이 3단 계약으로만 어댑터를 다룬다. 플랫폼별 내부(스크립트 작성·렌더·업로드)는
어댑터 안에 갇히고, 엔진/관측성/HITL은 계약만 본다.

현재 구현 상태:
  - review  : ✅ adapters.youtube.review.ComplianceReviewer (→ core.trust.Verdict)
  - produce : 🔄 내부 모듈 이식 진행 중 (captions 등 단계별)
  - publish : ⬜ 예정 (uploader 이식)

적정 고도(브리프 8장): 메서드 시그니처는 youtube 기준이다. 2번째 어댑터(research/digest)가
일반화를 강제할 때 입력 타입(brief/asset)을 정제한다. 지금 억지로 일반화하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from core.trust.verify import Verdict


@runtime_checkable
class PlatformAdapter(Protocol):
    """플랫폼 어댑터 3단 계약. 현재 유일 구현 대상은 youtube."""

    def produce(self, brief: Any) -> Any:
        """브리프/스크립트 → 플랫폼 자산(영상 등). (구현 진행 중)"""
        ...

    def review(self, content: Any, seo: Any) -> Verdict:
        """발행 전 검증 → Verdict. (구현됨: adapters.youtube.review)"""
        ...

    def publish(self, asset: Any) -> Any:
        """자산 → 플랫폼 게시(업로드). (구현 예정)"""
        ...


@dataclass(frozen=True)
class GateDecision:
    """발행 게이트 판정 — 엔진이 어댑터 review로부터 내린 진행/보류 결정."""

    proceed: bool      # True면 publish 진행 가능
    verdict: Verdict   # 근거가 된 검증 결과


def gate(adapter: PlatformAdapter, content: Any, seo: Any) -> GateDecision:
    """엔진 발행 게이트: 어댑터의 review 결과로 발행 여부를 정한다.

    엔진은 어댑터 구체 타입(youtube/research/...)을 **모른다** — `PlatformAdapter` 포트로만 다룬다.
    정책: `recommendation == "approve"`일 때만 진행. `"review"`(HITL 대기)·`"reject"`는 보류.
    (Trust ② HITL 게이트를 엔진 레벨에서 표현 — 애매하면 멈추고 사람에게.)
    """
    verdict = adapter.review(content, seo)
    return GateDecision(proceed=verdict.recommendation == "approve", verdict=verdict)
