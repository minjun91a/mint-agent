# -*- coding: utf-8 -*-
"""
adapters.research.review 테스트 — 출처 인용 검증.

원본에 대응물이 없는 **신규 구축**이라 parity 대조 대상이 아니다. 대신 검증 규칙의 근거는
`summarize.SYSTEM_PROMPT`가 모델에게 약속시킨 항목들이며, 그 약속이 지켜졌는지를 고정한다.

룰 층은 완전히 순수(LLM·네트워크 없음)라 목 없이 검증한다. 근거 대조(deep)만 가짜 게이트웨이 주입.
"""
from __future__ import annotations

import json

from adapters.research.review import (
    CitationReviewer,
    check_citations,
    extract_citations,
    review,
)
from core.trust.verify import Verdict


def _digest(summary: str, *, fetched: list[int] | None = None, n: int = 2) -> dict:
    """hits n건짜리 다이제스트. `fetched`에 든 번호(1-based)만 본문을 가져온 것으로 본다."""
    got = set(range(1, n + 1)) if fetched is None else set(fetched)
    return {
        "query": "질의",
        "summary": summary,
        "hits": [
            {"title": f"T{i}", "url": f"https://e{i}.com", "snippet": f"s{i}",
             "fetched_chars": 100 if i in got else 0}
            for i in range(1, n + 1)
        ],
    }


GOOD = "- 사실 A [출처 1]\n- 사실 B [출처 2]\n\n## 출처\n1. T1 — https://e1.com\n2. T2 — https://e2.com"


class _FakeGateway:
    """주입용 가짜 LLM 게이트웨이 — canned JSON 반환 또는 예외."""

    def __init__(self, *, payload: dict | None = None, raw: str | None = None,
                 raises: Exception | None = None, available: bool = True) -> None:
        self._raw = raw if raw is not None else json.dumps(payload or {})
        self._raises = raises
        self.available = available
        self.calls: list[dict] = []

    def complete(self, system: str, prompt: str, **kwargs) -> str:
        self.calls.append({"system": system, "prompt": prompt, **kwargs})
        if self._raises:
            raise self._raises
        return self._raw


# ── 순수: extract_citations ─────────────────────────────────────────


def test_extract_citations_in_order_with_duplicates():
    assert extract_citations("a [출처 2] b [출처 1] c [출처 2]") == [2, 1, 2]


def test_extract_citations_tolerates_missing_space():
    assert extract_citations("[출처3]") == [3]


def test_extract_citations_none_found():
    assert extract_citations("근거 없는 문장") == []


# ── 순수: check_citations ───────────────────────────────────────────


def test_clean_digest_has_no_violations():
    assert check_citations(_digest(GOOD)) == []


def test_empty_summary_is_high_and_short_circuits():
    vs = check_citations(_digest(""))
    assert [v.category for v in vs] == ["summary_missing"]
    assert vs[0].severity == "high"


def test_failure_reason_summary_is_high():
    vs = check_citations(_digest("❌ 검색 결과 없음"))
    assert [v.category for v in vs] == ["summary_missing"]


def test_no_citation_when_hits_exist_is_high():
    vs = check_citations(_digest("근거 없는 주장입니다.\n\n## 출처\n1. T1"))
    cats = [v.category for v in vs]
    assert "no_citation" in cats
    assert next(v for v in vs if v.category == "no_citation").severity == "high"


def test_citation_out_of_range_is_high():
    vs = check_citations(_digest("사실 [출처 5]\n\n## 출처\n1. T1"))
    v = next(v for v in vs if v.category == "citation_out_of_range")
    assert v.severity == "high"
    assert "출처는 2건뿐" in v.detail


def test_citing_unfetched_source_is_high():
    """2번은 페치 실패라 요약 프롬프트에 들어가지도 않았다 — 인용했다면 환각이다."""
    vs = check_citations(_digest("사실 [출처 1]\n사실 [출처 2]\n\n## 출처\n1. T1", fetched=[1]))
    v = next(v for v in vs if v.category == "citation_unfetched_source")
    assert v.severity == "high"
    assert "[출처 2]" in v.detail


def test_missing_sources_section_is_med():
    vs = check_citations(_digest("- 사실 A [출처 1]\n- 사실 B [출처 2]"))
    v = next(v for v in vs if v.category == "missing_sources_section")
    assert v.severity == "med"


def test_unused_fetched_source_is_low():
    vs = check_citations(_digest("- 사실 A [출처 1]\n\n## 출처\n1. T1"))
    v = next(v for v in vs if v.category == "unused_source")
    assert v.severity == "low"
    assert "[2]" in v.detail


def test_no_hits_and_no_citation_is_not_flagged():
    # hits가 없으면 인용이 없는 게 정상이다(요약이 "출처 부족"을 말했을 수 있다).
    d = {"query": "q", "summary": "출처 부족\n\n## 출처\n(없음)", "hits": []}
    assert [v.category for v in check_citations(d)] == []


def test_duplicate_bad_citation_reported_once():
    vs = check_citations(_digest("[출처 9] 그리고 [출처 9]\n\n## 출처\n1. T1"))
    assert sum(v.category == "citation_out_of_range" for v in vs) == 1


# ── 룰 전용 Verdict 정책 ────────────────────────────────────────────


def test_clean_digest_is_approved():
    v = review(_digest(GOOD), deep=False)
    assert (v.recommendation, v.passed, v.score) == ("approve", True, 0.0)


def test_high_violation_is_rejected_not_deferred():
    """youtube 게이트와 다른 정책: 환각 인용은 사람을 기다리지 않고 거절한다."""
    v = review(_digest("사실 [출처 9]\n\n## 출처\n1. T1"), deep=False)
    assert v.recommendation == "reject"
    assert v.passed is False


