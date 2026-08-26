# -*- coding: utf-8 -*-
"""
core.approval 테스트 — HITL 승인 지점(`GateDecision` → 최종 실행 여부).

신규 구축(원본 대응 없음 — `agents/approvals_db.py`는 Discord 결합이라 이식 대상이 아니다,
`docs/inventory.md` 참조). `ask`를 가짜 콜백으로 주입해 결정적으로 검증한다.
"""
from __future__ import annotations

from core.approval import console_ask, resolve
from core.pipeline import GateDecision, gate
from core.trust.verify import Verdict, Violation


def _verdict(rec: str, *, violations: list[Violation] | None = None) -> Verdict:
    return Verdict(passed=(rec == "approve"), score=0.0,
                   violations=violations or [], recommendation=rec)


class _StubAdapter:
    """`test_core_pipeline.py`와 동일한 최소 어댑터 — 고정된 Verdict을 낸다."""

    def __init__(self, verdict: Verdict) -> None:
        self._verdict = verdict

    def produce(self, brief): ...        # pragma: no cover
    def publish(self, asset): ...        # pragma: no cover

    def review(self, content, context=None) -> Verdict:
        return self._verdict


def _decision(rec: str, *, violations: list[Violation] | None = None) -> GateDecision:
    return gate(_StubAdapter(_verdict(rec, violations=violations)), {})


class _Unreachable:
    """호출되면 실패하는 가짜 `ask` — 룰이 이미 낸 결정을 사람에게 다시 묻지 않음을 증명."""

    def __call__(self, decision: GateDecision) -> bool:
        raise AssertionError("ask가 불릴 필요 없는 경로에서 호출됐다")


# ── 룰이 이미 낸 결정: 사람에게 안 간다 ────────────────────────────────


def test_resolve_publishes_on_rule_approve_without_asking():
    outcome = resolve(_decision("approve"), ask=_Unreachable())
    assert outcome.publish is True
    assert outcome.source == "rule_approve"


def test_resolve_blocks_on_rule_reject_without_asking():
    outcome = resolve(_decision("reject"), ask=_Unreachable())
    assert outcome.publish is False
    assert outcome.source == "rule_reject"


# ── 보류(review)만 사람에게 ────────────────────────────────────────────


def test_resolve_asks_human_only_when_requires_human():
    seen: list[GateDecision] = []

    def ask(decision: GateDecision) -> bool:
        seen.append(decision)
        return True

    decision = _decision("review")
    outcome = resolve(decision, ask=ask)

    assert seen == [decision]
    assert outcome.publish is True
    assert outcome.source == "human_approve"


def test_resolve_respects_human_rejection():
    outcome = resolve(_decision("review"), ask=lambda decision: False)
    assert outcome.publish is False
    assert outcome.source == "human_reject"


def test_resolve_passes_full_decision_to_ask():
    """`ask`는 Verdict 전체(위반 목록 포함)에 접근할 수 있어야 사람이 판단할 근거가 생긴다."""
    decision = _decision("review", violations=[Violation(category="c", severity="med", detail="d")])

    captured: list[GateDecision] = []
    resolve(decision, ask=lambda d: (captured.append(d), True)[1])

    assert captured[0].verdict.violations[0].category == "c"


# ── console_ask: 기본(콘솔) 구현 ────────────────────────────────────────


def test_console_ask_approves_on_y(capsys):
    decision = _decision("review", violations=[Violation(category="c", severity="med", detail="d")])
    approved = console_ask(decision, input_fn=lambda prompt: "y")
    out = capsys.readouterr().out

    assert approved is True
    assert "사람 판단 필요" in out
    assert "[med] c: d" in out


def test_console_ask_rejects_on_anything_else():
    decision = _decision("review")
    assert console_ask(decision, input_fn=lambda prompt: "") is False
    assert console_ask(decision, input_fn=lambda prompt: "n") is False


def test_console_ask_accepts_uppercase_yes():
    decision = _decision("review")
    assert console_ask(decision, input_fn=lambda prompt: "Y") is True
    assert console_ask(decision, input_fn=lambda prompt: "yes") is True
