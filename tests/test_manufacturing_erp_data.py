# -*- coding: utf-8 -*-
"""
adapters.manufacturing.erp_data 테스트.

신규 구축(원본 대응 없음 — parity 대상 아님). 순수 행 매핑(`parse_csv_rows`)은 파일 없이,
`load_erp_snapshot`은 tmp 디렉터리에 실제 CSV를 써서 IO(+Excel BOM 흡수)까지 검증한다.
"""
from __future__ import annotations

from pathlib import Path

from adapters.manufacturing.erp_data import (
    BomLine,
    InboundLine,
    ProductionActualLine,
    ProductionPlanLine,
    ShipmentLine,
    _row_to_bom,
    _row_to_inbound,
    _row_to_production_actual,
    _row_to_production_plan,
    _row_to_shipment,
    load_erp_snapshot,
    parse_csv_rows,
)


# ── 순수: 행 매핑 ────────────────────────────────────────────────────────


def test_parse_csv_rows_maps_bom():
    rows = [{"품목코드": "P-100", "자재코드": "M-01", "소요수량": "4"}]
    assert parse_csv_rows(rows, _row_to_bom) == [BomLine("P-100", "M-01", 4)]


def test_parse_csv_rows_maps_inbound():
    rows = [{"자재코드": "M-01", "협력업체": "협력사A", "입고예정일": "2026-08-25", "예정수량": "600"}]
    assert parse_csv_rows(rows, _row_to_inbound) == [
        InboundLine("M-01", "협력사A", "2026-08-25", 600)
    ]


def test_parse_csv_rows_maps_production_plan():
    rows = [{"일자": "2026-08-24", "품목코드": "P-100", "계획수량": "80"}]
    assert parse_csv_rows(rows, _row_to_production_plan) == [
        ProductionPlanLine("2026-08-24", "P-100", 80)
    ]


def test_parse_csv_rows_maps_production_actual():
    rows = [{"일자": "2026-08-24", "품목코드": "P-100", "완성수량": "75"}]
    assert parse_csv_rows(rows, _row_to_production_actual) == [
        ProductionActualLine("2026-08-24", "P-100", 75)
    ]


def test_parse_csv_rows_maps_shipment():
    rows = [{"고객사": "고객사A", "품목코드": "P-100", "출하예정일": "2026-08-31", "출하수량": "500"}]
    assert parse_csv_rows(rows, _row_to_shipment) == [
        ShipmentLine("고객사A", "P-100", "2026-08-31", 500)
    ]


def test_parse_csv_rows_empty_list_returns_empty():
    assert parse_csv_rows([], _row_to_bom) == []


# ── IO: load_erp_snapshot ────────────────────────────────────────────────

_FILES = {
    "bom.csv": "품목코드,자재코드,소요수량\nP-100,M-01,4\n",
    "inbound_schedule.csv": "자재코드,협력업체,입고예정일,예정수량\nM-01,협력사A,2026-08-25,600\n",
    "production_plan.csv": "일자,품목코드,계획수량\n2026-08-24,P-100,80\n",
    "production_actual.csv": "일자,품목코드,완성수량\n2026-08-24,P-100,75\n",
    "shipment_plan.csv": "고객사,품목코드,출하예정일,출하수량\n고객사A,P-100,2026-08-31,500\n",
}


def _write_snapshot_dir(tmp_path: Path, *, bom_bytes: bytes | None = None) -> Path:
    for name, content in _FILES.items():
        data = bom_bytes + content.encode("utf-8") if name == "bom.csv" and bom_bytes else content
        mode, enc = ("wb", None) if isinstance(data, bytes) else ("w", "utf-8")
        with (tmp_path / name).open(mode, encoding=enc) as f:
            f.write(data)
    return tmp_path


def test_load_erp_snapshot_reads_all_five_files(tmp_path: Path):
    snap = load_erp_snapshot(_write_snapshot_dir(tmp_path))
    assert snap.bom == [BomLine("P-100", "M-01", 4)]
    assert snap.inbound == [InboundLine("M-01", "협력사A", "2026-08-25", 600)]
    assert snap.production_plan == [ProductionPlanLine("2026-08-24", "P-100", 80)]
    assert snap.production_actual == [ProductionActualLine("2026-08-24", "P-100", 75)]
    assert snap.shipments == [ShipmentLine("고객사A", "P-100", "2026-08-31", 500)]


def test_load_erp_snapshot_absorbs_excel_bom_prefix(tmp_path: Path):
    """Excel이 CSV를 저장할 때 남기는 UTF-8 BOM(`﻿`)이 첫 컬럼명에 섞여 들어가지 않아야 한다."""
    snap = load_erp_snapshot(_write_snapshot_dir(tmp_path, bom_bytes=b"\xef\xbb\xbf"))
    assert snap.bom == [BomLine("P-100", "M-01", 4)]
