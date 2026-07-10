# -*- coding: utf-8 -*-
"""
core.embedding — 임베딩 호출 게이트웨이 (엔진의 단일 임베딩 seam).

`core.llm`의 `LLMGateway`와 **같은 모양**이다: 포트 하나(`EmbeddingGateway`) 뒤에 프로바이더
구현을 두고, SDK import는 각 구현의 `embed` 안에서만 일어난다. 어댑터·엔진은 어느 프로바이더인지
모른 채 동작하므로 프로바이더를 바꿔도 호출부는 불변이다.

  - `available` = 실제 호출 가능 여부(키 보유). False면 호출부가 RAG를 건너뛰고 원본을 쓴다.
  - `embed(texts, *, model) -> list[list[float]]` = 텍스트 목록 → 벡터 목록(입력과 같은 순서·길이).

SDK 응답 형태 주의(2026-07 기준 확인):
  - **구 SDK** `google.generativeai`: `genai.embed_content(model='models/gemini-embedding-001',
    content=...)` → dict, 키 `"embedding"`. `content`가 문자열이면 벡터 하나, 문자열 목록이면
    벡터 목록이 온다. (이 프로젝트의 `GeminiGateway`도 구 SDK를 쓰므로 여기서도 구 SDK에 맞춘다.)
  - **신 SDK** `google.genai`는 `client.models.embed_content(contents=...)` → `result.embeddings[i].values`로
    모양이 다르다. 지금 쓰지 않는다.
  - OpenAI: `client.embeddings.create(model=..., input=[...])` → `resp.data[i].embedding`.

두 형태(단일 벡터 / 벡터 목록)를 모두 `_as_vectors`가 정규화한다 — 프로바이더가 무엇을 돌려주든
포트의 계약은 "입력 개수만큼의 벡터 목록"이다.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger("mint.core.embedding")

# 프로바이더 기본 모델. 구 SDK는 'models/' 접두사를 요구한다.
GEMINI_EMBED_MODEL = "models/gemini-embedding-001"
OPENAI_EMBED_MODEL = "text-embedding-3-small"


@runtime_checkable
class EmbeddingGateway(Protocol):
    """임베딩 호출 포트. 어댑터는 구체 SDK가 아니라 이 인터페이스에 의존한다."""

    @property
    def available(self) -> bool:
        """실제 호출 가능 여부(예: API 키 보유). False면 호출부는 RAG를 건너뛴다."""
        ...

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        """텍스트 목록 → 벡터 목록. 입력과 **같은 순서·같은 개수**를 보장한다."""
        ...


def _as_vectors(raw: Any, expected: int) -> list[list[float]]:
    """프로바이더 응답 → 벡터 목록으로 정규화.

    단일 입력이면 프로바이더가 벡터 하나(`[0.1, 0.2, ...]`)를 줄 수 있고, 배치면 벡터 목록을 준다.
    개수가 입력과 다르면 조용히 넘기지 않고 **에러를 낸다** — 벡터와 청크의 짝이 어긋나면
    엉뚱한 근거가 인용되기 때문이다.
    """
    if raw is None:
        raise ValueError("임베딩 응답이 비어 있습니다")
    vectors = [raw] if raw and not isinstance(raw[0], (list, tuple)) else list(raw)
    if len(vectors) != expected:
        raise ValueError(f"임베딩 개수 불일치: 입력 {expected}건, 응답 {len(vectors)}건")
    return [[float(x) for x in v] for v in vectors]


class GeminiEmbedder:
    """`google.generativeai` 뒤의 임베딩 seam. SDK import는 `embed` 안에서만.

    api_key 미명시 시 호출 시점에 `GEMINI_API_KEY`에서 해석한다(지연 평가 — `GeminiGateway`와 동일).
    """

    def __init__(self, *, api_key: str | None = None) -> None:
        self._explicit_key = api_key

    @property
    def _key(self) -> str:
        if self._explicit_key is not None:
            return self._explicit_key
        return os.getenv("GEMINI_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self._key)

    def embed(self, texts: list[str], *, model: str = GEMINI_EMBED_MODEL) -> list[list[float]]:
        import google.generativeai as genai  # 선택 의존성: 호출 시점 지연 import

        if not texts:
            return []
        genai.configure(api_key=self._key)
        resp = genai.embed_content(model=model, content=texts)
        raw = resp["embedding"] if isinstance(resp, dict) else getattr(resp, "embedding", None)
        return _as_vectors(raw, len(texts))


class OpenAIEmbedder:
    """`openai` 뒤의 임베딩 seam (프로바이더 #2). `GeminiEmbedder`와 같은 포트를 구현한다."""

    def __init__(self, *, api_key: str | None = None) -> None:
        self._explicit_key = api_key

    @property
    def _key(self) -> str:
        if self._explicit_key is not None:
            return self._explicit_key
        return os.getenv("OPENAI_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self._key)

    def embed(self, texts: list[str], *, model: str = OPENAI_EMBED_MODEL) -> list[list[float]]:
        from openai import OpenAI  # 선택 의존성: 호출 시점 지연 import

        if not texts:
            return []
        client = OpenAI(api_key=self._key)
        resp = client.embeddings.create(model=model, input=texts)
        return _as_vectors([d.embedding for d in resp.data], len(texts))
