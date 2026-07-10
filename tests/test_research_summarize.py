# -*- coding: utf-8 -*-
"""
adapters.research.summarize 특성화 테스트.

LLM(Gemini)이 끼므로 원본 parity 불가 → 순수 로직(예산 배분·프롬프트 조립) 특성화 +
가짜 게이트웨이 주입(mock 패턴, seo·story_writer와 동일).
"""
from __future__ import annotations

from adapters.research.summarize import (
    MAX_TOTAL_CHARS,
    NO_SOURCES,
    SYSTEM_PROMPT,
    UNAVAILABLE,
    build_prompt,
    build_sources_block,
    summarize,
)
from adapters.research.search import SearchHit

HITS = [SearchHit(f"T{i}", f"https://e{i}.com", f"s{i}") for i in range(1, 6)]


class _FakeGateway:
    """주입용 가짜 LLM 게이트웨이 — 호출 인자를 기록하고 canned 텍스트를 돌려준다."""

    def __init__(self, *, text: str = "요약", available: bool = True) -> None:
        self._text = text
        self.available = available
        self.calls: list[dict] = []

    def complete(self, system: str, prompt: str, **kwargs) -> str:
        self.calls.append({"system": system, "prompt": prompt, **kwargs})
        return self._text


# ── 순수: build_sources_block ───────────────────────────────────────


def test_build_sources_block_formats_each_source():
    blocks = build_sources_block(HITS[:1], ["본문"])
    assert blocks == ["[출처 1] T1\nURL: https://e1.com\n발췌:\n본문\n"]


def test_build_sources_block_skips_empty_bodies():
    blocks = build_sources_block(HITS[:3], ["a", "", "c"])
    assert len(blocks) == 2


def test_build_sources_block_keeps_original_index_leaving_gaps():
    """인용 무결성: 2번 소스가 페치 실패해도 3번은 여전히 `[출처 3]`이다(번호 재부여 없음)."""
    blocks = build_sources_block(HITS[:3], ["a", "", "c"])
    assert blocks[0].startswith("[출처 1]")
    assert blocks[1].startswith("[출처 3]")


def test_build_sources_block_truncates_body_to_max_body():
    blocks = build_sources_block(HITS[:1], ["x" * 9000], max_body=100)
    assert "x" * 100 + "\n" in blocks[0]
    assert "x" * 101 not in blocks[0]


def test_build_sources_block_stops_when_total_budget_exhausted():
    # 소스당 6000자 × 5건 = 30000자 > 24000자 총합 한도 → 뒤쪽 소스는 버려진다.
    blocks = build_sources_block(HITS, ["x" * 6000] * 5)
    assert len(blocks) == 4
    assert sum(len(b) for b in blocks) < MAX_TOTAL_CHARS + 1000


def test_build_sources_block_all_empty_returns_nothing():
    assert build_sources_block(HITS, [""] * 5) == []


# ── 순수: build_prompt ──────────────────────────────────────────────


def test_build_prompt_joins_query_and_sources():
    prompt = build_prompt("질의", ["A", "B"])
    assert prompt == "# QUERY\n질의\n\n# SOURCES\nA\n---\nB"


# ── 게이트웨이 주입: summarize ──────────────────────────────────────


def test_summarize_returns_gateway_text():
    gw = _FakeGateway(text="  ## 결론  ")
    assert summarize("q", HITS[:1], ["본문"], gateway=gw) == "## 결론"


def test_summarize_calls_gateway_with_prose_settings():
    """요약은 산문이다 — json_mode=False, temperature=0.3, system=SYSTEM_PROMPT."""
    gw = _FakeGateway()
    summarize("q", HITS[:1], ["본문"], model="m-1", gateway=gw)

    call = gw.calls[0]
    assert call["system"] == SYSTEM_PROMPT
    assert call["model"] == "m-1"
    assert call["temperature"] == 0.3
    assert call["json_mode"] is False
    assert call["prompt"].startswith("# QUERY\nq\n\n# SOURCES\n[출처 1] T1")


def test_summarize_without_available_gateway_returns_reason():
    gw = _FakeGateway(available=False)
    assert summarize("q", HITS[:1], ["본문"], gateway=gw) == UNAVAILABLE
    assert gw.calls == []


def test_summarize_with_no_fetchable_bodies_returns_reason():
    gw = _FakeGateway()
    assert summarize("q", HITS, [""] * 5, gateway=gw) == NO_SOURCES
    assert gw.calls == []


def test_system_prompt_forces_citation_and_forbids_speculation():
    assert "[출처 N]" in SYSTEM_PROMPT
    assert "추측 금지" in SYSTEM_PROMPT
    assert "출처 부족" in SYSTEM_PROMPT


# ── auto 라우팅 옵트인 (Phase 2) ────────────────────────────────────────────


class _RecordingBase:
    """CostRouter가 감쌀 밑단 게이트웨이 — 실제로 어떤 모델명을 받았는지 기록한다."""

    available = True

    def __init__(self) -> None:
        self.models: list[str] = []

    def complete(self, system: str, prompt: str, *, model: str, **kwargs) -> str:
        self.models.append(model)
        return "요약"


def test_summarize_with_auto_model_routes_within_gemini():
    """호출부가 model="auto"를 넘기면(옵트인) 복잡도에 따라 Gemini 안에서 티어가 갈린다.

    라우팅은 조립점의 프로바이더별 라우트를 따르므로, Gemini 게이트웨이에 Claude 모델명이
    넘어가지 않는다(그 잠복 버그의 회귀 방지).
    """
    from core.gateway import GEMINI_ROUTE
    from core.routing import AUTO, CostRouter

    base = _RecordingBase()
    summarize("이 문단을 요약해줘", HITS[:1], ["본문"],
              model=AUTO, gateway=CostRouter(base, route=GEMINI_ROUTE))

    assert base.models == ["gemini-2.5-flash-lite"]     # 저난도 → 최저가 티어


def test_summarize_without_auto_keeps_explicit_model():
    """기본은 옵트인 아님 — 명시 모델이 그대로 간다(동작 불변)."""
    from core.gateway import GEMINI_ROUTE
    from core.routing import CostRouter

    base = _RecordingBase()
    summarize("q", HITS[:1], ["본문"], model="gemini-2.5-flash",
              gateway=CostRouter(base, route=GEMINI_ROUTE))

    assert base.models == ["gemini-2.5-flash"]
