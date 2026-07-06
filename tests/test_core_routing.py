# -*- coding: utf-8 -*-
"""
core.routing 테스트 — 복잡도 분류·비용 라우팅·계측.

외부 API 없이 전부 결정적으로 돈다: 가짜 게이트웨이(호출 인자 기록)를 주입해 '어떤 모델로
갔는가'와 '무엇이 계측됐는가'를 고정한다. 라우터는 Claude를 몰라도(토이 라우트로도) 동작함을
함께 증명한다 = 엔진 범용성.

비용 공식 parity: `estimate_cost`는 운영 `claude_runner._estimate_cost`와 동일 수식이므로
원본 가격표 기준 기대값을 하드코딩해 대조한다.
"""
from __future__ import annotations

import json

import pytest

from core.llm import Completion, LLMGateway, Usage
from core.routing import (
    AUTO,
    DEFAULT_PRICING,
    CallRecord,
    Classification,
    CostRouter,
    Tier,
    aggregate,
    classify,
    estimate_cost,
    estimate_tokens,
    is_priced,
    jsonl_sink,
)


class _FakeGateway:
    """호출 인자를 기록하는 가짜 LLMGateway. 지정한 텍스트를 그대로 돌려준다."""

    def __init__(self, *, reply: str = "ok", available: bool = True):
        self._reply = reply
        self._available = available
        self.calls: list[dict] = []

    @property
    def available(self) -> bool:
        return self._available

    def complete(self, system, prompt, *, model, temperature=0.4, json_mode=True):
        self.calls.append({"system": system, "prompt": prompt, "model": model,
                           "temperature": temperature, "json_mode": json_mode})
        return self._reply


class _UsageGateway:
    """complete_ex로 실측 usage를 노출하는 가짜 게이트웨이(UsageAwareGateway)."""

    available = True

    def __init__(self, *, reply="ok", usage=Usage(100, 50)):
        self._reply = reply
        self._usage = usage
        self.calls: list = []

    def complete(self, system, prompt, *, model, temperature=0.4, json_mode=True):
        return self.complete_ex(system, prompt, model=model,
                                temperature=temperature, json_mode=json_mode).text

    def complete_ex(self, system, prompt, *, model, temperature=0.4, json_mode=True):
        self.calls.append({"model": model})
        return Completion(text=self._reply, usage=self._usage)


# 티어를 결정적으로 가르는 토이 라우트/가격(Claude 무관 = 엔진 범용성).
_TOY_ROUTE = {Tier.CHEAP: "toy-cheap", Tier.BALANCED: "toy-mid", Tier.DEEP: "toy-deep"}
_TOY_PRICING = {
    "toy-cheap": {"in": 1.0, "out": 2.0},
    "toy-mid": {"in": 10.0, "out": 20.0},
    "toy-deep": {"in": 100.0, "out": 200.0},
}


# ── estimate_cost: 원본 공식 parity ──────────────────────────────────────────

def test_estimate_cost_matches_original_formula():
    # opus-4-7: (1000*15 + 1000*75)/1e6 = 0.09  (원본 _PRICING·_estimate_cost 상속)
    assert estimate_cost("claude-opus-4-7", 1000, 1000) == pytest.approx(0.09)
    # haiku: (2000*1 + 500*5)/1e6 = 0.0045
    assert estimate_cost("claude-haiku-4-5", 2000, 500) == pytest.approx(0.0045)


def test_estimate_cost_unknown_model_uses_fallback_price():
    # 미등재 모델 → sonnet급 폴백 {in:3, out:15} (원본 _estimate_cost 기본값과 동일)
    assert estimate_cost("unknown", 1000, 1000) == pytest.approx((3.0 + 15.0) / 1e6 * 1000)


def test_estimate_cost_pricing_is_injectable():
    assert estimate_cost("toy-deep", 100, 100, pricing=_TOY_PRICING) == pytest.approx(
        (100 * 100.0 + 100 * 200.0) / 1e6)


