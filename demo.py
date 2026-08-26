# -*- coding: utf-8 -*-
"""
demo.py — mint-agent를 **API 키 없이** 돌려보는 진입점.

    python demo.py            # 전체
    python demo.py gate       # 발행 게이트만
    python demo.py --help     # 목록

보여주는 것은 전부 **키·네트워크 없이 실제로 도는 코드**다. 실 LLM 호출·업로드·임베딩은
키가 필요하므로 데모에 넣지 않는다 — 없는 것을 있는 척하지 않는다.

  1. gate      발행 게이트: AI 라벨 없는 콘텐츠를 사람 판단으로 돌린다 (룰 기반, 결정적)
  2. citation  환각 인용 검증: 없는 출처를 인용하면 거절한다 (LLM 불필요)
  3. routing   복잡도 비용 라우팅: 쉬운 일에 비싼 모델을 쓰지 않는다 (실단가 기준)
  4. consensus 컨센서스 게이트: 리뷰어가 갈리면 사람에게
  5. audit     게이트 결정 → 감사 로그 → eval 하니스 → 리포트 (루프가 닫힌다)
  6. manufacturing 제조 역방향 연쇄 + HITL 승인: 출하일 변경 → 부족 자재 산출 →
                   환각 코드·정보 노출 검증 → 사람 승인/반려 시뮬레이션
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from adapters.manufacturing.adapter import ManufacturingAdapter
from adapters.manufacturing.analyze import ShipmentChangeTrigger, analyze_shipment_change
from adapters.manufacturing.erp_data import load_erp_snapshot
from adapters.research.adapter import ResearchAdapter
from adapters.research.review import CitationReviewer
from adapters.youtube.adapter import YouTubeAdapter
from core.approval import console_ask, resolve  # noqa: F401 — 재노출(demo.console_ask), 실행 경로에서는 안 씀
from core.gateway import default_claude, default_gemini
from core.observability import render_gate_report
from core.pipeline import gate, gate_consensus, jsonl_audit_sink
from core.routing import AUTO, estimate_cost
from core.trust.eval import load_jsonl
from core.trust.verify import Verdict

CLOCK = "2026-07-10T09:00:00"


def _force_utf8_stdout() -> None:
    """Windows 콘솔(cp949)에서 한글·이모지가 깨지지 않게. import 시점이 아니라 실행 시점에만."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def _title(n: int, text: str) -> None:
    print(f"\n{'─' * 66}\n {n}. {text}\n{'─' * 66}")


# ── 1. 발행 게이트 ─────────────────────────────────────────────────────────

def demo_gate() -> None:
    _title(1, "발행 게이트 — approve만 통과. 애매하면 멈추고 사람에게")

    adapter = YouTubeAdapter(review_deep=False)   # 룰만 사용(LLM 심층 검사 끔)
    script = {"title": "The Murim Genius", "body": "A young martial artist levels up."}

    labeled = {"title": "K-Webtoon Short", "description": "Epic short. AI-generated assets used."}
    unlabeled = {"title": "K-Webtoon Short", "description": "Epic short."}

    for name, seo in (("AI 라벨 있음", labeled), ("AI 라벨 없음", unlabeled)):
        d = gate(adapter, script, seo)
        flag = "발행 진행" if d.proceed else ("사람 판단 대기" if d.requires_human else "거절")
        print(f"  {name:14s} → {d.verdict.recommendation:8s} proceed={str(d.proceed):5s}  {flag}")
        for v in d.verdict.violations:
            print(f"                   └ [{v.severity}] {v.category}")

    print("\n  거절과 보류는 다르다 — requires_human일 때만 사람을 부른다.")


# ── 2. 환각 인용 검증 ──────────────────────────────────────────────────────

def demo_citation() -> None:
    _title(2, "환각 인용 검증 — LLM 없이 잡는다 (사실 문제이므로)")

    hits = [
        {"title": "T1", "url": "https://a.com/1", "fetched_chars": 5231},
        {"title": "T2", "url": "https://b.com/2", "fetched_chars": 0},      # 페치 실패
    ]
    reviewer = CitationReviewer(deep=False)

    cases = {
        "정상 인용": "- 사실 A [출처 1]\n\n## 출처\n1. T1 — https://a.com/1",
        "없는 출처 인용": "- 사실 A [출처 7]\n\n## 출처\n1. T1",
        "안 읽은 소스 인용": "- 사실 A [출처 1]\n- 사실 B [출처 2]\n\n## 출처\n1. T1",
        "인용 없음": "그냥 단언합니다.\n\n## 출처\n1. T1",
    }
    for name, summary in cases.items():
        v = reviewer.review({"query": "q", "summary": summary, "hits": hits})
        cats = ", ".join(x.category for x in v.violations if x.severity == "high") or "—"
        print(f"  {name:18s} → {v.recommendation:8s} {cats}")

    print("\n  '출처 2'는 페치에 실패해 요약 입력에 들어가지도 않았다. 인용했다면 지어낸 것이다.")


