# -*- coding: utf-8 -*-
"""
core.trust.eval 테스트 — 발행 게이트 감사 로그의 집계·모순 감사.

합성 레코드와 임시 JSONL로 결정적으로 검증한다(실 운영 로그 없이). 불변식은 원본 compliance
설계(reject⟹¬passed, approve⟹passed, score∈[0,1])에 근거한다.

**하위호환이 이 테스트의 절반이다.** 실 운영 로그 40건은 `violations` 필드가 없는 구 스키마다
(🟢 키 전수 집계로 확인). `violations is None`(미기록)과 `()`(위반 없음)을 뭉개면 그 40건이
전부 '위반 0건'으로 보이고, 새 불변식 두 개는 40건 전부를 오탐한다.
"""
from __future__ import annotations

import json

from core.trust.eval import (
    AuditRecord,
    ViolationRecord,
    check_invariants,
    evaluate,
    load_dir,
    load_jsonl,
    rule_candidates,
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


# ── 위반 카테고리: 미기록(None) vs 위반 없음(()) ──────────────────────


def _vrec(categories, **kw) -> AuditRecord:
    """카테고리를 기록한 레코드. `categories`는 (category, severity) 튜플 목록."""
    vs = tuple(ViolationRecord(c, s) for c, s in categories)
    return _rec(violations=vs, violation_count=len(vs), **kw)


def test_old_schema_record_reports_categories_as_unrecorded_not_empty():
    """구 로그(violations 키 부재)를 '위반 없음'으로 둔갑시키면 안 된다."""
    r = AuditRecord.from_dict({"title": "T", "violation_count": 6})
    assert r.violations is None and r.has_categories is False
    assert r.violation_count == 6            # 개수는 살아 있다


def test_no_violations_is_empty_tuple_not_none():
    r = AuditRecord.from_dict({"title": "T", "violations": [], "violation_count": 0})
    assert r.violations == () and r.has_categories is True


def test_from_dict_parses_violations():
    r = AuditRecord.from_dict({"violations": [{"category": "ai_labeling", "severity": "med"}]})
    assert r.violations == (ViolationRecord("ai_labeling", "med"),)
    assert r.violation_count == 1            # 키 부재 시 len()으로 도출


def test_from_dict_keeps_recorded_count_even_when_it_disagrees():
    """`len(violations)`로 덮으면 기록 버그를 모순 감사가 영영 못 잡는다."""
    r = AuditRecord.from_dict({"violations": [{"category": "x"}], "violation_count": 5})
    assert r.violation_count == 5 and len(r.violations) == 1


def test_from_dict_tolerates_malformed_violations_field():
    for raw in ("not a list", {"category": "x"}, 3):
        assert AuditRecord.from_dict({"violations": raw}).violations is None
    r = AuditRecord.from_dict({"violations": [{"category": "x"}, "junk", None]})
    assert r.violations == (ViolationRecord("x", ""),)   # dict 아닌 원소는 버린다


# ── summarize: 카테고리 집계 ──


def test_summarize_counts_categories_only_from_recorded_records():
    recs = [_vrec([("a", "high"), ("b", "low")]), _rec(violation_count=9)]   # 두 번째는 미기록
    s = summarize(recs)
    assert s["categorized_count"] == 1
    assert s["by_category"] == {"a": 1, "b": 1}
    assert s["by_severity"] == {"high": 1, "low": 1}
    assert s["violations"]["total"] == 11        # 개수 통계는 미기록분도 센다


def test_summarize_empty_has_category_keys():
    s = summarize([])
    assert s["categorized_count"] == 0 and s["by_category"] == {} and s["by_severity"] == {}


# ── check_invariants: 카테고리 기록 시에만 적용되는 두 불변식 ──


def test_count_mismatch_flagged_when_categories_recorded():
    r = _rec(violations=(ViolationRecord("x", "high"),), violation_count=5)
    assert [f["rule"] for f in check_invariants([r])] == ["violation_count_mismatch"]


def test_count_mismatch_not_flagged_for_old_schema():
    """구 로그 40건은 violations=None·violation_count>0 — 여기서 오탐하면 전부 빨개진다."""
    assert check_invariants([_rec(violation_count=6)]) == []


def test_unknown_severity_flagged():
    r = _vrec([("x", "critical")])
    f = check_invariants([r])
    assert [x["rule"] for x in f] == ["severity_enum"]
    assert "critical" in f[0]["detail"] and "x" in f[0]["detail"]


def test_recorded_categories_clean_record_has_no_findings():
    assert check_invariants([_vrec([("x", "high")], passed=False, recommendation="reject")]) == []


# ── rule_candidates: 검출 패턴 → 규칙 후보 ──


def test_no_candidates_without_recorded_categories():
    assert rule_candidates([_rec(violation_count=9)] * 5) == []


def test_candidate_needs_min_count():
    recs = [_vrec([("rare", "low")])] + [_vrec([("common", "high")]) for _ in range(3)]
    assert [c.category for c in rule_candidates(recs, min_count=3)] == ["common"]
    assert {c.category for c in rule_candidates(recs, min_count=1)} == {"rare", "common"}


def test_candidate_counts_decisions_not_violations():
    """한 결정이 같은 카테고리를 두 번 위반해도 1로 센다 — 아니면 support가 1.0을 넘는다."""
    recs = [_vrec([("dup", "low"), ("dup", "low")]) for _ in range(2)]
    c = rule_candidates(recs, min_count=1)[0]
    assert c.count == 2 and c.support == 1.0


def test_candidate_support_denominator_excludes_unrecorded():
    recs = [_vrec([("x", "low")]), _vrec([]), _rec(violation_count=3)]   # 3번째 미기록
    c = rule_candidates(recs, min_count=1)[0]
    assert c.support == 0.5      # 분모는 2(기록된 것만), 3이 아니다


def test_candidate_severity_is_max_observed():
    recs = [_vrec([("x", "low")]), _vrec([("x", "high")]), _vrec([("x", "med")])]
    assert rule_candidates(recs, min_count=1)[0].severity == "high"


def test_candidate_severity_ignores_unknown_values():
    recs = [_vrec([("x", "low")]), _vrec([("x", "critical")])]
    assert rule_candidates(recs, min_count=1)[0].severity == "low"
    assert rule_candidates([_vrec([("x", "critical")])], min_count=1)[0].severity == ""


def test_always_rejected_solo_category_is_promote_candidate():
    recs = [_vrec([("hallucinated_citation", "high")],
                  passed=False, recommendation="reject") for _ in range(3)]
    c = rule_candidates(recs)[0]
    assert c.always_rejected is True and c.solo_count == 3 and c.action == "promote"


def test_always_rejected_but_never_solo_is_observe_not_promote():
    """동시발생 ≠ 인과. 옆의 high 위반이 낸 reject를 med 규칙이 가로채면 안 된다."""
    recs = [_vrec([("high_one", "high"), ("tagalong", "med")],
                  passed=False, recommendation="reject") for _ in range(3)]
    tagalong = next(c for c in rule_candidates(recs) if c.category == "tagalong")
    assert tagalong.always_rejected is True         # 관측은 사실이고
    assert tagalong.solo_count == 0                 # 단독으론 한 번도 안 떴다
    assert tagalong.action == "observe"             # 그래서 승격 근거가 없다


def test_solo_count_counts_decisions_where_it_was_the_only_rule():
    recs = [_vrec([("x", "low")]),                       # 단독
            _vrec([("x", "low"), ("y", "low")]),         # 동반
            _vrec([("x", "low"), ("x", "low")])]         # 같은 규칙 중복 = 여전히 단독
    x = next(c for c in rule_candidates(recs, min_count=1) if c.category == "x")
    assert x.count == 3 and x.solo_count == 2


def test_never_blocking_category_is_demote_candidate():
    recs = [_vrec([("nit", "low")], passed=True, recommendation="approve") for _ in range(3)]
    c = rule_candidates(recs)[0]
    assert c.never_blocking is True and c.action == "demote"


def test_mixed_outcome_category_is_observe():
    recs = [_vrec([("x", "med")], passed=True, recommendation="approve"),
            _vrec([("x", "med")], passed=False, recommendation="reject"),
            _vrec([("x", "med")], passed=True, recommendation="review")]
    c = rule_candidates(recs)[0]
    assert (c.always_rejected, c.never_blocking, c.action) == (False, False, "observe")


def test_candidates_sorted_by_count_then_name():
    recs = ([_vrec([("b", "low")]) for _ in range(2)]
            + [_vrec([("a", "low")]) for _ in range(2)]
            + [_vrec([("c", "low")]) for _ in range(5)])
    assert [c.category for c in rule_candidates(recs, min_count=1)] == ["c", "a", "b"]


def test_evaluate_includes_rule_candidates():
    recs = [_vrec([("x", "high")], passed=False, recommendation="reject") for _ in range(3)]
    out = evaluate(recs)
    assert [c.category for c in out["rule_candidates"]] == ["x"]
    assert evaluate(recs, min_count=99)["rule_candidates"] == []
