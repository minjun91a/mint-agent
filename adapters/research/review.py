# -*- coding: utf-8 -*-
"""
adapters.research.review — 리서치 다이제스트 발행 전 **출처 인용 검증** (produce/**review**/publish의 review).

`core.trust.Reviewer` 구현. youtube의 `ComplianceReviewer`와 **규칙도 정책도 전혀 다르지만**
결과는 같은 `Verdict` 어휘로 돌려준다 — 엔진은 둘을 구분하지 않는다.

**정직성: 이식이 아니라 신규 구축이다.** 원본 `youtube-agent/agents/research.py`에는 검증 단계가
없다(검색→페치→요약이 전부). 그러면 무엇을 근거로 규칙을 정하는가 —
**`summarize.SYSTEM_PROMPT`가 모델에게 약속시킨 것들**이다:

    - 각 사실 옆에 [출처 N] 형식으로 인용 (N은 입력 SOURCES 인덱스)
    - 발췌에 없는 내용은 "출처 부족"이라고 명시, 추측 금지
    - 마지막에 "## 출처" 섹션에 모두 나열

즉 **프롬프트는 계약이고, review는 그 계약의 테스트**다. 모델이 약속을 지켰는지는 대부분 LLM 없이
검증 가능한 **사실 문제**다(인용 번호가 범위 안인가, 그 소스를 실제로 읽기는 했는가). 그래서 룰 층은
완전히 결정적이다. LLM은 "발췌에 없는 주장"처럼 룰로 못 잡는 것에만 쓴다(`deep=True` + 근거 본문 주입).

정책(youtube와 다름 — 어댑터의 자율):
    - high 위반(존재하지 않는 출처 인용 등) → `reject` · `passed=False`
    - 위반 없음                              → `approve` · `passed=True`
    - med/low만                              → `review`(HITL) · `passed=True`
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from core.gateway import default_gemini
from core.llm import LLMGateway
from core.trust.verify import Verdict, Violation

log = logging.getLogger("mint.adapters.research.review")

_DEFAULT_MODEL = "gemini-2.5-flash-lite"

# `[출처 3]` · `[출처3]` — summarize가 심는 인용 마커(SYSTEM_PROMPT 규칙 2).
_CITATION = re.compile(r"\[출처\s*(\d+)\]")

# SYSTEM_PROMPT 규칙 4가 요구하는 말미 섹션.
_SOURCES_HEADING = "## 출처"

# produce가 실패 사유를 요약 자리에 담아 돌려줄 때의 접두사(`NO_HITS`·`NO_SOURCES`·`UNAVAILABLE`).
_FAILURE_PREFIX = "❌"

_LLM_SYSTEM = """당신은 리서치 요약의 근거 검증자입니다.
SUMMARY의 각 주장이 SOURCES 발췌로 뒷받침되는지 판정하세요.

Output JSON only:
{
  "passed": true/false,
  "score": 0.0~1.0,
  "violations": [
    {"category": "unsupported_claim", "severity": "low|med|high", "detail": "..."}
  ],
  "recommendation": "approve" | "review" | "reject"
}

