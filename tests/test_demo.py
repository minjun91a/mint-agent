# -*- coding: utf-8 -*-
"""
demo.py 스모크 테스트.

데모는 README가 "클론하고 바로 돌려보세요"라고 약속하는 유일한 실행 진입점이다. 그 약속이
조용히 깨지지 않도록 CI에서 매번 돌린다 — **API 키·네트워크 없이** 도는지도 함께 고정한다.
"""
from __future__ import annotations

import pytest

import demo
from core.pipeline import gate
from core.trust.verify import Verdict, Violation


def test_every_demo_runs_without_keys_or_network(capsys):
    """모든 데모가 예외 없이 돈다(키·네트워크 없음)."""
    for name, fn in demo.DEMOS.items():
        fn()
    out = capsys.readouterr().out
    assert out.strip()


def test_main_runs_all_demos_by_default(capsys):
    """인자 없이 실행하면 전체가 돈다.

    회귀 방지: `nargs="*"` + `choices=`를 함께 쓰면 Python 3.12/3.13의 argparse가 기본값 `[]`를
    choices에 대해 검증해 `SystemExit(2)`로 죽는다(3.14에서 수정됨). 로컬이 3.14라 통과했고
    CI(3.12·3.13)에서만 터졌다 — 그래서 이제 CI 매트릭스에 3.14도 넣었다.
    """
    assert demo.main([]) == 0
    out = capsys.readouterr().out
    for heading in ("발행 게이트", "환각 인용 검증", "복잡도 비용 라우팅",
                    "컨센서스 게이트", "감사 루프", "제조 도메인"):
        assert heading in out


def test_main_runs_a_single_demo(capsys):
    assert demo.main(["gate"]) == 0
    out = capsys.readouterr().out
    assert "발행 게이트" in out
    assert "환각 인용 검증" not in out


def test_main_runs_several_named_demos(capsys):
    assert demo.main(["gate", "routing"]) == 0
    out = capsys.readouterr().out
    assert "발행 게이트" in out and "복잡도 비용 라우팅" in out
    assert "감사 루프" not in out


def test_main_rejects_unknown_demo():
    with pytest.raises(SystemExit):
        demo.main(["존재하지-않는-데모"])


def test_main_rejects_mix_of_known_and_unknown(capsys):
    """알 수 없는 이름이 섞여 있으면 아무것도 실행하지 않고 죽는다."""
    with pytest.raises(SystemExit):
        demo.main(["gate", "없는데모"])


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


def test_audit_demo_shows_which_rules_fired(capsys):
    """원본 로그로는 답할 수 없던 질문 — '어떤 규칙이 터졌나'."""
    demo.demo_audit()
    out = capsys.readouterr().out
    assert "### 터진 규칙" in out
    for category in ("ai_labeling", "citation_out_of_range", "missing_sources_section"):
        assert category in out


def test_audit_demo_promotes_only_the_rule_that_stands_alone(capsys):
    """`citation_out_of_range`만 단독으로 reject를 냈다. 나머지는 업혀 온 규칙이다."""
    demo.demo_audit()
    out = capsys.readouterr().out
    promoted = [l for l in out.splitlines() if "사전 차단 후보" in l]
    assert len(promoted) == 1 and "citation_out_of_range" in promoted[0]
    assert "동시발생 ≠ 인과" in out


def test_audit_demo_reports_no_float_noise(capsys):
    demo.demo_audit()
    assert "0.44999" not in capsys.readouterr().out


def test_manufacturing_demo_finds_only_m01_short(capsys):
    demo.demo_manufacturing()
    out = capsys.readouterr().out
    assert "M-01" in out and "440개" in out


def test_manufacturing_demo_distinguishes_hallucination_from_info_leak(capsys):
    """환각 코드는 reject, 정보 노출은 review — 같은 '위반'이어도 정책이 다르다."""
    demo.demo_manufacturing()
    out = capsys.readouterr().out
    lines = out.splitlines()
    hallucinated = next(l for l in lines if "환각 코드" in l)
    leaked = next(l for l in lines if "내부정보 노출" in l)
    assert "reject" in hallucinated
    assert "review" in leaked


def test_manufacturing_demo_never_calls_blocking_input(capsys, monkeypatch):
    """기본 실행 경로는 사람 입력을 기다리면 안 된다(CI가 인자 없이 모든 데모를 돈다)."""
    def _boom(*args, **kwargs):
        raise AssertionError("demo_manufacturing이 실제 input()을 불렀다")

    monkeypatch.setattr("builtins.input", _boom)
    demo.demo_manufacturing()   # 예외 없이 끝나야 한다


def test_manufacturing_demo_simulates_both_approval_outcomes(capsys):
    demo.demo_manufacturing()
    out = capsys.readouterr().out
    assert "publish=True (source=human_approve)" in out
    assert "publish=False (source=human_reject)" in out


# ── console_ask: 실 콘솔 승인 구현(기본 경로에서는 안 불림) ──────────────


class _ReviewStubAdapter:
    """`test_core_approval.py`와 같은 최소 어댑터 — 고정된 Verdict을 낸다."""

    def __init__(self, verdict: Verdict) -> None:
        self._verdict = verdict

    def produce(self, brief): ...        # pragma: no cover
    def publish(self, asset): ...        # pragma: no cover

    def review(self, content, context=None) -> Verdict:
        return self._verdict


def _review_decision(**kwargs):
    return gate(_ReviewStubAdapter(Verdict(passed=True, score=0.15, recommendation="review",
                                           **kwargs)), {})


def test_console_ask_approves_on_y(capsys):
    decision = _review_decision(violations=[Violation(category="c", severity="med", detail="d")])
    approved = demo.console_ask(decision, input_fn=lambda prompt: "y")
    out = capsys.readouterr().out

    assert approved is True
    assert "사람 판단 필요" in out
    assert "[med] c: d" in out


def test_console_ask_rejects_on_anything_else():
    decision = _review_decision()
    assert demo.console_ask(decision, input_fn=lambda prompt: "") is False
    assert demo.console_ask(decision, input_fn=lambda prompt: "n") is False
