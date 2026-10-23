"""고정 평가 입력을 사람이 검수할 문서와 빈 응답표로 내보낸다. 정답은 바꾸지 않는다."""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path

from evals.manufacturing import load_dataset

TITLES = {'adjustment_plan': '내부 조정안', 'supplier_request': '협력업체 요청', 'customer_reply': '고객 회신'}
QUESTIONS = {
    'normal': '명시한 항목이 근거와 일치하는가? 문서 완성도가 아닌 현재 규칙상 발행 허용이라는 판정에 동의하는가?',
    'quantity': '같은 숫자가 다른 항목에 존재해도 대상·항목이 다르면 불일치로 볼 수 있는가?',
    'date': '기존/신규 출하일·입고일의 역할과 연도·달력 유효성을 대조했는가?',
    'unverified': '틀렸다고 단정할 근거와 자동 확인할 근거를 구분했는가? 사람이 확인할 때까지 보류하는가?',
    'information_leak': '현재 정책은 내부 정보 노출을 고위험 거절이 아닌 사람 검토로 분류한다. 이 정책에 동의하는가?',
    'missing': '필수 초안이 없으므로 사람 승인 요청 없이 생성 결과를 거절하는가?',
    'unknown_code': '근거 범위에 없는 코드의 사용을 거절하는가?',
    'generation_failure': '오류 종류·실패 단계가 맞고, 앞선 초안이 완성돼도 전체 발행을 중단하는가?',
}


