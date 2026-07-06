# -*- coding: utf-8 -*-
"""
core.trust.consensus 테스트 — 멀티 리뷰어 컨센서스 결합.

순수 `combine`(합의/불일치·보수적 결합·위반 병합)과 `run`(실패 격리)을 실 LLM 없이 결정적으로
검증한다. 리뷰어는 주입이라 엔진은 youtube도 모델 수도 모른다.
"""
from __future__ import annotations

from core.trust.consensus import ConsensusVerdict, combine, run
from core.trust.verify import Verdict, Violation


def _v(rec, *, passed=True, score=0.0, violations=None) -> Verdict:
    return Verdict(passed=passed, score=score, violations=violations or [], recommendation=rec)


# ── combine: 합의 ──

def test_all_approve_is_consensus_approve():
    c = combine([_v("approve"), _v("approve"), _v("approve")])
    assert c.recommendation == "approve" and c.agreement == "consensus"
    assert c.passed is True and c.votes == ["approve", "approve", "approve"]
    assert c.member_count == 3


def test_all_reject_is_consensus_reject():
    c = combine([_v("reject", passed=False), _v("reject", passed=False)])
    assert c.recommendation == "reject" and c.agreement == "consensus" and c.passed is False


# ── combine: 불일치 + 보수적 결합 ──

def test_disagreement_flagged_and_conservative_recommendation():
    # approve vs reject → disagree, 보수적으로 reject 채택
    c = combine([_v("approve"), _v("reject", passed=False)])
    assert c.agreement == "disagree"
    assert c.recommendation == "reject"        # 가장 안전한 표
    assert c.passed is False                   # 전원 통과 아님


def test_review_beats_approve_but_not_reject():
    assert combine([_v("approve"), _v("review")]).recommendation == "review"
    assert combine([_v("review"), _v("reject", passed=False)]).recommendation == "reject"


def test_score_is_max_risk():
    assert combine([_v("approve", score=0.2), _v("review", score=0.7)]).score == 0.7


def test_single_reviewer_is_trivially_consensus():
    c = combine([_v("approve")])
    assert c.agreement == "consensus" and c.recommendation == "approve"


# ── combine: 위반 병합(중복 제거) ──

def test_violations_merged_and_deduped():
    a = _v("review", violations=[Violation("ai_label", "med", "x")])
    b = _v("reject", passed=False, violations=[
        Violation("ai_label", "med", "x"),          # a와 중복
        Violation("deepfake", "high", "y"),
    ])
    c = combine([a, b])
    cats = sorted(v.category for v in c.violations)
    assert cats == ["ai_label", "deepfake"]         # 중복 1건 제거


def test_empty_is_conservative_review():
    c = combine([])
    assert c.recommendation == "review" and c.agreement == "disagree" and c.passed is False


# ── run: 실패 격리 ──

def test_run_combines_thunks():
    c = run([lambda: _v("approve"), lambda: _v("approve")])
    assert c.recommendation == "approve" and c.agreement == "consensus"


def test_run_isolates_failing_reviewer_as_review():
    def boom():
        raise RuntimeError("reviewer down")

    c = run([lambda: _v("approve"), boom])
    assert "review" in c.votes                  # 실패 리뷰어 → 보수적 review 표
    assert c.agreement == "disagree"            # approve vs review → 불일치
    assert c.recommendation == "review"
    assert c.passed is False                    # 실패 리뷰어는 미통과로 격리


def test_run_all_fail_is_review():
    def boom():
        raise ValueError("x")

    c = run([boom, boom])
    assert c.recommendation == "review" and c.agreement == "consensus"  # 둘 다 review로 일치


# ── to_dict: 감사/eval 연결 ──

def test_to_dict_shape_matches_audit_fields():
    c = combine([_v("approve", violations=[Violation("t", "low", "d")]), _v("review")])
    d = c.to_dict()
    assert set(d) == {"recommendation", "passed", "score", "agreement", "votes", "violation_count"}
    assert d["agreement"] == "disagree" and d["violation_count"] == 1
