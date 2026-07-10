# -*- coding: utf-8 -*-
"""
adapters.research.search 특성화(golden-master) 테스트.

youtube-agent/agents/research.py의 `search`/`_clean`/`_resolve_ddg_redirect`에서 이식.
파싱은 전부 순수라 목 없이 검증하고, HTTP만 가짜 fetcher로 대체한다.
"""
from __future__ import annotations

import dataclasses

import pytest

from adapters.research._http import HttpResponse
from adapters.research.search import (
    DDG_HTML,
    SearchHit,
    build_search_url,
    clean_html,
    parse_results,
    resolve_ddg_redirect,
    search,
)

# 결과 3건짜리 DDG HTML 픽스처(1번은 리다이렉트 래핑 + HTML 엔티티).
DDG_FIXTURE = """
<div><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.com%2F1">A &amp; B</a>
<a class="result__snippet">snip <b>one</b></a></div>
<div><a class="result__a" href="https://b.com/2">Title 2</a>
<a class="result__snippet">snip two</a></div>
<div><a class="result__a" href="https://c.com/3">Title 3</a>
<a class="result__snippet">snip three</a></div>
"""


class _FakeFetcher:
    """주입용 가짜 HTTP fetcher — 받은 URL을 기록하고 canned 응답을 돌려준다."""

    def __init__(self, text: str = "", content_type: str = "text/html") -> None:
        self._resp = HttpResponse(text=text, content_type=content_type)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, **kwargs) -> HttpResponse:
        self.calls.append((url, kwargs))
        return self._resp


# ── 순수: clean_html ────────────────────────────────────────────────


def test_clean_html_strips_tags_and_unescapes():
    assert clean_html("<b>Hello</b> &amp; bye") == "Hello & bye"


def test_clean_html_trims_whitespace():
    assert clean_html("  <a href='x'>T</a>  ") == "T"


def test_clean_html_empty_input():
    assert clean_html("") == ""


# ── 순수: resolve_ddg_redirect ──────────────────────────────────────


def test_resolve_ddg_redirect_unwraps_uddg():
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fq%3D1"
    assert resolve_ddg_redirect(href) == "https://example.com/a?q=1"


def test_resolve_ddg_redirect_passes_through_direct_url():
    assert resolve_ddg_redirect("https://example.com/plain") == "https://example.com/plain"


def test_resolve_ddg_redirect_adds_scheme_to_protocol_relative():
    assert resolve_ddg_redirect("//example.com/x") == "https://example.com/x"


def test_resolve_ddg_redirect_without_uddg_param_keeps_href():
    # /l/ 경로지만 uddg가 없으면 스킴만 붙인 원 href를 유지한다.
    assert resolve_ddg_redirect("/l/?nope=1") == "/l/?nope=1"


def test_resolve_ddg_redirect_decodes_percent_encoded_hangul():
    href = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fk.kr%2F%ED%95%9C%EA%B8%80"
    assert resolve_ddg_redirect(href) == "https://k.kr/한글"


# ── 순수: build_search_url ──────────────────────────────────────────


def test_build_search_url_quotes_query():
    assert build_search_url("ai shorts trends") == DDG_HTML.format(q="ai+shorts+trends")


def test_build_search_url_encodes_hangul():
    assert "%ED%95%9C" in build_search_url("한글")


# ── 순수: parse_results ─────────────────────────────────────────────


def test_parse_results_extracts_title_url_snippet():
    hits = parse_results(DDG_FIXTURE, top_k=5)
    assert hits[0] == SearchHit(title="A & B", url="https://a.com/1", snippet="snip one")
    assert [h.url for h in hits] == ["https://a.com/1", "https://b.com/2", "https://c.com/3"]


def test_parse_results_honors_top_k():
    assert len(parse_results(DDG_FIXTURE, top_k=2)) == 2


def test_parse_results_top_k_zero_still_returns_one_hit():
    """원본 기벽 고정: 개수 컷이 append **후**라 top_k=0에도 1건이 나온다."""
    assert len(parse_results(DDG_FIXTURE, top_k=0)) == 1


def test_parse_results_no_match_returns_empty():
    assert parse_results("<html>no results</html>", top_k=5) == []


def test_search_hit_is_frozen():
    hit = SearchHit("t", "u", "s")
    with pytest.raises(dataclasses.FrozenInstanceError):
        hit.title = "x"  # type: ignore[misc]


# ── IO 주입: search ─────────────────────────────────────────────────


def test_search_uses_injected_fetcher_and_parses():
    fetcher = _FakeFetcher(text=DDG_FIXTURE)
    hits = search("ai shorts", top_k=2, fetch=fetcher)

    assert len(hits) == 2
    assert hits[1].title == "Title 2"


def test_search_requests_ddg_endpoint_with_timeout():
    fetcher = _FakeFetcher(text=DDG_FIXTURE)
    search("ai shorts", fetch=fetcher, timeout=7)

    url, kwargs = fetcher.calls[0]
    assert url == DDG_HTML.format(q="ai+shorts")
    assert kwargs == {"timeout": 7}


def test_search_propagates_fetch_failure():
    """원본 `search`는 요청 실패를 방어하지 않는다 — 전파 동작을 고정한다."""

    def boom(url: str, **kwargs) -> HttpResponse:
        raise TimeoutError("network down")

    with pytest.raises(TimeoutError, match="network down"):
        search("q", fetch=boom)
