# -*- coding: utf-8 -*-
"""
adapters.research.adapter 특성화 테스트 — **두 번째 어댑터**.

핵심 명제(ADR 0003): 엔진(`core/`)을 한 줄도 바꾸지 않고 도메인이 전혀 다른 어댑터가 붙는다.
그 증거를 여기서 고정한다 — `ResearchAdapter`가 `core.pipeline.PlatformAdapter` 포트를 만족하고,
엔진은 구체 타입을 모른 채 다룬다.

주입 지점 둘(HTTP·LLM)을 가짜로 채워 네트워크·모델 없이 produce 전 구간을 결정적으로 돌린다.
"""
from __future__ import annotations

import pytest

from adapters.research._http import HttpResponse
from adapters.research.adapter import NO_HITS, ResearchAdapter
from adapters.research.search import build_search_url
from core.pipeline import GateDecision, PlatformAdapter, gate
from core.trust.verify import Verdict

DDG_FIXTURE = """
<div><a class="result__a" href="https://a.com/1">Title 1</a>
<a class="result__snippet">snip one</a></div>
<div><a class="result__a" href="https://b.com/2">Title 2</a>
<a class="result__snippet">snip two</a></div>
"""

QUERY = "ai shorts trends"


class _FakeFetcher:
    """검색 URL엔 DDG HTML을, 문서 URL엔 본문을 돌려준다. 미등록 URL은 실패."""

    def __init__(self, pages: dict[str, HttpResponse]) -> None:
        self._pages = pages
        self.calls: list[str] = []

    def __call__(self, url: str, **kwargs) -> HttpResponse:
        self.calls.append(url)
        if url not in self._pages:
            raise ConnectionError(f"unreachable: {url}")
        return self._pages[url]


class _FakeGateway:
    available = True

    def __init__(self, text: str = "## 결론\n- 사실 [출처 1]") -> None:
        self._text = text
        self.prompts: list[str] = []

    def complete(self, system: str, prompt: str, **kwargs) -> str:
        self.prompts.append(prompt)
        return self._text


def _pages(**bodies: str) -> dict[str, HttpResponse]:
    pages = {build_search_url(QUERY): HttpResponse(text=DDG_FIXTURE, content_type="text/html")}
    for host, body in bodies.items():
        url = {"a": "https://a.com/1", "b": "https://b.com/2"}[host]
        pages[url] = HttpResponse(text=body, content_type="text/html")
    return pages


# ── 엔진 계약 ───────────────────────────────────────────────────────


def test_research_adapter_satisfies_platform_adapter_port():
    """엔진은 research를 `PlatformAdapter`로만 본다 — 두 번째 어댑터가 포트를 만족한다."""
    assert isinstance(ResearchAdapter(), PlatformAdapter)


def test_publish_requires_backend_injection():
    with pytest.raises(NotImplementedError, match="백엔드"):
        ResearchAdapter().publish({})


def test_engine_gate_drives_research_adapter():
    """엔진 발행 게이트가 youtube가 **아닌** 어댑터를 구동한다 — 포트 분리의 최종 증명.

    `gate`는 research를 모르고, `context=None`(근거 본문 없음)이라 룰 인용 검증만 돈다.
    """
    clean = {
        "query": "q",
        "summary": "- 사실 [출처 1]\n\n## 출처\n1. T1 — https://a.com/1",
        "hits": [{"title": "T1", "url": "https://a.com/1", "snippet": "s", "fetched_chars": 10}],
    }
    decision = gate(ResearchAdapter(review_deep=False), clean)

    assert isinstance(decision, GateDecision)
    assert decision.proceed is True
    assert decision.verdict.recommendation == "approve"


def test_engine_gate_blocks_hallucinated_citation():
    """존재하지 않는 출처를 인용하면 엔진 게이트가 발행을 막는다."""
    bad = {
        "query": "q",
        "summary": "- 사실 [출처 7]\n\n## 출처\n1. T1",
        "hits": [{"title": "T1", "url": "https://a.com/1", "snippet": "s", "fetched_chars": 10}],
    }
    decision = gate(ResearchAdapter(review_deep=False), bad)

    assert decision.proceed is False
    assert decision.verdict.recommendation == "reject"
    assert "citation_out_of_range" in [v.category for v in decision.verdict.violations]


