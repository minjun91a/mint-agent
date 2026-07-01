# -*- coding: utf-8 -*-
"""
adapters.youtube.tts_engine — 음성 합성 (produce 자산화, 다중 프로바이더 TTS + 멀티보이스).

`youtube-agent/agents/tts_engine.py`를 모노레포로 이식. 외부 TTS(ElevenLabs/OpenAI)·파일 상태
(보이스 회전)·ffmpeg(무음·concat)가 얽혀 결정적 테스트가 불가능했다 → 지금까지 확립한 세 주입
패턴을 모두 적용해 가른다:

  1. **순수** — 화자 태그 분할(`split_by_speaker`)·ffmpeg 명령 빌더(`build_silence_cmd`/
     `build_audio_concat_cmd`). 결정적 → 특성화 + 원본 parity(split).
  2. **상태(경로·rng 주입)** — `VoiceLibrary`: 보이스 설정 로딩·직전 보이스 회전·화자 매핑
     (`core/memory`·`story_writer.TropeLibrary`와 같은 패턴).
  3. **외부 API(백엔드 주입)** — `SynthBackend`(ElevenLabs/OpenAI). 프로바이더 dispatch + EL 실패
     시 OpenAI 폴백. ffmpeg 실행은 공유 러너(`_ffmpeg`) 주입.
"""
from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from adapters.youtube._ffmpeg import CommandRunner, default_ffmpeg, default_run, parse_duration

log = logging.getLogger("mint.adapters.youtube.tts_engine")

# 프로바이더별 기본 모델 (cfg가 model을 안 주면 사용). 원본과 동일.
_DEFAULT_MODEL = {"elevenlabs": "eleven_multilingual_v2", "openai": "tts-1-hd"}


# ── 순수: 화자 분할 ────────────────────────────────────────────────────────

SPEAKER_TAG_RE = re.compile(
    r"^\s*\[(NARRATOR|SYSTEM|DIALOGUE_F|DIALOGUE_M)\]\s*(.*)$",
    re.IGNORECASE,
)


def split_by_speaker(text: str) -> list[tuple[str, str]]:
    """텍스트를 [SPEAKER] 태그 기준 (speaker, content) 리스트로 분할. 태그 없는 줄은 직전 화자에
    이어붙이고, 첫 줄에 태그 없으면 NARRATOR. 원본 `_split_by_speaker`와 동일(순수)."""
    segments: list[tuple[str, str]] = []
    current_speaker = "NARRATOR"
    current_text: list[str] = []

    for raw in text.splitlines():
        m = SPEAKER_TAG_RE.match(raw)
        if m:
            if current_text:
                joined = "\n".join(current_text).strip()
                if joined:
                    segments.append((current_speaker, joined))
            current_speaker = m.group(1).upper()
            rest = m.group(2).strip()
            current_text = [rest] if rest else []
        else:
            current_text.append(raw)

    if current_text:
        joined = "\n".join(current_text).strip()
        if joined:
            segments.append((current_speaker, joined))
    return segments


# ── 순수: ffmpeg 명령 빌더 (오디오) ────────────────────────────────────────

def build_silence_cmd(ffmpeg: str, out: str | Path, *, duration_sec: float) -> list[str]:
    """무음 mp3 생성 명령. 원본 `_silence_mp3`와 동일."""
    return [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", f"{duration_sec:.2f}",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(out),
    ]


def build_audio_concat_cmd(ffmpeg: str, list_file: str | Path, out: str | Path) -> list[str]:
    """오디오 청크 concat 명령(mp3 재인코딩). 원본 멀티보이스 concat과 동일."""
    return [
        ffmpeg, "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(out),
    ]


# ── 상태: 보이스 설정·회전 (경로·rng 주입) ─────────────────────────────────

