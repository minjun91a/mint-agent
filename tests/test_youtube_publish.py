# -*- coding: utf-8 -*-
"""
adapters.youtube.publish 테스트.

publish는 실제 YouTube API라 원본 parity가 불가 → 두 층으로 검증:
  1. build_video_body — 순수 결정적 로직(특성화)
  2. YouTubePublisher — 가짜(fake) 백엔드 주입으로 호출/결과 검증(mock 패턴, 외부 의존 0)
"""
from __future__ import annotations

from adapters.youtube.publish import (
    DEFAULT_TAGS,
    YouTubePublisher,
    build_video_body,
)


# ── 순수 로직: build_video_body 특성화 ──

def test_body_appends_ai_disclosure_when_absent():
    b = build_video_body("t", "plain desc")
    assert "🤖" in b["snippet"]["description"]
    assert b["snippet"]["description"].endswith("AI-generated assets.")


def test_body_keeps_description_when_disclosure_present():
    b = build_video_body("t", "already 🤖 disclosed")
    assert b["snippet"]["description"] == "already 🤖 disclosed"


def test_body_truncates_title_to_100():
    assert len(build_video_body("x" * 150, "d")["snippet"]["title"]) == 100


def test_body_truncates_description_to_5000():
    # 🤖 포함 → 고지 미첨부, 5000자 절단
    b = build_video_body("t", "🤖" + "y" * 6000)
    assert len(b["snippet"]["description"]) == 5000


def test_body_default_tags_when_none():
    assert build_video_body("t", "d")["snippet"]["tags"] == DEFAULT_TAGS


def test_body_empty_tags_falls_back_to_default():
    assert build_video_body("t", "d", tags=[])["snippet"]["tags"] == DEFAULT_TAGS


def test_body_limits_tags_to_15():
    b = build_video_body("t", "d", tags=[f"t{i}" for i in range(20)])
    assert len(b["snippet"]["tags"]) == 15


def test_body_status_flags_and_passthrough():
    b = build_video_body("t", "d", category_id="1", privacy="public")
    assert b["snippet"]["categoryId"] == "1"
    assert b["snippet"]["defaultLanguage"] == "en"
    assert b["status"] == {
        "privacyStatus": "public",
        "selfDeclaredMadeForKids": False,
        "madeForKids": False,
    }


# ── I/O 층: 가짜 백엔드 주입 ──

class FakeBackend:
    """UploadBackend 계약만 만족하는 테스트 더블 — 호출 기록, 외부 의존 0."""

    def __init__(self, video_id: str = "vid123") -> None:
        self.video_id = video_id
        self.inserted = None
        self.thumbnail = None
        self.caption = None

    def insert_video(self, body, video_path):
        self.inserted = (body, video_path)
        return self.video_id

    def set_thumbnail(self, video_id, thumbnail_path):
        self.thumbnail = (video_id, thumbnail_path)

    def insert_caption(self, video_id, srt_path, language, name):
        self.caption = (video_id, srt_path, language, name)
        return True


def test_publish_returns_result_and_passes_built_body(tmp_path):
    be = FakeBackend("abc")
    res = YouTubePublisher(be).publish(tmp_path / "v.mp4", None, "Title", "desc", privacy="unlisted")
    assert res == {
        "video_id": "abc",
        "url": "https://youtu.be/abc",
        "status": "unlisted",
        "captions_uploaded": False,
    }
    body, _ = be.inserted
    assert body == build_video_body("Title", "desc", privacy="unlisted")
    assert be.thumbnail is None   # 썸네일 None → 호출 안 함


def test_publish_sets_thumbnail_when_file_exists(tmp_path):
    thumb = tmp_path / "t.png"; thumb.write_bytes(b"x")
    be = FakeBackend()
    YouTubePublisher(be).publish(tmp_path / "v.mp4", thumb, "T", "d")
    assert be.thumbnail is not None


def test_publish_uploads_caption_when_srt_exists(tmp_path):
    srt = tmp_path / "c.srt"; srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    be = FakeBackend()
    res = YouTubePublisher(be).publish(tmp_path / "v.mp4", None, "T", "d", srt_path=srt)
    assert res["captions_uploaded"] is True
    assert be.caption[2] == "en"   # language


def test_publish_swallows_thumbnail_error(tmp_path):
    thumb = tmp_path / "t.png"; thumb.write_bytes(b"x")

    class BoomBackend(FakeBackend):
        def set_thumbnail(self, video_id, thumbnail_path):
            raise RuntimeError("boom")

    res = YouTubePublisher(BoomBackend()).publish(tmp_path / "v.mp4", thumb, "T", "d")
    assert res["video_id"] == "vid123"   # 업로드 성공, 썸네일 실패는 무시
