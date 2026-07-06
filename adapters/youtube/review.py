# -*- coding: utf-8 -*-
"""
adapters.youtube.review — YouTube 발행 전 컴플라이언스 게이트 (produce/**review**/publish의 review).

`youtube-agent/agents/compliance.py`를 모노레포로 이식. 룰 검사층(`_quick_rule_check`)은
결정적(정규식, I/O 없음)이고, `deep=True`일 때만 Gemini 심층 검사(키 없으면 룰 결과로 폴백).
**룰 검사층의 동작은 원본과 동일**하며 `tests/test_youtube_review.py`의 특성화 테스트로 고정한다.

결과는 `core.trust.Verdict`로 표준화한다. 원본의 dict 반환과의 호환은 `check()`가 제공.

이식 시 의도적 차이 1건(비결정 LLM 경로에 한정): 원본은 LLM이 돌려준 violation을 raw dict로
이어붙였지만, 여기서는 `Violation`으로 정규화한다(category/severity/detail 누락 시 안전 기본값).
결정적 룰 경로(테스트 대상)에는 영향 없음.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from core.gateway import default_gemini
from core.llm import LLMGateway
from core.trust.verify import Verdict, Violation

log = logging.getLogger("mint.adapters.youtube.review")

_DEFAULT_MODEL = "gemini-2.5-flash-lite"

# ── 빠른 룰 체크 패턴 (원본 동일) ──────────────────────────────────────────

# 명백한 위반 신호 — script/title/description에 있으면 high 위반
_HARD_BLOCK_PATTERNS = [
    re.compile(r"\b(suicide|kill yourself|self.harm)\b", re.I),
    re.compile(r"\b(porn|nude|sexual|nsfw)\b", re.I),
    re.compile(r"\b(racial slur|n.word)\b", re.I),
    re.compile(r"\b(crypto.{0,5}scam|guaranteed return|easy money)\b", re.I),
]

# AI 라벨링 — description에 반드시 포함되어야 할 마커
_AI_LABEL_MARKERS = [
    "generative ai", "ai-generated", "🤖", "AI-generated assets",
    "altered or synthetic",  # YouTube 정책 표현
]

# 의료/금융/법률 자문 신호 (script에 있으면 medium 경고)
_PROF_ADVICE_HINTS = [
    re.compile(r"\b(diagnose|cure|treatment|medication|prescribe)\b", re.I),
    re.compile(r"\b(invest in|buy this stock|guaranteed profit)\b", re.I),
    re.compile(r"\b(legal advice|lawsuit|sue)\b", re.I),
]

_LLM_SYSTEM = """You are a YouTube AI content compliance reviewer.
Given a SCRIPT + SEO_META JSON, classify if it complies with these policies:
- YouTube AI generated content labeling (must disclose synthetic content)
- EU AI Act Article 50 (generative AI marking obligation)
- Korean AI Basic Act (synthetic content notification)
- General platform ToS (no defamation, no real-person deepfake without consent, no incitement)

Output JSON only:
{
  "passed": true/false,
  "score": 0.0~1.0,                 # risk score (0=safe, 1=critical)
  "violations": [
    {"category": "...", "severity": "low|med|high", "detail": "..."}
  ],
  "recommendation": "approve" | "review" | "reject"
}

