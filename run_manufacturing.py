# -*- coding: utf-8 -*-
"""
run_manufacturing.py — manufacturing 어댑터 **엔드투엔드 실행 진입점** (프로덕션, 대화형 HITL 포함).

`demo.py`의 `manufacturing` 데모와 다르다. 데모는 **키 없이 도는 조각만** 보여주려고 초안을
손으로 채운다(citation 데모와 같은 이유 — LLM 호출은 API 키가 필요하다). 이 스크립트는
**실제로** `produce → gate → (필요시 사람 승인) → publish`를 돌린다 — `docs/metrics.md`가
정직하게 남겨둔 *"end-to-end 파이프라인을 구동하는 프로덕션 진입점은 없다"*를 manufacturing
세로선에서 닫는다.

    python run_manufacturing.py                                     # 기본 시나리오
    python run_manufacturing.py --customer 고객사A --product P-100 --new-date 2026-08-28
    python run_manufacturing.py --data-dir data/manufacturing --out-dir output/manufacturing

**API 키가 없어도 죽지 않는다.** `ManufacturingAdapter`가 기본 게이트웨이(`default_gemini()`)를
쓰므로, 키가 없으면 초안 생성이 "LLM 게이트웨이 미가용"으로 안전 폴백하고, review가 그걸
`draft_missing`(high)으로 잡아 `reject`한다 — 없는 것을 있는 척하지 않는다. **사람 승인이
필요한 경우(`requires_human`)에만** 터미널에서 실제로 y/n을 묻는다(`core.approval.console_ask`).
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from adapters.manufacturing.adapter import ManufacturingAdapter
from adapters.manufacturing.analyze import ShipmentChangeTrigger
from adapters.manufacturing.draft import DEFAULT_TIMEOUT_SECONDS, render_impact_facts
from adapters.manufacturing.errors import DraftGenerationError
from adapters.manufacturing.erp_data import load_erp_snapshot
from adapters.manufacturing.publish import FileConfirmationSink, ManufacturingPublisher
from core.approval import ApprovalPrompt, console_ask
from core.llm import LLMGateway

_DEFAULT_DATA_DIR = Path(__file__).parent / "data" / "manufacturing"
_DEFAULT_OUT_DIR = Path(__file__).parent / "output" / "manufacturing"


def _force_utf8_console() -> None:
    """Windows 콘솔(cp949)에서 한글·이모지가 깨지지 않게. import 시점이 아니라 실행 시점에만.

    stdout뿐 아니라 stderr도 바꾼다 — `demo.py`(produce를 안 부름)와 달리 이 스크립트는
    `draft.py`의 `log.warning`(게이트웨이 미가용 등)이 실제로 stderr에 찍힌다. stdout만 고치면
    로그 쪽은 그대로 cp949로 깨진다(🟢 실행해서 직접 확인 — 이 스크립트를 실제로 돌려보기 전엔
    `demo.py`가 이 경로를 한 번도 안 밟아서 드러나지 않았던 결함이다).
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="manufacturing 어댑터 실행 — 출하일 변경 → 연쇄 영향 분석 → HITL 승인 → 확정.")
    parser.add_argument("--customer", default="고객사A", help="출하 건의 고객사 (기본: 고객사A)")
    parser.add_argument("--product", default="P-100", help="출하 건의 품목코드 (기본: P-100)")
    parser.add_argument("--new-date", default="2026-08-28", dest="new_date",
                        help="변경된 출하예정일 YYYY-MM-DD (기본: 2026-08-28)")
    parser.add_argument("--data-dir", default=str(_DEFAULT_DATA_DIR), dest="data_dir",
                        help="ERP CSV 5개가 있는 디렉터리")
    parser.add_argument("--out-dir", default=str(_DEFAULT_OUT_DIR), dest="out_dir",
                        help="확정 문서를 쓸 디렉터리")
    parser.add_argument('--timeout-seconds', type=_positive_seconds, default=DEFAULT_TIMEOUT_SECONDS,
                        help='기본 Gemini 호출 1회의 제한 시간(초), 기본 30. 자동 재시도 없음')
    return parser.parse_args(argv)


def _positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError('제한 시간은 양수인 유한 숫자여야 합니다.')
    return seconds


def run(args: argparse.Namespace, *, ask: ApprovalPrompt = console_ask,
        gateway: LLMGateway | None = None) -> int:
    """파싱된 인자 → 종료 코드. `ask`·`gateway` 주입으로 대화형·네트워크 없이도 테스트 가능
    (`gateway` 미지정 시 `ManufacturingAdapter` 기본값 = 실 `default_gemini()`)."""
    try:
        snapshot = load_erp_snapshot(args.data_dir)
    except (FileNotFoundError, OSError) as exc:
        print(f"❌ ERP 데이터를 읽을 수 없습니다({args.data_dir}): {exc}")
        return 2

    trigger = ShipmentChangeTrigger(customer=args.customer, product=args.product,
                                    new_ship_date=args.new_date)
    adapter = ManufacturingAdapter(
        snapshot, gateway=gateway,
        publisher=ManufacturingPublisher(FileConfirmationSink(args.out_dir)),
        timeout_seconds=args.timeout_seconds)

    try:
        produced = adapter.produce(trigger)
    except DraftGenerationError as exc:
        print(f'초안 생성 실패: stage={exc.stage} code={exc.kind}. 발행하지 않음. 자동 재시도 없음.')
        return 3
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1

    def ask_with_preview(decision):
        print('\n검토할 발행 내용 (아직 저장되지 않음)')
        print(render_impact_facts(produced['impact']))
        for title, key in (('일정 조정안', 'adjustment_plan'),
                           ('협력업체 요청', 'supplier_request'),
                           ('고객사 회신', 'customer_reply')):
            print(f'\n## {title}\n{produced[key]}')
        return ask(decision)

    authorization = adapter.authorize_publish(produced, ask=ask_with_preview)
    decision = authorization.decision
    v = decision.verdict
    print(f"게이트 판정: {v.recommendation} (위반 {len(v.violations)}건)")
    for violation in v.violations:
        print(f"  └ [{violation.severity}] {violation.category}: {violation.detail}")

    outcome = authorization.outcome
    if not outcome.publish:
        print(f"발행하지 않음 (source={outcome.source})")
        return 0

    try:
        result = adapter.publish(produced, permit=authorization.permit)
    except PermissionError as exc:
        print(f'발행하지 않음: {exc}')
        return 1
    print(f"✅ 확정 발행: {result['location']}")
    return 0


def main(argv: list[str] | None = None, *, ask: ApprovalPrompt = console_ask) -> int:
    _force_utf8_console()
    return run(_parse_args(argv), ask=ask)


if __name__ == "__main__":
    raise SystemExit(main())

