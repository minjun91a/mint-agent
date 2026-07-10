# -*- coding: utf-8 -*-
"""
adapters.research.rag — 수집 본문에서 질의에 관련된 발췌만 고른다 (RAG 선택기).

기존 `summarize.build_sources_block`은 본문을 **앞에서부터** 6000자 자른다. 관련 대목이 문서 뒤쪽에
있으면 요약 입력에 못 들어간다. 여기서는 `core.retrieval`로 문서를 청킹·임베딩해 질의와 유사한
조각만 골라 본문을 대체한다.

**인용 무결성 불변식.** 선택기는 `bodies`와 **같은 길이의 목록**을 돌려주고, i번째 자리에는 i번째
문서의 발췌만 담는다. 즉 `[출처 N]`의 N이 절대 어긋나지 않는다(`core.retrieval.Chunk.source_id`가
1-based 문서 번호). 관련 조각이 없는 문서는 **빈 문자열**이 되어, 기존 파이프라인이 원래 하던 대로
요약 입력에서 제외하고 번호만 소비한다 — `review`의 `unused_source`(low)로 잡힌다.

**RAG 실패는 리서치를 죽이지 않는다.** 임베더가 미가용(키 없음)이거나 호출이 실패하면 경고를 남기고
**원본 본문을 그대로** 돌려준다(RAG 없이 기존 동작). 없는 능력을 흉내내지 않고, 있는 능력을 잃지도 않는다.
"""
from __future__ import annotations

import logging
from typing import Callable

from adapters.research.fetch import MAX_BODY_CHARS
from core.embedding import EmbeddingGateway
from core.retrieval import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OVERLAP,
    Chunk,
    InMemoryVectorStore,
    Retriever,
    VectorStore,
)

log = logging.getLogger("mint.adapters.research.rag")

# 같은 문서에서 고른 조각들 사이에 넣는 생략 표시(발췌가 연속이 아님을 요약 모델에게 알린다).
ELLIPSIS = "\n…\n"

DEFAULT_TOP_K = 8


def assemble(chunks: list[Chunk], doc_count: int, *, max_chars: int = MAX_BODY_CHARS) -> list[str]:
    """선택된 조각 → 문서별 발췌 목록 (순수).

    문서 순서·문서 내 조각 순서를 복원해 이어붙인다(검색 점수 순이 아니라 **원문 순서**로 읽혀야 한다).
    반환 길이는 항상 `doc_count` — 조각이 없는 문서는 빈 문자열.
    """
    by_doc: dict[int, list[Chunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.source_id, []).append(c)

    out: list[str] = []
    for source_id in range(1, doc_count + 1):
        picked = sorted(by_doc.get(source_id, []), key=lambda c: c.index)
        out.append(ELLIPSIS.join(c.text for c in picked)[:max_chars])
    return out


class RagSelector:
    """질의 관련 발췌 선택기. 임베더·저장소 팩토리 주입 → 엔진·프로바이더·DB를 모른다.

    저장소는 **질의마다 새로 만든다**(`store_factory`). 리서치 1회의 수집 본문만 담으면 되기 때문이고,
    pgvector처럼 영속 저장소를 쓰려면 팩토리에서 그걸 돌려주면 된다.
    """

    def __init__(self, embedder: EmbeddingGateway, *,
                 store_factory: Callable[[], VectorStore] = InMemoryVectorStore,
                 model: str | None = None,
                 chunk_size: int = DEFAULT_CHUNK_SIZE,
                 overlap: int = DEFAULT_OVERLAP,
                 top_k: int = DEFAULT_TOP_K,
                 max_chars: int = MAX_BODY_CHARS) -> None:
        self._embedder = embedder
        self._store_factory = store_factory
        self._model = model
        self._chunk_size = chunk_size
        self._overlap = overlap
        self._top_k = top_k
        self._max_chars = max_chars

    def select(self, query: str, bodies: list[str]) -> list[str]:
        """질의 + 본문 목록 → 문서별 관련 발췌 목록(같은 길이·같은 순서)."""
        if not self._embedder.available:
            log.warning("임베더 미가용 — RAG 없이 원본 본문 사용")
            return bodies
        if not any(b.strip() for b in bodies):
            return bodies

        try:
            retriever = Retriever(self._embedder, self._store_factory(), model=self._model,
                                  chunk_size=self._chunk_size, overlap=self._overlap)
            if retriever.index(bodies) == 0:
                return bodies
            hits = retriever.retrieve(query, self._top_k)
        except Exception as exc:  # noqa: BLE001 — RAG 실패는 리서치 전체를 죽이지 않는다
            log.warning("RAG 선택 실패 — 원본 본문 사용: %s", exc)
            return bodies

        if not hits:
            return bodies
        return assemble([c for c, _score in hits], len(bodies), max_chars=self._max_chars)
