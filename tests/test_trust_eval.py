# -*- coding: utf-8 -*-
"""
core.trust.eval 테스트 — 발행 게이트 감사 로그의 집계·모순 감사.

합성 레코드와 임시 JSONL로 결정적으로 검증한다(실 운영 로그 없이). 불변식은 원본 compliance
설계(reject⟹¬passed, approve⟹passed, score∈[0,1])에 근거한다.
"""
from __future__ import annotations

import json

from core.trust.eval import (
    AuditRecord,
    check_invariants,
    evaluate,
    load_dir,
    load_jsonl,
    summarize,
)


def _rec(**kw) -> AuditRecord:
    base = dict(ts="2026-05-07T07:00:00", channel="alpha", title="t",
                passed=True, score=0.3, recommendation="approve",
                agreement="consensus", violation_count=2)
    base.update(kw)
    return AuditRecord(**base)


# ── AuditRecord.from_dict: 관대한 파싱 ──

def test_from_dict_fills_defaults():
    r = AuditRecord.from_dict({"title": "x"})
    assert r.title == "x" and r.passed is False and r.recommendation == "review"
    assert r.score == 0.0 and r.violation_count == 0


def test_from_dict_coerces_types():
    r = AuditRecord.from_dict({"score": "0.8", "violation_count": "6", "passed": 1})
    assert r.score == 0.8 and r.violation_count == 6 and r.passed is True


# ── load_jsonl / load_dir ──

def test_load_jsonl_parses_and_skips_bad_lines(tmp_path):
    f = tmp_path / "compliance_audit_2026-05-07.jsonl"
    f.write_text(
        '{"title": "a", "passed": true, "recommendation": "approve"}\n'
        "not json at all\n"
        "\n"
        '{"title": "b", "passed": false, "recommendation": "reject"}\n',
        encoding="utf-8",
    )
    recs = load_jsonl(f)
    assert [r.title for r in recs] == ["a", "b"]        # 손상 줄·빈 줄 건너뜀


def test_load_jsonl_missing_file_is_empty(tmp_path):
    assert load_jsonl(tmp_path / "nope.jsonl") == []


def test_load_dir_globs_in_order(tmp_path):
    (tmp_path / "compliance_audit_2026-05-08.jsonl").write_text(
        '{"title": "later"}\n', encoding="utf-8")
    (tmp_path / "compliance_audit_2026-05-07.jsonl").write_text(
        '{"title": "earlier"}\n', encoding="utf-8")
    (tmp_path / "other.jsonl").write_text('{"title": "ignored"}\n', encoding="utf-8")
    recs = load_dir(tmp_path)
    assert [r.title for r in recs] == ["earlier", "later"]   # 파일명 순, 패턴 외 무시


# ── summarize: 집계 지표 ──

def test_summarize_empty():
    s = summarize([])
    assert s["count"] == 0 and s["pass_rate"] == 0.0 and s["by_recommendation"] == {}


def test_summarize_metrics():
    recs = [
        _rec(passed=True, score=0.2, recommendation="approve", violation_count=1, ts="2026-05-07T07:00:00"),
        _rec(passed=False, score=0.8, recommendation="reject", violation_count=6, ts="2026-05-09T07:00:00", channel="beta"),
        _rec(passed=True, score=0.5, recommendation="review", violation_count=3, ts="2026-05-08T07:00:00"),
    ]
    s = summarize(recs)
    assert s["count"] == 3
    assert s["pass_rate"] == round(2 / 3, 4)
    assert s["by_recommendation"] == {"approve": 1, "reject": 1, "review": 1}
    assert s["by_channel"] == {"alpha": 2, "beta": 1}
    assert s["date_range"] == ["2026-05-07T07:00:00", "2026-05-09T07:00:00"]
    assert s["score"]["min"] == 0.2 and s["score"]["max"] == 0.8
    assert s["violations"]["total"] == 10 and s["violations"]["max"] == 6


def test_summarize_by_agreement_skips_empty():
    recs = [_rec(agreement="consensus"), _rec(agreement="")]
    assert summarize(recs)["by_agreement"] == {"consensus": 1}


# ── check_invariants: 자체 모순 감사 ──

def test_clean_records_have_no_findings():
    recs = [
        _rec(passed=True, recommendation="approve", score=0.1),
        _rec(passed=False, recommendation="reject", score=0.9),
        _rec(passed=True, recommendation="review", score=0.5),
    ]
    assert check_invariants(recs) == []


def test_reject_but_passed_is_flagged():
    f = check_invariants([_rec(recommendation="reject", passed=True)])
    assert len(f) == 1 and f[0]["rule"] == "reject_implies_not_passed"


def test_approve_but_not_passed_is_flagged():
    f = check_invariants([_rec(recommendation="approve", passed=False)])
    assert f[0]["rule"] == "approve_implies_passed"


def test_score_out_of_range_flagged():
    rules = {x["rule"] for x in check_invariants([_rec(score=1.5, passed=True, recommendation="approve")])}
    assert "score_range" in rules


def test_unknown_recommendation_flagged():
    f = check_invariants([_rec(recommendation="maybe", passed=True)])
    assert any(x["rule"] == "recommendation_enum" for x in f)


def test_negative_violation_count_flagged():
    f = check_invariants([_rec(violation_count=-1)])
    assert any(x["rule"] == "violation_count_nonneg" for x in f)


def test_finding_carries_ts_and_title():
    f = check_invariants([_rec(recommendation="reject", passed=True, title="Bad One", ts="T1")])
    assert f[0]["ts"] == "T1" and f[0]["title"] == "Bad One"


# ── evaluate: 통합 ──

def test_evaluate_bundles_summary_and_findings():
    recs = [_rec(recommendation="reject", passed=True)]      # 모순 1건
    out = evaluate(recs)
    assert out["summary"]["count"] == 1
    assert len(out["invariant_findings"]) == 1


def test_evaluate_roundtrips_from_jsonl(tmp_path):
    f = tmp_path / "compliance_audit_2026-05-07.jsonl"
    f.write_text(json.dumps({
        "ts": "2026-05-07T07:08:49", "channel": "alpha", "title": "T",
        "passed": False, "score": 0.8, "recommendation": "reject",
        "agreement": "consensus", "violation_count": 6,
    }) + "\n", encoding="utf-8")
    out = evaluate(load_jsonl(f))
    assert out["summary"]["count"] == 1
    assert out["invariant_findings"] == []                  # 정합(reject & ¬passed)
