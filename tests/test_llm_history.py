"""Fake network only; reuse the same single synthetic business row as v0 tests."""
import io
import json
import shutil
import sqlite3
import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from backend.app import create_app
from backend.llm import LlmService, load_connection
from test_workbench import post, seed, wait


CONFIG = dict(api_url='https://provider.invalid/custom/completion?revision=1', api_key='test-only-private-key-12345',
              model='test-model', max_tokens=123, timeout=3, stream=False, enable_thinking=True, skip_special_tokens=False)
PAYLOAD = {'criteria': {'C001': {'verdict': 'pass', 'reason': 'Fake network response', 'evidence': []}}}


def config_file(tmp_path, **updates):
    path = tmp_path / 'connection.json'
    path.write_text(json.dumps(CONFIG | updates), encoding='utf-8')
    return path


def completion(payload=PAYLOAD, usage=None):
    return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(payload)}}], 'usage': usage})


def request_body(row, mapping, evaluator, mode='real'):
    return dict(mapping_id=mapping['id'], mapping_version=mapping['version'], evaluator_id=evaluator['id'],
                evaluator_version=evaluator['version'], row_ids=[row['row_id']], execution_mode=mode)


def delete(client, kind, object_id):
    return client.request('DELETE', f'/api/v1/history/{kind}/{object_id}', json={'confirm_id': object_id})


def test_configuration_validation_and_fingerprints(tmp_path):
    path = config_file(tmp_path)
    cfg = load_connection(path)
    public = cfg.public()
    assert 'api_key' not in public and 'api_url' not in public
    assert CONFIG['api_key'] not in repr(cfg)
    assert load_connection(config_file(tmp_path, api_key='rotated-key')).public() == public
    assert load_connection(config_file(tmp_path, model='changed')).public()['fingerprint'] != public['fingerprint']
    for field, value in [('api_url', 'https://user:password@host/v1'), ('api_url', 'file:///tmp/a'),
                         ('api_key', 'replace-me'), ('model', ''), ('max_tokens', True), ('max_tokens', 0),
                         ('timeout', float('nan')), ('timeout', True), ('stream', 'false'), ('enable_thinking', 1)]:
        path = config_file(tmp_path, **{field: value})
        service = LlmService(path)
        assert not service.status()['configured']
        assert CONFIG['api_key'] not in json.dumps(service.status())
    path.write_text('{bad', encoding='utf-8')
    assert not LlmService(path).status()['configured']
    assert not LlmService(tmp_path / 'absent').status()['configured']


