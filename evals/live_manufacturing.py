"""제조 모델 실행 준비/측정. 기본은 오프라인 계획이며 --live만 Gemini REST를 호출한다."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import statistics
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener

from adapters.manufacturing.adapter import ManufacturingAdapter
from adapters.manufacturing import draft as draft_module
from adapters.manufacturing.analyze import ShipmentChangeTrigger
from adapters.manufacturing.errors import DraftGenerationError, classify_failure
from adapters.manufacturing.erp_data import load_erp_snapshot
from evals.manufacturing import ROOT, _CODE_FILES

STAGES = ('adjustment_plan', 'supplier_request', 'customer_reply')
TRIGGER = ShipmentChangeTrigger('고객사A', 'P-100', '2026-08-28')
ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
PRICE_FIELDS = ('input_usd_per_million', 'cached_input_usd_per_million', 'output_usd_per_million')


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


def load_pricing(path: Path | None, model: str | None) -> dict | None:
    if path is None:
        return None
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('단가는 JSON 객체여야 합니다.')
    if not model or data['model'] != model or data['currency'] != 'USD' or data['tier'] != 'standard':
        raise ValueError('단가의 모델·USD·standard 조건이 실행과 일치해야 합니다.')
    if not isinstance(data['verified_on'], str) or date.fromisoformat(data['verified_on']) > date.today():
        raise ValueError('미래 날짜의 확인 기록은 사용할 수 없습니다.')
    if not isinstance(data['source_url'], str) or not data['source_url'].startswith('https://ai.google.dev/'):
        raise ValueError('공식 단가 출처 URL이 필요합니다.')
    for field in PRICE_FIELDS:
        value = data[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('단가는 0 이상 유한 숫자로 직접 확인해야 합니다.')
    return {key: data[key] for key in ('model', 'currency', 'tier', 'verified_on', 'source_url', *PRICE_FIELDS)}


def usage_cost(payload: dict, pricing: dict | None) -> dict:
    """텍스트 전용 응답의 제공자 토큰을 보존한다. 불완전한 계측은 0달러가 아니다."""
    raw = payload.get('usageMetadata') or {}
    if not isinstance(raw, dict):
        raw = {}
    def token(name):
        value = raw.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    usage = {key: token(key) for key in ('promptTokenCount', 'candidatesTokenCount', 'thoughtsTokenCount',
                                       'cachedContentTokenCount', 'totalTokenCount', 'toolUsePromptTokenCount')}
    p, c, total = (usage[key] for key in ('promptTokenCount', 'candidatesTokenCount', 'totalTokenCount'))
    result = {'provider_usage': usage, 'estimated_cost_usd': None, 'cost_status': 'usage_incomplete',
              'provider_service_tier': raw.get('serviceTier'),
              'assumptions': [], 'billable_output_tokens': None}
    if raw.get('serviceTier') not in (None, 'unspecified', 'standard'):
        result['cost_status'] = 'unsupported_service_tier'
        return result
    if p is None or c is None or total is None:
        return result
    if 'toolUsePromptTokenCount' in raw and usage['toolUsePromptTokenCount'] is None:
        return result
    cached = usage['cachedContentTokenCount']
    if cached is None:
        if 'cachedContentTokenCount' in raw:
            return result
        cached = 0
        result['assumptions'].append('omitted cachedContentTokenCount treated as zero')
    thoughts = usage['thoughtsTokenCount']
    if thoughts is None:
        if 'thoughtsTokenCount' in raw:
            return result
        thoughts = total - p - c
        result['assumptions'].append('thinking tokens derived from total minus prompt minus candidates')
    if cached > p or thoughts < 0 or total != p + c + thoughts or (usage['toolUsePromptTokenCount'] or 0):
        result['cost_status'] = 'usage_inconsistent_or_unsupported'
        return result
    result['billable_output_tokens'] = c + thoughts
    if pricing is None:
        result['cost_status'] = 'pricing_not_supplied'
        return result
    result['estimated_cost_usd'] = ((p - cached) * pricing['input_usd_per_million']
                                    + cached * pricing['cached_input_usd_per_million']
                                    + (c + thoughts) * pricing['output_usd_per_million']) / 1_000_000
    result['cost_status'] = 'token_price_estimate_not_invoice'
    return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def rest_generate(model: str, payload: dict, *, api_key: str, timeout: float) -> dict:
    """고정 HTTPS 호스트, 헤더 키, 리다이렉트 없음, 자동 재시도 없음. timeout은 소켓 작업 제한이다."""
    request = Request(ENDPOINT.format(model=model), data=_json(payload).encode('utf-8'),
                      headers={'Content-Type': 'application/json', 'x-goog-api-key': api_key}, method='POST')
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(2_000_001)
    except HTTPError:
        raise
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise TimeoutError() from None
        raise ConnectionError() from None
    if len(raw) > 2_000_000:
        raise ValueError('oversize response')
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('invalid response object')
    return data


def simulated_generate(model, payload, **kwargs):
    return {'modelVersion': 'SIMULATED-NO-MODEL',
            'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '출하 일정을 검토 중입니다.'}]}}],
            'usageMetadata': {'promptTokenCount': 120, 'candidatesTokenCount': 32,
                              'thoughtsTokenCount': 8, 'cachedContentTokenCount': 0, 'totalTokenCount': 160}}


class MeasuredGateway:
    available = True

    def __init__(self, *, transport, api_key, timeout, max_output_tokens, pricing, record, clock=time.perf_counter):
        self.transport, self.api_key, self.timeout = transport, api_key, timeout
        self.max_output_tokens, self.pricing = max_output_tokens, pricing
        self.record, self.clock = record, clock
        self.rows = []

    def complete(self, system, prompt, *, model, temperature=0.3, json_mode=False):
        payload = {'systemInstruction': {'parts': [{'text': system}]},
                   'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
                   'generationConfig': {'temperature': temperature, 'maxOutputTokens': self.max_output_tokens,
                                        'candidateCount': 1}}
        row = {'stage': STAGES[len(self.rows)], 'requested_model': model, 'status': 'error',
               'started_at_utc': datetime.now(timezone.utc).isoformat(),
               'request_sha256': _hash(_json(payload).encode('utf-8')), 'error_kind': None,
               'model_version': None, 'finish_reason': None, 'text': None,
               **usage_cost({}, self.pricing)}
        started = self.clock()
        try:
            response = self.transport(model, payload, api_key=self.api_key, timeout=self.timeout)
            row.update(usage_cost(response, self.pricing))
            row['model_version'] = response.get('modelVersion')
            candidates = response.get('candidates') or []
            candidate = candidates[0] if candidates else {}
            row['finish_reason'] = candidate.get('finishReason')
            parts = candidate.get('content', {}).get('parts', [])
            text = ''.join(p['text'] for p in parts if isinstance(p.get('text'), str) and not p.get('thought'))
            row['text'] = text
            if row['finish_reason'] != 'STOP' or not text.strip():
                raise ValueError('incomplete or empty response')
            row['status'] = 'success'
            return text
        except Exception as exc:
            row['error_kind'] = classify_failure(exc)
            raise
        finally:
            row['elapsed_seconds'] = max(0, self.clock() - started)
            self.rows.append(row)
            self.record(row)


def latency(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {'n': 0, 'min': None, 'p50': None, 'p95': None, 'max': None}
    return {'n': len(ordered), 'min': ordered[0], 'p50': statistics.median(ordered),
            'p95': ordered[math.ceil(.95 * len(ordered)) - 1], 'max': ordered[-1]}


def manifest(args, pricing) -> dict:
    sources = {p: _hash((ROOT / p).read_text(encoding='utf-8').encode('utf-8'))
               for p in (*_CODE_FILES, 'evals/live_manufacturing.py')}
    inputs = {p.name: _hash(p.read_bytes()) for p in sorted((ROOT / 'data/manufacturing').glob('*.csv'))}
    return {'schema_version': 1, 'mode': 'live' if args.live else 'simulation' if args.simulate else 'plan',
            'created_at_utc': datetime.now(timezone.utc).isoformat(), 'python': platform.python_version(),
            'platform': platform.platform(), 'transport': 'stdlib urllib / Gemini Developer API v1beta REST',
            'sdk': None, 'model': args.model, 'trigger': asdict(TRIGGER), 'repeats': args.repeats,
            'maximum_generation_calls': args.repeats * 3, 'max_output_tokens_per_call': args.max_output_tokens,
            'socket_timeout_seconds': args.timeout_seconds, 'automatic_retries': 0, 'temperature': 0.3,
            'publication_enabled': False, 'pricing': pricing,
            'cost_note': 'token-price estimate only; failed/unreported usage may still be billed; not a USD cap',
            'source_sha256': sources, 'input_sha256': inputs,
            'prompts_sha256': {stage: _hash(getattr(draft_module, name).encode('utf-8'))
                               for stage, name in zip(STAGES, ('_ADJUSTMENT_SYSTEM_PROMPT', '_SUPPLIER_SYSTEM_PROMPT', '_CUSTOMER_SYSTEM_PROMPT'))}}


def measure(args, plan, *, transport, api_key, out_dir: Path) -> dict:
    attempts, calls = [], []
    def record(row):
        value = {**row, 'attempt': len(attempts) + 1}
        calls.append(value)
        with (out_dir / 'calls.jsonl').open('a', encoding='utf-8') as file:
            file.write(json.dumps(value, ensure_ascii=False) + '\n')
    for _ in range(args.repeats):
        gateway = MeasuredGateway(transport=transport, api_key=api_key, timeout=args.timeout_seconds,
                                  max_output_tokens=args.max_output_tokens, pricing=plan['pricing'], record=record)
        adapter = ManufacturingAdapter(load_erp_snapshot(ROOT / 'data/manufacturing'), model=args.model,
                                       gateway=gateway)
        started = time.perf_counter()
        attempt = {'status': 'error', 'verdict': None, 'violations': [], 'error_stage': None, 'error_kind': None}
        try:
            produced = adapter.produce(TRIGGER)
            verdict = adapter.review(produced)
            attempt.update(status='complete', verdict=verdict.recommendation,
                           violations=[asdict(v) for v in verdict.violations])
        except DraftGenerationError as exc:
            attempt.update(error_stage=exc.stage, error_kind=exc.kind)
        attempt['elapsed_seconds'] = time.perf_counter() - started
        attempts.append(attempt)
        if attempt['status'] != 'complete':
            break  # 재시도/후속 반복 없음. 이미 완료된 호출은 로그에 남는다.
    known = [c['estimated_cost_usd'] for c in calls if c['estimated_cost_usd'] is not None]
    report = {'manifest': plan, 'attempts': attempts, 'calls': calls,
              'summary': {'attempts_completed': sum(a['status'] == 'complete' for a in attempts),
                          'calls_attempted': len(calls), 'call_failures': sum(c['status'] != 'success' for c in calls),
                          'call_latency_seconds_all_attempts': latency([c['elapsed_seconds'] for c in calls]),
                          'pipeline_latency_seconds_all_attempts': latency([a['elapsed_seconds'] for a in attempts]),
                          'priced_calls': len(known), 'unpriced_calls': len(calls) - len(known),
                          'known_cost_subtotal_usd': sum(known) if known else None,
                          'total_estimated_cost_usd': sum(known) if len(known) == len(calls) and known else None}}
    (out_dir / 'report.json').write_text(_json(report), encoding='utf-8')
    return report


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--live', action='store_true')
    mode.add_argument('--simulate', action='store_true')
    parser.add_argument('--model', help='실행 직전 이용 권한·가용성을 확인한 Gemini 모델 ID')
    parser.add_argument('--repeats', type=int, choices=range(1, 4), default=1)
    parser.add_argument('--timeout-seconds', type=float, default=30.)
    parser.add_argument('--max-output-tokens', type=int, default=2048)
    parser.add_argument('--pricing', type=Path)
    parser.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        parser.error('timeout은 양수인 유한 숫자여야 합니다.')
    if not 1 <= args.max_output_tokens <= 8192:
        parser.error('max-output-tokens 범위는 1~8192입니다.')
    if args.model and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', args.model):
        parser.error('모델 ID만 입력하세요. models/ 접두사·URL·auto는 사용하지 않습니다.')
    if args.model == 'auto' or (args.live and not args.model):
        parser.error('--live에는 구체적인 --model이 필요합니다.')
    if args.simulate:
        args.model = 'SIMULATED-NO-MODEL'
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        pricing = load_pricing(args.pricing, args.model)
        plan = manifest(args, pricing)
        api_key = os.environ.get('GEMINI_API_KEY', '') if args.live else ''
        if args.live and not api_key:
            raise ValueError('GEMINI_API_KEY 환경변수가 필요합니다.')
        args.out_dir.mkdir(parents=True, exist_ok=False)  # 기존 기록을 덮어쓰거나 재실행하지 않는다.
        (args.out_dir / 'manifest.json').write_text(_json(plan), encoding='utf-8')
        if not args.live and not args.simulate:
            print(f'오프라인 실행 계획 저장: {args.out_dir}. 모델 호출 없음.')
            return 0
        report = measure(args, plan, transport=rest_generate if args.live else simulated_generate,
                         api_key=api_key, out_dir=args.out_dir)
        print(f'{plan["mode"]} 측정 기록 저장: {args.out_dir}. 확정 문서 발행 없음.')
        return 0 if all(a['status'] == 'complete' for a in report['attempts']) else 3
    except (ValueError, KeyError, OSError):
        print('준비/기록 실패: 입력·단가·키 존재 여부·새 출력 경로를 확인하세요. 예외 원문은 출력하지 않습니다.')
        return 2


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
