# -*- coding: utf-8 -*-
"""
adapters.manufacturing.analyze — 출하일 변경 → 역방향 연쇄 영향 (순수·결정적).

**핵심 설계 결정**: 수량·부족분은 여기서 산수로 확정한다. LLM은 이 결과를 `draft.py`에서
**서술**만 한다 — "몇 개가 부족한가"를 모델에게 계산시키지 않는다(환각 위험이 가장 큰 지점이
바로 숫자다). `review.py`(다음 슬라이스)가 LLM 초안의 숫자를 이 결과와 대조한다 — research의
`check_citations`가 인용 번호를 `hits`와 대조하는 것과 같은 자리.

두 방향을 다룬다 — 고객사발(출하일 변경, `ShipmentChangeTrigger`)과 공급사발(자재 입고 지연,
`MaterialDelayTrigger`). **후자는 새 계산 로직이 없다**: 자재가 늦게 오는 것과 출하일이
당겨지는 것은 둘 다 "새 마감까지 자재가 확보되는가"라는 같은 질문이다 — 스냅샷을 바꿔서
같은 `analyze_shipment_change`를 재사용한다(`analyze_material_delay` 참조). 같은 코어로
양방향이 다 돌면 도메인 어댑터 설계가 옳다는 증거다(D5).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from adapters.manufacturing.erp_data import ErpSnapshot, InboundLine, ShipmentLine


@dataclass(frozen=True)
class ShipmentChangeTrigger:
    """고객사발 트리거: 특정 출하 건의 출하예정일을 `new_ship_date`로 바꾼다."""

    customer: str
    product: str
    new_ship_date: str  # ISO(YYYY-MM-DD)


@dataclass(frozen=True)
class MaterialShortfall:
    """새 출하일까지 확보되지 않는 자재 1종. `late_arrivals`가 부족한 이유(증거)다."""

    material: str
    qty_needed: int
    qty_available_by_new_date: int
    shortfall: int
    late_arrivals: list[InboundLine] = field(default_factory=list)


@dataclass(frozen=True)
class CascadeImpact:
    """트리거 적용 결과 — LLM 초안과 review 게이트가 공유하는 사실 기반(ground truth)."""

    shipment: ShipmentLine
    new_ship_date: str
    days_pulled_forward: int  # 양수=앞당김, 음수=늦춰짐
    qty_remaining_to_produce: int
    shortfalls: list[MaterialShortfall]

    @property
    def feasible(self) -> bool:
        """새 출하일까지 부족한 자재가 하나도 없는가."""
        return not self.shortfalls


def _find_shipment(snapshot: ErpSnapshot, customer: str, product: str) -> ShipmentLine:
    for s in snapshot.shipments:
        if s.customer == customer and s.product == product:
            return s
    raise ValueError(f"출하 계획에 없는 건입니다: 고객사={customer!r}, 품목={product!r}")


def _produced_so_far(snapshot: ErpSnapshot, product: str) -> int:
    """해당 품목의 실제 생산 완료 누계 (`production_actual` 전체 — 특정 출하 건에 묶이지 않음)."""
    return sum(a.qty for a in snapshot.production_actual if a.product == product)


def _material_needed(snapshot: ErpSnapshot, product: str, qty: int) -> dict[str, int]:
    """품목을 `qty`개 더 만드는 데 필요한 자재별 총량 (BOM 전개)."""
    return {b.material: b.qty_per_unit * qty for b in snapshot.bom if b.product == product}


def _material_position(snapshot: ErpSnapshot, material: str,
                        by_date: date) -> tuple[int, list[InboundLine]]:
    """`by_date`까지 입고 확정된 자재 수량과, 그 이후 도착(=지연) 입고 목록."""
    available = 0
    late: list[InboundLine] = []
    for line in snapshot.inbound:
        if line.material != material:
            continue
        if date.fromisoformat(line.due_date) <= by_date:
            available += line.qty
        else:
            late.append(line)
    return available, late


def analyze_shipment_change(snapshot: ErpSnapshot, trigger: ShipmentChangeTrigger) -> CascadeImpact:
    """트리거(출하일 변경) → 역방향 연쇄 영향. 존재하지 않는 출하 건이면 `ValueError`.

    순서: ① 대상 출하 건 조회 → ② 남은 생산량(출하수량 − 기생산량) → ③ BOM 전개로
    자재별 필요량 산출 → ④ 자재별로 새 출하일까지 확보량과 비교해 부족분·지연 입고 판정.
    """
    shipment = _find_shipment(snapshot, trigger.customer, trigger.product)
    new_date = date.fromisoformat(trigger.new_ship_date)
    old_date = date.fromisoformat(shipment.ship_date)

    remaining = max(0, shipment.qty - _produced_so_far(snapshot, trigger.product))
    needed = _material_needed(snapshot, trigger.product, remaining)

    shortfalls: list[MaterialShortfall] = []
    for material, qty_needed in sorted(needed.items()):
        available, late = _material_position(snapshot, material, new_date)
        shortfall = max(0, qty_needed - available)
        if shortfall > 0:
            shortfalls.append(MaterialShortfall(
                material=material, qty_needed=qty_needed,
                qty_available_by_new_date=available, shortfall=shortfall,
                late_arrivals=late,
            ))

    return CascadeImpact(
        shipment=shipment,
        new_ship_date=trigger.new_ship_date,
        days_pulled_forward=(old_date - new_date).days,
        qty_remaining_to_produce=remaining,
        shortfalls=shortfalls,
    )


@dataclass(frozen=True)
class MaterialDelayTrigger:
    """공급사발 트리거: `material`의 `original_due_date` 입고 건이 `new_due_date`로 늦춰진다.

    영향을 확인할 출하 건(`customer`·`product`)도 함께 지정한다 — `ShipmentChangeTrigger`와
    같은 모양이라 `ManufacturingAdapter.produce()`가 둘을 같은 방식으로 받는다.
    """

    customer: str
    product: str
    material: str
    original_due_date: str  # ISO(YYYY-MM-DD)
    new_due_date: str  # ISO(YYYY-MM-DD)


def apply_material_delay(snapshot: ErpSnapshot, trigger: MaterialDelayTrigger) -> ErpSnapshot:
    """`trigger`가 가리키는 입고 건 하나의 날짜만 늦춘 새 스냅샷 (순수). 원본은 그대로 둔다.

    가리키는 입고 건이 없으면 `ValueError` — 트리거가 실재하지 않는 데이터를 가리키면 안 된다
    (`_find_shipment`와 같은 정직성).
    """
    matched = False
    updated: list[InboundLine] = []
    for line in snapshot.inbound:
        if line.material == trigger.material and line.due_date == trigger.original_due_date:
            matched = True
            updated.append(InboundLine(material=line.material, supplier=line.supplier,
                                       due_date=trigger.new_due_date, qty=line.qty))
        else:
            updated.append(line)

    if not matched:
        raise ValueError(
            f"입고 계획에 없는 건입니다: 자재={trigger.material!r}, "
            f"입고예정일={trigger.original_due_date!r}"
        )

    return ErpSnapshot(bom=snapshot.bom, inbound=updated,
                       production_plan=snapshot.production_plan,
                       production_actual=snapshot.production_actual,
                       shipments=snapshot.shipments)


def analyze_material_delay(snapshot: ErpSnapshot, trigger: MaterialDelayTrigger) -> CascadeImpact:
    """트리거(자재 입고 지연) → 순방향 연쇄 영향.

    출하일 자체는 안 바뀐다 — 그래서 지연이 반영된 스냅샷에 "새 출하일 = 원래 출하일"인
    `ShipmentChangeTrigger`를 태워 `analyze_shipment_change`를 그대로 재사용한다. 결과
    `days_pulled_forward`가 0인 것이 정상이다(날짜가 아니라 자재 쪽이 바뀐 트리거이므로).
    """
    delayed = apply_material_delay(snapshot, trigger)
    shipment = _find_shipment(snapshot, trigger.customer, trigger.product)
    return analyze_shipment_change(
        delayed, ShipmentChangeTrigger(customer=trigger.customer, product=trigger.product,
                                       new_ship_date=shipment.ship_date))


__all__ = [
    "ShipmentChangeTrigger", "MaterialDelayTrigger", "MaterialShortfall", "CascadeImpact",
    "analyze_shipment_change", "apply_material_delay", "analyze_material_delay",
]
