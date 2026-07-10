# -*- coding: utf-8 -*-
"""
core.retrieval — 청킹·벡터 저장·유사도 검색 (엔진의 RAG 메커니즘).

**이것은 "어댑터가 강제한 엔진 변경"이 아니다.** research 어댑터는 엔진 변경 0줄로 붙었고
(`38473b5`), produce·review·publish 3단이 이미 그 상태로 돈다. 여기 있는 것은 *새 엔진 역량*이다 —
긴 문서에서 질의에 관련된 부분만 골라 모델 입력에 넣는 메커니즘. research가 첫 사용처일 뿐,
플랫폼을 모른다(youtube도 긴 원작 텍스트에서 관련 대목을 뽑는 데 쓸 수 있다).

구성(다른 엔진 모듈과 같은 결):
  - **순수**: `chunk_text` · `chunk_documents` · `cosine` · SQL 빌더 — 결정적, 목 없이 테스트.
  - **포트**: `VectorStore`(add/search) · `EmbeddingGateway`(core.embedding) — 주입 대상.
  - **구현**: `InMemoryVectorStore`(결정적, 의존 0) · `PgVectorStore`(**연결 객체를 주입**받으므로
    psycopg 의존이 이 모듈에 없다 — `GoogleUploadBackend`가 클라이언트를 주입받는 것과 같은 패턴).

`source_id`는 **1-based 문서 번호**다. research에서는 그대로 `[출처 N]`의 N이 되므로, 검색으로
발췌를 골라도 인용 번호가 어긋나지 않는다.

pgvector 실 연결은 아직 검증되지 않았다(로컬 Postgres 미배선). SQL 생성과 커서 호출은 가짜 연결로
고정하되, 실 DB 왕복은 🔴 미검증이다 — 지어내지 않는다.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from core.embedding import EmbeddingGateway

log = logging.getLogger("mint.core.retrieval")

DEFAULT_CHUNK_SIZE = 800
DEFAULT_OVERLAP = 100

# SQL 식별자(테이블명) 화이트리스트 — 문자열 보간 지점이라 반드시 검증한다.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class Chunk:
    """문서 조각. `source_id`는 1-based 문서 번호(research의 출처 번호와 같다)."""

    text: str
    source_id: int
    index: int   # 문서 내 조각 순번(0-based)


# ── 순수: 청킹 ────────────────────────────────────────────────────────────


def chunk_text(text: str, *, size: int = DEFAULT_CHUNK_SIZE,
               overlap: int = DEFAULT_OVERLAP) -> list[str]:
    """텍스트 → 겹치는 고정 길이 조각 (순수).

    마지막 조각은 `size`보다 짧을 수 있다. 빈 텍스트는 조각 0개.
    `overlap >= size`면 전진하지 못하므로 거부한다(무한 루프 방지).
    """
    if size <= 0:
        raise ValueError("size는 1 이상이어야 합니다")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap은 0 이상 size 미만이어야 합니다")

    text = text.strip()
    if not text:
        return []

    step = size - overlap
    chunks: list[str] = []
    i = 0
    while i < len(text):
        chunks.append(text[i:i + size])
        if i + size >= len(text):
            break
        i += step
    return chunks


def chunk_documents(docs: list[str], *, size: int = DEFAULT_CHUNK_SIZE,
                    overlap: int = DEFAULT_OVERLAP) -> list[Chunk]:
    """문서 목록 → `Chunk` 목록 (순수). 빈 문서는 조각을 만들지 않지만 **번호는 소비한다**.

    번호를 다시 매기지 않는 이유는 research의 출처 번호와 같다 — 2번 문서가 비었다고 3번을 2번으로
    당기면 인용이 어긋난다.
    """
    chunks: list[Chunk] = []
    for source_id, doc in enumerate(docs, start=1):
        for index, piece in enumerate(chunk_text(doc, size=size, overlap=overlap)):
            chunks.append(Chunk(text=piece, source_id=source_id, index=index))
    return chunks


# ── 순수: 유사도 ──────────────────────────────────────────────────────────


def cosine(a: list[float], b: list[float]) -> float:
    """코사인 유사도 (순수). 영벡터가 끼면 0.0 (0으로 나누지 않는다)."""
    if len(a) != len(b):
        raise ValueError(f"차원 불일치: {len(a)} vs {len(b)}")
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


# ── 포트 ─────────────────────────────────────────────────────────────────


@runtime_checkable
class VectorStore(Protocol):
    """벡터 저장·검색 경계. 실 구현(pgvector)과 인메모리·fake가 만족하는 계약."""

    def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """조각과 벡터를 짝지어 저장한다(길이가 같아야 한다)."""
        ...

    def search(self, vector: list[float], top_k: int) -> list[tuple[Chunk, float]]:
        """질의 벡터와 가장 유사한 조각 `top_k`개를 (조각, 점수) 내림차순으로."""
        ...


# ── 구현: 인메모리 ────────────────────────────────────────────────────────


class InMemoryVectorStore:
    """의존 0의 벡터 저장소. 완전히 결정적이라 테스트·소규모 리서치에 그대로 쓴다.

    동점이면 **삽입 순서**를 유지한다(파이썬 정렬이 안정 정렬).
    """

    def __init__(self) -> None:
        self._items: list[tuple[Chunk, list[float]]] = []

    def __len__(self) -> int:
        return len(self._items)

    def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError(f"조각 {len(chunks)}개와 벡터 {len(vectors)}개의 수가 다릅니다")
        self._items.extend(zip(chunks, vectors))

    def search(self, vector: list[float], top_k: int) -> list[tuple[Chunk, float]]:
        if top_k <= 0:
            return []
        scored = [(chunk, cosine(vector, vec)) for chunk, vec in self._items]
        return sorted(scored, key=lambda pair: -pair[1])[:top_k]


# ── 구현: pgvector (SQL 빌더는 순수, 연결은 주입) ─────────────────────────


def _check_ident(name: str) -> str:
    """테이블명 검증 — SQL에 보간되는 유일한 지점이라 화이트리스트로 막는다."""
    if not _IDENT.match(name):
        raise ValueError(f"안전하지 않은 테이블명: {name!r}")
    return name


def vector_literal(vector: list[float]) -> str:
    """벡터 → pgvector 리터럴 `[0.1,0.2]` (순수)."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def create_table_sql(table: str, dim: int) -> str:
    """pgvector 테이블 DDL (순수). 확장은 호출자가 미리 켜 둔다(`CREATE EXTENSION vector`)."""
    if dim <= 0:
        raise ValueError("dim은 1 이상이어야 합니다")
    t = _check_ident(table)
    return (
        f"CREATE TABLE IF NOT EXISTS {t} ("
        " id BIGSERIAL PRIMARY KEY,"
        " text TEXT NOT NULL,"
        " source_id INTEGER NOT NULL,"
        " chunk_index INTEGER NOT NULL,"
        f" embedding VECTOR({dim}) NOT NULL)"
    )