# ── 3. 복잡도 비용 라우팅 ──────────────────────────────────────────────────

def demo_routing() -> None:
    _title(3, "복잡도 비용 라우팅 — 쉬운 일에 비싼 모델을 쓰지 않는다")

    gemini = default_gemini(api_key="demo")       # 호출 없이 라우팅만 미리보기
    claude = default_claude(api_key="demo")
    prompts = [("이 문단을 요약해줘", "저난도"), ("이 아키텍처를 분석·리팩터해줘", "고난도")]

    print("  프로바이더 안에서 티어링한다(SDK가 바뀌지 않는다):\n")
    for prompt, label in prompts:
        g, _ = gemini.route_for("", prompt, model=AUTO)
        c, _ = claude.route_for("", prompt, model=AUTO)
        print(f"  {label}  {prompt[:18]:20s} Gemini→{g:22s} Claude→{c}")

    lite = estimate_cost("gemini-2.5-flash-lite", 10_000, 2_000)
    pro = estimate_cost("gemini-2.5-pro", 10_000, 2_000)
    print(f"\n  같은 요청(10k in / 2k out)의 실단가:")
    print(f"    저난도 티어 flash-lite  ${lite:.5f}")
    print(f"    고난도 티어 pro         ${pro:.5f}   ({pro / lite:.0f}배)")
    print("\n  단가는 프로바이더 공식 문서로 확인한 값이다. 표에 없는 모델은 비용을 매기지 않는다.")


# ── 4. 컨센서스 게이트 ────────────────────────────────────────────────────

def demo_consensus() -> None:
    _title(4, "컨센서스 게이트 — 리뷰어가 갈리면 사람에게")

    def approve() -> Verdict:
        return Verdict(passed=True, score=0.0, recommendation="approve")

    def review() -> Verdict:
        return Verdict(passed=True, score=0.1, recommendation="review")

    def reject() -> Verdict:
        return Verdict(passed=False, score=0.5, recommendation="reject")

    def broken() -> Verdict:
        raise RuntimeError("모델 응답 없음")

    cases = {
        "전원 승인": [approve, approve, approve],
        "한 명 거절": [approve, approve, reject],
        "의견 갈림": [approve, review],
        "리뷰어 장애": [approve, broken],
        "리뷰어 없음": [],
    }
    for name, reviewers in cases.items():
        d = gate_consensus(reviewers)
        who = "사람" if d.requires_human else ("거절" if d.rejected else "발행")
        print(f"  {name:12s} → {d.verdict.recommendation:8s} {d.agreement or '—':10s} → {who}")

    print("\n  아무도 검증하지 않은 것은 통과시키지 않는다. 리뷰어가 터져도 멈추지 않는다.")


# ── 5. 감사 루프 ──────────────────────────────────────────────────────────

def demo_audit() -> None:
    _title(5, "감사 루프 — 게이트 결정 → JSONL → eval 하니스 → 리포트")

    youtube = YouTubeAdapter(review_deep=False)
    research = ResearchAdapter(review_deep=False)   # 조립만 — produce/publish는 부르지 않는다
    script = {"title": "T", "body": "b"}
    hits = [{"title": "T1", "url": "https://a.com/1", "fetched_chars": 5231},
            {"title": "T2", "url": "https://b.com/2", "fetched_chars": 4102}]
    sources = "\n\n## 출처\n1. T1\n2. T2"
    bad_citation = {"query": "q", "hits": hits,
                    "summary": "- A [출처 1]\n- B [출처 2]\n- C [출처 7]" + sources}
    no_citation = {"query": "q", "hits": hits, "summary": "그냥 단언합니다."}

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "gate_audit.jsonl"
        sink = jsonl_audit_sink(path, clock=lambda: CLOCK)

        gate(youtube, script, {"description": "AI-generated assets used."},
             sink=sink, channel="youtube", title="라벨 있음")
        for _ in range(2):
            gate(youtube, script, {"description": "no disclosure"},
                 sink=sink, channel="youtube", title="라벨 없음")
        for _ in range(2):
            gate(research, bad_citation, sink=sink, channel="research", title="없는 출처 인용")
        for _ in range(2):
            gate(research, no_citation, sink=sink, channel="research", title="인용 없음")
        gate_consensus([lambda: Verdict(True, 0.0, recommendation="approve"),
                        lambda: Verdict(True, 0.1, recommendation="review")],
                       sink=sink, channel="research", title="리뷰어 불일치")

        records = load_jsonl(path)
        print(f"  게이트 결정 {len(records)}건을 기록했다: {path.name}\n")
        print("\n".join("  " + l for l in
                        render_gate_report(records, min_count=2).splitlines()))

    print("\n  우리 게이트가 남긴 로그를 우리 eval 하니스가 그대로 읽는다.")
    print("  '모순 감사'는 정답 라벨 없이 게이트가 자기 규칙과 어긋나는지 본다.")
    print("  원본 로그는 위반 '개수'만 남겨서 어떤 규칙이 터졌는지 물을 수 없었다.")
    print("  이제 물을 수 있다 — 단, 함께 뜬 규칙을 승격 후보로 착각하지 않는다(단독 열).")


