# -*- coding: utf-8 -*-
"""
run_manufacturing.py 스모크 테스트 — 실제 e2e 진입점.

`demo.py`의 manufacturing 데모와 달리 이 스크립트는 **produce를 진짜 부른다**. 키 없이 안전하게
reject되는 경로와, 가짜 게이트웨이로 실제 approve→publish·review(HITL)→승인/반려까지 가는
경로를 함께 고정한다. 실 네트워크·실 콘솔 입력은 쓰지 않는다(게이트웨이·ask 둘 다 주입).
"""
from __future__ import annotations

from pathlib import Path

import run_manufacturing as rm

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "manufacturing"


class _CleanGateway:
    """세 프롬프트 다 알려진 코드만 언급하고, 고객사 회신엔 내부 정보를 안 담는다(정책 준수)."""

    available = True

    def complete(self, system: str, prompt: str, **kwargs) -> str:
        if "회신" in system:   # _CUSTOMER_SYSTEM_PROMPT만 "회신"을 요구한다
            return "출하 일정을 검토 중입니다. 곧 회신드리겠습니다."
        return "M-01 자재 부족 — 협력사A에 요청 필요."


class _LeakyGateway:
    """고객사 회신에도 협력업체명·자재코드를 그대로 흘린다 — review가 잡아야 하는 케이스."""

    available = True

    def complete(self, system: str, prompt: str, **kwargs) -> str:
        return "협력사A의 M-01 자재가 부족해 조율 중입니다."


class _Unreachable:
    """호출되면 실패하는 가짜 `ask` — approve/reject 경로에서 사람에게 안 감을 증명."""

    def __call__(self, decision):
        raise AssertionError("ask가 불릴 필요 없는 경로에서 호출됐다")


def _args(tmp_path, **overrides):
    defaults = {"customer": "고객사A", "product": "P-100", "new-date": "2026-08-28",
                "data-dir": str(DATA_DIR), "out-dir": str(tmp_path)}
    defaults.update(overrides)
    return rm._parse_args([f"--{k}={v}" for k, v in defaults.items()])


# ── 키 없이도 안전 ────────────────────────────────────────────────────


def test_run_rejects_gracefully_without_api_key(tmp_path, capsys):
    """키 없이 돌아도 죽지 않는다 — draft_missing이 잡혀 reject, 사람에게도 안 간다."""
    code = rm.run(_args(tmp_path), ask=_Unreachable())
    out = capsys.readouterr().out

    assert code == 0
    assert "reject" in out
    assert "발행하지 않음" in out
    assert list(tmp_path.iterdir()) == []


# ── 가짜 게이트웨이로 실제 approve→publish ───────────────────────────


def test_run_publishes_when_gateway_and_review_are_clean(tmp_path, capsys):
    code = rm.run(_args(tmp_path), ask=_Unreachable(), gateway=_CleanGateway())
    out = capsys.readouterr().out

    assert code == 0
    assert "approve" in out
    assert "확정 발행" in out
    assert list(tmp_path.glob("*.md"))


# ── HITL: 정보 노출은 review로 가고, 사람 판단을 따른다 ──────────────


def test_run_asks_human_and_respects_rejection(tmp_path, capsys):
    code = rm.run(_args(tmp_path), ask=lambda d: False, gateway=_LeakyGateway())
    out = capsys.readouterr().out

    assert code == 0
    assert "review" in out
    assert "human_reject" in out
    assert list(tmp_path.glob("*.md")) == []


def test_run_asks_human_and_publishes_on_approval(tmp_path):
    code = rm.run(_args(tmp_path), ask=lambda d: True, gateway=_LeakyGateway())

    assert code == 0
    assert list(tmp_path.glob("*.md"))


# ── 에러 경로: 크래시 없이 정직하게 보고 ──────────────────────────────


def test_run_reports_unknown_shipment_without_crashing(tmp_path, capsys):
    code = rm.run(_args(tmp_path, customer="없는고객사"), ask=_Unreachable())
    out = capsys.readouterr().out

    assert code == 1
    assert "출하 계획에 없는 건" in out


def test_run_reports_missing_data_dir_without_crashing(tmp_path, capsys):
    missing = tmp_path / "no-such-dir"
    code = rm.run(_args(tmp_path, **{"data-dir": str(missing)}), ask=_Unreachable())
    out = capsys.readouterr().out

    assert code == 2
    assert "ERP 데이터를 읽을 수 없습니다" in out


# ── main(): argv 파싱 + ask 배선 ──────────────────────────────────────


def test_main_wires_argv_without_asking_when_no_key(tmp_path):
    """키 없음 → reject → ask 호출 안 됨(Unreachable이 안 터지면 배선이 맞다는 뜻)."""
    code = rm.main(["--out-dir", str(tmp_path)], ask=_Unreachable())
    assert code == 0


def test_human_sees_facts_and_all_drafts_before_deciding(tmp_path, capsys):
    def ask(decision):
        preview = capsys.readouterr().out
        for expected in ('아직 저장되지 않음', '부족 440개', '## 일정 조정안',
                         '## 협력업체 요청', '## 고객사 회신', '협력사A의 M-01'):
            assert expected in preview
        assert list(tmp_path.glob('*.md')) == []
        return False

    assert rm.run(_args(tmp_path), ask=ask, gateway=_LeakyGateway()) == 0
    assert list(tmp_path.glob('*.md')) == []


def test_wrong_quantity_is_rejected_without_asking_or_writing(tmp_path):
    class WrongQuantity(_CleanGateway):
        def complete(self, system, prompt, **kwargs):
            return 'M-01 부족 수량 500개'  # 실제 부족분은 440, 500은 출하수량.

    assert rm.run(_args(tmp_path), ask=_Unreachable(), gateway=WrongQuantity()) == 0
    assert list(tmp_path.glob('*.md')) == []

