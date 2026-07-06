# -*- coding: utf-8 -*-
"""
core.persona — 페르소나 + 채널 메모리 + LLM 호출 메커니즘 (플랫폼 무관 엔진).

`youtube-agent/agents/persona.py`에서 **메커니즘만** 엔진으로 추출한다(control과 같은 분리).
원본은 페르소나 정의(youtube 특화)·채널 매핑(Discord env)·메모리(하드코딩 경로)·Gemini 호출이
한 파일에 섞여 있었다. 여기서는:

  - **엔진** = `Persona` 형태 + `PersonaRegistry`(채널→페르소나 해석) + `ask`(메모리·게이트웨이
    주입으로 프롬프트 구성→LLM 호출→히스토리 저장). 특정 페르소나를 모른다.
  - **어댑터(adapters/youtube/persona.py)** = youtube 페르소나 정의 + Discord 채널 매핑.

주입: 메모리는 `core.memory.ChannelMemory`(경로 주입), LLM은 `core.llm` 게이트웨이(프로바이더 무관),
provider="claude"는 미이식 `claude_runner` 대신 **`agent_runner` 주입 seam**(미주입 시 NotImplemented).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from core.gateway import default_gemini
from core.llm import LLMGateway

log = logging.getLogger("mint.core.persona")


@dataclass
class Persona:
    """페르소나 정의. 원본 `Persona`와 동일 형태(플랫폼 무관)."""

    key: str
    label: str
    system: str
    model: str = "gemini-2.5-flash"
    temperature: float = 0.4
    memory_window: int = 12          # 직전 N턴 컨텍스트 포함 (0=비활성)
    save_history: bool = True
    provider: str = "gemini"         # "gemini" | "claude"
    perms_env_key: str = ""
    max_iterations: int = 12
    extras: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class MemoryStore(Protocol):
    """대화 메모리 계약 — `core.memory.ChannelMemory`가 만족(테스트는 fake 주입)."""

    def recent(self, channel_id: str | int, n: int = 20) -> list[tuple[str, str]]: ...
    def append(self, channel_id: str | int, role: str, content: str) -> None: ...


# agent_runner(persona, channel_id, user_text, extra_context) -> str
AgentRunner = Callable[..., str]


# ── 순수: 프롬프트 구성 ────────────────────────────────────────────────────

def format_history(turns: list[tuple[str, str]]) -> str:
    """최근 대화 턴 → 히스토리 블록. 빈 리스트면 "". 원본 `_build_history_text` 포맷과 동일."""
    if not turns:
        return ""
    lines = ["# 이전 대화 (오래된 → 최근)"]
    for role, content in turns:
        tag = "USER" if role == "user" else "ASSISTANT"
        lines.append(f"[{tag}] {content}")
    return "\n".join(lines)


def build_prompt(history_block: str, extra_context: dict | str | None, user_text: str) -> str:
    """히스토리 + (선택)CONTEXT + QUESTION 조립. 원본 ask의 프롬프트 구성과 동일(순수)."""
    parts: list[str] = []
    if history_block:
        parts.append(history_block)
    if extra_context:
        extra = (json.dumps(extra_context, ensure_ascii=False, indent=2, default=str)
                 if isinstance(extra_context, dict) else str(extra_context))
        parts.append("# CONTEXT\n" + extra)
    parts.append(f"# QUESTION\n{user_text}")
    return "\n\n".join(parts)


# ── 채널 → 페르소나 해석 ───────────────────────────────────────────────────

class PersonaRegistry:
    """페르소나 집합 + 채널→키 해석 함수로 채널 ID를 Persona로 매핑."""

    def __init__(self, personas: dict[str, Persona], default: Persona, *,
                 channel_key: Callable[[str], str | None] | None = None) -> None:
        self.personas = personas
        self.default = default
        self._channel_key = channel_key or (lambda cid: None)

    def resolve(self, channel_id: str | int) -> Persona:
        """채널 ID → Persona. 매핑 없으면 default. 원본 `resolve`와 동일 흐름."""
        key = self._channel_key(str(channel_id))
        if key and key in self.personas:
            return self.personas[key]
        return self.default

    def all(self) -> dict[str, Persona]:
        return {**self.personas, self.default.key: self.default}


# ── 호출 메커니즘 ──────────────────────────────────────────────────────────

def ask(persona: Persona, channel_id: str | int, user_text: str, *,
        memory: MemoryStore, gateway: LLMGateway | None = None,
        extra_context: dict | str | None = None,
        agent_runner: AgentRunner | None = None) -> str:
    """페르소나 + 채널 메모리 + (선택)외부 컨텍스트로 LLM 호출, 답변 반환.

    provider="claude"면 주입된 `agent_runner`로 위임(미이식 claude_runner의 seam),
    아니면 게이트웨이 단발 호출(히스토리 저장). 원본 `ask`의 gemini 분기 동작 보존.
    """
    if persona.provider == "claude":
        if agent_runner is None:
            raise NotImplementedError(
                "claude provider는 agent_runner 주입 필요 (claude_runner 미이식)")
        return agent_runner(persona, channel_id, user_text, extra_context)

    gw = gateway if gateway is not None else default_gemini()
    if not gw.available:
        return "❌ LLM 게이트웨이 미가용"

    turns = memory.recent(channel_id, n=persona.memory_window) if persona.memory_window > 0 else []
    prompt = build_prompt(format_history(turns), extra_context, user_text)
    try:
        text = gw.complete(persona.system, prompt, model=persona.model,
                           temperature=persona.temperature, json_mode=False).strip()
    except Exception as exc:
        log.warning(f"LLM 호출 실패 ({persona.key}): {exc}")
        return f"❌ 응답 생성 실패: {exc}"

    if persona.save_history:
        memory.append(channel_id, "user", user_text)
        if text:
            memory.append(channel_id, "assistant", text)
    return text
