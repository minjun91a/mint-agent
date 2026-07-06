# -*- coding: utf-8 -*-
"""
adapters.youtube.control — youtube 도메인의 intent 어휘·룰·폴백 프롬프트 (control 어댑터 절반).

라우팅 *메커니즘*은 엔진(`core.control.IntentRouter`)에 있고, 여기서는 youtube가 무엇을 인식할지
(intent 목록·정규식 룰·arg 추출·LLM 폴백 프롬프트)를 정의해 라우터를 구성한다. 룰/추출 로직은
`youtube-agent/agents/control.py`와 **동일**(원본 parity 대상).

액션 배선(`dispatch`)은 **핸들러 주입**으로 다룬다: 엔진 `core.control.dispatch`가 라우팅을,
여기 `build_handlers`가 intent→파이프라인 매핑을 담당한다. 미이식 파이프라인(`tools.*`)은
주입점(seam)으로 남겨, 주입되지 않은 intent는 엔진이 "미구현"으로 정직하게 응답한다.
"""
from __future__ import annotations

import re
from typing import Any

from core.control import Command, Handler, IntentRouter, Rule
from core.control import dispatch as _dispatch

KNOWN_INTENTS = {
    "story", "story_gem", "story_custom", "series",
    "research", "brief", "daily", "channel_info", "help",
    "unknown",
}

_CATEGORIES = ["Murim", "Cultivation", "Reincarnation Returner", "Tower Dungeon", "Necromancer Villain"]
_CATEGORY_HINTS = {
    "무림": "Murim",
    "murim": "Murim",
    "wuxia": "Murim",

    "선도": "Cultivation",
    "선협": "Cultivation",
    "cultivation": "Cultivation",
    "xianxia": "Cultivation",

    "회귀": "Reincarnation Returner",
    "회귀자": "Reincarnation Returner",
    "환생": "Reincarnation Returner",
    "returner": "Reincarnation Returner",
    "reincarnation": "Reincarnation Returner",

    "타워": "Tower Dungeon",
    "던전": "Tower Dungeon",
    "tower": "Tower Dungeon",
    "dungeon": "Tower Dungeon",

    "네크로맨서": "Necromancer Villain",
    "사령술사": "Necromancer Villain",
    "악역": "Necromancer Villain",
    "necromancer": "Necromancer Villain",
    "villain": "Necromancer Villain",
}


def _detect_category(text: str) -> str | None:
    low = text.lower()
    for k, v in _CATEGORY_HINTS.items():
        if k in low:
            return v
    return None


def _detect_count(text: str, default: int = 1) -> int:
    m = re.search(r"(\d+)\s*(?:편|개)", text)
    if m:
        return max(1, min(10, int(m.group(1))))
    return default


def _strip_query(text: str) -> str:
    """research 인텐트의 쿼리 본문만 남기기. 원본 `_strip_query`와 동일."""
    t = text.strip()
    t = re.sub(r"^(리서치|research|검색해줘|찾아봐|조사해|search)\s*[:：]?\s*", "", t, flags=re.I)
    return t.strip()


# ── 인텐트별 arg 추출 (원본 parse_rule의 인라인 분기와 동일) ──────────────

def _extract_story(text: str) -> dict[str, Any]:
    args: dict[str, Any] = {}
    if re.search(r"(업로드\s*하지\s*마|no.?upload|로컬만|업로드\s*x)", text, re.I):
        args["no_upload"] = True
    return args


def _extract_story_gem(text: str) -> dict[str, Any]:
    args: dict[str, Any] = {}
    cat = _detect_category(text)
    if cat:
        args["category"] = cat
    if re.search(r"\bpro\b|2\.5.?pro", text, re.I):
        args["model"] = "gemini-2.5-pro"
    return args


def _extract_series(text: str) -> dict[str, Any]:
    args: dict[str, Any] = {"shorts": _detect_count(text, default=4)}
    if re.search(r"롱폼만|long.?form\s*only|숏폼\s*없", text, re.I):
        args["shorts"] = 0
    return args


def _extract_research(text: str) -> dict[str, Any] | Command:
    q = _strip_query(text)
    if not q:
        return Command(intent="unknown", source="rule",
                       explain="research 인텐트는 매칭됐으나 쿼리가 비어있음")
    return {"query": q, "top": _detect_count(text, default=5)}


# ── 룰 목록 (원본 _RULES 순서·패턴 동일) ───────────────────────────────────

RULES: list[Rule] = [
    Rule(re.compile(r"(브리핑|brief|오늘\s*(할\s*일|우선순위)|아침\s*보고)", re.I), "brief"),
    Rule(re.compile(r"(유튜브|youtube)?.{0,4}(일일|daily)\s*(리포트|report)", re.I), "daily"),
    Rule(re.compile(r"채널\s*(정보|메타|info)|channel.?info", re.I), "channel_info"),
    Rule(re.compile(r"(gem|젬).{0,8}(쇼츠|숏폼|short|story|스토리|영상)", re.I), "story_gem", _extract_story_gem),
    Rule(re.compile(r"(쇼츠|숏폼|short).{0,8}(gem|젬)", re.I), "story_gem", _extract_story_gem),
    Rule(re.compile(r"(시리즈|series|롱폼|long.?form)", re.I), "series", _extract_series),
    Rule(re.compile(r"(쇼츠|숏폼|short).{0,8}(만들|생성|뽑|올려|업로드)", re.I), "story", _extract_story),
    Rule(re.compile(r"(스토리|story).{0,8}(만들|생성)", re.I), "story", _extract_story),
    Rule(re.compile(r"(리서치|research|검색해|조사해|찾아|find\s+about|search)", re.I), "research", _extract_research),
    Rule(re.compile(r"(도움말|help|뭐.?할\s*수|명령어|커맨드)", re.I), "help"),
]


