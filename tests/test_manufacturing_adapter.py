# -*- coding: utf-8 -*-
"""
adapters.manufacturing.adapter 특성화 테스트 — **세 번째 어댑터**.

produce·review·publish 3단이 전부 배선됐다. publish는 백엔드 미주입 시 명시적으로
NotImplementedError를 낸다(research publish와 같은 규약 — 기본 동작을 몰래 만들지 않는다).

실 데모 데이터(`data/manufacturing/`)를 그대로 로드해 쓴다 — `analyze.py` 테스트가 이미
이 데이터의 산수를 회귀 고정해뒀으므로, 여기서는 어댑터 배선(트리거→dict 조립)과
produce→gate→publish 3단 관통만 검증한다(research adapter 테스트와 같은 구성).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from adapters.manufacturing.adapter import ManufacturingAdapter
from adapters.manufacturing.analyze import ShipmentChangeTrigger
from adapters.manufacturing.erp_data import load_erp_snapshot
from adapters.manufacturing.publish import FileConfirmationSink, ManufacturingPublisher
from core.pipeline import PlatformAdapter, gate

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "manufacturing"
TRIGGER = ShipmentChangeTrigger(customer="고객사A", product="P-100", new_ship_date="2026-08-28")


class _FakeGateway:
    available = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, system: str, prompt: str, **kwargs) -> str:
        self.calls.append(system)
        return "초안"


def _adapter(gateway: _FakeGateway | None = None, *,
            publisher: ManufacturingPublisher | None = None) -> ManufacturingAdapter:
    return ManufacturingAdapter(load_erp_snapshot(DATA_DIR),
                                gateway=gateway or _FakeGateway(), publisher=publisher)


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


def test_review_approves_clean_drafts():
    """가짜 게이트웨이가 낸 "초안" 텍스트엔 코드가 없으니 위반이 없다."""
    decision = _adapter().review(_adapter().produce(TRIGGER))
    assert decision.recommendation == "approve"


def test_review_rejects_hallucinated_code():
    class _HallucinatingGateway(_FakeGateway):
        def complete(self, system: str, prompt: str, **kwargs) -> str:
            super().complete(system, prompt, **kwargs)
            return "협력사 재고를 보니 M-99가 급히 필요합니다."   # 존재하지 않는 자재코드

    adapter = _adapter(_HallucinatingGateway())
    decision = adapter.review(adapter.produce(TRIGGER))

    assert decision.recommendation == "reject"
    assert "unknown_code_reference" in [v.category for v in decision.violations]


def test_publish_requires_backend_injection():
    """발행은 되돌리기 어려운 외부 행위 — 기본 sink를 몰래 만들지 않는다."""
    with pytest.raises(NotImplementedError, match="백엔드"):
        _adapter().publish({})


def test_publish_delegates_to_injected_publisher(tmp_path: Path):
    adapter = _adapter(publisher=ManufacturingPublisher(
        FileConfirmationSink(tmp_path), clock=lambda: "2026-08-26T09:00:00"))

    out = adapter.publish(adapter.produce(TRIGGER))
    assert Path(out["location"]).exists()


# ── 3단 계약 관통: produce → gate → publish ──────────────────────────


def test_produce_gate_publish_roundtrip(tmp_path: Path):
    """엔진 게이트가 통과시킨 조정안만 발행된다 — PlatformAdapter 3단이 실제로 이어진다."""
    adapter = _adapter(publisher=ManufacturingPublisher(
        FileConfirmationSink(tmp_path), clock=lambda: "2026-08-26T09:00:00"))

    produced = adapter.produce(TRIGGER)
    decision = gate(adapter, produced)
    assert decision.proceed is True

    out = adapter.publish(produced)
    doc = Path(out["location"]).read_text(encoding="utf-8")
    assert "M-01" in doc


def test_gate_blocks_publish_on_hallucinated_code(tmp_path: Path):
    """초안이 존재하지 않는 자재코드를 지어내면 게이트가 막고, 발행은 일어나지 않는다."""
    class _HallucinatingGateway(_FakeGateway):
        def complete(self, system: str, prompt: str, **kwargs) -> str:
            super().complete(system, prompt, **kwargs)
            return "M-99가 급히 필요합니다."   # 존재하지 않는 자재코드

    adapter = _adapter(_HallucinatingGateway(), publisher=ManufacturingPublisher(
        FileConfirmationSink(tmp_path), clock=lambda: "2026-08-26T09:00:00"))

    produced = adapter.produce(TRIGGER)
    decision = gate(adapter, produced)

    assert decision.proceed is False
    assert decision.verdict.recommendation == "reject"
    assert list(tmp_path.glob("*.md")) == []   # 발행되지 않았다
