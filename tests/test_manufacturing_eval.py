"""고정 정답·분할·체크섬과 실제 검증/발행 경계의 재현성을 확인한다."""
import hashlib
import json

import pytest

from evals import manufacturing as evaluation


@pytest.fixture(scope='module')
def report():
    return evaluation.evaluate()


def test_fixed_dataset_policy_results(report):
    metrics = report['metrics']
    assert metrics['cases'] == metrics['exact_matches'] == 42
    assert metrics['normal_cases'] == 10
    assert metrics['risk_cases'] == 28
    assert metrics['normal_false_blocks'] == metrics['unsafe_auto_publishes'] == 0
    assert metrics['generation_errors'] == 4
    assert metrics['human_review_cases'] == 10
    assert metrics['without_gate_baseline']['unsafe_published'] == 28
    assert {name: value['cases'] for name, value in report['splits'].items()} == {'dev': 29, 'holdout': 13}
    assert all(row['match'] for row in report['results'])


def test_saved_reports_match_current_dataset_and_code(report):
    folder = evaluation.ROOT / 'docs/eval-results'
    assert json.loads((folder / 'manufacturing-v1.json').read_text(encoding='utf-8')) == report
    assert (folder / 'manufacturing-v1.md').read_text(encoding='utf-8') == evaluation.markdown(report)


def _write_dataset(tmp_path, data):
    path = tmp_path / 'fixture.json'
    raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
    path.write_bytes(raw)
    path.with_suffix('.sha256').write_text(hashlib.sha256(raw).hexdigest(), encoding='ascii')
    return path


def test_edited_fixture_without_checksum_update_is_rejected(tmp_path):
    data, _ = evaluation.load_dataset()
    path = _write_dataset(tmp_path, data)
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='해시 불일치'):
        evaluation.evaluate(path=path)


@pytest.mark.parametrize('field,value', [('id', 'N02'), ('split', 'test'),
                                        ('expected', 'pass'), ('rationale', ''), ('scenario', 'missing')])
def test_invalid_case_metadata_is_rejected(tmp_path, field, value):
    data, _ = evaluation.load_dataset()
    data['cases'][0][field] = value
    with pytest.raises(ValueError, match='평가 사례'):
        evaluation.load_dataset(_write_dataset(tmp_path, data))


def test_wrong_expected_answer_is_reported_as_mismatch(tmp_path):
    data, _ = evaluation.load_dataset()
    data['cases'][0]['expected'] = 'reject'
    report = evaluation.evaluate(path=_write_dataset(tmp_path, data))
    assert report['metrics']['exact_matches'] == 41
    assert report['metrics']['unsafe_auto_publishes'] == 1
    assert report['results'][0]['match'] is False


@pytest.mark.parametrize('split,count', [('dev', 29), ('holdout', 13)])
def test_cli_runs_reserved_split_without_network(tmp_path, split, count):
    output = tmp_path / 'result.json'
    assert evaluation.main(['--split', split, '--output', str(output)]) == 0
    report = json.loads(output.read_text(encoding='utf-8'))
    assert len(report['results']) == count
    assert all(row['split'] == split for row in report['results'])


def test_cli_fails_when_expected_policy_regresses(monkeypatch, tmp_path):
    data, _ = evaluation.load_dataset()
    data['cases'][0]['expected'] = 'reject'
    path = _write_dataset(tmp_path, data)
    original = evaluation.evaluate
    monkeypatch.setattr(evaluation, 'evaluate', lambda **kw: original(path=path, **kw))
    assert evaluation.main(['--output', str(tmp_path / 'result.json')]) == 1
