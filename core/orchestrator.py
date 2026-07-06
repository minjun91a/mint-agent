# -*- coding: utf-8 -*-
"""
core.orchestrator — 운영 데이터 수집 → LLM 우선순위 브리핑 엔진 (플랫폼 무관).

`youtube-agent/agents/orchestrator.py`에서 **브리핑 메커니즘만** 엔진으로 추출한다(control·persona와
같은 분리). 원본은 데이터 소스(AXIS DB·youtube)·프롬프트·Gemini 호출이 한 파일에 섞여 있었다.
여기서는 "무엇을 수집하는지 모르는" 범용 엔진만 둔다:

  - **수집** = 이름→콜러블(collector) 맵을 받아 각 소스를 모은다(소스별 실패는 격리).
  - **브리핑/응답** = 모은 payload를 게이트웨이로 요약(시스템 프롬프트는 호출측이 주입).

구체 소스(youtube의 `collect_yt` 등)는 어댑터가 정의한다. 원본의 AXIS 수집부(`collect_axis`)는
프로젝트 범위 제외 결정에 따라 **이식하지 않는다**.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from core.gateway import default_gemini
from core.llm import LLMGateway

log = logging.getLogger("mint.core.orchestrator")

# 이름 없는 인자로 호출되는 데이터 수집기: () -> dict
Collector = Callable[[], dict]

_DEFAULT_INTRO = (
    "다음은 현재 시점의 운영 상태 데이터입니다.\n"
    "이 데이터만 근거로 우선순위 브리핑을 생성하세요.\n\n"
)


def collect_sources(collectors: dict[str, Collector]) -> dict[str, Any]:
    """각 수집기를 실행해 {이름: 데이터}로 모은다. 한 소스가 실패해도 나머지는 진행."""
    out: dict[str, Any] = {}
    for name, fn in collectors.items():
        try:
            out[name] = fn()
        except Exception as exc:
            log.warning(f"수집 실패 ({name}): {exc}")
            out[name] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    return out


def build_briefing(payload: dict, *, system: str, gateway: LLMGateway | None = None,
                   model: str = "gemini-2.5-flash", temperature: float = 0.3,
                   intro: str = _DEFAULT_INTRO) -> str:
    """payload(JSON)를 근거로 우선순위 브리핑 생성. 게이트웨이 주입, 미가용/실패 시 안전 메시지."""
    gw = gateway if gateway is not None else default_gemini()
    if not gw.available:
        return "❌ LLM 게이트웨이 미가용"
    prompt = intro + f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}\n```"
    try:
        return gw.complete(system, prompt, model=model, temperature=temperature,
                           json_mode=False).strip()
    except Exception as exc:
        log.warning(f"브리핑 생성 실패: {exc}")
        return f"❌ 응답 생성 실패: {exc}"


def answer(question: str, context: dict, *, system: str, gateway: LLMGateway | None = None,
           model: str = "gemini-2.5-flash", temperature: float = 0.4) -> str:
    """context(JSON) 근거 자유 질의 응답. 원본 `answer`의 프롬프트 형식과 동일(순수 조립 + 게이트웨이)."""
    gw = gateway if gateway is not None else default_gemini()
    if not gw.available:
        return "❌ LLM 게이트웨이 미가용"
    prompt = (f"# QUESTION\n{question}\n\n"
              f"# CONTEXT\n```json\n{json.dumps(context, ensure_ascii=False, indent=2, default=str)}\n```")
    try:
        return gw.complete(system, prompt, model=model, temperature=temperature,
                           json_mode=False).strip()
    except Exception as exc:
        log.warning(f"응답 생성 실패: {exc}")
        return f"❌ 응답 생성 실패: {exc}"


def run_brief(collectors: dict[str, Collector], *, system: str,
              gateway: LLMGateway | None = None, model: str = "gemini-2.5-flash",
              temperature: float = 0.3, today: str = "", now: str = "",
              intro: str = _DEFAULT_INTRO) -> dict[str, Any]:
    """소스 수집 → 브리핑 생성. 반환: {소스들..., "summary"}. 원본 `run_brief` 형태와 동일."""
    sources = collect_sources(collectors)
    payload = {"today": today, "now": now, **sources}
    summary = build_briefing(payload, system=system, gateway=gateway, model=model,
                             temperature=temperature, intro=intro)
    return {**sources, "summary": summary}
