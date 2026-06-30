# -*- coding: utf-8 -*-
"""
core.llm 게이트웨이 테스트.

`GeminiGateway`는 `google.generativeai`를 호출하는 **유일한 곳**이다. 실제 SDK/네트워크 없이
그 한 곳의 동작(생성 설정 빌드·응답 strip·미가용 판정)을 고정한다 → 가짜 genai 모듈을 주입.
"""
from __future__ import annotations

import sys
import types

from core.llm import GeminiGateway, LLMGateway


def _install_fake_genai(monkeypatch, *, text: str | None):
    """가짜 `google.generativeai`를 sys.modules에 심고, 받은 인자를 기록할 dict를 돌려준다."""
    calls: dict = {}

    def configure(api_key=None):
        calls["api_key"] = api_key

    class _Resp:
        def __init__(self, t):
            self.text = t

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
