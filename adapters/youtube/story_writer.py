# -*- coding: utf-8 -*-
"""
adapters.youtube.story_writer — 트로프 → 1분 영문 쇼츠 스크립트 (produce 단계, LLM).

`youtube-agent/agents/story_writer.py`를 모노레포로 이식. 원본은 ① OpenAI 호출 ② 트로프 풀
파일 I/O ③ 무작위 선택 ④ 시간 기반 사용이력 dedup이 한 함수에 엉켜 있어 결정적 테스트가
불가능했다. 경계를 갈라 각각을 검증한다:

  1. **순수 로직** — `format_tropes`/`_build_user_msg`(트로프 → 프롬프트 문자열). 결정적 → 특성화.
  2. **상태 I/O** — `TropeLibrary`(풀 로딩·사용이력·선택). 경로·clock·rng를 **주입**해 결정적화
     (엔진 `core/memory`의 DB 경로 주입과 같은 패턴).
  3. **LLM 경계** — `write_script(..., gateway=...)`로 `core.llm.LLMGateway`를 **주입**. 기본은
     `OpenAIGateway`(원본과 동일 프로바이더), 미가용 시 원본처럼 `RuntimeError`.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import yaml

from core.gateway import default_openai
from core.llm import LLMGateway

log = logging.getLogger("mint.adapters.youtube.story_writer")

_DEFAULT_MODEL = "gpt-4o-mini"

# 원본 story_writer.SYSTEM_PROMPT과 동일 (영문 쇼츠 작법 지시).
SYSTEM_PROMPT = """You are an English-language fantasy shorts writer for YouTube. Write a 90-second short script.

# Channel Identity

CHANNEL: Manhwa Decoded (@manhwadecodedHQ).
CONCEPT: AI pastiche that decodes K-webtoon tropes — beyond the recap.
Five axes: Why / Patterns / Structure / Data / Automation.

Target audience: Global English speakers, fans of murim/cultivation, regression, LitRPG, dungeon/tower, Korean webtoon/manhwa.
Tone: Cinematic, dramatic, voice-of-fate. American English with a hint of mythic gravitas.

⭐ FIRST 5 SECONDS — THE HOOK (most critical). Use exactly ONE of these 4 patterns:
   (1) Shock fact      : "Ten years I waited. Yesterday, he died."
   (2) Forbidden truth : "The oracle has been lying for a thousand years."
   (3) Number + twist  : "I regressed nine times. The tenth time, my enemy remembered me."
   (4) Taboo revelation: "They told me this sword would kill me. They all lied."
   Hook = 1 line, 8~14 words, opening word must be a number, question word, or shocking noun.

BODY 60~75 seconds:
   - Event-driven. Show, don't describe. One line = 8~14 words = ~1.5 seconds TTS.
   - 3-act mini-arc: Conflict -> Revelation -> Twist
   - Never flat. Every 5 seconds delivers a new information unit.
   - Use vivid murim/cultivation/regression vocabulary: qi, ascension, S-rank, dungeon, oracle, contract, awakening, cycle, necromancy.

CLIFFHANGER 5~10 seconds:
   - Make them need part 2. Trail off mid-revelation: "But the moment he saw what was behind the door..."
   - Question-form ending preferred.

SCENES (5~7):
   - Each scene: narration_segment (English line) + image_prompt (English visual)
   - All 5~7 scenes share the same art style keywords for consistency.

⭐ IMAGE PROMPT UNIFIED RULES (every scene + thumbnail):
   - Always include: "korean webtoon manhwa style, painterly, soft cinematic lighting, vibrant colors, detailed character art, 9:16 vertical composition"
   - Recurring character: use identical descriptors across scenes (e.g. "young man with silver hair and crimson cloak" in all 5)
   - FORBIDDEN: battle, fight, kill, blood, weapon, monster, demon, violence, dark, scary, tense, intense, war, gore
   - PREFERRED: ethereal, magical, dramatic, dreamy, mystical, glowing, otherworldly, ascending, contemplative

⭐ THUMBNAIL:
   - thumbnail_prompt: most impactful scene as close-up, dramatic lighting
   - thumbnail_text: ENGLISH, 2~5 words, ALL CAPS, punchy. (e.g. "10TH REGRESSION", "GOD LIED", "S-RANK SECRET")

⭐ TITLE:
   - English, ≤60 chars, click-worthy.
   - Pattern: number/secret/forbidden/last + noun. (e.g. "The 10th Regression Changed Everything", "She Wasn't Just a Slave Girl")

