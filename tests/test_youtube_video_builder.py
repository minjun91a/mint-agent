# -*- coding: utf-8 -*-
"""
adapters.youtube.video_builder 테스트.

ffmpeg subprocess가 얽혀 결정적 테스트가 불가능했던 모듈을, 순수 커맨드 빌더(골든 arg 리스트)
+ 러너 주입(가짜 러너로 명령 시퀀스 검증)으로 가른다 — 실제 ffmpeg 없이 검증.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from adapters.youtube.video_builder import (
    _DEFAULT_SRT_STYLE,
    RunResult,
    build_clip_cmd,
    build_concat_cmd,
    build_mux_cmd,
    build_subtitle_cmd,
    build_video,
    parse_duration,
    zoom_frames,
)


# ── 순수 로직 ──

def test_parse_duration_matches_and_defaults():
    assert parse_duration("bla Duration: 00:01:30.50, start: 0.0") == pytest.approx(90.5)
    assert parse_duration("Duration: 01:00:00.00,") == pytest.approx(3600.0)
    assert parse_duration("no duration line") == 0.0


@pytest.mark.parametrize("per_image,exp", [(1.5, 45), (0.5, 30), (1.0, 30), (3.0, 90), (0.0, 30)])
def test_zoom_frames(per_image, exp):
    assert zoom_frames(per_image) == exp


def test_build_clip_cmd_golden():
    cmd = build_clip_cmd("ff", "a.png", "c.mp4", per_image=1.5, width=1080, height=1920)
    assert cmd == [
        "ff", "-y", "-loop", "1", "-i", "a.png",
        "-vf",
        "scale=1188:2112:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "zoompan=z='min(zoom+0.0015,1.10)':d=45:s=1080x1920:fps=30",
        "-t", "1.500",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
        "-preset", "veryfast", "-crf", "23",
        "c.mp4",
    ]


def test_build_concat_cmd_golden():
    assert build_concat_cmd("ff", "clips.txt", "silent.mp4") == [
        "ff", "-y", "-f", "concat", "-safe", "0",
        "-i", "clips.txt", "-c", "copy", "silent.mp4",
    ]


def test_build_subtitle_cmd_golden():
    cmd = build_subtitle_cmd("ff", "silent.mp4", "subbed.mp4", srt_name="cap.srt")
    assert cmd == [
        "ff", "-y",
        "-i", "silent.mp4",
        "-vf", f"subtitles='cap.srt':force_style='{_DEFAULT_SRT_STYLE}'",
        "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-crf", "20",
        "-threads", "2",
        "subbed.mp4",
    ]


def test_build_mux_cmd_golden():
    assert build_mux_cmd("ff", "v.mp4", "a.mp3", "out.mp4") == [
        "ff", "-y", "-i", "v.mp4", "-i", "a.mp3",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "out.mp4",
    ]


def test_srt_style_has_impact_and_margin():
    assert "FontName=Impact" in _DEFAULT_SRT_STYLE
    assert "MarginV=160" in _DEFAULT_SRT_STYLE


# ── 러너 주입: 명령 시퀀스 (실제 ffmpeg 없이) ──

class _FakeRunner:
    """가짜 러너 — (cmd, cwd) 기록. 프로브 호출('-y' 없음)엔 Duration stderr 반환."""

    def __init__(self, *, duration="Duration: 00:00:06.00, start: 0.0", fail=None):
        self.calls: list[tuple[list[str], str | None]] = []
        self._dur = duration
        self._fail = fail  # callable(cmd) -> bool

    def __call__(self, cmd, *, cwd=None) -> RunResult:
        self.calls.append((cmd, cwd))
        if "-y" not in cmd:                       # 프로브: [ffmpeg, "-i", audio]
            return RunResult(0, self._dur)
        if self._fail is not None and self._fail(cmd):
            return RunResult(1, "boom")
        return RunResult(0, "")


def test_build_video_requires_at_least_one_image(tmp_path):
    with pytest.raises(ValueError):
        build_video(tmp_path / "a.mp3", [], tmp_path / "o.mp4", ffmpeg="ff", run=_FakeRunner())


def test_build_video_raises_on_zero_duration(tmp_path):
    runner = _FakeRunner(duration="no duration here")
    with pytest.raises(RuntimeError):
        build_video(tmp_path / "a.mp3", [tmp_path / "s.png"], tmp_path / "o.mp4",
                    ffmpeg="ff", run=runner)


def test_build_video_no_srt_command_sequence(tmp_path):
    runner = _FakeRunner()
    imgs = [tmp_path / "s1.png", tmp_path / "s2.png"]
    out = tmp_path / "final.mp4"
    res = build_video(tmp_path / "a.mp3", imgs, out, ffmpeg="ff", run=runner)
    assert res == out

    cmds = [c for c, _ in runner.calls]
    assert cmds[0] == ["ff", "-i", str(tmp_path / "a.mp3")]      # 프로브 먼저
    assert sum(1 for c in cmds if "-loop" in c) == 2            # 이미지당 클립 1개
    assert sum(1 for c in cmds if "concat" in c) == 1
    assert not any("subtitles=" in " ".join(c) for c in cmds)  # 자막 없음
    assert cmds[-1][-1] == str(out)                             # 마지막 mux → output
    assert len(cmds) == 1 + 2 + 1 + 1                          # 프로브+클립2+concat+mux


def test_build_video_with_srt_adds_subtitle_step_with_cwd(tmp_path):
    srt = tmp_path / "cap.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    runner = _FakeRunner()
    out = tmp_path / "final.mp4"
    build_video(tmp_path / "a.mp3", [tmp_path / "s1.png"], out,
                srt_path=srt, ffmpeg="ff", run=runner)

    sub = [(c, cwd) for c, cwd in runner.calls if "subtitles=" in " ".join(c)]
    assert len(sub) == 1
    assert sub[0][1] == str(srt.resolve().parent)              # 자막 실행 cwd = srt 폴더
    cmds = [c for c, _ in runner.calls]
    assert len(cmds) == 1 + 1 + 1 + 1 + 1                      # 프로브+클립1+concat+자막+mux
    assert "silent_subbed.mp4" in " ".join(cmds[-1])          # mux 입력 = 자막본


def test_build_video_raises_on_clip_failure(tmp_path):
    runner = _FakeRunner(fail=lambda c: "-loop" in c)          # 클립 명령 실패
    with pytest.raises(RuntimeError):
        build_video(tmp_path / "a.mp3", [tmp_path / "s.png"], tmp_path / "o.mp4",
                    ffmpeg="ff", run=runner)
