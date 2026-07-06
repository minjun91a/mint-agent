# -*- coding: utf-8 -*-
"""
adapters.youtube.seo — 영문 제목·설명·태그·해시태그 생성 (produce 단계, LLM).

`youtube-agent/agents/seo.py`를 모노레포로 이식. LLM(Gemini) 호출이 끼므로 parity 불가 →
순수/LLM 경계를 갈라 검증한다:

  1. **순수 로직** — 카테고리 매핑(`_resolve_category_id`)·태그 정규화(`_ensure_15_tags`)·
     프롬프트 구성(`_build_user_msg`)·fallback(`_build_fallback`)·응답 정규화(`_normalize`).
     전부 결정적 → 특성화 테스트 + 원본 parity(순수 함수에 한해).
  2. **LLM 경계** — `generate_seo_meta(..., gateway=...)`로 `core.llm.LLMGateway`를 **주입**.
     테스트는 가짜 게이트웨이(canned JSON)를 넣어 정규화/fallback을 검증(mock 패턴). 실 Gemini
     호출은 엔진의 `GeminiGateway` 한 곳에 격리된다(모델 호출 seam).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.gateway import default_gemini
from core.llm import LLMGateway

log = logging.getLogger("mint.adapters.youtube.seo")

# YouTube category mapping — fantasy 쇼츠는 1(Film & Animation) 또는 24(Entertainment)
CATEGORY_MAP = {
    "default": "24",  # Entertainment
    "Murim": "1",     # Film & Animation
    "Cultivation": "1",
    "Reincarnation Returner": "1",
    "Tower Dungeon": "20",  # Gaming (LitRPG 톤)
    "Necromancer Villain": "1",
}

CATEGORY_SEED_TAGS = {
    "Murim": ["murim", "wuxia", "cultivation manhwa", "qi master"],
    "Cultivation": ["cultivation", "xianxia", "dao", "ascension"],
    "Reincarnation Returner": ["regression", "returner", "second chance", "reincarnation"],
    "Tower Dungeon": ["tower of god", "litrpg", "dungeon", "hunter"],
    "Necromancer Villain": ["necromancer", "antihero", "dark fantasy", "villain protagonist"],
}

BASE_TAGS = [
    "shorts", "ai animation", "ai webtoon", "korean webtoon",
    "manhwa", "fantasy shorts",
]

_SEO_SYSTEM = """You are a YouTube Shorts SEO optimizer for an AI-generated K-webtoon style fantasy channel.
Channel handle: @manhwadecodedHQ — global English audience, fantasy/murim/regression/cultivation.

Your job: from a SCRIPT and optional TREND_CONTEXT, produce optimized YouTube metadata as JSON.

Hard rules:
1) `title` — English, ≤95 chars (room for "🔥" or "Pt.1" later). Click-worthy but NOT clickbait-fraud (must reflect the actual story).
2) `description` — 4~6 short paragraphs (≤4500 chars total).
   - Line 1: A 1-sentence hook (≤100 chars) — this shows in feed previews. PUT THE STRONGEST HOOK HERE.
   - Line 2: blank
   - Then 2~3 short paragraphs of synopsis (≤300 chars each, vivid).
   - Then "▶ More episodes coming. Subscribe @manhwadecodedHQ." line.
   - Then a hashtag block (5~7 hashtags on one line).
   - Finally: "🤖 Created with generative AI." disclosure line.
3) `tags` — exactly 15 distinct English tags. Mix:
   - 5 from script category & tropes
   - 5 from TREND_CONTEXT keywords (if provided; else use synonyms)
   - 5 generic discovery (shorts, ai animation, manhwa, etc)
   Each tag ≤30 chars.
4) `hashtags` — 5~7 short hashtags starting with #, lowercase, no spaces. Include #shorts.

