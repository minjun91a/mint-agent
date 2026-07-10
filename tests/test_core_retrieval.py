# -*- coding: utf-8 -*-
"""
core.retrieval 특성화 테스트.

청킹·유사도·SQL 빌더는 순수라 목 없이 검증한다. `PgVectorStore`는 **연결 주입**이라 가짜 연결로
SQL·바인딩을 고정하고, 실 Postgres 왕복은 🔴 미검증(로컬 DB 미배선)임을 문서가 명시한다.

엔진 범용성: 저장소·임베더가 주입이므로 이 모듈은 research도 youtube도 모른다 — 토이 문서로 검증한다.
"""
from __future__ import annotations

import pytest

from core.retrieval import (
    Chunk,
    InMemoryVectorStore,
    PgVectorStore,
    Retriever,
    VectorStore,
    chunk_documents,
    chunk_text,
    cosine,
    create_table_sql,
    insert_sql,
    search_sql,
    vector_literal,
)


class _WordEmbedder:
    """결정적 가짜 임베더 — 고정 어휘의 등장 횟수를 벡터로. (키워드가 겹칠수록 유사도가 높다)"""

    VOCAB = ("고양이", "강아지", "우주")
    available = True

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(t.count(w)) for w in self.VOCAB] for t in texts]


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._conn.executed.append((sql, params))

    def executemany(self, sql, rows):
        self._conn.many.append((sql, list(rows)))

    def fetchall(self):
        return self._conn.rows


class _FakeConn:
    """DB-API 2.0 커서 계약만 흉내내는 가짜 연결(psycopg 불필요)."""

    def __init__(self, rows=()):
        self.executed: list = []
        self.many: list = []
        self.rows = list(rows)

    def cursor(self):
        return _FakeCursor(self)


# ── 순수: chunk_text ────────────────────────────────────────────────


def test_chunk_text_splits_with_overlap():
    assert chunk_text("abcdefghij", size=4, overlap=1) == ["abcd", "defg", "ghij"]


def test_chunk_text_last_chunk_may_be_short():
    assert chunk_text("abcdefg", size=3, overlap=0) == ["abc", "def", "g"]


def test_chunk_text_shorter_than_size_is_one_chunk():
    assert chunk_text("abc", size=10, overlap=2) == ["abc"]


def test_chunk_text_empty_returns_nothing():
    assert chunk_text("") == []
    assert chunk_text("   \n ") == []


def test_chunk_text_rejects_overlap_ge_size():
    """overlap이 size 이상이면 전진하지 못한다 — 무한 루프 대신 에러."""
    with pytest.raises(ValueError, match="overlap"):
        chunk_text("abc", size=3, overlap=3)


def test_chunk_text_rejects_nonpositive_size():
    with pytest.raises(ValueError, match="size"):
        chunk_text("abc", size=0)


# ── 순수: chunk_documents ───────────────────────────────────────────


def test_chunk_documents_assigns_1based_source_ids():
    chunks = chunk_documents(["abcd", "ef"], size=2, overlap=0)
    assert [(c.source_id, c.index, c.text) for c in chunks] == [
        (1, 0, "ab"), (1, 1, "cd"), (2, 0, "ef"),
    ]


def test_chunk_documents_empty_doc_consumes_its_number():
    """2번 문서가 비어도 3번은 3번이다 — 인용 번호를 다시 매기지 않는다."""
    chunks = chunk_documents(["a", "", "c"], size=5, overlap=0)
    assert [c.source_id for c in chunks] == [1, 3]


# ── 순수: cosine ────────────────────────────────────────────────────


