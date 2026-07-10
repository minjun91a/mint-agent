# -*- coding: utf-8 -*-
"""
core.gateway 테스트 — 조립점(관측 배선).

게이트웨이 생성을 한 곳으로 모아, 관측 sink를 켜면 default_* 로 만든 모든 게이트웨이가 호출을
계측함을 고정한다. 관측 전용(모델 덮지 않음)이라 감싼 게이트웨이의 available/응답은 위임된다.
"""
from __future__ import annotations

import pytest

from core.gateway import (
    CLAUDE_ROUTE,
    GEMINI_ROUTE,
    OPENAI_ROUTE,
    clear_observer,
    default_claude,
    default_gemini,
    default_openai,
    get_observer,
    set_observer,
    wrap,
)
from core.llm import LLMGateway
from core.routing import AUTO, CallRecord, CostRouter, DEFAULT_PRICING, Tier, is_priced


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


# ── auto 라우팅: 라우트는 프로바이더와 짝을 이룬다 ──────────────────────────
# 회귀 방지. 이전에는 세 팩토리가 전부 DEFAULT_ROUTE(Claude 모델)를 썼다. 관측 전용이라
# 아무도 auto를 넘기지 않아 드러나지 않았지만, default_gemini()에 auto를 주면
# claude-haiku-4-5-20251001을 Gemini SDK로 넘기는 잠복 버그였다.


def test_gemini_auto_routes_within_gemini():
    gw = default_gemini(api_key="k")
    cheap, cls_c = gw.route_for("", "이 문단을 요약해줘", model=AUTO)
    deep, cls_d = gw.route_for("", "이 아키텍처를 분석·리팩터해줘", model=AUTO)

    assert cheap == "gemini-2.5-flash-lite" and cls_c.tier is Tier.CHEAP
    assert deep == "gemini-2.5-pro" and cls_d.tier is Tier.DEEP


def test_gemini_auto_never_picks_a_claude_model():
    """회귀 방지: 프로바이더를 넘나드는 모델명이 나오면 안 된다."""
    gw = default_gemini(api_key="k")
    for prompt in ("요약해줘", "설계를 분석해줘", "그냥 답해줘"):
        chosen, _ = gw.route_for("", prompt, model=AUTO)
        assert chosen.startswith("gemini-")


def test_claude_auto_routes_within_claude():
    gw = default_claude(api_key="k")
    for prompt in ("요약해줘", "설계를 분석해줘", "그냥 답해줘"):
        chosen, _ = gw.route_for("", prompt, model=AUTO)
        assert chosen.startswith("claude-")


def test_openai_auto_raises_instead_of_guessing():
    """라우트가 비었으면 조용히 엉뚱한 모델로 가지 않고 명시적으로 실패한다."""
    with pytest.raises(ValueError, match="auto 라우팅 불가"):
        default_openai(api_key="k").route_for("", "요약해줘", model=AUTO)


def test_openai_explicit_model_still_works():
    """auto만 막힌다 — 명시 모델 경로(gpt-4o-mini)는 그대로 통과·계측된다."""
    chosen, cls = default_openai(api_key="k").route_for("", "요약해줘", model="gpt-4o-mini")
    assert chosen == "gpt-4o-mini" and cls is None


def test_every_routed_model_has_a_real_price():
    """라우트의 모든 모델은 실단가가 있어야 한다 — 라우팅의 비용 절감이 계측되지 않으면 무의미."""
    for route in (GEMINI_ROUTE, CLAUDE_ROUTE):
        for tier, model in route.items():
            assert is_priced(model), f"{tier.value} 티어 모델 {model}에 단가가 없다"


def test_openai_route_is_intentionally_empty():
    assert OPENAI_ROUTE == {}      # 티어를 나눌 모델이 없다 — 없는 라우트를 지어내지 않는다


def test_gemini_route_cheap_is_actually_cheaper_than_deep():
    """라우팅이 값을 하려면 티어 간 단가 격차가 실제로 있어야 한다."""
    cheap = DEFAULT_PRICING[GEMINI_ROUTE[Tier.CHEAP]]
    deep = DEFAULT_PRICING[GEMINI_ROUTE[Tier.DEEP]]
    assert cheap["in"] < deep["in"] and cheap["out"] < deep["out"]


def test_auto_routing_is_still_opt_in():
    """기본은 관측 전용 — 명시 모델은 라우팅되지 않고 그대로 통과한다."""
    chosen, cls = default_gemini(api_key="k").route_for("", "설계를 분석해줘",
                                                        model="gemini-2.5-flash")
    assert chosen == "gemini-2.5-flash"   # DEEP으로 승격되지 않음
    assert cls is None