def test_gate_passes_context_through_to_adapter():
    """엔진은 context를 해석하지 않고 어댑터에 그대로 넘긴다(youtube=seo, research=bodies)."""
    seen: list = []

    class _SpyAdapter:
        def produce(self, brief): ...
        def publish(self, asset): ...

        def review(self, content, context=None):
            seen.append(context)
            return Verdict(passed=True, score=0.0, recommendation="approve")

    gate(_SpyAdapter(), {}, {"bodies": ["b1"]})
    assert seen == [{"bodies": ["b1"]}]


# ── produce ────────────────────────────────────────────────────────


def test_produce_returns_query_summary_and_hits():
    fetcher = _FakeFetcher(_pages(a="<p>본문 A</p>", b="<p>본문 B</p>"))
    adapter = ResearchAdapter(gateway=_FakeGateway(), fetch=fetcher)

    out = adapter.produce(QUERY)

    assert out["query"] == QUERY
    assert out["summary"] == "## 결론\n- 사실 [출처 1]"
    assert [h["url"] for h in out["hits"]] == ["https://a.com/1", "https://b.com/2"]
    assert [h["title"] for h in out["hits"]] == ["Title 1", "Title 2"]
    assert [h["snippet"] for h in out["hits"]] == ["snip one", "snip two"]


def test_produce_reports_fetched_chars_per_hit():
    fetcher = _FakeFetcher(_pages(a="<p>12345</p>", b="<p>123</p>"))
    adapter = ResearchAdapter(gateway=_FakeGateway(), fetch=fetcher)

    assert [h["fetched_chars"] for h in adapter.produce(QUERY)["hits"]] == [5, 3]


def test_produce_keeps_hit_with_zero_chars_when_fetch_fails():
    """페치 실패한 소스도 hits에 남되 `fetched_chars=0` — 무엇이 요약에 안 들어갔는지 보인다."""
    pages = _pages(a="<p>본문 A</p>")  # b.com은 미등록 → ConnectionError
    adapter = ResearchAdapter(gateway=_FakeGateway(), fetch=_FakeFetcher(pages))

    hits = adapter.produce(QUERY)["hits"]
    assert [h["fetched_chars"] for h in hits] == [len("본문 A"), 0]


def test_produce_excludes_failed_source_from_llm_prompt():
    pages = _pages(a="<p>본문 A</p>")
    gw = _FakeGateway()
    ResearchAdapter(gateway=gw, fetch=_FakeFetcher(pages)).produce(QUERY)

    prompt = gw.prompts[0]
    assert "[출처 1]" in prompt
    assert "[출처 2]" not in prompt


def test_produce_without_search_hits_short_circuits():
    empty = {build_search_url(QUERY): HttpResponse(text="<html>none</html>",
                                                   content_type="text/html")}
    gw = _FakeGateway()
    out = ResearchAdapter(gateway=gw, fetch=_FakeFetcher(empty)).produce(QUERY)

    assert out == {"query": QUERY, "summary": NO_HITS, "hits": []}
    assert gw.prompts == []  # 요약 호출 안 함


def test_produce_honors_top_k():
    fetcher = _FakeFetcher(_pages(a="<p>본문 A</p>"))
    adapter = ResearchAdapter(top_k=1, gateway=_FakeGateway(), fetch=fetcher)

    assert len(adapter.produce(QUERY)["hits"]) == 1


def test_produce_fetches_search_then_each_hit():
    fetcher = _FakeFetcher(_pages(a="<p>A</p>", b="<p>B</p>"))
    ResearchAdapter(gateway=_FakeGateway(), fetch=fetcher).produce(QUERY)

    assert fetcher.calls == [build_search_url(QUERY), "https://a.com/1", "https://b.com/2"]
