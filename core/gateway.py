# -*- coding: utf-8 -*-
"""
core.gateway — 게이트웨이 조립점(composition root) + 관측 배선.

어댑터·엔진은 게이트웨이를 직접 `GeminiGateway()`로 new 하지 않고 이 팩토리를 거친다.
그러면 `CostRouter` 관측 래핑을 **한 곳에서** 켜고 끌 수 있다(호출부 코드 불변):

    # 런타임 진입점(예: CLI/봇)에서 한 번:
    from core.gateway import set_observer
    from core.routing import jsonl_sink
    set_observer(jsonl_sink("logs/llm_cost.jsonl", clock=lambda: datetime.now().isoformat()))

    # 이후 어댑터가 default_gemini()로 만든 게이트웨이는 전부 호출을 계측한다.

설계 메모:
  - **관측 전용 배선**이다. 감싸도 `model`을 덮지 않으므로(명시 모델은 그대로 통과) 기존 어댑터의
    의도된 모델 선택·동작은 **불변**이다. 라우팅(`model="auto"`)은 호출부가 명시적으로 옵트인할 때만.
  - **비용 정직성**: 라우터는 단가표에 있는 모델만 비용을 매기고, 없으면 미가격(None)으로 남긴다
    (`core.routing.is_priced`). Gemini·OpenAI·Claude 실단가는 2026-07-10에 프로바이더 공식
    문서로 확인해 `DEFAULT_PRICING`에 등록됐다 — 라이브 경로의 비용이 켜져 있다. 표에 없는
    모델은 여전히 미가격(None)으로 남고, 비용을 지어내지 않는다.
  - 관측 sink 기본값은 없음(no-op) — `set_observer`로 켜기 전에는 래퍼가 계측만 하고 버린다.
"""
from __future__ import annotations

from typing import Any

from core.llm import GeminiGateway, LLMGateway, OpenAIGateway
from core.routing import DEFAULT_PRICING, DEFAULT_ROUTE, CostRouter, Sink, Tier

# 프로세스 전역 관측 sink. 진입점이 set_observer로 켠다. 기본 None = 계측 결과를 버림.
_observer: Sink | None = None


def set_observer(sink: Sink | None) -> None:
    """관측 sink를 설정한다(예: `jsonl_sink`). None이면 관측을 끈다."""
    global _observer
    _observer = sink


def get_observer() -> Sink | None:
    """현재 관측 sink를 돌려준다(테스트·조회용)."""
    return _observer


def clear_observer() -> None:
    """관측 sink를 해제한다(테스트 격리·명시적 끄기)."""
    global _observer
    _observer = None


def wrap(base: LLMGateway, *,
         route: dict[Tier, str] = DEFAULT_ROUTE,
         pricing: dict[str, dict[str, Any]] = DEFAULT_PRICING) -> CostRouter:
    """임의의 게이트웨이를 현재 관측 sink가 달린 `CostRouter`로 감싼다(관측 전용 기본값)."""
    return CostRouter(base, route=route, pricing=pricing, sink=_observer)


def default_gemini(*, api_key: str | None = None) -> CostRouter:
    """관측 래핑된 기본 Gemini 게이트웨이. 어댑터의 `else GeminiGateway()` 자리를 대체한다."""
    return wrap(GeminiGateway(api_key=api_key))


def default_openai(*, api_key: str | None = None) -> CostRouter:
    """관측 래핑된 기본 OpenAI 게이트웨이. 어댑터의 `else OpenAIGateway()` 자리를 대체한다."""
    return wrap(OpenAIGateway(api_key=api_key))
