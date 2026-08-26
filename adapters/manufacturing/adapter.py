# -*- coding: utf-8 -*-
"""
adapters.manufacturing.adapter — manufacturing 어댑터 (`core.pipeline.PlatformAdapter` 구현).

**세 번째 어댑터.** `produce`·`review`가 이 슬라이스까지 배선됐다 — `publish`(확정 조정안)는
다음 슬라이스(research·youtube도 같은 순서로 지어졌다: 무엇을 만들고 검증할지가 먼저 정해진
뒤에야 "확정"이 의미를 가진다).
"""
from __future__ import annotations

from typing import Any

from adapters.manufacturing.analyze import ShipmentChangeTrigger, analyze_shipment_change
from adapters.manufacturing.draft import (
    _DEFAULT_MODEL,
    draft_adjustment_plan,
    draft_customer_reply,
    draft_supplier_request,
)
from adapters.manufacturing.erp_data import ErpSnapshot
from adapters.manufacturing.review import review as review_drafts
from core.llm import LLMGateway
from core.trust.verify import Verdict


class ManufacturingAdapter:
    """manufacturing produce/review/publish 어댑터. 스냅샷은 생성 시 주입(경로 주입과 같은 규약 —
    IO는 호출부가 `load_erp_snapshot`으로 미리 해서 넘긴다, 어댑터는 스냅샷을 모른 채 재사용만)."""

    def __init__(self, snapshot: ErpSnapshot, *, model: str = _DEFAULT_MODEL,
                 gateway: LLMGateway | None = None) -> None:
        self._snapshot = snapshot
        self._model = model
        self._gateway = gateway

    def produce(self, brief: ShipmentChangeTrigger) -> dict[str, Any]:
        """트리거(고객사발 출하일 변경) → 연쇄 영향 + 3종 초안.

        `impact`를 결과에 그대로 담는다 — research의 `hits`와 같은 자리: review 게이트가
        초안 속 숫자를 이 사실과 대조할 근거.
        """
        impact = analyze_shipment_change(self._snapshot, brief)
        kwargs = {"model": self._model, "gateway": self._gateway}
        return {
            "trigger": brief,
            "impact": impact,
            "adjustment_plan": draft_adjustment_plan(impact, **kwargs),
            "supplier_request": draft_supplier_request(impact, **kwargs),
            "customer_reply": draft_customer_reply(impact, **kwargs),
        }

    def review(self, content: dict[str, Any], context: Any = None) -> Verdict:
        """초안 3종 → 환각 코드 검증 Verdict. LLM 불필요, 완전히 결정적(`review.py`)."""
        return review_drafts(content)

    def publish(self, asset: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("manufacturing publish 미배선 — 다음 슬라이스")


__all__ = ["ManufacturingAdapter"]
