# -*- coding: utf-8 -*-
"""
adapters.research._http — 공유 HTTP 수집 seam(요청 실행·디코딩).

`search`(검색 결과 HTML)와 `fetch`(문서 본문) 둘 다 `urllib`로 GET 한다. ffmpeg 러너를
`adapters.youtube._ffmpeg`로 모았던 것과 같은 근거로, **두 번째 호출부가 생긴 시점**에
실행 seam을 한 곳으로 모은다. 각 모듈의 '파싱'은 서로 다르므로 각자 보유하고,
여기엔 **공통 요청 계약**만 둔다.

주입 이유: 네트워크는 비결정적이다. 테스트는 가짜 fetcher를 주입해 HTML 픽스처로 결정적으로
돌고, 운영은 `urllib_fetch`를 쓴다. 어댑터 코드는 어느 쪽인지 모른다.
"""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import Callable

# 원본(youtube-agent/agents/research.py)과 포맷 동일, 제품 토큰만 갱신(의도적 차이).
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) mint-agent/research/1.0"
TIMEOUT = 12


@dataclass(frozen=True)
class HttpResponse:
    """GET 결과 최소형 — 본문 텍스트 + Content-Type(본문 페치의 타입 게이트가 쓴다)."""

    text: str
    content_type: str = ""


# fetch(url, *, timeout=TIMEOUT, max_bytes=None) -> HttpResponse
HttpFetcher = Callable[..., HttpResponse]


def urllib_fetch(url: str, *, timeout: int = TIMEOUT,
                 max_bytes: int | None = None) -> HttpResponse:
    """실 네트워크 GET. `max_bytes`가 있으면 그만큼만 읽는다(본문 페치의 상한).

    디코딩은 원본과 동일하게 `utf-8`/`errors="replace"` — 깨진 바이트로 죽지 않는다.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (스킴은 호출부 통제)
        ctype = r.headers.get("Content-Type", "")
        raw = r.read() if max_bytes is None else r.read(max_bytes)
    return HttpResponse(text=raw.decode("utf-8", errors="replace"), content_type=ctype)