Rules:
- If high severity violation: passed=false, recommendation=reject
- If only low/med: passed=true (with violations listed for record), recommendation=approve or review
- Be conservative: when in doubt, recommendation="review" (human decides via Discord button).
"""


def _score(violations: list[Violation]) -> float:
    """원본과 동일한 위험 점수: 위반 1건당 0.15, 상한 1.0."""
    return min(1.0, len(violations) * 0.15)


def _quick_rule_check(script: dict[str, Any], seo: dict[str, Any]) -> list[Violation]:
    """LLM 호출 전 빠른 정규식 검사 (결정적). 원본 로직과 동일."""
    violations: list[Violation] = []

    full_text = " ".join([
        str(script.get("title", "")),
        str(script.get("hook", "")),
        str(script.get("body", "")),
        str(script.get("cliffhanger", "")),
        str(seo.get("title", "")),
        str(seo.get("description", "")),
    ])
    description_text = str(seo.get("description", "") or "")

    # 1. Hard block 패턴
    for pat in _HARD_BLOCK_PATTERNS:
        m = pat.search(full_text)
        if m:
            violations.append(Violation(
                category="hard_block",
                severity="high",
                detail=f"금지 표현 매칭: '{m.group(0)}'",
            ))

    # 2. AI 라벨링 — description에 마커 1개 이상 포함되어야
    if not any(m.lower() in description_text.lower() for m in _AI_LABEL_MARKERS):
        violations.append(Violation(
            category="ai_labeling",
            severity="med",
            detail="description에 AI 생성 콘텐츠 disclosure 미포함 — "
                   "'🤖 generative AI' 또는 'AI-generated assets' 추가 권장",
        ))

    # 3. 전문가 자문 영역
    for pat in _PROF_ADVICE_HINTS:
        m = pat.search(full_text)
        if m:
            violations.append(Violation(
                category="professional_advice",
                severity="med",
                detail=f"전문가 자문 영역 표현 감지: '{m.group(0)}' — 면책 명시 또는 표현 변경 필요",
            ))
            break  # 1건만 신호로 충분

    return violations


def _rule_only_verdict(violations: list[Violation]) -> Verdict:
    """룰 결과만으로 Verdict 구성 (deep=False · LLM 키 없음 경로). 원본과 동일."""
    return Verdict(
        passed=not any(v.severity == "high" for v in violations),
        score=_score(violations),
        violations=violations,
        recommendation="approve" if not violations else "review",
    )


def _llm_deep_check(script: dict[str, Any], seo: dict[str, Any],
                    rule_violations: list[Violation],
                    *, model: str = _DEFAULT_MODEL,
                    gateway: LLMGateway | None = None) -> Verdict:
    """게이트웨이 심층 분석. 미가용(키 없음)이면 룰 결과, 호출 실패 시 룰 결과(단 recommendation='review').

    LLM 미가용과 LLM 오류를 구분한다: 전자는 자연스러운 룰 권고를 그대로 두고, 후자는 안전하게
    'review'(사람이 판단)로 강제한다. (원본 동작 보존)
    """
    gw = gateway if gateway is not None else default_gemini()
    if not gw.available:
        return _rule_only_verdict(rule_violations)

    try:
        prompt = json.dumps({
            "SCRIPT": {k: script.get(k, "") for k in
                       ("title", "hook", "body", "cliffhanger", "category", "_used_tropes")},
            "SEO_META": {k: seo.get(k, "") for k in
                         ("title", "description", "tags", "hashtags", "category_id")},
            "RULE_VIOLATIONS": [v.to_dict() for v in rule_violations],
        }, ensure_ascii=False, indent=2, default=str)
        raw = gw.complete(_LLM_SYSTEM, prompt, model=model, temperature=0.2)
        data = json.loads(raw)
    except Exception as exc:
        log.exception(f"compliance LLM 실패 — 룰 결과만 사용: {exc}")
        # 원본 동작: LLM 실패 시 recommendation은 항상 'review'로 안전화
        return Verdict(
            passed=not any(v.severity == "high" for v in rule_violations),
            score=_score(rule_violations),
            violations=rule_violations,
            recommendation="review",
        )

    # LLM violation을 Violation으로 정규화 후 룰 결과와 병합
    llm_violations = [
        Violation(
            category=str(v.get("category", "?")),
            severity=v.get("severity", "low"),
            detail=str(v.get("detail", "")),
        )
        for v in (data.get("violations") or [])
    ]
    violations = list(rule_violations) + llm_violations
    # 원본과 **의도적 차이**: eval 하니스(core/trust/eval)가 실 로그에서 검출한 결함을 교정한다.
    # 원본 LLM 경로는 미정규화 점수(score=40 등)·미검증 권고를 그대로 통과시켜 Verdict 계약
    # (score∈[0,1], recommendation∈{approve,review,reject})을 어겼다. 여기서 계약에 맞게
    # 클램프·검증한다. 미확인 권고는 원본 정신("의심스러우면 review")대로 review로 안전화.
    raw_score = float(data.get("score", 0.0) or 0.0)
    raw_rec = str(data.get("recommendation", "review"))
    return Verdict(
        passed=bool(data.get("passed", True)) and not any(v.severity == "high" for v in violations),
        score=max(0.0, min(1.0, raw_score)),
        violations=violations,
        recommendation=raw_rec if raw_rec in ("approve", "review", "reject") else "review",
    )


# ── 진입점 ────────────────────────────────────────────────────────────────

def review(script: dict[str, Any], seo: dict[str, Any], *,
           deep: bool = True, model: str = _DEFAULT_MODEL,
           gateway: LLMGateway | None = None) -> Verdict:
    """script + SEO 메타 → 검증 Verdict.

    deep=True (기본): 룰 + 게이트웨이 LLM 심층. deep=False: 룰만(오프라인·비용 절감, 완전 결정적).
    gateway: LLM 게이트웨이 주입(테스트용 fake). 미지정 시 실 `GeminiGateway`.
    """
    rule_v = _quick_rule_check(script, seo)
    if not deep:
        return _rule_only_verdict(rule_v)
    return _llm_deep_check(script, seo, rule_v, model=model, gateway=gateway)


def check(script: dict[str, Any], seo: dict[str, Any], *,
          deep: bool = True, model: str = _DEFAULT_MODEL,
          gateway: LLMGateway | None = None) -> dict[str, Any]:
    """레거시 호환 진입점 — 원본 `compliance.check`와 동일한 dict 반환."""
    return review(script, seo, deep=deep, model=model, gateway=gateway).to_dict()


class ComplianceReviewer:
    """`core.trust.Reviewer` 구현 — YouTube 발행 전 게이트."""

    def __init__(self, *, deep: bool = True, model: str = _DEFAULT_MODEL,
                 gateway: LLMGateway | None = None) -> None:
        self.deep = deep
        self.model = model
        self.gateway = gateway

    def review(self, script: dict[str, Any], seo: dict[str, Any]) -> Verdict:
        return review(script, seo, deep=self.deep, model=self.model, gateway=self.gateway)


def format_violations_md(result: Verdict | dict[str, Any]) -> str:
    """Discord embed용 위반 사유 마크다운 (원본과 동일 출력)."""
    if isinstance(result, Verdict):
        vs: list[Any] = list(result.violations)
    else:
        vs = result.get("violations") or []
    if not vs:
        return "_위반 사항 없음_"
    sev_icon = {"high": "🔴", "med": "🟠", "low": "🟡"}
    lines = []
    for v in vs[:10]:
        sev = v.severity if isinstance(v, Violation) else v.get("severity", "low")
        cat = v.category if isinstance(v, Violation) else v.get("category", "?")
        detail = (v.detail if isinstance(v, Violation) else v.get("detail", "") or "")[:200]
        icon = sev_icon.get(sev, "⚪")
        lines.append(f"{icon} **{cat}** — {detail}")
    return "\n".join(lines)
