# -*- coding: utf-8 -*-
"""
adapters.research.publish — 리서치 다이제스트 발행 (produce/review/**publish**의 publish).

youtube publish와 같은 두 층 분리:

  1. **순수 로직** — `render_digest()` · `slugify()` · `build_meta()`: 다이제스트 → 마크다운 문서.
     결정적이라 특성화 테스트로 고정한다.
  2. **I/O 층** — `ResearchPublisher`가 `DigestSink`를 **주입**받아 전달을 위임. 테스트는 가짜 sink를
     넣어 호출·결과를 검증하고, `FileDigestSink`는 tmp 디렉터리로 실제 쓰기까지 검증한다.

**정직성: 원본에 발행 단계가 없다** — `youtube-agent/agents/research.py`는 dict를 돌려줄 뿐이다.
따라서 이식이 아니라 신규 구축이며 parity 대조 대상이 아니다(`review`·`consensus`와 같은 표기).

발행물에 **수집 메타를 남긴다.** 요약이 인용한 출처와 별개로, 어떤 소스를 실제로 읽었고 어떤 소스가
페치에 실패했는지가 문서에 보여야 한다 — 그래야 나중에 그 요약을 다시 믿을지 판단할 수 있다.
(이 정보는 `review`의 `citation_unfetched_source` 규칙이 보는 것과 같은 신호다.)

**덮어쓰기 안전.** 발행은 되돌리기 어려운 외부 행위다. `FileDigestSink`는 기본적으로 기존 파일을
덮어쓰지 않고 `-2`, `-3` … 접미사를 붙인다.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

log = logging.getLogger("mint.adapters.research.publish")

_MAX_SLUG = 60
_UNSAFE = re.compile(r"[^\w\s-]", re.UNICODE)   # 파일명 금지·구분 문자 제거
_SPACES = re.compile(r"[\s_]+")
_DASHES = re.compile(r"-{2,}")


def _now() -> str:
    """기본 시계 — ISO 초 단위. 결정적 테스트는 clock을 주입한다."""
    return datetime.now().isoformat(timespec="seconds")


# ── 순수 로직 ─────────────────────────────────────────────────────────────


def slugify(text: str, *, max_len: int = _MAX_SLUG) -> str:
    """질의 → 파일명 조각. 한글은 보존하고(NFC 정규화) 금지 문자만 턴다. (순수)"""
    s = unicodedata.normalize("NFC", text).strip().lower()
    s = _UNSAFE.sub("", s)
    s = _SPACES.sub("-", s)
    s = _DASHES.sub("-", s).strip("-")
    return s[:max_len].rstrip("-") or "digest"


def build_meta(digest: dict[str, Any], *, generated_at: str) -> dict[str, Any]:
    """다이제스트 → 발행 메타(수집 통계 포함). (순수)"""
    hits = list(digest.get("hits") or [])
    query = str(digest.get("query", "") or "")
    fetched = sum(1 for h in hits if int(h.get("fetched_chars", 0) or 0) > 0)
    return {
        "query": query,
        "generated_at": generated_at,
        "sources": len(hits),
        "fetched": fetched,
        "slug": slugify(query),
    }


def render_digest(digest: dict[str, Any], *, generated_at: str = "") -> str:
    """다이제스트 → 마크다운 문서 (순수·결정적).

    구성: 제목(질의) · 생성 메타 · 요약 원문 · 수집 정보 표(페치 실패를 숨기지 않는다).
    """
    meta = build_meta(digest, generated_at=generated_at)
    hits = list(digest.get("hits") or [])
    summary = str(digest.get("summary", "") or "").strip()

    lines = [f"# {meta['query']}", ""]
    if generated_at:
        lines.append(f"> 생성: {generated_at} · 출처 {meta['sources']}건 중 {meta['fetched']}건 본문 수집")
        lines.append("")
    lines.append(summary if summary else "_요약 없음_")
    lines += ["", "---", "", "## 수집 정보", "",
              "| # | 제목 | URL | 본문(자) |",
              "|---|------|-----|---------|"]

    for i, h in enumerate(hits, start=1):
        chars = int(h.get("fetched_chars", 0) or 0)
        # 페치 실패를 표에 남긴다 — 이 소스는 요약 입력에 들어가지 않았다.
        cell = f"{chars:,}" if chars > 0 else "❌ 페치 실패"
        lines.append(f"| {i} | {h.get('title', '')} | {h.get('url', '')} | {cell} |")

    if not hits:
        lines.append("| — | _출처 없음_ | | |")

    return "\n".join(lines) + "\n"


# ── I/O 층 ───────────────────────────────────────────────────────────────


class DigestSink(Protocol):
    """발행 I/O 경계 — 실 구현(파일·Discord·…)과 테스트 fake가 만족하는 계약."""

    def deliver(self, doc: str, meta: dict[str, Any]) -> str:
        """문서를 전달하고 위치 식별자(경로·URL·메시지 ID 등)를 돌려준다."""
        ...


class FileDigestSink:
    """마크다운 파일로 발행. 경로 주입(테스트는 tmp 디렉터리).

    파일명은 `{날짜}-{슬러그}.md`. 같은 이름이 있으면 `overwrite=True`가 아닌 한 `-2`, `-3` …을 붙인다.
    """

    def __init__(self, directory: str | Path, *, overwrite: bool = False) -> None:
        self._dir = Path(directory)
        self._overwrite = overwrite

    def _resolve_path(self, meta: dict[str, Any]) -> Path:
        date = str(meta.get("generated_at", ""))[:10] or "undated"
        base = f"{date}-{meta.get('slug', 'digest')}"
        path = self._dir / f"{base}.md"
        if self._overwrite:
            return path
        n = 2
        while path.exists():
            path = self._dir / f"{base}-{n}.md"
            n += 1
        return path

    def deliver(self, doc: str, meta: dict[str, Any]) -> str:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._resolve_path(meta)
        path.write_text(doc, encoding="utf-8")
        log.info("다이제스트 발행: %s", path)
        return str(path)


class ResearchPublisher:
    """publish 단계 — 렌더(순수) + sink 위임(I/O). sink 주입으로 테스트 가능."""

    def __init__(self, sink: DigestSink, *, clock: Callable[[], str] = _now) -> None:
        self._sink = sink
        self._clock = clock

    def publish(self, digest: dict[str, Any]) -> dict[str, Any]:
        """다이제스트 → 발행. `{location, chars, sources, fetched, generated_at}` 반환."""
        generated_at = self._clock()
        meta = build_meta(digest, generated_at=generated_at)
        doc = render_digest(digest, generated_at=generated_at)
        location = self._sink.deliver(doc, meta)
        return {
            "location": location,
            "chars": len(doc),
            "sources": meta["sources"],
            "fetched": meta["fetched"],
            "generated_at": generated_at,
        }