def test_is_priced_flags_table_membership():
    assert is_priced("claude-opus-4-7") is True            # DEFAULT_PRICING에 있음
    assert is_priced("gemini-2.5-flash") is False          # 단가 미확보 → 미가격
    assert is_priced("toy-deep", _TOY_PRICING) is True


# ── estimate_tokens: 근사 ────────────────────────────────────────────────────

def test_estimate_tokens_char_over_four():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 10) == 3          # ceil(10/4)


# ── classify: 복잡도 휴리스틱 ─────────────────────────────────────────────────

def test_classify_deep_keyword_routes_deep():
    c = classify("", "이 코드를 리팩터하고 아키텍처를 분석해줘")
    assert c.tier is Tier.DEEP
    assert c.score >= 2


def test_classify_cheap_keyword_routes_cheap():
    c = classify("", "다음 문단을 요약하고 태그를 추출해줘")
    assert c.tier is Tier.CHEAP
    assert c.score <= -2


def test_classify_neutral_defaults_balanced():
    c = classify("", "안녕, 오늘 날씨 어때?")
    assert c.tier is Tier.BALANCED


def test_classify_override_wins():
    c = classify("분석 설계 디버그", "리팩터", override=Tier.CHEAP)
    assert c.tier is Tier.CHEAP
    assert c.reasons == ["override"]


def test_classify_json_mode_nudges_cheaper():
    base = classify("", "상태를 알려줘")
    withjson = classify("", "상태를 알려줘", json_mode=True)
    assert withjson.score == base.score - 1


def test_classify_long_prompt_nudges_up():
    long = classify("", "설명해줘 " + "x" * 2500)
    assert "long" in long.reasons


def test_classify_reasons_are_explainable():
    c = classify("", "코드를 디버그하고 분석해줘")
    assert any("deep-kw" in r for r in c.reasons)


# ── CostRouter: 라우팅 + 위임 ─────────────────────────────────────────────────

def test_router_is_llm_gateway():
    assert isinstance(CostRouter(_FakeGateway()), LLMGateway)


def test_router_available_delegates():
    assert CostRouter(_FakeGateway(available=False)).available is False
    assert CostRouter(_FakeGateway(available=True)).available is True


def test_router_auto_routes_deep_to_deep_model():
    gw = _FakeGateway(reply="r")
    router = CostRouter(gw, route=_TOY_ROUTE, pricing=_TOY_PRICING)
    router.complete("", "아키텍처를 설계하고 분석해줘", model=AUTO)
    assert gw.calls[0]["model"] == "toy-deep"       # 감싼 게이트웨이엔 라우팅된 모델이 감


def test_router_auto_routes_cheap_to_cheap_model():
    gw = _FakeGateway()
    router = CostRouter(gw, route=_TOY_ROUTE, pricing=_TOY_PRICING)
    router.complete("", "요약하고 분류해줘", model=AUTO)
    assert gw.calls[0]["model"] == "toy-cheap"


def test_router_empty_model_also_routes():
    gw = _FakeGateway()
    router = CostRouter(gw, route=_TOY_ROUTE, pricing=_TOY_PRICING)
    router.complete("", "분석 설계", model="")
    assert gw.calls[0]["model"] == "toy-deep"


def test_router_explicit_model_bypasses_routing():
    gw = _FakeGateway()
    router = CostRouter(gw, route=_TOY_ROUTE, pricing=_TOY_PRICING)
    router.complete("", "아키텍처 분석", model="pinned-model")
    assert gw.calls[0]["model"] == "pinned-model"   # 명시 모델은 그대로 통과


def test_router_passes_through_temperature_and_json_mode():
    gw = _FakeGateway()
    router = CostRouter(gw, route=_TOY_ROUTE, pricing=_TOY_PRICING)
    router.complete("S", "P", model=AUTO, temperature=0.9, json_mode=False)
    assert gw.calls[0]["temperature"] == 0.9
    assert gw.calls[0]["json_mode"] is False


def test_router_returns_gateway_text():
    router = CostRouter(_FakeGateway(reply="hello"), route=_TOY_ROUTE, pricing=_TOY_PRICING)
    assert router.complete("", "hi", model=AUTO) == "hello"