def render_packet() -> tuple[str, str]:
    data, digest = load_dataset()
    lines = ['# 제조 평가셋 v1 — 사람 검수 자료', '',
             f'입력 버전: `{data["dataset_version"]}` / SHA-256: `{digest}`', '',
             '**42건 모두 미검수 상태다. 아래 기대 판정은 검토할 제안이며 사용자 확인 결과가 아니다.**', '',
             '자동 판정 결과는 넣지 않았다. 근거 사실과 초안부터 읽고 기대 판정·정책에 동의하는지 확인한다.',
             '이 자료는 정답을 보여주는 검수 자료이므로 블라인드 평가에 재사용하지 않는다.', '',
             '## 작성 방법', '',
             'CSV의 `review_status`를 `agree / change / unsure` 중 하나로 채운다. 변경할 때는',
             '`proposed_label`에 `approve / review / reject / error`, `reviewer_reason`에 이유를 쓴다.',
             '오류 단계·종류 수정은 `proposed_error_kind`, `proposed_error_stage`에 적는다.',
             '`reviewer`, `reviewed_on`도 직접 작성한다. 빈 칸은 동의로 간주하지 않는다.',
             'v1 입력은 그대로 보관하고 이견을 검토한 뒤 v2를 만든다.', '',
             '## 판정 기준', '',
             '- `approve`: 현재 명시적 규칙에서 발행을 막을 근거가 없음. 업무 문서의 완성도나 모든 사실의 정확성을 보장하지 않음.',
             '- `review`: 표현을 자동 해석하기 어렵거나 현재 정책상 내부 정보 노출을 사람이 확인해야 함. 사람 승인 전 자동 발행 없음.',
             '- `reject`: 명시적 수량·날짜 불일치, 없는 코드 또는 필수 초안 누락. 사람 승인으로 우회하지 않음.',
             '- `error`: 생성 중 API/의존성 오류. review 판정 이전에 중단하고 발행 없음.', '',
             '특히 숫자가 없고 내용이 짧은 N07도 규칙상 approve다. 이를 업무상 불충분하다고 보면',
             '규칙 판정 라벨과 문서 완성도 라벨을 분리해야 한다는 의견을 남길 수 있다.', '',
             '## 근거 사실', '',
             '기본 사례는 포함된 가상 CSV의 출하 변경이다. 출하 500개 − 생산 완료 240개 = 남은 생산 260개,',
             'M-01 BOM 4개 × 260개 = 필요 1,040개, 신규 출하일까지 확보 600개 → 부족 440개다.',
             '`two-materials`는 교차 혼동 검사용 합성 근거이며 CSV에서 계산한 추가 자재가 아니다.', '']
    for name, spec in data['scenarios'].items():
        s = spec['shipment']
        lines += [f'### {name}', '',
                  f'- 고객/제품: {s["customer"]} / {s["product"]}; 출하수량 {s["qty"]}개.',
                  f'- 기존 출하 {s["ship_date"]} → 신규 출하 {spec["new_ship_date"]}; {spec["days_pulled_forward"]}일 앞당김.',
                  f'- 남은 생산 {spec["qty_remaining_to_produce"]}개.', '',
                  '| 자재 | 필요 | 신규 출하일까지 확보 | 부족 | 늦은 입고 |', '|---|---:|---:|---:|---|']
        for sf in spec['shortfalls']:
            late = '; '.join(f'{v["supplier"]} {v["due_date"]} {v["qty"]}개' for v in sf['late_arrivals']) or '없음'
            lines.append(f'| {sf["material"]} | {sf["qty_needed"]} | {sf["qty_available_by_new_date"]} | {sf["shortfall"]} | {late} |')
        lines += ['']
    columns = ['id', 'split', 'scenario', 'category', 'expected_label', 'expected_error_kind', 'expected_error_stage',
               'rationale', 'review_status', 'proposed_label', 'proposed_error_kind', 'proposed_error_stage',
               'reviewer_reason', 'reviewer', 'reviewed_on']
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator='\n')
    writer.writeheader()
    for case in data['cases']:
        lines += [f'## {case["id"]} — {case["category"]} / {case["split"]}', '',
                  f'근거 시나리오: `{case["scenario"]}`. 기대 판정 제안: **{case["expected"]}**.', '']
        if 'fault' in case:
            lines += [f'주입: {case["fault"]["at"]}번째 호출에서 `{case["fault"]["kind"]}`.',
                      f'기대 오류: `{case["expected_error"]}` / 단계: `{case["expected_stage"]}`.',
                      '이 사례는 고정 초안을 검증하는 대신 실제 produce에 오류를 주입한다.',
                      '실패 전 성공 호출은 “출하 일정을 검토 중입니다.”를 반환한다.', '']
        else:
            drafts = {**data['base_drafts'], **case.get('drafts', {})}
            for key, title in TITLES.items():
                lines += [f'**{title}**', '', '```text',
                          drafts[key] if isinstance(drafts[key], str) and drafts[key] else
                          ('[빈 문자열]' if drafts[key] == '' else f'[비문자열: {json.dumps(drafts[key])}]'), '```', '']
        lines += [f'판단 근거 제안: {case["rationale"]}.', '',
                  f'검수 질문: {QUESTIONS[case["category"]]}', '',
                  '사용자 확인: 미검수. 동의/수정/보류와 이유를 CSV의 같은 ID 행에 작성.', '']
        writer.writerow({'id': case['id'], 'split': case['split'], 'scenario': case['scenario'],
                         'category': case['category'], 'expected_label': case['expected'],
                         'expected_error_kind': case.get('expected_error', ''),
                         'expected_error_stage': case.get('expected_stage', ''), 'rationale': case['rationale']})
    return '\n'.join(lines), buffer.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    if any((args.out_dir / name).exists() for name in ('manufacturing-v1-review.md', 'manufacturing-v1-decisions.csv')):
        parser.error('검수 응답을 보존하기 위해 기존 자료를 덮어쓰지 않습니다. 새 경로를 사용하세요.')
    args.out_dir.mkdir(parents=True, exist_ok=True)
    md, csv_text = render_packet()
    (args.out_dir / 'manufacturing-v1-review.md').write_text(md, encoding='utf-8')
    (args.out_dir / 'manufacturing-v1-decisions.csv').write_text(csv_text, encoding='utf-8-sig', newline='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