Output JSON only, no prose. Schema:
{
  "title": "...",
  "description": "...",
  "tags": ["tag1", ...],
  "hashtags": ["#shorts", "#aiwebtoon", ...]
}
"""


def _build_user_msg(script: dict[str, Any], planner_trends: list[dict] | None) -> str:
    parts: list[str] = []
    parts.append("# SCRIPT")
    parts.append(json.dumps({
        "title": script.get("title") or script.get("title_en", ""),
        "category": script.get("category", ""),
        "hook": script.get("hook", "")[:600],
        "body": script.get("body", "")[:1500],
        "cliffhanger": script.get("cliffhanger", "")[:400],
        "tropes": script.get("_used_tropes", []),
        "thumbnail_text": script.get("thumbnail_text", ""),
    }, ensure_ascii=False, indent=2))

    if planner_trends:
        parts.append("# TREND_CONTEXT")
        trim: list[dict] = []
        for t in planner_trends[:3]:
            trim.append({
                "category": t.get("category", ""),
                "summary": (t.get("summary", "") or "")[:1500],
            })
        parts.append(json.dumps(trim, ensure_ascii=False, indent=2))
    return "\n\n".join(parts)


def _resolve_category_id(category: str) -> str:
    if not category:
        return CATEGORY_MAP["default"]
    return CATEGORY_MAP.get(category) or CATEGORY_MAP["default"]


def _ensure_15_tags(tags: list[str], category: str) -> list[str]:
    """15개 미만/초과 태그를 정규화(dedup·lowercase·30자·시드 채움). 원본 동일."""
    seen = set()
    out: list[str] = []
    for t in tags:
        t = (t or "").strip().lower()
        if not t or t in seen:
            continue
        if len(t) > 30:
            t = t[:30]
        seen.add(t)
        out.append(t)
        if len(out) >= 15:
            break
    seeds = CATEGORY_SEED_TAGS.get(category, []) + BASE_TAGS
    for s in seeds:
        if len(out) >= 15:
            break
        s = s.lower()
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out[:15]


def _build_fallback(script: dict[str, Any], category: str) -> dict[str, Any]:
    """Gemini 미사용/실패 시 보수적 메타. 원본 fallback과 동일."""
    fallback_title = (script.get("title") or script.get("title_en") or "Fantasy Short")[:95]
    return {
        "title": fallback_title,
        "description": ((script.get("hook", "") or "")
                        + "\n\n" + (script.get("body", "") or "")[:1500]
                        + "\n\n▶ More episodes coming. Subscribe @manhwadecodedHQ."
                        + "\n\n#shorts #aiwebtoon #manhwa"
                        + "\n\n🤖 Created with generative AI.")[:4500],
        "tags": _ensure_15_tags(BASE_TAGS, category),
        "hashtags": ["#shorts", "#aiwebtoon", "#manhwa", "#fantasy", "#kwebtoon"],
        "category_id": _resolve_category_id(category),
    }


def _normalize(data: dict[str, Any], script: dict[str, Any], category: str,
               fallback: dict[str, Any]) -> dict[str, Any]:
    """LLM 응답(data) → 최종 메타. 원본 후처리 로직과 동일."""
    fallback_title = (script.get("title") or script.get("title_en") or "Fantasy Short")[:95]
    title = (data.get("title") or fallback_title)[:95]
    description = (data.get("description") or fallback["description"])[:4500]
    tags = _ensure_15_tags(data.get("tags") or [], category)
    hashtags = data.get("hashtags") or fallback["hashtags"]
    if not isinstance(hashtags, list):
        hashtags = fallback["hashtags"]
    hashtags = [h if h.startswith("#") else f"#{h}" for h in hashtags[:7]]
    return {
        "title": title,
        "description": description,
        "tags": tags,
        "hashtags": hashtags,
        "category_id": _resolve_category_id(category),
    }


def generate_seo_meta(script: dict[str, Any],
                      planner_trends: list[dict] | None = None,
                      *, model: str = "gemini-2.5-flash",
                      gateway: LLMGateway | None = None) -> dict[str, Any]:
    """script + 선택적 트렌드 → SEO 메타데이터 dict.

    gateway: LLM 게이트웨이 주입(테스트용 fake). 미지정 시 실 `GeminiGateway`.
    게이트웨이가 미가용(키 없음)이거나 호출 실패 시 보수적 fallback. (원본 동작 보존)
    """
    category = script.get("category", "") or ""
    fallback = _build_fallback(script, category)

    gw = gateway if gateway is not None else default_gemini()

    # 게이트웨이가 호출 불가(키 없음)면 바로 fallback (원본 동작)
    if not gw.available:
        log.warning("LLM 게이트웨이 미가용 — SEO fallback 사용")
        return fallback

    try:
        raw = gw.complete(_SEO_SYSTEM, _build_user_msg(script, planner_trends),
                          model=model, temperature=0.6)
        data = json.loads(raw)
    except Exception as exc:
        log.exception(f"SEO 생성 실패 — fallback 사용 ({exc})")
        return fallback

    return _normalize(data, script, category, fallback)
