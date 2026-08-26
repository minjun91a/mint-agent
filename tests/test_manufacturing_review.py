# -*- coding: utf-8 -*-
"""
adapters.manufacturing.review 테스트.

신규 구축 — research의 `check_citations`와 같은 자리(완전히 결정적, LLM 불필요). "정답"은
`analyze.py`가 산수로 확정한 `CascadeImpact`이고, 초안 텍스트가 그 밖의 코드를 지어냈는지만
정규식으로 대조한다.
"""
from __future__ import annotations

import pytest

from adapters.manufacturing.analyze import CascadeImpact, MaterialShortfall
from adapters.manufacturing.draft import UNAVAILABLE
from adapters.manufacturing.erp_data import InboundLine, ShipmentLine
from adapters.manufacturing.review import (
    check_customer_leak,
    check_draft,
    known_codes,
    known_suppliers,
    review,
)

SHIPMENT = ShipmentLine("고객사A", "P-100", "2026-08-31", 500)

SHORT_IMPACT = CascadeImpact(
    shipment=SHIPMENT, new_ship_date="2026-08-28", days_pulled_forward=3,
    qty_remaining_to_produce=260,
    shortfalls=[MaterialShortfall(
        material="M-01", qty_needed=1040, qty_available_by_new_date=600, shortfall=440,
        late_arrivals=[InboundLine("M-01", "협력사A", "2026-08-29", 500)],
    )],
)

CLEAN_CONTENT = {
    "impact": SHORT_IMPACT,
    "adjustment_plan": "P-100 출하를 위해 M-01 자재 440개가 부족합니다. 협력사A에 긴급 요청 필요.",
    "supplier_request": "M-01 자재를 2026-08-28까지 440개 추가 입고 요청드립니다.",
    "customer_reply": "출하 일정 조율을 위해 검토 중입니다.",
}


# ── 순수: known_codes ─────────────────────────────────────────────────────


def test_known_codes_includes_product_and_shortfall_materials():
    assert known_codes(SHORT_IMPACT) == {"P-100", "M-01"}


def test_known_codes_includes_late_arrival_materials():
    impact = CascadeImpact(
        shipment=SHIPMENT, new_ship_date="2026-08-28", days_pulled_forward=3,
        qty_remaining_to_produce=260,
        shortfalls=[MaterialShortfall("M-02", 100, 50, 50,
                                      late_arrivals=[InboundLine("M-03", "협력사B", "2026-08-30", 10)])],
    )
    assert known_codes(impact) == {"P-100", "M-02", "M-03"}


# ── 순수: check_draft ───────────────────────────────────────────────────


def test_check_draft_passes_when_only_known_codes_used():
    assert check_draft("조정안", CLEAN_CONTENT["adjustment_plan"], SHORT_IMPACT) == []


def test_check_draft_flags_unknown_code():
    violations = check_draft("조정안", "M-99를 대체 조달하세요.", SHORT_IMPACT)
    assert len(violations) == 1
    assert violations[0].category == "unknown_code_reference"
    assert violations[0].severity == "high"


def test_check_draft_flags_each_unknown_code_once():
    violations = check_draft("조정안", "M-99와 M-99, P-999를 확인하세요.", SHORT_IMPACT)
    assert {v.detail.split("'")[1] for v in violations} == {"M-99", "P-999"}


def test_check_draft_flags_empty_text():
    assert check_draft("고객사 회신", "", SHORT_IMPACT)[0].category == "draft_missing"


def test_check_draft_flags_gateway_unavailable_sentinel():
    assert check_draft("협력업체 요청", UNAVAILABLE, SHORT_IMPACT)[0].category == "draft_missing"


# ── review: 세 초안 종합 판정 ──────────────────────────────────────────


def test_review_approves_when_all_drafts_are_grounded():
    verdict = review(CLEAN_CONTENT)
    assert verdict.recommendation == "approve"
    assert verdict.passed is True
    assert verdict.violations == []


def test_review_rejects_when_any_draft_has_unknown_code():
    bad = {**CLEAN_CONTENT, "supplier_request": "M-77도 같이 요청하세요."}
    verdict = review(bad)
    assert verdict.recommendation == "reject"
    assert verdict.passed is False


def test_review_rejects_when_draft_missing():
    bad = {**CLEAN_CONTENT, "customer_reply": UNAVAILABLE}
    verdict = review(bad)
    assert verdict.recommendation == "reject"
    assert "draft_missing" in [v.category for v in verdict.violations]


def test_review_score_scales_with_violation_count():
    bad = {**CLEAN_CONTENT, "adjustment_plan": "M-1, M-2, M-3를 확인."}
    assert review(bad).score == pytest.approx(0.45)   # 3건 × 0.15 (부동소수점 오차 허용)


# ── 순수: known_suppliers ─────────────────────────────────────────────────


def test_known_suppliers_collects_late_arrival_suppliers():
    assert known_suppliers(SHORT_IMPACT) == {"협력사A"}


def test_known_suppliers_empty_when_feasible():
    feasible = CascadeImpact(shipment=SHIPMENT, new_ship_date="2026-08-28",
                             days_pulled_forward=3, qty_remaining_to_produce=0, shortfalls=[])
    assert known_suppliers(feasible) == set()


# ── 순수: check_customer_leak ───────────────────────────────────────────


def test_check_customer_leak_passes_clean_reply():
    assert check_customer_leak(CLEAN_CONTENT["customer_reply"], SHORT_IMPACT) == []


def test_check_customer_leak_flags_internal_material_code():
    violations = check_customer_leak("M-01 자재 부족으로 지연됩니다.", SHORT_IMPACT)
    assert len(violations) == 1
    assert violations[0].category == "customer_facing_info_leak"
    assert violations[0].severity == "med"


def test_check_customer_leak_flags_supplier_name():
    violations = check_customer_leak("협력사A 사정으로 조율 중입니다.", SHORT_IMPACT)
    assert violations[0].category == "customer_facing_info_leak"


def test_check_customer_leak_does_not_flag_own_product_code():
    """고객사는 자기가 주문한 품목코드는 이미 안다 — 그건 노출이 아니다."""
    assert check_customer_leak("P-100 출하를 조율 중입니다.", SHORT_IMPACT) == []


def test_check_customer_leak_ignores_missing_or_unavailable_text():
    """비었거나 미가용 사유는 check_draft가 이미 draft_missing으로 잡는다 — 중복 신고 안 함."""
    assert check_customer_leak("", SHORT_IMPACT) == []
    assert check_customer_leak(UNAVAILABLE, SHORT_IMPACT) == []


# ── review: 정보 노출은 review(HITL), 환각은 reject ─────────────────────


def test_review_sends_customer_leak_to_human_not_reject():
    leaky = {**CLEAN_CONTENT, "customer_reply": "협력사A의 M-01 자재가 부족해 조율 중입니다."}
    verdict = review(leaky)

    assert verdict.recommendation == "review"   # reject가 아니다 — 환각이 아니라 정책 문제
    assert verdict.passed is True                # med는 passed를 안 깬다(high만 깬다)
    assert "customer_facing_info_leak" in [v.category for v in verdict.violations]
