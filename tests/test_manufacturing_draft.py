# -*- coding: utf-8 -*-
"""
adapters.manufacturing.draft 테스트.

신규 구축 — LLM이 끼므로 순수 로직(사실 블록 렌더링)은 목 없이, 세 초안 함수는 가짜 게이트웨이
주입(research summarize와 동일 패턴)으로 검증한다. 핵심 계약: **세 함수가 독립 호출**이라는 것과
**사실 블록 밖 숫자를 안 만든다**는 규칙이 각자의 SYSTEM_PROMPT에 박혀 있다는 것.
"""
from __future__ import annotations

from adapters.manufacturing.analyze import CascadeImpact, MaterialShortfall
from adapters.manufacturing.draft import (
    UNAVAILABLE,
    _ADJUSTMENT_SYSTEM_PROMPT,
    _CUSTOMER_SYSTEM_PROMPT,
    _SUPPLIER_SYSTEM_PROMPT,
    draft_adjustment_plan,
    draft_customer_reply,
    draft_supplier_request,
    render_impact_facts,
)
from adapters.manufacturing.erp_data import InboundLine, ShipmentLine

SHIPMENT = ShipmentLine("고객사A", "P-100", "2026-08-31", 500)

FEASIBLE_IMPACT = CascadeImpact(
    shipment=SHIPMENT, new_ship_date="2026-08-28", days_pulled_forward=3,
    qty_remaining_to_produce=260, shortfalls=[],
)

SHORT_IMPACT = CascadeImpact(
    shipment=SHIPMENT, new_ship_date="2026-08-28", days_pulled_forward=3,
    qty_remaining_to_produce=260,
    shortfalls=[MaterialShortfall(
        material="M-01", qty_needed=1040, qty_available_by_new_date=600, shortfall=440,
        late_arrivals=[InboundLine("M-01", "협력사A", "2026-08-29", 500)],
    )],
)


class _FakeGateway:
    """주입용 가짜 LLM 게이트웨이 — 호출 인자를 기록하고 canned 텍스트를 돌려준다."""

    def __init__(self, *, text: str = "초안", available: bool = True) -> None:
        self._text = text
        self.available = available
        self.calls: list[dict] = []

    def complete(self, system: str, prompt: str, **kwargs) -> str:
        self.calls.append({"system": system, "prompt": prompt, **kwargs})
        return self._text


# ── 순수: render_impact_facts ────────────────────────────────────────


def test_render_impact_facts_reports_no_shortfall_when_feasible():
    text = render_impact_facts(FEASIBLE_IMPACT)
    assert "부족한 자재 없음" in text
    assert "M-01" not in text


def test_render_impact_facts_lists_shortfall_and_late_arrival():
    text = render_impact_facts(SHORT_IMPACT)
    assert "[M-01] 필요 1040개" in text
    assert "부족 440개" in text
    assert "지연 입고: 협력사A 2026-08-29 예정 500개" in text


def test_render_impact_facts_labels_pulled_forward_direction():
    assert "3일 앞당김" in render_impact_facts(SHORT_IMPACT)


def test_render_impact_facts_labels_postponed_direction():
    postponed = CascadeImpact(shipment=SHIPMENT, new_ship_date="2026-09-05",
                              days_pulled_forward=-5, qty_remaining_to_produce=0, shortfalls=[])
    assert "5일 연기" in render_impact_facts(postponed)


# ── 게이트웨이 주입: 세 초안 함수 ─────────────────────────────────────


def test_draft_adjustment_plan_uses_its_own_contract():
    gw = _FakeGateway()
    draft_adjustment_plan(SHORT_IMPACT, gateway=gw)
    assert gw.calls[0]["system"] == _ADJUSTMENT_SYSTEM_PROMPT


def test_draft_supplier_request_uses_its_own_contract():
    gw = _FakeGateway()
    draft_supplier_request(SHORT_IMPACT, gateway=gw)
    assert gw.calls[0]["system"] == _SUPPLIER_SYSTEM_PROMPT


def test_draft_customer_reply_uses_its_own_contract():
    gw = _FakeGateway()
    draft_customer_reply(SHORT_IMPACT, gateway=gw)
    assert gw.calls[0]["system"] == _CUSTOMER_SYSTEM_PROMPT


def test_draft_calls_are_prose_not_json():
    gw = _FakeGateway()
    draft_adjustment_plan(SHORT_IMPACT, gateway=gw)
    assert gw.calls[0]["json_mode"] is False


def test_draft_prompt_is_exactly_the_impact_facts_block():
    gw = _FakeGateway()
    draft_customer_reply(SHORT_IMPACT, gateway=gw)
    assert gw.calls[0]["prompt"] == render_impact_facts(SHORT_IMPACT)


def test_draft_strips_gateway_whitespace():
    gw = _FakeGateway(text="  회신 초안  ")
    assert draft_customer_reply(SHORT_IMPACT, gateway=gw) == "회신 초안"


def test_draft_returns_reason_when_gateway_unavailable():
    gw = _FakeGateway(available=False)
    assert draft_adjustment_plan(SHORT_IMPACT, gateway=gw) == UNAVAILABLE
    assert gw.calls == []


def test_three_drafts_are_independent_calls_with_distinct_contracts():
    """1회 통합 호출이 아니라 3회 독립 호출 — 하나가 실패해도 나머지는 살아남는 전제(설계 결정)."""
    gw = _FakeGateway()
    draft_adjustment_plan(SHORT_IMPACT, gateway=gw)
    draft_supplier_request(SHORT_IMPACT, gateway=gw)
    draft_customer_reply(SHORT_IMPACT, gateway=gw)

    assert len(gw.calls) == 3
    assert len({c["system"] for c in gw.calls}) == 3
