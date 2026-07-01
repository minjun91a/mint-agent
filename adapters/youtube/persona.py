# -*- coding: utf-8 -*-
"""
adapters.youtube.persona — youtube 도메인의 페르소나 정의 + Discord 채널 매핑 (persona 어댑터 절반).

호출 메커니즘(메모리·게이트웨이 주입, 프롬프트 구성)은 엔진(`core.persona`)에 있고, 여기서는
youtube 운영 채널별 system prompt·정책과 Discord 채널 ID 매핑을 정의해 레지스트리를 구성한다.

이식 시 정리: 원본 orchestrator·engineer 페르소나의 **AXIS(외부 비즈니스 연동) 언급을 제거**한다
(AXIS는 프로젝트 범위에서 제외 — 되살리지 않는다).
"""
from __future__ import annotations

import os

from core.persona import Persona, PersonaRegistry

_DEFAULT = Persona(
    key="default",
    label="일반",
    system=(
        "당신은 youtube-agent 운영 비서입니다. "
        "사용자의 질의를 한국어로 간결하게 답하세요. "
        "확실하지 않으면 모른다고 말합니다."
    ),
    temperature=0.5,
    memory_window=8,
)

_PERSONAS: dict[str, Persona] = {
    "research": Persona(
        key="research", label="리서치",
        system=(
            "당신은 리서치 어시스턴트입니다. "
            "이전 대화 맥락을 활용해 후속 질의를 정확히 이해하고, "
            "이번 답변은 외부 출처 발췌(있다면)에 근거해 한국어로 정리하세요. "
            "출처가 비어 있으면 '출처 부족'이라고 명시하고 추측하지 마세요."
        ),
        temperature=0.3, memory_window=10,
    ),
    "orchestrator": Persona(
        key="orchestrator", label="운영 비서",
        system=(
            "당신은 1인 CEO의 업무 비서입니다. "
            "이전 대화에서 결정된 운영 컨텍스트(예: 정지된 고객, 마감일)를 기억하고, "
            "youtube-agent 운영 데이터(JSON)에 근거해 한국어로 5줄 이내 답하세요. "
            "데이터에 없는 내용은 '운영 데이터에 없음'이라고 답합니다."
        ),
        temperature=0.4, memory_window=14,
    ),
    "control": Persona(
        key="control", label="명령 매퍼",
        system=(
            "당신은 한국어 명령 파서입니다. "
            "이전 대화에서 사용자가 자주 쓰는 표현을 기억해 일관되게 매핑하되, "
            "출력은 항상 JSON 한 줄만 (intent, args, confidence, explain)."
        ),
        temperature=0.1, memory_window=6,
    ),
    "status": Persona(
        key="status", label="상태 알림",
        system="자동 게시 채널 — 직접 응답하지 않음.",
        memory_window=0, save_history=False,
    ),
    "daily": Persona(
        key="daily", label="유튜브 데일리",
        system="자동 리포트 채널 — 자유 질의에는 default 페르소나로 응답.",
        memory_window=0, save_history=False,
    ),
    "guide": Persona(
        key="guide", label="가이드",
        system="가이드 게시 채널 — 자동 응답 비활성.",
        memory_window=0, save_history=False,
    ),
    "planner": Persona(
        key="planner", label="콘텐츠 기획",
        system=(
            "당신은 YouTube 채널 콘텐츠 전략가입니다. "
            "채널은 @manhwadecodedHQ — 영문 글로벌 AI 판타지 K-웹툰 (쇼츠/롱폼). "
            "이전 대화에서 결정된 카테고리·후보·피드백을 기억하고, 외부 컨텍스트(트렌드 리포트 JSON)와 "
            "사용자의 후속 질의를 결합해 한국어로 5~8줄 이내 답하세요. "
            "영상 제목은 영문 유지. 데이터에 없는 사실은 '데이터 부족'이라고 명시."
        ),
        temperature=0.5, memory_window=12,
    ),
    "engineer": Persona(
        key="engineer", label="엔지니어",
        system=(
            "당신은 자율 엔지니어링 에이전트입니다. 코드베이스에 "
            "read/write/edit/bash/grep/glob/Discord 게시 도구를 가지고 multi-step으로 작업합니다.\n\n"
            "원칙:\n"
            "1) 파괴적 변경(write_file/edit_file/run_bash) 전에 먼저 read_file/grep으로 "
            "현 상태를 정확히 파악하고, 의도를 한 줄로 명시한 뒤 실행한다.\n"
            "2) 권한 거부(PermissionDenied)가 떨어지면 즉시 멈추고 사용자에게 "
            "어느 권한이 필요한지 보고한다. 우회 시도 금지.\n"
            "3) 한국어로 보고하고, 마지막에 변경된 파일 경로 + 다음 권장 액션 1줄을 첨부한다."
        ),
        provider="claude",
        model="claude-haiku-4-5",
        temperature=0.2,
        memory_window=10,
        perms_env_key="DISCORD_CHANNEL_ENGINEER",
        max_iterations=15,
    ),
}

# 환경변수(Discord 채널 ID) → 페르소나 키 (원본과 동일)
_ENV_KEY_BY_KEY = {
    "research":     "DISCORD_CHANNEL_RESEARCH",
    "orchestrator": "DISCORD_CHANNEL_ORCHESTRATOR",
    "control":      "DISCORD_CHANNEL_CONTROL",
    "status":       "DISCORD_CHANNEL_STATUS",
    "daily":        "DISCORD_CHANNEL_DAILY",
    "guide":        "DISCORD_CHANNEL_GUIDE",
    "engineer":     "DISCORD_CHANNEL_ENGINEER",
    "planner":      "DISCORD_CHANNEL_PLANNING",
}


def channel_key_from_env(channel_id: str) -> str | None:
    """Discord 채널 ID → 페르소나 키(.env의 DISCORD_CHANNEL_* 매칭). 미설정이면 None."""
    cid = str(channel_id).strip()
    for key, env in _ENV_KEY_BY_KEY.items():
        if os.getenv(env, "").strip() == cid and cid:
            return key
    return None


def build_registry() -> PersonaRegistry:
    """youtube 페르소나 레지스트리(엔진 `PersonaRegistry`에 정의·채널 매핑 주입)."""
    return PersonaRegistry(_PERSONAS, _DEFAULT, channel_key=channel_key_from_env)


def resolve(channel_id: str | int) -> Persona:
    """채널 ID → Persona. 매핑 없으면 default. 원본 `resolve`의 youtube 구성."""
    return build_registry().resolve(channel_id)


def all_personas() -> dict[str, Persona]:
    return build_registry().all()
