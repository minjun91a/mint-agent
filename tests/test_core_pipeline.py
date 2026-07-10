# -*- coding: utf-8 -*-
"""
core.pipeline 테스트 — 발행 게이트 + 가드레일(감사 로그 · HITL 지점 · 컨센서스 배선).

핵심: 게이트가 남긴 감사 로그를 **우리 eval 하니스가 그대로 읽는다**. 그전까지 게이트는 아무
기록도 남기지 않아, `core.trust.eval`은 원본 운영 로그만 감사할 수 있었다(우리 게이트는 감사
대상이 아니었다). 그 루프를 관통 테스트로 닫는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.pipeline import (
    GateDecision,
    PlatformAdapter,
    audit_record,
    gate,
    gate_consensus,
    jsonl_audit_sink,
)
from core.trust.eval import load_jsonl, evaluate
from core.trust.verify import Verdict, Violation

CLOCK = lambda: "2026-07-10T09:00:00"  # noqa: E731


def _verdict(rec: str, *, passed: bool = True, score: float = 0.0,
             violations: list[Violation] | None = None) -> Verdict:
    return Verdict(passed=passed, score=score, violations=violations or [], recommendation=rec)


class _StubAdapter:
    """PlatformAdapter 계약만 만족하는 어댑터 — 엔진은 구체 타입을 모른다."""

    def __init__(self, verdict: Verdict) -> None:
        self._verdict = verdict
        self.seen: list = []

    def produce(self, brief): ...        # pragma: no cover
    def publish(self, asset): ...        # pragma: no cover

    def review(self, content, context=None) -> Verdict:
        self.seen.append((content, context))
        return self._verdict


# ── 정책: approve만 통과 ────────────────────────────────────────────


def test_gate_approves_only_on_approve():
    assert gate(_StubAdapter(_verdict("approve")), {}).proceed is True
    assert gate(_StubAdapter(_verdict("review")), {}).proceed is False
    assert gate(_StubAdapter(_verdict("reject")), {}).proceed is False


def test_gate_satisfies_platform_adapter_port():
    assert isinstance(_StubAdapter(_verdict("approve")), PlatformAdapter)


def test_gate_passes_context_through_untouched():
    adapter = _StubAdapter(_verdict("approve"))
    gate(adapter, {"a": 1}, {"bodies": ["b"]})
    assert adapter.seen == [({"a": 1}, {"bodies": ["b"]})]


# ── HITL 지점: 거절과 보류는 다르다 ─────────────────────────────────


def test_requires_human_only_for_review():
    """`proceed=False`만으로는 거절과 보류가 구분되지 않는다 — 보류만 사람에게 올린다."""
    held = gate(_StubAdapter(_verdict("review")), {})
    rejected = gate(_StubAdapter(_verdict("reject")), {})

    assert held.requires_human is True and held.rejected is False
    assert rejected.requires_human is False and rejected.rejected is True


def test_approved_needs_neither():
    d = gate(_StubAdapter(_verdict("approve")), {})
    assert d.requires_human is False and d.rejected is False


# ── 순수: audit_record ──────────────────────────────────────────────


def test_audit_record_maps_verdict_fields():
    d = GateDecision(proceed=False, verdict=_verdict(
        "reject", passed=False, score=0.45,
        violations=[Violation("x", "high"), Violation("y", "low")]))
    row = audit_record(d, ts="T", channel="ch", title="t")

    assert row == {"ts": "T", "channel": "ch", "title": "t", "passed": False,
                   "score": 0.45, "recommendation": "reject", "agreement": "",
                   "violation_count": 2}


def test_audit_record_carries_agreement_from_consensus():
    d = GateDecision(proceed=False, verdict=_verdict("review"), agreement="disagree")
    assert audit_record(d)["agreement"] == "disagree"


# ── 감사 로그 sink ──────────────────────────────────────────────────


def test_gate_writes_audit_row(tmp_path: Path):
    sink = jsonl_audit_sink(tmp_path / "audit.jsonl", clock=CLOCK)
    gate(_StubAdapter(_verdict("approve", passed=True)), {}, sink=sink,
         channel="research", title="다이제스트")

    rows = [json.loads(l) for l in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["recommendation"] == "approve"
    assert rows[0]["channel"] == "research"
    assert rows[0]["ts"] == "2026-07-10T09:00:00"


def test_gate_without_sink_writes_nothing(tmp_path: Path):
    gate(_StubAdapter(_verdict("approve")), {})
    assert list(tmp_path.iterdir()) == []


def test_sink_creates_missing_directory(tmp_path: Path):
    sink = jsonl_audit_sink(tmp_path / "nested" / "audit.jsonl", clock=CLOCK)
    gate(_StubAdapter(_verdict("approve")), {}, sink=sink)
    assert (tmp_path / "nested" / "audit.jsonl").exists()


def test_sink_appends_across_calls(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    sink = jsonl_audit_sink(path, clock=CLOCK)
    gate(_StubAdapter(_verdict("approve")), {}, sink=sink)
    gate(_StubAdapter(_verdict("reject", passed=False)), {}, sink=sink)

    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_audit_failure_does_not_break_the_gate():
    """감사 기록 실패가 발행 판단을 깨지 않는다 — 관측은 주 경로를 막지 않는다."""
    def boom(row):
        raise RuntimeError("disk full")

    d = gate(_StubAdapter(_verdict("approve")), {}, sink=boom)
    assert d.proceed is True


def test_unwritable_path_is_logged_not_raised(tmp_path: Path):
    # 디렉터리를 파일 자리에 두어 쓰기를 실패시킨다.
    target = tmp_path / "blocked"
    target.mkdir()
    sink = jsonl_audit_sink(target, clock=CLOCK)
    assert gate(_StubAdapter(_verdict("approve")), {}, sink=sink).proceed is True


# ── 컨센서스 배선 ───────────────────────────────────────────────────


def test_gate_consensus_all_approve_proceeds():
    d = gate_consensus([lambda: _verdict("approve"), lambda: _verdict("approve")])
    assert d.proceed is True
    assert d.agreement == "consensus"


def test_gate_consensus_one_reject_blocks_everything():
    """보수적 결합: 한 명이라도 거절하면 발행하지 않는다."""
    d = gate_consensus([lambda: _verdict("approve"),
                        lambda: _verdict("reject", passed=False),
                        lambda: _verdict("approve")])
    assert d.proceed is False
    assert d.verdict.recommendation == "reject"
    assert d.agreement == "disagree"


def test_gate_consensus_disagreement_goes_to_human():
    d = gate_consensus([lambda: _verdict("approve"), lambda: _verdict("review")])
    assert d.requires_human is True
    assert d.agreement == "disagree"


def test_gate_consensus_isolates_a_failing_reviewer():
    """리뷰어 하나가 터져도 컨센서스는 멈추지 않고, 그 표는 보수적 review가 된다."""
    def boom() -> Verdict:
        raise RuntimeError("모델 다운")

    d = gate_consensus([lambda: _verdict("approve"), boom])
    assert d.proceed is False
    assert d.requires_human is True


def test_gate_consensus_with_no_reviewers_holds():
    """아무도 검증하지 않은 것을 통과시키지 않는다."""
    d = gate_consensus([])
    assert d.proceed is False
    assert d.requires_human is True


def test_gate_consensus_writes_agreement_to_audit(tmp_path: Path):
    sink = jsonl_audit_sink(tmp_path / "a.jsonl", clock=CLOCK)
    gate_consensus([lambda: _verdict("approve"), lambda: _verdict("reject", passed=False)],
                   sink=sink, channel="yt")

    row = json.loads((tmp_path / "a.jsonl").read_text(encoding="utf-8").strip())
    assert row["agreement"] == "disagree"          # 불일치가 감사 로그에 남는다
    assert row["recommendation"] == "reject"


# ── ★ 루프가 닫힌다: gate → jsonl → eval → 리포트 ──────────────────


def test_gate_audit_log_is_readable_by_our_own_eval_harness(tmp_path: Path):
    """그전까지 eval은 **원본 운영 로그**만 감사할 수 있었다 — 우리 게이트는 감사 대상이 아니었다.

    이제 게이트가 남긴 로그를 우리 eval 하니스가 그대로 읽고, 모순 감사까지 돌린다.
    """
    path = tmp_path / "gate_audit.jsonl"
    sink = jsonl_audit_sink(path, clock=CLOCK)

    gate(_StubAdapter(_verdict("approve", passed=True)), {}, sink=sink, channel="research")
    gate(_StubAdapter(_verdict("reject", passed=False, score=0.3,
                               violations=[Violation("citation_out_of_range", "high")])),
         {}, sink=sink, channel="research")
    gate_consensus([lambda: _verdict("approve"), lambda: _verdict("review")], sink=sink)

    report = evaluate(load_jsonl(path))

    assert report["summary"]["count"] == 3
    assert report["summary"]["by_recommendation"] == {"approve": 1, "reject": 1, "review": 1}
    assert report["summary"]["by_channel"]["research"] == 2
    # reject 1건만 passed=False. review는 passed가 자유다(불변식이 강제하는 건 approve/reject뿐).
    assert report["summary"]["pass_rate"] == pytest.approx(2 / 3, abs=1e-4)
    # 게이트가 자기 규칙과 모순되지 않는다: reject⟹¬passed · approve⟹passed · score∈[0,1]
    assert report["invariant_findings"] == []


def test_our_gate_log_records_disagreement_for_the_report(tmp_path: Path):
    path = tmp_path / "a.jsonl"
    sink = jsonl_audit_sink(path, clock=CLOCK)
    gate_consensus([lambda: _verdict("approve"), lambda: _verdict("approve")], sink=sink)
    gate_consensus([lambda: _verdict("approve"), lambda: _verdict("review")], sink=sink)

    summary = evaluate(load_jsonl(path))["summary"]
    assert summary["by_agreement"] == {"consensus": 1, "disagree": 1}


def test_single_reviewer_gate_leaves_agreement_blank(tmp_path: Path):
    """단일 리뷰어 경로엔 '합의'라는 개념이 없다 — 없는 신호를 지어내지 않는다."""
    path = tmp_path / "a.jsonl"
    gate(_StubAdapter(_verdict("approve")), {}, sink=jsonl_audit_sink(path, clock=CLOCK))

    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["agreement"] == ""
    assert evaluate(load_jsonl(path))["summary"]["by_agreement"] == {}
