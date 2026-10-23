# -*- coding: utf-8 -*-
"""
adapters.manufacturing.review — 코드·수량·날짜와 고객 정보 노출 검사.

`core.trust.Reviewer` 구현. research의 `CitationReviewer`와 같은 자리 — 규칙은 다르지만
결과는 같은 `Verdict` 어휘다. `CascadeImpact`를 근거로 명시적인 수량/날짜를 대조하고,
대상·항목을 연결하지 못한 숫자는 사람 검토로 보낸다. 지원 문법은 facts.py와
docs/manufacturing-validation.md에 있다. 문장 전체의 의미 정확도 검증은 아니다.

정책(어댑터 자율, research·youtube와 각각 다름):
    - 존재하지 않는 코드 인용(high)        → `reject`
    - 초안 누락/미생성(high)               → `reject`
    - 명확한 수량/날짜 불일치(high)        → `reject`
    - 해석 불가능한 숫자/날짜(med)         → `review`
    - 고객사 회신에 내부 정보 노출(med)    → `review`(HITL — 환각이 아니라 정책 문제라 사람 판단)
    - 위반 없음                            → `approve`

**이전 한계를 닫음(2026-08-26)**: `draft.py`의 고객사용 프롬프트가 "협력업체명·자재코드를
고객사에 노출하지 말라"고 지시하는데, 처음 버전은 그 지시를 지켰는지 검증하지 않았다.
`check_customer_leak`이 그 자리를 채운다. 이후 facts.py의 불명확한 수량/날짜도
같은 `review`(HITL) 경로를 사용한다.
"""
from __future__ import annotations

from typing import Any

from adapters.manufacturing.analyze import CascadeImpact
from adapters.manufacturing.draft import UNAVAILABLE
from adapters.manufacturing.facts import CODE, check_facts
from core.trust.verify import Verdict, Violation

# M-01, P-100 같은 자재·품목 코드 패턴 — 이 저장소 데모 데이터의 명명 규칙과 일치시킨다.
#
# ★ `\b`가 아니라 lookaround을 쓴다. Python `re`의 `\b`는 유니코드 `\w` 기준인데, 한글 음절도
# `\w`로 친다 — 그래서 "M-99를"처럼 코드 뒤에 조사가 바로 붙으면(한국어 문서에서는 기본값)
# 숫자↔한글 사이에 "경계"가 없다고 판정돼 `\b`가 매치를 막는다(직접 재현: `\bM-99\b`는
# "M-99를"에 매치하지 않는다). 코드 앞뒤가 영문/숫자로 안 이어지는지만 lookaround로 확인하고,
# 뒤에 한글이 오든 공백이 오든 상관하지 않는다.
_CODE = CODE

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
    violations = [
        Violation(category="unknown_code_reference", severity="high",
                 detail=f"{label}에서 '{code}'는 이 연쇄 영향 분석에 등장하지 않는 코드입니다.")
        for code in sorted(set(_CODE.findall(text)))
        if code not in known
    ]
    # 먼저 코드 오류를 보고한다. 그 숫자를 수량으로 다시 해석하지 않는다.
    return violations + check_facts(label, text, impact)


def known_suppliers(impact: CascadeImpact) -> set[str]:
    """`impact`의 지연 입고 건에 등장하는 협력업체명 (순수) — 고객사 회신에 새면 안 되는 정보."""
    return {late.supplier for sf in impact.shortfalls for late in sf.late_arrivals}


def check_customer_leak(text: str, impact: CascadeImpact) -> list[Violation]:
    """고객사 회신에 내부 자재코드·협력업체명이 노출됐는지 (순수).

    환각이 아니라 **정보 노출** 정책 위반이라 severity="med" — `reject`가 아니라 사람 판단으로
    보낸다. 빈 텍스트·게이트웨이 미가용은 `check_draft`가 이미 `draft_missing`(high)으로 잡으므로
    여기서 중복 신고하지 않는다.
    """
    if not text.strip() or text == UNAVAILABLE:
        return []

    leaked_codes = (known_codes(impact) & set(_CODE.findall(text))) - {impact.shipment.product}
    leaked_suppliers = {s for s in known_suppliers(impact) if s in text}

    violations: list[Violation] = []
    if leaked_codes:
        violations.append(Violation(
            category="customer_facing_info_leak", severity="med",
            detail=f"고객사 회신에 내부 자재코드가 노출됐습니다: {sorted(leaked_codes)}"))
    if leaked_suppliers:
        violations.append(Violation(
            category="customer_facing_info_leak", severity="med",
            detail=f"고객사 회신에 협력업체명이 노출됐습니다: {sorted(leaked_suppliers)}"))
    return violations


def review(content: dict[str, Any]) -> Verdict:
    """`ManufacturingAdapter.produce()` 결과 → 검증 Verdict. 초안 3종 + 고객 정보 노출을 대조한다."""
    impact: CascadeImpact = content["impact"]
    violations: list[Violation] = [
        v for label, key in _DRAFT_LABELS
        for v in check_draft(label, content.get(key) if isinstance(content.get(key), str) else '', impact)
    ]
    violations += check_customer_leak(str(content.get("customer_reply", "")), impact)

    has_high = any(v.severity == "high" for v in violations)
    if has_high:
        recommendation = "reject"
    elif violations:
        recommendation = "review"
    else:
        recommendation = "approve"

    return Verdict(
        passed=not has_high,
        score=min(1.0, len(violations) * 0.15),
        violations=violations,
        recommendation=recommendation,
    )


__all__ = ["known_codes", "known_suppliers", "check_draft", "check_customer_leak", "review"]

