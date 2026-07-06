# -*- coding: utf-8 -*-
"""
core.observability 테스트 — 계측 데이터 → Markdown 리포트 렌더링.

순수·결정적. 합성 레코드로 리포트 구조·수치·정직성 표기(근사/실측·미가격·모순)를 고정한다.
"""
from __future__ import annotations

from core.observability import dashboard, render_cost_report, render_gate_report
from core.routing import CallRecord
from core.trust.eval import AuditRecord


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


def test_gate_report_flags_invariant_findings():
    # reject인데 passed=true → 모순 1건이 리포트에 ⚠️로 뜬다
    md = render_gate_report([_audit(recommendation="reject", passed=True)])
    assert "모순 감사** 1건" in md and "⚠️" in md
    assert "reject_implies_not_passed" in md


def test_gate_report_clean_shows_no_findings():
    md = render_gate_report([_audit(recommendation="approve", passed=True)])
    assert "모순 감사** 0건" in md and "✅ 없음" in md


# ── dashboard ──

def test_dashboard_combines_sections():
    md = dashboard(calls=[_call()], audits=[_audit()])
    assert "# mint-agent 관측 대시" in md
    assert "## 비용·호출 리포트" in md and "## 발행 게이트 감사 리포트" in md


def test_dashboard_omits_missing_sections():
    md = dashboard(calls=[_call()])
    assert "## 비용·호출 리포트" in md
    assert "발행 게이트" not in md              # audits 미제공 → 섹션 없음
