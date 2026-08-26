# -*- coding: utf-8 -*-
"""
core.approval — HITL 승인 지점. `core.pipeline.gate()`가 낸 `GateDecision`을 최종 실행 여부로 바꾼다.

`docs/metrics.md`가 정직하게 남겨둔 미배선 지점을 채운다: *"HITL은 `GateDecision.requires_human`까지
표현되고 그 다음이 비어 있다."* 이 모듈은 **어댑터를 모른다** — youtube·research·manufacturing
전부 같은 방식으로 쓴다(엔진 레벨 관심사, `core.pipeline`과 나란히).

`ask`(사람에게 묻는 콜백)는 포트로 두고, 콘솔·Discord 버튼·Slack 승인 등 구체 구현은 갈아끼운다
(`core.llm.LLMGateway`·`core.trust.consensus.run`의 리뷰어 thunk와 같은 주입 규약). `console_ask`가
그 기본 구현이다 — `core.llm.py`가 포트(`LLMGateway`)와 기본 구현(`GeminiGateway` 등)을 한 파일에
두는 것과 같은 배치(2026-08-26 정정: 처음엔 "엔진은 콘솔을 모른다"며 `demo.py`에 넣었는데,
`run_manufacturing.py`라는 **두 번째 소비자**가 생기자 데모가 프로덕션 스크립트에 의존하는
역방향 결합이 됐다 — 그래서 포트를 정의한 이 파일로 옮겼다).

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


def console_ask(decision: GateDecision, *, input_fn: Callable[[str], str] = input) -> bool:
    """실제 터미널에서 사람에게 승인을 묻는다 — `ApprovalPrompt`의 기본(콘솔) 구현.

    `input_fn` 주입으로 결정적 테스트가 가능하다. **호출부가 스스로 조심할 것**: 자동 실행
    환경(CI·스모크 테스트)에서 이 함수를 부르면 입력을 기다리며 멈춘다 — `demo.py`는 그래서
    기본 실행 경로에서 이 함수를 부르지 않고, 대화형 진입점(`run_manufacturing.py`)만 쓴다.
    """
    v = decision.verdict
    print(f"\n⚠️  사람 판단 필요 — recommendation={v.recommendation}, score={v.score:.2f}")
    for viol in v.violations:
        print(f"   └ [{viol.severity}] {viol.category}: {viol.detail}")
    return input_fn("승인하시겠습니까? [y/N] ").strip().lower() in ("y", "yes")


__all__ = ["ApprovalOutcome", "ApprovalPrompt", "console_ask", "resolve"]