규칙:
- 발췌에 근거가 없는 사실 주장 → severity="high"
- 발췌를 과장·왜곡한 주장 → severity="med"
- 판단이 애매하면 recommendation="review" (사람이 결정한다)
"""


def _score(violations: list[Violation]) -> float:
    """위험 점수: 위반 1건당 0.15, 상한 1.0. (youtube 게이트의 규약을 그대로 상속)"""
    return min(1.0, len(violations) * 0.15)


# ── 순수 룰 층 ────────────────────────────────────────────────────────────


def extract_citations(summary: str) -> list[int]:
    """요약에서 인용 번호를 등장 순서대로 뽑는다(중복 포함). (순수)"""
    return [int(m.group(1)) for m in _CITATION.finditer(summary)]


def check_citations(digest: dict[str, Any]) -> list[Violation]:
    """다이제스트 → 인용 무결성 위반 목록. LLM·네트워크 없이 완전히 결정적. (순수)

    `digest`는 `ResearchAdapter.produce`의 반환형 — `{"query", "summary", "hits":[{... ,"fetched_chars"}]}`.
    """
    violations: list[Violation] = []
    summary = str(digest.get("summary", "") or "")
    hits = list(digest.get("hits") or [])

    # 0. 요약 자체가 없거나 실패 사유다 → 발행 불가.
    if not summary.strip() or summary.startswith(_FAILURE_PREFIX):
        violations.append(Violation(
            category="summary_missing",
            severity="high",
            detail=f"요약이 비었거나 실패 사유입니다: '{summary[:60]}'",
        ))
        return violations  # 이후 인용 검사는 의미 없음

    cited = extract_citations(summary)

    # 1. 인용이 하나도 없다 → 근거 없는 주장(SYSTEM_PROMPT 규칙 2 위반).
    if hits and not cited:
        violations.append(Violation(
            category="no_citation",
            severity="high",
            detail=f"출처 {len(hits)}건을 제공했으나 요약에 [출처 N] 인용이 없습니다.",
        ))

    fetched = {i for i, h in enumerate(hits, start=1) if int(h.get("fetched_chars", 0) or 0) > 0}

    for n in sorted(set(cited)):
        # 2. 존재하지 않는 출처 번호 → 환각 인용.
        if n < 1 or n > len(hits):
            violations.append(Violation(
                category="citation_out_of_range",
                severity="high",
                detail=f"[출처 {n}]을 인용했으나 출처는 {len(hits)}건뿐입니다.",
            ))
        # 3. 본문을 못 읽은 소스를 인용 → 요약 프롬프트에 들어가지도 않은 근거.
        elif n not in fetched:
            violations.append(Violation(
                category="citation_unfetched_source",
                severity="high",
                detail=f"[출처 {n}]은 본문 페치에 실패해(fetched_chars=0) 요약 입력에 없었습니다.",
            ))

    # 4. 출처 섹션 누락 (SYSTEM_PROMPT 규칙 4).
    if _SOURCES_HEADING not in summary:
        violations.append(Violation(
            category="missing_sources_section",
            severity="med",
            detail=f"말미에 '{_SOURCES_HEADING}' 섹션이 없습니다.",
        ))

    # 5. 읽어놓고 인용하지 않은 소스 (정보성 — 요약이 근거를 놓쳤을 수 있다).
    unused = sorted(fetched - set(cited))
    if unused:
        violations.append(Violation(
            category="unused_source",
            severity="low",
            detail=f"본문을 가져왔으나 인용되지 않은 출처: {unused}",
        ))

    return violations


def _rule_only_verdict(violations: list[Violation]) -> Verdict:
    """룰 결과만으로 Verdict 구성 (deep=False · 근거 본문 미제공 · LLM 미가용 경로).

    youtube 게이트와 **정책이 다르다**: 환각 인용은 사람 판단을 기다릴 것 없이 거절한다.
    """
    has_high = any(v.severity == "high" for v in violations)
    if has_high:
        recommendation = "reject"
    elif violations:
        recommendation = "review"
    else:
        recommendation = "approve"
    return Verdict(
        passed=not has_high,
        score=_score(violations),
        violations=violations,
        recommendation=recommendation,
    )


# ── LLM 심층 층 (근거 대조) ───────────────────────────────────────────────


def _build_deep_prompt(digest: dict[str, Any], sources: list[str],
                       rule_violations: list[Violation]) -> str:
    """근거 대조용 프롬프트 (순수). 본문이 있는 소스만 넣는다."""
    hits = list(digest.get("hits") or [])
    blocks = [
        {"n": i, "title": h.get("title", ""), "url": h.get("url", ""), "excerpt": body}
        for i, (h, body) in enumerate(zip(hits, sources), start=1) if body
    ]
    return json.dumps({
        "QUERY": digest.get("query", ""),
        "SUMMARY": digest.get("summary", ""),
        "SOURCES": blocks,
        "RULE_VIOLATIONS": [v.to_dict() for v in rule_violations],
    }, ensure_ascii=False, indent=2, default=str)


def _llm_deep_check(digest: dict[str, Any], sources: list[str],
                    rule_violations: list[Violation],
                    *, model: str = _DEFAULT_MODEL,
                    gateway: LLMGateway | None = None) -> Verdict:
    """게이트웨이 근거 대조. 미가용이면 룰 결과, 호출 실패 시 룰 결과(단 high 없으면 'review'로 안전화).

    youtube 게이트와 같은 구분: LLM **미가용**(키 없음)은 자연스러운 룰 권고를 그대로 두고,
    LLM **오류**는 판단 근거가 사라진 것이므로 사람에게 넘긴다.
    """
    gw = gateway if gateway is not None else default_gemini()
    if not gw.available:
        log.warning("LLM 게이트웨이 미가용 — 룰 검증 결과만 사용")
        return _rule_only_verdict(rule_violations)

    try:
        raw = gw.complete(_LLM_SYSTEM, _build_deep_prompt(digest, sources, rule_violations),
                          model=model, temperature=0.2)
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 — LLM 오류는 사람 판단으로 격리
        log.exception("근거 대조 LLM 실패 — 룰 결과만 사용: %s", exc)
        has_high = any(v.severity == "high" for v in rule_violations)
        return Verdict(
            passed=not has_high,
            score=_score(rule_violations),
            violations=rule_violations,
            recommendation="reject" if has_high else "review",
        )

    llm_violations = [
        Violation(
            category=str(v.get("category", "unsupported_claim")),
            severity=v.get("severity", "low"),
            detail=str(v.get("detail", "")),
        )
        for v in (data.get("violations") or [])
    ]
    violations = list(rule_violations) + llm_violations
    has_high = any(v.severity == "high" for v in violations)

    # Verdict 계약(score∈[0,1], recommendation∈{approve,review,reject})을 강제한다.
    # youtube 게이트가 실 로그에서 미정규화 점수(score=40)를 흘린 전례가 있다 — eval이 잡았고
    # `8fc909f`에서 클램프했다. 같은 실수를 두 번 하지 않는다.
    raw_score = float(data.get("score", 0.0) or 0.0)
    raw_rec = str(data.get("recommendation", "review"))
    recommendation = raw_rec if raw_rec in ("approve", "review", "reject") else "review"
    if has_high:
        recommendation = "reject"   # 불변식: reject ⟸ high (그리고 passed=False)

    return Verdict(
        passed=bool(data.get("passed", True)) and not has_high,
        score=max(0.0, min(1.0, raw_score)),
        violations=violations,
        recommendation=recommendation,
    )


# ── 진입점 ────────────────────────────────────────────────────────────────


def review(digest: dict[str, Any], sources: list[str] | None = None, *,
           deep: bool = True, model: str = _DEFAULT_MODEL,
           gateway: LLMGateway | None = None) -> Verdict:
    """다이제스트 → 검증 Verdict.

    `sources`(hits와 같은 순서의 본문 목록)를 주고 `deep=True`면 근거 대조까지 한다.
    본문 없이는 근거를 대조할 수 없으므로 **룰 검증만** 수행한다(없는 능력을 흉내내지 않는다).
    """
    rule_v = check_citations(digest)
    if not deep or not sources:
        return _rule_only_verdict(rule_v)
    return _llm_deep_check(digest, sources, rule_v, model=model, gateway=gateway)


class CitationReviewer:
    """`core.trust.Reviewer` 구현 — 리서치 다이제스트 발행 전 게이트.

    `context`는 엔진이 해석하지 않고 넘겨주는 부가 자료다. 여기서는 `{"bodies": [...]}`로 근거 본문을
    받는다(없으면 룰 검증만).
    """

    def __init__(self, *, deep: bool = True, model: str = _DEFAULT_MODEL,
                 gateway: LLMGateway | None = None) -> None:
        self.deep = deep
        self.model = model
        self.gateway = gateway

    def review(self, content: dict[str, Any], context: Any = None) -> Verdict:
        sources = context.get("bodies") if isinstance(context, dict) else None
        return review(content, sources, deep=self.deep, model=self.model, gateway=self.gateway)
