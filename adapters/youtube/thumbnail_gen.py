# -*- coding: utf-8 -*-
"""
adapters.youtube.thumbnail_gen — 썸네일 생성 (produce 자산화, 배경 이미지 + Pillow 텍스트 오버레이).

`youtube-agent/agents/thumbnail_gen.py`를 모노레포로 이식. 원본은 배경 이미지를 DALL-E로 직접
생성했다 → 여기서는 **배경 생성기(`gen_bg`)를 주입**해, 실 이미지 API 없이 오버레이·A/B 변형·
정리 흐름을 검증한다(image_gen과 같은 주입 사상). Pillow 렌더링은 결정적(폰트에 의존)이라 특성화한다.

  - **순수/결정적** — 변형 스타일(`_VARIANT_STYLES`)·렌더링(`render_thumbnail`: 그라디언트 +
    자동 맞춤 폰트 + 아웃라인 텍스트). 원본 로직과 동일.
  - **주입** — `gen_bg(prompt, path) -> path` 로 배경 생성을 주입(운영은 image_gen 백엔드로 배선).
    시스템 폰트가 없으면 기본 폰트로 폴백 → 크로스 플랫폼.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("mint.adapters.youtube.thumbnail_gen")

# (prompt, output_path) -> 배경 이미지 경로
BackgroundGenerator = Callable[[str, Path], Path]

# 영문 후크용 굵은 폰트 우선(Impact 톤), 한글 fallback. 없으면 기본 폰트. 원본과 동일.
FONT_CANDIDATES = [
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/bahnschrift.ttf",
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/HMKMRHD.ttf",
]

# 변형별 색상·위치 — A/B 테스트용. A: 상단/노랑(충격형) · B: 하단/흰+빨강(질문형). 원본과 동일.
_VARIANT_STYLES: dict[str, dict[str, Any]] = {
    "A": {
        "y_ratio": 0.08,
        "fill": (255, 220, 30),
        "outline_rgb": (0, 0, 0),
    },
    "B": {
        "y_ratio": 0.62,
        "fill": (255, 255, 255),
        "outline_rgb": (200, 0, 0),
    },
}


def _find_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """후보 폰트 중 존재하는 첫 truetype, 없으면 기본 폰트. 원본과 동일."""
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    log.warning("폰트 후보 모두 없음 — 기본 폰트 사용")
    return ImageFont.load_default()


def render_thumbnail(img_rgb: Image.Image, hook_text: str,
                     variant_style: dict[str, Any]) -> Image.Image:
    """배경 위에 그라디언트 + 자동 맞춤 후크 텍스트(아웃라인)를 그린다. 원본 `_render_thumbnail`과 동일."""
    img = img_rgb.copy()
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    grad_height = img.height // 3
    for y in range(grad_height):
        alpha = int(180 * (1 - y / grad_height))
        # variant B(하단)는 아래에서 위로 그라디언트
        real_y = img.height - 1 - y if variant_style["y_ratio"] > 0.5 else y
        od.rectangle([(0, real_y), (img.width, real_y + 1)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(img)
    target_w = int(img.width * 0.9)
    size = 180
    font = _find_font(size)
    while size > 60:
        bbox = draw.textbbox((0, 0), hook_text, font=font)
        if bbox[2] - bbox[0] <= target_w:
            break
        size -= 10
        font = _find_font(size)

    bbox = draw.textbbox((0, 0), hook_text, font=font)
    w = bbox[2] - bbox[0]
    x = (img.width - w) // 2
    y = int(img.height * variant_style["y_ratio"])

    outline_rgb = variant_style["outline_rgb"]
    for ox in range(-6, 7, 3):
        for oy in range(-6, 7, 3):
            draw.text((x + ox, y + oy), hook_text, font=font, fill=outline_rgb)
    draw.text((x, y), hook_text, font=font, fill=variant_style["fill"])
    return img


def build_thumbnail(image_prompt: str, hook_text: str, output_path: Path, *,
                    variant: str = "A", alt_hook_text: str | None = None,
                    gen_bg: BackgroundGenerator) -> Path | dict[str, Any]:
    """배경 이미지 생성 + 후크 텍스트 오버레이. 원본과 동일한 파일 규칙.

    variant="A"/"B": 단일 변형 → output_path 반환.
    variant="AB": A·B 둘 다 생성(같은 배경) → {"primary": A, "variants": [A, B]} 반환.
    alt_hook_text: variant B에서 다른 텍스트(예: 질문형).
    gen_bg: 배경 이미지 생성기 주입(운영은 image_gen 백엔드로 배선).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bg_path = output_path.with_suffix(".bg.png")
    gen_bg(image_prompt, bg_path)
    bg_rgb = Image.open(bg_path).convert("RGB")

    def _save(v: str, hook: str, dst: Path) -> Path:
        out_img = render_thumbnail(bg_rgb, hook, _VARIANT_STYLES[v])
        out_img.save(dst, "PNG", quality=95)
        log.info(f"썸네일 [{v}] 생성: {dst}")
        return dst

    if variant != "AB":
        path = _save(variant, hook_text, output_path)
        bg_path.unlink(missing_ok=True)
        return path

    # AB: 같은 배경으로 A + B 두 장 + 기본(uploader 우선 적용)
    stem = output_path.stem
    a_path = output_path.with_name(f"{stem}.A.png")
    b_path = output_path.with_name(f"{stem}.B.png")
    _save("A", hook_text, a_path)
    _save("B", alt_hook_text or hook_text, b_path)
    Image.open(a_path).save(output_path, "PNG", quality=95)
    bg_path.unlink(missing_ok=True)
    return {"primary": output_path, "variants": [a_path, b_path]}
