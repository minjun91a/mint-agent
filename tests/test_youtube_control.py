# -*- coding: utf-8 -*-
"""
adapters.youtube.control 특성화 테스트 — youtube 룰 라우팅(결정적).

원본 agents/control.py의 룰/추출 로직을 고정한다. LLM 폴백(비결정)은 엔진 테스트에서 다룸.
"""
from __future__ import annotations

from adapters.youtube.control import parse, parse_rule


def _intent(text: str) -> str:
    return parse_rule(text).intent


# ── intent 매핑 ──

def test_brief():
    assert _intent("오늘 우선순위 브리핑") == "brief"


def test_daily_report():
    assert _intent("유튜브 일일 리포트 보여줘") == "daily"


def test_channel_info():
    assert _intent("채널 정보") == "channel_info"


def test_help():
    assert _intent("명령어 뭐 있어") == "help"


def test_series_with_count():
    cmd = parse_rule("시리즈 만들어 숏폼 3편")
    assert cmd.intent == "series"
    assert cmd.args["shorts"] == 3


def test_series_longform_only_sets_zero():
    cmd = parse_rule("시리즈 롱폼만")
    assert cmd.intent == "series"
    assert cmd.args["shorts"] == 0


def test_story_gem_with_category_and_pro_model():
    cmd = parse_rule("gem 무림 쇼츠 만들어줘 pro")
    assert cmd.intent == "story_gem"
    assert cmd.args["category"] == "Murim"
    assert cmd.args["model"] == "gemini-2.5-pro"


def test_story_with_no_upload():
    cmd = parse_rule("쇼츠 만들어줘 업로드 하지 마")
    assert cmd.intent == "story"
    assert cmd.args["no_upload"] is True


def test_gem_takes_priority_over_plain_story():
    # 'gem' + 'story' 키워드 동시 → story_gem 룰이 먼저(순서)
    assert _intent("gem 스토리 만들어") == "story_gem"


def test_research_extracts_query_and_top():
    cmd = parse_rule("리서치 AI shorts trends 5개")
    assert cmd.intent == "research"
    assert cmd.args["query"] == "AI shorts trends 5개"
    assert cmd.args["top"] == 5


def test_research_empty_query_becomes_unknown():
    cmd = parse_rule("리서치")
    assert cmd.intent == "unknown"
    assert "쿼리가 비어" in cmd.explain


def test_no_match_returns_none():
    assert parse_rule("완전히 관계없는 잡담") is None


def test_parse_without_llm_falls_back_to_unknown():
    cmd = parse("관계없는 잡담", allow_llm=False)
    assert cmd.intent == "unknown"
    assert cmd.source == "rule"


def test_rule_command_metadata():
    cmd = parse_rule("브리핑")
    assert cmd.source == "rule"
    assert cmd.confidence == 0.95
