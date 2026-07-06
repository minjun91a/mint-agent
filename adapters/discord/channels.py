# -*- coding: utf-8 -*-
"""
adapters.discord.channels — Discord 채널 정체성 매핑 (IO 어댑터의 절반).

'2중 결합' 분해: youtube 콘텐츠 어댑터(`adapters/youtube/persona.py`)에 섞여 있던
**Discord 채널 ID → 역할(페르소나 키)** 매핑을 이리로 분리한다. 이제 경계가 명확하다:

  - youtube 어댑터 = 콘텐츠(페르소나 *정의*: 프롬프트·정책). Discord를 모른다.
  - discord 어댑터 = IO 정체성(어느 채널이 어느 역할인가).
  - 엔진(`core.persona.PersonaRegistry`) = 둘 다 몰라도 됨 — 채널 해석기를 *주입*받는다.

매핑 값은 원본 `youtube-agent/agents/persona.py`와 동일(env 변수명 parity). 실제 봇 런타임
(discord_bot·notifier·approvals)은 아직 이식 전 — Phase 1은 여기 '경계 분리'까지다.
"""
from __future__ import annotations

import os

# 페르소나(역할) 키 → Discord 채널 ID 환경변수. 원본 `_ENV_KEY_BY_KEY`와 동일.
ENV_KEY_BY_ROLE: dict[str, str] = {
    "research":     "DISCORD_CHANNEL_RESEARCH",
    "orchestrator": "DISCORD_CHANNEL_ORCHESTRATOR",
    "control":      "DISCORD_CHANNEL_CONTROL",
    "status":       "DISCORD_CHANNEL_STATUS",
    "daily":        "DISCORD_CHANNEL_DAILY",
    "guide":        "DISCORD_CHANNEL_GUIDE",
    "engineer":     "DISCORD_CHANNEL_ENGINEER",
    "planner":      "DISCORD_CHANNEL_PLANNING",
}


def channel_key_from_env(channel_id: str | int) -> str | None:
    """Discord 채널 ID → 역할 키(.env의 `DISCORD_CHANNEL_*` 매칭). 미설정이면 None.

    원본 `persona.channel_key_from_env`와 동일 동작(지연 env 평가 → monkeypatch 반영).
    """
    cid = str(channel_id).strip()
    if not cid:
        return None
    for role, env in ENV_KEY_BY_ROLE.items():
        if os.getenv(env, "").strip() == cid:
            return role
    return None
