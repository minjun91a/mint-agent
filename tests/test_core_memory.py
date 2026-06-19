# -*- coding: utf-8 -*-
"""
core.memory.ChannelMemory 특성화(golden-master) 테스트.

youtube-agent/agents/memory.py에서 엔진으로 이식한 채널 메모리의 결정적 동작을 고정한다.
temp DB를 주입해 격리 실행(ts 같은 비결정 값은 단언하지 않음).
"""
from __future__ import annotations

import pytest

from core.memory import ChannelMemory


@pytest.fixture
def mem(tmp_path):
    return ChannelMemory(tmp_path / "mem.db")


def test_append_and_recent_chronological(mem):
    mem.append("c1", "user", "a")
    mem.append("c1", "assistant", "b")
    mem.append("c1", "user", "c")
    assert mem.recent("c1", 20) == [("user", "a"), ("assistant", "b"), ("user", "c")]


def test_recent_limits_to_last_n(mem):
    for i in range(5):
        mem.append("c1", "user", f"m{i}")
    assert mem.recent("c1", 2) == [("user", "m3"), ("user", "m4")]


def test_recent_n_clamped_to_1_and_200(mem):
    mem.append("c1", "user", "x")
    assert mem.recent("c1", 0) == [("user", "x")]      # n<1 → 1
    assert mem.recent("c1", 9999) == [("user", "x")]   # n>200 → 200


def test_invalid_role_raises(mem):
    with pytest.raises(ValueError):
        mem.append("c1", "system", "nope")


def test_empty_content_not_stored(mem):
    mem.append("c1", "user", "")
    assert mem.recent("c1") == []
    assert mem.summary("c1")["turns"] == 0


def test_content_truncated_to_8000(mem):
    mem.append("c1", "user", "z" * 9000)
    (role, content), = mem.recent("c1")
    assert len(content) == 8000


def test_reset_deletes_and_returns_count(mem):
    mem.append("c1", "user", "a")
    mem.append("c1", "user", "b")
    assert mem.reset("c1") == 2
    assert mem.recent("c1") == []


def test_channel_isolation(mem):
    mem.append("c1", "user", "one")
    mem.append("c2", "user", "two")
    assert mem.recent("c1") == [("user", "one")]
    assert mem.recent("c2") == [("user", "two")]


def test_summary_counts(mem):
    mem.append("c1", "user", "a")
    mem.append("c1", "assistant", "b")
    s = mem.summary("c1")
    assert s["channel_id"] == "c1"
    assert s["turns"] == 2
    assert s["oldest"] is not None and s["newest"] is not None


def test_summary_empty_channel(mem):
    assert mem.summary("none") == {"channel_id": "none", "turns": 0, "oldest": None, "newest": None}


def test_recent_across_collects_all_with_fields(mem):
    mem.append("c1", "user", "a")
    mem.append("c2", "assistant", "b")
    rows = mem.recent_across(["c1", "c2"], n_per=5)
    assert sorted(r["content"] for r in rows) == ["a", "b"]
    assert all(set(r.keys()) == {"channel_id", "role", "content", "ts"} for r in rows)


def test_recent_across_empty_ids(mem):
    assert mem.recent_across([], n_per=5) == []


def test_channels_with_history(mem):
    mem.append("c1", "user", "a")
    mem.append("c2", "user", "b")
    ids = {c["channel_id"] for c in mem.channels_with_history()}
    assert ids == {"c1", "c2"}


def test_channel_id_coerced_to_str(mem):
    mem.append(123, "user", "numeric")
    assert mem.recent(123) == [("user", "numeric")]
    assert mem.recent("123") == [("user", "numeric")]  # 같은 채널(문자열화)
