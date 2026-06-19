# -*- coding: utf-8 -*-
"""
통합 테스트 — 엔진↔어댑터가 `PlatformAdapter` 포트로 실제 연결되는지 증명.

핵심: 엔진 `gate()`는 어댑터 구체 타입을 모른다. YouTubeAdapter로도, youtube가 아닌
가짜 어댑터로도 동일하게 동작 → 포트/어댑터가 진짜 분리됐음을 입증.
"""
from __future__ import annotations

import pytest

from adapters.youtube.adapter import YouTubeAdapter
from core.pipeline import GateDecision, PlatformAdapter, gate
from core.trust.verify import Verdict, Violation

CLEAN_SCRIPT = {
    "title": "The Murim Genius Returns",
    "body": "A young martial artist levels up through the tower.",
}
CLEAN_SEO = {"title": "K-Webtoon Short", "description": "🤖 generative AI assets used."}


def test_youtube_adapter_satisfies_platform_adapter_protocol():
    assert isinstance(YouTubeAdapter(), PlatformAdapter)


def test_review_delegates_and_returns_verdict():
    v = YouTubeAdapter(review_deep=False).review(CLEAN_SCRIPT, CLEAN_SEO)
    assert isinstance(v, Verdict)
    assert v.recommendation == "approve"


def test_produce_and_publish_not_implemented_yet():
    a = YouTubeAdapter()
    with pytest.raises(NotImplementedError):
        a.produce({})
    with pytest.raises(NotImplementedError):
        a.publish({})


# ── 엔진 발행 게이트가 포트를 통해 어댑터를 다룬다 ──

def test_gate_approves_clean_content():
    d = gate(YouTubeAdapter(review_deep=False), CLEAN_SCRIPT, CLEAN_SEO)
    assert isinstance(d, GateDecision)
    assert d.proceed is True
    assert d.verdict.recommendation == "approve"


def test_gate_holds_when_ai_label_missing():
    seo = {"title": "t", "description": "no disclosure"}
    d = gate(YouTubeAdapter(review_deep=False), CLEAN_SCRIPT, seo)
    assert d.proceed is False               # review → HITL 보류
    assert d.verdict.recommendation == "review"


def test_gate_holds_on_hard_block():
    script = {**CLEAN_SCRIPT, "body": "this short contains nsfw content"}
    d = gate(YouTubeAdapter(review_deep=False), script, CLEAN_SEO)
    assert d.proceed is False
    assert d.verdict.has_high_severity is True


# ── 포트 분리 증명: 엔진 gate는 youtube가 아닌 어댑터에도 동작 ──

class _FakeAdapter:
    """youtube와 무관한 임의 어댑터 — PlatformAdapter 계약만 만족."""

    def __init__(self, verdict: Verdict) -> None:
        self._verdict = verdict

    def produce(self, brief):  # noqa: D401
        return brief

    def review(self, content, seo) -> Verdict:
        return self._verdict

    def publish(self, asset):
        return asset


def test_gate_is_adapter_agnostic():
    approve = Verdict(passed=True, score=0.0, violations=[], recommendation="approve")
    reject = Verdict(passed=False, score=1.0,
                     violations=[Violation("x", "high")], recommendation="reject")

    assert isinstance(_FakeAdapter(approve), PlatformAdapter)
    assert gate(_FakeAdapter(approve), {}, {}).proceed is True   # 엔진은 구체 타입을 모름
    assert gate(_FakeAdapter(reject), {}, {}).proceed is False