⭐ OUTPUT — JSON only, nothing else:
{
  "title": "click-worthy English title (<=60 chars)",
  "hook": "1 line English, 8~14 words",
  "body": "multi-line English, 60~75 sec worth",
  "cliffhanger": "1~2 lines English, 5~10 sec",
  "scenes": [
    {"order": 1, "narration_segment": "English line spoken in this scene", "image_prompt": "english visual prompt with korean webtoon manhwa style, painterly, soft cinematic lighting, vibrant colors, detailed character art, 9:16 vertical composition"}
  ],
  "thumbnail_prompt": "english thumbnail prompt, dramatic close-up, korean webtoon manhwa style, 9:16 vertical",
  "thumbnail_text": "ENGLISH 2~5 WORDS, ALL CAPS"
}
"""


# ── 순수 로직 ──────────────────────────────────────────────────────────────

def format_tropes(tropes: list[dict[str, Any]]) -> str:
    """트로프 카드들을 프롬프트용 한 줄씩 문자열로. 원본 `trope_text` 구성과 동일."""
    return "\n".join(
        f"- [{t['id']}/{t['category']}] hook='{t['hook']}' setup='{t['setup']}' twist='{t['twist']}'"
        for t in tropes
    )


def _build_user_msg(tropes: list[dict[str, Any]]) -> str:
    return ("Combine these tropes into a 90-second English fantasy short script:\n\n"
            + format_tropes(tropes))


# ── 상태 I/O: 트로프 풀·사용이력·선택 (경로·clock·rng 주입) ────────────────

class TropeLibrary:
    """트로프 풀 로딩 + 최근 사용 dedup + 선택. 위치/시간/무작위성을 전부 주입해 결정적으로 만든다.

    원본은 모듈 상수 경로(`data/tropes.yaml`, `data/used_tropes.json`)와 `datetime.now()`,
    `random`에 직접 의존했다 → 여기서는 생성자 주입으로 위치·시간 결합을 끊는다.
    """

    def __init__(self, *, tropes_path: str | Path, used_log_path: str | Path,
                 now: Callable[[], datetime] | None = None) -> None:
        self.tropes_path = Path(tropes_path)
        self.used_log_path = Path(used_log_path)
        self._now = now or datetime.now

    def load(self) -> list[dict[str, Any]]:
        return yaml.safe_load(self.tropes_path.read_text(encoding="utf-8"))["tropes"]

    def recent_used(self, hours: int = 48) -> set[str]:
        """최근 `hours` 내 사용된 트로프 ID 집합. 원본 `_load_used_recent`과 동일."""
        if not self.used_log_path.exists():
            return set()
        try:
            data = json.loads(self.used_log_path.read_text(encoding="utf-8"))
        except Exception:
            return set()
        cutoff = self._now() - timedelta(hours=hours)
        return {e["id"] for e in data if datetime.fromisoformat(e["at"]) > cutoff}

    def record_used(self, ids: list[str]) -> None:
        """사용 ID 기록 + 7일 이전 항목 정리. 원본 `_record_used`와 동일."""
        self.used_log_path.parent.mkdir(parents=True, exist_ok=True)
        history: list[dict[str, str]] = []
        if self.used_log_path.exists():
            try:
                history = json.loads(self.used_log_path.read_text(encoding="utf-8"))
            except Exception:
                history = []
        now = self._now().isoformat(timespec="seconds")
        for tid in ids:
            history.append({"id": tid, "at": now})
        cutoff = self._now() - timedelta(days=7)
        history = [e for e in history if datetime.fromisoformat(e["at"]) > cutoff]
        self.used_log_path.write_text(
            json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    def pick(self, n: int = 2, *, rng: random.Random | None = None) -> list[dict[str, Any]]:
        """최근 48h 미사용 트로프 중 n개 무작위 선택. 부족하면 풀 전체 재오픈(원본 동일)."""
        chooser = rng or random
        pool = self.load()
        used = self.recent_used()
        fresh = [t for t in pool if t["id"] not in used]
        if len(fresh) < n:
            fresh = pool
        return chooser.sample(fresh, k=min(n, len(fresh)))


# ── LLM 경계 ───────────────────────────────────────────────────────────────

def write_script(tropes: list[dict[str, Any]], *, model: str = _DEFAULT_MODEL,
                 gateway: LLMGateway | None = None) -> dict[str, Any]:
    """트로프 → 스크립트 dict. LLM은 게이트웨이 주입(기본 `OpenAIGateway`).

    게이트웨이 미가용(키 없음)이면 원본처럼 `RuntimeError`. 결과에 `_used_tropes`를 부착한다.
    (사용이력 파일 기록은 부수효과이므로 분리 — `generate()`가 묶는다.)
    """
    gw = gateway if gateway is not None else default_openai()
    if not gw.available:
        raise RuntimeError("LLM 게이트웨이 미가용 — OPENAI_API_KEY 확인")

    raw = gw.complete(SYSTEM_PROMPT, _build_user_msg(tropes), model=model, temperature=0.9)
    data = json.loads(raw)
    data["_used_tropes"] = [t["id"] for t in tropes]
    log.info(f"스크립트 생성: title='{data.get('title', '')}' tropes={data['_used_tropes']}")
    return data


def generate(library: TropeLibrary, *, n: int = 2, model: str = _DEFAULT_MODEL,
             gateway: LLMGateway | None = None,
             rng: random.Random | None = None) -> dict[str, Any]:
    """원본 `write_script(tropes=None)` 흐름 재현: 트로프 선택 → 스크립트 생성 → 사용이력 기록."""
    tropes = library.pick(n, rng=rng)
    data = write_script(tropes, model=model, gateway=gateway)
    library.record_used(data["_used_tropes"])
    return data
