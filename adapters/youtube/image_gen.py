# -*- coding: utf-8 -*-
"""
adapters.youtube.image_gen — 장면 이미지 생성 (produce 자산화, 외부 이미지 API).

`youtube-agent/agents/image_gen.py`를 모노레포로 이식. 원본은 FLUX(Replicate) 호출·DALL-E 폴백·
검열 재시도·rate-limit이 한 덩어리라 결정적 테스트가 불가능했다. publish 때 확립한 **백엔드 주입**
패턴으로 경계를 가른다:

  1. **순수 로직** — 프롬프트 안전화(`sanitize_prompt`)·안전 후미(`enrich_prompt`)·DALL-E 재시도
     프롬프트열(`dalle_attempt_prompts`)·검열 판정(`is_content_policy_block`)·FLUX 입력(`_flux_inputs`)·
     FLUX 출력 처리(`_default_flux_download`). 전부 결정적 → 특성화 + 원본 parity.
  2. **백엔드 경계** — `ImageBackend` 포트 구현체(`FluxBackend`/`DalleBackend`)에 저수준 호출을 주입.
     엔진/오케스트레이션은 포트만 알고, 실제 API/다운로드는 주입된 콜러블이 담당(테스트는 fake).
"""
from __future__ import annotations

import logging
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

log = logging.getLogger("mint.adapters.youtube.image_gen")

# 검열/폴백 마지막 수단 — 캐릭터 없는 안전 풍경 (원본 동일 문구)
GENERIC_FALLBACK_PROMPT = (
    "ethereal korean fantasy landscape, magical glowing tower, dreamy atmosphere, "
    "painterly, cinematic, 9:16 vertical, no characters"
)


# ── 순수 로직 ──────────────────────────────────────────────────────────────

DANGER_WORDS = [
    "battling", "battle", "fighting", "fight", "violence", "violent",
    "blood", "bloody", "kill", "killing", "dead", "death", "corpse",
    "weapon", "sword fight", "tense", "dark", "monster", "demon",
    "evil", "scary", "horror", "intense",
]

# 위험 단어 → 안전 표현 치환표 (원본 `_sanitize_prompt`와 동일)
_SANITIZE_REPL = {
    "battling monsters": "facing mystical creatures",
    "battle": "encounter",
    "fighting": "confronting",
    "violence": "tension",
    "blood": "magic",
    "bloody": "vivid",
    "kill": "defeat",
    "dead": "fallen",
    "death": "ending",
    "weapon": "magical artifact",
    "tense": "dramatic",
    "dark": "mysterious",
    "monster": "creature",
    "demon": "spirit",
    "evil": "shadowy",
    "scary": "mysterious",
    "intense": "dramatic",
}


def sanitize_prompt(prompt: str) -> str:
    """위험 단어를 안전 표현으로 치환(원형 + Title-case). 원본 로직과 동일."""
    safe = prompt
    for k, v in _SANITIZE_REPL.items():
        safe = safe.replace(k, v).replace(k.title(), v.title())
    return safe


def enrich_prompt(prompt: str, *, safety_suffix: str = "") -> str:
    """안전 후미를 프롬프트 끝에 1회 첨가(이미 포함이면 생략). 원본 `_enrich`와 동일."""
    base = prompt.rstrip(", ").rstrip()
    if safety_suffix and safety_suffix not in base:
        base = f"{base}, {safety_suffix}"
    return base


def dalle_attempt_prompts(prompt: str) -> list[str]:
    """DALL-E 3단 재시도 프롬프트열: [원본, 안전화, 일반 폴백]. 원본 attempts와 동일."""
    return [prompt, sanitize_prompt(prompt), GENERIC_FALLBACK_PROMPT]


def is_content_policy_block(exc: Exception) -> bool:
    """검열/정책 차단성 예외인지(다음 폴백으로 넘어갈지) 판정. 원본 조건과 동일."""
    s = str(exc)
    return "content_policy" in s or "safety" in s or "400" in s


def _flux_inputs(flux_cfg: dict[str, Any], prompt: str) -> dict[str, Any]:
    """FLUX(Replicate) 입력 dict 구성. 원본 inputs 구성과 동일."""
    inputs: dict[str, Any] = {
        "prompt": prompt,
        "aspect_ratio": flux_cfg.get("aspect_ratio", "9:16"),
        "num_inference_steps": flux_cfg.get("num_inference_steps", 4),
        "output_format": flux_cfg.get("output_format", "png"),
        "output_quality": flux_cfg.get("output_quality", 90),
    }
    if flux_cfg.get("go_fast") and "schnell" in flux_cfg.get("model", ""):
        inputs["go_fast"] = True
    return inputs


