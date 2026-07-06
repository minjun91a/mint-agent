# -*- coding: utf-8 -*-
"""
core.trust.eval — 발행 게이트 결정 로그의 eval 하니스 (Trust 레이어 첫 지표).

`verify.py`가 검증 *어휘*(Verdict)를 정의했다면, 이 모듈은 게이트가 실제로 내린 결정들을
**측정**한다. 운영 `compliance_audit_*.jsonl`(발행 전 게이트가 매 콘텐츠에 남긴 감사 로그)을
읽어 두 가지를 뽑는다:

  1. **집계 지표(`summarize`)** — 통과율·권고 분포·점수/위반 통계·채널별 분해. 원시 로그를
     "게이트가 어떻게 행동했나"로 요약한다(README 실측 지표의 재료).
  2. **자체 모순 감사(`check_invariants`)** — 결정이 게이트 *자신의 규칙*과 모순되지 않는지
     검사한다. 정답 라벨이 없어도 확인 가능한 논리적 불변식만 본다(아래).

불변식은 원본 compliance 설계(`agents/compliance.py`)에서 근거를 얻는다:
  - `score = min(1.0, 위반수 * 0.15)` → score ∈ [0, 1]
  - "high severity 위반 → passed=false, recommendation=reject", "low/med만 → passed=true"
  → 그래서 `reject ⟹ ¬passed`, `approve ⟹ passed`는 논리적으로 성립해야 한다. 이를 어긴
    레코드는 로깅 버그·규칙 드리프트의 신호다.

플랫폼 무관: 이 하니스는 '게이트 결정 레코드'라는 trust 어휘만 알 뿐 youtube를 모른다. 실제
로그 경로는 **주입**(외부 데이터) — 테스트는 합성 레코드/임시 파일로 결정적으로 돈다.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path

# 유효한 권고값(verify.Recommendation과 동일 집합).
RECOMMENDATIONS = ("approve", "review", "reject")


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

    @classmethod
    def from_dict(cls, d: dict) -> "AuditRecord":
        """dict → 레코드. 타입·누락에 관대하게 정규화(운영 로그의 스키마 흔들림 대비)."""
        return cls(
            ts=str(d.get("ts", "")),
            channel=str(d.get("channel", "")),
            title=str(d.get("title", "")),
            passed=bool(d.get("passed", False)),
            score=float(d.get("score", 0.0) or 0.0),
            recommendation=str(d.get("recommendation", "review")),
            agreement=str(d.get("agreement", "")),
            violation_count=int(d.get("violation_count", 0) or 0),
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
    }


def summarize(records: list[AuditRecord]) -> dict:
    """레코드 → 집계 지표(통과율·권고 분포·점수/위반 통계·채널별). 게이트 행동의 관측 요약."""
    n = len(records)
    if n == 0:
        return _empty_summary()
    scores = [r.score for r in records]
    vcounts = [r.violation_count for r in records]
    stamps = sorted(r.ts for r in records if r.ts)
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
    }


def check_invariants(records: list[AuditRecord]) -> list[dict]:
    """게이트 결정이 자체 규칙과 모순되지 않는지 감사. 위반을 finding 목록으로 반환(정답 불필요).

    각 finding: `{"ts", "title", "rule", "detail"}`. 빈 목록 = 모든 결정이 자기 규칙에 정합.
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
    return findings


def evaluate(records: list[AuditRecord]) -> dict:
    """집계 + 모순 감사를 한 번에. Trust 레이어의 발행 게이트 eval 리포트."""
    return {"summary": summarize(records), "invariant_findings": check_invariants(records)}
