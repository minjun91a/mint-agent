# -*- coding: utf-8 -*-
"""
adapters.youtube._ffmpeg — 공유 ffmpeg 인프라(실행 러너·길이 파싱·경로 해석).

ffmpeg를 쓰는 모듈이 둘이 되면서(`video_builder`·`tts_engine`) 실행 seam(러너 주입)과 stderr
길이 파싱을 한 곳으로 모은다(브리프: 일반화는 2번째가 강제할 때). 각 모듈의 '명령 빌더'는
명령이 서로 다르므로 각자 보유하고, 여기엔 **공통 실행 계약**만 둔다.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class RunResult:
    """명령 실행 결과 — 러너 주입 계약(반환 최소형)."""

    returncode: int
    stderr: str = ""


# run(cmd, *, cwd=None) -> RunResult
CommandRunner = Callable[..., RunResult]


def parse_duration(stderr: str) -> float:
    """ffmpeg `-i` stderr에서 Duration(hh:mm:ss.ss)을 초로. 미검출 시 0.0."""
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", stderr)
    if not m:
        return 0.0
    h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mn * 60 + s


def default_ffmpeg() -> str:
    """imageio_ffmpeg 지연 해석 — 모듈 import 시점에 선택 의존성을 끌어오지 않기 위함."""
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def default_run(cmd: list[str], *, cwd: str | None = None) -> RunResult:
    """실 subprocess 실행 — stderr 캡처."""
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", cwd=cwd)
    return RunResult(returncode=r.returncode, stderr=r.stderr or "")
