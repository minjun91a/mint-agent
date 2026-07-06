# -*- coding: utf-8 -*-
"""
core.observability — 계측 데이터를 사람이 읽는 리포트로 렌더링 (Trust 레이어 ④ 관측성).

지금까지 엔진이 계측한 두 흐름을 legible한 Markdown 리포트로 묶는다:
  - **비용·호출** (`core.routing.CallRecord`) → `render_cost_report`
  - **발행 게이트 감사** (`core.trust.eval.AuditRecord`) → `render_gate_report`

원시 `aggregate`/`evaluate` dict가 "무엇을 쟀나"라면, 여기 렌더러는 "그래서 어떻다"를 보여준다
(README 지표 테이블·CLI 대시·Discord 요약의 재료). 렌더링은 순수·결정적이라 실 데이터 없이도
테스트된다. **정직성 유지**: 근사/실측 비율(`token_source`)·미가격 호출·모순 건수를 숨기지 않고
리포트에 명시한다 — 리포트가 데이터보다 장밋빛이면 안 된다.
"""
from __future__ import annotations

from core.routing import CallRecord, aggregate
from core.trust.eval import AuditRecord, evaluate


def _pct(part: int, whole: int) -> str:
    """정수 비율 → 'NN.N%' (분모 0이면 '—')."""
    return f"{part / whole * 100:.1f}%" if whole else "—"


def _dist(counter: dict[str, int]) -> str:
    """{k: n} → 'k=n · k2=n2' (내림차순). 비면 '(없음)'."""
    if not counter:
        return "(없음)"
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return " · ".join(f"{k}={n}" for k, n in items)


def render_cost_report(records: list[CallRecord]) -> str:
    """CallRecord 목록 → 비용·호출 Markdown 리포트. 근사/실측·미가격을 명시한다."""
    rep = aggregate(records)
    n = rep["calls"]
    lines = ["## 비용·호출 리포트", ""]
    if n == 0:
        lines.append("_호출 없음._")
        return "\n".join(lines)

    measured = rep["measured_calls"]
    lines += [
        f"- **호출** {n}건 (실측 {measured} · 근사 {n - measured})",
        f"- **비용** ${rep['cost_usd']:.6f}"
        + (f" (미가격 {rep['unpriced_calls']}건 제외)" if rep["unpriced_calls"] else ""),
        f"- **토큰** 입력 {rep['in_tokens']:,} · 출력 {rep['out_tokens']:,}",
        "",
        "### 티어별",
        "| 티어 | 호출 | 비용(USD) |",
        "|---|---:|---:|",
    ]
    for tier, slot in sorted(rep["by_tier"].items()):
        lines.append(f"| {tier} | {slot['calls']} | {slot['cost_usd']:.6f} |")
    lines += ["", "### 모델별", "| 모델 | 호출 | 비용(USD) |", "|---|---:|---:|"]
    for model, slot in sorted(rep["by_model"].items()):
        lines.append(f"| {model} | {slot['calls']} | {slot['cost_usd']:.6f} |")
    return "\n".join(lines)


def render_gate_report(audit_records: list[AuditRecord]) -> str:
    """AuditRecord 목록 → 발행 게이트 감사 Markdown 리포트. 통과율·분포·모순 건수를 명시한다."""
    out = evaluate(audit_records)
    s = out["summary"]
    n = s["count"]
    lines = ["## 발행 게이트 감사 리포트", ""]
    if n == 0:
        lines.append("_감사 레코드 없음._")
        return "\n".join(lines)

    passed = round(s["pass_rate"] * n)
    findings = out["invariant_findings"]
    lines += [
        f"- **결정** {n}건 (기간 {s['date_range'][0]} ~ {s['date_range'][1]})",
        f"- **통과율** {_pct(passed, n)}",
        f"- **권고** {_dist(s['by_recommendation'])}",
        f"- **합의** {_dist(s['by_agreement'])}",
        f"- **점수** 평균 {s['score']['mean']} (min {s['score']['min']} · max {s['score']['max']})",
        f"- **모순 감사** {len(findings)}건"
        + (f" ⚠️ {_dist(_count_rules(findings))}" if findings else " ✅ 없음"),
    ]
    return "\n".join(lines)


def _count_rules(findings: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        rule = f.get("rule", "?")
        out[rule] = out.get(rule, 0) + 1
    return out


def dashboard(*, calls: list[CallRecord] | None = None,
              audits: list[AuditRecord] | None = None) -> str:
    """비용 + 게이트 리포트를 한 대시보드로 묶는다. 주어진 섹션만 렌더링한다."""
    sections: list[str] = ["# mint-agent 관측 대시"]
    if calls is not None:
        sections.append(render_cost_report(calls))
    if audits is not None:
        sections.append(render_gate_report(audits))
    return "\n\n".join(sections)
