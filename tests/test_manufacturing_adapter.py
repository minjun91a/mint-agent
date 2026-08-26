# -*- coding: utf-8 -*-
"""
adapters.manufacturing.adapter 특성화 테스트 — **세 번째 어댑터**.

이 슬라이스는 produce만 배선한다. review·publish는 다음 슬라이스 전까지 명시적으로
NotImplementedError를 낸다(research publish가 백엔드 미주입 시 그랬던 것과 같은 규약 —
기본 동작을 몰래 만들지 않는다).

실 데모 데이터(`data/manufacturing/`)를 그대로 로드해 쓴다 — `analyze.py` 테스트가 이미
이 데이터의 산수를 회귀 고정해뒀으므로, 여기서는 어댑터 배선(트리거→dict 조립)만 검증한다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from adapters.manufacturing.adapter import ManufacturingAdapter
from adapters.manufacturing.analyze import ShipmentChangeTrigger
from adapters.manufacturing.erp_data import load_erp_snapshot
from core.pipeline import PlatformAdapter

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "manufacturing"
TRIGGER = ShipmentChangeTrigger(customer="고객사A", product="P-100", new_ship_date="2026-08-28")


class _FakeGateway:
    available = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, system: str, prompt: str, **kwargs) -> str:
        self.calls.append(system)
        return "초안"


def _adapter(gateway: _FakeGateway | None = None) -> ManufacturingAdapter:
    return ManufacturingAdapter(load_erp_snapshot(DATA_DIR), gateway=gateway or _FakeGateway())


def test_manufacturing_adapter_satisfies_platform_adapter_port():
    """엔진은 이 클래스를 포트로만 다룬다 — 세 번째 어댑터도 같은 계약을 만족한다."""
    assert isinstance(_adapter(), PlatformAdapter)


def test_produce_returns_trigger_impact_and_three_drafts():
    out = _adapter().produce(TRIGGER)

    assert out["trigger"] is TRIGGER
    assert out["impact"].shortfalls[0].material == "M-01"   # analyze.py가 확정한 사실
    assert out["adjustment_plan"] == "초안"
    assert out["supplier_request"] == "초안"
    assert out["customer_reply"] == "초안"


def test_produce_calls_gateway_three_times_with_distinct_contracts():
    gw = _FakeGateway()
    _adapter(gw).produce(TRIGGER)

    assert len(gw.calls) == 3
    assert len(set(gw.calls)) == 3   # 조정안·협력업체·고객사 — 셋 다 다른 system prompt


def test_review_not_yet_wired():
    with pytest.raises(NotImplementedError, match="review"):
        _adapter().review({})


def test_publish_not_yet_wired():
    with pytest.raises(NotImplementedError, match="publish"):
        _adapter().publish({})