def test_route_for_previews_without_calling():
    gw = _FakeGateway()
    router = CostRouter(gw, route=_TOY_ROUTE, pricing=_TOY_PRICING)
    model, cls = router.route_for("", "설계 분석", model=AUTO)
    assert model == "toy-deep" and cls.tier is Tier.DEEP
    assert gw.calls == []                            # 미리보기는 호출하지 않음
    model2, cls2 = router.route_for("", "x", model="fixed")
    assert model2 == "fixed" and cls2 is None


# ── 관측: sink · CallRecord · aggregate ──────────────────────────────────────

def test_router_emits_call_record_to_sink():
    records: list[CallRecord] = []
    gw = _FakeGateway(reply="abcdefgh")              # 8자 → 2 out-tokens
    router = CostRouter(gw, route=_TOY_ROUTE, pricing=_TOY_PRICING, sink=records.append)
    router.complete("SYS", "요약 분류", model=AUTO)
    assert len(records) == 1
    rec = records[0]
    assert rec.model == "toy-cheap" and rec.tier == "cheap"
    assert rec.out_tokens == 2
    assert rec.cost_usd == pytest.approx(estimate_cost("toy-cheap", rec.in_tokens, 2, pricing=_TOY_PRICING))


def test_router_records_explicit_tier_label():
    records: list[CallRecord] = []
    router = CostRouter(_FakeGateway(), route=_TOY_ROUTE, pricing=_TOY_PRICING, sink=records.append)
    router.complete("", "x", model="pinned")
    assert records[0].tier == "explicit"
    assert records[0].reasons == ["explicit-model"]


def test_router_unpriced_model_records_none_cost():
    # 단가표에 없는 모델(예: Gemini)은 비용을 지어내지 않고 None으로 남긴다.
    records: list[CallRecord] = []
    router = CostRouter(_FakeGateway(), sink=records.append)   # DEFAULT_PRICING = Claude만
    router.complete("", "요약해줘", model="gemini-2.5-flash")
    assert records[0].model == "gemini-2.5-flash"
    assert records[0].cost_usd is None                        # 미가격
    assert records[0].in_tokens > 0                           # 토큰·티어는 여전히 계측


def test_sink_failure_does_not_break_call():
    def boom(rec):
        raise RuntimeError("sink down")

    router = CostRouter(_FakeGateway(reply="ok"), route=_TOY_ROUTE, pricing=_TOY_PRICING, sink=boom)
    assert router.complete("", "hi", model=AUTO) == "ok"   # 관측 실패가 주 경로를 막지 않음


def test_no_sink_is_noop():
    router = CostRouter(_FakeGateway(reply="ok"), route=_TOY_ROUTE, pricing=_TOY_PRICING)
    assert router.complete("", "hi", model=AUTO) == "ok"


def test_jsonl_sink_writes_lines_with_injected_clock(tmp_path):
    path = tmp_path / "logs" / "cost.jsonl"
    sink = jsonl_sink(path, clock=lambda: "2026-07-06T00:00:00")
    router = CostRouter(_FakeGateway(reply="r"), route=_TOY_ROUTE, pricing=_TOY_PRICING, sink=sink)
    router.complete("", "설계 분석", model=AUTO)
    router.complete("", "요약", model=AUTO)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    row = json.loads(lines[0])
    assert row["model"] == "toy-deep" and row["ts"] == "2026-07-06T00:00:00"
    assert "cost_usd" in row


def test_aggregate_totals_and_breakdown():
    records = [
        CallRecord("toy-deep", "deep", 4, 100, 50, 0.02, 0.4, True, ["deep-kw x1"]),
        CallRecord("toy-cheap", "cheap", -2, 20, 10, 0.001, 0.4, True, ["cheap-kw x1"]),
        CallRecord("toy-deep", "deep", 2, 200, 80, 0.05, 0.4, True, []),
    ]
    rep = aggregate(records)
    assert rep["calls"] == 3
    assert rep["in_tokens"] == 320 and rep["out_tokens"] == 140
    assert rep["cost_usd"] == pytest.approx(0.071)
    assert rep["by_model"]["toy-deep"]["calls"] == 2
    assert rep["by_tier"]["cheap"]["calls"] == 1


