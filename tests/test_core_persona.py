# -*- coding: utf-8 -*-
"""
core.persona 엔진 테스트 — 페르소나 호출 메커니즘(메모리·게이트웨이 주입).

프롬프트 구성(순수)·채널 해석·gemini 호출·claude seam을 플랫폼 무관하게 확인한다.
"""
from __future__ import annotations

import pytest

from core.persona import (
    Persona,
    PersonaRegistry,
    ask,
    build_prompt,
    format_history,
)

GEM = Persona(key="k", label="L", system="SYS", model="m", temperature=0.3, memory_window=5)
NOSAVE = Persona(key="n", label="N", system="SYS", memory_window=0, save_history=False)
CLAUDE = Persona(key="e", label="E", system="SYS", provider="claude")
DEFAULT = Persona(key="default", label="일반", system="D")


class _FakeMemory:
    def __init__(self, turns=None):
        self._turns = turns or []
        self.appended: list = []

    def recent(self, channel_id, n=20):
        return list(self._turns)[-n:]

    def append(self, channel_id, role, content):
        self.appended.append((str(channel_id), role, content))


class _FakeGateway:
    def __init__(self, *, text="answer", raises=None, available=True):
        self._text = text
        self._raises = raises
        self.available = available
        self.calls: list = []

    def complete(self, system, prompt, *, model, temperature=0.4, json_mode=True):
        self.calls.append({"system": system, "prompt": prompt, "model": model,
                           "temperature": temperature, "json_mode": json_mode})
        if self._raises is not None:
            raise self._raises
        return self._text


# ── 순수: 프롬프트 구성 ──

def test_format_history_empty_and_turns():
    assert format_history([]) == ""
    out = format_history([("user", "hi"), ("assistant", "yo")])
    assert out.splitlines() == ["# 이전 대화 (오래된 → 최근)", "[USER] hi", "[ASSISTANT] yo"]


def test_build_prompt_question_only():
    assert build_prompt("", None, "q") == "# QUESTION\nq"


def test_build_prompt_with_history_and_dict_context():
    p = build_prompt("# 이전 대화", {"a": 1}, "q")
    assert p.startswith("# 이전 대화\n\n")
    assert "# CONTEXT" in p and '"a": 1' in p
    assert p.endswith("# QUESTION\nq")


def test_build_prompt_empty_context_skipped():
    assert "# CONTEXT" not in build_prompt("", {}, "q")   # falsy context → 생략


# ── 채널 해석 ──

def test_registry_resolves_by_channel_key_else_default():
    reg = PersonaRegistry({"k": GEM}, DEFAULT,
                          channel_key=lambda cid: "k" if cid == "100" else None)
    assert reg.resolve("100").key == "k"
    assert reg.resolve("999").key == "default"
    assert set(reg.all()) == {"k", "default"}


# ── ask: gemini 경로 ──

def test_ask_gemini_returns_text_and_saves_history():
    mem = _FakeMemory()
    gw = _FakeGateway(text="  answer  ")
    out = ask(GEM, "c1", "질문", memory=mem, gateway=gw)
    assert out == "answer"                                 # strip
    assert gw.calls[0]["json_mode"] is False               # 페르소나 대화는 JSON 모드 아님
    assert gw.calls[0]["model"] == "m" and gw.calls[0]["temperature"] == 0.3
    assert mem.appended == [("c1", "user", "질문"), ("c1", "assistant", "answer")]


def test_ask_includes_history_in_prompt():
    mem = _FakeMemory(turns=[("user", "earlier")])
    gw = _FakeGateway()
    ask(GEM, "c1", "질문", memory=mem, gateway=gw)
    assert "earlier" in gw.calls[0]["prompt"]              # 히스토리 포함


def test_ask_memory_window_zero_skips_history():
    mem = _FakeMemory(turns=[("user", "earlier")])
    gw = _FakeGateway()
    ask(NOSAVE, "c1", "질문", memory=mem, gateway=gw)
    assert "earlier" not in gw.calls[0]["prompt"]          # window=0 → 히스토리 없음
    assert mem.appended == []                              # save_history=False → 저장 안 함


def test_ask_unavailable_gateway_returns_error():
    mem = _FakeMemory()
    out = ask(GEM, "c1", "질문", memory=mem, gateway=_FakeGateway(available=False))
    assert "미가용" in out
    assert mem.appended == []


def test_ask_gateway_error_is_safe():
    mem = _FakeMemory()
    out = ask(GEM, "c1", "질문", memory=mem, gateway=_FakeGateway(raises=RuntimeError("x")))
    assert out.startswith("❌ 응답 생성 실패")
    assert mem.appended == []                              # 실패 시 히스토리 저장 안 함


# ── ask: claude seam ──

def test_ask_claude_without_runner_raises():
    with pytest.raises(NotImplementedError):
        ask(CLAUDE, "c1", "질문", memory=_FakeMemory())


def test_ask_claude_delegates_to_injected_runner():
    seen = {}

    def runner(persona, channel_id, user_text, extra_context):
        seen["persona"] = persona.key
        return "claude-result"

    out = ask(CLAUDE, "c1", "질문", memory=_FakeMemory(), agent_runner=runner)
    assert out == "claude-result" and seen["persona"] == "e"
