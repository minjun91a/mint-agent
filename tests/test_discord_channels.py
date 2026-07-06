# -*- coding: utf-8 -*-
"""
adapters.discord 테스트 — 채널 정체성 매핑 + youtube 페르소나 라우팅(IO↔콘텐츠 조립).

'2중 결합' 분해 검증: Discord 채널 ID → 역할 매핑(env)과, 그것으로 youtube 페르소나를 고르는
라우팅을 고정한다. 의존 방향은 IO(discord) → 콘텐츠(youtube)뿐임을 함께 확인한다.
"""
from __future__ import annotations

from adapters.discord.channels import ENV_KEY_BY_ROLE, channel_key_from_env
from adapters.discord.personas import build_registry_for_discord, resolve


# ── channel_key_from_env: 채널 ID → 역할 (원본 parity) ──

def test_channel_key_from_env_maps_role(monkeypatch):
    monkeypatch.setenv("DISCORD_CHANNEL_RESEARCH", "12345")
    assert channel_key_from_env("12345") == "research"
    assert channel_key_from_env(12345) == "research"        # int도 허용


def test_channel_key_from_env_unmapped_is_none(monkeypatch):
    monkeypatch.delenv("DISCORD_CHANNEL_RESEARCH", raising=False)
    assert channel_key_from_env("99999") is None


def test_channel_key_from_env_empty_is_none():
    assert channel_key_from_env("") is None
    assert channel_key_from_env("   ") is None


def test_env_key_by_role_has_expected_roles():
    assert {"research", "orchestrator", "control", "status",
            "daily", "guide", "engineer", "planner"} == set(ENV_KEY_BY_ROLE)


# ── discord.personas.resolve: 채널 → youtube 페르소나 (조립) ──

def test_resolve_routes_channel_to_youtube_persona(monkeypatch):
    monkeypatch.setenv("DISCORD_CHANNEL_ORCHESTRATOR", "777")
    assert resolve("777").key == "orchestrator"
    assert resolve(777).key == "orchestrator"


def test_resolve_unmapped_channel_is_default(monkeypatch):
    monkeypatch.delenv("DISCORD_CHANNEL_ORCHESTRATOR", raising=False)
    assert resolve("nope").key == "default"


def test_build_registry_for_discord_wires_resolver(monkeypatch):
    monkeypatch.setenv("DISCORD_CHANNEL_PLANNING", "555")
    reg = build_registry_for_discord()
    assert reg.resolve("555").key == "planner"