def test_aggregate_empty():
    rep = aggregate([])
    assert rep["calls"] == 0 and rep["cost_usd"] == 0.0 and rep["by_model"] == {}
    assert rep["unpriced_calls"] == 0


def test_aggregate_skips_unpriced_from_cost():
    records = [
        CallRecord("claude-opus-4-7", "deep", 4, 100, 50, 0.02, 0.4, True, []),
        CallRecord("gemini-2.5-flash", "explicit", 0, 20, 10, None, 0.4, True, []),  # 미가격
    ]
    rep = aggregate(records)
    assert rep["calls"] == 2 and rep["unpriced_calls"] == 1
    assert rep["cost_usd"] == pytest.approx(0.02)             # 미가격은 총액에서 제외
    assert rep["in_tokens"] == 120                            # 토큰은 미가격도 합산
    assert rep["by_model"]["gemini-2.5-flash"]["calls"] == 1
    assert rep["by_model"]["gemini-2.5-flash"]["cost_usd"] == 0.0


# ── 기본 라우트/가격은 출처 붙은(=가격표에 있는) 모델만 가리킨다 ──

def test_default_route_targets_are_all_priced():
    from core.routing import DEFAULT_ROUTE

    for model in DEFAULT_ROUTE.values():
        assert model in DEFAULT_PRICING          # 기본 라우트의 모든 모델은 단가 출처가 있다


# ── 실측 usage: provider가 주면 실측, 아니면 근사 ──

def test_router_uses_measured_usage_when_gateway_exposes_it():
    records: list[CallRecord] = []
    gw = _UsageGateway(usage=Usage(100, 50))
    router = CostRouter(gw, route=_TOY_ROUTE, pricing=_TOY_PRICING, sink=records.append)
    router.complete("아주 긴 시스템 프롬프트" * 50, "설계 분석", model=AUTO)
    r = records[0]
    assert r.in_tokens == 100 and r.out_tokens == 50   # 근사 아님 — provider 실측 그대로
    assert r.token_source == "measured"


def test_router_falls_back_to_estimate_without_usage_gateway():
    records: list[CallRecord] = []
    router = CostRouter(_FakeGateway(reply="abcd"), route=_TOY_ROUTE,
                        pricing=_TOY_PRICING, sink=records.append)
    router.complete("", "요약", model=AUTO)
    assert records[0].token_source == "estimated"        # complete_ex 없음 → 문자수 근사


def test_router_usage_none_falls_back_to_estimate():
    records: list[CallRecord] = []
    gw = _UsageGateway(reply="abcdefgh", usage=None)      # usage-aware지만 usage 미노출
    router = CostRouter(gw, route=_TOY_ROUTE, pricing=_TOY_PRICING, sink=records.append)
    router.complete("", "요약", model=AUTO)
    assert records[0].token_source == "estimated"
    assert records[0].out_tokens == 2                    # 8자 → ceil(8/4) 근사


def test_measured_cost_uses_real_tokens():
    records: list[CallRecord] = []
    gw = _UsageGateway(usage=Usage(1000, 500))
    router = CostRouter(gw, route=_TOY_ROUTE, pricing=_TOY_PRICING, sink=records.append)
    router.complete("", "설계 분석", model=AUTO)          # → toy-deep(priced)
    assert records[0].cost_usd == pytest.approx(
        estimate_cost("toy-deep", 1000, 500, pricing=_TOY_PRICING))


def test_aggregate_counts_measured_calls():
    recs = [
        CallRecord("m", "deep", 0, 10, 5, 0.001, 0.4, True, [], "measured"),
        CallRecord("m", "deep", 0, 10, 5, 0.001, 0.4, True, [], "estimated"),
    ]
    assert aggregate(recs)["measured_calls"] == 1