class VoiceLibrary:
    """보이스 설정 로딩 + 직전 보이스 회전 + 화자 매핑. 위치·무작위성을 주입해 결정적으로 만든다.

    원본은 모듈 상수 경로(`data/voices.yaml`·`data/last_voice.json`)와 `random`에 직접 의존했다.
    """

    def __init__(self, *, voices_path: str | Path, last_voice_path: str | Path,
                 rng: random.Random | None = None) -> None:
        self.voices_path = Path(voices_path)
        self.last_voice_path = Path(last_voice_path)
        self._rng = rng or random

    def load(self) -> dict[str, Any]:
        return yaml.safe_load(self.voices_path.read_text(encoding="utf-8"))

    def last_used(self) -> str | None:
        if not self.last_voice_path.exists():
            return None
        try:
            return json.loads(self.last_voice_path.read_text(encoding="utf-8")).get("id")
        except Exception:
            return None

    def save_last(self, voice_id: str) -> None:
        self.last_voice_path.parent.mkdir(parents=True, exist_ok=True)
        self.last_voice_path.write_text(
            json.dumps({"id": voice_id}, ensure_ascii=False), encoding="utf-8")

    def pick(self, force_id: str | None = None) -> dict[str, Any]:
        """직전 보이스를 제외한 무작위 선택(force_id 지정 시 고정). 원본 `pick_voice`와 동일."""
        voices = self.load()["voices"]
        if force_id:
            for v in voices:
                if v["id"] == force_id:
                    return v
            raise ValueError(f"voice id 없음: {force_id}")
        last = self.last_used()
        candidates = [v for v in voices if v["id"] != last] or voices
        return self._rng.choice(candidates)

    def resolve_speaker(self, speaker: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        """화자명 → voice dict. NARRATOR는 풀에서 회전, 그 외는 고정. 원본 `_resolve_speaker`와 동일."""
        cfg = cfg if cfg is not None else self.load()
        speaker = speaker.upper()
        spec = cfg.get("speakers", {}).get(speaker, {})

        if speaker == "NARRATOR":
            pool_ids = spec.get("pool") or [v["id"] for v in cfg["voices"]]
            last = self.last_used()
            candidates = [vid for vid in pool_ids if vid != last] or pool_ids
            chosen_id = self._rng.choice(candidates)
            for v in cfg["voices"]:
                if v["id"] == chosen_id:
                    voice = dict(v)
                    voice["speed"] = spec.get("speed", voice.get("speed", 1.0))
                    return voice
            raise ValueError(f"NARRATOR pool에 없는 voice id: {chosen_id}")

        voice_id = spec.get("voice_id")
        if voice_id:
            for v in cfg["voices"]:
                if v["id"] == voice_id:
                    voice = dict(v)
                    voice["speed"] = spec.get("speed", voice.get("speed", 1.0))
                    if spec.get("openai_voice"):
                        voice["openai_voice"] = spec["openai_voice"]
                    return voice
        if spec.get("openai_voice"):
            return {
                "id": f"INLINE_{speaker}",
                "openai_voice": spec["openai_voice"],
                "speed": spec.get("speed", 1.0),
            }
        raise ValueError(f"화자 매핑 없음: {speaker}")


# ── 외부 API: 합성 백엔드 (주입) ───────────────────────────────────────────

@runtime_checkable
class SynthBackend(Protocol):
    """TTS 합성 백엔드 계약. 오케스트레이션은 이 포트만 알고 구현체는 교체 가능."""

    name: str

    def synthesize(self, text: str, output_path: Path, voice: dict, model: str) -> Path: ...


class ElevenLabsBackend:
    """ElevenLabs TTS. SDK는 호출 시점 지연 import."""

    name = "elevenlabs"

    def synthesize(self, text: str, output_path: Path, voice: dict, model: str) -> Path:
        import os

        from elevenlabs.client import ElevenLabs

        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY 미설정")
        client = ElevenLabs(api_key=api_key)
        audio_iter = client.text_to_speech.convert(
            voice_id=voice["voice_id"], text=text, model_id=model,
            voice_settings={
                "stability": voice.get("stability", 0.5),
                "similarity_boost": voice.get("similarity_boost", 0.75),
                "style": voice.get("style", 0.4),
                "use_speaker_boost": True,
            },
            output_format="mp3_44100_128",
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            for chunk in audio_iter:
                if chunk:
                    f.write(chunk)
        return output_path


class OpenAIBackend:
    """OpenAI TTS(폴백). SDK는 호출 시점 지연 import."""

    name = "openai"

    def synthesize(self, text: str, output_path: Path, voice: dict, model: str) -> Path:
        import os

        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY 미설정")
        speed = float(voice.get("speed", 1.0))
        client = OpenAI(api_key=api_key)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with client.audio.speech.with_streaming_response.create(
            model=model, voice=voice["openai_voice"], input=text, speed=speed,
        ) as resp:
            resp.stream_to_file(str(output_path))
        return output_path


def default_backends() -> dict[str, SynthBackend]:
    """운영 기본 백엔드 묶음."""
    return {"elevenlabs": ElevenLabsBackend(), "openai": OpenAIBackend()}


# ── 오케스트레이션 ─────────────────────────────────────────────────────────

class TTSEngine:
    """보이스 라이브러리 + 백엔드 + ffmpeg 러너를 묶는 합성 엔진(주입식).

    provider/model은 설정(voices.yaml)에서 읽어 호출측이 주입한다. 단일/멀티보이스 합성을 제공.
    """

    def __init__(self, *, library: VoiceLibrary, backends: dict[str, SynthBackend],
                 provider: str = "elevenlabs", model: str | None = None,
                 pause_between: float = 0.35,
                 run: CommandRunner | None = None, ffmpeg: str | None = None) -> None:
        self.library = library
        self.backends = backends
        self.provider = provider
        self._model = model
        self.pause_between = pause_between
        self._run = run or default_run
        self._ffmpeg = ffmpeg

    @property
    def model(self) -> str:
        return self._model or _DEFAULT_MODEL.get(self.provider, "tts-1-hd")

    def _ffmpeg_path(self) -> str:
        if self._ffmpeg is None:
            self._ffmpeg = default_ffmpeg()
        return self._ffmpeg

    def measure_seconds(self, path: str | Path) -> float:
        """오디오 길이(초) — ffmpeg -i stderr 파싱."""
        return parse_duration(self._run([self._ffmpeg_path(), "-i", str(path)]).stderr)

    def synthesize(self, text: str, output_path: Path, voice: dict | None = None) -> Path:
        """단일 보이스 합성. voice 미지정 시 라이브러리에서 회전 선택. EL 실패 시 OpenAI 폴백.

        원본 `synthesize` 동작 보존: 프로바이더 dispatch → 실패 시(EL이고 openai_voice 있으면)
        OpenAI 폴백 → 성공한 보이스를 last로 저장.
        """
        if voice is None:
            voice = self.library.pick()
        try:
            path = self.backends[self.provider].synthesize(text, output_path, voice, self.model)
        except Exception as exc:
            if self.provider == "elevenlabs" and voice.get("openai_voice") and "openai" in self.backends:
                log.warning(f"ElevenLabs 실패 ({exc}) — OpenAI TTS로 fallback")
                path = self.backends["openai"].synthesize(text, output_path, voice, "tts-1-hd")
            else:
                raise
        self.library.save_last(voice["id"])
        return path

    def synthesize_multi(self, text: str, output_path: Path, *,
                         return_timeline: bool = False):
        """멀티보이스 합성. [SPEAKER] 태그가 여러 화자면 청크별 합성 후 ffmpeg concat.

        태그가 없거나 단일 화자면 `synthesize()`와 동일. return_timeline이면 (path, timeline)
        (timeline: start_sec/end_sec/speaker/text; 무음 pause는 미포함). 원본과 동일.
        """
        segments = split_by_speaker(text)
        has_multi = len({sp for sp, _ in segments}) > 1

        if not has_multi:
            path = self.synthesize(text, output_path)
            if not return_timeline:
                return path
            dur = self.measure_seconds(path)
            speaker = segments[0][0] if segments else "NARRATOR"
            body = segments[0][1] if segments else text
            return path, [{"start_sec": 0.0, "end_sec": dur, "speaker": speaker, "text": body}]

        cfg = self.library.load()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        work_dir = output_path.parent / f".tts_{output_path.stem}"
        work_dir.mkdir(exist_ok=True)

        chunk_paths: list[Path] = []
        timeline: list[dict] = []
        cursor = 0.0
        last_narrator_voice: str | None = None
        for i, (speaker, content) in enumerate(segments):
            voice = self.library.resolve_speaker(speaker, cfg)
            chunk_path = work_dir / f"chunk_{i:02d}.mp3"
            self.synthesize(content, chunk_path, voice=voice)
            chunk_paths.append(chunk_path)
            if speaker == "NARRATOR":
                last_narrator_voice = voice["id"]

            chunk_dur = self.measure_seconds(chunk_path)
            timeline.append({
                "start_sec": cursor,
                "end_sec": cursor + chunk_dur,
                "speaker": speaker,
                "text": content,
            })
            cursor += chunk_dur

            # 화자 사이 짧은 무음(타임라인엔 미포함)
            if self.pause_between > 0 and i < len(segments) - 1:
                sil = work_dir / f"sil_{i:02d}.mp3"
                res = self._run(build_silence_cmd(self._ffmpeg_path(), sil,
                                                  duration_sec=self.pause_between))
                if res.returncode != 0:
                    raise RuntimeError("무음 생성 실패")
                chunk_paths.append(sil)
                cursor += self.pause_between

        list_file = work_dir / "concat.txt"
        list_file.write_text(
            "\n".join(f"file '{p.resolve().as_posix()}'" for p in chunk_paths) + "\n",
            encoding="utf-8",
        )
        res = self._run(build_audio_concat_cmd(
            self._ffmpeg_path(), str(list_file.resolve()), str(output_path.resolve())))
        if res.returncode != 0:
            log.error(f"멀티보이스 concat 실패: {res.stderr[-500:]}")
            raise RuntimeError("멀티보이스 ffmpeg concat 실패")

        try:
            for p in work_dir.iterdir():
                p.unlink()
            work_dir.rmdir()
        except Exception:
            pass

        if last_narrator_voice:
            self.library.save_last(last_narrator_voice)
        log.info(f"멀티보이스 합성 완료: {output_path} ({len(segments)} 청크, {cursor:.1f}s)")
        if return_timeline:
            return output_path, timeline
        return output_path
