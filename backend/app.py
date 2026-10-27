import copy
import io
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jsonschema.exceptions import SchemaError
from openpyxl import load_workbook

from .data_io import fields_for, flatten, parse_file, write_xlsx
from .engine import (MockGateway, Runner, check_schema, default_evaluator,
                     export_table, judge_request, make_request, missing_roles, role_values, summary)
from .models import (AnalysisRequest, BuildRequest, Criterion, EvaluatorSave, ExportRequest,
                     ExtractRequest, MappingSave, PreviewRequest, RetryRequest, ReviewSave,
                     RunRequest, SpecSave)
from .store import Store, now, uid

REPO_ROOT = Path(__file__).resolve().parents[1]
BUSY = ('queued', 'running', 'cancelling')


def dataset_meta(dataset):
    return {key: value for key, value in dataset.items() if key != 'rows'}


def run_meta(run):
    return {key: run.get(key) for key in ('id', 'created_at', 'started_at', 'finished_at', 'status', 'is_mock', 'parent_run_id', 'validation_case_id')} | {
        'dataset_name': run['snapshot']['dataset']['filename'], 'spec_title': run['snapshot']['spec']['title'], 'summary': summary(run)}


def create_app(data_dir=None):
    data_root = Path(data_dir or os.environ.get('EVAL_DATA_DIR', str(REPO_ROOT.parent / 'review_workflow_data'))).resolve()

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(data_root)
        app.state.runner = Runner(app.state.store)
        yield
        app.state.runner.close()
        app.state.store.close()

    app = FastAPI(title='Eval Studio', version='0.0.1', lifespan=lifespan)

    @app.exception_handler(KeyError)
    async def missing_handler(_, exc):
        return JSONResponse(status_code=404, content={'detail': str(exc)})

    @app.exception_handler(ValueError)
    @app.exception_handler(SchemaError)
    async def validation_handler(_, exc):
        return JSONResponse(status_code=400, content={'detail': str(exc)[:2000]})

    def store():
        return app.state.store

    def context(mapping_id, mapping_version, evaluator_id, evaluator_version):
        mapping = store().get(mapping_id, mapping_version, 'mapping')
        dataset = store().get(mapping['dataset_id'], kind='dataset')
        evaluator = store().get(evaluator_id, evaluator_version, 'evaluator')
        spec = store().get(evaluator['spec_id'], evaluator['spec_version'], 'spec')
        for criterion in spec['criteria']:
            if criterion['enabled'] and criterion['method'] != 'llm' and criterion['target_field'] and criterion['target_field'] not in mapping['outputs']:
                raise ValueError(f"标准 {criterion['id']} 的规则目标必须属于当前映射的输出字段。")
        return dataset, mapping, spec, evaluator

    def enqueue(snapshot, selected, parent_run_id=None, case_id=None):
        run = {'id': uid('run'), 'created_at': now(), 'started_at': None, 'finished_at': None,
               'status': 'queued', 'cancel_requested': False, 'is_mock': True, 'error': None,
               'parent_run_id': parent_run_id, 'validation_case_id': case_id, 'snapshot': copy.deepcopy(snapshot),
               'records': [{'row_id': row_id, 'status': 'pending', 'attempts': [], 'reviews': []} for row_id in selected]}
        app.state.runner.submit(run)
        return run_meta(run)

    @app.get('/api/v1/health')
    def health():
        return {'status': 'ok', 'mode': 'mock_only', 'data_dir': str(data_root)}

    @app.get('/api/v1/catalog')
    def catalog():
        return {'datasets': [dataset_meta(d) for d in store().list('dataset')], 'mappings': store().list('mapping'),
                'specs': store().list('spec'), 'evaluators': store().list('evaluator'),
                'runs': [run_meta(r) for r in store().runs()], 'cases': store().list('case')}

    @app.get('/api/v1/entities/{entity_id}')
    def entity(entity_id: str, version: int | None = None):
        value = store().get(entity_id, version)
        return dataset_meta(value) if value['kind'] == 'dataset' else value

    @app.get('/api/v1/entities/{entity_id}/versions')
    def versions(entity_id: str):
        return [dataset_meta(v) if v['kind'] == 'dataset' else v for v in store().versions(entity_id)]

    @app.post('/api/v1/datasets/inspect')
    async def inspect(file: UploadFile = File(...)):
        if Path(file.filename or '').suffix.lower() != '.xlsx':
            raise ValueError('工作表检查仅适用于 XLSX。')
        content = await file.read(25 * 1024 * 1024 + 1)
        if len(content) > 25 * 1024 * 1024:
            raise ValueError('v0 单文件最大 25 MB。')
        try:
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
            try:
                return {'sheets': workbook.sheetnames}
            finally:
                workbook.close()
        except Exception as exc:
            raise ValueError(f'无法读取 XLSX：{exc}') from exc

    @app.post('/api/v1/datasets/import')
    async def import_dataset(file: UploadFile = File(...), sheet: str = Form(''), header_row: int = Form(1)):
        filename = Path(file.filename or 'upload').name
        content = await file.read(25 * 1024 * 1024 + 1)
        try:
            records, sources, sheet_name, source_hash = parse_file(filename, content, sheet or None, header_row)
        except Exception as exc:
            raise ValueError(f'导入失败：{exc}') from exc
        dataset_id = uid('dataset')
        folder = store().root / 'imports'
        folder.mkdir(exist_ok=True)
        source_path = folder / (dataset_id + Path(filename).suffix.lower())
        source_path.write_bytes(content)
        dataset = store().save('dataset', {'filename': filename, 'sheet': sheet_name, 'header_row': header_row,
            'source_hash': source_hash, 'source_path': str(source_path), 'row_count': len(records), 'fields': fields_for(records),
            'rows': [{'row_id': f'{dataset_id}:{index}', 'source_row': source_row, 'original': record}
                     for index, (record, source_row) in enumerate(zip(records, sources), 1)]}, dataset_id)
        return dataset_meta(dataset)

    @app.get('/api/v1/datasets/{dataset_id}/rows')
    def dataset_rows(dataset_id: str, offset: int = 0, limit: int = 50):
        dataset = store().get(dataset_id, kind='dataset')
        if offset < 0 or not 1 <= limit <= 200:
            raise ValueError('offset 必须非负，limit 范围为 1–200。')
        return {'rows': dataset['rows'][offset:offset + limit], 'total': dataset['row_count']}

    @app.post('/api/v1/mappings')
    def save_mapping(body: MappingSave):
        dataset = store().get(body.dataset_id, kind='dataset')
        fields = {field['path'] for field in dataset['fields']}
        configured = body.inputs + body.outputs + body.references
        if len(configured) != len(set(configured)):
            raise ValueError('同一字段不可重复映射到不同角色。')
        if any(path not in fields for path in configured):
            raise ValueError('映射包含数据集中不存在的字段。')
        if body.id and store().get(body.id, kind='mapping')['dataset_id'] != body.dataset_id:
            raise ValueError('不同数据集请建立新的映射。')
        return store().save('mapping', body.model_dump(exclude={'id'}), body.id)

    @app.post('/api/v1/specs')
    def save_spec(body: SpecSave):
        for criterion in body.criteria:
            if criterion.method == 'json_schema':
                check_schema(criterion.rule_schema)
        if body.id:
            store().get(body.id, kind='spec')
        return store().save('spec', body.model_dump(exclude={'id'}), body.id)

    @app.post('/api/v1/specs/extract')
    def extract(body: ExtractRequest):
        criteria = [Criterion(id='C001', name='输出符合任务要求', description='请根据实际任务人工改写本条模拟候选标准。', required_roles=['input', 'output']).model_dump(),
                    Criterion(id='C002', name='输出非空', description='待评测输出应有内容。', method='non_empty').model_dump()]
        request = make_request('extract', '从生成任务说明提取候选标准，不能执行生成任务。返回 JSON，包含 criteria 数组；候选必须由人工确认。', {'generation_prompt': body.generation_prompt})
        response = MockGateway().invoke(request, body.mock_response if body.mock_response is not None else json.dumps({'criteria': criteria}, ensure_ascii=False))
        try:
            parsed = json.loads(response['choices'][0]['message']['content'])
            criteria = [Criterion.model_validate(c).model_dump() for c in parsed['criteria']]
            SpecSave(title='候选校验', criteria=criteria)
        except Exception as exc:
            return JSONResponse(status_code=400, content={'detail': '模拟提取响应不符合候选标准结构：' + str(exc), 'trace': {'request': request, 'raw_response': response}})
        return {'criteria': criteria, 'is_mock': True, 'notice': '这些是通用模拟候选，未对 Prompt 做语义提取，必须编辑并确认。', 'trace': {'request': request, 'raw_response': response}}

    @app.post('/api/v1/evidence/analyze')
    def analyze(body: AnalysisRequest):
        mapping = store().get(body.mapping_id, body.mapping_version, 'mapping')
        dataset = store().get(mapping['dataset_id'], kind='dataset')
        spec = store().get(body.spec_id, body.spec_version, 'spec')
        row = next((r for r in dataset['rows'] if r['row_id'] == body.row_id), None)
        if row is None:
            raise ValueError('请选择当前数据集的一条样本。')
        values = role_values(mapping, row)
        reports = [{'criterion_id': c['id'], 'method': c['method'], 'required_roles': c['required_roles'],
                    'missing_roles': missing_roles(c, values), 'available_fields': {k: list(v) for k, v in values.items()},
                    'risk': '仅检查当前行的角色内容是否存在；未验证依据正确性及语义充分性。'} for c in spec['criteria'] if c['enabled']]
        request = make_request('evidence_analysis', '分析评测所需依据和局限，返回 JSON。不可将字段存在等同于证据充分。', {'criteria': spec['criteria'], 'fields': values})
        response = MockGateway().invoke(request, body.mock_response if body.mock_response is not None else json.dumps({'advice': '模拟建议：核对标准范围，补充参考依据，人工确认后再试评。'}, ensure_ascii=False))
        try:
            advice = json.loads(response['choices'][0]['message']['content'])
        except Exception as exc:
            return JSONResponse(status_code=400, content={'detail': f'模拟分析响应不是合法 JSON：{exc}', 'trace': {'request': request, 'raw_response': response}})
        return {'reports': reports, 'advice': advice, 'is_mock': True, 'trace': {'request': request, 'raw_response': response}}

    @app.post('/api/v1/evaluators/build')
    def build_evaluator(body: BuildRequest):
        return default_evaluator(store().get(body.spec_id, body.spec_version, 'spec'))

    @app.post('/api/v1/evaluators')
    def save_evaluator(body: EvaluatorSave):
        spec = store().get(body.spec_id, body.spec_version, 'spec')
        check_schema(body.response_schema)
        if body.response_schema.get('type') != 'object':
            raise ValueError('评测响应 Schema 的顶层 type 必须为 object。')
        ids = {c['id'] for c in spec['criteria'] if c['enabled']}
        projected = [p.criterion_id for p in body.projection]
        if len(set(projected)) != len(projected) or any(c not in ids for c in projected):
            raise ValueError('结果投影的标准 ID 必须存在、启用且不重复。')
        for projection in body.projection:
            for path in [projection.verdict_path, projection.reason_path, projection.evidence_path]:
                if path and not path.startswith('/'):
                    raise ValueError('投影路径必须使用 JSON Pointer（如 /criteria/C001/verdict）。')
            if not projection.verdict_path:
                raise ValueError('判定路径不能为空。')
        if body.id:
            store().get(body.id, kind='evaluator')
        return store().save('evaluator', body.model_dump(exclude={'id'}), body.id)

    @app.post('/api/v1/requests/preview')
    def preview(body: PreviewRequest):
        dataset, mapping, spec, evaluator = context(body.mapping_id, body.mapping_version, body.evaluator_id, body.evaluator_version)
        row = next((r for r in dataset['rows'] if r['row_id'] == body.row_id), None)
        if row is None:
            raise ValueError('样本不属于当前数据集。')
        return {'request': judge_request(mapping, spec, evaluator, row), 'row': row,
                'versions': {'mapping': mapping['version'], 'spec': spec['version'], 'evaluator': evaluator['version']}}

    @app.post('/api/v1/runs')
    def create_run(body: RunRequest):
        dataset, mapping, spec, evaluator = context(body.mapping_id, body.mapping_version, body.evaluator_id, body.evaluator_version)
        available = [row['row_id'] for row in dataset['rows']]
        if body.selection_mode == 'all':
            if body.row_ids:
                raise ValueError('全量模式不能同时指定 row_ids。')
            selected = available
        else:
            if not body.row_ids or len(body.row_ids) != len(set(body.row_ids)) or any(r not in available for r in body.row_ids):
                raise ValueError('请明确选择有效且不重复的样本 ID。')
            selected = [r for r in available if r in set(body.row_ids)]
        return enqueue({'dataset': dataset, 'mapping': mapping, 'spec': spec, 'evaluator': evaluator}, selected)

    @app.get('/api/v1/runs/{run_id}')
    def get_run(run_id: str):
        run = store().get_run(run_id)
        # Dataset rows are included only in the detail endpoint, never in catalog listings.
        run['summary'] = summary(run)
        payload_fields = {}
        for record in run['records']:
            if record['attempts'] and record['attempts'][-1]['parsed'] is not None:
                payload_fields.update(dict.fromkeys(flatten(record['attempts'][-1]['parsed'])))
        run['export_fields'] = list(payload_fields)
        run['validation_case'] = store().get(run['validation_case_id'], kind='case') if run.get('validation_case_id') else None
        return run

    @app.post('/api/v1/runs/{run_id}/cancel')
    def cancel_run(run_id: str):
        def update(run):
            if run['status'] in BUSY:
                run.update(cancel_requested=True, status='cancelling')
        return run_meta(store().update_run(run_id, update))

    @app.post('/api/v1/runs/{run_id}/retry')
    def retry_run(run_id: str):
        def update(run):
            if run['status'] in BUSY:
                raise ValueError('运行中的任务不能重试。')
            failed = [r for r in run['records'] if r['status'] != 'completed']
            if not failed:
                raise ValueError('没有需要重试的记录。')
            for record in failed:
                record['status'] = 'pending'
            run.update(status='queued', cancel_requested=False, finished_at=None, error=None)
        run = store().update_run(run_id, update)
        app.state.runner.queue.put(run_id)
        return run_meta(run)

    @app.post('/api/v1/runs/{run_id}/rerun')
    def rerun(run_id: str, body: RetryRequest):
        run = store().get_run(run_id)
        snapshot = copy.deepcopy(run['snapshot'])
        if not body.evaluator_id or not body.evaluator_version:
            raise ValueError('请选择复测使用的评测器版本。')
        dataset, mapping, spec, evaluator = context(snapshot['mapping']['id'], snapshot['mapping']['version'], body.evaluator_id, body.evaluator_version)
        snapshot.update(spec=spec, evaluator=evaluator)
        return enqueue(snapshot, [r['row_id'] for r in run['records']], parent_run_id=run_id)

    @app.post('/api/v1/runs/{run_id}/rows/{row_id}/review')
    def review(run_id: str, row_id: str, body: ReviewSave):
        def update(run):
            if run['status'] in BUSY:
                raise ValueError('请在运行结束后进行复核。')
            record = next((r for r in run['records'] if r['row_id'] == row_id), None)
            if not record or not record['attempts']:
                raise ValueError('该记录尚无可复核结果。')
            if body.criterion_id not in {c['id'] for c in run['snapshot']['spec']['criteria'] if c['enabled']}:
                raise ValueError('标准不属于本次运行。')
            if body.save_case and run['is_mock'] and not body.confirm_mock:
                raise ValueError('保存回归案例前，请明确确认人工判定；模拟结果本身不是正确答案。')
            revision = {'id': uid('review'), 'created_at': now(), 'attempt_id': record['attempts'][-1]['id'],
                        'criterion_id': body.criterion_id, 'verdict': body.verdict, 'reason': body.reason,
                        'source': 'human', 'source_run_is_mock': run['is_mock']}
            record['reviews'].append(revision)
            if body.save_case:
                case = store().save('case', {'run_id': run_id, 'row_id': row_id, 'criterion_id': body.criterion_id,
                    'expected': body.verdict, 'reason': body.reason, 'review_id': revision['id'],
                    'spec_id': run['snapshot']['spec']['id'], 'spec_version': run['snapshot']['spec']['version'],
                    'confirmed_by_human': True, 'source_run_is_mock': run['is_mock']})
                revision['case_id'] = case['id']
        return store().update_run(run_id, update)

    @app.post('/api/v1/cases/{case_id}/rerun')
    def rerun_case(case_id: str, body: RetryRequest):
        case = store().get(case_id, kind='case')
        original_run = store().get_run(case['run_id'])
        if not body.evaluator_id or not body.evaluator_version:
            raise ValueError('请选择复测评测器。')
        snapshot = copy.deepcopy(original_run['snapshot'])
        _, _, spec, evaluator = context(snapshot['mapping']['id'], snapshot['mapping']['version'], body.evaluator_id, body.evaluator_version)
        if spec['id'] != case['spec_id']:
            raise ValueError('回归案例必须使用同一评测标准方案的版本；不同任务请重新建立人工案例。')
        if case['criterion_id'] not in {c['id'] for c in spec['criteria'] if c['enabled']}:
            raise ValueError('新标准中没有此案例的标准 ID，需重新确认案例适用性。')
        snapshot.update(spec=spec, evaluator=evaluator)
        return enqueue(snapshot, [case['row_id']], original_run['id'], case_id)

    @app.post('/api/v1/runs/{run_id}/export')
    def export(run_id: str, body: ExportRequest):
        run = store().get_run(run_id)
        if run['status'] in BUSY:
            raise ValueError('请等待任务结束后导出。')
        columns, rows, _ = export_table(run, body.fields)
        folder = store().root / 'runs' / run_id
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f'{uid("export")}.xlsx'
        write_xlsx(target, rows, columns)
        manifest = {**run, 'export': {'columns': columns, 'created_at': now(), 'file': target.name}}
        target.with_suffix('.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        return FileResponse(target, filename=f'{run_id}_mock.xlsx', media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    frontend = REPO_ROOT / 'frontend'

    @app.get('/')
    def index():
        if (frontend / 'index.html').exists():
            return FileResponse(frontend / 'index.html', headers={'Cache-Control': 'no-cache'})
        return JSONResponse(status_code=503, content={'message': '缺少页面文件，请复制完整项目目录（包括 frontend/）。', 'api_docs': '/docs'})

    if frontend.is_dir():
        app.mount('/static', StaticFiles(directory=frontend), name='static')
    return app


app = create_app()