_FALLBACK_SYSTEM = """당신은 한국어 명령 파서입니다.
사용자 발화를 다음 intent 중 하나로 매핑해 JSON만 출력하세요.

INTENTS:
- story          : 1편짜리 쇼츠 자동 생성 (args: no_upload?: bool)
- story_gem      : Gemini Gem 작가로 쇼츠 생성 (args: category? in [Murim, Cultivation, Reincarnation Returner, Tower Dungeon, Necromancer Villain], model? in [gemini-2.5-flash, gemini-2.5-pro])
- series         : 롱폼 1편 + 숏폼 N편 (args: shorts?: int 0~10)
- research       : 웹 검색+요약 (args: query: str, top?: int 1~8)
- brief          : 오늘의 우선순위 브리핑
- daily          : YouTube 일일 리포트
- channel_info   : 채널 메타 요약
- help           : 사용 가능한 명령 목록
- unknown        : 매핑 불가

출력 형식 (절대 다른 텍스트 추가하지 말 것):
{"intent": "...", "args": {...}, "confidence": 0.0~1.0, "explain": "한 줄 설명"}
"""


def build_router(gateway=None) -> IntentRouter:
    """youtube intent 라우터 구성(엔진 `IntentRouter`에 youtube 룰/프롬프트 주입)."""
    return IntentRouter(RULES, KNOWN_INTENTS, fallback_system=_FALLBACK_SYSTEM,
                        fallback_model="gemini-2.5-flash", gateway=gateway)


def parse(text: str, *, allow_llm: bool = True, gateway=None) -> Command:
    """자연어 → Command (룰 → LLM 폴백). 원본 `control.parse`의 youtube 구성."""
    return build_router(gateway).parse(text, allow_llm=allow_llm)


def parse_rule(text: str) -> Command | None:
    """룰 매칭만(결정적). 원본 `control.parse_rule`과 동일 결과."""
    return build_router().parse_rule(text)


# ── dispatch: intent → 파이프라인 (핸들러 맵, 파이프라인은 주입) ────────────────

# 원본 dispatch의 help 응답(youtube 명령 목록) — 콘텐츠라 어댑터에 둔다.
_HELP_TEXT = (
    "**사용 가능한 명령 (자연어 OK):**\n"
    "• `브리핑` / `오늘 우선순위`         → /brief\n"
    "• `쇼츠 1편 만들어줘`                → /story\n"
    "• `gem 무림 쇼츠 만들어`             → /story-gem (Gem 작가)\n"
    "• `시리즈 만들어 숏폼 4편`           → /series\n"
    "• `리서치 AI shorts trends`          → /research\n"
    "• `유튜브 일일 리포트`               → /daily\n"
    "• `채널 정보`                        → /channel-info"
)


def build_handlers(*, run_story: Callable[..., Any] | None = None,
                   run_story_gem: Callable[..., Any] | None = None,
                   run_series: Callable[..., Any] | None = None,
                   run_research: Callable[..., Any] | None = None,
                   run_brief: Callable[..., Any] | None = None,
                   daily_report: Callable[[], Any] | None = None,
                   channel_info: Callable[[], Any] | None = None,
                   help_text: str = _HELP_TEXT) -> dict[str, Handler]:
    """youtube intent → 핸들러 맵. 각 파이프라인은 **콜러블로 주입**한다.

    원본 `dispatch`의 intent별 arg 추출(`no_upload`·`category`/`model`·`shorts`·`query`/`top`)을
    그대로 보존하되, 실행 대상 파이프라인은 주입점으로 뺐다. 미주입 intent는 맵에 없으므로
    엔진 `dispatch`가 "미구현"으로 응답한다(미이식 파이프라인을 정직하게 표시). Discord 등 IO
    결합(`discord=True`)은 주입되는 콜러블의 책임으로 넘겨 여기서 제거한다.
    """
    handlers: dict[str, Handler] = {
        "help": lambda cmd: {"ok": True, "msg": help_text},
    }
    if run_story is not None:
        handlers["story"] = lambda cmd: {"ok": True, "result": run_story(
            upload=not cmd.args.get("no_upload", False))}
    if run_story_gem is not None:
        handlers["story_gem"] = lambda cmd: {"ok": True, "result": run_story_gem(
            category=cmd.args.get("category"),
            model=cmd.args.get("model", "gemini-2.5-flash"))}
    if run_series is not None:
        handlers["series"] = lambda cmd: {"ok": True, "result": run_series(
            top_shorts=int(cmd.args.get("shorts", 4)))}
    if run_research is not None:
        handlers["research"] = lambda cmd: {"ok": True, "result": run_research(
            cmd.args["query"], top_k=int(cmd.args.get("top", 5)))}
    if run_brief is not None:
        handlers["brief"] = lambda cmd: {"ok": True, "result": run_brief()}
    if daily_report is not None:
        handlers["daily"] = lambda cmd: {"ok": True, "result": {"text": daily_report()}}
    if channel_info is not None:
        handlers["channel_info"] = lambda cmd: {"ok": True, "result": channel_info()}
    return handlers


def dispatch(cmd: Command, **injected: Any) -> dict:
    """`Command` → 실행 결과. `build_handlers`로 핸들러를 만들어 엔진 `dispatch`에 넘긴다.

    파이프라인 콜러블을 키워드로 주입한다(예: `dispatch(cmd, run_brief=..., run_story=...)`).
    미주입 intent는 "디스패처 미구현: …"로 응답 — 원본 반환 규약과 동일.
    """
    return _dispatch(cmd, build_handlers(**injected))
