# -*- coding: utf-8 -*-
"""
adapters.youtube.orchestrator — youtube 운영 데이터 수집 + 브리핑/응답 프롬프트 (orchestrator 어댑터 절반).

브리핑 메커니즘은 엔진(`core.orchestrator`)에 있고, 여기서는 youtube 데이터 수집기(`collect_yt`)와
브리핑/응답 시스템 프롬프트를 정의한다.

이식 시 정리(범위 제외 결정):
  - 원본의 AXIS 수집부(`collect_axis`, master_dashboard.db)는 **이식하지 않는다**.
  - 브리핑/응답 프롬프트의 AXIS 언급도 제거 → youtube 운영 데이터 기준.
  - `build_report`(미이식 `tools.youtube_daily`)는 **주입 seam**으로 받는다.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from core.orchestrator import Collector
from core.orchestrator import answer as _answer
from core.orchestrator import run_brief as _run_brief

log = logging.getLogger("mint.adapters.youtube.orchestrator")

# build_report(channel_id, recent_n=, trend_days=) -> dict
BuildReport = Callable[..., dict]

_BRIEF_SYSTEM = """당신은 유튜브 자동화 운영 비서입니다.
주어진 youtube-agent(채널·파이프라인) 데이터로
오늘 처리해야 할 업무를 한국어 우선순위 브리핑으로 정리하세요.

규칙:
- 첫 줄에 "## 오늘의 우선순위 (YYYY-MM-DD)"
- 각 항목은 **[긴급도]** 형식 (🔴 긴급 / 🟡 중요 / 🟢 평시)
- 각 항목 끝에 (근거: <필드명>=<값>) 형식으로 데이터 출처 명시
- 채널 지표 급변(구독·조회 이상)·파이프라인 실행 누락/오류는 🔴
- 파이프라인 추가 실행 권장, 채널 성장 신호는 🟢
- 데이터가 비었으면 그 항목은 생략 (가짜 항목 만들지 말 것)
- 마지막에 "## 한 줄 요약" 한 문장
"""

_ANSWER_SYSTEM = """당신은 유튜브 자동화 운영 비서입니다.
첨부된 youtube-agent 운영 데이터(JSON)를 근거로만 한국어로 답하세요.
데이터에 없는 내용은 추측하지 말고 "운영 데이터에 없음"이라고 답합니다.
답변은 5줄 이내로 간결하게.
"""


def collect_yt(*, output_dir: str | Path, build_report: BuildReport,
               channel_id: str | None = None, now: datetime | None = None) -> dict:
    """youtube 운영 데이터 수집 — 24h 내 파이프라인 실행(output 폴더 stamp) + 채널 리포트.

    원본 `collect_yt`와 동일(AXIS 무관). `build_report`·출력 폴더·시계를 주입해 결정적으로 만든다.
    """
    now = now or datetime.now()
    cutoff = now - timedelta(hours=24)
    out: dict = {
        "channel": None, "weekly_delta": None, "yesterday": None,
        "recent_runs_24h": [], "available": False, "error": None,
    }

    output_dir = Path(output_dir)
    if output_dir.exists():
        for p in sorted(output_dir.iterdir()):
            if not p.is_dir():
                continue
            try:
                ts = datetime.fromtimestamp(p.stat().st_mtime)
                if ts >= cutoff:
                    out["recent_runs_24h"].append({
                        "stamp": p.name,
                        "mtime": ts.strftime("%Y-%m-%d %H:%M"),
                    })
            except Exception:
                continue

    if not channel_id:
        channel_id = os.getenv("YOUTUBE_CHANNEL_ID", "")
    if not channel_id:
        out["error"] = "YOUTUBE_CHANNEL_ID 미설정"
        return out

    try:
        data = build_report(channel_id, recent_n=3, trend_days=7)
        out["channel"] = data["channel"]
        out["weekly_delta"] = data["weekly"]
        if data["daily"]:
            y = data["daily"][-1]
            out["yesterday"] = {
                "views": int(y.get("views", 0)),
                "subs_gained": int(y.get("subscribersGained", 0)),
                "subs_lost": int(y.get("subscribersLost", 0)),
                "watch_minutes": int(y.get("estimatedMinutesWatched", 0)),
            }
        out["available"] = True
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        log.warning(f"YT collect 실패: {exc}")
    return out


def youtube_collector(*, output_dir: str | Path, build_report: BuildReport,
                      channel_id: str | None = None, now: datetime | None = None) -> Collector:
    """엔진 `run_brief`에 넣을 무인자 수집기로 감싼다."""
    return lambda: collect_yt(output_dir=output_dir, build_report=build_report,
                              channel_id=channel_id, now=now)


def run_brief(*, output_dir: str | Path, build_report: BuildReport,
              channel_id: str | None = None, gateway=None,
              model: str = "gemini-2.5-flash", now: datetime | None = None) -> dict:
    """youtube 소스 수집 → 우선순위 브리핑. 원본 `run_brief`의 youtube 구성(AXIS 제외)."""
    now = now or datetime.now()
    collectors = {"youtube": youtube_collector(
        output_dir=output_dir, build_report=build_report, channel_id=channel_id, now=now)}
    return _run_brief(collectors, system=_BRIEF_SYSTEM, gateway=gateway, model=model,
                      today=now.date().isoformat(), now=now.isoformat(timespec="minutes"))


def answer(question: str, context: dict, *, gateway=None, model: str = "gemini-2.5-flash") -> str:
    """youtube 운영 데이터 근거 자유 질의 응답."""
    return _answer(question, context, system=_ANSWER_SYSTEM, gateway=gateway, model=model)
