# -*- coding: utf-8 -*-
"""
adapters.youtube.clip_extractor 특성화(golden-master) 테스트.

youtube-agent/agents/clip_extractor.py에서 이식한 롱폼→숏폼 변환의 결정적 동작을 고정한다.
순수 dict 변환(LLM·네트워크 없음)이라 목 없이 검증 가능.
"""
from __future__ import annotations

import pytest

from adapters.youtube.clip_extractor import build_short_scripts

LONGFORM = {
    "title": "Tower of Trials",
    "thumbnail_prompt": "epic tower",
    "thumbnail_text": "RISE",
    "chapters": [
        {"scenes": [
            {"narration_segment": "C1 scene1", "image_prompt": "img-c1-s1"},
            {"narration_segment": "C1 scene2", "image_prompt": "img-c1-s2"},
            {"narration_segment": "C1 scene3", "image_prompt": "img-c1-s3"},
        ]},
        {"scenes": [
            {"narration_segment": "C2 scene1", "image_prompt": "img-c2-s1"},
            {"narration_segment": "C2 scene2", "image_prompt": "img-c2-s2"},
        ]},
    ],
    "highlight_clips": [
        {"chapter": 1, "image_index": 2, "narration_segment": "hook A", "score": 9},
        {"chapter": 2, "image_index": 1, "narration_segment": "hook B", "score": 5},
    ],
}


def test_basic_structure_and_count():
    shorts = build_short_scripts(LONGFORM, top_n=4)
    assert len(shorts) == 2
    assert [s["title"] for s in shorts] == [
        "Tower of Trials — Highlight 1",
        "Tower of Trials — Highlight 2",
    ]
    assert [s["hook"] for s in shorts] == ["[NARRATOR] hook A", "[NARRATOR] hook B"]
    assert all(s["cliffhanger"] == "[NARRATOR] Watch the full story on the channel." for s in shorts)


def test_dedup_by_image_prompt_and_cross_chapter():
    # Highlight 1(chapter1): scenes[0,1,primary=1,2] + 다음 챕터 첫 장면 → dedup 후 4장
    s0 = build_short_scripts(LONGFORM, top_n=4)[0]
    prompts = [sc["image_prompt"] for sc in s0["scenes"]]
    assert prompts == ["img-c1-s1", "img-c1-s2", "img-c1-s3", "img-c2-s1"]
    assert len(prompts) == len(set(prompts))  # 중복 없음


def test_scene_order_is_sequential():
    s0 = build_short_scripts(LONGFORM, top_n=4)[0]
    assert [sc["order"] for sc in s0["scenes"]] == [1, 2, 3, 4]


def test_source_metadata():
    shorts = build_short_scripts(LONGFORM, top_n=4)
    assert shorts[0]["_source"] == {"longform_title": "Tower of Trials", "chapter": 1, "score": 9}
    assert shorts[1]["_source"] == {"longform_title": "Tower of Trials", "chapter": 2, "score": 5}


def test_thumbnail_fields():
    s0 = build_short_scripts(LONGFORM, top_n=4)[0]
    assert s0["thumbnail_prompt"] == "epic tower"
    assert s0["thumbnail_text"] == "RISE"


def test_sorted_by_score_desc_and_top_n():
    lf = {**LONGFORM, "highlight_clips": [
        {"chapter": 1, "image_index": 1, "narration_segment": "low", "score": 1},
        {"chapter": 2, "image_index": 1, "narration_segment": "high", "score": 10},
    ]}
    shorts = build_short_scripts(lf, top_n=1)
    assert len(shorts) == 1
    assert shorts[0]["hook"] == "[NARRATOR] high"  # 높은 점수 우선


def test_fallback_when_no_highlights():
    lf = {"title": "X", "chapters": [{"scenes": [
        {"narration_segment": "first", "image_prompt": "p1"},
    ]}]}
    shorts = build_short_scripts(lf, top_n=4)
    assert len(shorts) == 1
    assert shorts[0]["hook"] == "[NARRATOR] first"   # 챕터1 첫 장면 narration 사용
    assert shorts[0]["_source"]["score"] == 5         # 합성 highlight 기본 score


def test_out_of_range_chapter_skipped():
    lf = {"title": "X", "chapters": [{"scenes": [{"narration_segment": "a", "image_prompt": "p"}]}],
          "highlight_clips": [{"chapter": 9, "image_index": 1, "narration_segment": "h", "score": 3}]}
    assert build_short_scripts(lf, top_n=4) == []   # ch_idx 범위 밖 → skip


def test_thumbnail_text_default_and_truncation():
    lf = {**LONGFORM, "thumbnail_text": None}
    s0 = build_short_scripts(lf, top_n=1)[0]
    assert s0["thumbnail_text"] == "EPIC"  # None → 'EPIC' 기본
