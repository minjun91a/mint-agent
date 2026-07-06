# -*- coding: utf-8 -*-
"""
adapters.youtube.tts_engine 테스트.

외부 TTS·파일 상태·ffmpeg가 얽혀 결정적 테스트가 불가능했던 모듈을, 세 주입 패턴으로 가른다:
순수(화자분할·명령 빌더) + 상태(VoiceLibrary 경로·rng 주입) + 백엔드/러너 주입.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
import yaml

from adapters.youtube._ffmpeg import RunResult
from adapters.youtube.tts_engine import (
    TTSEngine,
    VoiceLibrary,
    build_audio_concat_cmd,
    build_silence_cmd,
    split_by_speaker,
)

VOICES = {
    "provider": "elevenlabs",
    "voices": [
        {"id": "v1", "voice_id": "el1", "openai_voice": "alloy", "speed": 1.0},
        {"id": "v2", "voice_id": "el2", "openai_voice": "echo"},
        {"id": "v3", "voice_id": "el3"},
    ],
    "speakers": {
        "NARRATOR": {"pool": ["v1", "v2"], "speed": 1.1},
        "SYSTEM": {"voice_id": "v3", "openai_voice": "onyx"},
    },
}


def _make_lib(tmp_path, *, last=None, rng=None) -> VoiceLibrary:
    vp = tmp_path / "voices.yaml"
    vp.write_text(yaml.safe_dump(VOICES, allow_unicode=True), encoding="utf-8")
    lp = tmp_path / "last_voice.json"
    if last is not None:
        lp.write_text(json.dumps({"id": last}), encoding="utf-8")
    return VoiceLibrary(voices_path=vp, last_voice_path=lp, rng=(rng or random.Random(0)))


class _FakeBackend:
    """가짜 TTS 백엔드 — 더미 mp3 파일 생성·호출 기록·실패 흉내."""

    def __init__(self, name, *, fail=False, record=None):
        self.name = name
        self._fail = fail
        self._record = record if record is not None else []

    def synthesize(self, text, output_path, voice, model) -> Path:
        self._record.append((self.name, text, voice.get("id"), model))
        if self._fail:
            raise RuntimeError(f"{self.name} down")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"MP3")
        return Path(output_path)


class _FakeRunner:
    """가짜 ffmpeg 러너 — measure('-y' 없음)엔 Duration 반환, 그 외 성공."""

    def __init__(self, *, duration="Duration: 00:00:02.00, start: 0.0"):
        self.calls = []
        self._dur = duration

    def __call__(self, cmd, *, cwd=None) -> RunResult:
        self.calls.append(cmd)
        if "-y" not in cmd:
            return RunResult(0, self._dur)
        return RunResult(0, "")


# ── 순수: 화자 분할 ──

def test_split_no_tags_is_single_narrator():
    assert split_by_speaker("hello\nworld") == [("NARRATOR", "hello\nworld")]


def test_split_multiple_speakers():
    text = "[NARRATOR] once upon a time\n[SYSTEM] level up\n[DIALOGUE_F] who are you"
    assert split_by_speaker(text) == [
        ("NARRATOR", "once upon a time"),
        ("SYSTEM", "level up"),
        ("DIALOGUE_F", "who are you"),
    ]


def test_split_continuation_lines_attach_to_current_speaker():
    text = "[SYSTEM] line one\nline two\n[NARRATOR] back"
    assert split_by_speaker(text) == [
        ("SYSTEM", "line one\nline two"),
        ("NARRATOR", "back"),
    ]


def test_split_leading_untagged_is_narrator():
    text = "intro line\n[SYSTEM] sys"
    assert split_by_speaker(text) == [("NARRATOR", "intro line"), ("SYSTEM", "sys")]


# ── 순수: ffmpeg 명령 빌더 ──

def test_build_silence_cmd_golden():
    assert build_silence_cmd("ff", "sil.mp3", duration_sec=0.35) == [
        "ff", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", "0.35", "-c:a", "libmp3lame", "-b:a", "192k", "sil.mp3",
    ]


def test_build_audio_concat_cmd_golden():
    assert build_audio_concat_cmd("ff", "list.txt", "out.mp3") == [
        "ff", "-y", "-f", "concat", "-safe", "0",
        "-i", "list.txt", "-c:a", "libmp3lame", "-b:a", "192k", "out.mp3",
    ]


# ── 상태: VoiceLibrary ──

def test_pick_force_id_returns_that_voice(tmp_path):
    assert _make_lib(tmp_path).pick(force_id="v2")["id"] == "v2"


def test_pick_unknown_force_id_raises(tmp_path):
    with pytest.raises(ValueError):
        _make_lib(tmp_path).pick(force_id="nope")


def test_pick_excludes_last_used(tmp_path):
    lib = _make_lib(tmp_path, last="v1", rng=random.Random(0))
    for _ in range(10):
        assert lib.pick()["id"] != "v1"     # 직전 보이스 제외


def test_save_and_last_used_roundtrip(tmp_path):
    lib = _make_lib(tmp_path)
    assert lib.last_used() is None
    lib.save_last("v3")
    assert lib.last_used() == "v3"


def test_resolve_narrator_rotates_excluding_last(tmp_path):
    lib = _make_lib(tmp_path, last="v1")
    voice = lib.resolve_speaker("NARRATOR", VOICES)
    assert voice["id"] == "v2"              # pool [v1,v2] - last v1 → v2
    assert voice["speed"] == 1.1            # spec speed


def test_resolve_fixed_speaker_with_voice_id_and_openai(tmp_path):
    voice = _make_lib(tmp_path).resolve_speaker("SYSTEM", VOICES)
    assert voice["id"] == "v3"
    assert voice["openai_voice"] == "onyx"  # spec가 덮어씀


def test_resolve_unmapped_speaker_raises(tmp_path):
    with pytest.raises(ValueError):
        _make_lib(tmp_path).resolve_speaker("DIALOGUE_M", VOICES)  # speakers에 없음


# ── 오케스트레이션: 단일 합성 + 폴백 ──

def test_synthesize_uses_provider_and_saves_last(tmp_path):
    rec = []
    engine = TTSEngine(library=_make_lib(tmp_path),
                       backends={"elevenlabs": _FakeBackend("elevenlabs", record=rec)},
                       provider="elevenlabs")
    voice = {"id": "v1", "voice_id": "el1", "openai_voice": "alloy"}
    out = engine.synthesize("hi", tmp_path / "a.mp3", voice=voice)
    assert out.read_bytes() == b"MP3"
    assert rec[0][0] == "elevenlabs" and rec[0][3] == "eleven_multilingual_v2"
    assert engine.library.last_used() == "v1"


def test_synthesize_falls_back_to_openai_when_elevenlabs_fails(tmp_path):
    rec = []
    engine = TTSEngine(
        library=_make_lib(tmp_path),
        backends={"elevenlabs": _FakeBackend("elevenlabs", fail=True, record=rec),
                  "openai": _FakeBackend("openai", record=rec)},
        provider="elevenlabs")
    voice = {"id": "v1", "voice_id": "el1", "openai_voice": "alloy"}
    engine.synthesize("hi", tmp_path / "a.mp3", voice=voice)
    assert [r[0] for r in rec] == ["elevenlabs", "openai"]   # EL 시도 → OpenAI 폴백
    assert rec[1][3] == "tts-1-hd"                            # 폴백 모델


def test_synthesize_raises_when_no_openai_voice_for_fallback(tmp_path):
    engine = TTSEngine(
        library=_make_lib(tmp_path),
        backends={"elevenlabs": _FakeBackend("elevenlabs", fail=True)},
        provider="elevenlabs")
    voice = {"id": "v3", "voice_id": "el3"}                   # openai_voice 없음
    with pytest.raises(RuntimeError):
        engine.synthesize("hi", tmp_path / "a.mp3", voice=voice)


# ── 오케스트레이션: 멀티보이스 ──

def test_synthesize_multi_single_speaker_returns_one_timeline(tmp_path):
    engine = TTSEngine(library=_make_lib(tmp_path, rng=random.Random(0)),
                       backends={"elevenlabs": _FakeBackend("elevenlabs")},
                       provider="elevenlabs", run=_FakeRunner(), ffmpeg="ff")
    path, timeline = engine.synthesize_multi("plain narration", tmp_path / "out.mp3",
                                             return_timeline=True)
    assert path == tmp_path / "out.mp3"
    assert len(timeline) == 1
    assert timeline[0]["speaker"] == "NARRATOR"
    assert timeline[0]["end_sec"] == pytest.approx(2.0)      # measure = 2.0s


def test_synthesize_multi_multiple_speakers_chunks_and_concats(tmp_path):
    rec = []
    runner = _FakeRunner()
    engine = TTSEngine(
        library=_make_lib(tmp_path, rng=random.Random(0)),
        backends={"elevenlabs": _FakeBackend("elevenlabs", record=rec),
                  "openai": _FakeBackend("openai", record=rec)},
        provider="elevenlabs", pause_between=0.35, run=runner, ffmpeg="ff")
    text = "[NARRATOR] once\n[SYSTEM] level up"
    path, timeline = engine.synthesize_multi(text, tmp_path / "out.mp3", return_timeline=True)

    assert path == tmp_path / "out.mp3"
    assert len(timeline) == 2                                # 화자 2 → 청크 2
    assert timeline[0]["start_sec"] == 0.0
    assert timeline[1]["start_sec"] == pytest.approx(2.0 + 0.35)  # 앞 청크 2s + 무음 0.35s
    assert sum(1 for c in runner.calls if "anullsrc=r=44100:cl=mono" in c) == 1  # 무음 1회
    assert any("concat" in c for c in runner.calls)          # 최종 concat
    assert sum(1 for r in rec if r[0] in ("elevenlabs", "openai")) == 2  # 청크 2회 합성
