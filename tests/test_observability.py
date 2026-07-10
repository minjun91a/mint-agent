# -*- coding: utf-8 -*-
"""
core.observability 테스트 — 계측 데이터 → Markdown 리포트 렌더링.

순수·결정적. 합성 레코드로 리포트 구조·수치·정직성 표기(근사/실측·미가격·모순)를 고정한다.
"""
from __future__ import annotations

from core.observability import dashboard, render_cost_report, render_gate_report
from core.routing import CallRecord
from core.trust.eval import AuditRecord, ViolationRecord


def _call(model="claude-opus-4-7", tier="deep", cost=0.02, src="measured",
          in_tok=100, out_tok=50) -> CallRecord:
    return CallRecord(model=model, tier=tier, complexity_score=0, in_tokens=in_tok,
                      out_tokens=out_tok, cost_usd=cost, temperature=0.4,
                      json_mode=True, reasons=[], token_source=src)


def _audit(**kw) -> AuditRecord:
    base = dict(ts="2026-05-07T07:00:00", channel="alpha", title="t", passed=True,
                score=0.3, recommendation="approve", agreement="consensus", violation_count=2)
    base.update(kw)
    return AuditRecord(**base)


# ── render_cost_report ──

def test_cost_report_empty():
    assert "호출 없음" in render_cost_report([])


def test_cost_report_measured_vs_estimated_and_cost():
    recs = [_call(src="measured", cost=0.02), _call(src="estimated", cost=0.03)]
    md = render_cost_report(recs)
    assert "호출** 2건 (실측 1 · 근사 1)" in md
    assert "0.050000" in md                     # 총 비용 합
    assert "### 티어별" in md and "### 모델별" in md


def test_cost_report_flags_unpriced():
    recs = [_call(cost=0.02), _call(cost=None)]
    md = render_cost_report(recs)
    assert "미가격 1건 제외" in md               # 미가격 정직 표기


def test_cost_report_tier_and_model_rows():
    recs = [_call(model="claude-opus-4-7", tier="deep"),
            _call(model="claude-haiku-4-5", tier="cheap")]
    md = render_cost_report(recs)
    assert "| deep |" in md and "| cheap |" in md
    assert "| claude-opus-4-7 |" in md and "| claude-haiku-4-5 |" in md


# ── render_gate_report ──

def test_gate_report_empty():
    assert "감사 레코드 없음" in render_gate_report([])


def test_gate_report_pass_rate_and_distributions():
    recs = [
        _audit(passed=True, recommendation="approve", agreement="consensus"),
        _audit(passed=False, recommendation="reject", agreement="disagree"),
    ]
    md = render_gate_report(recs)
    assert "결정** 2건" in md
    assert "통과율** 50.0%" in md
    assert "approve=1" in md and "reject=1" in md
    assert "consensus=1" in md and "disagree=1" in md


def test_gate_report_trims_float_noise_in_score_range():
    """min/max는 원시값이라 0.44999999999999996처럼 뜬다 — 리포트에 잡음을 흘리지 않는다."""
    md = render_gate_report([_audit(score=0.15 * 3), _audit(score=0.0)])
    assert "max 0.45" in md and "0.4499" not in md


def test_gate_report_flags_invariant_findings():
    # reject인데 passed=true → 모순 1건이 리포트에 ⚠️로 뜬다
    md = render_gate_report([_audit(recommendation="reject", passed=True)])
    assert "모순 감사** 1건" in md and "⚠️" in md
    assert "reject_implies_not_passed" in md


def test_gate_report_clean_shows_no_findings():
    md = render_gate_report([_audit(recommendation="approve", passed=True)])
    assert "모순 감사** 0건" in md and "✅ 없음" in md


# ── render_gate_report: 터진 규칙 · 규칙 후보 ──

def _vaudit(categories, **kw) -> AuditRecord:
    vs = tuple(ViolationRecord(c, s) for c, s in categories)
    return _audit(violations=vs, violation_count=len(vs), **kw)


def test_gate_report_says_categories_unrecorded_for_old_schema():
    """구 로그만 있으면 '규칙 표'를 지어내지 말고 답할 수 없다고 말한다."""
    md = render_gate_report([_audit(violation_count=6)])
    assert "카테고리를 기록한 레코드가 없다" in md
    assert "### 터진 규칙" not in md