def _default_flux_download(url_or_file: Any, output_path: Path) -> Path:
    """FLUX 출력(FileOutput 객체 / URL 문자열)을 파일로. 원본 출력 처리와 동일."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(url_or_file, "read"):
        with open(output_path, "wb") as f:
            f.write(url_or_file.read())
    elif isinstance(url_or_file, str) and url_or_file.startswith("http"):
        urllib.request.urlretrieve(url_or_file, output_path)
    else:
        raise RuntimeError(f"FLUX 출력 타입 미지원: {type(url_or_file)} {url_or_file!r}")
    return output_path


# ── 백엔드 포트 ────────────────────────────────────────────────────────────

@runtime_checkable
class ImageBackend(Protocol):
    """이미지 생성 백엔드 계약. 오케스트레이션은 이 포트만 알고 구현체는 교체 가능."""

    name: str

    def generate(self, prompt: str, output_path: Path) -> Path: ...


# (model, inputs) -> replicate 출력 / (cfg, prompt) -> image url / (src, path) -> None
FluxRun = Callable[[str, dict], Any]
FluxDownload = Callable[[Any, Path], Path]
DalleImageURL = Callable[[dict, str], str]
Download = Callable[[str, Path], Any]


def _default_flux_run(model: str, inputs: dict) -> Any:
    """실 Replicate 호출 — replicate 지연 import."""
    import os

    import replicate

    token = os.getenv("REPLICATE_API_TOKEN")
    if not token:
        raise RuntimeError(
            "REPLICATE_API_TOKEN 미설정 — https://replicate.com/account/api-tokens 발급 후 .env 추가")
    client = replicate.Client(api_token=token)
    return client.run(model, input=inputs)


class FluxBackend:
    """FLUX(Replicate) 이미지 백엔드. 저수준 호출(`run`)·다운로드·시계는 주입 가능(테스트 fake).

    rate-limit 가드(마지막 호출로부터 `min_interval`초 미만이면 대기)를 인스턴스 상태로 보유한다
    (원본은 모듈 전역이었음 → 주입 가능한 clock/sleep으로 결정적화).
    """

    name = "flux"

    def __init__(self, flux_cfg: dict[str, Any], *,
                 run: FluxRun | None = None, download: FluxDownload | None = None,
                 now: Callable[[], float] | None = None,
                 sleep: Callable[[float], None] | None = None,
                 min_interval: float = 11.0) -> None:
        self._cfg = flux_cfg
        self._run = run or _default_flux_run
        self._download = download or _default_flux_download
        self._now = now or time.time
        self._sleep = sleep or time.sleep
        self._min_interval = min_interval
        self._last_call = 0.0

    def generate(self, prompt: str, output_path: Path) -> Path:
        elapsed = self._now() - self._last_call
        if elapsed < self._min_interval:
            self._sleep(self._min_interval - elapsed)
        self._last_call = self._now()

        out = self._run(self._cfg.get("model", ""), _flux_inputs(self._cfg, prompt))
        url_or_file = out[0] if isinstance(out, list) else out
        return self._download(url_or_file, output_path)


def _default_dalle_image_url(dalle_cfg: dict, prompt: str) -> str:
    """실 DALL-E 호출 → 이미지 URL. openai 지연 import."""
    import os

    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = client.images.generate(
        model=dalle_cfg.get("model", "dall-e-3"),
        prompt=prompt,
        size=dalle_cfg.get("size", "1024x1792"),
        quality=dalle_cfg.get("quality", "standard"),
        n=1,
    )
    return resp.data[0].url


class DalleBackend:
    """DALL-E 3 이미지 백엔드. 검열 차단 시 [원본→안전화→일반] 3단 재시도. 호출·다운로드 주입 가능."""

    name = "dalle"

    def __init__(self, dalle_cfg: dict[str, Any], *,
                 image_url: DalleImageURL | None = None,
                 download: Download | None = None) -> None:
        self._cfg = dalle_cfg
        self._image_url = image_url or _default_dalle_image_url
        self._download = download or urllib.request.urlretrieve

    def generate(self, prompt: str, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        last_exc: Exception | None = None
        for i, p in enumerate(dalle_attempt_prompts(prompt)):
            try:
                log.info(f"DALL-E 생성 시도 {i + 1}/3: '{p[:80]}...'")
                url = self._image_url(self._cfg, p)
                self._download(url, output_path)
                return output_path
            except Exception as exc:
                last_exc = exc
                if is_content_policy_block(exc):
                    log.warning("  검열 차단 — 다음 fallback")
                    continue
                raise
        raise RuntimeError(f"DALL-E 실패: {last_exc}")


# ── 오케스트레이션 ─────────────────────────────────────────────────────────

def generate_with_fallback(prompt: str, output_path: Path, *,
                           primary: ImageBackend, fallback: ImageBackend | None = None,
                           safety_suffix: str = "") -> Path:
    """안전 후미 첨가 → primary 생성, 실패 시 fallback. 원본 `generate()` 분기와 동일 의미."""
    enriched = enrich_prompt(prompt, safety_suffix=safety_suffix)
    try:
        return primary.generate(enriched, output_path)
    except Exception as exc:
        if fallback is not None:
            log.warning(f"{primary.name} 실패 ({exc}) — {fallback.name} fallback")
            return fallback.generate(enriched, output_path)
        raise


def generate_scenes(scenes: list[dict], out_dir: Path, *,
                    primary: ImageBackend, fallback: ImageBackend | None = None,
                    safety_suffix: str = "") -> list[Path]:
    """장면 리스트 → scene_NN.png 경로 리스트. 원본 `generate_scenes`와 동일한 파일 규칙."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for sc in scenes:
        order = sc.get("order", len(paths) + 1)
        path = out_dir / f"scene_{order:02d}.png"
        generate_with_fallback(sc["image_prompt"], path,
                               primary=primary, fallback=fallback, safety_suffix=safety_suffix)
        paths.append(path)
    return paths


def backends_from_config(cfg: dict[str, Any]) -> tuple[ImageBackend, ImageBackend | None]:
    """image_models 설정 → (primary, fallback). provider=flux면 (FLUX, DALL-E), 아니면 (DALL-E, None).

    원본 동작: flux는 실패 시 dalle 폴백, dalle 단독은 폴백 없음(그대로 예외).
    """
    provider = str(cfg.get("provider", "flux")).lower()
    flux = FluxBackend(cfg.get("flux", {}))
    dalle = DalleBackend(cfg.get("dalle", {}))
    if provider == "flux":
        return flux, dalle
    return dalle, None