def test_request_protocol_usage_and_credential_redaction(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return completion(usage={'prompt_tokens': 11, 'completion_tokens': 7})
    root = tmp_path / 'runtime'
    with TestClient(create_app(root, config_file(tmp_path), httpx.MockTransport(handler))) as client:
        _, row, mapping, _, evaluator = seed(client)
        preview = post(client, '/requests/preview', request_body(row, mapping, evaluator) | {'row_id': row['row_id']})
        assert not requests  # Preview and startup never probe.
        run = wait(client, post(client, '/runs', request_body(row, mapping, evaluator))['id'])
        attempt = run['records'][0]['attempts'][0]
        assert attempt['status'] == 'completed' and not attempt['is_mock']
        assert attempt['usage']['prompt_tokens'] == 11 and attempt['latency_ms'] >= 0
        assert run['lineage']['model_config']['fingerprint']
        request = requests[0]
        assert str(request.url) == CONFIG['api_url']
        assert request.headers['Authorization'] == 'Bearer ' + CONFIG['api_key']
        body = json.loads(request.content)
        assert body['extra_body'] == {'enable_thinking': True, 'skip_special_tokens': False}
        assert body['max_tokens'] == 123 and body['stream'] is False and body['model'] == 'test-model'
        assert 'response_format' not in body
        assert preview['request']['body'] == body
        export = client.post(f"/api/v1/runs/{run['id']}/export", json={})
        book = load_workbook(io.BytesIO(export.content))
        columns, values = list(book.active.values)
        assert values[columns.index('eval.is_mock')] is False
        assert '_real.xlsx' in export.headers['content-disposition']
        visible = json.dumps(run) + client.get('/api/v1/llm/status').text + json.dumps(preview)
        assert CONFIG['api_key'] not in visible and CONFIG['api_url'] not in visible
    assert CONFIG['api_key'].encode() not in (root / 'workbench.sqlite3').read_bytes()
    for file in root.rglob('*.json'):
        assert CONFIG['api_key'] not in file.read_text(encoding='utf-8')


@pytest.mark.parametrize('failure,expected', [('timeout', 'call_failed'), ('http', 'call_failed'),
    ('envelope', 'call_failed'), ('json', 'parse_failed'), ('schema', 'schema_failed'), ('projection', 'projection_failed')])
def test_real_failures_are_recorded_without_content_failure(tmp_path, failure, expected):
    calls = []
    def handler(request):
        calls.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout(CONFIG['api_key'] + CONFIG['api_url'])
        if failure == 'http':
            return httpx.Response(401, text=CONFIG['api_key'] + ' access denied')
        if failure == 'envelope':
            return httpx.Response(200, text='not-json ' + CONFIG['api_key'])
        if failure == 'json':
            return httpx.Response(200, json={'choices': [{'message': {'content': '{bad ' + CONFIG['api_key']}}]})
        return completion({} if failure == 'schema' else PAYLOAD)
    with TestClient(create_app(tmp_path / 'runtime', config_file(tmp_path), httpx.MockTransport(handler))) as client:
        _, row, mapping, _, evaluator = seed(client)
        if failure == 'projection':
            evaluator['projection'][0]['verdict_path'] = '/missing'
            evaluator = post(client, '/evaluators', evaluator)
        run = wait(client, post(client, '/runs', request_body(row, mapping, evaluator))['id'])
        attempt = run['records'][0]['attempts'][0]
        assert attempt['status'] == expected and attempt['request'] and attempt['error']
        assert sum(run['summary']['verdict_counts'].values()) == 0
        assert len(calls) == 1  # No automatic retry or fallback.
        assert CONFIG['api_key'] not in json.dumps(run)
        if failure != 'timeout':
            assert attempt['raw_response'] is not None


@pytest.mark.parametrize('ending,expected', [('done', 'completed'), ('finish', 'completed'), ('broken', 'call_failed'), ('invalid', 'call_failed')])
def test_sse_completion_usage_and_partial_failures(tmp_path, ending, expected):
    output = json.dumps(PAYLOAD)
    chunks = [{'choices': [{'delta': {'content': output[:20]}}]},
              {'choices': [{'delta': {'content': output[20:]}, 'finish_reason': 'stop' if ending == 'finish' else None}]},
              {'choices': [], 'usage': {'total_tokens': 9}}]
    stream = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks)
    if ending == 'done':
        stream += 'data: [DONE]\n\n'
    elif ending == 'invalid':
        stream += 'data: {bad\n\n'
    with TestClient(create_app(tmp_path / 'runtime', config_file(tmp_path, stream=True),
                              httpx.MockTransport(lambda _: httpx.Response(200, text=stream)))) as client:
        _, row, mapping, _, evaluator = seed(client)
        run = wait(client, post(client, '/runs', request_body(row, mapping, evaluator))['id'])
        attempt = run['records'][0]['attempts'][0]
        assert attempt['status'] == expected and attempt['raw_output'] == output
        assert attempt['usage']['total_tokens'] == 9
        assert attempt['first_token_latency_ms'] >= 0 and attempt['raw_response']
        if ending == 'invalid':
            assert '{bad' in attempt['raw_response']['events']


