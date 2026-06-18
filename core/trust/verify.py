# -*- coding: utf-8 -*-
"""
core.trust.verify — 플랫폼 무관 '검증(verify/review)' 포트.

Trust 레이어 ①(검증/가드레일)의 **공통 어휘**를 정의한다. 어댑터(youtube, research, ...)는
각자의 규칙으로 Reviewer를 구현하되, 결과는 항상 이 `Verdict` 형태로 돌려준다.
→ 엔진·관측성·HITL은 어댑터 내부를 몰라도 `Verdict` 하나로 일관 처리한다.

적정 고도(브리프 8장): 지금은 어댑터가 youtube 하나뿐이라 `Reviewer.review`의 입력
시그니처를 강제로 일반화하지 않는다(일반화는 2번째 어댑터가 강제할 때). 지금 공유하는 것은
'결과 어휘(Verdict/Violation)'까지다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Severity = Literal["low", "med", "high"]
Recommendation = Literal["approve", "review", "reject"]


@dataclass(frozen=True)
class Violation:
    """단일 위반 사항. 어댑터 규칙이 무엇이든 이 형태로 보고한다."""

    category: str
    severity: Severity
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"category": self.category, "severity": self.severity, "detail": self.detail}


@dataclass(frozen=True)
class Verdict:
    """검증 결과 표준형. `to_dict()`는 레거시 dict 형태(passed/score/violations/recommendation)와 동일."""

    passed: bool
    score: float
    violations: list[Violation] = field(default_factory=list)
    recommendation: Recommendation = "review"

    @property
    def has_high_severity(self) -> bool:
        return any(v.severity == "high" for v in self.violations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "violations": [v.to_dict() for v in self.violations],
            "recommendation": self.recommendation,
        }


@runtime_checkable
class Reviewer(Protocol):
    """발행 전 콘텐츠를 검증해 `Verdict`를 돌려주는 어댑터 인터페이스.

    입력 시그니처는 어댑터가 정한다(youtube는 (script, seo)). 공통 계약은 '반환이 Verdict'라는 점.
    """

    def review(self, *args: Any, **kwargs: Any) -> Verdict: ...
