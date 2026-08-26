# -*- coding: utf-8 -*-
"""
adapters.manufacturing.review — 발행 전 **환각 코드 검증** (produce/**review**/publish의 review).

`core.trust.Reviewer` 구현. research의 `CitationReviewer`와 같은 자리 — 규칙은 다르지만
결과는 같은 `Verdict` 어휘다. 여기서 대조하는 건 인용 번호가 아니라 **자재·품목 코드**다:
`analyze.py`가 이미 산수로 확정한 `CascadeImpact`를 "정답"으로 놓고, LLM 초안 3종(`draft.py`)이
그 밖의 코드를 지어냈는지 정규식으로 검사한다 — **완전히 결정적, LLM 불필요**(research의
`check_citations`와 같은 설계: 사실 문제는 사실 문제로 검증한다).

정책(어댑터 자율, research·youtube와 각각 다름):
    - 존재하지 않는 코드 인용(high) → `reject`
    - 초안 누락/미생성(high)        → `reject`
    - 위반 없음                     → `approve`

🔴 **알려진 한계(다음 슬라이스 후보)**: `draft.py`의 고객사용 프롬프트가 "협력업체명·자재코드를
고객사에 노출하지 말라"고 지시하지만, 이 리뷰어는 **그 지시를 지켰는지는 검증하지 않는다** —
지금 검사하는 건 "지어낸 코드가 있는가"뿐이다. 정보 노출 규칙은 별도 카테고리로 추가해야 한다.
"""
from __future__ import annotations

import re
from typing import Any

from adapters.manufacturing.analyze import CascadeImpact
from adapters.manufacturing.draft import UNAVAILABLE
from core.trust.verify import Verdict, Violation

# M-01, P-100 같은 자재·품목 코드 패턴 — 이 저장소 데모 데이터의 명명 규칙과 일치시킨다.
#
# ★ `\b`가 아니라 lookaround을 쓴다. Python `re`의 `\b`는 유니코드 `\w` 기준인데, 한글 음절도
# `\w`로 친다 — 그래서 "M-99를"처럼 코드 뒤에 조사가 바로 붙으면(한국어 문서에서는 기본값)
# 숫자↔한글 사이에 "경계"가 없다고 판정돼 `\b`가 매치를 막는다(직접 재현: `\bM-99\b`는
# "M-99를"에 매치하지 않는다). 코드 앞뒤가 영문/숫자로 안 이어지는지만 lookaround로 확인하고,
# 뒤에 한글이 오든 공백이 오든 상관하지 않는다.
_CODE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,4}-\d+(?!\d)")

_DRAFT_LABELS = (("조정안", "adjustment_plan"),
                 ("협력업체 요청", "supplier_request"),
                 ("고객사 회신", "customer_reply"))


def known_codes(impact: CascadeImpact) -> set[str]:
    """`impact`에서 등장 가능한 코드 전체 (순수) — 초안이 이 밖의 코드를 쓰면 환각이다."""
    codes = {impact.shipment.product}
    for sf in impact.shortfalls:
        codes.add(sf.material)
        for late in sf.late_arrivals:
            codes.add(late.material)
    return codes


def check_draft(label: str, text: str, impact: CascadeImpact) -> list[Violation]:
    """초안 하나 → 위반 목록 (순수, 결정적). 비었거나 게이트웨이 미가용 사유면 그 자체가 위반."""
    if not text.strip() or text == UNAVAILABLE:
        return [Violation(category="draft_missing", severity="high",
                          detail=f"{label} 초안이 비었거나 생성되지 않았습니다: '{text[:40]}'")]

    known = known_codes(impact)
    return [
        Violation(category="unknown_code_reference", severity="high",
                 detail=f"{label}에서 '{code}'는 이 연쇄 영향 분석에 등장하지 않는 코드입니다.")
        for code in sorted(set(_CODE.findall(text)))
        if code not in known
    ]


def review(content: dict[str, Any]) -> Verdict:
    """`ManufacturingAdapter.produce()` 결과 → 검증 Verdict. 초안 3종 전부를 대조한다."""
    impact: CascadeImpact = content["impact"]
    violations: list[Violation] = [
        v for label, key in _DRAFT_LABELS
        for v in check_draft(label, str(content.get(key, "")), impact)
    ]
    has_high = any(v.severity == "high" for v in violations)
    return Verdict(
        passed=not has_high,
        score=min(1.0, len(violations) * 0.15),
        violations=violations,
        recommendation="reject" if has_high else "approve",
    )


__all__ = ["known_codes", "check_draft", "review"]
