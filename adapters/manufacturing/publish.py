# -*- coding: utf-8 -*-
"""
adapters.manufacturing.publish — 승인된 조정안 확정 (produce/review/**publish**의 publish).

research publish와 같은 두 층 분리: 순수 렌더(`render_confirmation`) + I/O(`FileConfirmationSink`
백엔드 주입). **원본 없음 → 신규 구축**(research publish·consensus와 같은 표기).

authorize가 독립 스냅샷을 공통 gate와 사람 승인 정책으로 검토한 뒤 일회성 허가를 발급한다.
publish는 허가가 발급된 인스턴스·내용·사용 여부를 확인한 뒤에만 sink를 호출한다.
사실 블록(render_impact_facts)도 함께 저장해 승인 근거를 재검토할 수 있다.

**덮어쓰기 안전**: 발행은 되돌리기 어려운 외부 행위다. `FileConfirmationSink`는 기본적으로
기존 파일을 덮어쓰지 않고 `-2`, `-3` … 접미사를 붙인다(research `FileDigestSink`와 같은 규약).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from secrets import token_urlsafe
from typing import Any, Callable, Protocol

from adapters.manufacturing.draft import render_impact_facts
from adapters.manufacturing.review import review as review_drafts
from core.approval import ApprovalOutcome, ApprovalPrompt, resolve
from core.pipeline import GateDecision, gate
from core.trust.verify import Verdict

log = logging.getLogger("mint.adapters.manufacturing.publish")

_UNSAFE = re.compile(r"[^\w\s-]", re.UNICODE)
_SPACES = re.compile(r"[\s_]+")


def _now() -> str:
    """기본 시계 — ISO 초 단위. 결정적 테스트는 clock을 주입한다."""
    return datetime.now().isoformat(timespec="seconds")


def _slug(text: str) -> str:
    """파일명 조각으로 안전화 (순수) — 한글은 보존하고 금지 문자만 뗀다(research `slugify`와 동일 발상)."""
    s = unicodedata.normalize("NFC", text).strip().lower()
    s = _UNSAFE.sub("", s)
    return _SPACES.sub("-", s).strip("-") or "shipment"


def build_meta(content: dict[str, Any], *, generated_at: str) -> dict[str, Any]:
    """`ManufacturingAdapter.produce()` 결과 → 발행 메타 (순수).

    전부 `impact`에서만 읽는다 — `content["trigger"]`는 안 본다. 트리거 타입이 둘로 늘면서
    (`ShipmentChangeTrigger`·`MaterialDelayTrigger`) `trigger.new_ship_date`가 항상 있다는
    가정이 깨졌다 — `CascadeImpact`는 어느 트리거든 항상 이 필드들을 갖는다(공통 계약).
    """
    impact = content["impact"]
    s = impact.shipment
    return {
        "generated_at": generated_at,
        "customer": s.customer,
        "product": s.product,
        "new_ship_date": impact.new_ship_date,
        "feasible": impact.feasible,
        "shortfall_count": len(impact.shortfalls),
        "slug": f"{impact.new_ship_date}-{_slug(s.customer)}-{_slug(s.product)}",
    }


def render_confirmation(content: dict[str, Any], *, generated_at: str = "") -> str:
    """produce() 결과 → 확정 문서 (순수·결정적). 사실 블록 + 초안 3종을 그대로 남긴다."""
    impact = content["impact"]
    meta = build_meta(content, generated_at=generated_at)

    lines = [f"# 출하 조정 확정 — {meta['customer']} / {meta['product']}", ""]
    if generated_at:
        status = "실행 가능" if meta["feasible"] else f"부족 자재 {meta['shortfall_count']}건"
        lines += [f"> 확정: {generated_at} · {status}", ""]
    lines += [render_impact_facts(impact), "", "---", "",
              "## 일정 조정안(내부)", "", str(content.get("adjustment_plan", "")), "",
              "## 협력업체 입고 조정 요청", "", str(content.get("supplier_request", "")), "",
              "## 고객사 회신", "", str(content.get("customer_reply", ""))]
    return "\n".join(lines) + "\n"


class ConfirmationSink(Protocol):
    """발행 I/O 경계 — 실 구현(파일·…)과 테스트 fake가 만족하는 계약."""

    def deliver(self, doc: str, meta: dict[str, Any]) -> str:
        """문서를 전달하고 위치 식별자(경로 등)를 돌려준다."""
        ...


class FileConfirmationSink:
    """마크다운 파일로 발행. 경로 주입(테스트는 tmp 디렉터리)."""

    def __init__(self, directory: str | Path, *, overwrite: bool = False) -> None:
        self._dir = Path(directory)
        self._overwrite = overwrite

    def _resolve_path(self, meta: dict[str, Any]) -> Path:
        path = self._dir / f"{meta['slug']}.md"
        if self._overwrite:
            return path
        n = 2
        while path.exists():
            path = self._dir / f"{meta['slug']}-{n}.md"
            n += 1
        return path

    def deliver(self, doc: str, meta: dict[str, Any]) -> str:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._resolve_path(meta)
        path.write_text(doc, encoding="utf-8")
        log.info("조정안 확정 발행: %s", path)
        return str(path)


class ManufacturingPublisher:
    """검토한 내용에 대한 일회성 허가를 확인한 뒤 sink에 전달한다.

    허가는 이 publisher 인스턴스에서만 유효하다. 임의 Python 코드나 sink 직접 호출을
    격리하는 보안 샌드박스가 아니라, 제조 발행 API의 실수·승인 우회를 막는 경계다.
    """

    def __init__(self, sink: ConfirmationSink, *, clock: Callable[[], str] = _now) -> None:
        self._sink = sink
        self._clock = clock
        self._permits: dict[str, str] = {}

    def review(self, content: dict[str, Any], context: Any = None) -> Verdict:
        """공통 gate가 호출하는 도메인 검사. 외부 판정을 승인 증거로 신뢰하지 않는다."""
        return review_drafts(content)

    def authorize(self, content: dict[str, Any], *, ask: ApprovalPrompt) -> PublicationAuthorization:
        """독립 스냅샷을 검토하고 review만 사람에게 묻는다. reject에는 허가를 발급하지 않는다."""
        snapshot = _publication_snapshot(content)
        fingerprint = _fingerprint(snapshot)
        decision = gate(self, snapshot)
        outcome = resolve(decision, ask=ask)
        permit = None
        if outcome.publish is True:
            token = token_urlsafe(32)
            self._permits[token] = fingerprint
            permit = PublicationPermit(token)
        return PublicationAuthorization(decision, outcome, permit)

    def publish(self, content: dict[str, Any], *, permit: PublicationPermit | None = None) -> dict[str, Any]:
        """허가 없는 호출·다른 내용·재사용을 거절한다. sink 실패 시에도 허가는 소비된다."""
        expected = self._permits.pop(permit.token, None) if isinstance(permit, PublicationPermit) else None
        if expected is None:
            raise PermissionError('유효한 제조 발행 승인이 필요합니다. authorize를 먼저 호출하세요.')
        snapshot = _publication_snapshot(content)
        if _fingerprint(snapshot) != expected:
            raise PermissionError('승인 후 내용이 변경되었습니다. 다시 검토·승인하세요.')
        generated_at = self._clock()
        meta = build_meta(snapshot, generated_at=generated_at)
        doc = render_confirmation(snapshot, generated_at=generated_at)
        location = self._sink.deliver(doc, meta)
        return {
            "location": location,
            "generated_at": generated_at,
            "feasible": meta["feasible"],
            "shortfall_count": meta["shortfall_count"],
        }


@dataclass(frozen=True)
class PublicationPermit:
    """publisher가 보관한 승인과 대조할 일회성 식별자. 객체를 직접 만들어도 허가되지 않는다."""

    token: str


@dataclass(frozen=True)
class PublicationAuthorization:
    decision: GateDecision
    outcome: ApprovalOutcome
    permit: PublicationPermit | None


def _publication_snapshot(content: dict[str, Any]) -> dict[str, Any]:
    """렌더러가 실제 사용하는 모든 내용을 복사. frozen dataclass의 내부 list도 격리한다."""
    return deepcopy({key: content.get(key) for key in (
        'impact', 'adjustment_plan', 'supplier_request', 'customer_reply')})


def _fingerprint(content: dict[str, Any]) -> str:
    payload = {**content, 'impact': asdict(content['impact'])}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ConfirmationSink", "FileConfirmationSink", "ManufacturingPublisher",
    "build_meta", "render_confirmation",
    "PublicationPermit", "PublicationAuthorization",
]

