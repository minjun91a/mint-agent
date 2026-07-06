# -*- coding: utf-8 -*-
"""
adapters.youtube.control 특성화 테스트 — youtube 룰 라우팅(결정적).

원본 agents/control.py의 룰/추출 로직을 고정한다. LLM 폴백(비결정)은 엔진 테스트에서 다룸.
"""
from __future__ import annotations

from core.control import Command
from adapters.youtube.control import build_handlers, dispatch, parse, parse_rule


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


# ── dispatch: 핸들러 맵 (파이프라인 주입, 원본 arg 추출 parity) ──

def test_story_handler_extracts_no_upload():
    calls = {}

    def run_story(*, upload):
        calls["upload"] = upload
        return "vid"

    out = dispatch(Command(intent="story", args={"no_upload": True}), run_story=run_story)
    assert out == {"ok": True, "result": "vid"} and calls["upload"] is False


def test_story_gem_handler_extracts_category_and_model():
    calls = {}

    def run_gem(*, category, model):
        calls.update(category=category, model=model)
        return "g"

    dispatch(Command(intent="story_gem", args={"category": "Murim", "model": "gemini-2.5-pro"}),
             run_story_gem=run_gem)
    assert calls == {"category": "Murim", "model": "gemini-2.5-pro"}


def test_story_gem_default_model():
    calls = {}
    dispatch(Command(intent="story_gem", args={}),
             run_story_gem=lambda *, category, model: calls.update(model=model))
    assert calls["model"] == "gemini-2.5-flash"          # 원본 기본값


def test_series_handler_extracts_shorts():
    calls = {}
    dispatch(Command(intent="series", args={"shorts": 4}),
             run_series=lambda *, top_shorts: calls.update(n=top_shorts))
    assert calls["n"] == 4


def test_research_handler_extracts_query_and_top():
    calls = {}
    dispatch(Command(intent="research", args={"query": "AI shorts", "top": 3}),
             run_research=lambda q, *, top_k: calls.update(q=q, k=top_k))
    assert calls == {"q": "AI shorts", "k": 3}


def test_brief_handler_wires_injected_callable():
    assert dispatch(Command(intent="brief"), run_brief=lambda: "briefing") == {
        "ok": True, "result": "briefing"}


def test_daily_handler_wraps_text():
    assert dispatch(Command(intent="daily"), daily_report=lambda: "report") == {
        "ok": True, "result": {"text": "report"}}


def test_channel_info_handler():
    assert dispatch(Command(intent="channel_info"),
                    channel_info=lambda: {"title": "X"})["result"] == {"title": "X"}


def test_help_always_present():
    out = dispatch(Command(intent="help"))
    assert out["ok"] is True and "/brief" in out["msg"]


def test_uninjected_pipeline_is_unsupported():
    # 미이식 파이프라인(story) 미주입 → 엔진이 "미구현"으로 정직하게 응답.
    out = dispatch(Command(intent="story", args={}))
    assert out["ok"] is False and "미구현" in out["msg"] and "story" in out["msg"]


def test_unknown_intent_dispatch():
    assert dispatch(Command(intent="unknown")) == {"ok": False, "msg": "unknown intent"}


def test_build_handlers_omits_uninjected():
    handlers = build_handlers(run_brief=lambda: "b")
    assert set(handlers) == {"help", "brief"}            # 주입한 것 + help만
