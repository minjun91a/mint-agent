# -*- coding: utf-8 -*-
"""
adapters.youtube.review 특성화(golden-master) 테스트.

목적: youtube-agent/agents/compliance.py에서 이식한 **룰 검사층의 동작을 고정**해,
이후 리팩터가 동작을 바꾸지 않음을 보증한다(브리프 8장: 테스트로 동일성 보증).
결정적 경로(deep=False / 룰)만 검증한다 — LLM(deep=True+키) 경로는 비결정이므로 제외.
"""
from __future__ import annotations

import pytest

from adapters.youtube.review import (
    ComplianceReviewer,
    _quick_rule_check,
    check,
    format_violations_md,
    review,
)
from core.trust.verify import Reviewer, Verdict, Violation

# AI 라벨 마커가 포함된 깨끗한 입력
CLEAN_SCRIPT = {
    "title": "The Murim Genius Returns",
    "hook": "A fallen master awakens with a system.",
    "body": "A young martial artist levels up through the tower and reclaims his clan.",
    "cliffhanger": "But the final gate was never meant to open.",
}
CLEAN_SEO = {
    "title": "The Murim Genius Returns | K-Webtoon Short",
    "description": "An epic K-webtoon short. 🤖 generative AI assets used.",
}


def test_clean_input_passes_rule_only():
    v = review(CLEAN_SCRIPT, CLEAN_SEO, deep=False)
    assert isinstance(v, Verdict)
    assert v.violations == []
    assert v.passed is True
    assert v.score == 0.0
    assert v.recommendation == "approve"


def test_missing_ai_label_flags_medium():
    seo = {"title": "t", "description": "no disclosure here at all"}
    v = review(CLEAN_SCRIPT, seo, deep=False)
    cats = [x.category for x in v.violations]
    assert cats == ["ai_labeling"]
    assert v.violations[0].severity == "med"
    assert v.passed is True              # med-only → passed
    assert v.score == pytest.approx(0.15)
    assert v.recommendation == "review"  # 위반 존재 → review


def test_hard_block_high_fails_but_recommends_review():
    # 룰 전용 경로에서 high 위반은 passed=False, 단 recommendation은 'review'(원본 동작 그대로)
    script = {**CLEAN_SCRIPT, "body": "this short contains nsfw content"}
    v = review(script, CLEAN_SEO, deep=False)
    sevs = [x.severity for x in v.violations]
    cats = [x.category for x in v.violations]
    assert "hard_block" in cats
    assert "high" in sevs
    assert v.passed is False
    assert v.recommendation == "review"
    assert v.has_high_severity is True


def test_professional_advice_flags_medium():
    script = {**CLEAN_SCRIPT, "body": "in this guide we diagnose your rare disease"}
    v = review(script, CLEAN_SEO, deep=False)
    cats = [x.category for x in v.violations]
    assert "professional_advice" in cats
    assert v.passed is True  # med only


def test_score_scales_with_violation_count():
    # ai_labeling(med) + professional_advice(med) = 2건 → 0.30
    script = {**CLEAN_SCRIPT, "body": "we will diagnose you today"}
    seo = {"title": "t", "description": "no disclosure"}
    v = review(script, seo, deep=False)
    assert len(v.violations) == 2
    assert v.score == pytest.approx(0.30)


def test_check_returns_legacy_dict_shape():
    d = check(CLEAN_SCRIPT, CLEAN_SEO, deep=False)
    assert set(d.keys()) == {"passed", "score", "violations", "recommendation"}
    assert isinstance(d["violations"], list)
    assert d == {"passed": True, "score": 0.0, "violations": [], "recommendation": "approve"}


def test_quick_rule_check_returns_violations():
    seo = {"title": "t", "description": "plain"}
    out = _quick_rule_check(CLEAN_SCRIPT, seo)
    assert all(isinstance(x, Violation) for x in out)
    assert [x.category for x in out] == ["ai_labeling"]


def test_deep_without_api_key_falls_back_to_rules(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    v = review(CLEAN_SCRIPT, CLEAN_SEO, deep=True)  # 키 없음 → 룰 결과
    assert v.passed is True
    assert v.recommendation == "approve"
    assert v.violations == []


def test_reviewer_implements_protocol_and_returns_verdict():
    r = ComplianceReviewer(deep=False)
    assert isinstance(r, Reviewer)
    v = r.review(CLEAN_SCRIPT, CLEAN_SEO)
    assert isinstance(v, Verdict)
    assert v.recommendation == "approve"


def test_format_violations_md():
    assert format_violations_md(review(CLEAN_SCRIPT, CLEAN_SEO, deep=False)) == "_위반 사항 없음_"
    seo = {"title": "t", "description": "no disclosure"}
    md = format_violations_md(review(CLEAN_SCRIPT, seo, deep=False))
    assert "🟠" in md and "ai_labeling" in md
