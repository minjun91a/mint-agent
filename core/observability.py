# -*- coding: utf-8 -*-
"""
core.observability — 계측 데이터를 사람이 읽는 리포트로 렌더링 (Trust 레이어 ④ 관측성).

지금까지 엔진이 계측한 두 흐름을 legible한 Markdown 리포트로 묶는다:
  - **비용·호출** (`core.routing.CallRecord`) → `render_cost_report`
  - **발행 게이트 감사** (`core.trust.eval.AuditRecord`) → `render_gate_report`

원시 `aggregate`/`evaluate` dict가 "무엇을 쟀나"라면, 여기 렌더러는 "그래서 어떻다"를 보여준다
(README 지표 테이블·CLI 대시·Discord 요약의 재료). 렌더링은 순수·결정적이라 실 데이터 없이도
테스트된다. **정직성 유지**: 근사/실측 비율(`token_source`)·미가격 호출·모순 건수·**카테고리
미기록 레코드 수**를 숨기지 않고 리포트에 명시한다 — 리포트가 데이터보다 장밋빛이면 안 된다.
"""
from __future__ import annotations

from core.routing import CallRecord, aggregate
from core.trust.eval import AuditRecord, RuleCandidate, evaluate, rule_candidates


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


#: 규칙 후보 제안 → 사람이 읽는 한 줄.
_ACTIONS = {
    "promote": "🔺 사전 차단 후보 (단독으로도 reject)",
    "demote": "🔻 소음 후보 (떠도 통과)",
    "observe": "👀 관찰",
}


def _reason(c: RuleCandidate) -> str:
    """`observe` 중 **함정인 경우에만** 이유를 단다.

    '항상 reject인데 단독 0'은 결정이 갈린 게 아니라 다른 규칙에 **업힌** 것이다 — 표만 보면
    승격 후보로 오독하기 쉬우므로 이 한 줄이 필요하다. 나머지 `observe`에는 아무 말도 붙이지
    않는다: 라벨에 '결정이 갈림'을 박으면 전부 `review`였던 규칙까지 갈렸다고 말하게 된다.
    """
    if c.action == "observe" and c.always_rejected and c.solo_count == 0:
        return " — 항상 다른 규칙과 함께 떴다(동시발생 ≠ 인과)"
    return ""


def render_gate_report(audit_records: list[AuditRecord], *, min_count: int = 3) -> str:
    """AuditRecord 목록 → 발행 게이트 감사 Markdown 리포트.

    통과율·분포·모순 건수에 더해 **어떤 규칙이 몇 번 터졌는지**와 규칙 후보를 싣는다.
    카테고리를 기록하지 않은 레코드가 섞여 있으면 그 사실을 감추지 않고 분모와 함께 밝힌다 —
    구 스키마 로그를 '위반 없음'으로 보이게 하면 리포트가 데이터보다 장밋빛이 된다.
    """
    out = evaluate(audit_records, min_count=min_count)
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
        # min/max는 원시값이라 부동소수 잡음이 그대로 보인다(0.44999999999999996). 표시에서만 다듬는다.
        f"- **점수** 평균 {s['score']['mean']}"
        f" (min {round(s['score']['min'], 4)} · max {round(s['score']['max'], 4)})",
        f"- **모순 감사** {len(findings)}건"
        + (f" ⚠️ {_dist(_count_rules(findings))}" if findings else " ✅ 없음"),
    ]
    # 표는 **모든** 규칙을 싣는다(min_count=1). 후보는 그중 반복된 것만 — 알고 있는 심각도를
    # '모름(—)'으로 적지 않기 위해, 후보 목록이 아니라 전체 목록에서 뽑는다.
    all_rules = rule_candidates(audit_records, min_count=1)
    lines += _render_rules(all_rules, s["categorized_count"], n, min_count)
    return "\n".join(lines)


def _render_rules(all_rules: list[RuleCandidate], categorized: int,
                  n: int, min_count: int) -> list[str]:
    """위반 규칙 분포 + 규칙 후보 섹션. 카테고리 미기록 레코드는 명시한다."""
    if categorized == 0:
        return ["", f"> 위반 카테고리를 기록한 레코드가 없다({n}건 전부 구 스키마)."
                    " 어떤 규칙이 터졌는지는 이 로그로 답할 수 없다."]

    lines = ["", "### 터진 규칙", ""]
    if categorized < n:
        lines += [f"> {n}건 중 **{categorized}건**만 카테고리를 기록했다."
                  f" 아래 분모는 {categorized}다.", ""]
    if not all_rules:
        lines.append(f"_위반 없음 ({categorized}건 모두 무위반)._")
        return lines

    lines += ["| 규칙 | 심각도 | 결정 수 | 단독 | support |", "|---|---|---:|---:|---:|"]
    for c in all_rules:
        lines.append(f"| {c.category} | {c.severity or '—'} | {c.count} "
                     f"| {c.solo_count} | {c.support:.0%} |")

    candidates = [c for c in all_rules if c.count >= min_count]
    lines += ["", f"### 규칙 후보 ({min_count}회 이상)", ""]
    if not candidates:
        lines.append(f"_{min_count}회 이상 반복된 규칙 없음._")
        return lines
    lines += ["| 규칙 | 결정 수 | 제안 |", "|---|---:|---|"]
    for c in candidates:
        lines.append(f"| {c.category} | {c.count} | {_ACTIONS[c.action]}{_reason(c)} |")
    lines += ["", "> '단독'은 그 규칙이 **유일한 위반**이었던 결정 수다. 0이면 항상 다른 규칙에",
              "> 업혀 뜬 것이므로 승격 근거가 없다. 제안일 뿐 자동 적용하지 않는다 —",
              "> 규칙을 바꾸는 것은 사람의 몫이다."]
    return lines


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
