import hashlib
import io
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import markdown
from fastapi import APIRouter, FastAPI, File, Form, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from jsonschema.exceptions import SchemaError
from openpyxl import load_workbook

from .data_io import MAX_IMPORT_BYTES, check_import_size, fields_for, parse_file
from .engine import Runner
from .history import History
from .llm import Gateway, GatewayError, ModelSettings
from .models import (ConnectionTest, MappingSave, ObjectDelete, ProfileSave, ProjectDelete, ProjectSave)
from .store import Conflict, Store, now, uid
from .workflow import register, run_meta

ROOT = Path(__file__).resolve().parents[1]


def asset_url(name):
    digest = hashlib.sha256((ROOT / 'frontend' / name).read_bytes()).hexdigest()[:16]
    return f'/static/{name}?v={digest}'


def dataset_meta(value):
    return {k: v for k, v in value.items() if k != 'rows'}


def create_app(data_dir=None, llm_config_path=None):
    data_root = Path(data_dir or os.environ.get('EVAL_DATA_DIR', str(ROOT.parent / 'review_workflow_data'))).resolve() / 'v1_1'

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(data_root)
        runner = None
        try:
            app.state.settings = ModelSettings(llm_config_path or ROOT / 'config' / 'llm_config.local.json')
            app.state.gateway = Gateway(app.state.settings)
            app.state.history = History(app.state.store)
            for project in app.state.store.projects(True):
                for call in app.state.store.entities(project['id'], 'standard_test', True, summary=True):
                    if call['status'] == 'design_running':
                        call = app.state.store.get(project['id'], call['id'], kind='standard_test')
                        call.update(status='interrupted', finished_at=now(), error='服务重启，设计调用已中断。')
                        app.state.store.save(project['id'], 'standard_test', call, call['id'])
            app.state.runner = Runner(app.state.store, app.state.gateway)
            runner = app.state.runner
            yield
        finally:
            if runner:
                runner.close()
            app.state.store.close()

    app = FastAPI(title='Eval Studio', version='1.1.0', lifespan=lifespan)
    router = APIRouter(prefix='/api/v1/projects/{project_id}')

    def store():
        return app.state.store

    def clean(value):
        return app.state.settings.sanitize(value)

    @app.exception_handler(KeyError)
    async def missing(_, exc):
        return JSONResponse(status_code=404, content={'detail': clean(str(exc))})

    @app.exception_handler(ValueError)
    @app.exception_handler(SchemaError)
    async def invalid(_, exc):
        return JSONResponse(status_code=400, content={'detail': clean(str(exc))[:2000]})

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        return JSONResponse(status_code=409, content={'detail': clean(str(exc)), 'references': clean(exc.references)})

    @app.exception_handler(sqlite3.Error)
    async def database_error(_, exc):
        return JSONResponse(status_code=503, content={'detail': '数据库暂时繁忙或存储操作失败，请稍后重试读取；不要重复提交模型任务。', 'code': 'storage_unavailable'})

    @app.exception_handler(OSError)
    async def file_error(_, exc):
        return JSONResponse(status_code=400, content={'detail': '文件操作失败，请检查数据目录权限、剩余空间和文件占用。'})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_, exc):
        # Never echo a submitted key through Pydantic's input/context fields.
        details = [{'loc': list(e['loc']), 'message': e['msg']} for e in exc.errors()]
        return JSONResponse(status_code=422, content={'detail': clean(details)})

    @app.get('/api/v1/health')
    async def health():
        return {'status': 'ok', 'version': '1.1.0', 'storage_revision': 4, 'data_dir': str(data_root), 'database': 'workbench_v1_1.sqlite3'}

    @app.get('/api/v1/llm/status')
    @app.get('/api/v1/llm/profiles')
    def llm_config():
        return app.state.settings.public()

    @app.post('/api/v1/llm/profiles')
    def create_profile(body: ProfileSave):
        value = app.state.settings.save(body.name, body.model_dump(exclude={'name', 'execution'}), execution=body.execution.model_dump())
        store().event(None, value['profile_id'], 'model_profile_created')
        return value

    @app.put('/api/v1/llm/profiles/{profile_id}')
    def save_profile(profile_id: str, body: ProfileSave):
        value = app.state.settings.save(body.name, body.model_dump(exclude={'name', 'execution'}), profile_id, execution=body.execution.model_dump())
        store().event(None, profile_id, 'model_profile_saved')
        return value

    @app.post('/api/v1/llm/profiles/{profile_id}/activate')
    def activate_profile(profile_id: str):
        value = app.state.settings.activate(profile_id)
        store().event(None, profile_id, 'model_profile_activated', value['parameters'])
        return value

    @app.delete('/api/v1/llm/profiles/{profile_id}')
    def delete_profile(profile_id: str):
        value = app.state.settings.delete(profile_id)
        store().event(None, profile_id, 'model_profile_deleted')
        return value

    @app.post('/api/v1/llm/profiles/{profile_id}/clear-key')
    def clear_key(profile_id: str):
        value = app.state.settings.clear_key(profile_id)
        store().event(None, profile_id, 'model_key_cleared')
        return value

    @app.post('/api/v1/llm/test')
    def test_connection(body: ConnectionTest):
        connection = app.state.settings.candidate(body.model_dump(exclude={'profile_id'}), body.profile_id)
        try:
            trace = app.state.gateway.invoke([{'role': 'user', 'content': '请只回复 OK。'}], connection, probe=True)
            result = {'success': True, 'message': '连接与协议检查通过，不代表业务 JSON 格式或评测质量已验证。', 'trace': trace}
        except GatewayError as exc:
            result = {'success': False, 'message': str(exc), 'trace': exc.trace}
        store().event(None, body.profile_id or 'unsaved_profile', 'connection_test', result)
        return result

    @app.get('/api/v1/projects')
    def projects(include_archived: bool = False, query: str = ''):
        return {'projects': store().projects(include_archived, query)}

    @app.post('/api/v1/projects')
    def create_project(body: ProjectSave):
        return store().save_project(body.model_dump())

    @router.get('')
    def get_project(project_id: str):
        return store().project(project_id)

    @router.put('')
    def update_project(project_id: str, body: ProjectSave):
        return store().save_project(body.model_dump(), project_id)

    @router.post('/archive')
    def archive_project(project_id: str):
        return app.state.history.archive_project(project_id)

    @router.post('/restore')
    def restore_project(project_id: str):
        return app.state.history.archive_project(project_id, True)

    @router.get('/deletion-scope')
    def project_scope(project_id: str):
        return app.state.history.deletion_scope(project_id)

    @router.delete('')
    def delete_project(project_id: str, body: ProjectDelete):
        return app.state.history.delete_project(project_id, body.confirm_name)

    @router.get('/catalog')
    def catalog(project_id: str):
        return {'project': store().project(project_id), 'datasets': [dataset_meta(v) for v in store().entities(project_id, 'dataset', summary=True)],
                'mappings': store().entities(project_id, 'mapping', summary=True), 'standards': store().entities(project_id, 'standard'),
                'plans': store().entities(project_id, 'execution_plan'), 'runs': [run_meta(r) for r in store().runs(project_id)]}

    @router.get('/entities/{object_id}')
    def get_entity(project_id: str, object_id: str, version: int | None = None):
        value = store().get(project_id, object_id, version)
        return dataset_meta(value) if value['kind'] == 'dataset' else value

    @router.get('/entities/{object_id}/versions')
    def versions(project_id: str, object_id: str):
        return [dataset_meta(v) if v['kind'] == 'dataset' else v for v in store().versions(project_id, object_id)]

    @router.get('/entities/{object_id}/compare')
    def compare(project_id: str, object_id: str, left: int, right: int):
        before, after = (store().get(project_id, object_id, v) for v in (left, right))
        if before['kind'] not in ('standard', 'execution_plan'):
            raise ValueError('仅支持标准和批跑方案的版本对比。')
        ignore = {'id', 'project_id', 'kind', 'version', 'created_at', 'archived_at'}
        changes = []
        def walk(a, b, path=''):
            if isinstance(a, dict) and isinstance(b, dict):
                for key in sorted(a.keys() | b.keys()):
                    if not path and key in ignore:
                        continue
                    walk(a.get(key), b.get(key), path + '/' + str(key))
            elif a != b:
                changes.append({'path': path, 'before': a, 'after': b})
        walk(before, after)
        return {'left': left, 'right': right, 'differences': changes}

    @router.post('/datasets/inspect')
    async def inspect(project_id: str, file: UploadFile = File(...)):
        store().project(project_id, active=True)
        if Path(file.filename or '').suffix.lower() != '.xlsx':
            raise ValueError('工作表检查仅接受 XLSX。')
        content = await file.read(MAX_IMPORT_BYTES + 1)
        check_import_size(content)
        try:
            book = load_workbook(io.BytesIO(content), read_only=True)
            try:
                return {'sheets': book.sheetnames}
            finally:
                book.close()
        except Exception:
            raise ValueError('无法读取 XLSX，请检查文件内容。') from None

    @router.post('/datasets/import')
    async def import_dataset(project_id: str, file: UploadFile = File(...), sheet: str = Form(''), header_row: int = Form(1)):
        store().project(project_id, active=True)
        filename = Path((file.filename or 'upload').replace('\\', '/')).name
        content = await file.read(MAX_IMPORT_BYTES + 1)
        check_import_size(content)
        def ingest():
            try:
                records, sources, sheet_name, source_hash = parse_file(filename, content, sheet, header_row)
            except ValueError:
                raise
            except Exception:
                raise ValueError('文件解析失败，请检查编码、格式及工作表。') from None
            with store().lock:
                store().writable(project_id)
                folder = app.state.history.project_dir(project_id) / 'imports'
                app.state.history.validate_path(folder, app.state.history.project_dir(project_id))
                folder.mkdir(parents=True, exist_ok=True)
                dataset_id = uid('dataset')
                target = folder / (dataset_id + Path(filename).suffix.lower())
                try:
                    target.write_bytes(content)
                    value = {'filename': filename, 'sheet': sheet_name, 'header_row': header_row, 'source_hash': source_hash,
                        'row_count': len(records), 'fields': fields_for(records),
                        'rows': [{'row_id': f'{dataset_id}:{i}', 'source_row': n, 'original': row}
                                 for i, (row, n) in enumerate(zip(records, sources), 1)]}
                    result = store().save(project_id, 'dataset', value, new_id=dataset_id)
                    return dataset_meta(result)
                except Exception:
                    if target.exists():
                        target.unlink()
                    raise
        return await run_in_threadpool(ingest)

    @router.get('/datasets/{dataset_id}/rows')
    def rows(project_id: str, dataset_id: str, offset: int = 0, limit: int = 20, version: int | None = None):
        dataset = store().get(project_id, dataset_id, version=version, kind='dataset')
        if offset < 0 or limit not in (10, 20, 50):
            raise ValueError('offset 必须非负，limit 仅支持 10、20、50。')
        return {'rows': store().data_page(project_id, dataset['material_id'], offset, limit), 'total': dataset['row_count']}

    @router.post('/mappings')
    def save_mapping(project_id: str, body: MappingSave):
        with store().lock:
            dataset = store().get(project_id, body.dataset_id, kind='dataset', active=True)
            available = {f['path'] for f in dataset['fields']}
            if len(set(body.evaluation_fields)) != len(body.evaluation_fields) or any(p not in available for p in body.evaluation_fields):
                raise ValueError('请选择数据集中存在且不重复的评测字段。')
            return store().save(project_id, 'mapping', body.model_dump(exclude={'id'}), body.id)

    get_run = register(app, router, store)

    @router.get('/history')
    def history(project_id: str, kind: str | None = None, include_archived: bool = False):
        return {'items': app.state.history.listing(project_id, kind, include_archived)}

    @router.get('/history/events')
    def events(project_id: str, object_id: str | None = None):
        return {'events': store().events(project_id, object_id)}

    @router.get('/history/{kind}/{object_id}')
    def history_detail(project_id: str, kind: str, object_id: str):
        value = app.state.history.get(project_id, kind, object_id)
        if kind == 'dataset':
            value = dataset_meta(value) | {'preview_rows': store().data_page(project_id, value['material_id'], 0, 20)}
        elif kind == 'run':
            value = get_run(project_id, object_id)
        versions = [] if kind == 'run' else store().versions(project_id, object_id)
        if kind == 'dataset':
            versions = [dataset_meta(v) for v in versions]
        return {'object': value, 'versions': versions, 'references': app.state.history.references(project_id, kind, object_id),
                'events': store().events(project_id, object_id)}

    @router.post('/history/{kind}/{object_id}/archive')
    def archive(project_id: str, kind: str, object_id: str):
        return app.state.history.archive(project_id, kind, object_id)

    @router.post('/history/{kind}/{object_id}/restore')
    def restore(project_id: str, kind: str, object_id: str):
        return app.state.history.archive(project_id, kind, object_id, True)

    @router.delete('/history/{kind}/{object_id}')
    def delete(project_id: str, kind: str, object_id: str, body: ObjectDelete):
        if body.confirm_id != object_id:
            raise ValueError('确认 ID 不匹配。')
        return app.state.history.delete(project_id, kind, object_id)

    app.include_router(router)

    @app.get('/help/{page}', response_class=HTMLResponse)
    def help_page(page: str):
        paths = {'tutorial': ROOT / 'docs' / '使用教程.md', 'deployment': ROOT / 'docs' / 'Linux部署.md'}
        if page not in paths:
            raise KeyError('帮助页面不存在。')
        source = paths[page].read_text(encoding='utf-8')
        content = markdown.markdown(source, extensions=['tables', 'fenced_code', 'toc', 'attr_list'])
        content = content.replace('href="Linux部署.md', 'href="/help/deployment').replace('href="使用教程.md', 'href="/help/tutorial')
        return HTMLResponse('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                            f'<title>Eval Studio 使用帮助</title><link rel="stylesheet" href="{asset_url("style.css")}"></head>'
                            '<body class="help-document"><nav><a href="/help/tutorial">使用教程</a> · <a href="/help/deployment">部署说明</a></nav><article>' + content + '</article></body></html>')

    @app.get('/')
    def index():
        html = (ROOT / 'frontend' / 'index.html').read_text(encoding='utf-8')
        for name in ('app.js', 'style.css'):
            html = html.replace('/static/' + name, asset_url(name))
        return HTMLResponse(html, headers={'Cache-Control': 'no-cache'})

    app.mount('/static', StaticFiles(directory=ROOT / 'frontend'), name='static')
    return app


app = create_app()
