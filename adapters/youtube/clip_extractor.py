# -*- coding: utf-8 -*-
"""
adapters.youtube.clip_extractor — 롱폼 → 숏폼 후크 추출기 (produce 단계, 결정적).

`youtube-agent/agents/clip_extractor.py`를 모노레포로 이식. longform dict의
highlight_clips에서 상위 N개를 독립 숏폼 스크립트로 변환한다. LLM·네트워크 없는 순수
dict 변환 — 동작은 원본과 동일하며 `tests/test_youtube_clip_extractor.py`로 고정한다.

사용법:
    from adapters.youtube.clip_extractor import build_short_scripts
    shorts = build_short_scripts(longform_dict, top_n=4)
"""
from __future__ import annotations

import logging

log = logging.getLogger("mint.adapters.youtube.clip_extractor")


def build_short_scripts(longform: dict, top_n: int = 4) -> list[dict]:
    """롱폼 dict의 highlight_clips에서 상위 N개 후크를 숏폼 스크립트로 변환."""
    highlights = sorted(
        longform.get("highlight_clips", []),
        key=lambda c: c.get("score", 0),
        reverse=True,
    )[:top_n]

    if not highlights:
        log.warning("highlight_clips 없음 — 챕터 1을 기본 숏폼 후보로 사용")
        ch = (longform.get("chapters") or [{}])[0]
        first_scene = (ch.get("scenes") or [{}])[0]
        highlights = [{
            "chapter": 1,
            "narration_segment": first_scene.get("narration_segment", longform.get("series_hook", "")),
            "image_index": 1,
            "score": 5,
        }]

    chapters = longform.get("chapters", [])
    shorts: list[dict] = []

    for i, hc in enumerate(highlights):
        ch_idx = max(1, hc.get("chapter", 1)) - 1
        if ch_idx >= len(chapters):
            log.warning(f"highlight chapter {ch_idx+1} 존재하지 않음 — skip")
            continue
        chapter = chapters[ch_idx]
        scenes = chapter.get("scenes", [])
        if not scenes:
            continue

        # 핵심 이미지 + 앞뒤 이미지로 6장 구성
        img_idx = max(1, hc.get("image_index", 1)) - 1
        img_idx = min(img_idx, len(scenes) - 1)

        # 챕터 내 핵심 장면 + 다른 챕터에서 보조 장면 가져와 6장 구성
        primary = scenes[img_idx]
        # 챕터 내 시작/중반/끝 + 인접 챕터 1장씩으로 6장 구성
        candidates = []
        candidates.append(scenes[0])
        if len(scenes) > 2:
            candidates.append(scenes[len(scenes) // 2])
        candidates.append(primary)
        candidates.append(scenes[-1])
        # 다음 챕터 첫 장면 (있으면)
        if ch_idx + 1 < len(chapters):
            next_scenes = chapters[ch_idx + 1].get("scenes", [])
            if next_scenes:
                candidates.append(next_scenes[0])
        # 중복 제거 (image_prompt 기준), 6장 한도
        seen = set()
        unique = []
        for c in candidates:
            key = c.get("image_prompt", "")
            if key not in seen:
                unique.append(c)
                seen.add(key)
            if len(unique) >= 6:
                break

        # 숏폼 스크립트 — 5초 후크 + 30~40초 본문 + 5초 클리프행어
        short = {
            "title": f"{longform.get('title', 'Fantasy Short')} — Highlight {i+1}",
            "hook":  f"[NARRATOR] {hc['narration_segment']}",
            "body":  "\n".join(
                f"[NARRATOR] {sc.get('narration_segment','')}" for sc in unique[:5]
            ),
            "cliffhanger": "[NARRATOR] Watch the full story on the channel.",
            "scenes": [
                {
                    "order":             idx + 1,
                    "narration_segment": sc.get("narration_segment", ""),
                    "image_prompt":      sc.get("image_prompt", ""),
                }
                for idx, sc in enumerate(unique[:6])
            ],
            "thumbnail_prompt": longform.get("thumbnail_prompt", primary.get("image_prompt", "")),
            "thumbnail_text":  (longform.get("thumbnail_text") or "EPIC")[:30],
            "_source": {
                "longform_title": longform.get("title", ""),
                "chapter":        ch_idx + 1,
                "score":          hc.get("score"),
            },
        }
        shorts.append(short)

    log.info(f"숏폼 후보 {len(shorts)}편 추출 완료")
    return shorts
