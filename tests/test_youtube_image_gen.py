# -*- coding: utf-8 -*-
"""
adapters.youtube.image_gen 테스트.

원본은 FLUX 호출·DALL-E 폴백·검열 재시도·rate-limit이 엉켜 결정적 테스트가 불가능했다.
순수(프롬프트 안전화/후미/재시도열/판정) + 백엔드 주입(FLUX/DALL-E 저수준 호출 fake)으로 가른다.
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest

from adapters.youtube.image_gen import (
    GENERIC_FALLBACK_PROMPT,
    DalleBackend,
    FluxBackend,
    ImageBackend,
    _default_flux_download,
    _flux_inputs,
    backends_from_config,
    dalle_attempt_prompts,
    enrich_prompt,
    generate_scenes,
    generate_with_fallback,
    is_content_policy_block,
    sanitize_prompt,
)


# ── 순수 로직 ──

def test_sanitize_prompt_replaces_danger_words():
    out = sanitize_prompt("an epic battle with blood and a weapon")
    assert "battle" not in out and "blood" not in out and "weapon" not in out
    assert "encounter" in out and "magic" in out and "magical artifact" in out


def test_sanitize_prompt_title_case():
    assert sanitize_prompt("Battle of Death") == "Encounter of Ending"


def test_enrich_appends_suffix_once():
    assert enrich_prompt("a hero, ", safety_suffix="X") == "a hero, X"
    assert enrich_prompt("a hero, X", safety_suffix="X") == "a hero, X"  # 이미 포함 → 생략


def test_enrich_no_suffix_returns_trimmed():
    assert enrich_prompt("a hero, ", safety_suffix="") == "a hero"


def test_dalle_attempt_prompts_order():
    aps = dalle_attempt_prompts("epic battle")
    assert len(aps) == 3
    assert aps[0] == "epic battle"
    assert aps[1] == sanitize_prompt("epic battle")
    assert aps[2] == GENERIC_FALLBACK_PROMPT


@pytest.mark.parametrize("msg,exp", [
    ("content_policy violation", True), ("safety system", True), ("Error 400", True),
    ("timeout", False), ("500 server error", False),
])
def test_is_content_policy_block(msg, exp):
    assert is_content_policy_block(Exception(msg)) is exp


def test_flux_inputs_defaults_and_go_fast():
    inp = _flux_inputs({"model": "black-forest-labs/flux-schnell", "go_fast": True}, "hero")
    assert inp["prompt"] == "hero"
    assert inp["aspect_ratio"] == "9:16"
    assert inp["num_inference_steps"] == 4
    assert inp["go_fast"] is True


def test_flux_inputs_no_go_fast_when_not_schnell():
    assert "go_fast" not in _flux_inputs({"model": "flux-dev", "go_fast": True}, "hero")


def test_flux_inputs_no_go_fast_when_flag_off():
    assert "go_fast" not in _flux_inputs({"model": "flux-schnell", "go_fast": False}, "hero")


def test_flux_download_fileoutput_object(tmp_path):
    class _FO:
        def read(self):
            return b"IMG"

    out = _default_flux_download(_FO(), tmp_path / "a.png")
    assert out.read_bytes() == b"IMG"


def test_flux_download_url(tmp_path, monkeypatch):
    seen = {}

    def fake_urlretrieve(url, path):
        seen["url"] = url
        Path(path).write_bytes(b"X")

    monkeypatch.setattr(urllib.request, "urlretrieve", fake_urlretrieve)
    out = _default_flux_download("http://img/x.png", tmp_path / "b.png")
    assert seen["url"] == "http://img/x.png"
    assert out.read_bytes() == b"X"


def test_flux_download_unknown_type_raises(tmp_path):
    with pytest.raises(RuntimeError):
        _default_flux_download(12345, tmp_path / "c.png")


# ── 백엔드 주입: 포트 + 폴백 ──

class _FakeBackend:
    """ImageBackend 계약을 만족하는 가짜 백엔드 — 호출 기록·성공/실패 흉내."""

    def __init__(self, *, name="fake", marker="ok", raises=None, record=None):
        self.name = name
        self._marker = marker
        self._raises = raises
        self._record = record if record is not None else []

    def generate(self, prompt: str, output_path: Path) -> Path:
        self._record.append((self.name, prompt, output_path))
        if self._raises is not None:
            raise self._raises
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(self._marker, encoding="utf-8")
        return output_path


def test_fake_backend_satisfies_protocol():
    assert isinstance(_FakeBackend(), ImageBackend)


def test_generate_with_fallback_uses_primary_and_enriches(tmp_path):
    rec = []
    primary = _FakeBackend(name="p", record=rec)
    out = generate_with_fallback("a hero, ", tmp_path / "x.png",
                                 primary=primary, safety_suffix="SAFE")
    assert out.read_text() == "ok"
    assert rec == [("p", "a hero, SAFE", tmp_path / "x.png")]  # 안전 후미 첨가된 프롬프트


def test_generate_falls_back_to_secondary(tmp_path):
    rec = []
    primary = _FakeBackend(name="p", raises=RuntimeError("boom"), record=rec)
    fallback = _FakeBackend(name="f", marker="fb", record=rec)
    out = generate_with_fallback("hero", tmp_path / "x.png",
                                 primary=primary, fallback=fallback)
    assert out.read_text() == "fb"
    assert [r[0] for r in rec] == ["p", "f"]
    assert rec[0][1] == rec[1][1] == "hero"   # 둘 다 같은 (후미 없는) 프롬프트


def test_generate_raises_when_both_fail(tmp_path):
    primary = _FakeBackend(name="p", raises=RuntimeError("b1"))
    fallback = _FakeBackend(name="f", raises=RuntimeError("b2"))
    with pytest.raises(RuntimeError):
        generate_with_fallback("hero", tmp_path / "x.png",
                               primary=primary, fallback=fallback)


def test_generate_raises_when_no_fallback(tmp_path):
    primary = _FakeBackend(name="p", raises=RuntimeError("b"))
    with pytest.raises(RuntimeError):
        generate_with_fallback("hero", tmp_path / "x.png", primary=primary)


def test_generate_scenes_writes_ordered_paths(tmp_path):
    rec = []
    primary = _FakeBackend(name="p", record=rec)
    scenes = [{"order": 1, "image_prompt": "s1"}, {"order": 2, "image_prompt": "s2"}]
    paths = generate_scenes(scenes, tmp_path / "scenes",
                            primary=primary, safety_suffix="SS")
    assert [p.name for p in paths] == ["scene_01.png", "scene_02.png"]
    assert all(p.exists() for p in paths)
    assert [r[1] for r in rec] == ["s1, SS", "s2, SS"]


# ── FluxBackend: run/download 주입 ──

def test_flux_backend_list_output_uses_first(tmp_path):
    seen = {}

    def run(model, inputs):
        seen["model"], seen["inputs"] = model, inputs
        return ["http://img/1.png"]

    def download(src, path):
        seen["src"] = src
        Path(path).write_text("f")
        return Path(path)

    b = FluxBackend({"model": "m-schnell", "go_fast": True},
                    run=run, download=download, now=lambda: 1e9, sleep=lambda s: None)
    out = b.generate("hero", tmp_path / "x.png")
    assert seen["src"] == "http://img/1.png"      # list[0]
    assert seen["model"] == "m-schnell"
    assert seen["inputs"]["go_fast"] is True
    assert out.read_text() == "f"


def test_flux_backend_scalar_output(tmp_path):
    seen = {}

    def run(m, i):
        return "http://img/single.png"

    def download(src, path):
        seen["src"] = src
        Path(path).write_text("f")
        return Path(path)

    FluxBackend({"model": "m"}, run=run, download=download,
                now=lambda: 1e9, sleep=lambda s: None).generate("hero", tmp_path / "y.png")
    assert seen["src"] == "http://img/single.png"


# ── DalleBackend: image_url/download 주입 (검열 재시도) ──

def test_dalle_retries_on_policy_then_succeeds(tmp_path):
    calls = []
    seen = {}

    def image_url(cfg, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            raise Exception("content_policy blocked")   # 1차 검열 차단
        return "http://img/ok.png"

    def download(url, path):
        seen["url"] = url
        Path(path).write_text("d")
        return Path(path)

    out = DalleBackend({"model": "dall-e-3"}, image_url=image_url, download=download) \
        .generate("epic battle", tmp_path / "d.png")
    assert calls[0] == "epic battle"
    assert calls[1] == sanitize_prompt("epic battle")   # 2차 = 안전화 프롬프트
    assert seen["url"] == "http://img/ok.png"
    assert out.read_text() == "d"


def test_dalle_non_policy_error_raises_immediately(tmp_path):
    def image_url(cfg, prompt):
        raise ValueError("network down")   # 검열 아님 → 즉시 전파

    with pytest.raises(ValueError):
        DalleBackend({}, image_url=image_url, download=lambda u, p: None) \
            .generate("hero", tmp_path / "d.png")


def test_dalle_all_blocked_raises_runtime(tmp_path):
    def image_url(cfg, prompt):
        raise Exception("safety 400")

    with pytest.raises(RuntimeError):
        DalleBackend({}, image_url=image_url, download=lambda u, p: None) \
            .generate("hero", tmp_path / "d.png")


# ── config 팩토리 ──

def test_backends_from_config_flux_has_dalle_fallback():
    primary, fallback = backends_from_config({"provider": "flux", "flux": {}, "dalle": {}})
    assert primary.name == "flux" and fallback.name == "dalle"


def test_backends_from_config_dalle_has_no_fallback():
    primary, fallback = backends_from_config({"provider": "dalle", "dalle": {}})
    assert primary.name == "dalle" and fallback is None
