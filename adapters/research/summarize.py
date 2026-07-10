# -*- coding: utf-8 -*-
"""
adapters.research.summarize — 수집한 본문 → 출처 인용 요약(LLM).

원본: `youtube-agent/agents/research.py`의 `_SYSTEM_PROMPT`/`summarize`.

순수/IO 분리(LLM 모듈 표준 패턴 — seo·story_writer와 동일):
  - 순수: `build_sources_block`(문자 예산 배분) · `build_prompt`(QUERY/SOURCES 조립)
    → 프롬프트 리터럴·예산 규칙 모두 **원본과 동일**(parity 대조 대상).
  - LLM : `summarize`가 `LLMGateway`를 받는다(기본 `default_gemini()` = 관측 래핑된 조립점).

원본 동작 보존 메모:
  - 출처 번호는 `hits` **원본 인덱스**다. 본문이 빈 소스는 건너뛰되 번호는 소비되므로
    번호에 구멍이 생길 수 있다(예: 2번 페치 실패 → `[출처 1]`, `[출처 3]`). 인용 무결성을
    위해 번호를 다시 매기지 **않는다**.
  - 총합 예산(`MAX_TOTAL_CHARS`)이 500자 미만 남으면 이후 소스를 버린다.

의도적 차이: 게이트웨이 미가용 메시지는 프로바이더 이름을 말하지 않는다. 게이트웨이는 주입
대상이라 Gemini라고 단정할 수 없다(원본은 `"❌ GEMINI_API_KEY 미설정"`).
"""
from __future__ import annotations

import logging
from typing import Protocol

from adapters.research.fetch import MAX_BODY_CHARS
from adapters.research.search import SearchHit
from core.gateway import default_gemini
from core.llm import LLMGateway


class Selector(Protocol):
    """본문 발췌 선택기 포트 — 질의와 본문 목록을 받아 **같은 길이**의 발췌 목록을 돌려준다."""

    def select(self, query: str, bodies: list[str]) -> list[str]: ...

log = logging.getLogger("mint.adapters.research.summarize")

MAX_TOTAL_CHARS = 24000   # LLM 입력 총합 한도(안전 여유)
_MIN_BUDGET = 500         # 남은 예산이 이보다 적으면 소스 추가 중단
_DEFAULT_MODEL = "gemini-2.5-flash"
_TEMPERATURE = 0.3

UNAVAILABLE = "❌ LLM 게이트웨이 미가용 — API 키를 확인하세요"
NO_SOURCES = "❌ 본문을 가져올 수 있는 출처가 없습니다."

SYSTEM_PROMPT = """당신은 리서치 어시스턴트입니다.
사용자 질의에 대해 제공된 웹 본문 발췌를 근거로만 한국어로 답하세요.

규칙:
- 핵심 결론을 먼저 3~5줄 bullet로 제시
- 각 사실 옆에 [출처 N] 형식으로 인용 (N은 입력 SOURCES 인덱스)
- 발췌에 없는 내용은 "출처 부족"이라고 명시, 추측 금지
- 마지막에 "## 출처" 섹션에 N. 제목 — URL 형식으로 모두 나열
"""


def build_sources_block(hits: list[SearchHit], bodies: list[str], *,
                        max_body: int = MAX_BODY_CHARS,
                        max_total: int = MAX_TOTAL_CHARS) -> list[str]:
    """(hits, bodies) → 출처 블록 목록 (순수). 문자 예산을 소스 순서대로 배분한다."""
    blocks: list[str] = []
    used = 0
    for i, (h, body) in enumerate(zip(hits, bodies), start=1):
        if not body:
            continue
        budget = max_total - used
        if budget < _MIN_BUDGET:
            break
        snippet = body[:min(max_body, budget)]
        used += len(snippet)
        blocks.append(f"[출처 {i}] {h.title}\nURL: {h.url}\n발췌:\n{snippet}\n")
    return blocks


def build_prompt(query: str, blocks: list[str]) -> str:
    """질의 + 출처 블록 → LLM user 프롬프트 (순수)."""
    return f"# QUERY\n{query}\n\n# SOURCES\n" + "\n---\n".join(blocks)


def summarize(query: str, hits: list[SearchHit], bodies: list[str], *,
              model: str = _DEFAULT_MODEL,
              gateway: LLMGateway | None = None,
              selector: Selector | None = None) -> str:
    """출처 인용을 강제한 한국어 요약. 게이트웨이 미가용/출처 부재는 사유 문자열로 반환.

    `selector`(예: `rag.RagSelector`)를 주면 본문을 질의 관련 발췌로 먼저 좁힌다. 선택기는 본문과
    **같은 길이·같은 순서**를 돌려주기로 계약돼 있어 출처 번호가 어긋나지 않는다.
    """
    gw = gateway if gateway is not None else default_gemini()

    if not gw.available:
        log.warning("LLM 게이트웨이 미가용 — 요약 생략")
        return UNAVAILABLE

    if selector is not None:
        bodies = selector.select(query, bodies)

    blocks = build_sources_block(hits, bodies)
    if not blocks:
        return NO_SOURCES

    return gw.complete(
        SYSTEM_PROMPT,
        build_prompt(query, blocks),
        model=model,
        temperature=_TEMPERATURE,
        json_mode=False,   # 요약은 마크다운 산문이다(JSON 아님)
    ).strip()
