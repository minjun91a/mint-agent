# -*- coding: utf-8 -*-
"""
core.approval — HITL 승인 지점. `core.pipeline.gate()`가 낸 `GateDecision`을 최종 실행 여부로 바꾼다.

`docs/metrics.md`가 정직하게 남겨둔 미배선 지점을 채운다: *"HITL은 `GateDecision.requires_human`까지
표현되고 그 다음이 비어 있다."* 이 모듈은 **어댑터를 모른다** — youtube·research·manufacturing
전부 같은 방식으로 쓴다(엔진 레벨 관심사, `core.pipeline`과 나란히).

**의도적으로 여기 없는 것: 실제 입출력.** `ask`(사람에게 묻는 콜백)를 포트로만 두고, 콘솔·Discord
버튼·Slack 승인 등 구체 구현은 호출부가 주입한다(`core.llm.LLMGateway`·`core.trust.consensus.run`의
리뷰어 thunk와 같은 주입 규약). CLI 구현은 다음 슬라이스에서 `demo.py`에 붙는다.

정책은 한 곳: `proceed`(rule-approve)는 사람 없이 진행, `rejected`는 사람 없이 중단, **오직
`requires_human`(review)만** `ask`를 부른다. 룰이 이미 낸 결정을 사람에게 다시 묻지 않는다 —
그게 "애매한 것만 사람에게"라는 게이트의 존재 이유다(거절과 보류를 구분한 이유와 같다).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from core.pipeline import GateDecision

Source = Literal["rule_approve", "rule_reject", "human_approve", "human_reject"]

#: 사람에게 결정을 묻는 콜백 — `GateDecision`을 보여주고 승인 여부(bool)를 받는다.
ApprovalPrompt = Callable[[GateDecision], bool]


@dataclass(frozen=True)
class ApprovalOutcome:
    """게이트 결정 + (필요시) 사람 판단을 합친 최종 결과."""

    publish: bool
    source: Source


def resolve(decision: GateDecision, *, ask: ApprovalPrompt) -> ApprovalOutcome:
    """게이트 결정을 최종 실행 여부로 바꾼다. `requires_human`일 때만 `ask`를 부른다.

    - `proceed=True`(룰 승인) → 사람 없이 발행. `ask`는 호출되지 않는다.
    - `rejected=True`(룰 거절) → 사람 없이 중단. `ask`는 호출되지 않는다
      (거절은 보류가 아니다 — `GateDecision.requires_human`이 애초에 그 구분을 위해 있다).
    - 그 외(`requires_human=True`, 보류) → `ask(decision)`을 불러 사람 판단을 받는다.
    """
    if decision.proceed:
        return ApprovalOutcome(publish=True, source="rule_approve")
    if decision.rejected:
        return ApprovalOutcome(publish=False, source="rule_reject")

    approved = ask(decision)
    return ApprovalOutcome(publish=approved,
                           source="human_approve" if approved else "human_reject")


__all__ = ["ApprovalOutcome", "ApprovalPrompt", "resolve"]
