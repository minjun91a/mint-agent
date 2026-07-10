# -*- coding: utf-8 -*-
"""
adapters.research.search — 검색 결과 수집(DuckDuckGo HTML 엔드포인트).

원본: `youtube-agent/agents/research.py`의 `search`/`_clean`/`_resolve_ddg_redirect`.

순수/IO 분리:
  - 순수: `build_search_url` · `clean_html` · `resolve_ddg_redirect` · `parse_results`
    → 정규식·언이스케이프·리다이렉트 해석은 **원본과 동일**(parity 대조 대상).
  - IO  : `search`가 `HttpFetcher`를 받는다(기본 `urllib_fetch`).

원본 동작 보존 메모:
  - 결과 개수 컷은 `append` **후** `len(hits) >= top_k` 체크다. 따라서 `top_k=0`이어도 1건이
    반환된다(원본 그대로 — 특성화 테스트로 고정).
  - 검색 요청 실패는 잡지 않고 전파한다(원본 `search`는 방어하지 않는다).
"""
from __future__ import annotations

import html
import re
import urllib.parse
from dataclasses import dataclass

from adapters.research._http import TIMEOUT, HttpFetcher, urllib_fetch

DDG_HTML = "https://duckduckgo.com/html/?q={q}"

_DDG_RESULT = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)


@dataclass(frozen=True)
class SearchHit:
    """검색 결과 1건. (원본은 가변 dataclass — 변형하는 곳이 없어 frozen으로 굳혔다.)"""

    title: str
    url: str
    snippet: str


def clean_html(text: str) -> str:
    """태그 제거 + HTML 엔티티 언이스케이프 + 트림. (원본 `_clean`)"""
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def resolve_ddg_redirect(href: str) -> str:
    """DuckDuckGo가 `/l/?uddg=...`로 감싼 링크에서 원 URL을 뽑는다. (원본 `_resolve_ddg_redirect`)"""
    if href.startswith("//"):
        href = "https:" + href
    if "/l/?" in href or href.startswith("//duckduckgo.com/l/"):
        qs = urllib.parse.urlparse(href).query
        params = urllib.parse.parse_qs(qs)
        if "uddg" in params:
            return urllib.parse.unquote(params["uddg"][0])
    return href


def build_search_url(query: str) -> str:
    """질의 → DDG HTML 엔드포인트 URL."""
    return DDG_HTML.format(q=urllib.parse.quote_plus(query))


def parse_results(body: str, top_k: int = 5) -> list[SearchHit]:
    """DDG 결과 HTML → `SearchHit` 목록 (순수)."""
    hits: list[SearchHit] = []
    for m in _DDG_RESULT.finditer(body):
        href, title_html, snip_html = m.group(1), m.group(2), m.group(3)
        hits.append(SearchHit(
            title=clean_html(title_html),
            url=resolve_ddg_redirect(href),
            snippet=clean_html(snip_html),
        ))
        if len(hits) >= top_k:
            break
    return hits


def search(query: str, top_k: int = 5, *,
           fetch: HttpFetcher = urllib_fetch, timeout: int = TIMEOUT) -> list[SearchHit]:
    """질의 → 상위 `top_k` 검색 결과. HTTP는 주입된 `fetch`가 수행한다."""
    resp = fetch(build_search_url(query), timeout=timeout)
    return parse_results(resp.text, top_k=top_k)