def test_only_low_med_defers_to_human():
    v = review(_digest("- A [출처 1]\n- B [출처 2]"), deep=False)   # 출처 섹션 누락(med)
    assert v.recommendation == "review"
    assert v.passed is True


def test_score_is_015_per_violation_capped():
    v = review(_digest("- A [출처 1]"), deep=False)   # missing_sources_section + unused_source
    assert v.score == 0.30


def test_verdict_satisfies_engine_invariants():
    """reject ⟹ ¬passed · approve ⟹ passed · score∈[0,1] (core.trust.eval의 불변식)."""
    for summary in (GOOD, "", "사실 [출처 9]", "- A [출처 1]"):
        v = review(_digest(summary), deep=False)
        assert 0.0 <= v.score <= 1.0
        if v.recommendation == "reject":
            assert v.passed is False
        if v.recommendation == "approve":
            assert v.passed is True


# ── deep: 근거 대조 (게이트웨이 주입) ───────────────────────────────


def test_deep_is_skipped_without_source_bodies():
    """근거 본문이 없으면 대조할 수 없다 — 없는 능력을 흉내내지 않는다."""
    gw = _FakeGateway(payload={"passed": True, "score": 0.0, "recommendation": "approve"})
    review(_digest(GOOD), None, deep=True, gateway=gw)
    assert gw.calls == []


def test_deep_merges_llm_violations_with_rule_violations():
    gw = _FakeGateway(payload={
        "passed": False, "score": 0.5, "recommendation": "reject",
        "violations": [{"category": "unsupported_claim", "severity": "high",
                        "detail": "발췌에 없는 수치"}],
    })
    v = review(_digest("- A [출처 1]"), ["본문1", "본문2"], deep=True, gateway=gw)

    cats = [x.category for x in v.violations]
    assert "missing_sources_section" in cats   # 룰
    assert "unsupported_claim" in cats         # LLM
    assert v.recommendation == "reject"


def test_deep_prompt_excludes_sources_without_body():
    gw = _FakeGateway(payload={"passed": True, "score": 0.0, "recommendation": "approve"})
    review(_digest(GOOD, fetched=[1]), ["본문1", ""], deep=True, gateway=gw)

    payload = json.loads(gw.calls[0]["prompt"])
    assert [b["n"] for b in payload["SOURCES"]] == [1]


def test_deep_unavailable_gateway_falls_back_to_rules():
    gw = _FakeGateway(available=False)
    v = review(_digest(GOOD), ["본문1", "본문2"], deep=True, gateway=gw)
    assert v.recommendation == "approve"
    assert gw.calls == []


def test_deep_llm_error_defers_to_human():
    gw = _FakeGateway(raises=RuntimeError("boom"))
    v = review(_digest(GOOD), ["본문1", "본문2"], deep=True, gateway=gw)
    assert v.recommendation == "review"   # 판단 근거가 사라졌다 → 사람에게
    assert v.passed is True


def test_deep_llm_error_with_rule_high_still_rejects():
    gw = _FakeGateway(raises=RuntimeError("boom"))
    v = review(_digest("사실 [출처 9]"), ["본문1", "본문2"], deep=True, gateway=gw)
    assert v.recommendation == "reject"
    assert v.passed is False


def test_deep_clamps_out_of_range_score():
    """youtube 게이트가 실 로그에서 흘린 score=40 결함을 여기선 애초에 막는다."""
    gw = _FakeGateway(payload={"passed": True, "score": 40, "recommendation": "approve"})
    assert review(_digest(GOOD), ["b1", "b2"], deep=True, gateway=gw).score == 1.0


def test_deep_rejects_unknown_recommendation_to_review():
    gw = _FakeGateway(payload={"passed": True, "score": 0.1, "recommendation": "맘에듦"})
    assert review(_digest(GOOD), ["b1", "b2"], deep=True, gateway=gw).recommendation == "review"


def test_deep_malformed_json_defers_to_human():
    gw = _FakeGateway(raw="not json at all")
    assert review(_digest(GOOD), ["b1", "b2"], deep=True, gateway=gw).recommendation == "review"


def test_deep_high_severity_forces_reject_even_if_llm_says_approve():
    """LLM이 approve라 해도 high 위반이 있으면 거절 — 불변식 reject ⟸ high."""
    gw = _FakeGateway(payload={
        "passed": True, "score": 0.1, "recommendation": "approve",
        "violations": [{"category": "unsupported_claim", "severity": "high", "detail": "d"}],
    })
    v = review(_digest(GOOD), ["b1", "b2"], deep=True, gateway=gw)
    assert v.recommendation == "reject"
    assert v.passed is False


# ── Reviewer 포트 ───────────────────────────────────────────────────


def test_citation_reviewer_returns_verdict():
    assert isinstance(CitationReviewer(deep=False).review(_digest(GOOD)), Verdict)


def test_citation_reviewer_reads_bodies_from_context():
    gw = _FakeGateway(payload={"passed": True, "score": 0.0, "recommendation": "approve"})
    CitationReviewer(gateway=gw).review(_digest(GOOD), {"bodies": ["b1", "b2"]})
    assert len(gw.calls) == 1


def test_citation_reviewer_ignores_non_dict_context():
    gw = _FakeGateway(payload={"passed": True, "score": 0.0, "recommendation": "approve"})
    CitationReviewer(gateway=gw).review(_digest(GOOD), "seo-ish string")
    assert gw.calls == []   # bodies 없음 → 룰만
