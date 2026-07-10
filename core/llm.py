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
  - **빈 응답 폴백은 `json_mode`를 따른다**(`_empty_fallback`). JSON 요청엔 `"{}"`, 산문 요청엔
    `""`. 산문 호출부에 `"{}"`가 새어나가면 사용자에게 그대로 보인다.

Phase 2 연결점: 비용 라우팅(복잡도→모델 티어)과 관측성(토큰·지연·비용 로깅)은 전부 이 seam에
끼운다. 게이트웨이를 데코레이트/래핑하면 어댑터 코드 변경 없이 라우팅·계측이 들어온다.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

log = logging.getLogger("mint.core.llm")

# 응답이 비어 올 때의 안전 기본값 — JSON을 요청한 호출부는 파싱 가능한 빈 객체를 받아야 한다.
_EMPTY_JSON = "{}"


def _empty_fallback(json_mode: bool) -> str:
    """빈 응답(안전필터 차단 등) 폴백값.

    `json_mode`면 파싱 가능한 빈 객체를, 아니면 **빈 문자열**을 돌려준다. 산문을 요청한
    호출부(orchestrator 브리핑·persona 응답·research 요약)에 `"{}"`가 새어나가면 그대로
    사용자에게 노출된다 — 2번째 어댑터(research)가 드러낸 결함.
    """
    return _EMPTY_JSON if json_mode else ""


@dataclass(frozen=True)
class Usage:
    """한 호출의 provider 실측 토큰 사용량. 근사(문자수 기반)와 구분되는 '진짜' 값."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Completion:
    """`complete_ex`의 결과 — 텍스트 + (있으면) 실측 usage. usage=None이면 provider가 미노출."""

    text: str
    usage: Usage | None = None


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


@runtime_checkable
class UsageAwareGateway(Protocol):
    """실측 usage까지 노출하는 확장 포트(선택). `CostRouter`가 있으면 근사 대신 실측을 쓴다.

    구현 게이트웨이는 `complete`를 `complete_ex(...).text`로 위임한다(하위호환 — 기존 호출부는
    여전히 `complete`로 str만 받는다). usage를 못 뽑는 응답이면 `Completion.usage=None`.
    """

    def complete_ex(self, system: str, prompt: str, *, model: str,
                    temperature: float = 0.4, json_mode: bool = True) -> Completion:
        ...


def _usage_from(obj, in_attr: str, out_attr: str) -> Usage | None:
    """provider 응답의 usage 객체에서 (입력, 출력) 토큰을 방어적으로 추출. 없으면 None."""
    if obj is None:
        return None
    inp = getattr(obj, in_attr, None)
    out = getattr(obj, out_attr, None)
    if inp is None or out is None:
        return None
    try:
        return Usage(int(inp), int(out))
    except (TypeError, ValueError):
        return None


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
        return self.complete_ex(system, prompt, model=model,
                                temperature=temperature, json_mode=json_mode).text

    def complete_ex(self, system: str, prompt: str, *, model: str,
                    temperature: float = 0.4, json_mode: bool = True) -> Completion:
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
        text = (getattr(resp, "text", None) or _empty_fallback(json_mode)).strip()
        # Gemini usage_metadata: prompt_token_count / candidates_token_count
        usage = _usage_from(getattr(resp, "usage_metadata", None),
                            "prompt_token_count", "candidates_token_count")
        return Completion(text=text, usage=usage)


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
        return self.complete_ex(system, prompt, model=model,
                                temperature=temperature, json_mode=json_mode).text

    def complete_ex(self, system: str, prompt: str, *, model: str,
                    temperature: float = 0.4, json_mode: bool = True) -> Completion:
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
        text = (resp.choices[0].message.content or _empty_fallback(json_mode)).strip()
        # OpenAI usage: prompt_tokens / completion_tokens
        usage = _usage_from(getattr(resp, "usage", None),
                            "prompt_tokens", "completion_tokens")
        return Completion(text=text, usage=usage)


class ClaudeGateway:
    """`anthropic` 뒤의 LLM seam (프로바이더 #3). SDK import는 `complete` 안에서만 일어난다.

    `Gemini/OpenAIGateway`와 동일한 `LLMGateway` 포트를 구현한다. 매핑 차이:
      - system은 messages API의 top-level `system` 파라미터로,
      - json_mode는 Anthropic에 전용 플래그가 없어 **system에 JSON-only 지시를 덧붙여** 강제한다,
      - messages API는 `max_tokens`가 필수라 기본값을 둔다(호출부는 포트만 보므로 노출 안 함).
    Windows + 한글 USERPROFILE 환경에서 SDK 기본 헤더의 ASCII 인코딩이 실패하는 문제를 피하려
    `default_headers`를 ASCII 안전값으로 명시한다(운영 `claude_runner`의 회피책 상속).
    api_key 미명시 시 호출 시점에 `ANTHROPIC_API_KEY`에서 해석한다(지연 평가).

    이 게이트웨이의 존재로 `core.routing`의 기본 라우트(Opus/Sonnet/Haiku)가 실제 도달 가능한
    대상을 갖는다 — 복잡도 비용 라우터가 감쌀 프로바이더.
    """

    _JSON_INSTRUCTION = "\n\n반드시 유효한 JSON 하나만 출력하세요. 다른 텍스트를 덧붙이지 마세요."

    def __init__(self, *, api_key: str | None = None, max_tokens: int = 4096) -> None:
        self._explicit_key = api_key
        self._max_tokens = max_tokens

    @property
    def _key(self) -> str:
        if self._explicit_key is not None:
            return self._explicit_key
        return os.getenv("ANTHROPIC_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self._key)

    def complete(self, system: str, prompt: str, *, model: str,
                 temperature: float = 0.4, json_mode: bool = True) -> str:
        return self.complete_ex(system, prompt, model=model,
                                temperature=temperature, json_mode=json_mode).text

    def complete_ex(self, system: str, prompt: str, *, model: str,
                    temperature: float = 0.4, json_mode: bool = True) -> Completion:
        import anthropic  # 선택 의존성: 호출 시점 지연 import

        client = anthropic.Anthropic(
            api_key=self._key,
            default_headers={"User-Agent": "mint-agent/1.0"},
        )
        sys_prompt = system + self._JSON_INSTRUCTION if json_mode else system
        resp = client.messages.create(
            model=model,
            system=sys_prompt,
            max_tokens=self._max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        blocks = getattr(resp, "content", None) or []
        text = "".join(getattr(b, "text", "") for b in blocks)
        # Anthropic usage: input_tokens / output_tokens
        usage = _usage_from(getattr(resp, "usage", None), "input_tokens", "output_tokens")
        return Completion(text=(text or _empty_fallback(json_mode)).strip(), usage=usage)
