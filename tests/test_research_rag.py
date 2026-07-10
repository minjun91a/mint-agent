# -*- coding: utf-8 -*-
"""
adapters.research.rag 특성화 테스트.

임베더는 결정적 가짜(키워드 카운트)를 주입한다. 핵심 불변식은 **인용 번호 보존** —
선택기는 본문과 같은 길이·같은 순서의 목록을 돌려주고, 관련 조각이 없는 문서는 빈 문자열이 된다.
"""
from __future__ import annotations

from adapters.research.rag import ELLIPSIS, RagSelector, assemble
from adapters.research.search import SearchHit
from adapters.research.summarize import build_sources_block, summarize
from core.retrieval import Chunk


class _WordEmbedder:
    """고정 어휘 등장 횟수 = 벡터. 키워드가 겹칠수록 유사도가 높다(결정적)."""

    VOCAB = ("고양이", "강아지", "우주")

    def __init__(self, *, available: bool = True, raises: Exception | None = None) -> None:
        self.available = available
        self._raises = raises
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        if self._raises:
            raise self._raises
        self.calls.append(list(texts))
        return [[float(t.count(w)) for w in self.VOCAB] for t in texts]


# ── 순수: assemble ──────────────────────────────────────────────────


def test_assemble_restores_document_and_chunk_order():
    """검색 점수 순이 아니라 원문 순서로 읽혀야 한다."""
    chunks = [Chunk("뒤", 1, 1), Chunk("앞", 1, 0)]
    assert assemble(chunks, 1) == ["앞" + ELLIPSIS + "뒤"]


def test_assemble_returns_one_entry_per_document():
    assert assemble([Chunk("x", 2, 0)], 3) == ["", "x", ""]


def test_assemble_empty_selection_gives_all_blank():
    assert assemble([], 2) == ["", ""]


def test_assemble_truncates_each_document_to_max_chars():
    chunks = [Chunk("a" * 50, 1, 0)]
    assert len(assemble(chunks, 1, max_chars=10)[0]) == 10


# ── RagSelector ─────────────────────────────────────────────────────


DOCS = [
    "고양이 고양이 고양이는 잠을 잔다",
    "강아지는 뛴다",
    "우주는 넓다",
]


def test_selector_picks_relevant_document_only():
    sel = RagSelector(_WordEmbedder(), chunk_size=100, overlap=0, top_k=1)
    out = sel.select("고양이", DOCS)

    assert out[0].startswith("고양이")
    assert out[1] == "" and out[2] == ""


def test_selector_returns_same_length_as_input():
    """인용 번호 불변식: i번째 자리에는 i번째 문서의 발췌만."""
    sel = RagSelector(_WordEmbedder(), chunk_size=100, overlap=0, top_k=2)
    assert len(sel.select("고양이", DOCS)) == len(DOCS)


def test_selector_preserves_source_numbering_through_summarize():
    """RAG로 2번 문서가 탈락해도 3번 출처는 여전히 [출처 3]이다."""
    sel = RagSelector(_WordEmbedder(), chunk_size=100, overlap=0, top_k=2)
    bodies = sel.select("고양이 우주", DOCS)

    hits = [SearchHit(f"T{i}", f"https://e{i}.com", "s") for i in range(1, 4)]
    blocks = build_sources_block(hits, bodies)

    assert blocks[0].startswith("[출처 1]")
    assert blocks[1].startswith("[출처 3]")   # 강아지 문서(2번)는 탈락, 번호는 소비됨


def test_selector_without_available_embedder_returns_original():
    """임베더 미가용 → RAG 없이 기존 동작. 있는 능력을 잃지 않는다."""
    embedder = _WordEmbedder(available=False)
    assert RagSelector(embedder).select("고양이", DOCS) == DOCS
    assert embedder.calls == []


def test_selector_on_embedding_failure_returns_original():
    """RAG 실패가 리서치 전체를 죽이지 않는다."""
    sel = RagSelector(_WordEmbedder(raises=RuntimeError("boom")))
    assert sel.select("고양이", DOCS) == DOCS


def test_selector_with_all_empty_bodies_returns_original():
    sel = RagSelector(_WordEmbedder())
    assert sel.select("q", ["", "  "]) == ["", "  "]


def test_selector_with_no_matching_chunks_returns_original():
    """질의 어휘가 하나도 안 겹치면 전부 0점 → 조각은 나오지만 발췌는 원문 순서대로."""
    sel = RagSelector(_WordEmbedder(), chunk_size=100, overlap=0, top_k=3)
    out = sel.select("전혀 다른 말", DOCS)
    assert len(out) == 3


def test_selector_truncates_to_max_chars():
    long_doc = ["고양이 " * 500]
    sel = RagSelector(_WordEmbedder(), chunk_size=100, overlap=0, top_k=50, max_chars=120)
    assert len(sel.select("고양이", long_doc)[0]) == 120


def test_selector_joins_chunks_with_ellipsis():
    """비연속 발췌임을 요약 모델에게 알린다."""
    doc = ["고양이" + "x" * 100 + "고양이"]
    sel = RagSelector(_WordEmbedder(), chunk_size=50, overlap=0, top_k=3)
    assert ELLIPSIS in sel.select("고양이", doc)[0]


# ── summarize 통합 (selector 옵트인) ────────────────────────────────


class _FakeGateway:
    available = True

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, system: str, prompt: str, **kwargs) -> str:
        self.prompts.append(prompt)
        return "요약"


HITS = [SearchHit(f"T{i}", f"https://e{i}.com", "s") for i in range(1, 4)]


def test_summarize_without_selector_keeps_original_bodies():
    """RAG는 옵트인 — 미주입 시 기존 동작(앞에서부터 문자 예산)."""
    gw = _FakeGateway()
    summarize("고양이", HITS, DOCS, gateway=gw)
    assert "강아지는 뛴다" in gw.prompts[0]


def test_summarize_with_selector_narrows_prompt_to_relevant_excerpts():
    gw = _FakeGateway()
    sel = RagSelector(_WordEmbedder(), chunk_size=100, overlap=0, top_k=1)
    summarize("고양이", HITS, DOCS, gateway=gw, selector=sel)

    prompt = gw.prompts[0]
    assert "고양이" in prompt
    assert "강아지는 뛴다" not in prompt   # 관련 없는 문서는 프롬프트에서 빠졌다


def test_summarize_selector_runs_only_when_gateway_available():
    """게이트웨이가 미가용이면 임베딩 비용도 쓰지 않는다."""

    class _Unavailable:
        available = False

        def complete(self, *a, **k):  # pragma: no cover
            raise AssertionError("호출되면 안 된다")

    embedder = _WordEmbedder()
    summarize("고양이", HITS, DOCS, gateway=_Unavailable(),
              selector=RagSelector(embedder))
    assert embedder.calls == []
