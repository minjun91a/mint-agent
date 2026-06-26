# -*- coding: utf-8 -*-
"""
adapters.youtube.publish — YouTube 발행(업로드) (produce/review/**publish**의 publish).

`youtube-agent/agents/uploader.py`를 모노레포로 이식. 이 모듈은 실제 YouTube Data API를
호출하므로 이전 조각들처럼 "원본 parity"로 검증할 수 없다(실제 업로드라 비결정·부작용).
대신 두 층으로 갈라 테스트한다:

  1. **순수 로직** — `build_video_body()`: 입력 → 업로드 요청 body(dict). 결정적이라 특성화 테스트.
  2. **I/O 층** — `YouTubePublisher`가 **백엔드를 주입**받아 업로드를 위임. 테스트는 가짜(fake)
     백엔드를 주입해 호출/결과를 검증(mock 패턴). 실 백엔드(`GoogleUploadBackend`)는
     `googleapiclient`를 지연 import하므로, 테스트 경로엔 외부 의존이 끼지 않는다.

실 OAuth 클라이언트 배선(get_credentials 등)은 모노레포에 아직 없음 → 라이브 업로드는
`GoogleUploadBackend(youtube_client)`에 클라이언트를 주입해야 동작(후속 인프라).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("mint.adapters.youtube.publish")

# AI 생성 콘텐츠 고지 (원본 동일)
AI_DISCLOSURE = (
    "\n\n---\n"
    "🤖 This video was created with generative AI. "
    "Script, narration, and visuals are AI-generated assets."
)

# tags 미지정 시 기본 태그 (원본 동일)
DEFAULT_TAGS = [
    "AI fantasy", "fantasy shorts", "manhwa", "korean webtoon",
    "cultivation", "regression", "necromancer", "murim",
    "tower of god", "litrpg", "shorts",
]


def build_video_body(title: str, description: str, *,
                     tags: list[str] | None = None,
                     category_id: str = "24",   # 24 = Entertainment
                     privacy: str = "private") -> dict:
    """업로드 요청 body 구성 (순수·결정적). 원본 upload_video의 body 로직과 동일.

    - description에 '🤖'가 없으면 AI 고지 자동 첨부, 5000자 절단
    - title 100자 절단, tags 미지정 시 DEFAULT_TAGS, 15개 한도
    """
    final_description = description if "🤖" in description else (description + AI_DISCLOSURE)
    final_tags = (tags or []) or DEFAULT_TAGS
    return {
        "snippet": {
            "title": title[:100],
            "description": final_description[:5000],
            "tags": final_tags[:15],
            "categoryId": category_id,
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy,           # private | unlisted | public
            "selfDeclaredMadeForKids": False,
            "madeForKids": False,
        },
    }


class UploadBackend(Protocol):
    """업로드 I/O 경계 — 실 구현(Google)과 테스트 fake가 만족하는 계약."""

    def insert_video(self, body: dict, video_path: Path) -> str:
        """영상 업로드 → video_id 반환."""
        ...

    def set_thumbnail(self, video_id: str, thumbnail_path: Path) -> None:
        ...

    def insert_caption(self, video_id: str, srt_path: Path, language: str, name: str) -> bool:
        ...


class GoogleUploadBackend:
    """실 업로드 백엔드 — googleapiclient youtube 리소스를 주입받는다.

    클라이언트 생성/OAuth는 호출자 책임(모노레포 미배선). `googleapiclient`는 지연 import.
    """

    def __init__(self, youtube_client: Any) -> None:
        self._yt = youtube_client

    def insert_video(self, body: dict, video_path: Path) -> str:
        from googleapiclient.http import MediaFileUpload  # 지연 import
        media = MediaFileUpload(str(video_path), mimetype="video/mp4",
                                resumable=True, chunksize=1024 * 1024 * 4)
        req = self._yt.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = req.next_chunk()
            if status:
                log.info(f"  진행 {int(status.progress() * 100)}%")
        return response["id"]

    def set_thumbnail(self, video_id: str, thumbnail_path: Path) -> None:
        from googleapiclient.http import MediaFileUpload  # 지연 import
        self._yt.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/png"),
        ).execute()

    def insert_caption(self, video_id: str, srt_path: Path, language: str, name: str) -> bool:
        from googleapiclient.http import MediaFileUpload  # 지연 import
        media = MediaFileUpload(str(srt_path), mimetype="application/octet-stream")
        body = {"snippet": {"videoId": video_id, "language": language, "name": name, "isDraft": False}}
        self._yt.captions().insert(part="snippet", body=body, media_body=media).execute()
        return True


class YouTubePublisher:
    """publish 단계 — body 구성(순수) + 백엔드 위임(I/O). 백엔드 주입으로 테스트 가능."""

    def __init__(self, backend: UploadBackend) -> None:
        self._backend = backend

    def publish(self, video_path: str | Path, thumbnail_path: str | Path | None,
                title: str, description: str, *,
                tags: list[str] | None = None,
                category_id: str = "24",
                privacy: str = "private",
                srt_path: str | Path | None = None,
                caption_language: str = "en") -> dict:
        """업로드 후 {video_id, url, status, captions_uploaded} 반환. 원본 동작과 동일 흐름."""
        body = build_video_body(title, description, tags=tags,
                                category_id=category_id, privacy=privacy)
        video_id = self._backend.insert_video(body, Path(video_path))
        log.info(f"업로드 완료: video_id={video_id}")

        if thumbnail_path and Path(thumbnail_path).exists():
            try:
                self._backend.set_thumbnail(video_id, Path(thumbnail_path))
            except Exception as exc:
                log.warning(f"썸네일 설정 실패: {exc}")

        captions_uploaded = False
        if srt_path and Path(srt_path).exists():
            try:
                captions_uploaded = self._backend.insert_caption(
                    video_id, Path(srt_path), caption_language, "English")
            except Exception as exc:
                log.warning(f"자막 업로드 실패: {exc}")

        return {
            "video_id": video_id,
            "url": f"https://youtu.be/{video_id}",
            "status": privacy,
            "captions_uploaded": captions_uploaded,
        }
