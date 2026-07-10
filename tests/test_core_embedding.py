# -*- coding: utf-8 -*-
"""
core.embedding 특성화 테스트.

프로바이더 SDK는 가짜를 `sys.modules`에 심어 대체한다(test_core_llm과 같은 패턴) — 실 네트워크·키 없이
응답 매핑·정규화·키 해석을 결정적으로 고정한다.
"""
from __future__ import annotations

import sys
import types

import pytest

from core.embedding import (
    GEMINI_EMBED_MODEL,
    OPENAI_EMBED_MODEL,
    EmbeddingGateway,
    GeminiEmbedder,
    OpenAIEmbedder,
    _as_vectors,
)


def _install_fake_genai(monkeypatch, *, embedding):
    """가짜 `google.generativeai` — embed_content 인자를 기록하고 canned 응답을 돌려준다."""
    calls: dict = {}

    def embed_content(*, model, content):
        calls["model"] = model
        calls["content"] = content
        return {"embedding": embedding}

    fake = types.ModuleType("google.generativeai")
    fake.configure = lambda **kw: calls.update(kw)
    fake.embed_content = embed_content
    google_pkg = types.ModuleType("google")
    google_pkg.generativeai = fake
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake)
    return calls


def _install_fake_openai(monkeypatch, *, embeddings):
    calls: dict = {}

    class _Datum:
        def __init__(self, e):
            self.embedding = e

    class _Embeddings:
        def create(self, **kwargs):
            calls.update(kwargs)
            return types.SimpleNamespace(data=[_Datum(e) for e in embeddings])

    class OpenAI:
        def __init__(self, *, api_key=None):
            calls["api_key"] = api_key
            self.embeddings = _Embeddings()

    fake = types.ModuleType("openai")
    fake.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", fake)
    return calls


# ── 순수: _as_vectors ───────────────────────────────────────────────


def test_as_vectors_wraps_single_vector():
    assert _as_vectors([0.1, 0.2], 1) == [[0.1, 0.2]]


def test_as_vectors_passes_through_batch():
    assert _as_vectors([[0.1], [0.2]], 2) == [[0.1], [0.2]]


def test_as_vectors_coerces_to_float():
    assert _as_vectors([[1, 2]], 1) == [[1.0, 2.0]]


def test_as_vectors_rejects_count_mismatch():
    """벡터와 청크의 짝이 어긋나면 엉뚱한 근거가 인용된다 — 조용히 넘기지 않는다."""
    with pytest.raises(ValueError, match="개수 불일치"):
        _as_vectors([[0.1], [0.2]], 3)


def test_as_vectors_rejects_none():
    with pytest.raises(ValueError, match="비어 있"):
        _as_vectors(None, 1)


# ── 포트 / available ────────────────────────────────────────────────


def test_embedders_satisfy_port():
    assert isinstance(GeminiEmbedder(api_key="k"), EmbeddingGateway)
    assert isinstance(OpenAIEmbedder(api_key="k"), EmbeddingGateway)


def test_available_false_with_empty_key():
    assert GeminiEmbedder(api_key="").available is False


def test_available_reads_env_lazily(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert GeminiEmbedder().available is False
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert GeminiEmbedder().available is True


# ── Gemini ─────────────────────────────────────────────────────────


def test_gemini_embeds_batch(monkeypatch):
    calls = _install_fake_genai(monkeypatch, embedding=[[0.1, 0.2], [0.3, 0.4]])
    out = GeminiEmbedder(api_key="k").embed(["a", "b"])

    assert out == [[0.1, 0.2], [0.3, 0.4]]
    assert calls["model"] == GEMINI_EMBED_MODEL
    assert calls["content"] == ["a", "b"]


def test_gemini_normalizes_single_vector_response(monkeypatch):
    """구 SDK는 입력이 하나면 벡터 하나를 준다 — 포트 계약은 언제나 '벡터 목록'."""
    _install_fake_genai(monkeypatch, embedding=[0.1, 0.2])
    assert GeminiEmbedder(api_key="k").embed(["only"]) == [[0.1, 0.2]]


def test_gemini_empty_input_skips_sdk(monkeypatch):
    _install_fake_genai(monkeypatch, embedding=[])
    assert GeminiEmbedder(api_key="k").embed([]) == []


def test_gemini_honors_explicit_model(monkeypatch):
    calls = _install_fake_genai(monkeypatch, embedding=[[0.1]])
    GeminiEmbedder(api_key="k").embed(["a"], model="models/custom")
    assert calls["model"] == "models/custom"


# ── OpenAI ─────────────────────────────────────────────────────────


def test_openai_embeds_batch(monkeypatch):
    calls = _install_fake_openai(monkeypatch, embeddings=[[0.5], [0.6]])
    out = OpenAIEmbedder(api_key="k").embed(["a", "b"])

    assert out == [[0.5], [0.6]]
    assert calls["model"] == OPENAI_EMBED_MODEL
    assert calls["input"] == ["a", "b"]
    assert calls["api_key"] == "k"


def test_openai_empty_input_skips_sdk(monkeypatch):
    _install_fake_openai(monkeypatch, embeddings=[])
    assert OpenAIEmbedder(api_key="k").embed([]) == []


def test_both_providers_share_the_same_port(monkeypatch):
    """같은 `embed` 포트가 Gemini·OpenAI에 동일하게 매핑된다 — 호출부는 프로바이더를 모른다."""
    _install_fake_genai(monkeypatch, embedding=[[1.0, 0.0]])
    _install_fake_openai(monkeypatch, embeddings=[[1.0, 0.0]])

    gemini: EmbeddingGateway = GeminiEmbedder(api_key="k")
    openai: EmbeddingGateway = OpenAIEmbedder(api_key="k")
    assert gemini.embed(["x"]) == openai.embed(["x"])
