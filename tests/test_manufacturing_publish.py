# -*- coding: utf-8 -*-
"""
adapters.manufacturing.publish 특성화 테스트.

원본에 발행 단계가 없어 parity 대조 대상이 아니다(신규 구축, research publish와 같은 표기).
렌더링은 순수라 목 없이 검증하고, 전달은 가짜 sink 주입 + `FileConfirmationSink`는 tmp
디렉터리로 실제 쓰기까지 확인한다.
"""
from __future__ import annotations

from pathlib import Path

from adapters.manufacturing.analyze import CascadeImpact, MaterialShortfall, ShipmentChangeTrigger
from adapters.manufacturing.erp_data import InboundLine, ShipmentLine
from adapters.manufacturing.publish import (
    FileConfirmationSink,
    ManufacturingPublisher,
    build_meta,
    render_confirmation,
)

CLOCK = lambda: "2026-08-26T09:00:00"  # noqa: E731 — 결정적 시계 주입

SHIPMENT = ShipmentLine("고객사A", "P-100", "2026-08-31", 500)
TRIGGER = ShipmentChangeTrigger(customer="고객사A", product="P-100", new_ship_date="2026-08-28")

SHORT_IMPACT = CascadeImpact(
    shipment=SHIPMENT, new_ship_date="2026-08-28", days_pulled_forward=3,
    qty_remaining_to_produce=260,
    shortfalls=[MaterialShortfall(
        material="M-01", qty_needed=1040, qty_available_by_new_date=600, shortfall=440,
        late_arrivals=[InboundLine("M-01", "협력사A", "2026-08-29", 500)],
    )],
)

FEASIBLE_IMPACT = CascadeImpact(
    shipment=SHIPMENT, new_ship_date="2026-08-28", days_pulled_forward=3,
    qty_remaining_to_produce=260, shortfalls=[],
)

CONTENT = {
    "trigger": TRIGGER,
    "impact": SHORT_IMPACT,
    "adjustment_plan": "M-01 자재 440개 부족 — 협력사A에 긴급 요청 필요.",
    "supplier_request": "M-01 자재를 2026-08-28까지 440개 추가 요청드립니다.",
    "customer_reply": "출하 일정을 검토 중입니다.",
}


class _FakeSink:
    """주입용 가짜 sink — 받은 문서·메타를 기록하고 가짜 위치를 돌려준다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def deliver(self, doc: str, meta: dict) -> str:
        self.calls.append((doc, meta))
        return "fake://confirmation/1"


# ── 순수: build_meta ────────────────────────────────────────────────


def test_build_meta_extracts_trigger_and_feasibility():
    meta = build_meta(CONTENT, generated_at="2026-08-26T09:00:00")
    assert meta["customer"] == "고객사A"
    assert meta["product"] == "P-100"
    assert meta["feasible"] is False
    assert meta["shortfall_count"] == 1


def test_build_meta_slug_combines_date_customer_product():
    meta = build_meta(CONTENT, generated_at="")
    assert meta["slug"] == "2026-08-28-고객사a-p-100"


# ── 순수: render_confirmation ───────────────────────────────────────


def test_render_confirmation_reports_shortfall_status():
    doc = render_confirmation(CONTENT, generated_at="2026-08-26T09:00:00")
    assert "부족 자재 1건" in doc


def test_render_confirmation_reports_feasible_status():
    feasible = {**CONTENT, "impact": FEASIBLE_IMPACT}
    doc = render_confirmation(feasible, generated_at="2026-08-26T09:00:00")
    assert "실행 가능" in doc


def test_render_confirmation_includes_impact_facts_and_all_three_drafts():
    doc = render_confirmation(CONTENT)
    assert "[M-01] 필요 1040개" in doc               # 사실 블록
    assert "협력사A에 긴급 요청 필요" in doc          # 조정안
    assert "440개 추가 요청드립니다" in doc           # 협력업체 요청
    assert "출하 일정을 검토 중입니다" in doc          # 고객사 회신


# ── I/O: ManufacturingPublisher + FileConfirmationSink ───────────────


def test_publisher_delivers_rendered_doc_to_sink():
    sink = _FakeSink()
    out = ManufacturingPublisher(sink, clock=CLOCK).publish(CONTENT)

    assert out["location"] == "fake://confirmation/1"
    assert out["feasible"] is False
    assert out["shortfall_count"] == 1
    doc, meta = sink.calls[0]
    assert "M-01" in doc
    assert meta["generated_at"] == "2026-08-26T09:00:00"


def test_file_confirmation_sink_writes_readable_file(tmp_path: Path):
    out = ManufacturingPublisher(FileConfirmationSink(tmp_path), clock=CLOCK).publish(CONTENT)

    path = Path(out["location"])
    assert path.exists()
    assert "고객사A" in path.read_text(encoding="utf-8")


def test_file_confirmation_sink_does_not_overwrite_by_default(tmp_path: Path):
    publisher = ManufacturingPublisher(FileConfirmationSink(tmp_path), clock=CLOCK)
    first = publisher.publish(CONTENT)
    second = publisher.publish(CONTENT)

    assert first["location"] != second["location"]
    assert Path(first["location"]).exists()
    assert Path(second["location"]).exists()
