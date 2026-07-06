# -*- coding: utf-8 -*-
"""
adapters.discord.personas — Discord 채널 → youtube 페르소나 라우팅 (IO↔콘텐츠 조립점).

'2중 결합' 분해의 조립 지점. 의존 방향이 **IO → 콘텐츠**(올바른 방향)임을 보인다:
Discord 어댑터가 자신의 채널 정체성(`channels.channel_key_from_env`)으로 youtube 콘텐츠
어댑터의 페르소나 레지스트리를 구성한다. 반대(youtube → discord) 결합은 없다 —
youtube 어댑터는 이제 Discord를 import 하지 않는다.

원본 `persona.build_registry`/`resolve`(youtube에 Discord가 섞여 있던 형태)와 동일한
동작을, 경계를 가른 채 재현한다.
"""
from __future__ import annotations

from adapters.discord.channels import channel_key_from_env
from adapters.youtube.persona import build_registry
from core.persona import Persona, PersonaRegistry


def build_registry_for_discord() -> PersonaRegistry:
    """youtube 페르소나 + Discord 채널 해석기를 결합한 레지스트리(엔진에 resolver 주입)."""
    return build_registry(channel_key=channel_key_from_env)


def resolve(channel_id: str | int) -> Persona:
    """Discord 채널 ID → youtube Persona. 매핑 없으면 default. 원본 `resolve`와 동일 결과."""
    return build_registry_for_discord().resolve(channel_id)
