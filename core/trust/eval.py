# -*- coding: utf-8 -*-
"""
core.trust.eval — 발행 게이트 결정 로그의 eval 하니스 (Trust 레이어 첫 지표).

`verify.py`가 검증 *어휘*(Verdict)를 정의했다면, 이 모듈은 게이트가 실제로 내린 결정들을
**측정**한다. 운영 `compliance_audit_*.jsonl`(발행 전 게이트가 매 콘텐츠에 남긴 감사 로그)을
읽어 세 가지를 뽑는다:

  1. **집계 지표(`summarize`)** — 통과율·권고 분포·점수/위반 통계·채널별 분해. 원시 로그를
     "게이트가 어떻게 행동했나"로 요약한다(README 실측 지표의 재료).
  2. **자체 모순 감사(`check_invariants`)** — 결정이 게이트 *자신의 규칙*과 모순되지 않는지
     검사한다. 정답 라벨이 없어도 확인 가능한 논리적 불변식만 본다(아래).
  3. **규칙 후보 추출(`rule_candidates`)** — 반복해서 터지는 위반 카테고리를 규칙 후보로
     올린다. *제안일 뿐 자동 적용하지 않는다* — 규칙을 바꾸는 것은 사람의 몫이다.

불변식은 원본 compliance 설계(`agents/compliance.py`)에서 근거를 얻는다:
  - `score = min(1.0, 위반수 * 0.15)` → score ∈ [0, 1]
  - "high severity 위반 → passed=false, recommendation=reject", "low/med만 → passed=true"
  → 그래서 `reject ⟹ ¬passed`, `approve ⟹ passed`는 논리적으로 성립해야 한다. 이를 어긴
    레코드는 로깅 버그·규칙 드리프트의 신호다.

**★ 감사 로그가 버리고 있던 것(2026-07-10).** 원본 로그 40건의 키를 전수 집계하면
`{ts, channel, title, passed, score, recommendation, agreement, violation_count}`뿐이다 —
위반이 **몇 건**인지는 남기고 **어떤 규칙**이 터졌는지는 남기지 않았다. 그래서 원본도, 이식한
게이트도 "가장 많이 터지는 규칙은 무엇인가"를 로그로 답할 수 없었고, 검출 패턴을 규칙 후보로
올리는 길이 스키마에서 끊겨 있었다. 이제 `violations`(카테고리·심각도)를 남긴다. 단
**`detail`은 남기지 않는다** — 위반 상세는 콘텐츠 조각을 담을 수 있고, 감사 로그는 콘텐츠
보관소가 아니다.

구 스키마와의 구분은 `None`이 진다: `violations is None`은 "**카테고리 미기록**"이고
`violations == ()`은 "**위반 없음**"이다. 둘을 뭉개면 40건의 구 로그가 전부 '위반 0건'으로
보인다 — `routing.cost_usd=None`(미가격)과 같은 규약, 없는 것을 지어내지 않는다.

플랫폼 무관: 이 하니스는 '게이트 결정 레코드'라는 trust 어휘만 알 뿐 youtube를 모른다. 실제
로그 경로는 **주입**(외부 데이터) — 테스트는 합성 레코드/임시 파일로 결정적으로 돈다.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 유효한 권고값(verify.Recommendation과 동일 집합).
RECOMMENDATIONS = ("approve", "review", "reject")
#: 유효한 심각도(verify.Severity와 동일 집합). 순서 = 낮음 → 높음.
SEVERITIES = ("low", "med", "high")


@dataclass(frozen=True)
class ViolationRecord:
    """감사 로그에 남는 위반 1건 — 카테고리와 심각도뿐(`detail`은 의도적으로 제외)."""

    category: str
    severity: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "ViolationRecord":
        return cls(category=str(d.get("category", "")), severity=str(d.get("severity", "")))


def _parse_violations(raw: Any) -> tuple[ViolationRecord, ...] | None:
    """`violations` 필드 → 레코드 튜플. **None은 '위반 없음'이 아니라 '카테고리 미기록'.**"""
    if not isinstance(raw, list):
        return None   # 구 스키마(필드 부재) 또는 스키마 흔들림 — 있는 척하지 않는다
    return tuple(ViolationRecord.from_dict(v) for v in raw if isinstance(v, dict))


@dataclass(frozen=True)
class AuditRecord:
    """게이트 결정 1건. `compliance_audit_*.jsonl`의 한 줄과 대응(누락 필드는 안전 기본값)."""

    ts: str = ""
    channel: str = ""
    title: str = ""
    passed: bool = False
    score: float = 0.0
    recommendation: str = "review"
    agreement: str = ""
    violation_count: int = 0
    #: 위반 카테고리·심각도. `None` = 로그에 미기록(구 스키마), `()` = 위반 없음.
    violations: tuple[ViolationRecord, ...] | None = None

    @property
    def has_categories(self) -> bool:
        """이 레코드가 '어떤 규칙이 터졌는지'를 담고 있는가."""
        return self.violations is not None

    @classmethod
    def from_dict(cls, d: dict) -> "AuditRecord":
        """dict → 레코드. 타입·누락에 관대하게 정규화(운영 로그의 스키마 흔들림 대비).

        `violation_count`는 키가 있으면 **기록된 값을 그대로** 쓴다 — `len(violations)`로
        덮으면 둘이 어긋난 로그(기록 버그)를 모순 감사가 영영 잡을 수 없다.
        """
        violations = _parse_violations(d.get("violations"))
        if "violation_count" in d:
            count = int(d.get("violation_count") or 0)
        else:
            count = len(violations) if violations is not None else 0
        return cls(
            ts=str(d.get("ts", "")),
            channel=str(d.get("channel", "")),
            title=str(d.get("title", "")),
            passed=bool(d.get("passed", False)),
            score=float(d.get("score", 0.0) or 0.0),
            recommendation=str(d.get("recommendation", "review")),
            agreement=str(d.get("agreement", "")),
            violation_count=count,
            violations=violations,
        )


def load_jsonl(path: str | Path) -> list[AuditRecord]:
    """단일 JSONL 감사 로그 → 레코드 목록. 손상된 줄은 건너뛴다(append-only 로그의 부분 손상 대비)."""
    p = Path(path)
    if not p.exists():
        return []
    records: list[AuditRecord] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(AuditRecord.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            continue   # 깨진 줄은 조용히 무시(측정을 멈추지 않는다)
    return records


def load_dir(directory: str | Path, *,
             pattern: str = "compliance_audit_*.jsonl") -> list[AuditRecord]:
    """디렉터리의 감사 로그들을 파일명 순으로 모은다(경로 주입 — 실 로그는 외부에서)."""
    return [rec for f in sorted(Path(directory).glob(pattern)) for rec in load_jsonl(f)]


def _counter(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _empty_summary() -> dict:
    return {
        "count": 0, "date_range": ["", ""], "pass_rate": 0.0,
        "by_recommendation": {}, "by_channel": {}, "by_agreement": {},
        "score": {"min": 0.0, "max": 0.0, "mean": 0.0},
        "violations": {"total": 0, "max": 0, "mean": 0.0},
        "categorized_count": 0, "by_category": {}, "by_severity": {},
    }


def summarize(records: list[AuditRecord]) -> dict:
    """레코드 → 집계 지표(통과율·권고 분포·점수/위반 통계·채널별). 게이트 행동의 관측 요약.

    `by_category`/`by_severity`는 **카테고리를 기록한 레코드만** 센다. 몇 건이 그런지는
    `categorized_count`가 말한다 — 구 스키마 로그를 '위반 0건'으로 둔갑시키지 않기 위해서다.
    """
    n = len(records)
    if n == 0:
        return _empty_summary()
    scores = [r.score for r in records]
    vcounts = [r.violation_count for r in records]
    stamps = sorted(r.ts for r in records if r.ts)
    categorized = [r for r in records if r.has_categories]
    return {
        "count": n,
        "date_range": [stamps[0], stamps[-1]] if stamps else ["", ""],
        "pass_rate": round(sum(1 for r in records if r.passed) / n, 4),
        "by_recommendation": _counter(r.recommendation for r in records),
        "by_channel": _counter(r.channel for r in records),
        "by_agreement": _counter(r.agreement for r in records if r.agreement),
        "score": {"min": min(scores), "max": max(scores),
                  "mean": round(statistics.fmean(scores), 4)},
        "violations": {"total": sum(vcounts), "max": max(vcounts),
                       "mean": round(statistics.fmean(vcounts), 4)},
        "categorized_count": len(categorized),
        "by_category": _counter(v.category for r in categorized for v in r.violations),
        "by_severity": _counter(v.severity for r in categorized for v in r.violations),
    }


# ── 규칙 후보 추출 ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RuleCandidate:
    """반복 검출된 위반 카테고리 1건 → 사람이 검토할 규칙 후보.

    `action`은 **제안**이다. 규칙을 승격·강등하는 것은 사람의 판단이며, 이 모듈은 근거(빈도·
    단독성·결정 분포)를 붙여 올릴 뿐이다.

    **동시발생 ≠ 인과.** 한 결정에 여러 규칙이 함께 뜨면 그 결정의 `reject`는 전부의 것으로
    집계된다 — 옆에서 터진 high 위반 덕에 med 규칙이 '항상 reject'로 보인다. `solo_count`가
    그 구분을 진다: **단독으로 뜬 적이 한 번도 없는 규칙은 승격 근거가 없다.**
    """

    category: str
    severity: str          # 관측된 최고 심각도
    count: int             # 이 카테고리가 등장한 결정 수
    support: float         # count / 카테고리를 기록한 레코드 수
    solo_count: int        # 이 카테고리가 **유일한** 위반이었던 결정 수
    always_rejected: bool  # 등장한 모든 결정이 reject였는가
    never_blocking: bool   # 등장한 모든 결정이 approve였는가(결정을 바꾸지 못했다)

    @property
    def action(self) -> str:
        """`promote`(사전 차단 규칙 후보) · `demote`(소음 후보) · `observe`(판단 보류)."""
        if self.always_rejected and self.solo_count > 0:
            return "promote"   # 단독으로도 reject를 냈다 — 이 규칙 하나로 막을 수 있다
        if self.never_blocking:
            return "demote"    # 떠도 통과했다 — 결정을 바꾸지 못하는 규칙
        return "observe"


def _max_severity(severities) -> str:
    """관측된 심각도 중 가장 높은 것. 알 수 없는 값은 무시하고, 하나도 없으면 ''."""
    known = [s for s in severities if s in SEVERITIES]
    return max(known, key=SEVERITIES.index) if known else ""


def rule_candidates(records: list[AuditRecord], *, min_count: int = 3) -> list[RuleCandidate]:
    """카테고리를 기록한 레코드에서 `min_count` 이상 반복된 위반 → 규칙 후보 목록.

    카테고리 미기록 레코드는 **분모에서도 빠진다**(그 레코드는 어떤 카테고리도 부정하지 않는다).
    `count`는 위반 건수가 아니라 **그 카테고리가 등장한 결정 수**다 — 한 결정이 같은 카테고리를
    두 번 위반해도 1로 센다(그러지 않으면 `support`가 1.0을 넘는다).
    정렬은 (빈도 내림차순, 카테고리 오름차순) — 결정적.
    """
    categorized = [r for r in records if r.has_categories]
    if not categorized:
        return []

    hits: dict[str, list[AuditRecord]] = {}
    severities: dict[str, list[str]] = {}
    solo: dict[str, int] = {}
    for r in categorized:
        seen = list(dict.fromkeys(v.category for v in r.violations))   # 결정당 1회, 순서 보존
        for category in seen:
            hits.setdefault(category, []).append(r)
            if len(seen) == 1:
                solo[category] = solo.get(category, 0) + 1
        for v in r.violations:
            severities.setdefault(v.category, []).append(v.severity)

    denom = len(categorized)
    out = [
        RuleCandidate(
            category=category,
            severity=_max_severity(severities[category]),
            count=len(rs),
            support=round(len(rs) / denom, 4),
            solo_count=solo.get(category, 0),
            always_rejected=all(r.recommendation == "reject" for r in rs),
            never_blocking=all(r.recommendation == "approve" for r in rs),
        )
        for category, rs in hits.items()
        if len(rs) >= min_count
    ]
    return sorted(out, key=lambda c: (-c.count, c.category))


def check_invariants(records: list[AuditRecord]) -> list[dict]:
    """게이트 결정이 자체 규칙과 모순되지 않는지 감사. 위반을 finding 목록으로 반환(정답 불필요).

    각 finding: `{"ts", "title", "rule", "detail"}`. 빈 목록 = 모든 결정이 자기 규칙에 정합.

    카테고리 관련 두 불변식은 **카테고리를 기록한 레코드에만** 적용한다. 구 스키마 로그
    (`violations is None`)에 적용하면 40건 전부가 `violation_count != 0` 으로 오탐된다.
    """
    findings: list[dict] = []
    for r in records:
        def add(rule: str, detail: str) -> None:
            findings.append({"ts": r.ts, "title": r.title, "rule": rule, "detail": detail})

        if not (0.0 <= r.score <= 1.0):
            add("score_range", f"score={r.score} ∉ [0,1]")
        if r.recommendation not in RECOMMENDATIONS:
            add("recommendation_enum", f"recommendation={r.recommendation!r} ∉ {RECOMMENDATIONS}")
        if r.violation_count < 0:
            add("violation_count_nonneg", f"violation_count={r.violation_count} < 0")
        if r.recommendation == "reject" and r.passed:
            add("reject_implies_not_passed", "recommendation=reject 인데 passed=true")
        if r.recommendation == "approve" and not r.passed:
            add("approve_implies_passed", "recommendation=approve 인데 passed=false")

        if r.violations is None:
            continue   # 카테고리 미기록 — 아래 두 불변식은 판정할 근거가 없다
        if len(r.violations) != r.violation_count:
            add("violation_count_mismatch",
                f"violation_count={r.violation_count} ≠ len(violations)={len(r.violations)}")
        for v in r.violations:
            if v.severity not in SEVERITIES:
                add("severity_enum", f"severity={v.severity!r} ∉ {SEVERITIES} ({v.category})")
    return findings


def evaluate(records: list[AuditRecord], *, min_count: int = 3) -> dict:
    """집계 + 모순 감사 + 규칙 후보를 한 번에. Trust 레이어의 발행 게이트 eval 리포트."""
    return {
        "summary": summarize(records),
        "invariant_findings": check_invariants(records),
        "rule_candidates": rule_candidates(records, min_count=min_count),
    }
