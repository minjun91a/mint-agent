# -*- coding: utf-8 -*-
"""
adapters.youtube.thumbnail_gen 테스트.

배경 생성(DALL-E)을 주입으로 빼서, 실 이미지 API 없이 Pillow 오버레이·A/B 변형·정리 흐름을 검증.
Pillow 렌더링은 폰트에 의존하나(시스템 폰트 없으면 기본 폰트) 출력 속성은 결정적이라 특성화한다.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from adapters.youtube.thumbnail_gen import (
    _VARIANT_STYLES,
    build_thumbnail,
    render_thumbnail,
)

BG_SIZE = (64, 96)   # 세로 9:16 축소


def _fake_bg(*, size=BG_SIZE, record=None):
    """주입용 가짜 배경 생성기 — 로컬에 단색 PNG를 만든다(실 API 없음)."""
    def gen(prompt: str, path: Path) -> Path:
        if record is not None:
            record.append((prompt, Path(path)))
        Image.new("RGB", size, (10, 20, 30)).save(path, "PNG")
        return Path(path)
    return gen


# ── 순수/결정적 ──

def test_variant_styles_top_and_bottom():
    assert set(_VARIANT_STYLES) == {"A", "B"}
    assert _VARIANT_STYLES["A"]["y_ratio"] < 0.5    # A = 상단
    assert _VARIANT_STYLES["B"]["y_ratio"] > 0.5    # B = 하단
    for s in _VARIANT_STYLES.values():
        assert set(s) == {"y_ratio", "fill", "outline_rgb"}


def test_render_thumbnail_preserves_size_and_mode():
    bg = Image.new("RGB", BG_SIZE, (100, 100, 100))
    out = render_thumbnail(bg, "RISE", _VARIANT_STYLES["A"])
    assert out.size == BG_SIZE
    assert out.mode == "RGB"


def test_render_thumbnail_does_not_mutate_input():
    bg = Image.new("RGB", BG_SIZE, (100, 100, 100))
    before = bg.getpixel((0, 0))
    render_thumbnail(bg, "RISE", _VARIANT_STYLES["B"])
    assert bg.getpixel((0, 0)) == before   # 원본 불변(copy)


# ── 주입 + 오케스트레이션 ──

def test_build_thumbnail_single_variant(tmp_path):
    out = build_thumbnail("a hero", "RISE", tmp_path / "t.png",
                          variant="A", gen_bg=_fake_bg())
    assert out == tmp_path / "t.png"
    assert out.exists()
    with Image.open(out) as im:
        assert im.size == BG_SIZE
    assert not (tmp_path / "t.bg.png").exists()   # 배경 임시파일 정리됨


def test_build_thumbnail_passes_prompt_to_gen_bg(tmp_path):
    rec: list = []
    build_thumbnail("my prompt", "HOOK", tmp_path / "t.png",
                    variant="A", gen_bg=_fake_bg(record=rec))
    assert len(rec) == 1
    assert rec[0][0] == "my prompt"
    assert rec[0][1].name == "t.bg.png"           # 배경 경로 규칙


def test_build_thumbnail_ab_variant(tmp_path):
    res = build_thumbnail("a hero", "RISE", tmp_path / "t.png",
                          variant="AB", alt_hook_text="WHAT NEXT?", gen_bg=_fake_bg())
    assert isinstance(res, dict)
    assert res["primary"] == tmp_path / "t.png"
    assert res["variants"] == [tmp_path / "t.A.png", tmp_path / "t.B.png"]
    for p in [res["primary"], *res["variants"]]:
        assert Path(p).exists()
    assert not (tmp_path / "t.bg.png").exists()   # 배경 임시파일 정리됨