def test_cosine_identical_vectors_is_one():
    assert cosine([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero():
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_vector_is_zero_not_error():
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_rejects_dimension_mismatch():
    with pytest.raises(ValueError, match="차원 불일치"):
        cosine([1.0], [1.0, 2.0])


# ── InMemoryVectorStore ─────────────────────────────────────────────


def _chunks(*texts: str) -> list[Chunk]:
    return [Chunk(text=t, source_id=i, index=0) for i, t in enumerate(texts, start=1)]


def test_in_memory_store_satisfies_port():
    assert isinstance(InMemoryVectorStore(), VectorStore)


def test_in_memory_store_ranks_by_similarity():
    store = InMemoryVectorStore()
    store.add(_chunks("a", "b"), [[1.0, 0.0], [0.0, 1.0]])

    got = store.search([1.0, 0.0], top_k=2)
    assert [c.text for c, _ in got] == ["a", "b"]
    assert got[0][1] == pytest.approx(1.0)


def test_in_memory_store_honors_top_k():
    store = InMemoryVectorStore()
    store.add(_chunks("a", "b", "c"), [[1.0], [1.0], [1.0]])
    assert len(store.search([1.0], top_k=2)) == 2


def test_in_memory_store_top_k_zero_returns_empty():
    store = InMemoryVectorStore()
    store.add(_chunks("a"), [[1.0]])
    assert store.search([1.0], top_k=0) == []


def test_in_memory_store_ties_keep_insertion_order():
    store = InMemoryVectorStore()
    store.add(_chunks("first", "second"), [[1.0, 0.0], [1.0, 0.0]])
    assert [c.text for c, _ in store.search([1.0, 0.0], top_k=2)] == ["first", "second"]


def test_in_memory_store_rejects_length_mismatch():
    with pytest.raises(ValueError, match="수가 다릅니다"):
        InMemoryVectorStore().add(_chunks("a", "b"), [[1.0]])


# ── 순수: pgvector SQL 빌더 ─────────────────────────────────────────


def test_vector_literal_format():
    assert vector_literal([0.5, 1.0]) == "[0.5,1.0]"


def test_create_table_sql_includes_dim():
    assert "VECTOR(768)" in create_table_sql("research_chunks", 768)


def test_search_sql_uses_cosine_distance_operator():
    sql = search_sql("t")
    assert "<=>" in sql
    assert "1 - (embedding <=> %s::vector) AS score" in sql
    assert sql.rstrip().endswith("LIMIT %s")


def test_insert_sql_binds_all_values():
    assert insert_sql("t").count("%s") == 4


@pytest.mark.parametrize("bad", ["t; DROP TABLE x", "1t", "t-1", "", "t x"])
def test_sql_builders_reject_unsafe_table_names(bad):
    """테이블명은 SQL에 보간되는 유일한 지점 — 화이트리스트로 막는다."""
    with pytest.raises(ValueError, match="안전하지 않은"):
        create_table_sql(bad, 3)


def test_create_table_sql_rejects_nonpositive_dim():
    with pytest.raises(ValueError, match="dim"):
        create_table_sql("t", 0)


# ── PgVectorStore (연결 주입, 실 DB 미검증) ────────────────────────


def test_pg_store_satisfies_port():
    assert isinstance(PgVectorStore(_FakeConn()), VectorStore)


def test_pg_store_create_table_executes_ddl():
    conn = _FakeConn()
    PgVectorStore(conn, table="t", dim=3).create_table()
    assert conn.executed[0][0].startswith("CREATE TABLE IF NOT EXISTS t")


def test_pg_store_add_binds_rows_with_vector_literals():
    conn = _FakeConn()
    PgVectorStore(conn, table="t").add(_chunks("a"), [[0.5, 1.0]])

    sql, rows = conn.many[0]
    assert sql == insert_sql("t")
    assert rows == [("a", 1, 0, "[0.5,1.0]")]


def test_pg_store_add_empty_is_noop():
    conn = _FakeConn()
    PgVectorStore(conn).add([], [])
    assert conn.many == []


def test_pg_store_search_binds_vector_twice_and_limit():
    conn = _FakeConn(rows=[("chunk", 2, 1, 0.87)])
    got = PgVectorStore(conn, table="t").search([1.0], top_k=3)

    sql, params = conn.executed[0]
    assert sql == search_sql("t")
    assert params == ("[1.0]", "[1.0]", 3)   # ORDER BY와 SELECT에 각각 한 번
    assert got == [(Chunk(text="chunk", source_id=2, index=1), 0.87)]


def test_pg_store_search_top_k_zero_skips_query():
    conn = _FakeConn()
    assert PgVectorStore(conn).search([1.0], top_k=0) == []
    assert conn.executed == []


def test_pg_store_rejects_unsafe_table_at_construction():
    with pytest.raises(ValueError, match="안전하지 않은"):
        PgVectorStore(_FakeConn(), table="t; DROP TABLE x")


# ── Retriever (엔진 범용성: 플랫폼을 모른다) ───────────────────────


DOCS = ["고양이는 잠을 잔다", "강아지는 뛴다", "우주는 넓다"]


def test_retriever_indexes_all_chunks():
    r = Retriever(_WordEmbedder(), InMemoryVectorStore(), chunk_size=100, overlap=0)
    assert r.index(DOCS) == 3


def test_retriever_retrieves_semantically_closest_doc():
    embedder = _WordEmbedder()
    r = Retriever(embedder, InMemoryVectorStore(), chunk_size=100, overlap=0)
    r.index(DOCS)

    top = r.retrieve("고양이", top_k=1)
    assert top[0][0].text == "고양이는 잠을 잔다"
    assert top[0][0].source_id == 1


def test_retriever_empty_docs_index_nothing():
    r = Retriever(_WordEmbedder(), InMemoryVectorStore())
    assert r.index(["", "  "]) == 0


def test_retriever_blank_query_returns_nothing():
    r = Retriever(_WordEmbedder(), InMemoryVectorStore(), chunk_size=100, overlap=0)
    r.index(DOCS)
    assert r.retrieve("   ", top_k=3) == []


def test_retriever_passes_model_when_given():
    embedder = _WordEmbedder()
    Retriever(embedder, InMemoryVectorStore(), model="m-1", chunk_size=100, overlap=0).index(["고양이"])
    assert embedder.calls == [["고양이"]]


def test_retriever_works_with_pg_store_too():
    """저장소는 주입 — 엔진은 인메모리인지 pgvector인지 모른다."""
    conn = _FakeConn(rows=[("고양이는 잠을 잔다", 1, 0, 0.99)])
    r = Retriever(_WordEmbedder(), PgVectorStore(conn, table="t", dim=3),
                  chunk_size=100, overlap=0)
    r.index(DOCS)

    assert conn.many[0][1][0][1] == 1                       # source_id 보존
    assert r.retrieve("고양이", top_k=1)[0][0].source_id == 1
