"""유료 호출 없이 검수 자료와 실제 실행 준비의 경계·계측 정확성을 검증한다."""
import csv
import io
import json
from urllib.error import HTTPError, URLError

import pytest

from evals import live_manufacturing as live
from evals import review_packet

PRICING = {'model': 'verified-model', 'currency': 'USD', 'tier': 'standard',
           'verified_on': '2026-01-01', 'source_url': 'https://ai.google.dev/gemini-api/docs/pricing',
           'input_usd_per_million': 1., 'cached_input_usd_per_million': .1, 'output_usd_per_million': 2.}


def test_packet_lists_every_case_with_pending_decisions_and_all_drafts():
    md, decisions = review_packet.render_packet()
    rows = list(csv.DictReader(io.StringIO(decisions)))
    assert len(rows) == len({r['id'] for r in rows}) == 42
    assert all(f'## {r["id"]} — ' in md for r in rows)
    assert md.count('**내부 조정안**') == md.count('**고객 회신**') == 38
    assert '[빈 문자열]' in md and '[비문자열: null]' in md
    for row in rows:
        assert row['rationale'] and row['expected_label']
        assert all(row[k] == '' for k in ('review_status', 'proposed_label', 'reviewer_reason', 'reviewer', 'reviewed_on'))
    folder = live.ROOT / 'docs/eval-review'
    assert (folder / 'manufacturing-v1-review.md').read_text(encoding='utf-8') == md
    assert (folder / 'manufacturing-v1-decisions.csv').read_text(encoding='utf-8-sig') == decisions


def test_export_never_overwrites_user_review(tmp_path):
    review_packet.main(['--out-dir', str(tmp_path)])
    target = tmp_path / 'manufacturing-v1-decisions.csv'
    target.write_text('user answers', encoding='utf-8')
    with pytest.raises(SystemExit):
        review_packet.main(['--out-dir', str(tmp_path)])
    assert target.read_text(encoding='utf-8') == 'user answers'


def response():
    data = live.simulated_generate(None, None)
    data['usageMetadata'] = {'promptTokenCount': 100, 'candidatesTokenCount': 20,
                             'thoughtsTokenCount': 30, 'cachedContentTokenCount': 40, 'totalTokenCount': 150}
    return data


def test_thinking_and_cached_tokens_are_priced_separately():
    result = live.usage_cost(response(), PRICING)
    assert result['estimated_cost_usd'] == pytest.approx(.000164)
    assert result['billable_output_tokens'] == 50
    assert result['provider_usage']['thoughtsTokenCount'] == 30
    assert result['assumptions'] == []


@pytest.mark.parametrize('mutation', ['missing_usage', 'bad_total', 'negative', 'cached_too_large', 'tool_usage', 'priority'])
def test_incomplete_or_inconsistent_usage_never_becomes_zero_cost(mutation):
    data = response()
    usage = data['usageMetadata']
    if mutation == 'missing_usage':
        data.pop('usageMetadata')
    elif mutation == 'bad_total':
        usage['totalTokenCount'] = 149
    elif mutation == 'negative':
        usage['promptTokenCount'] = -1
    elif mutation == 'cached_too_large':
        usage['cachedContentTokenCount'] = 101
    elif mutation == 'tool_usage':
        usage['toolUsePromptTokenCount'] = 1
    else:
        usage['serviceTier'] = 'priority'
    assert live.usage_cost(data, PRICING)['estimated_cost_usd'] is None


def test_omitted_thinking_is_derived_and_disclosed_and_unpriced_remains_unknown():
    data = response()
    del data['usageMetadata']['thoughtsTokenCount']
    result = live.usage_cost(data, PRICING)
    assert result['estimated_cost_usd'] == pytest.approx(.000164)
    assert result['assumptions']
    assert live.usage_cost(data, None)['estimated_cost_usd'] is None


def test_plan_does_not_call_transport_or_store_environment_key(tmp_path, monkeypatch):
    secret = 'FAKE-SECRET-DO-NOT-PERSIST'
    monkeypatch.setenv('GEMINI_API_KEY', secret)
    monkeypatch.setattr(live, 'rest_generate', lambda *a, **kw: pytest.fail('network in plan mode'))
    output = tmp_path / 'plan'
    assert live.main(['--out-dir', str(output)]) == 0
    text = (output / 'manifest.json').read_text(encoding='utf-8')
    assert secret not in text
    plan = json.loads(text)
    assert plan['mode'] == 'plan' and plan['maximum_generation_calls'] == 3
    assert not plan['publication_enabled']
    assert not (output / 'calls.jsonl').exists()


def test_simulation_runs_pipeline_and_marks_synthetic_usage(tmp_path):
    output = tmp_path / 'simulation'
    assert live.main(['--simulate', '--repeats', '2', '--out-dir', str(output)]) == 0
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert report['manifest']['mode'] == 'simulation'
    assert report['summary']['calls_attempted'] == 6
    assert report['summary']['attempts_completed'] == 2
    assert report['summary']['total_estimated_cost_usd'] is None
    assert all(c['model_version'] == 'SIMULATED-NO-MODEL' for c in report['calls'])
    assert [c['stage'] for c in report['calls'][:3]] == list(live.STAGES)
    assert not list(output.glob('*.md'))


