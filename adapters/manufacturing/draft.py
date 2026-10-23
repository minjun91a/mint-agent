# -*- coding: utf-8 -*-
"""
adapters.manufacturing.draft — `CascadeImpact`(순수 계산 결과) → 문서 3종 초안(LLM).

research의 `summarize.py`와 같은 자리: LLM 모듈 표준 패턴(순수 프롬프트 조립 + 게이트웨이 주입).
**차이점 하나**: research는 호출 1번(요약 1개)이지만, 여기는 **독립 호출 3번**이다 — 대상 문서가
셋 다 독자·톤·허용 정보 범위가 달라(내부 검토용 / 협력업체 발송용 / 고객사 발송용), 계약을 하나로
합치면 뭉개진다(2026-08-26 설계 결정, `docs/roadmap.md` 참조).

세 프롬프트가 공유하는 규칙 하나: **`render_impact_facts`가 만든 사실 블록 밖의 숫자·자재코드·
날짜를 지어내지 않는다.** `analyze.py`가 이미 산수를 확정했으므로, LLM은 서술만 한다.
review 게이트는 지원하는 명시적 수량·날짜 표현을 대조하고, 연결할 수 없는 숫자는
사람 검토로 보낸다. 문장 전체의 의미를 보증하지는 않는다.
"""
from __future__ import annotations

import logging

from adapters.manufacturing.analyze import CascadeImpact
from core.gateway import default_gemini
from core.llm import LLMGateway

log = logging.getLogger("mint.adapters.manufacturing.draft")

_DEFAULT_MODEL = "gemini-2.5-flash"
_TEMPERATURE = 0.3

UNAVAILABLE = "❌ LLM 게이트웨이 미가용 — API 키를 확인하세요"

_ADJUSTMENT_SYSTEM_PROMPT = """당신은 제조 공정관리 담당자를 돕는 어시스턴트입니다.
아래 '출하 변경 사실'만 근거로 내부 검토용 일정 조정안을 한국어로 작성하세요.

규칙:
- 사실 블록에 없는 자재코드·수량·날짜·협력업체명을 지어내지 마세요.
- 부족 자재가 있으면 그 자재코드와 부족 수량을 그대로 인용하고, 가능한 대응 방향(협력업체 긴급
  요청·부분 출하·대체 조달 등)을 제안하세요. 부족 자재가 없으면 "계획대로 진행 가능"이라고
  명시하세요.
- 담당자가 바로 검토할 수 있게 bullet 형식으로 간결하게 쓰세요.
- 숫자는 'M-01 부족 수량 440개', 'M-01 필요 1040개', '신규 출하일 2026-08-28'처럼
  대상과 항목을 명시하세요. 상대 날짜·한글 수사·번호 목록 대신 절대 날짜·숫자와 - 목록을 쓰세요.
"""

_SUPPLIER_SYSTEM_PROMPT = """당신은 협력업체에 자재 입고를 앞당겨달라고 요청하는 공문을 쓰는
어시스턴트입니다. 아래 '출하 변경 사실'만 근거로 한국어 요청문을 작성하세요.

규칙:
- 사실 블록에 있는 부족 자재코드·부족 수량·신규 출하예정일만 언급하세요. 지어내지 마세요.
- 부족 자재가 없으면 "현재 요청할 조정 사항 없음"이라고만 답하세요(요청문을 만들지 마세요).
- 정중한 비즈니스 문서 톤으로, 무엇이·언제까지·얼마나 필요한지 명확히 쓰세요.
- 수량은 자재코드와 '부족 수량/추가' 항목을, 날짜는 '신규 출하일 YYYY-MM-DD'를 명시하세요.
"""

