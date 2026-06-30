# -*- coding: utf-8 -*-
"""
core.llm — 모델 호출 게이트웨이 (엔진의 단일 LLM seam).

어댑터(seo·review·…)가 직접 SDK를 만지지 않게, **모든 LLM 호출을 이 한 곳으로 통과**시킨다.
지금까지 `adapters/youtube/seo.py`와 `adapters/youtube/review.py`가 각자
`google.generativeai`를 지연 import → configure → GenerativeModel → generate_content 하는
동일한 6줄을 중복했다. 두 번째 호출부가 생긴 시점(브리프 8장: "일반화는 2번째가 강제할 때")에
그 중복을 게이트웨이로 모은다.

설계 의도:
  - **`LLMGateway`** = 엔진이 의존하는 포트. 어댑터는 구체 SDK(`GeminiGateway`)가 아니라
    이 인터페이스에 기대므로, 테스트는 가짜 게이트웨이를 주입하고(결정적), 운영은 실 SDK를 쓴다.
  - **`available`** = "이 게이트웨이가 실제 호출을 할 수 있는가"(키 보유 여부). 호출부는 이 값으로
    'LLM 미가용(→보수적 폴백)'과 'LLM 오류(→다른 처리)'를 구분한다. 이 구분은 기존 동작이므로
    포트에 1급으로 노출한다.
  - **격리**: 프로바이더 SDK import는 오직 각 게이트웨이의 `complete` 안에서만 일어난다
    (`GeminiGateway`→`google.generativeai`, `OpenAIGateway`→`openai`). 선택 의존성을 엔진
    import 시점에 끌어오지 않기 위함.
  - **프로바이더 무관 포트**: 같은 `complete(system, prompt, *, model, temperature, json_mode)`가
    Gemini(system_instruction+generate_content)와 OpenAI(chat messages)에 동일하게 매핑된다.
    어댑터는 어느 프로바이더인지 모른 채 동작 → 프로바이더 교체로 엔진/어댑터 코드는 불변.

Phase 2 연결점: 비용 라우팅(복잡도→모델 티어)과 관측성(토큰·지연·비용 로깅)은 전부 이 seam에
끼운다. 게이트웨이를 데코레이트/래핑하면 어댑터 코드 변경 없이 라우팅·계측이 들어온다.
"""
from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

log = logging.getLogger("mint.core.llm")

# 응답이 비어 올 때의 안전 기본값 — 호출부는 JSON 파싱을 기대하므로 빈 객체로 폴백.
_EMPTY_JSON = "{}"


@runtime_checkable
class LLMGateway(Protocol):
    """LLM 호출 포트. 어댑터는 구체 SDK가 아니라 이 인터페이스에 의존한다."""

    @property
    def available(self) -> bool:
        """실제 호출 가능 여부(예: API 키 보유). False면 호출부는 보수적 폴백을 택한다."""
        ...

    def complete(self, system: str, prompt: str, *, model: str,
                 temperature: float = 0.4, json_mode: bool = True) -> str:
        """system 지시 + prompt → 모델 응답 텍스트. json_mode면 JSON 출력을 강제한다."""
        ...


class GeminiGateway:
    """`google.generativeai` 뒤의 단일 LLM seam. SDK import는 `complete` 안에서만 일어난다.

    api_key를 명시하지 않으면 호출 시점에 `GEMINI_API_KEY` 환경변수에서 해석한다(지연 평가 →
    테스트의 monkeypatch.delenv가 그대로 반영됨).
    """

    def __init__(self, *, api_key: str | None = None) -> None:
        self._explicit_key = api_key

    @property
    def _key(self) -> str:
        if self._explicit_key is not None:
            return self._explicit_key
        return os.getenv("GEMINI_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self._key)

    def complete(self, system: str, prompt: str, *, model: str,
                 temperature: float = 0.4, json_mode: bool = True) -> str:
        import google.generativeai as genai  # 선택 의존성: 호출 시점 지연 import

        genai.configure(api_key=self._key)
        generation_config: dict[str, object] = {"temperature": temperature}
        if json_mode:
            generation_config["response_mime_type"] = "application/json"
        gm = genai.GenerativeModel(
            model_name=model,
            system_instruction=system,
            generation_config=generation_config,
        )
        resp = gm.generate_content(prompt)
        return (getattr(resp, "text", None) or _EMPTY_JSON).strip()


class OpenAIGateway:
    """`openai` 뒤의 LLM seam (프로바이더 #2). SDK import는 `complete` 안에서만 일어난다.

    `GeminiGateway`와 동일한 `LLMGateway` 포트를 구현한다. system/prompt는 OpenAI chat
    메시지(system+user)로, json_mode는 `response_format`으로 매핑된다.
    api_key 미명시 시 호출 시점에 `OPENAI_API_KEY`에서 해석한다(지연 평가).
    """

    def __init__(self, *, api_key: str | None = None) -> None:
        self._explicit_key = api_key

    @property
    def _key(self) -> str:
        if self._explicit_key is not None:
            return self._explicit_key
        return os.getenv("OPENAI_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self._key)

    def complete(self, system: str, prompt: str, *, model: str,
                 temperature: float = 0.4, json_mode: bool = True) -> str:
        from openai import OpenAI  # 선택 의존성: 호출 시점 지연 import

        client = OpenAI(api_key=self._key)
        kwargs: dict[str, object] = {
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or _EMPTY_JSON).strip()
