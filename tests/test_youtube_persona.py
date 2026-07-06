# -*- coding: utf-8 -*-
"""
adapters.youtube.persona 테스트 — youtube 페르소나 *정의*(콘텐츠). Discord 채널 매핑은 분리됨.

원본 페르소나 세트를 고정하고, 이식 시 제거하기로 한 AXIS 언급이 없는지 검증한다.
채널→페르소나 라우팅(Discord IO)은 `test_discord_channels.py`에서 다룬다.
"""
from __future__ import annotations

from adapters.youtube.persona import all_personas, build_registry, resolve


def test_youtube_persona_is_discord_free_by_default():
    # 해석기 미주입이면 채널 라우팅이 없어 default (youtube는 Discord를 모른다).
    assert resolve("12345").key == "default"


def test_resolve_accepts_injected_channel_key():
    # 채널 해석기를 주입하면 라우팅된다(엔진 PersonaRegistry가 resolver를 받는 구조).
    reg = build_registry(channel_key=lambda cid: "research" if cid == "42" else None)
    assert reg.resolve("42").key == "research"
    assert reg.resolve("99").key == "default"


def test_all_personas_has_expected_keys():
    keys = set(all_personas())
    assert {"research", "orchestrator", "control", "status", "daily",
            "guide", "planner", "engineer", "default"} <= keys


def test_engineer_is_claude_provider():
    assert all_personas()["engineer"].provider == "claude"


def test_no_axis_references_anywhere():
    for p in all_personas().values():
        assert "AXIS" not in p.system
        assert "axis-automation" not in p.system     # 범위 제외 — 되살리지 않음
