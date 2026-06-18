# -*- coding: utf-8 -*-
"""
adapters.youtube.captions — 멀티보이스 TTS 타임라인 → SRT(영문 burn-in 자막).

`youtube-agent/agents/captions.py`를 모노레포로 이식. produce 단계(자산화/렌더)의 결정적
하위 모듈 — LLM·네트워크 없음. 동작은 원본과 동일하며 `tests/test_youtube_captions.py`의
특성화 테스트로 고정한다.

흐름:
    1) tts_engine이 청크별 타임라인(start_sec, end_sec, speaker, text) 반환
    2) build_srt() — 긴 청크는 단어 단위로 분할, segment 길이에 비례 시간 배분
       (모바일 가독성: 28자/2줄 한도)
    3) strip_speaker_tag() — [SPEAKER] 태그 제거
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("mint.adapters.youtube.captions")

SPEAKER_TAG_RE = re.compile(
    r"^\s*\[(NARRATOR|SYSTEM|DIALOGUE_F|DIALOGUE_M)\]\s*",
    re.IGNORECASE,
)

# 모바일 쇼츠 가독성 한도
MAX_CHARS_PER_LINE = 28
MAX_LINES_PER_CUE = 2


@dataclass
class TimelineSegment:
    start_sec: float
    end_sec: float
    speaker: str
    text: str  # raw text (태그 제거 후)


@dataclass
class SrtCue:
    index: int
    start: float
    end: float
    lines: list[str]


# ── 텍스트 정제 ───────────────────────────────────────────────────────────

def strip_speaker_tag(text: str) -> str:
    """[SPEAKER] 태그를 제거하고 본문만 반환."""
    if not text:
        return ""
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        cleaned_lines.append(SPEAKER_TAG_RE.sub("", line).strip())
    return " ".join(l for l in cleaned_lines if l).strip()


# ── 문장 → cue 분할 ──────────────────────────────────────────────────────

_SENTENCE_END_RE = re.compile(r"(?<=[.!?…])\s+|(?<=[,;:])\s+(?=\S{8,})")


def _split_into_phrases(text: str) -> list[str]:
    """긴 텍스트를 자막 cue 후보 phrase로 분할.

    - 문장 부호로 1차 split
    - 한 phrase가 28*2=56자보다 길면 단어 단위로 추가 split
    """
    parts = [p.strip() for p in _SENTENCE_END_RE.split(text) if p.strip()]
    if not parts:
        parts = [text.strip()]

    max_phrase = MAX_CHARS_PER_LINE * MAX_LINES_PER_CUE
    out: list[str] = []
    for p in parts:
        if len(p) <= max_phrase:
            out.append(p)
            continue
        # 단어 단위로 누적
        words = p.split()
        cur = ""
        for w in words:
            if len(cur) + len(w) + 1 > max_phrase and cur:
                out.append(cur.strip())
                cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur:
            out.append(cur.strip())
    return out


def _wrap_cue_text(phrase: str) -> list[str]:
    """phrase를 28자 max 2줄로 wrap. 단어 경계 보존."""
    words = phrase.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 > MAX_CHARS_PER_LINE and cur:
            lines.append(cur.strip())
            cur = w
            if len(lines) >= MAX_LINES_PER_CUE:
                # 더 이상 줄을 못 만들면 마지막 줄에 강제 합침 (말줄임 방지)
                lines[-1] = (lines[-1] + " " + w).strip()
                cur = ""
        else:
            cur = (cur + " " + w).strip()
    if cur:
        if len(lines) < MAX_LINES_PER_CUE:
            lines.append(cur.strip())
        else:
            lines[-1] = (lines[-1] + " " + cur).strip()
    return lines or [phrase[:MAX_CHARS_PER_LINE]]


# ── SRT 빌드 ──────────────────────────────────────────────────────────────

def _fmt_ts(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_cues(timeline: list[TimelineSegment | dict]) -> list[SrtCue]:
    """타임라인 segment들을 cue 리스트로 변환.

    각 segment의 text를 phrase 단위로 split → segment 길이에 비례 분배.
    """
    cues: list[SrtCue] = []
    idx = 1
    for seg in timeline:
        if isinstance(seg, dict):
            start = float(seg.get("start_sec", 0))
            end = float(seg.get("end_sec", 0))
            speaker = seg.get("speaker", "NARRATOR")
            text = strip_speaker_tag(seg.get("text", "") or "")
        else:
            start, end, speaker, text = seg.start_sec, seg.end_sec, seg.speaker, strip_speaker_tag(seg.text)

        if not text or end <= start:
            continue

        phrases = _split_into_phrases(text)
        seg_dur = end - start
        # 각 phrase의 길이(문자수)에 비례 시간 배분
        char_total = sum(max(1, len(p)) for p in phrases)
        cur = start
        for p in phrases:
            share = max(1, len(p)) / char_total
            cue_dur = max(0.6, seg_dur * share)  # 최소 0.6s 보장 (가독성)
            cue_end = min(end, cur + cue_dur)
            if cue_end <= cur:
                cue_end = cur + 0.6
            lines = _wrap_cue_text(p)
            cues.append(SrtCue(index=idx, start=cur, end=cue_end, lines=lines))
            idx += 1
            cur = cue_end
    return cues


def write_srt(cues: list[SrtCue], srt_path: Path) -> Path:
    """cue 리스트 → SRT 파일."""
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    for c in cues:
        blocks.append(
            f"{c.index}\n"
            f"{_fmt_ts(c.start)} --> {_fmt_ts(c.end)}\n"
            + "\n".join(c.lines)
        )
    srt_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    log.info(f"SRT 작성: {srt_path}  ({len(cues)} cues)")
    return srt_path


def build_srt(timeline: list[dict] | list[TimelineSegment], srt_path: Path) -> Path:
    """진입점 — 타임라인 → SRT 파일."""
    cues = build_cues(timeline)
    if not cues:
        log.warning("SRT 생성: 빈 cue (timeline 비어있음 or text 없음)")
    return write_srt(cues, srt_path)
