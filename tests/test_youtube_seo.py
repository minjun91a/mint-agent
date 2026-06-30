# -*- coding: utf-8 -*-
"""
adapters.youtube.seo 테스트.

LLM(Gemini)이 끼므로 원본 parity 불가 → 순수 로직 특성화 + 가짜 게이트웨이 주입(mock 패턴).
"""
from __future__ import annotations

import json

import pytest

from adapters.youtube.seo import (
    _build_fallback,
    _build_user_msg,
    _ensure_15_tags,
    _resolve_category_id,
    generate_seo_meta,
)

SCRIPT = {
    "title": "The Murim Genius",
    "category": "Murim",
    "hook": "A fallen master awakens with a system.",
    "body": "He climbs the endless tower and reclaims his clan.",
    "cliffhanger": "But the final gate was never meant to open.",
    "_used_tropes": ["regression"],
    "thumbnail_text": "RISE",
}


class _FakeGateway:
    """주입용 가짜 LLM 게이트웨이 — canned 텍스트 반환 또는 예외 발생 (mock 패턴)."""

    available = True

    def __init__(self, *, text: str | None = None, raises: Exception | None = None) -> None:
        self._text = text
        self._raises = raises

    def complete(self, system: str, prompt: str, *, model: str,
                 temperature: float = 0.4, json_mode: bool = True) -> str:
        if self._raises is not None:
            raise self._raises
        return self._text or "{}"


# ── 순수 로직 ──

@pytest.mark.parametrize("cat,exp", [
    ("Murim", "1"), ("Cultivation", "1"), ("Tower Dungeon", "20"),
    ("", "24"), ("Unknown", "24"),
])
def test_resolve_category_id(cat, exp):
    assert _resolve_category_id(cat) == exp


def test_ensure_15_tags_dedup_lowercase_truncate():
    out = _ensure_15_tags(["Murim", "murim", "   ", "X" * 40], "Murim")
    assert out.count("murim") == 1
    assert all(t == t.lower() for t in out)
    assert all(len(t) <= 30 for t in out)


def test_ensure_15_tags_caps_at_15():
    out = _ensure_15_tags([f"tag{i}" for i in range(30)], "Murim")
    assert out == [f"tag{i}" for i in range(15)]


def test_ensure_15_tags_appends_seeds_when_short():
    out = _ensure_15_tags(["custom"], "Murim")
    assert out[0] == "custom"
    assert "murim" in out          # 카테고리 시드 채움
    assert "shorts" in out         # BASE 시드 채움


def test_build_user_msg_script_only_and_with_trends():
    assert "# TREND_CONTEXT" not in _build_user_msg(SCRIPT, None)
    msg = _build_user_msg(SCRIPT, [{"category": "Murim", "summary": "rising trend"}])
    assert "# SCRIPT" in msg and "# TREND_CONTEXT" in msg and "rising trend" in msg


def test_build_fallback_structure():
    fb = _build_fallback(SCRIPT, "Murim")
    assert set(fb.keys()) == {"title", "description", "tags", "hashtags", "category_id"}
    assert fb["title"] == "The Murim Genius"
    assert fb["category_id"] == "1"
    assert "🤖 Created with generative AI." in fb["description"]
    assert len(fb["tags"]) <= 15


# ── LLM 경계: 가짜 게이트웨이 주입 ──

def test_generate_normalizes_injected_response():
    payload = {"title": "X" * 120, "description": "d",
               "tags": ["Aaa", "aaa", "bbb"], "hashtags": ["shorts", "aiwebtoon"]}
    meta = generate_seo_meta(SCRIPT, gateway=_FakeGateway(text=json.dumps(payload)))
    assert len(meta["title"]) == 95                     # 120 → 95 절단
    assert meta["hashtags"] == ["#shorts", "#aiwebtoon"]  # # 접두
    assert meta["category_id"] == "1"
    assert all(t == t.lower() for t in meta["tags"])


def test_generate_falls_back_on_gateway_error():
    meta = generate_seo_meta(SCRIPT, gateway=_FakeGateway(raises=RuntimeError("api down")))
    assert meta["title"] == "The Murim Genius"
    assert "🤖 Created with generative AI." in meta["description"]


def test_generate_falls_back_on_invalid_json():
    meta = generate_seo_meta(SCRIPT, gateway=_FakeGateway(text="not json {"))
    assert meta["title"] == "The Murim Genius"
    assert meta["category_id"] == "1"


def test_generate_falls_back_when_gateway_unavailable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    meta = generate_seo_meta(SCRIPT)   # 주입 없음 + 키 없음 → 게이트웨이 미가용 → fallback
    assert meta["title"] == "The Murim Genius"
    assert len(meta["tags"]) <= 15
