# -*- coding: utf-8 -*-
"""
adapters.youtube.captions 특성화(golden-master) 테스트.

youtube-agent/agents/captions.py에서 이식한 자막 빌더의 결정적 동작을 고정한다.
타임라인 → cue → SRT 전 구간이 순수(LLM·네트워크 없음)라 목 없이 검증 가능.
"""
from __future__ import annotations

import pytest

from adapters.youtube.captions import (
    SrtCue,
    _fmt_ts,
    build_cues,
    build_srt,
    strip_speaker_tag,
)


@pytest.mark.parametrize("raw,expected", [
    ("[NARRATOR] Hello world", "Hello world"),
    ("[SYSTEM] A\n[DIALOGUE_F] B", "A B"),
    ("  [narrator]  spaced  ", "spaced"),
    ("", ""),
    ("no tag here", "no tag here"),
])
def test_strip_speaker_tag(raw, expected):
    assert strip_speaker_tag(raw) == expected


@pytest.mark.parametrize("sec,expected", [
    (0.0, "00:00:00,000"),
    (2.0, "00:00:02,000"),
    (1.5, "00:00:01,500"),
    (3661.5, "01:01:01,500"),
    (-5.0, "00:00:00,000"),       # 음수 보호
    (0.9995, "00:00:01,000"),     # ms 반올림 → 1000 carry
])
def test_fmt_ts(sec, expected):
    assert _fmt_ts(sec) == expected


def test_build_cues_single_phrase():
    timeline = [{"start_sec": 0.0, "end_sec": 2.0, "speaker": "NARRATOR",
                 "text": "[NARRATOR] Hello world."}]
    cues = build_cues(timeline)
    assert len(cues) == 1
    c = cues[0]
    assert (c.index, c.start, c.end, c.lines) == (1, 0.0, 2.0, ["Hello world."])


def test_build_cues_splits_sentences_and_apportions_time():
    timeline = [{"start_sec": 0.0, "end_sec": 4.0, "text": "[NARRATOR] AAAA. BBBB."}]
    cues = build_cues(timeline)
    assert [(c.index, c.start, c.end, c.lines) for c in cues] == [
        (1, 0.0, 2.0, ["AAAA."]),
        (2, 2.0, 4.0, ["BBBB."]),
    ]


def test_build_cues_skips_empty_or_zero_duration():
    timeline = [
        {"start_sec": 0.0, "end_sec": 0.0, "text": "[NARRATOR] zero dur"},   # end<=start
        {"start_sec": 1.0, "end_sec": 2.0, "text": "[NARRATOR]   "},          # 빈 텍스트
    ]
    assert build_cues(timeline) == []


def test_build_cues_long_text_wraps_two_lines():
    # 28자 초과 phrase → 2줄 wrap
    timeline = [{"start_sec": 0.0, "end_sec": 5.0,
                 "text": "[NARRATOR] the fallen master climbs the endless tower"}]
    cues = build_cues(timeline)
    assert len(cues) >= 1
    for c in cues:
        assert len(c.lines) <= 2
        assert all(isinstance(x, str) and x for x in c.lines)


def test_build_srt_writes_file(tmp_path):
    timeline = [{"start_sec": 0.0, "end_sec": 4.0, "text": "[NARRATOR] AAAA. BBBB."}]
    out = build_srt(timeline, tmp_path / "out.srt")
    content = out.read_text(encoding="utf-8")
    assert content == (
        "1\n00:00:00,000 --> 00:00:02,000\nAAAA.\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nBBBB.\n"
    )


def test_build_srt_empty_timeline_writes_trailing_newline(tmp_path):
    out = build_srt([], tmp_path / "empty.srt")
    assert out.read_text(encoding="utf-8") == "\n"


def test_dataclass_timeline_segment_input():
    from adapters.youtube.captions import TimelineSegment
    seg = TimelineSegment(start_sec=0.0, end_sec=2.0, speaker="NARRATOR", text="[NARRATOR] Hi there.")
    cues = build_cues([seg])
    assert len(cues) == 1
    assert cues[0].lines == ["Hi there."]
