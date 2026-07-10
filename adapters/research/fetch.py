# -*- coding: utf-8 -*-
"""
adapters.research.fetch — 문서 본문 페치 + 태그 제거(stdlib만, BS4 미사용).

원본: `youtube-agent/agents/research.py`의 `fetch_text`.

순수/IO 분리:
  - 순수: `strip_html` — 스크립트/스타일/레이아웃 블록 제거 → 잔여 태그 제거 → 언이스케이프 →
    공백 정규화 → `max_chars` 컷. 정규식·순서 모두 **원본과 동일**(parity 대조 대상).
  - IO  : `fetch_text`가 `HttpFetcher`를 받는다(기본 `urllib_fetch`).

원본 동작 보존 메모:
  - Content-Type이 `text/html`·`text/plain` 둘 다 아니면 **빈 문자열**(요약 입력에서 제외).
  - 페치 실패는 예외를 삼키고 경고 로그 + 빈 문자열 — 소스 1건 실패가 리서치 전체를 죽이지 않는다.
  - 응답은 800KB까지만 읽는다(`MAX_BYTES`).
"""
from __future__ import annotations

import html
import logging
import re

from adapters.research._http import TIMEOUT, HttpFetcher, urllib_fetch

log = logging.getLogger("mint.adapters.research.fetch")

MAX_BODY_CHARS = 6000   # 페이지당 요약 입력 한도
MAX_BYTES = 800_000     # 응답 읽기 상한

_TAG_STRIP = re.compile(
    r"<(script|style|noscript|svg|header|footer|nav|aside)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_ANY = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

_TEXT_TYPES = ("text/html", "text/plain")


def strip_html(text: str, max_chars: int = MAX_BODY_CHARS) -> str:
    """HTML → 평문 (순수). (원본 `fetch_text`의 정제 구간)"""
    text = _TAG_STRIP.sub(" ", text)
    text = _TAG_ANY.sub(" ", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text).strip()
    return text[:max_chars]


def is_text_response(content_type: str) -> bool:
    """요약 입력으로 쓸 수 있는 Content-Type인가. (원본의 타입 게이트)"""
    return any(t in content_type for t in _TEXT_TYPES)


def fetch_text(url: str, max_chars: int = MAX_BODY_CHARS, *,
               fetch: HttpFetcher = urllib_fetch, timeout: int = TIMEOUT) -> str:
    """URL → 본문 평문. 실패·비텍스트 응답이면 빈 문자열(소스 단위 실패 격리)."""
    try:
        resp = fetch(url, timeout=timeout, max_bytes=MAX_BYTES)
        if not is_text_response(resp.content_type):
            return ""
        raw = resp.text
    except Exception as exc:  # noqa: BLE001 — 소스 1건 실패는 격리한다(원본 동작)
        log.warning("fetch failed %s: %s", url, exc)
        return ""
    return strip_html(raw, max_chars)
