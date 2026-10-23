"""SDK·네트워크 없이 실패 종류, 부분 성공 중단, 민감한 예외 원문 제외를 검증한다."""
from __future__ import annotations

from pathlib import Path

import pytest

from adapters.manufacturing.adapter import ManufacturingAdapter
from adapters.manufacturing.analyze import ShipmentChangeTrigger
from adapters.manufacturing.errors import DraftGenerationError, classify_failure
from adapters.manufacturing.erp_data import load_erp_snapshot
import run_manufacturing as rm

DATA = Path(__file__).resolve().parent.parent / 'data/manufacturing'
SECRET = 'do-not-print-api-key-or-private-prompt'


class HTTPError(Exception):
    def __init__(self, status):
        self.status_code = status
        super().__init__(SECRET)


@pytest.mark.parametrize(('error', 'expected'), [
    (TimeoutError(SECRET), 'timeout'),
    (type('DeadlineExceeded', (Exception,), {})(SECRET), 'timeout'),
    (ImportError(SECRET), 'dependency_missing'),
    (HTTPError(401), 'authentication'), (HTTPError(403), 'authentication'),
    (HTTPError(429), 'rate_limit'), (HTTPError(500), 'provider_error'),
    (ConnectionError(SECRET), 'connection'), (ValueError(SECRET), 'invalid_response'),
    (RuntimeError(SECRET), 'provider_error'),
])
def test_failure_classification_does_not_need_provider_sdks(error, expected):
    assert classify_failure(error) == expected


class FailingGateway:
    available = True

    def __init__(self, fail_at=1, error=None, response='unused'):
        self.calls = 0
        self.fail_at = fail_at
        self.error = error
        self.response = response

    def complete(self, *args, **kwargs):
        self.calls += 1
        if self.calls == self.fail_at:
            if self.error is not None:
                raise self.error
            return self.response
        return '출하 일정을 검토 중입니다.'


@pytest.mark.parametrize(('fail_at', 'stage'), [(1, 'adjustment_plan'), (2, 'supplier_request'), (3, 'customer_reply')])
def test_partial_success_does_not_retry_or_generate_later_drafts(fail_at, stage):
    gateway = FailingGateway(fail_at, TimeoutError(SECRET))
    adapter = ManufacturingAdapter(load_erp_snapshot(DATA), gateway=gateway)
    with pytest.raises(DraftGenerationError) as captured:
        adapter.produce(ShipmentChangeTrigger('고객사A', 'P-100', '2026-08-28'))
    assert captured.value.stage == stage and captured.value.kind == 'timeout'
    assert SECRET not in str(captured.value)
    assert gateway.calls == fail_at


@pytest.mark.parametrize(('error', 'response', 'kind'), [
    (TimeoutError(SECRET), None, 'timeout'),
    (ImportError(SECRET), None, 'dependency_missing'),
    (HTTPError(401), None, 'authentication'), (HTTPError(429), None, 'rate_limit'),
    (HTTPError(503), None, 'provider_error'),
    (ConnectionError(SECRET), None, 'connection'),
    (None, None, 'invalid_response'), (None, {}, 'invalid_response'),
    (None, '   ', 'invalid_response'),
])
def test_cli_failure_never_asks_or_writes_and_hides_exception_text(tmp_path, capsys, caplog, error, response, kind):
    gateway = FailingGateway(2, error, response)
    args = rm._parse_args(['--out-dir', str(tmp_path)])

    def never_ask(decision):
        raise AssertionError('failed generation must not ask for publication')

    assert rm.run(args, ask=never_ask, gateway=gateway) == 3
    captured = capsys.readouterr()
    assert f'stage=supplier_request code={kind}' in captured.out
    assert '발행하지 않음' in captured.out
    assert SECRET not in captured.out + captured.err + caplog.text
    assert gateway.calls == 2 and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf'])
def test_cli_rejects_non_finite_or_nonpositive_timeout(value):
    with pytest.raises(SystemExit):
        rm._parse_args(['--timeout-seconds', value])


def test_manufacturing_default_passes_timeout_to_gateway_factory(monkeypatch):
    from adapters.manufacturing import draft
    seen = []
    gateway = FailingGateway(fail_at=99)
    monkeypatch.setattr(draft, 'default_gemini', lambda **kw: seen.append(kw) or gateway)
    adapter = ManufacturingAdapter(load_erp_snapshot(DATA), timeout_seconds=4.5)
    adapter.produce(ShipmentChangeTrigger('고객사A', 'P-100', '2026-08-28'))
    assert seen == [{'timeout_seconds': 4.5}] * 3