# ── 6. 제조 도메인 — 역방향 연쇄 + HITL 승인 ─────────────────────────────────

_MANUFACTURING_DATA = Path(__file__).parent / "data" / "manufacturing"


def demo_manufacturing() -> None:
    _title(6, "제조 도메인 — 출하일 변경의 역방향 연쇄 + HITL 승인")

    snapshot = load_erp_snapshot(_MANUFACTURING_DATA)
    trigger = ShipmentChangeTrigger(customer="고객사A", product="P-100", new_ship_date="2026-08-28")
    impact = analyze_shipment_change(snapshot, trigger)

    print(f"  고객사A: P-100 출하 {impact.shipment.ship_date} → {impact.new_ship_date} "
          f"({impact.days_pulled_forward}일 앞당김)")
    for sf in impact.shortfalls:
        late = ", ".join(f"{l.due_date} {l.qty}개" for l in sf.late_arrivals)
        print(f"    └ 부족: [{sf.material}] {sf.shortfall}개 (지연 입고: {late})")

    # 아래 3케이스는 초안을 손으로 채운다(citation 데모와 같은 이유) — LLM 호출은 키가 필요하다.
    adapter = ManufacturingAdapter(snapshot)
    clean = {
        "impact": impact,
        "adjustment_plan": "M-01 자재 440개 부족 — 협력사A에 긴급 요청 필요.",
        "supplier_request": "M-01 자재를 2026-08-28까지 440개 추가 요청드립니다.",
        "customer_reply": "출하 일정을 검토 중입니다. 곧 회신드리겠습니다.",
    }
    hallucinated = {**clean, "supplier_request": "M-99도 같이 요청해야 합니다."}
    leaky = {**clean, "customer_reply": "협력사A의 M-01 자재가 부족해 조율 중입니다."}

    print()
    for name, content in (("정상 초안", clean), ("환각 코드(M-99)", hallucinated),
                          ("고객에 내부정보 노출", leaky)):
        d = gate(adapter, content)
        flag = "발행 진행" if d.proceed else ("사람 판단 대기" if d.requires_human else "거절")
        print(f"  {name:20s} → {d.verdict.recommendation:8s} {flag}")

    print("\n  '고객에 내부정보 노출'만 review다 — 환각이 아니라 정책 문제라 사람이 최종 판단한다.")
    print("  HITL 승인(core.approval.resolve) — 실제 입력 대신 두 결과를 시뮬레이션:")

    leak_decision = gate(adapter, leaky)
    for label, ask in (("사람이 승인", lambda d: True), ("사람이 반려", lambda d: False)):
        outcome = resolve(leak_decision, ask=ask)
        print(f"    {label} → publish={outcome.publish} (source={outcome.source})")

    print("\n  실전에서는 ask=console_ask로 바꾸면 터미널에서 실제 y/n을 받는다(대화형 전용).")


DEMOS = {
    "gate": demo_gate,
    "citation": demo_citation,
    "routing": demo_routing,
    "consensus": demo_consensus,
    "audit": demo_audit,
    "manufacturing": demo_manufacturing,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="mint-agent 데모 — API 키·네트워크 없이 실제로 도는 코드만 보여준다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="예: python demo.py gate routing",
    )
    # `choices=`를 쓰지 않는다: Python 3.12/3.13의 argparse는 `nargs="*"`에서 기본값 `[]`까지
    # choices에 대해 검증해 인자 없이 실행하면 죽는다(3.14에서 수정됨). 직접 검증한다.
    parser.add_argument("demos", nargs="*", default=[],
                        help=f"실행할 데모 ({' · '.join(DEMOS)}). 생략하면 전체.")
    args = parser.parse_args(argv)

    unknown = [d for d in args.demos if d not in DEMOS]
    if unknown:
        parser.error(f"알 수 없는 데모: {', '.join(unknown)} (가능: {' · '.join(DEMOS)})")

    _force_utf8_stdout()
    chosen = args.demos or list(DEMOS)
    print("mint-agent 데모 — API 키 불필요, 네트워크 불필요")
    print("실 LLM 호출·업로드·임베딩은 키가 필요하므로 데모에 없다.")
    for name in chosen:
        DEMOS[name]()
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
