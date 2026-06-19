# -*- coding: utf-8 -*-
"""
core.memory — 채널 단위 대화 메모리 (엔진, 플랫폼 무관).

`youtube-agent/agents/memory.py`를 모노레포 엔진으로 이식. 원본은 DB 경로가 모듈 전역으로
하드코딩(`_DB_PATH`)돼 있어 youtube-agent 위치에 결합돼 있었다. 엔진은 위치를 몰라야 하므로
**경로를 주입받는 `ChannelMemory` 클래스**로 바꿨다.

SQL·검증·슬라이싱 로직은 원본과 동일하며(`tests/test_core_memory.py`가 고정),
원본 모듈 함수들과 parity로 동일성을 증명한다.

테이블: channel_turns(id, channel_id, role, content, ts)
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger("mint.core.memory")


class ChannelMemory:
    """채널별 대화 히스토리를 SQLite에 영속 저장. 슬라이딩 N턴 윈도우로 컨텍스트 빌드.

    엔진 사용처가 DB 경로를 주입한다(어댑터·테스트가 위치를 정함).
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.Lock()

    def _con(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.db_path), check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("""
            CREATE TABLE IF NOT EXISTS channel_turns (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT    NOT NULL,
                role       TEXT    NOT NULL CHECK (role IN ('user', 'assistant')),
                content    TEXT    NOT NULL,
                ts         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_channel_turns_channel
            ON channel_turns (channel_id, id)
        """)
        return con

    def append(self, channel_id: str | int, role: str, content: str) -> None:
        if role not in ("user", "assistant"):
            raise ValueError(f"role must be user|assistant, got {role}")
        if not content:
            return
        with self._lock:
            con = self._con()
            try:
                con.execute(
                    "INSERT INTO channel_turns(channel_id, role, content, ts) VALUES (?,?,?,?)",
                    (str(channel_id), role, content[:8000],
                     datetime.now().isoformat(timespec="seconds")),
                )
                con.commit()
            finally:
                con.close()

    def recent(self, channel_id: str | int, n: int = 20) -> list[tuple[str, str]]:
        """최근 N턴(user/assistant 모두 포함)을 시간 오름차순으로 반환."""
        n = max(1, min(int(n), 200))
        with self._lock:
            con = self._con()
            try:
                rows = con.execute("""
                    SELECT role, content FROM channel_turns
                     WHERE channel_id = ?
                  ORDER BY id DESC
                     LIMIT ?
                """, (str(channel_id), n)).fetchall()
            finally:
                con.close()
        return [(r["role"], r["content"]) for r in reversed(rows)]

    def reset(self, channel_id: str | int) -> int:
        with self._lock:
            con = self._con()
            try:
                cur = con.execute(
                    "DELETE FROM channel_turns WHERE channel_id = ?",
                    (str(channel_id),),
                )
                con.commit()
                return cur.rowcount
            finally:
                con.close()

    def recent_across(self, channel_ids: list[str | int], n_per: int = 5) -> list[dict]:
        """여러 채널의 최근 n_per턴을 시간 오름차순으로 통합 반환.

        각 row: {"channel_id", "role", "content", "ts"}
        """
        if not channel_ids:
            return []
        n_per = max(1, min(int(n_per), 50))
        out: list[dict] = []
        with self._lock:
            con = self._con()
            try:
                for cid in channel_ids:
                    rows = con.execute("""
                        SELECT channel_id, role, content, ts
                          FROM channel_turns
                         WHERE channel_id = ?
                      ORDER BY id DESC LIMIT ?
                    """, (str(cid), n_per)).fetchall()
                    out.extend(dict(r) for r in reversed(rows))
            finally:
                con.close()
        out.sort(key=lambda r: r["ts"])
        return out

    def channels_with_history(self, limit: int = 30) -> list[dict]:
        """메모리가 쌓인 채널 ID + 턴수 목록."""
        with self._lock:
            con = self._con()
            try:
                rows = con.execute("""
                    SELECT channel_id, COUNT(*) AS n,
                           MIN(ts) AS oldest, MAX(ts) AS newest
                      FROM channel_turns
                     GROUP BY channel_id
                     ORDER BY MAX(ts) DESC
                     LIMIT ?
                """, (limit,)).fetchall()
            finally:
                con.close()
        return [dict(r) for r in rows]

    def summary(self, channel_id: str | int) -> dict:
        with self._lock:
            con = self._con()
            try:
                row = con.execute("""
                    SELECT COUNT(*) AS n,
                           MIN(ts) AS oldest,
                           MAX(ts) AS newest
                    FROM channel_turns WHERE channel_id = ?
                """, (str(channel_id),)).fetchone()
            finally:
                con.close()
        return {
            "channel_id": str(channel_id),
            "turns": int(row["n"] or 0),
            "oldest": row["oldest"],
            "newest": row["newest"],
        }
