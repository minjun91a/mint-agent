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
  - **기본은 관측 전용**이다. 감싸도 `model`을 덮지 않으므로(명시 모델은 그대로 통과) 기존 어댑터의
    의도된 모델 선택·동작은 **불변**이다. 라우팅(`model="auto"`)은 호출부가 명시적으로 옵트인할 때만.
  - **라우트는 프로바이더와 짝을 이룬다.** 이전에는 세 팩토리가 모두 `DEFAULT_ROUTE`(Claude 모델)를
    썼다. 관측 전용이라 아무도 `auto`를 넘기지 않아 드러나지 않았지만, `default_gemini()`에 `auto`를
    주면 **`claude-haiku-4-5-20251001`을 Gemini SDK로 넘기는** 잠복 버그였다(2026-07-10 재현·교정).
    이제 팩토리마다 자기 프로바이더의 라우트를 붙인다.
  - **비용 정직성**: 라우터는 단가표에 있는 모델만 비용을 매기고, 없으면 미가격(None)으로 남긴다
    (`core.routing.is_priced`). Gemini·OpenAI·Claude 실단가는 2026-07-10에 프로바이더 공식
    문서로 확인해 `DEFAULT_PRICING`에 등록됐다 — 라이브 경로의 비용이 켜져 있다. 표에 없는
    모델은 여전히 미가격(None)으로 남고, 비용을 지어내지 않는다.
  - 관측 sink 기본값은 없음(no-op) — `set_observer`로 켜기 전에는 래퍼가 계측만 하고 버린다.
"""
from __future__ import annotations

from typing import Any

from core.llm import ClaudeGateway, GeminiGateway, LLMGateway, OpenAIGateway
from core.routing import DEFAULT_PRICING, DEFAULT_ROUTE, CostRouter, Sink, Tier

# ── 프로바이더별 auto 라우트 ───────────────────────────────────────────────────
# 각 라우트의 모든 모델은 `DEFAULT_PRICING`에 실단가가 있다(그래야 라우팅의 비용 절감이 계측된다).

#: Claude 라우트. `core.routing.DEFAULT_ROUTE`와 같다(haiku → sonnet → opus).
CLAUDE_ROUTE: dict[Tier, str] = DEFAULT_ROUTE

#: Gemini 라우트. 같은 프로바이더 안에서 티어링하므로 auto를 켜도 SDK가 바뀌지 않는다.
#: 단가 격차: flash-lite 0.10/0.40 → pro 1.25/10.00 (입력 12.5배·출력 25배)
GEMINI_ROUTE: dict[Tier, str] = {
    Tier.CHEAP: "gemini-2.5-flash-lite",
    Tier.BALANCED: "gemini-2.5-flash",
    Tier.DEEP: "gemini-2.5-pro",
}

#: OpenAI 라우트는 **비어 있다** — 이 프로젝트가 쓰는 OpenAI 모델은 `gpt-4o-mini` 하나뿐이라
#: 티어를 나눌 대상이 없다. 없는 라우트를 지어내지 않는다. `auto`를 넘기면 `CostRouter`가
#: 명시적 에러를 낸다(조용히 엉뚱한 모델로 가지 않는다). 상위 모델 단가를 확보하면 채우면 된다.
OPENAI_ROUTE: dict[Tier, str] = {}

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
    """임의의 게이트웨이를 현재 관측 sink가 달린 `CostRouter`로 감싼다(관측 전용 기본값).

    `route`는 **감싼 게이트웨이의 프로바이더와 맞아야** `model="auto"`가 안전하다.
    프로바이더별 팩토리(`default_*`)를 쓰면 알아서 짝이 맞는다.
    """
    return CostRouter(base, route=route, pricing=pricing, sink=_observer)


def default_gemini(*, api_key: str | None = None) -> CostRouter:
    """관측 래핑된 기본 Gemini 게이트웨이. 어댑터의 `else GeminiGateway()` 자리를 대체한다.

    `model="auto"`로 부르면 Gemini 안에서 티어링한다(flash-lite → flash → pro).
    """
    return wrap(GeminiGateway(api_key=api_key), route=GEMINI_ROUTE)


def default_openai(*, api_key: str | None = None) -> CostRouter:
    """관측 래핑된 기본 OpenAI 게이트웨이. 어댑터의 `else OpenAIGateway()` 자리를 대체한다.

    auto 라우팅은 **지원하지 않는다**(단가 확보 모델이 `gpt-4o-mini` 하나뿐이라 티어가 없다).
    `model="auto"`를 넘기면 명시적 에러가 난다 — 엉뚱한 모델로 조용히 가지 않는다.
    """
    return wrap(OpenAIGateway(api_key=api_key), route=OPENAI_ROUTE)


def default_claude(*, api_key: str | None = None) -> CostRouter:
    """관측 래핑된 기본 Claude 게이트웨이. `model="auto"`면 haiku → sonnet → opus로 티어링한다."""
    return wrap(ClaudeGateway(api_key=api_key), route=CLAUDE_ROUTE)