def test_gate_report_lists_fired_rules_with_severity():
    md = render_gate_report([_vaudit([("ai_labeling", "med"), ("banned_phrase", "high")])])
    assert "### 터진 규칙" in md
    assert "| ai_labeling | med | 1 | 0 | 100% |" in md      # 함께 떴다 → 단독 0
    assert "| banned_phrase | high | 1 | 0 | 100% |" in md


def test_gate_report_shows_severity_even_below_candidate_threshold():
    """후보에 못 든 규칙도 심각도는 안다 — '—'로 적으면 아는 것을 모른다고 적는 것이다."""
    md = render_gate_report([_vaudit([("rare", "high")])], min_count=3)
    assert "| rare | high | 1 | 1 | 100% |" in md
    assert "3회 이상 반복된 규칙 없음" in md


def test_gate_report_declares_mixed_schema_denominator():
    md = render_gate_report([_vaudit([("x", "low")]), _audit(violation_count=4)])
    assert "2건 중 **1건**만 카테고리를 기록했다" in md
    assert "| x | low | 1 | 1 | 100% |" in md      # 분모는 1(기록된 것만)


def test_gate_report_no_violations_is_not_unrecorded():
    md = render_gate_report([_vaudit([])])
    assert "1건 모두 무위반" in md
    assert "카테고리를 기록한 레코드가 없다" not in md


def test_gate_report_promotes_rule_that_rejects_on_its_own():
    recs = [_vaudit([("hallucinated_citation", "high")],
                    passed=False, recommendation="reject") for _ in range(3)]
    md = render_gate_report(recs)
    assert "### 규칙 후보 (3회 이상)" in md
    assert "사전 차단 후보" in md
    assert "자동 적용하지 않는다" in md          # 제안일 뿐임을 리포트가 말한다


def test_gate_report_does_not_promote_a_rule_that_only_rode_along():
    """항상 reject와 함께 떴지만 단독으론 한 번도 안 뜬 규칙 → 승격이 아니라 관찰."""
    recs = [_vaudit([("high_one", "high"), ("tagalong", "med")],
                    passed=False, recommendation="reject") for _ in range(3)]
    md = render_gate_report(recs)
    note = "👀 관찰 — 항상 다른 규칙과 함께 떴다(동시발생 ≠ 인과)"
    assert f"| tagalong | 3 | {note} |" in md
    assert f"| high_one | 3 | {note} |" in md
    assert "사전 차단 후보" not in md


def test_gate_report_demotes_never_blocking_rule():
    recs = [_vaudit([("nit", "low")], passed=True, recommendation="approve") for _ in range(3)]
    assert "소음 후보" in render_gate_report(recs)


def test_gate_report_observes_mixed_rule():
    recs = [_vaudit([("x", "med")], passed=True, recommendation="approve"),
            _vaudit([("x", "med")], passed=False, recommendation="reject"),
            _vaudit([("x", "med")], passed=True, recommendation="review")]
    md = render_gate_report(recs)
    assert "| x | 3 | 👀 관찰 |" in md
    assert "동시발생" not in md         # 단독으로 떴다 — 업힌 게 아니다


def test_gate_report_never_claims_decisions_split_when_they_did_not():
    """전부 review인 규칙은 '결정이 갈림'이 아니다 — 없는 이유를 붙이지 않는다."""
    recs = [_vaudit([("x", "med")], passed=True, recommendation="review") for _ in range(3)]
    md = render_gate_report(recs)
    assert "| x | 3 | 👀 관찰 |" in md
    assert "갈림" not in md


# ── dashboard ──

def test_dashboard_combines_sections():
    md = dashboard(calls=[_call()], audits=[_audit()])
    assert "# mint-agent 관측 대시" in md
    assert "## 비용·호출 리포트" in md and "## 발행 게이트 감사 리포트" in md


def test_dashboard_omits_missing_sections():
    md = dashboard(calls=[_call()])
    assert "## 비용·호출 리포트" in md
    assert "발행 게이트" not in md              # audits 미제공 → 섹션 없음
