# -*- coding: utf-8 -*-
"""
adapters.manufacturing.adapter — manufacturing 어댑터 (`core.pipeline.PlatformAdapter` 구현).

**세 번째 어댑터.** produce·review·publish 3단이 모두 배선됐다.
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
from adapters.manufacturing.publish import ManufacturingPublisher
from adapters.manufacturing.review import review as review_drafts
from core.llm import LLMGateway
from core.trust.verify import Verdict


class ManufacturingAdapter:
    """manufacturing produce/review/publish 어댑터. 스냅샷은 생성 시 주입(경로 주입과 같은 규약 —
    IO는 호출부가 `load_erp_snapshot`으로 미리 해서 넘긴다, 어댑터는 스냅샷을 모른 채 재사용만)."""

    def __init__(self, snapshot: ErpSnapshot, *, model: str = _DEFAULT_MODEL,
                 gateway: LLMGateway | None = None,
                 publisher: ManufacturingPublisher | None = None) -> None:
        self._snapshot = snapshot
        self._model = model
        self._gateway = gateway
        self._publisher = publisher

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
        """승인된 조정안 → 확정 발행. publisher 미주입 시 명시적 에러.

        발행은 되돌리기 어려운 외부 행위다. 기본 sink를 몰래 만들지 않고 **명시적 배선을
        요구**한다(research publish와 같은 규약).
        """
        if self._publisher is None:
            raise NotImplementedError(
                "manufacturing publish 백엔드 미배선 — ManufacturingPublisher(sink)를 주입하라"
            )
        return self._publisher.publish(asset)


__all__ = ["ManufacturingAdapter"]
