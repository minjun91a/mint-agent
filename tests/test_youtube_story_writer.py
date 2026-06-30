# -*- coding: utf-8 -*-
"""
adapters.youtube.story_writer 테스트.

원본은 OpenAI 호출·파일 I/O·random·시간 dedup이 한 함수에 엉켜 결정적 테스트가 불가능했다.
경계를 갈라 검증한다: 순수(format) · 상태 I/O(`TropeLibrary`, 경로·clock·rng 주입) · LLM(게이트웨이 주입).
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

import pytest
import yaml

from adapters.youtube.story_writer import (
    TropeLibrary,
    _build_user_msg,
    format_tropes,
    generate,
    write_script,
)

TROPES = [
    {"id": "T01", "category": "regression", "hook": "h1", "setup": "s1", "twist": "w1"},
    {"id": "T02", "category": "reincarnation", "hook": "h2", "setup": "s2", "twist": "w2"},
    {"id": "T03", "category": "murim", "hook": "h3", "setup": "s3", "twist": "w3"},
]

_NOW = datetime(2026, 6, 30, 12, 0, 0)


class _FakeGateway:
    """주입용 가짜 LLM 게이트웨이 — canned 텍스트 / 예외 / 미가용."""

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


def _make_library(tmp_path, *, used=None, now=None) -> TropeLibrary:
    tropes_path = tmp_path / "tropes.yaml"
    tropes_path.write_text(yaml.safe_dump({"tropes": TROPES}, allow_unicode=True),
                           encoding="utf-8")
    used_log = tmp_path / "used.json"
    if used is not None:
        used_log.write_text(json.dumps(used), encoding="utf-8")
    return TropeLibrary(tropes_path=tropes_path, used_log_path=used_log,
                        now=(now or (lambda: _NOW)))


# ── 순수 로직 ──

def test_format_tropes_exact_line():
    assert format_tropes([TROPES[0]]) == "- [T01/regression] hook='h1' setup='s1' twist='w1'"


def test_format_tropes_joins_with_newline():
    out = format_tropes(TROPES[:2])
    assert out.count("\n") == 1
    assert out.splitlines()[1].startswith("- [T02/reincarnation]")


def test_build_user_msg_prefix_and_body():
    msg = _build_user_msg(TROPES[:1])
    assert msg.startswith(
        "Combine these tropes into a 90-second English fantasy short script:\n\n")
    assert "- [T01/regression]" in msg


# ── 상태 I/O: TropeLibrary (경로·clock·rng 주입) ──

def test_library_load_reads_pool(tmp_path):
    lib = _make_library(tmp_path)
    assert [t["id"] for t in lib.load()] == ["T01", "T02", "T03"]


def test_recent_used_respects_48h_cutoff(tmp_path):
    used = [
        {"id": "T01", "at": (_NOW - timedelta(hours=10)).isoformat()},  # 48h 이내
        {"id": "T02", "at": (_NOW - timedelta(hours=60)).isoformat()},  # 48h 밖
    ]
    lib = _make_library(tmp_path, used=used)
    assert lib.recent_used(hours=48) == {"T01"}


def test_recent_used_empty_when_no_log(tmp_path):
    assert _make_library(tmp_path).recent_used() == set()


def test_record_used_appends_and_prunes_7d(tmp_path):
    old = {"id": "T09", "at": (_NOW - timedelta(days=8)).isoformat()}  # 7일 밖 → 정리
    lib = _make_library(tmp_path, used=[old])
    lib.record_used(["T01", "T02"])
    data = json.loads((tmp_path / "used.json").read_text(encoding="utf-8"))
    assert [e["id"] for e in data] == ["T01", "T02"]            # 오래된 항목 제거됨
    assert all(e["at"] == _NOW.isoformat(timespec="seconds") for e in data)


def test_pick_excludes_recent_and_is_deterministic(tmp_path):
    used = [{"id": "T01", "at": (_NOW - timedelta(hours=1)).isoformat()}]
    lib = _make_library(tmp_path, used=used)
    ids = [t["id"] for t in lib.pick(2, rng=random.Random(0))]
    assert "T01" not in ids                       # 최근 사용 제외
    assert set(ids) <= {"T02", "T03"} and len(ids) == 2
    assert [t["id"] for t in lib.pick(2, rng=random.Random(0))] == ids  # 동일 시드 동일 결과


def test_pick_reopens_full_pool_when_too_few_fresh(tmp_path):
    used = [{"id": tid, "at": (_NOW - timedelta(hours=1)).isoformat()}
            for tid in ("T01", "T02", "T03")]      # 전부 최근 사용 → fresh 0
    lib = _make_library(tmp_path, used=used)
    assert len(lib.pick(2, rng=random.Random(1))) == 2   # 풀 재오픈으로 선택 성공


# ── LLM 경계: 가짜 게이트웨이 주입 ──

def test_write_script_normalizes_and_attaches_tropes():
    payload = {"title": "T", "hook": "h", "body": "b", "cliffhanger": "c", "scenes": []}
    data = write_script(TROPES[:2], gateway=_FakeGateway(text=json.dumps(payload)))
    assert data["title"] == "T"
    assert data["_used_tropes"] == ["T01", "T02"]


def test_write_script_raises_when_gateway_unavailable():
    with pytest.raises(RuntimeError):
        write_script(TROPES[:1], gateway=_FakeGateway(available=False))


def test_generate_picks_writes_and_records(tmp_path):
    lib = _make_library(tmp_path)
    gw = _FakeGateway(text=json.dumps({"title": "Gen", "scenes": []}))
    data = generate(lib, n=2, gateway=gw, rng=random.Random(0))
    assert data["title"] == "Gen"
    assert len(data["_used_tropes"]) == 2
    recorded = json.loads((tmp_path / "used.json").read_text(encoding="utf-8"))
    assert {e["id"] for e in recorded} == set(data["_used_tropes"])  # 선택분이 기록됨
