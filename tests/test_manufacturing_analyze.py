# -*- coding: utf-8 -*-
"""
adapters.manufacturing.analyze 테스트.

신규 구축 — 산수를 코드가 확정한다는 게 핵심이므로, 합성 스냅샷으로 경계(부족/충분/미존재)를
고정하고, 실제 커밋된 `data/manufacturing/` CSV로 "M-01만 부족하다"는 데모 시나리오 자체를
회귀 테스트로 고정한다(손 계산과 코드 결과가 어긋나면 여기서 먼저 깨진다).
"""
from __future__ import annotations

from pathlib import Path

from adapters.manufacturing.analyze import ShipmentChangeTrigger, analyze_shipment_change
from adapters.manufacturing.erp_data import (
    BomLine,
    ErpSnapshot,
    InboundLine,
    ProductionActualLine,
    ShipmentLine,
    load_erp_snapshot,
)

import pytest

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "manufacturing"


def _snapshot(*, material_a_qty: int) -> ErpSnapshot:
    return ErpSnapshot(
        bom=[BomLine("X", "A", 2), BomLine("X", "B", 1)],
        inbound=[
            InboundLine("A", "Sup1", "2026-01-05", material_a_qty),
            InboundLine("B", "Sup1", "2026-01-05", 100),
        ],
        production_plan=[],
        production_actual=[ProductionActualLine("2026-01-01", "X", 40)],
        shipments=[ShipmentLine("C", "X", "2026-01-10", 100)],
    )


# ── 합성 스냅샷: 경계 케이스 ──────────────────────────────────────────────


def test_analyze_reports_shortfall_when_inbound_insufficient():
    snapshot = _snapshot(material_a_qty=50)   # 필요 120인데 50뿐
    trigger = ShipmentChangeTrigger(customer="C", product="X", new_ship_date="2026-01-08")

    impact = analyze_shipment_change(snapshot, trigger)

    assert impact.days_pulled_forward == 2       # 01-10 → 01-08
    assert impact.qty_remaining_to_produce == 60  # 100 - 40
    assert not impact.feasible
    assert [s.material for s in impact.shortfalls] == ["A"]
    shortfall = impact.shortfalls[0]
    assert (shortfall.qty_needed, shortfall.qty_available_by_new_date, shortfall.shortfall) == (120, 50, 70)


def test_analyze_feasible_when_inbound_covers_need():
    snapshot = _snapshot(material_a_qty=130)  # 필요 120 ≤ 130
    trigger = ShipmentChangeTrigger(customer="C", product="X", new_ship_date="2026-01-08")

    impact = analyze_shipment_change(snapshot, trigger)

    assert impact.feasible
    assert impact.shortfalls == []


def test_analyze_negative_days_when_date_pushed_back():
    snapshot = _snapshot(material_a_qty=130)
    trigger = ShipmentChangeTrigger(customer="C", product="X", new_ship_date="2026-01-15")

    impact = analyze_shipment_change(snapshot, trigger)

    assert impact.days_pulled_forward == -5  # 뒤로 밀림


def test_analyze_raises_for_unknown_shipment():
    snapshot = _snapshot(material_a_qty=130)
    trigger = ShipmentChangeTrigger(customer="없는고객사", product="X", new_ship_date="2026-01-08")

    with pytest.raises(ValueError, match="출하 계획에 없는 건"):
        analyze_shipment_change(snapshot, trigger)


# ── 실 데모 데이터: 시나리오 자체를 고정 ──────────────────────────────────


def test_analyze_against_demo_csv_finds_only_m01_short():
    """08-31→08-28(3일 앞당김) 트리거에서 M-01만 부족해야 한다(설계 시 손 계산과 대조)."""
    snapshot = load_erp_snapshot(DATA_DIR)
    trigger = ShipmentChangeTrigger(customer="고객사A", product="P-100", new_ship_date="2026-08-28")

    impact = analyze_shipment_change(snapshot, trigger)

    assert impact.days_pulled_forward == 3
    assert impact.qty_remaining_to_produce == 260   # 500 - (75+85+80)
    assert not impact.feasible
    assert [s.material for s in impact.shortfalls] == ["M-01"]

    shortfall = impact.shortfalls[0]
    assert shortfall.qty_needed == 1040              # 4 * 260
    assert shortfall.qty_available_by_new_date == 600
    assert shortfall.shortfall == 440
    assert [line.due_date for line in shortfall.late_arrivals] == ["2026-08-29"]
