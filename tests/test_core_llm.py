# -*- coding: utf-8 -*-
"""
core.llm 게이트웨이 테스트.

`GeminiGateway`는 `google.generativeai`를 호출하는 **유일한 곳**이다. 실제 SDK/네트워크 없이
그 한 곳의 동작(생성 설정 빌드·응답 strip·미가용 판정)을 고정한다 → 가짜 genai 모듈을 주입.
"""
from __future__ import annotations

import sys
import types

from core.llm import (
    ClaudeGateway,
    Completion,
    GeminiGateway,
    LLMGateway,
    OpenAIGateway,
    Usage,
    UsageAwareGateway,
)


class _NS:
    """테스트용 간이 네임스페이스(속성 주머니) — provider usage 객체 흉내."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _install_fake_genai(monkeypatch, *, text: str | None, usage=None):
    """가짜 `google.generativeai`를 sys.modules에 심고, 받은 인자를 기록할 dict를 돌려준다."""
    calls: dict = {}

    def configure(api_key=None):
        calls["api_key"] = api_key

    class _Resp:
        def __init__(self, t):
            self.text = t
            if usage is not None:
                self.usage_metadata = _NS(prompt_token_count=usage[0],
                                          candidates_token_count=usage[1])

    class GenerativeModel:
        def __init__(self, *, model_name, system_instruction, generation_config):
            calls["model_name"] = model_name
            calls["system_instruction"] = system_instruction
            calls["generation_config"] = generation_config

        def generate_content(self, prompt):
            calls["prompt"] = prompt
            return _Resp(text)

    fake = types.ModuleType("google.generativeai")
    fake.configure = configure
    fake.GenerativeModel = GenerativeModel

    google_pkg = types.ModuleType("google")
    google_pkg.generativeai = fake  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake)
    return calls


# ── available: 호출 가능 여부 판정 ──

def test_available_true_with_explicit_key():
    assert GeminiGateway(api_key="secret").available is True


def test_available_false_with_empty_explicit_key():
    assert GeminiGateway(api_key="").available is False


def test_available_reads_env_when_no_explicit_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert GeminiGateway().available is False
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert GeminiGateway().available is True


def test_satisfies_llm_gateway_protocol():
    assert isinstance(GeminiGateway(), LLMGateway)


# ── complete: genai 설정 빌드 & 응답 처리 ──

def test_complete_builds_json_config_and_strips(monkeypatch):
    calls = _install_fake_genai(monkeypatch, text='  {"x": 1}\n')
    out = GeminiGateway(api_key="kkk").complete(
        "SYS", "PROMPT", model="gemini-x", temperature=0.6)
    assert out == '{"x": 1}'                       # 앞뒤 공백 strip
    assert calls["api_key"] == "kkk"
    assert calls["model_name"] == "gemini-x"
    assert calls["system_instruction"] == "SYS"
    assert calls["prompt"] == "PROMPT"
    assert calls["generation_config"] == {
        "temperature": 0.6, "response_mime_type": "application/json"}


def test_complete_json_mode_false_omits_mime(monkeypatch):
    calls = _install_fake_genai(monkeypatch, text="plain")
    out = GeminiGateway(api_key="k").complete(
        "S", "P", model="m", temperature=0.2, json_mode=False)
    assert out == "plain"
    assert calls["generation_config"] == {"temperature": 0.2}
    assert "response_mime_type" not in calls["generation_config"]


def test_complete_empty_response_falls_back_to_empty_object(monkeypatch):
    _install_fake_genai(monkeypatch, text=None)
    out = GeminiGateway(api_key="k").complete("S", "P", model="m")
    assert out == "{}"


def test_complete_uses_env_key_when_not_explicit(monkeypatch):
    calls = _install_fake_genai(monkeypatch, text="{}")
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    GeminiGateway().complete("S", "P", model="m")
    assert calls["api_key"] == "env-key"


# ── OpenAIGateway: 프로바이더 #2 — 같은 포트, 다른 SDK 매핑 ──

def _install_fake_openai(monkeypatch, *, content: str | None, usage=None):
    """가짜 `openai`를 sys.modules에 심고, chat.completions.create 인자를 기록한다."""
    calls: dict = {}

    class _Msg:
        def __init__(self, c):
            self.content = c

    class _Choice:
        def __init__(self, c):
            self.message = _Msg(c)

    class _Resp:
        def __init__(self, c):
            self.choices = [_Choice(c)]
            if usage is not None:
                self.usage = _NS(prompt_tokens=usage[0], completion_tokens=usage[1])

    class _Completions:
        def create(self, **kwargs):
            calls.update(kwargs)
            return _Resp(content)

    class _Chat:
        def __init__(self):
            self.completions = _Completions()

    class OpenAI:
        def __init__(self, *, api_key=None):
            calls["api_key"] = api_key
            self.chat = _Chat()

    fake = types.ModuleType("openai")
    fake.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", fake)
    return calls


def test_openai_available_reads_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert OpenAIGateway().available is False
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    assert OpenAIGateway().available is True
    assert isinstance(OpenAIGateway(), LLMGateway)


def test_openai_complete_maps_port_to_chat_messages(monkeypatch):
    calls = _install_fake_openai(monkeypatch, content='  {"y": 2}\n')
    out = OpenAIGateway(api_key="kkk").complete(
        "SYS", "PROMPT", model="gpt-x", temperature=0.9)
    assert out == '{"y": 2}'                       # strip
    assert calls["api_key"] == "kkk"
    assert calls["model"] == "gpt-x"
    assert calls["temperature"] == 0.9
    assert calls["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "PROMPT"},
    ]
    assert calls["response_format"] == {"type": "json_object"}


def test_openai_complete_json_mode_false_omits_response_format(monkeypatch):
    calls = _install_fake_openai(monkeypatch, content="plain")
    out = OpenAIGateway(api_key="k").complete("S", "P", model="m", json_mode=False)
    assert out == "plain"
    assert "response_format" not in calls


def test_openai_complete_empty_falls_back_to_empty_object(monkeypatch):
    _install_fake_openai(monkeypatch, content=None)
    assert OpenAIGateway(api_key="k").complete("S", "P", model="m") == "{}"


# ── ClaudeGateway: 프로바이더 #3 — messages API 매핑 ──

def _install_fake_anthropic(monkeypatch, *, text: str | None, usage=None):
    """가짜 `anthropic`을 sys.modules에 심고, messages.create 인자를 기록한다."""
    calls: dict = {}

    class _Block:
        def __init__(self, t):
            self.text = t

    class _Resp:
        def __init__(self, t):
            self.content = [] if t is None else [_Block(t)]
            if usage is not None:
                self.usage = _NS(input_tokens=usage[0], output_tokens=usage[1])

    class _Messages:
        def create(self, **kwargs):
            calls.update(kwargs)
            return _Resp(text)

    class Anthropic:
        def __init__(self, *, api_key=None, default_headers=None):
            calls["api_key"] = api_key
            calls["default_headers"] = default_headers
            self.messages = _Messages()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return calls


def test_claude_available_reads_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ClaudeGateway().available is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert ClaudeGateway().available is True
    assert isinstance(ClaudeGateway(), LLMGateway)


def test_claude_complete_maps_port_to_messages(monkeypatch):
    calls = _install_fake_anthropic(monkeypatch, text='  {"z": 3}\n')
    out = ClaudeGateway(api_key="kkk").complete(
        "SYS", "PROMPT", model="claude-x", temperature=0.5, json_mode=False)
    assert out == '{"z": 3}'                                # strip
    assert calls["api_key"] == "kkk"
    assert calls["model"] == "claude-x"
    assert calls["temperature"] == 0.5
    assert calls["system"] == "SYS"                         # json_mode=False → 지시 없음
    assert calls["messages"] == [{"role": "user", "content": "PROMPT"}]
    assert calls["default_headers"] == {"User-Agent": "mint-agent/1.0"}
    assert "max_tokens" in calls                            # messages API 필수


def test_claude_json_mode_appends_instruction(monkeypatch):
    calls = _install_fake_anthropic(monkeypatch, text="{}")
    ClaudeGateway(api_key="k").complete("SYS", "P", model="m", json_mode=True)
    assert calls["system"].startswith("SYS")
    assert "JSON" in calls["system"]                        # system에 JSON-only 지시 덧붙임


def test_claude_complete_empty_falls_back_to_empty_object(monkeypatch):
    _install_fake_anthropic(monkeypatch, text=None)
    assert ClaudeGateway(api_key="k").complete("S", "P", model="m") == "{}"


# ── complete_ex: 실측 usage 노출 (하위호환 확장 포트) ──

def test_gateways_are_usage_aware():
    for gw in (GeminiGateway(), OpenAIGateway(), ClaudeGateway()):
        assert isinstance(gw, UsageAwareGateway)


def test_gemini_complete_ex_extracts_usage(monkeypatch):
    _install_fake_genai(monkeypatch, text='{"x":1}', usage=(120, 34))
    comp = GeminiGateway(api_key="k").complete_ex("S", "P", model="m")
    assert isinstance(comp, Completion) and comp.text == '{"x":1}'
    assert comp.usage == Usage(120, 34)


def test_openai_complete_ex_extracts_usage(monkeypatch):
    _install_fake_openai(monkeypatch, content="hi", usage=(50, 12))
    comp = OpenAIGateway(api_key="k").complete_ex("S", "P", model="m")
    assert comp.text == "hi" and comp.usage == Usage(50, 12)


def test_claude_complete_ex_extracts_usage(monkeypatch):
    _install_fake_anthropic(monkeypatch, text="ok", usage=(200, 80))
    comp = ClaudeGateway(api_key="k").complete_ex("S", "P", model="m")
    assert comp.text == "ok" and comp.usage == Usage(200, 80)


def test_complete_ex_usage_none_when_provider_omits(monkeypatch):
    # provider가 usage를 안 주면 None (지어내지 않음) — 라우터는 근사로 폴백한다.
    _install_fake_openai(monkeypatch, content="hi")   # usage 미지정
    assert OpenAIGateway(api_key="k").complete_ex("S", "P", model="m").usage is None


def test_complete_still_returns_str_via_complete_ex(monkeypatch):
    # 하위호환: complete는 여전히 str만 반환(complete_ex.text 위임).
    _install_fake_genai(monkeypatch, text="  plain  ", usage=(1, 2))
    assert GeminiGateway(api_key="k").complete("S", "P", model="m") == "plain"
