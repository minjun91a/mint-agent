# -*- coding: utf-8 -*-
"""
adapters.youtube.video_builder — 음성 + 이미지 슬라이드(+자막) → 세로 쇼츠 mp4 (produce 자산화, ffmpeg).

`youtube-agent/agents/video_builder.py`를 모노레포로 이식. ffmpeg subprocess가 얽혀 결정적
테스트가 불가능했던 모듈을, **순수 커맨드 빌더 + 러너 주입**으로 가른다(바이너리도 순수/IO 분리):

  1. **순수 로직** — 길이 파싱(`parse_duration`)·줌 프레임(`zoom_frames`)·ffmpeg 인자 리스트
     빌더(`build_clip_cmd`/`build_concat_cmd`/`build_subtitle_cmd`/`build_mux_cmd`). 전부 결정적
     → 골든(특성화) 테스트. 원본 인라인 명령 구성과 동일.
  2. **실행 경계** — `run(cmd, *, cwd=...)`(명령 실행)을 **주입**. 기본은 subprocess, 테스트는 가짜
     러너로 실제 ffmpeg 없이 "어떤 명령을 어떤 순서로 냈는가"를 검증. ffmpeg 경로는 지연 해석.

규격: 1080x1920 세로, H.264 + AAC. 원본의 deprecated `subtitles` 인자는 미사용이라 제거.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger("mint.adapters.youtube.video_builder")

# ASS force_style — Impact, 영문 burn-in, 하단 1/4 (원본과 동일)
_DEFAULT_SRT_STYLE = (
    "FontName=Impact,"
    "FontSize=18,"
    "PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000,"
    "BackColour=&H80000000,"
    "Bold=1,"
    "BorderStyle=1,"
    "Outline=4,"
    "Shadow=2,"
    "Alignment=2,"
    "MarginV=160"
)


@dataclass(frozen=True)
class RunResult:
    """명령 실행 결과 — 러너 주입 계약(반환 최소형)."""

    returncode: int
    stderr: str = ""


# run(cmd, *, cwd=None) -> RunResult
CommandRunner = Callable[..., RunResult]


# ── 순수 로직 ──────────────────────────────────────────────────────────────

def parse_duration(stderr: str) -> float:
    """ffmpeg `-i` stderr에서 Duration(hh:mm:ss.ss)을 초로. 미검출 시 0.0. 원본과 동일."""
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", stderr)
    if not m:
        return 0.0
    h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mn * 60 + s


def zoom_frames(per_image: float) -> int:
    """켄번스 zoompan 프레임 수(30fps 기준, 최소 30). 원본과 동일."""
    z = int(per_image * 30)
    return 30 if z < 30 else z


def build_clip_cmd(ffmpeg: str, img: str | Path, clip_out: str | Path, *,
                   per_image: float, width: int, height: int) -> list[str]:
    """이미지 1장 → per_image초 정지영상 mp4(scale·crop·슬로우 줌). 원본 인라인과 동일."""
    return [
        ffmpeg, "-y", "-loop", "1", "-i", str(img),
        "-vf",
        f"scale={width * 1.1:.0f}:{height * 1.1:.0f}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"zoompan=z='min(zoom+0.0015,1.10)':d={zoom_frames(per_image)}:s={width}x{height}:fps=30",
        "-t", f"{per_image:.3f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
        "-preset", "veryfast", "-crf", "23",
        str(clip_out),
    ]


def build_concat_cmd(ffmpeg: str, list_file: str | Path, out: str | Path) -> list[str]:
    """concat demuxer로 클립들을 무손실 이어붙임(stream copy). 원본과 동일."""
    return [
        ffmpeg, "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy", str(out),
    ]


def build_subtitle_cmd(ffmpeg: str, silent_in: str | Path, sub_out: str | Path, *,
                       srt_name: str, style: str = _DEFAULT_SRT_STYLE) -> list[str]:
    """무음 영상에 자막 burn-in(video-only, `-an`으로 오디오 미관여). 원본과 동일."""
    return [
        ffmpeg, "-y",
        "-i", str(silent_in),
        "-vf", f"subtitles='{srt_name}':force_style='{style}'",
        "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-crf", "20",
        "-threads", "2",
        str(sub_out),
    ]


def build_mux_cmd(ffmpeg: str, video_in: str | Path, audio_in: str | Path,
                  out: str | Path) -> list[str]:
    """영상(copy) + 오디오(AAC) mux. `-shortest`. 원본과 동일(영상은 항상 copy)."""
    return [
        ffmpeg, "-y",
        "-i", str(video_in),
        "-i", str(audio_in),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out),
    ]


# ── 실행 경계 ──────────────────────────────────────────────────────────────

def _default_ffmpeg() -> str:
    """imageio_ffmpeg 지연 해석 — 모듈 import 시점에 선택 의존성을 끌어오지 않기 위함."""
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _default_run(cmd: list[str], *, cwd: str | None = None) -> RunResult:
    """실 subprocess 실행 — stderr 캡처(원본과 동일 옵션)."""
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", cwd=cwd)
    return RunResult(returncode=r.returncode, stderr=r.stderr or "")


# ── 오케스트레이션 ─────────────────────────────────────────────────────────

def build_video(audio_path: str | Path, image_paths: list[Path], output_path: Path, *,
                srt_path: str | Path | None = None, burn_subtitles: bool = True,
                width: int = 1080, height: int = 1920,
                ffmpeg: str | None = None, run: CommandRunner | None = None) -> Path:
    """음성 + 이미지 N장 (+ SRT 자막 burn-in) → mp4 쇼츠.

    srt_path 지정 + burn_subtitles=True: ffmpeg subtitles 필터로 자막을 굽는다(자막 없으면 생략).
    ffmpeg/run 주입 가능(테스트용). 원본 3단 흐름(클립→concat→(자막)→mux)과 동일.
    """
    if not image_paths:
        raise ValueError("이미지가 1장 이상 필요")

    audio_path = Path(audio_path)
    ffmpeg = ffmpeg or _default_ffmpeg()
    run = run or _default_run

    # 오디오 길이 측정 (ffmpeg -i stderr 파싱)
    duration = parse_duration(run([ffmpeg, "-i", str(audio_path)]).stderr)
    if duration <= 0:
        raise RuntimeError(f"오디오 길이 측정 실패: {audio_path}")

    per_image = duration / len(image_paths)
    log.info(f"영상 합성 시작: {duration:.1f}s, {len(image_paths)}장 (장당 {per_image:.1f}s)"
             + (" + 자막 burn-in" if (srt_path and burn_subtitles) else ""))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output_path.parent / f".work_{output_path.stem}"
    work_dir.mkdir(exist_ok=True)

    # 1) 각 이미지를 per_image 길이의 클립으로
    clip_paths: list[Path] = []
    for i, img in enumerate(image_paths):
        clip = work_dir / f"clip_{i:02d}.mp4"
        res = run(build_clip_cmd(ffmpeg, str(img), str(clip),
                                 per_image=per_image, width=width, height=height))
        if res.returncode != 0:
            log.error(f"클립 생성 실패: {res.stderr[-500:]}")
            raise RuntimeError("ffmpeg 클립 생성 실패")
        clip_paths.append(clip)

    # 2) concat (절대 경로 list 파일)
    clip_list = work_dir / "clips.txt"
    clip_list.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in clip_paths) + "\n",
        encoding="utf-8",
    )
    silent_video = work_dir / "silent.mp4"
    res = run(build_concat_cmd(ffmpeg, str(clip_list.resolve()), str(silent_video.resolve())))
    if res.returncode != 0:
        log.error(f"concat 실패: {res.stderr[-500:]}")
        raise RuntimeError("ffmpeg concat 실패")

    # 3) (선택) 자막 burn-in → 영상+오디오 mux
    use_srt = bool(srt_path and burn_subtitles and Path(srt_path).exists())
    if use_srt:
        srt_dir = Path(srt_path).resolve().parent
        sub_video = work_dir / "silent_subbed.mp4"
        res = run(
            build_subtitle_cmd(ffmpeg, str(silent_video.resolve()), str(sub_video.resolve()),
                               srt_name=Path(srt_path).name),
            cwd=str(srt_dir),
        )
        if res.returncode != 0:
            log.error(f"자막 burn-in 실패: {res.stderr[-2000:]}")
            raise RuntimeError("ffmpeg 자막 burn-in 실패")
        video_for_mux: Path = sub_video
    else:
        video_for_mux = silent_video

    res = run(build_mux_cmd(ffmpeg, str(video_for_mux), str(audio_path), str(output_path)))
    if res.returncode != 0:
        log.error(f"최종 mux 실패: {res.stderr[-2000:]}")
        raise RuntimeError("ffmpeg mux 실패")

    # 4) 작업 폴더 정리
    try:
        for p in work_dir.iterdir():
            p.unlink()
        work_dir.rmdir()
    except Exception:
        pass

    log.info(f"영상 합성 완료: {output_path}  ({duration:.1f}s)")
    return output_path