def insert_sql(table: str) -> str:
    """조각 삽입문 (순수). 값은 파라미터 바인딩."""
    t = _check_ident(table)
    return (f"INSERT INTO {t} (text, source_id, chunk_index, embedding)"
            " VALUES (%s, %s, %s, %s::vector)")


def search_sql(table: str) -> str:
    """유사도 검색문 (순수). `<=>`는 pgvector의 코사인 거리 → 점수는 `1 - 거리`."""
    t = _check_ident(table)
    return (f"SELECT text, source_id, chunk_index, 1 - (embedding <=> %s::vector) AS score"
            f" FROM {t} ORDER BY embedding <=> %s::vector LIMIT %s")


class PgVectorStore:
    """pgvector 저장소 — **연결 객체를 주입**받는다(DB-API 2.0 커서 계약만 가정).

    psycopg를 이 모듈이 import하지 않으므로 테스트는 가짜 연결로 SQL·바인딩을 검증한다.
    🔴 **실 Postgres 왕복은 미검증**(로컬 DB 미배선). 연결·확장 설치는 호출자 책임.
    """

    def __init__(self, conn: Any, *, table: str = "research_chunks", dim: int = 768) -> None:
        self._conn = conn
        self._table = _check_ident(table)
        self._dim = dim

    def create_table(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(create_table_sql(self._table, self._dim))

    def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError(f"조각 {len(chunks)}개와 벡터 {len(vectors)}개의 수가 다릅니다")
        if not chunks:
            return
        rows = [(c.text, c.source_id, c.index, vector_literal(v))
                for c, v in zip(chunks, vectors)]
        with self._conn.cursor() as cur:
            cur.executemany(insert_sql(self._table), rows)

    def search(self, vector: list[float], top_k: int) -> list[tuple[Chunk, float]]:
        if top_k <= 0:
            return []
        lit = vector_literal(vector)
        with self._conn.cursor() as cur:
            cur.execute(search_sql(self._table), (lit, lit, top_k))
            rows = cur.fetchall()
        return [(Chunk(text=r[0], source_id=r[1], index=r[2]), float(r[3])) for r in rows]


# ── 조립: Retriever ───────────────────────────────────────────────────────


class Retriever:
    """임베더 + 저장소를 묶어 '색인 → 검색'을 제공한다. 둘 다 주입이라 엔진은 프로바이더·DB를 모른다."""

    def __init__(self, embedder: EmbeddingGateway, store: VectorStore, *,
                 model: str | None = None,
                 chunk_size: int = DEFAULT_CHUNK_SIZE,
                 overlap: int = DEFAULT_OVERLAP) -> None:
        self._embedder = embedder
        self._store = store
        self._model = model
        self._chunk_size = chunk_size
        self._overlap = overlap

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            return self._embedder.embed(texts)   # 구현의 기본 모델 사용
        return self._embedder.embed(texts, model=self._model)

    def index(self, docs: list[str]) -> int:
        """문서 목록을 청킹·임베딩해 저장. 저장한 조각 수를 돌려준다."""
        chunks = chunk_documents(docs, size=self._chunk_size, overlap=self._overlap)
        if not chunks:
            return 0
        self._store.add(chunks, self._embed([c.text for c in chunks]))
        return len(chunks)

    def retrieve(self, query: str, top_k: int = 5) -> list[tuple[Chunk, float]]:
        """질의와 유사한 조각 `top_k`개."""
        if not query.strip() or top_k <= 0:
            return []
        return self._store.search(self._embed([query])[0], top_k)