def test_independent_design_modes_saved_candidates_and_missing_evidence(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        system = json.loads(request.content)['messages'][0]['content']
        if 'method' in system:
            return completion({'criteria': [{'id': 'C001', 'name': 'Candidate', 'required_roles': ['reference', 'output']}]})
        return completion({'advice': 'Looks sufficient'})
    root = tmp_path / 'runtime'
    config = config_file(tmp_path)
    with TestClient(create_app(root, config, httpx.MockTransport(handler))) as client:
        dataset, row, mapping, spec, evaluator = seed(client)
        mock = post(client, '/specs/extract', {'generation_prompt': 'Do not generate'})
        assert mock['is_mock'] and not requests
        real = post(client, '/specs/extract', {'generation_prompt': 'Do not generate', 'execution_mode': 'real'})
        assert len(requests) == 1 and not real['is_mock']
        call_id = real['design_call_id']
        restored = client.get(f'/api/v1/history/design_call/{call_id}').json()['object']
        assert restored['result']['criteria'] == real['criteria']
        spec = post(client, '/specs', {'title': 'Restored candidate', 'criteria': real['criteria'], 'design_call_ids': [call_id]})
        assert delete(client, 'design_call', call_id).status_code == 409
        mapping = post(client, '/mappings', mapping | {'references': []})
        analyzed = post(client, '/evidence/analyze', {'mapping_id': mapping['id'], 'mapping_version': mapping['version'],
            'spec_id': spec['id'], 'spec_version': spec['version'], 'row_id': row['row_id'], 'execution_mode': 'real'})
        assert analyzed['reports'][0]['missing_roles'] == ['reference']
        assert len(requests) == 2 and analyzed['advice']['advice'] == 'Looks sufficient'
        # Analyze returns suggestions; deterministic reports cannot be overwritten.
        assert delete(client, 'dataset', dataset['id']).status_code == 409
        failed = client.post('/api/v1/specs/extract', json={'generation_prompt': 'anything', 'mock_response': '{bad'})
        assert failed.status_code == 400
        failure_id = failed.json()['design_call_id']
    with TestClient(create_app(root, config, httpx.MockTransport(handler))) as client:
        assert client.get(f'/api/v1/history/design_call/{call_id}').json()['object']['result']
        assert client.get(f'/api/v1/history/design_call/{failure_id}').json()['object']['status'] == 'parse_failed'
        assert client.get(f"/api/v1/datasets/{dataset['id']}/rows").json()['rows'][0]['original'] == row['original']


def test_missing_configuration_does_not_block_mock_or_program_rules(tmp_path):
    calls = []
    with TestClient(create_app(tmp_path / 'runtime', tmp_path / 'missing',
                              httpx.MockTransport(lambda r: calls.append(r)))) as client:
        _, row, mapping, _, evaluator = seed(client, method='non_empty')
        run = wait(client, post(client, '/runs', request_body(row, mapping, evaluator))['id'])
        assert run['status'] == 'completed' and not run['records'][0]['attempts'][0]['model_called']
        _, row, mapping, _, evaluator = seed(client)
        assert client.post('/api/v1/runs', json=request_body(row, mapping, evaluator)).status_code == 400
        assert wait(client, post(client, '/runs', request_body(row, mapping, evaluator, 'mock'))['id'])['is_mock']
        response = client.post('/api/v1/specs/extract', json={'generation_prompt': 'x', 'execution_mode': 'real'})
        assert response.status_code == 400 and response.json()['design_call_id']
        assert not calls


def test_retry_freezes_mode_and_public_config_but_allows_key_rotation(tmp_path):
    root = tmp_path / 'runtime'
    config = config_file(tmp_path)
    with TestClient(create_app(root, config, httpx.MockTransport(lambda _: httpx.Response(503)))) as client:
        _, row, mapping, _, evaluator = seed(client)
        run = wait(client, post(client, '/runs', request_body(row, mapping, evaluator))['id'])
        first_fingerprint = run['snapshot']['model_config']['fingerprint']
        # Editing the file while running has no effect until restart.
        config_file(tmp_path, model='new-model')
        assert client.get('/api/v1/llm/status').json()['parameters']['model'] == CONFIG['model']
    with TestClient(create_app(root, config, httpx.MockTransport(lambda _: completion()))) as client:
        blocked = client.post(f"/api/v1/runs/{run['id']}/retry")
        assert blocked.status_code == 409
        rerun = post(client, f"/runs/{run['id']}/rerun", {'evaluator_id': evaluator['id'], 'evaluator_version': 1, 'execution_mode': 'real'})
        assert wait(client, rerun['id'])['snapshot']['model_config']['fingerprint'] != first_fingerprint
        assert client.get(f"/api/v1/runs/{run['id']}").json()['snapshot']['model_config']['fingerprint'] == first_fingerprint
    config_file(tmp_path, api_key='new-private-key-for-tests')
    requests = []
    def handler(request):
        requests.append(request)
        return completion()
    with TestClient(create_app(root, config, httpx.MockTransport(handler))) as client:
        post(client, f"/runs/{run['id']}/retry", {})
        retried = wait(client, run['id'])
        assert len(retried['records'][0]['attempts']) == 2
        assert not retried['is_mock'] and retried['status'] == 'completed'
        assert requests[0].headers['Authorization'] == 'Bearer new-private-key-for-tests'


def test_history_archival_references_comparison_and_owned_cleanup(tmp_path):
    root = tmp_path / 'runtime'
    with TestClient(create_app(root, tmp_path / 'missing')) as client:
        dataset, row, mapping, spec, evaluator = seed(client)
        run = wait(client, post(client, '/runs', request_body(row, mapping, evaluator, 'mock'))['id'])
        for kind, item in [('dataset', dataset), ('spec', spec), ('evaluator', evaluator)]:
            response = delete(client, kind, item['id'])
            assert response.status_code == 409 and response.json()['references']
        spec2 = post(client, '/specs', spec | {'title': 'Version two'})
        comparison = client.get(f"/api/v1/entities/{spec['id']}/compare?left=1&right=2").json()
        assert any(d['path'] == '/title' for d in comparison['differences'])
        post(client, f"/history/spec/{spec['id']}/archive", {})
        assert not client.get('/api/v1/catalog').json()['specs']
        versions = client.get(f"/api/v1/entities/{spec['id']}/versions").json()
        assert all(v['archived_at'] for v in versions)
        assert client.get('/api/v1/history?kind=spec&include_archived=true').json()['items']
        assert client.post('/api/v1/evaluators/build', json={'spec_id': spec['id'], 'spec_version': 1}).status_code == 400
        assert client.get(f"/api/v1/runs/{run['id']}").json()['snapshot']['spec']['title'] == spec['title']
        post(client, f"/history/spec/{spec['id']}/restore", {})
        assert client.get('/api/v1/catalog').json()['specs'][0]['version'] == 2
        assert client.post(f"/api/v1/runs/{run['id']}/export", json={}).status_code == 200
        folder = root / 'runs' / run['id']
        assert folder.exists()
        assert delete(client, 'run', run['id']).status_code == 200 and not folder.exists()
        assert delete(client, 'evaluator', evaluator['id']).status_code == 200
        assert delete(client, 'spec', spec['id']).status_code == 200
        owned = root / 'imports' / (dataset['id'] + '.json')
        sentinel = root / 'imports' / 'unrelated.json'
        sentinel.write_text('preserve', encoding='utf-8')
        assert owned.exists()
        assert delete(client, 'dataset', dataset['id']).status_code == 200
        assert not owned.exists() and sentinel.exists()
        assert client.get(f"/api/v1/entities/{mapping['id']}").status_code == 404
        events = client.get(f"/api/v1/history/events?object_id={spec['id']}").json()['events']
        assert {'archive', 'restore', 'delete'} <= {e['action'] for e in events}


def test_case_and_followup_run_protect_ancestors(tmp_path):
    with TestClient(create_app(tmp_path / 'runtime', tmp_path / 'missing')) as client:
        _, row, mapping, _, evaluator = seed(client)
        run = wait(client, post(client, '/runs', request_body(row, mapping, evaluator, 'mock'))['id'])
        post(client, f"/runs/{run['id']}/rows/{row['row_id']}/review", {'criterion_id': 'C001', 'verdict': 'pass',
            'reason': 'Independent confirmation', 'save_case': True, 'confirm_mock': True})
        case = client.get('/api/v1/catalog').json()['cases'][0]
        assert delete(client, 'run', run['id']).status_code == 409
        child = post(client, f"/cases/{case['id']}/rerun", {'evaluator_id': evaluator['id'], 'evaluator_version': 1})
        wait(client, child['id'])
        assert delete(client, 'case', case['id']).status_code == 409
        assert delete(client, 'run', child['id']).status_code == 200
        assert delete(client, 'case', case['id']).status_code == 200
        assert delete(client, 'run', run['id']).status_code == 200


def test_busy_run_cannot_archive_or_delete(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def handler(_):
        entered.set()
        assert release.wait(5)
        return completion()
    with TestClient(create_app(tmp_path / 'runtime', config_file(tmp_path), httpx.MockTransport(handler))) as client:
        try:
            _, row, mapping, _, evaluator = seed(client)
            run = post(client, '/runs', request_body(row, mapping, evaluator))
            assert entered.wait(3)
            assert client.post(f"/api/v1/history/run/{run['id']}/archive").status_code == 409
            assert delete(client, 'run', run['id']).status_code == 409
        finally:
            release.set()
        wait(client, run['id'])


def test_legacy_database_copy_migrates_without_rewriting_snapshots(tmp_path):
    source = tmp_path / 'original'
    with TestClient(create_app(source, tmp_path / 'missing')) as client:
        dataset, row, mapping, spec, evaluator = seed(client)
        run = wait(client, post(client, '/runs', request_body(row, mapping, evaluator, 'mock'))['id'])
    clone = tmp_path / 'legacy-copy'
    clone.mkdir()
    shutil.copy2(source / 'workbench.sqlite3', clone / 'workbench.sqlite3')
    with sqlite3.connect(clone / 'workbench.sqlite3') as db:
        db.execute('DROP TABLE lifecycle')
        db.execute('DROP TABLE audit')
        legacy = json.loads(db.execute('SELECT body FROM runs').fetchone()[0])
        legacy.pop('execution_mode')
        legacy['snapshot'].pop('model_config')
        db.execute('UPDATE runs SET body=?', (json.dumps(legacy),))
    with TestClient(create_app(clone, tmp_path / 'missing')) as client:
        opened = client.get(f"/api/v1/runs/{run['id']}").json()
        assert opened['execution_mode'] == 'mock' and opened['snapshot'] == legacy['snapshot']
        assert client.get('/api/v1/history?kind=dataset').json()['items'][0]['archived_at'] is None
        child = post(client, f"/runs/{run['id']}/rerun", {'evaluator_id': evaluator['id'], 'evaluator_version': 1})
        assert wait(client, child['id'])['status'] == 'completed'
        delete(client, 'run', child['id'])
        delete(client, 'run', run['id'])
        delete(client, 'evaluator', evaluator['id'])
        delete(client, 'spec', spec['id'])
        assert delete(client, 'dataset', dataset['id']).status_code == 200
    # A copied database's old absolute source_path cannot delete the original upload.
    assert (source / 'imports' / (dataset['id'] + '.json')).exists()


def test_all_versions_keep_references_and_design_call_restart_recovery(tmp_path):
    root = tmp_path / 'runtime'
    with TestClient(create_app(root, tmp_path / 'missing')) as client:
        extracted = post(client, '/specs/extract', {'generation_prompt': 'Example only'})
        call_id = extracted['design_call_id']
        spec = post(client, '/specs', {'title': 'Linked', 'criteria': extracted['criteria'], 'design_call_ids': [call_id]})
        post(client, '/specs', spec | {'design_call_ids': []})
        assert delete(client, 'design_call', call_id).status_code == 409  # v1 still retains the link.
        pending = client.app.state.store.save('design_call', {'status': 'design_running', 'purpose': 'extract', 'references': []})
        invalid = client.post('/api/v1/specs/extract', json={'generation_prompt': 'x', 'mock_response': '{"criteria":NaN}'})
        assert invalid.status_code == 400
    with TestClient(create_app(root, tmp_path / 'missing')) as client:
        recovered = client.get(f"/api/v1/history/design_call/{pending['id']}").json()['object']
        assert recovered['status'] == 'interrupted' and recovered['version'] == 2
        assert delete(client, 'spec', spec['id']).status_code == 200
        assert delete(client, 'design_call', call_id).status_code == 200


def test_cleanup_rejects_paths_outside_data_directory(tmp_path, monkeypatch):
    external = tmp_path / 'original-file.json'
    external.write_text('must remain', encoding='utf-8')
    with TestClient(create_app(tmp_path / 'runtime', tmp_path / 'missing')) as client:
        dataset, _, _, _, _ = seed(client)
        monkeypatch.setattr(client.app.state.history, '_owned_paths', lambda *_: [external])
        assert delete(client, 'dataset', dataset['id']).status_code == 400
        assert client.get(f"/api/v1/entities/{dataset['id']}").status_code == 200
        assert external.read_text(encoding='utf-8') == 'must remain'
