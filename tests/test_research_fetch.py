# -*- coding: utf-8 -*-
"""
adapters.research.fetch 특성화(golden-master) 테스트.

youtube-agent/agents/research.py의 `fetch_text`에서 이식. 정제 구간(`strip_html`)은 순수라
목 없이 검증하고, HTTP만 가짜 fetcher로 대체해 실패 격리·타입 게이트를 결정적으로 고정한다.
"""
from __future__ import annotations

from adapters.research._http import HttpResponse
from adapters.research.fetch import (
    MAX_BODY_CHARS,
    MAX_BYTES,
    fetch_text,
    is_text_response,
    strip_html,
)

HTML_FIXTURE = (
    "<html><head><style>a{}</style></head><body><script>var x=1;</script>"
    "<nav>메뉴</nav><p>본문   &amp; 내용</p><footer>푸터</footer></body></html>"
)


class _FakeFetcher:
    """URL → 응답 매핑. 없는 URL은 예외를 던져 실패 격리를 검증한다."""

    def __init__(self, responses: dict[str, HttpResponse]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, **kwargs) -> HttpResponse:
        self.calls.append((url, kwargs))
        if url not in self._responses:
            raise ConnectionError(f"unreachable: {url}")
        return self._responses[url]


# ── 순수: strip_html ────────────────────────────────────────────────


def test_strip_html_removes_script_style_and_layout_blocks():
    out = strip_html(HTML_FIXTURE)
    for gone in ("var x=1", "a{}", "메뉴", "푸터"):
        assert gone not in out


def test_strip_html_keeps_body_text_and_unescapes():
    assert "본문 & 내용" in strip_html(HTML_FIXTURE)


def test_strip_html_collapses_whitespace():
    assert strip_html("<p>a\n\n  b\tc</p>") == "a b c"


def test_strip_html_truncates_to_max_chars():
    assert len(strip_html("<p>" + "x" * 500 + "</p>", max_chars=10)) == 10


def test_strip_html_empty_input():
    assert strip_html("") == ""


# ── 순수: is_text_response ──────────────────────────────────────────


def test_is_text_response_accepts_html_and_plain():
    assert is_text_response("text/html; charset=utf-8")
    assert is_text_response("text/plain")


def test_is_text_response_rejects_binary_types():
    assert not is_text_response("application/pdf")
    assert not is_text_response("")


# ── IO 주입: fetch_text ─────────────────────────────────────────────


def test_fetch_text_returns_stripped_body():
    url = "https://a.com/1"
    fetcher = _FakeFetcher({url: HttpResponse(text=HTML_FIXTURE, content_type="text/html")})

    assert "본문 & 내용" in fetch_text(url, fetch=fetcher)


def test_fetch_text_passes_byte_cap_and_timeout():
    url = "https://a.com/1"
    fetcher = _FakeFetcher({url: HttpResponse(text="<p>ok</p>", content_type="text/plain")})
    fetch_text(url, fetch=fetcher, timeout=3)

    assert fetcher.calls[0] == (url, {"timeout": 3, "max_bytes": MAX_BYTES})


def test_fetch_text_skips_non_text_content_type():
    url = "https://a.com/doc.pdf"
    fetcher = _FakeFetcher({url: HttpResponse(text="%PDF-1.7", content_type="application/pdf")})

    assert fetch_text(url, fetch=fetcher) == ""


def test_fetch_text_isolates_failure_as_empty_string():
    """소스 1건의 페치 실패가 리서치 전체를 죽이지 않는다(원본 동작)."""
    fetcher = _FakeFetcher({})

    assert fetch_text("https://down.example", fetch=fetcher) == ""


def test_fetch_text_honors_custom_max_chars():
    url = "https://a.com/1"
    fetcher = _FakeFetcher({url: HttpResponse(text="<p>" + "y" * 999 + "</p>",
                                              content_type="text/html")})

    assert len(fetch_text(url, max_chars=12, fetch=fetcher)) == 12


def test_fetch_text_default_cap_is_max_body_chars():
    url = "https://a.com/1"
    fetcher = _FakeFetcher({url: HttpResponse(text="z" * (MAX_BODY_CHARS + 500),
                                              content_type="text/html")})

    assert len(fetch_text(url, fetch=fetcher)) == MAX_BODY_CHARS
