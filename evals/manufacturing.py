"""고정된 합성 사례로 제조 검증·발행 정책을 평가한다. 실제 LLM 정확도 평가는 아니다."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from adapters.manufacturing.adapter import ManufacturingAdapter
from adapters.manufacturing.analyze import CascadeImpact, MaterialShortfall, ShipmentChangeTrigger
from adapters.manufacturing.errors import DraftGenerationError
from adapters.manufacturing.erp_data import InboundLine, ShipmentLine, load_erp_snapshot
from adapters.manufacturing.publish import ManufacturingPublisher

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / 'evals/fixtures/manufacturing-v1.json'
LABELS = ('approve', 'review', 'reject', 'error')
_CODE_FILES = (
    'evals/manufacturing.py', 'adapters/manufacturing/facts.py', 'adapters/manufacturing/review.py',
    'adapters/manufacturing/publish.py', 'adapters/manufacturing/draft.py',
    'adapters/manufacturing/errors.py', 'adapters/manufacturing/adapter.py',
    'adapters/manufacturing/analyze.py', 'adapters/manufacturing/erp_data.py',
    'core/approval.py', 'core/pipeline.py', 'core/llm.py', 'core/gateway.py', 'core/routing.py')


def load_dataset(path: Path = DATASET) -> tuple[dict, str]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != path.with_suffix('.sha256').read_text(encoding='ascii').strip():
        raise ValueError('평가셋 해시 불일치: 원본을 복원하거나 새 버전을 만드세요.')
    data = json.loads(raw)
    if data['schema_version'] != 1:
        raise ValueError('지원하지 않는 평가셋 버전')
    ids = set()
    for case in data['cases']:
        if (case['id'] in ids or case['split'] not in ('dev', 'holdout')
            or case['expected'] not in LABELS or not case['rationale'].strip()
            or case['scenario'] not in data['scenarios']):
            raise ValueError('평가 사례의 ID·분할·정답·근거·시나리오를 확인하세요.')
        ids.add(case['id'])
    return data, digest


def _impact(spec: dict) -> CascadeImpact:
    return CascadeImpact(
        shipment=ShipmentLine(**spec['shipment']), new_ship_date=spec['new_ship_date'],
        days_pulled_forward=spec['days_pulled_forward'],
        qty_remaining_to_produce=spec['qty_remaining_to_produce'],
        shortfalls=[MaterialShortfall(**{**sf, 'late_arrivals': [InboundLine(**v) for v in sf['late_arrivals']]})
                    for sf in spec['shortfalls']])


class _MemorySink:
    def __init__(self):
        self.delivered = 0

    def deliver(self, doc, meta):
        self.delivered += 1
        return 'memory://evaluation'


class _FaultGateway:
    available = True

    def __init__(self, fault):
        self.fault = fault
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        if self.calls != self.fault['at']:
            return '출하 일정을 검토 중입니다.'
        kind = self.fault['kind']
        if kind == 'timeout':
            raise TimeoutError('synthetic timeout')
        if kind == 'dependency_missing':
            raise ImportError('synthetic dependency failure')
        error = RuntimeError('synthetic provider failure')
        error.status_code = {'authentication': 401, 'rate_limit': 429}[kind]
        raise error


def _case(data: dict, case: dict) -> dict:
    sink = _MemorySink()
    publisher = ManufacturingPublisher(sink, clock=lambda: '2026-10-23T00:00:00')
    content = {'impact': _impact(data['scenarios'][case['scenario']]),
               **data['base_drafts'], **case.get('drafts', {})}
    error_kind = None
    error_stage = None
    try:
        if 'fault' in case:
            # 오류 주입도 실제 produce 경계를 통과한다. 네트워크는 사용하지 않는다.
            adapter = ManufacturingAdapter(load_erp_snapshot(ROOT / 'data/manufacturing'),
                                           gateway=_FaultGateway(case['fault']), publisher=publisher)
            content = adapter.produce(ShipmentChangeTrigger('고객사A', 'P-100', '2026-08-28'))
        authorization = publisher.authorize(content, ask=lambda decision: False)
        actual = authorization.decision.verdict.recommendation
        categories = sorted({v.category for v in authorization.decision.verdict.violations})
        if authorization.outcome.publish:
            publisher.publish(content, permit=authorization.permit)
    except DraftGenerationError as exc:
        actual, categories, error_kind = 'error', [], exc.kind
        error_stage = exc.stage
    error_matches = error_kind == case.get('expected_error') and error_stage == case.get('expected_stage')
    return {'id': case['id'], 'split': case['split'], 'category': case['category'],
            'expected': case['expected'], 'actual': actual,
            'match': actual == case['expected'] and error_matches,
            'violation_categories': categories, 'error_kind': error_kind, 'error_stage': error_stage,
            'auto_published': bool(sink.delivered),
            'rationale': case['rationale']}


def _metrics(rows: list[dict]) -> dict:
    matrix = {expected: {actual: 0 for actual in LABELS} for expected in LABELS}
    for row in rows:
        matrix[row['expected']][row['actual']] += 1
    normal = [r for r in rows if r['expected'] == 'approve']
    risks = [r for r in rows if r['expected'] in ('reject', 'review')]
    complete = [r for r in rows if r['actual'] != 'error']
    matches = sum(r['match'] for r in rows)
    false_blocked = sum(not r['auto_published'] for r in normal)
    return {
        'cases': len(rows), 'exact_matches': matches,
        'exact_match_rate': matches / len(rows) if rows else None,
        'normal_cases': len(normal), 'normal_false_blocks': false_blocked,
        'normal_false_block_rate': false_blocked / len(normal) if normal else None,
        'risk_cases': len(risks),
        'unsafe_auto_publishes': sum(r['auto_published'] for r in rows if r['expected'] != 'approve'),
        'risk_auto_block_rate': sum(not r['auto_published'] for r in risks) / len(risks) if risks else None,
        'human_review_cases': sum(r['actual'] == 'review' for r in rows),
        'generation_errors': sum(r['actual'] == 'error' for r in rows),
        'confusion_matrix': matrix,
        'categories': dict(sorted(Counter(r['category'] for r in rows).items())),
        'without_gate_baseline': {
            'completed_drafts': len(complete),
            'published': len(complete),
            'unsafe_published': sum(r['expected'] != 'approve' for r in complete),
            'policy': 'All completed synthetic drafts published; generation failures excluded.'}}


def evaluate(*, split: str = 'all', path: Path = DATASET) -> dict:
    data, digest = load_dataset(path)
    rows = [_case(data, c) for c in data['cases'] if split == 'all' or c['split'] == split]
    if not rows:
        raise ValueError('평가할 사례가 없습니다.')
    code = hashlib.sha256()
    for relative in _CODE_FILES:
        code.update(relative.encode('utf-8') + b'\0')
        code.update((ROOT / relative).read_text(encoding='utf-8').encode('utf-8'))
    return {'dataset_version': data['dataset_version'], 'dataset_sha256': digest,
            'code_sha256': code.hexdigest(), 'mode': 'offline synthetic fixtures, no model calls',
            'split': split, 'metrics': _metrics(rows),
            'splits': {s: _metrics([r for r in rows if r['split'] == s])
                       for s in ('dev', 'holdout') if any(r['split'] == s for r in rows)},
            'results': rows}


def markdown(report: dict) -> str:
    m = report['metrics']
    lines = ['# 제조 고정 평가 v1', '',
             '외부 모델을 호출하지 않은 수작업 합성 사례의 정책 평가다. 실제 LLM 정확도·비용·지연 지표가 아니다.', '',
             f"- 데이터: `{report['dataset_version']}` / `{report['dataset_sha256']}`",
             f"- 코드: `{report['code_sha256']}`", '',
             '| 분할 | 사례 | 판정·오류 일치 | 정상 오차단 | 위험 자동 발행 | 생성 실패 |',
             '|---|---:|---:|---:|---:|---:|']
    for name, values in [('all', m), *report['splits'].items()]:
        lines.append(f"| {name} | {values['cases']} | {values['exact_matches']} | {values['normal_false_blocks']} | {values['unsafe_auto_publishes']} | {values['generation_errors']} |")
    baseline = m['without_gate_baseline']
    lines += ['', f"무검증 비교 기준은 완료된 초안 {baseline['completed_drafts']}건을 모두 발행하는 정책이다. "
              f"그중 기대상 자동 발행하면 안 되는 사례는 {baseline['unsafe_published']}건이다. "
              '실제 이전 버전이나 운영 트래픽의 실측값은 아니다.', '',
              '| ID | 분할 | 기대 | 실제 | 자동 발행 |', '|---|---|---|---|---|']
    lines += [f"| {r['id']} | {r['split']} | {r['expected']} | {r['actual']} | {r['auto_published']} |"
              for r in report['results']]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=['all', 'dev', 'holdout'], default='all')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--markdown', type=Path)
    args = parser.parse_args(argv)
    report = evaluate(split=args.split)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    for path, text in ((args.output, rendered), (args.markdown, markdown(report))):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
    if not args.output:
        print(rendered)
    m = report['metrics']
    return int(m['exact_matches'] != m['cases'] or m['unsafe_auto_publishes'] != 0)


if __name__ == '__main__':
    raise SystemExit(main())
