# -*- coding: utf-8 -*-
"""
adapters.youtube.orchestrator 테스트 — youtube 수집(build_report 주입) + AXIS 부재.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from adapters.youtube.orchestrator import (
    _ANSWER_SYSTEM,
    _BRIEF_SYSTEM,
    answer,
    collect_yt,
    run_brief,
)

_NOW = datetime(2026, 7, 1, 12, 0, 0)


def _fake_report(channel_id, recent_n=3, trend_days=7):
    return {
        "channel": {"title": "MyCh"},
        "weekly": {"delta": 5},
        "daily": [{"views": 100, "subscribersGained": 3,
                   "subscribersLost": 1, "estimatedMinutesWatched": 50}],
    }


class _FakeGateway:
    available = True

    def __init__(self):
        self.systems: list = []

    def complete(self, system, prompt, *, model, temperature=0.4, json_mode=True):
        self.systems.append(system)
        return "resp"


def test_collect_yt_gathers_recent_stamps_and_report(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    recent = out_dir / "stamp_recent"
    recent.mkdir()
    old = out_dir / "stamp_old"
    old.mkdir()
    os.utime(recent, ((_NOW - timedelta(hours=1)).timestamp(),) * 2)
    os.utime(old, ((_NOW - timedelta(hours=30)).timestamp(),) * 2)

    out = collect_yt(output_dir=out_dir, build_report=_fake_report,
                     channel_id="C1", now=_NOW)
    assert out["available"] is True
    stamps = [r["stamp"] for r in out["recent_runs_24h"]]
    assert "stamp_recent" in stamps and "stamp_old" not in stamps   # 24h 컷오프
    assert out["channel"] == {"title": "MyCh"}
    assert out["yesterday"]["views"] == 100 and out["yesterday"]["subs_gained"] == 3


def test_collect_yt_missing_channel_id(monkeypatch, tmp_path):
    monkeypatch.delenv("YOUTUBE_CHANNEL_ID", raising=False)
    out = collect_yt(output_dir=tmp_path / "none", build_report=_fake_report, now=_NOW)
    assert out["available"] is False
    assert out["error"] == "YOUTUBE_CHANNEL_ID 미설정"


def test_collect_yt_report_error_captured(tmp_path):
    def boom(channel_id, recent_n=3, trend_days=7):
        raise RuntimeError("api down")

    out = collect_yt(output_dir=tmp_path, build_report=boom, channel_id="C1", now=_NOW)
    assert out["available"] is False
    assert "api down" in out["error"]


def test_run_brief_wires_youtube_collector(tmp_path):
    gw = _FakeGateway()
    res = run_brief(output_dir=tmp_path, build_report=_fake_report,
                    channel_id="C1", gateway=gw, now=_NOW)
    assert "youtube" in res and res["summary"] == "resp"
    assert res["youtube"]["available"] is True


def test_answer_uses_answer_system():
    gw = _FakeGateway()
    assert answer("무엇?", {"youtube": {}}, gateway=gw) == "resp"
    assert "운영 데이터에 없음" in gw.systems[0]


def test_no_axis_in_prompts():
    for s in (_BRIEF_SYSTEM, _ANSWER_SYSTEM):
        assert "AXIS" not in s
        assert "master_dashboard" not in s
