# -*- coding: utf-8 -*-
"""
adapters.youtube.adapter — YouTube 플랫폼 어댑터 (`core.pipeline.PlatformAdapter` 구현).

엔진은 이 클래스를 PlatformAdapter 포트로만 다룬다(구체 타입 모름). 현재:
  - review : ✅ compliance 게이트(ComplianceReviewer)로 위임
  - produce: 🔄 후속 이식 대기 (story_writer/tts/image/video) — 명시적 NotImplementedError
  - publish: ⬜ 후속 이식 대기 (uploader, YouTube Data API)
"""
from __future__ import annotations

from typing import Any

from adapters.youtube.review import _DEFAULT_MODEL, ComplianceReviewer
from core.trust.verify import Verdict


class YouTubeAdapter:
    """YouTube produce/review/publish 어댑터."""

    def __init__(self, *, review_deep: bool = True, model: str = _DEFAULT_MODEL) -> None:
        self._reviewer = ComplianceReviewer(deep=review_deep, model=model)

    def produce(self, brief: Any) -> Any:
        raise NotImplementedError(
            "produce 파이프라인은 후속 이식 — story_writer/tts/image_gen/video_builder 모듈 필요"
        )

    def review(self, content: dict, seo: dict) -> Verdict:
        """발행 전 컴플라이언스 검증 → Verdict."""
        return self._reviewer.review(content, seo)

    def publish(self, asset: Any) -> Any:
        raise NotImplementedError("publish는 후속 이식 — uploader(YouTube Data API) 모듈 필요")