def test_failed_live_call_preserves_partial_cost_without_retry_or_secret(tmp_path, monkeypatch, capsys):
    key = 'FAKE-PRIVATE-KEY'
    monkeypatch.setenv('GEMINI_API_KEY', key)
    count = 0
    def fake(model, payload, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise HTTPError('https://example.invalid', 429, key, None, None)
        return response()
    monkeypatch.setattr(live, 'rest_generate', fake)
    prices = tmp_path / 'prices.json'
    prices.write_text(json.dumps(PRICING), encoding='utf-8')
    output = tmp_path / 'live'
    assert live.main(['--live', '--model', 'verified-model', '--repeats', '3',
                      '--pricing', str(prices), '--out-dir', str(output)]) == 3
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert count == 2
    assert report['attempts'][0]['error_kind'] == 'rate_limit'
    assert report['attempts'][0]['error_stage'] == 'supplier_request'
    assert report['summary']['known_cost_subtotal_usd'] == pytest.approx(.000164)
    assert report['summary']['total_estimated_cost_usd'] is None
    assert len((output / 'calls.jsonl').read_text(encoding='utf-8').splitlines()) == 2
    assert key not in capsys.readouterr().out
    assert all(key not in p.read_text(encoding='utf-8') for p in output.iterdir())


def test_truncated_response_stops_and_keeps_reported_usage(tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'fake')
    def fake(*args, **kwargs):
        data = response()
        data['candidates'][0]['finishReason'] = 'MAX_TOKENS'
        return data
    monkeypatch.setattr(live, 'rest_generate', fake)
    output = tmp_path / 'cut'
    assert live.main(['--live', '--model', 'verified-model', '--out-dir', str(output)]) == 3
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert len(report['calls']) == 1
    assert report['calls'][0]['finish_reason'] == 'MAX_TOKENS'
    assert report['attempts'][0]['error_kind'] == 'invalid_response'


def test_transport_uses_header_key_and_no_redirect_with_explicit_request_limits(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return b'{}'
    class Opener:
        def open(self, req, *, timeout):
            assert req.full_url == live.ENDPOINT.format(model='test-model')
            assert 'fake-key' not in req.full_url
            assert req.get_header('X-goog-api-key') == 'fake-key'
            assert timeout == 2.5
            assert json.loads(req.data) == {'test': 'value'}
            return Response()
    def factory(handler):
        assert isinstance(handler, live._NoRedirect)
        return Opener()
    monkeypatch.setattr(live, 'build_opener', factory)
    assert live.rest_generate('test-model', {'test': 'value'}, api_key='fake-key', timeout=2.5) == {}
    assert live._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.invalid') is None


@pytest.mark.parametrize('cause,expected', [(TimeoutError(), TimeoutError), (OSError(), ConnectionError)])
def test_url_errors_are_sanitized(monkeypatch, cause, expected):
    class Opener:
        def open(self, *args, **kwargs): raise URLError(cause)
    monkeypatch.setattr(live, 'build_opener', lambda *args: Opener())
    with pytest.raises(expected):
        live.rest_generate('test-model', {}, api_key='fake', timeout=1)


def test_existing_records_and_model_price_mismatch_fail_before_call(tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'fake')
    monkeypatch.setattr(live, 'rest_generate', lambda *a, **kw: pytest.fail('network before preflight'))
    saved = tmp_path / 'existing'
    saved.mkdir()
    (saved / 'report.json').write_text('keep', encoding='utf-8')
    assert live.main(['--live', '--model', 'verified-model', '--out-dir', str(saved)]) == 2
    assert (saved / 'report.json').read_text(encoding='utf-8') == 'keep'
    prices = tmp_path / 'pricing.json'
    prices.write_text(json.dumps(PRICING), encoding='utf-8')
    assert live.main(['--live', '--model', 'other-model', '--pricing', str(prices),
                      '--out-dir', str(tmp_path / 'new')]) == 2
    assert not (tmp_path / 'new').exists()


@pytest.mark.parametrize('field,value', [('input_usd_per_million', None), ('input_usd_per_million', -1),
                                       ('output_usd_per_million', float('inf')), ('cached_input_usd_per_million', True),
                                       ('verified_on', 'YYYY-MM-DD'), ('verified_on', None), ('tier', 'batch')])
def test_unconfirmed_or_unsupported_prices_are_rejected(tmp_path, field, value):
    data = {**PRICING, field: value}
    path = tmp_path / 'pricing.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError):
        live.load_pricing(path, 'verified-model')


@pytest.mark.parametrize('args', [['--live'], ['--live', '--simulate'], ['--model', 'auto'],
                                 ['--model', '../escape'], ['--timeout-seconds', 'nan'], ['--repeats', '4']])
def test_invalid_live_plans_are_rejected_before_execution(tmp_path, args):
    with pytest.raises(SystemExit):
        live.parse_args([*args, '--out-dir', str(tmp_path)])
