# -*- coding: utf-8 -*-
"""
adapters.youtube.persona 테스트 — youtube 페르소나 정의·Discord 채널 매핑.

원본 페르소나 세트를 고정하고, 이식 시 제거하기로 한 AXIS 언급이 없는지 검증한다.
"""
from __future__ import annotations

from adapters.youtube.persona import all_personas, resolve


def test_resolve_by_env_channel(monkeypatch):
    monkeypatch.setenv("DISCORD_CHANNEL_RESEARCH", "12345")
    assert resolve("12345").key == "research"
    assert resolve(12345).key == "research"          # int도 허용


def test_resolve_unmapped_channel_is_default(monkeypatch):
    monkeypatch.delenv("DISCORD_CHANNEL_RESEARCH", raising=False)
    assert resolve("99999").key == "default"


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
