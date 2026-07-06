# -*- coding: utf-8 -*-
"""
core.control 엔진 테스트 — 범용 intent 라우터(youtube를 모르는 토이 룰로 검증).

룰 매칭·화이트리스트 검증·LLM 폴백(게이트웨이 주입)을 플랫폼 무관하게 확인한다.
"""
from __future__ import annotations

import json
import re

from core.control import Command, IntentRouter, Rule, describe, dispatch

TOY_RULES = [
    Rule(re.compile(r"\bhi\b", re.I), "greet"),
    Rule(re.compile(r"add (\d+)", re.I), "add",
         lambda t: {"n": int(re.search(r"add (\d+)", t, re.I).group(1))}),
    Rule(re.compile(r"\bneedy\b", re.I), "needy",
         lambda t: Command(intent="unknown", source="rule", explain="needy but empty")),
]
KNOWN = {"greet", "add", "needy", "unknown"}


class _FakeGateway:
    def __init__(self, *, text=None, raises=None, available=True):
        self._text = text
        self._raises = raises
        self.available = available

    def complete(self, system, prompt, *, model, temperature=0.4, json_mode=True):
        if self._raises is not None:
            raise self._raises
        return self._text or "{}"


def _router(*, gateway=None):
    return IntentRouter(TOY_RULES, KNOWN, fallback_system="SYS", gateway=gateway)


# ── 룰 경로 ──

def test_parse_rule_matches_and_extracts_args():
    cmd = _router().parse_rule("please add 7 now")
    assert cmd.intent == "add"
    assert cmd.args == {"n": 7}
    assert cmd.source == "rule"
    assert cmd.confidence == 0.95
    assert cmd.explain.startswith("rule=")


def test_parse_rule_extract_can_override_to_unknown():
    cmd = _router().parse_rule("needy")
    assert cmd.intent == "unknown"
    assert cmd.explain == "needy but empty"


def test_parse_rule_none_on_no_match_or_empty():
    assert _router().parse_rule("nothing here") is None
    assert _router().parse_rule("   ") is None


# ── parse: 룰 → LLM 폴백 ──

def test_parse_prefers_rule_over_llm():
    gw = _FakeGateway(text=json.dumps({"intent": "greet"}))  # 안 불려야 함
    cmd = _router(gateway=gw).parse("hi there")
    assert cmd.intent == "greet" and cmd.source == "rule"


def test_parse_no_llm_returns_unknown_when_no_rule():
    cmd = _router().parse("blah", allow_llm=False)
    assert cmd.intent == "unknown" and cmd.source == "rule"


def test_parse_llm_maps_known_intent():
    gw = _FakeGateway(text=json.dumps(
        {"intent": "add", "args": {"n": 3}, "confidence": 0.8, "explain": "ai"}))
    cmd = _router(gateway=gw).parse("some free text")
    assert cmd.intent == "add" and cmd.args == {"n": 3}
    assert cmd.source == "llm" and cmd.confidence == 0.8


def test_parse_llm_rejects_intent_outside_whitelist():
    gw = _FakeGateway(text=json.dumps({"intent": "delete_everything"}))
    cmd = _router(gateway=gw).parse("free text")
    assert cmd.intent == "unknown" and cmd.confidence == 0.0


def test_parse_llm_unavailable_gateway():
    cmd = _router(gateway=_FakeGateway(available=False)).parse("free text")
    assert cmd.intent == "unknown"
    assert "미가용" in cmd.explain


def test_parse_llm_gateway_error_is_safe():
    cmd = _router(gateway=_FakeGateway(raises=RuntimeError("boom"))).parse("free text")
    assert cmd.intent == "unknown" and cmd.source == "llm"


# ── describe ──

def test_describe_rule_llm_unknown():
    assert "🟢룰" in describe(Command(intent="add", args={"n": 1}, source="rule"))
    assert "🟡AI" in describe(Command(intent="add", source="llm"))
    assert "인식 실패" in describe(Command(intent="unknown", explain="x"))


# ── dispatch (핸들러 주입) — 엔진은 intent가 뭘 하는지 모른다 ──

def test_dispatch_routes_to_injected_handler():
    handlers = {"add": lambda cmd: {"ok": True, "result": cmd.args["n"] + 1}}
    assert dispatch(Command(intent="add", args={"n": 6}), handlers)["result"] == 7


def test_dispatch_unknown_intent():
    assert dispatch(Command(intent="unknown"), {}) == {"ok": False, "msg": "unknown intent"}


def test_dispatch_missing_handler_is_unsupported():
    out = dispatch(Command(intent="greet"), {})
    assert out["ok"] is False and "미구현" in out["msg"] and "greet" in out["msg"]


def test_dispatch_is_platform_agnostic():
    # 엔진 dispatch는 youtube를 모른다 — 토이 intent/핸들러로도 동작한다.
    handlers = {"toy": lambda cmd: {"ok": True, "result": "done"}}
    assert dispatch(Command(intent="toy"), handlers)["result"] == "done"


def test_dispatch_custom_messages():
    out = dispatch(Command(intent="x"), {}, unsupported_msg="no handler")
    assert out["msg"] == "no handler: x"
