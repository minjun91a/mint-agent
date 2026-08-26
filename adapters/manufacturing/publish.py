# -*- coding: utf-8 -*-
"""
adapters.manufacturing.publish — 승인된 조정안 확정 (produce/review/**publish**의 publish).

research publish와 같은 두 층 분리: 순수 렌더(`render_confirmation`) + I/O(`FileConfirmationSink`
백엔드 주입). **원본 없음 → 신규 구축**(research publish·consensus와 같은 표기).

엔진 게이트를 통과해야만 이 함수가 불리므로(`core.pipeline.gate`), 여기 적히는 초안은 이미
환각 코드 검증(`review.py`)을 통과한 것이다. 그래도 사실 블록(`render_impact_facts`)을 문서에
그대로 남긴다 — 나중에 "그때 뭘 근거로 승인했는지" 재검토 가능하게.

**덮어쓰기 안전**: 발행은 되돌리기 어려운 외부 행위다. `FileConfirmationSink`는 기본적으로
기존 파일을 덮어쓰지 않고 `-2`, `-3` … 접미사를 붙인다(research `FileDigestSink`와 같은 규약).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from adapters.manufacturing.draft import render_impact_facts

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
    """`ManufacturingAdapter.produce()` 결과 → 발행 메타 (순수)."""
    impact = content["impact"]
    trigger = content["trigger"]
    return {
        "generated_at": generated_at,
        "customer": trigger.customer,
        "product": trigger.product,
        "new_ship_date": trigger.new_ship_date,
        "feasible": impact.feasible,
        "shortfall_count": len(impact.shortfalls),
        "slug": f"{trigger.new_ship_date}-{_slug(trigger.customer)}-{_slug(trigger.product)}",
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
    """publish 단계 — 렌더(순수) + sink 위임(I/O). sink 주입으로 테스트 가능."""

    def __init__(self, sink: ConfirmationSink, *, clock: Callable[[], str] = _now) -> None:
        self._sink = sink
        self._clock = clock

    def publish(self, content: dict[str, Any]) -> dict[str, Any]:
        """produce() 결과 → 발행. `{location, generated_at, feasible, shortfall_count}` 반환."""
        generated_at = self._clock()
        meta = build_meta(content, generated_at=generated_at)
        doc = render_confirmation(content, generated_at=generated_at)
        location = self._sink.deliver(doc, meta)
        return {
            "location": location,
            "generated_at": generated_at,
            "feasible": meta["feasible"],
            "shortfall_count": meta["shortfall_count"],
        }


__all__ = [
    "ConfirmationSink", "FileConfirmationSink", "ManufacturingPublisher",
    "build_meta", "render_confirmation",
]
