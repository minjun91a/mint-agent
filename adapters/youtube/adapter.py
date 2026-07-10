# -*- coding: utf-8 -*-
"""
adapters.youtube.adapter — YouTube 플랫폼 어댑터 (`core.pipeline.PlatformAdapter` 구현).

엔진은 이 클래스를 PlatformAdapter 포트로만 다룬다(구체 타입 모름). 현재:
  - review : ✅ compliance 게이트(ComplianceReviewer)로 위임
  - produce: 🔄 후속 이식 대기 (story_writer/tts/image/video) — 명시적 NotImplementedError
  - publish: ✅ YouTubePublisher 위임(백엔드 주입 시) — 실 OAuth 배선은 후속
"""
from __future__ import annotations

from typing import Any

from adapters.youtube.publish import YouTubePublisher
from adapters.youtube.review import _DEFAULT_MODEL, ComplianceReviewer
from core.trust.verify import Verdict


class YouTubeAdapter:
    """YouTube produce/review/publish 어댑터."""

    def __init__(self, *, review_deep: bool = True, model: str = _DEFAULT_MODEL,
                 publisher: YouTubePublisher | None = None) -> None:
        self._reviewer = ComplianceReviewer(deep=review_deep, model=model)
        self._publisher = publisher

    def produce(self, brief: Any) -> Any:
        raise NotImplementedError(
            "produce 파이프라인은 후속 이식 — story_writer/tts/image_gen/video_builder 모듈 필요"
        )

    def review(self, content: dict, seo: dict | None = None) -> Verdict:
        """발행 전 컴플라이언스 검증 → Verdict.

        엔진 계약(`PlatformAdapter.review`)의 `context`는 optional이므로 `None`이 올 수 있다.
        youtube에서 그 자리는 SEO 메타이고, 없으면 빈 메타로 검사한다(→ AI 라벨 누락이 잡힌다).
        """
        return self._reviewer.review(content, seo or {})

    def publish(self, asset: dict) -> dict:
        """asset(dict) → YouTubePublisher로 위임. publisher 미주입 시 명시적 에러."""
        if self._publisher is None:
            raise NotImplementedError(
                "publish 백엔드 미배선 — YouTubePublisher(OAuth 클라이언트 백엔드)를 주입하라"
            )
        return self._publisher.publish(**asset)
