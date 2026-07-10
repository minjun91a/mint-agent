# -*- coding: utf-8 -*-
"""
adapters.research.adapter — research/digest 어댑터 (`core.pipeline.PlatformAdapter` 구현).

**두 번째 어댑터.** 엔진(`core/`)은 이 클래스를 PlatformAdapter 포트로만 다룬다(구체 타입 모름).
YouTube 어댑터와 도메인이 전혀 다름에도 엔진 코드가 한 줄도 바뀌지 않는다는 것이 ADR 0003의 명제다.

현재:
  - produce: ✅ 검색 → 본문 페치 → 출처 인용 요약(원본 `run_research` 동작)
  - review : ✅ 출처 인용 검증(`CitationReviewer` → `core.trust.Verdict`)
  - publish: ⬜ 예정 — 백엔드 주입(youtube publish와 같은 패턴)

주입 지점은 둘: HTTP(`fetch`)와 LLM(`gateway`). 둘 다 기본값이 실 구현이라 운영은 무설정으로
돌고, 테스트는 가짜를 넣어 네트워크·모델 없이 결정적으로 돈다.

`review(digest, context)`의 `context`에 `{"bodies": [...]}`를 주면 근거 대조(LLM)까지 하고,
없으면 룰 인용 검증만 한다. `produce`는 원본 `run_research`와 동일한 dict를 돌려주므로 본문을
포함하지 않는다 — 근거 본문이 필요하면 호출부가 `fetch_text` 결과를 보관해 넘긴다.
"""
from __future__ import annotations

from typing import Any

from adapters.research._http import HttpFetcher, urllib_fetch
from adapters.research.fetch import fetch_text
from adapters.research.review import CitationReviewer
from adapters.research.search import search
from adapters.research.summarize import _DEFAULT_MODEL, summarize
from core.llm import LLMGateway
from core.trust.verify import Verdict

NO_HITS = "❌ 검색 결과 없음"


class ResearchAdapter:
    """research produce/review/publish 어댑터."""

    def __init__(self, *, top_k: int = 5, model: str = _DEFAULT_MODEL,
                 gateway: LLMGateway | None = None,
                 fetch: HttpFetcher = urllib_fetch,
                 review_deep: bool = True,
                 reviewer: CitationReviewer | None = None) -> None:
        self._top_k = top_k
        self._model = model
        self._gateway = gateway
        self._fetch = fetch
        self._reviewer = reviewer or CitationReviewer(deep=review_deep, gateway=gateway)

    def produce(self, brief: str) -> dict[str, Any]:
        """질의(brief) → 리서치 다이제스트. 검색 결과가 없으면 사유를 담아 빈 결과를 돌려준다."""
        hits = search(brief, top_k=self._top_k, fetch=self._fetch)
        if not hits:
            return {"query": brief, "summary": NO_HITS, "hits": []}

        bodies = [fetch_text(h.url, fetch=self._fetch) for h in hits]
        summary = summarize(brief, hits, bodies,
                            model=self._model, gateway=self._gateway)
        return {
            "query": brief,
            "summary": summary,
            "hits": [
                {"title": h.title, "url": h.url, "snippet": h.snippet,
                 "fetched_chars": len(b)}
                for h, b in zip(hits, bodies)
            ],
        }

    def review(self, content: dict[str, Any], context: Any = None) -> Verdict:
        """다이제스트 → 출처 인용 검증 Verdict. `context={"bodies": [...]}`면 근거 대조까지."""
        return self._reviewer.review(content, context)

    def publish(self, asset: Any) -> Any:
        raise NotImplementedError(
            "research publish 백엔드 미배선 — 다이제스트 발행 백엔드를 주입하라"
        )
