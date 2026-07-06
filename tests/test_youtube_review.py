# -*- coding: utf-8 -*-
"""
adapters.youtube.review 특성화(golden-master) 테스트.

목적: youtube-agent/agents/compliance.py에서 이식한 **룰 검사층의 동작을 고정**해,
이후 리팩터가 동작을 바꾸지 않음을 보증한다(브리프 8장: 테스트로 동일성 보증).
룰 경로(deep=False)는 결정적이라 그대로 고정한다. LLM 심층 경로는 과거엔 비결정이라 제외했으나,
모델 게이트웨이 추출 이후 **가짜 게이트웨이 주입**으로 결정적으로 검증한다(룰+LLM 병합·오류 안전화).
"""
from __future__ import annotations

import json

import pytest

from adapters.youtube.review import (
    ComplianceReviewer,
    _quick_rule_check,
    check,
    format_violations_md,
    review,
)
from core.trust.verify import Reviewer, Verdict, Violation


class _FakeGateway:
    """주입용 가짜 LLM 게이트웨이 — canned 텍스트 / 예외 / 미가용을 흉내낸다."""

    def __init__(self, *, text: str | None = None, raises: Exception | None = None,
                 available: bool = True) -> None:
        self._text = text
        self._raises = raises
        self.available = available

    def complete(self, system: str, prompt: str, *, model: str,
                 temperature: float = 0.4, json_mode: bool = True) -> str:
        if self._raises is not None:
            raise self._raises
        return self._text or "{}"

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


# ── LLM 심층 경로: 가짜 게이트웨이 주입 (게이트웨이 추출로 새로 결정화) ──

def test_deep_merges_gateway_violations_after_rules():
    gw = _FakeGateway(text=json.dumps({
        "passed": True, "score": 0.1,
        "violations": [{"category": "tos", "severity": "low", "detail": "minor"}],
        "recommendation": "approve",
    }))
    v = review(CLEAN_SCRIPT, CLEAN_SEO, deep=True, gateway=gw)
    assert [x.category for x in v.violations] == ["tos"]   # 룰 위반 0 + LLM 1
    assert v.recommendation == "approve"                   # LLM 권고 채택
    assert v.score == pytest.approx(0.1)                   # LLM score 채택


def test_deep_high_severity_fails_even_if_llm_says_passed():
    seo = {"title": "t", "description": "no disclosure"}    # 룰: ai_labeling(med)
    gw = _FakeGateway(text=json.dumps({
        "passed": True, "score": 0.5,
        "violations": [{"category": "deepfake", "severity": "high", "detail": "x"}],
        "recommendation": "reject",
    }))
    v = review(CLEAN_SCRIPT, seo, deep=True, gateway=gw)
    assert [x.category for x in v.violations] == ["ai_labeling", "deepfake"]  # 룰 먼저, LLM 뒤
    assert v.passed is False              # high 포함 → LLM passed=True여도 False로 게이트
    assert v.has_high_severity is True
    assert v.recommendation == "reject"


def test_deep_gateway_error_forces_review():
    gw = _FakeGateway(raises=RuntimeError("api down"))
    v = review(CLEAN_SCRIPT, CLEAN_SEO, deep=True, gateway=gw)
    assert v.recommendation == "review"   # LLM 오류 → 안전하게 사람 판단으로
    assert v.violations == []             # 룰 위반 0 그대로
    assert v.passed is True


# ── eval이 검출한 결함 교정 (Verdict 계약 준수) — 원본과 의도적 차이 ──

def test_deep_clamps_out_of_range_llm_score():
    # eval이 실 로그에서 잡은 결함: LLM이 미정규화 점수(예: 40)를 반환 → 계약[0,1]로 클램프.
    gw = _FakeGateway(text=json.dumps({"passed": False, "score": 40.0,
                                       "violations": [], "recommendation": "reject"}))
    assert review(CLEAN_SCRIPT, CLEAN_SEO, deep=True, gateway=gw).score == 1.0


def test_deep_clamps_negative_score():
    gw = _FakeGateway(text=json.dumps({"passed": True, "score": -3,
                                       "violations": [], "recommendation": "approve"}))
    assert review(CLEAN_SCRIPT, CLEAN_SEO, deep=True, gateway=gw).score == 0.0


def test_deep_invalid_recommendation_defaults_to_review():
    gw = _FakeGateway(text=json.dumps({"passed": True, "score": 0.2,
                                       "violations": [], "recommendation": "block"}))
    assert review(CLEAN_SCRIPT, CLEAN_SEO, deep=True, gateway=gw).recommendation == "review"


def test_fixed_verdict_passes_eval_invariants():
    # 루프 닫기(find→fix→verify): eval이 잡던 결함(score=40)을 클램프한 뒤, 그 결정은
    # eval 불변식을 통과한다. eval이 발견 → 이식본 교정 → eval이 재확인.
    from core.trust.eval import AuditRecord, check_invariants

    gw = _FakeGateway(text=json.dumps({
        "passed": False, "score": 40.0,
        "violations": [{"category": "x", "severity": "high", "detail": "y"}],
        "recommendation": "reject",
    }))
    v = review(CLEAN_SCRIPT, CLEAN_SEO, deep=True, gateway=gw)
    rec = AuditRecord(passed=v.passed, score=v.score,
                      recommendation=v.recommendation, violation_count=len(v.violations))
    assert check_invariants([rec]) == []


def test_deep_unavailable_gateway_keeps_rule_recommendation():
    # 미가용(키 없음)은 오류와 다르다 — complete를 부르지 않고 룰의 자연 권고를 그대로 둔다.
    gw = _FakeGateway(available=False, raises=AssertionError("complete 호출 금지"))
    v = review(CLEAN_SCRIPT, CLEAN_SEO, deep=True, gateway=gw)
    assert v.recommendation == "approve"  # 룰 결과의 자연 권고 (강제 'review' 아님)
    assert v.violations == []
