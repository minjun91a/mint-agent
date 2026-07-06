# -*- coding: utf-8 -*-
"""
core.trust.consensus — 멀티 리뷰어 컨센서스 검증 (Trust 레이어 교차검증).

한 리뷰어의 판정을 믿는 대신, **같은 콘텐츠를 N개 리뷰어(모델·규칙)로 교차검증**해 판정을
결합하고 그들이 얼마나 일치하는지(`agreement`)를 남긴다. 불일치는 그 자체로 신호다 —
리뷰어들이 갈리면 사람이 봐야 한다.

근거·정직성:
  - 운영 감사 로그에 `agreement: "consensus" | "disagree"` 필드가 실재한다(원본은 `double_check`
    라는 2차 리뷰어로 산출). 다만 그 모듈 **소스는 현재 youtube-agent에 없어** faithful 이식이
    불가하다. 그래서 이건 *이식이 아니라 재구성*이다 — 단 결합 규칙은 관측된 의미(전원 일치=
    consensus)와 이 프로젝트의 보수성("의심스러우면 review/reject")에서 자연히 따라온다.
  - 결합은 `verify.Verdict` 어휘를 재사용한다. 리뷰어·모델은 **주입**이므로 엔진은 youtube도,
    몇 개의 모델을 쓰는지도 모른다(순수 `combine`은 실 LLM 없이 결정적으로 검증된다).

결합 정책(보수적):
  - recommendation: 하나라도 `reject` → `reject`, 아니면 하나라도 `review` → `review`, 전원
    `approve`여야 `approve`. (가장 안전한 표를 채택 — 발행은 되돌리기 어렵다)
  - passed: 전원 통과여야 통과.
  - score: 최댓값(가장 높은 위험도).
  - agreement: 모든 recommendation이 동일하면 `consensus`, 아니면 `disagree`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from core.trust.verify import Recommendation, Verdict, Violation

Agreement = Literal["consensus", "disagree"]

# 보수성 순위 — 값이 큰 쪽이 더 안전(보수)한 권고. 결합 시 최댓값을 채택.
_SEVERITY_RANK: dict[str, int] = {"approve": 0, "review": 1, "reject": 2}
_RANK_TO_REC: dict[int, Recommendation] = {0: "approve", 1: "review", 2: "reject"}


@dataclass(frozen=True)
class ConsensusVerdict:
    """N개 리뷰어 판정의 결합 결과. `votes`로 각 리뷰어의 권고를 남겨 불일치를 설명 가능하게 한다."""

    recommendation: Recommendation
    passed: bool
    score: float
    agreement: Agreement
    votes: list[str] = field(default_factory=list)     # 각 리뷰어의 recommendation
    violations: list[Violation] = field(default_factory=list)

    @property
    def member_count(self) -> int:
        return len(self.votes)

    def to_dict(self) -> dict:
        return {
            "recommendation": self.recommendation,
            "passed": self.passed,
            "score": self.score,
            "agreement": self.agreement,
            "votes": list(self.votes),
            "violation_count": len(self.violations),
        }


def combine(verdicts: list[Verdict]) -> ConsensusVerdict:
    """N개 `Verdict` → 보수적 컨센서스. 빈 입력은 보수적으로 review(사람 판단)로 안전화."""
    if not verdicts:
        return ConsensusVerdict(recommendation="review", passed=False, score=0.0,
                                agreement="disagree", votes=[], violations=[])

    votes = [v.recommendation for v in verdicts]
    worst_rank = max(_SEVERITY_RANK.get(r, 1) for r in votes)   # 미지값은 보수적으로 review 취급
    agreement: Agreement = "consensus" if len(set(votes)) == 1 else "disagree"

    # 위반은 리뷰어 간 중복 제거 후 합친다(같은 (category, severity, detail)은 1건).
    merged: list[Violation] = []
    seen: set[tuple[str, str, str]] = set()
    for v in verdicts:
        for viol in v.violations:
            key = (viol.category, viol.severity, viol.detail)
            if key not in seen:
                seen.add(key)
                merged.append(viol)

    return ConsensusVerdict(
        recommendation=_RANK_TO_REC[worst_rank],
        passed=all(v.passed for v in verdicts),
        score=max(v.score for v in verdicts),
        agreement=agreement,
        votes=votes,
        violations=merged,
    )


def run(reviews: list[Callable[[], Verdict]]) -> ConsensusVerdict:
    """리뷰어 thunk들을 실행해 결합한다. 실패한 리뷰어는 보수적 판정(review·미통과)으로 격리한다.

    한 리뷰어가 예외를 던져도 컨센서스를 멈추지 않는다 — 그 리뷰어의 표를 "불확실 → review"로
    대체해 결합에 포함한다(불확실성이 agreement를 disagree로 끌어 사람 판단을 유도).
    """
    verdicts: list[Verdict] = []
    for review in reviews:
        try:
            verdicts.append(review())
        except Exception:
            verdicts.append(Verdict(passed=False, score=0.0, violations=[],
                                    recommendation="review"))
    return combine(verdicts)