_CUSTOMER_SYSTEM_PROMPT = """당신은 고객사의 출하일 변경 요청에 회신하는 담당자를 돕는
어시스턴트입니다. 아래 '출하 변경 사실'만 근거로 한국어 회신 초안을 작성하세요.

규칙:
- 사실 블록에 없는 수량·날짜·자재코드를 지어내지 마세요.
- 부족 자재가 없으면 요청을 수용하는 회신을, 부족 자재가 있으면 현실적으로 가능한 대안(부분
  출하·조정된 날짜 등)을 제안하는 회신을 쓰세요.
- 협력업체명·내부 자재코드 등 사내 정보는 고객사에 노출하지 말고 일정 관점으로만 설명하세요.
- 격식 있는 비즈니스 서신 톤으로 쓰세요.
- 날짜는 '신규 출하일 YYYY-MM-DD', 출하수량은 '출하수량 N개'처럼 대상 항목을 명시하세요.
"""


def render_impact_facts(impact: CascadeImpact) -> str:
    """`CascadeImpact` → 프롬프트에 넣을 사실 블록 (순수).

    이 블록 밖의 숫자는 전부 모델이 지어낸 것이다 — 세 SYSTEM_PROMPT가 공통으로 강제하는 경계.
    """
    s = impact.shipment
    days = impact.days_pulled_forward
    direction = "앞당김" if days > 0 else "연기" if days < 0 else "변경 없음"
    lines = [
        "## 출하 변경 사실",
        f"- 고객사: {s.customer}",
        f"- 품목: {s.product}",
        f"- 기존 출하예정일: {s.ship_date} → 신규 출하예정일: {impact.new_ship_date} "
        f"({abs(days)}일 {direction})",
        f"- 출하수량: {s.qty}개",
        f"- 남은 생산 필요량(미완성분): {impact.qty_remaining_to_produce}개",
        "",
        "## 자재 확보 현황",
    ]
    if not impact.shortfalls:
        lines.append("- 부족한 자재 없음 — 신규 출하예정일까지 필요 자재 전량 확보 가능")
    for sf in impact.shortfalls:
        lines.append(
            f"- [{sf.material}] 필요 {sf.qty_needed}개 / 신규 출하일까지 확보 "
            f"{sf.qty_available_by_new_date}개 / 부족 {sf.shortfall}개"
        )
        for late in sf.late_arrivals:
            lines.append(
                f"    · 지연 입고: {late.supplier} {late.due_date} 예정 {late.qty}개 "
                f"(신규 출하일 이후 도착)"
            )
    return "\n".join(lines)


def _draft(system_prompt: str, impact: CascadeImpact, *,
          model: str, gateway: LLMGateway | None) -> str:
    """세 초안 함수가 공유하는 호출부 — system_prompt만 다르다."""
    gw = gateway if gateway is not None else default_gemini()
    if not gw.available:
        log.warning("LLM 게이트웨이 미가용 — 초안 생성 생략")
        return UNAVAILABLE
    return gw.complete(system_prompt, render_impact_facts(impact),
                       model=model, temperature=_TEMPERATURE, json_mode=False).strip()


def draft_adjustment_plan(impact: CascadeImpact, *, model: str = _DEFAULT_MODEL,
                          gateway: LLMGateway | None = None) -> str:
    """연쇄 영향 → 내부 검토용 일정 조정안."""
    return _draft(_ADJUSTMENT_SYSTEM_PROMPT, impact, model=model, gateway=gateway)


def draft_supplier_request(impact: CascadeImpact, *, model: str = _DEFAULT_MODEL,
                           gateway: LLMGateway | None = None) -> str:
    """연쇄 영향 → 협력업체 입고 조정 요청 초안."""
    return _draft(_SUPPLIER_SYSTEM_PROMPT, impact, model=model, gateway=gateway)


def draft_customer_reply(impact: CascadeImpact, *, model: str = _DEFAULT_MODEL,
                         gateway: LLMGateway | None = None) -> str:
    """연쇄 영향 → 고객사 회신 초안."""
    return _draft(_CUSTOMER_SYSTEM_PROMPT, impact, model=model, gateway=gateway)


__all__ = [
    "UNAVAILABLE", "render_impact_facts",
    "draft_adjustment_plan", "draft_supplier_request", "draft_customer_reply",
]

