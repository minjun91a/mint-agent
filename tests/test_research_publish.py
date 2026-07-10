# -*- coding: utf-8 -*-
"""
adapters.research.publish 특성화 테스트.

원본에 발행 단계가 없어 parity 대조 대상이 아니다(신규 구축). 렌더링은 순수라 목 없이 검증하고,
전달은 가짜 sink 주입 + `FileDigestSink`는 tmp 디렉터리로 실제 쓰기까지 확인한다.
"""
from __future__ import annotations

from pathlib import Path

from adapters.research.publish import (
    FileDigestSink,
    ResearchPublisher,
    build_meta,
    render_digest,
    slugify,
)

CLOCK = lambda: "2026-07-10T09:30:00"  # noqa: E731 — 결정적 시계 주입

DIGEST = {
    "query": "AI shorts trends 2026",
    "summary": "- 사실 A [출처 1]\n\n## 출처\n1. T1 — https://a.com/1",
    "hits": [
        {"title": "T1", "url": "https://a.com/1", "snippet": "s1", "fetched_chars": 5231},
        {"title": "T2", "url": "https://b.com/2", "snippet": "s2", "fetched_chars": 0},
    ],
}


class _FakeSink:
    """주입용 가짜 sink — 받은 문서·메타를 기록하고 가짜 위치를 돌려준다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def deliver(self, doc: str, meta: dict) -> str:
        self.calls.append((doc, meta))
        return "fake://digest/1"


# ── 순수: slugify ───────────────────────────────────────────────────


def test_slugify_lowercases_and_dashes_spaces():
    assert slugify("AI Shorts Trends 2026") == "ai-shorts-trends-2026"


def test_slugify_preserves_hangul():
    assert slugify("한글 질의") == "한글-질의"


def test_slugify_strips_unsafe_filename_chars():
    assert slugify('a/b:c*d?"e') == "abcde"


def test_slugify_collapses_repeated_dashes():
    assert slugify("a   -   b") == "a-b"


def test_slugify_truncates_and_trims_trailing_dash():
    assert slugify("x" * 80) == "x" * 60
    assert not slugify("word " + "y" * 100, max_len=5).endswith("-")


def test_slugify_empty_falls_back():
    assert slugify("!!!") == "digest"


# ── 순수: build_meta ────────────────────────────────────────────────


def test_build_meta_counts_fetched_sources():
    meta = build_meta(DIGEST, generated_at=CLOCK())
    assert meta["sources"] == 2
    assert meta["fetched"] == 1        # 2번은 페치 실패
    assert meta["slug"] == "ai-shorts-trends-2026"
    assert meta["generated_at"] == "2026-07-10T09:30:00"


def test_build_meta_with_no_hits():
    meta = build_meta({"query": "q", "summary": "", "hits": []}, generated_at="")
    assert (meta["sources"], meta["fetched"]) == (0, 0)


# ── 순수: render_digest ─────────────────────────────────────────────


def test_render_digest_has_query_title_and_summary():
    doc = render_digest(DIGEST, generated_at=CLOCK())
    assert doc.startswith("# AI shorts trends 2026")
    assert "- 사실 A [출처 1]" in doc


def test_render_digest_reports_collection_stats():
    doc = render_digest(DIGEST, generated_at=CLOCK())
    assert "출처 2건 중 1건 본문 수집" in doc


def test_render_digest_marks_failed_fetch_in_table():
    """페치 실패를 발행물이 숨기지 않는다 — 그 소스는 요약 입력에 없었다."""
    doc = render_digest(DIGEST, generated_at=CLOCK())
    assert "| 1 | T1 | https://a.com/1 | 5,231 |" in doc
    assert "| 2 | T2 | https://b.com/2 | ❌ 페치 실패 |" in doc


def test_render_digest_without_summary():
    doc = render_digest({"query": "q", "summary": "", "hits": []}, generated_at="")
    assert "_요약 없음_" in doc
    assert "_출처 없음_" in doc


def test_render_digest_omits_meta_line_without_clock():
    assert "생성:" not in render_digest(DIGEST)


def test_render_digest_is_deterministic():
    assert render_digest(DIGEST, generated_at=CLOCK()) == render_digest(DIGEST, generated_at=CLOCK())


# ── 주입: ResearchPublisher ─────────────────────────────────────────


def test_publisher_delivers_rendered_doc_and_meta():
    sink = _FakeSink()
    out = ResearchPublisher(sink, clock=CLOCK).publish(DIGEST)

    doc, meta = sink.calls[0]
    assert doc.startswith("# AI shorts trends 2026")
    assert meta["fetched"] == 1
    assert out["location"] == "fake://digest/1"
    assert out["chars"] == len(doc)
    assert out["generated_at"] == "2026-07-10T09:30:00"


def test_publisher_reports_source_counts():
    out = ResearchPublisher(_FakeSink(), clock=CLOCK).publish(DIGEST)
    assert (out["sources"], out["fetched"]) == (2, 1)


# ── FileDigestSink (실제 쓰기, tmp 경로) ────────────────────────────


def test_file_sink_writes_dated_slug_filename(tmp_path: Path):
    out = ResearchPublisher(FileDigestSink(tmp_path), clock=CLOCK).publish(DIGEST)

    path = Path(out["location"])
    assert path.name == "2026-07-10-ai-shorts-trends-2026.md"
    assert "# AI shorts trends 2026" in path.read_text(encoding="utf-8")


def test_file_sink_creates_missing_directory(tmp_path: Path):
    target = tmp_path / "nested" / "digests"
    ResearchPublisher(FileDigestSink(target), clock=CLOCK).publish(DIGEST)
    assert target.is_dir()


def test_file_sink_does_not_overwrite_by_default(tmp_path: Path):
    """발행은 되돌리기 어렵다 — 같은 이름이면 덮어쓰지 않고 접미사를 붙인다."""
    pub = ResearchPublisher(FileDigestSink(tmp_path), clock=CLOCK)
    first = Path(pub.publish(DIGEST)["location"])
    second = Path(pub.publish(DIGEST)["location"])
    third = Path(pub.publish(DIGEST)["location"])

    assert first.name == "2026-07-10-ai-shorts-trends-2026.md"
    assert second.name == "2026-07-10-ai-shorts-trends-2026-2.md"
    assert third.name == "2026-07-10-ai-shorts-trends-2026-3.md"
    assert first.exists() and second.exists() and third.exists()


def test_file_sink_overwrites_when_asked(tmp_path: Path):
    pub = ResearchPublisher(FileDigestSink(tmp_path, overwrite=True), clock=CLOCK)
    first = Path(pub.publish(DIGEST)["location"])
    second = Path(pub.publish(DIGEST)["location"])

    assert first == second
    assert len(list(tmp_path.glob("*.md"))) == 1


def test_file_sink_undated_when_clock_returns_empty(tmp_path: Path):
    out = ResearchPublisher(FileDigestSink(tmp_path), clock=lambda: "").publish(DIGEST)
    assert Path(out["location"]).name.startswith("undated-")
