# -*- coding: utf-8 -*-
"""
core.gateway 테스트 — 조립점(관측 배선).

게이트웨이 생성을 한 곳으로 모아, 관측 sink를 켜면 default_* 로 만든 모든 게이트웨이가 호출을
계측함을 고정한다. 관측 전용(모델 덮지 않음)이라 감싼 게이트웨이의 available/응답은 위임된다.
"""
from __future__ import annotations

import pytest

from core.gateway import (
    clear_observer,
    default_gemini,
    default_openai,
    get_observer,
    set_observer,
    wrap,
)
from core.llm import LLMGateway
from core.routing import CallRecord, CostRouter


@pytest.fixture(autouse=True)
def _isolate_observer():
    """관측 sink는 프로세스 전역 → 각 테스트 전후로 초기화(격리)."""
    clear_observer()
    yield
    clear_observer()


class _FakeGateway:
    def __init__(self, *, reply="ok", available=True):
        self._reply = reply
        self._available = available
        self.calls: list[dict] = []

    @property
    def available(self):
        return self._available

    def complete(self, system, prompt, *, model, temperature=0.4, json_mode=True):
        self.calls.append({"model": model})
        return self._reply


# ── observer 레지스트리 ──

def test_observer_roundtrip():
    assert get_observer() is None
    sink = lambda rec: None
    set_observer(sink)
    assert get_observer() is sink
    clear_observer()
    assert get_observer() is None


# ── wrap: 관측 래핑 ──

def test_wrap_returns_cost_router_and_delegates():
    base = _FakeGateway(reply="hello", available=False)
    gw = wrap(base)
    assert isinstance(gw, CostRouter) and isinstance(gw, LLMGateway)
    assert gw.available is False                      # available 위임
    assert gw.complete("", "hi", model="m") == "hello"  # 응답 위임


def test_wrap_emits_to_active_observer():
    records: list[CallRecord] = []
    set_observer(records.append)
    wrap(_FakeGateway()).complete("", "요약해줘", model="gemini-2.5-flash")
    assert len(records) == 1
    assert records[0].model == "gemini-2.5-flash"     # 명시 모델 그대로(관측 전용, 안 덮음)
    assert records[0].cost_usd is not None            # 실단가 확보(2026-07-10) → 라이브 비용 켜짐


def test_wrap_leaves_unknown_model_unpriced():
    """단가표에 없는 모델은 여전히 미가격 — 비용을 지어내지 않는다."""
    records: list[CallRecord] = []
    set_observer(records.append)
    wrap(_FakeGateway()).complete("", "요약해줘", model="아직-단가-없는-모델")
    assert records[0].cost_usd is None
    assert records[0].in_tokens > 0                   # 토큰·티어는 계속 계측


def test_wrap_without_observer_is_silent():
    # observer 미설정이면 계측 결과를 버릴 뿐, 호출은 정상 동작.
    assert wrap(_FakeGateway(reply="r")).complete("", "hi", model="m") == "r"


def test_observer_set_after_wrap_is_not_retroactive():
    # wrap 시점의 observer가 고정된다(팩토리는 그 순간의 sink를 주입).
    gw = wrap(_FakeGateway())
    records: list[CallRecord] = []
    set_observer(records.append)          # wrap 이후 설정 → 이 게이트웨이엔 반영 안 됨
    gw.complete("", "hi", model="m")
    assert records == []


# ── default_* : 어댑터의 else GeminiGateway() 자리 대체 ──

def test_default_gemini_wraps_real_gateway_and_delegates_available(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    gw = default_gemini()
    assert isinstance(gw, LLMGateway)
    assert gw.available is False                       # 키 없음 → 위임된 available False
    assert default_gemini(api_key="k").available is True


def test_default_openai_wraps_real_gateway(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert default_openai().available is False
    assert default_openai(api_key="k").available is True
