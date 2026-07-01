# -*- coding: utf-8 -*-
"""
core.orchestrator 엔진 테스트 — 수집(소스 격리)·브리핑·응답 메커니즘(게이트웨이 주입).
"""
from __future__ import annotations

from core.orchestrator import answer, build_briefing, collect_sources, run_brief


class _FakeGateway:
    def __init__(self, *, text="brief", raises=None, available=True):
        self._text = text
        self._raises = raises
        self.available = available
        self.calls: list = []

    def complete(self, system, prompt, *, model, temperature=0.4, json_mode=True):
        self.calls.append({"system": system, "prompt": prompt, "json_mode": json_mode})
        if self._raises is not None:
            raise self._raises
        return self._text


# ── 수집 ──

def test_collect_sources_isolates_failures():
    def ok():
        return {"x": 1}

    def boom():
        raise RuntimeError("db down")

    out = collect_sources({"a": ok, "b": boom})
    assert out["a"] == {"x": 1}
    assert out["b"]["available"] is False and "db down" in out["b"]["error"]


# ── 브리핑 ──

def test_build_briefing_wraps_payload_as_json():
    gw = _FakeGateway(text="  ## 오늘  ")
    out = build_briefing({"today": "2026-07-01", "youtube": {"v": 1}}, system="SYS", gateway=gw)
    assert out == "## 오늘"                                   # strip
    assert gw.calls[0]["json_mode"] is False
    assert '"today": "2026-07-01"' in gw.calls[0]["prompt"]   # payload가 JSON으로 포함


def test_build_briefing_unavailable_and_error():
    assert "미가용" in build_briefing({}, system="S", gateway=_FakeGateway(available=False))
    assert build_briefing({}, system="S", gateway=_FakeGateway(raises=RuntimeError("x"))
                          ).startswith("❌ 응답 생성 실패")


# ── 응답 ──

def test_answer_prompt_has_question_and_context():
    gw = _FakeGateway(text="resp")
    out = answer("무엇?", {"youtube": {"v": 1}}, system="SYS", gateway=gw)
    assert out == "resp"
    p = gw.calls[0]["prompt"]
    assert "# QUESTION\n무엇?" in p and "# CONTEXT" in p and '"v": 1' in p


# ── run_brief ──

def test_run_brief_returns_sources_and_summary():
    gw = _FakeGateway(text="SUMMARY")
    res = run_brief({"youtube": lambda: {"available": True, "v": 9}},
                    system="SYS", gateway=gw, today="2026-07-01", now="2026-07-01 09:00")
    assert res["youtube"] == {"available": True, "v": 9}
    assert res["summary"] == "SUMMARY"
    # payload에 today/now + 소스가 실려 게이트웨이로 감
    assert "2026-07-01" in gw.calls[0]["prompt"] and '"v": 9' in gw.calls[0]["prompt"]
