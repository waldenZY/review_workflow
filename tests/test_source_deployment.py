import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app

SOURCE = Path(__file__).resolve().parents[1]


@pytest.fixture
def copied_source(tmp_path):
    root = tmp_path / 'copied-app'
    root.mkdir()
    shutil.copytree(SOURCE / 'backend', root / 'backend', ignore=shutil.ignore_patterns('__pycache__'))
    (root / 'scripts').mkdir()
    shutil.copyfile(SOURCE / 'scripts' / 'start.py', root / 'scripts' / 'start.py')
    shutil.copytree(SOURCE / 'frontend', root / 'frontend')
    shutil.copyfile(SOURCE / 'requirements.txt', root / 'requirements.txt')
    return root


def test_source_frontend_served_without_build(tmp_path):
    with TestClient(create_app(tmp_path / 'runtime')) as client:
        page = client.get('/')
        assert page.status_code == 200
        assert page.headers['cache-control'] == 'no-cache'
        assert '/static/app.js' in page.text and '/static/style.css' in page.text
        for name in ('app.js', 'style.css'):
            response = client.get('/static/' + name)
            assert response.status_code == 200
            assert response.text == (SOURCE / 'frontend' / name).read_text(encoding='utf-8')
        assert client.get('/static/../backend/models.py').status_code == 404
    assert sorted(path.name for path in (SOURCE / 'frontend').iterdir()) == ['app.js', 'index.html', 'style.css']
    assert not (SOURCE / 'scripts' / 'package_release.py').exists()


def test_copied_source_starts_without_node_from_unrelated_directory(copied_source, tmp_path):
    unrelated = tmp_path / 'unrelated'
    unrelated.mkdir()
    runtime = tmp_path / 'outside-runtime'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    environment = dict(os.environ, PATH='', PYTHONPATH='', EVAL_DATA_DIR=str(tmp_path / 'wrong-env-directory'))
    log_path = tmp_path / 'server.log'
    with log_path.open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, str(copied_source / 'scripts' / 'start.py'), '--host', '127.0.0.1',
            '--port', str(port), '--data-dir', str(runtime)], cwd=unrelated, env=environment, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 12
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=0.5, trust_env=False) as client:
                while time.monotonic() < deadline:
                    assert process.poll() is None, log_path.read_text(encoding='utf-8')
                    try:
                        health = client.get('/api/v1/health')
                        if health.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail(log_path.read_text(encoding='utf-8'))
                assert health.json()['mode'] == 'mock_default'
                assert Path(health.json()['data_dir']) == runtime.resolve()
                assert '/static/app.js' in client.get('/').text
                assert client.get('/static/app.js').text == (SOURCE / 'frontend' / 'app.js').read_text(encoding='utf-8')
                assert client.get('/static/style.css').status_code == 200
                assert client.get('/api/v1/catalog').json()['datasets'] == []
                assert client.get('/openapi.json').status_code == 200
            assert (runtime / 'workbench.sqlite3').is_file()
            assert not (tmp_path / 'wrong-env-directory').exists()
        finally:
            process.terminate()
            process.wait(timeout=10)


def test_start_rejects_incomplete_source(copied_source, tmp_path):
    (copied_source / 'frontend' / 'app.js').unlink()
    result = subprocess.run([sys.executable, str(copied_source / 'scripts' / 'start.py')],
        cwd=tmp_path, env=dict(os.environ, PATH='', PYTHONPATH=''), capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert 'Missing frontend files' in result.stderr
