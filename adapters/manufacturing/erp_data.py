# -*- coding: utf-8 -*-
"""
adapters.manufacturing.erp_data — 모의 ERP CSV(`data/manufacturing/`) → 구조화 레코드.

research의 `search`/`fetch`가 "IO로 얻은 것을 구조화한다"는 역할이었던 것과 같은 자리다.
LLM은 이 레코드를 **한 줄도 계산하지 않는다** — 수량·부족분 산수는 `analyze.py`가 맡고,
여기서는 원본 CSV를 있는 그대로 파싱만 한다(해석 없음).

경로 주입(`core.memory.ChannelMemory`·`adapters.research.publish.FileDigestSink`와 같은 규약):
CSV가 있는 디렉터리를 인자로 받는다 — 실 데이터는 `data/manufacturing/`, 테스트는 tmp 디렉터리.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

_BOM_FILE = "bom.csv"
_INBOUND_FILE = "inbound_schedule.csv"
_PRODUCTION_PLAN_FILE = "production_plan.csv"
_PRODUCTION_ACTUAL_FILE = "production_actual.csv"
_SHIPMENT_FILE = "shipment_plan.csv"


@dataclass(frozen=True)
class BomLine:
    """품목 1개를 만드는 데 필요한 자재 1종의 소요량."""

    product: str
    material: str
    qty_per_unit: int


@dataclass(frozen=True)
class InboundLine:
    """협력업체발 자재 입고 예정 1건."""

    material: str
    supplier: str
    due_date: str  # ISO(YYYY-MM-DD)
    qty: int


@dataclass(frozen=True)
class ProductionPlanLine:
    """일자별 품목 생산 계획 1건."""

    date: str
    product: str
    qty: int


@dataclass(frozen=True)
class ProductionActualLine:
    """일자별 품목 실제 생산 완료 1건."""

    date: str
    product: str
    qty: int


@dataclass(frozen=True)
class ShipmentLine:
    """고객사 출하 계획 1건."""

    customer: str
    product: str
    ship_date: str  # ISO(YYYY-MM-DD)
    qty: int


@dataclass(frozen=True)
class ErpSnapshot:
    """5개 CSV를 한 시점의 스냅샷으로 묶은 것. `analyze.py`가 소비하는 입력 전부."""

    bom: list[BomLine]
    inbound: list[InboundLine]
    production_plan: list[ProductionPlanLine]
    production_actual: list[ProductionActualLine]
    shipments: list[ShipmentLine]


def _row_to_bom(row: dict[str, str]) -> BomLine:
    return BomLine(product=row["품목코드"], material=row["자재코드"],
                   qty_per_unit=int(row["소요수량"]))


def _row_to_inbound(row: dict[str, str]) -> InboundLine:
    return InboundLine(material=row["자재코드"], supplier=row["협력업체"],
                       due_date=row["입고예정일"], qty=int(row["예정수량"]))


def _row_to_production_plan(row: dict[str, str]) -> ProductionPlanLine:
    return ProductionPlanLine(date=row["일자"], product=row["품목코드"], qty=int(row["계획수량"]))


def _row_to_production_actual(row: dict[str, str]) -> ProductionActualLine:
    return ProductionActualLine(date=row["일자"], product=row["품목코드"], qty=int(row["완성수량"]))


def _row_to_shipment(row: dict[str, str]) -> ShipmentLine:
    return ShipmentLine(customer=row["고객사"], product=row["품목코드"],
                        ship_date=row["출하예정일"], qty=int(row["출하수량"]))


def parse_csv_rows(rows: list[dict[str, str]], row_fn: Callable[[dict[str, str]], T]) -> list[T]:
    """dict 행 목록 → 레코드 목록 (순수). 실 파일 없이 로딩 로직을 테스트하는 진입점."""
    return [row_fn(row) for row in rows]


def _read_csv(path: Path) -> list[dict[str, str]]:
    """CSV 파일 → dict 행 목록. `utf-8-sig`로 Excel이 남기는 BOM을 흡수한다(IO)."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_erp_snapshot(data_dir: str | Path) -> ErpSnapshot:
    """`data_dir`(5개 CSV가 있는 디렉터리) → `ErpSnapshot`.

    파일명은 고정(`bom.csv` 등) — 갈아끼우는 건 내용이지 이름이 아니다(실 ERP export도 같은 규약).
    """
    d = Path(data_dir)
    return ErpSnapshot(
        bom=parse_csv_rows(_read_csv(d / _BOM_FILE), _row_to_bom),
        inbound=parse_csv_rows(_read_csv(d / _INBOUND_FILE), _row_to_inbound),
        production_plan=parse_csv_rows(_read_csv(d / _PRODUCTION_PLAN_FILE), _row_to_production_plan),
        production_actual=parse_csv_rows(_read_csv(d / _PRODUCTION_ACTUAL_FILE), _row_to_production_actual),
        shipments=parse_csv_rows(_read_csv(d / _SHIPMENT_FILE), _row_to_shipment),
    )


__all__ = [
    "BomLine", "InboundLine", "ProductionPlanLine", "ProductionActualLine", "ShipmentLine",
    "ErpSnapshot", "parse_csv_rows", "load_erp_snapshot",
]
