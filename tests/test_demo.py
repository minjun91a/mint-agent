# -*- coding: utf-8 -*-
"""
demo.py 스모크 테스트.

데모는 README가 "클론하고 바로 돌려보세요"라고 약속하는 유일한 실행 진입점이다. 그 약속이
조용히 깨지지 않도록 CI에서 매번 돌린다 — **API 키·네트워크 없이** 도는지도 함께 고정한다.
"""
from __future__ import annotations

import pytest

import demo


def test_every_demo_runs_without_keys_or_network(capsys):
    """모든 데모가 예외 없이 돈다(키·네트워크 없음)."""
    for name, fn in demo.DEMOS.items():
        fn()
    out = capsys.readouterr().out
    assert out.strip()


def test_main_runs_all_demos_by_default(capsys):
    assert demo.main([]) == 0
    out = capsys.readouterr().out
    for heading in ("발행 게이트", "환각 인용 검증", "복잡도 비용 라우팅",
                    "컨센서스 게이트", "감사 루프"):
        assert heading in out


def test_main_runs_a_single_demo(capsys):
    assert demo.main(["gate"]) == 0
    out = capsys.readouterr().out
    assert "발행 게이트" in out
    assert "환각 인용 검증" not in out


def test_main_rejects_unknown_demo():
    with pytest.raises(SystemExit):
        demo.main(["존재하지-않는-데모"])


# ── 데모가 주장하는 결과가 실제 결과인가 ────────────────────────────


def test_gate_demo_shows_hitl_hold_for_missing_ai_label(capsys):
    demo.demo_gate()
    out = capsys.readouterr().out
    assert "AI 라벨 없음" in out and "review" in out
    assert "ai_labeling" in out


def test_citation_demo_rejects_all_three_hallucination_kinds(capsys):
    demo.demo_citation()
    out = capsys.readouterr().out
    for category in ("citation_out_of_range", "citation_unfetched_source", "no_citation"):
        assert category in out


def test_routing_demo_never_crosses_providers(capsys):
    """데모가 화면에 Claude 모델을 Gemini 라우팅 결과로 찍으면 안 된다."""
    demo.demo_routing()
    out = capsys.readouterr().out
    for line in out.splitlines():
        if "Gemini→" in line:
            gemini_part = line.split("Gemini→")[1].split("Claude→")[0]
            assert "claude" not in gemini_part


def test_consensus_demo_never_publishes_without_reviewers(capsys):
    demo.demo_consensus()
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if "리뷰어 없음" in l)
    assert "발행" not in line          # 아무도 검증 안 한 것은 통과시키지 않는다


def test_audit_demo_closes_the_loop(capsys):
    """게이트 결정 → JSONL → eval → 리포트가 실제로 렌더된다."""
    demo.demo_audit()
    out = capsys.readouterr().out
    assert "발행 게이트 감사 리포트" in out
    assert "모순 감사" in out
