"""Revision 4: independent standards and bounded group tasks. No migrations."""
import json

REVISION = 4
DATABASE = 'workbench_v1_1.sqlite3'
DDL = '''
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entities(project_id TEXT NOT NULL,id TEXT NOT NULL,
    version INTEGER NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,summary TEXT NOT NULL,
    PRIMARY KEY(project_id,id,version));
CREATE INDEX IF NOT EXISTS entities_project_kind ON entities(project_id,kind,id,version);
CREATE TABLE IF NOT EXISTS materials(project_id TEXT NOT NULL,id TEXT NOT NULL,
    fingerprint TEXT NOT NULL, row_count INTEGER NOT NULL, columns_json TEXT NOT NULL,
    PRIMARY KEY(project_id,id),UNIQUE(project_id,fingerprint));
CREATE TABLE IF NOT EXISTS data_rows(project_id TEXT NOT NULL,material_id TEXT NOT NULL,
    row_id TEXT NOT NULL,source_order INTEGER NOT NULL,source_row INTEGER NOT NULL,body TEXT NOT NULL,
    PRIMARY KEY(project_id,material_id,row_id),UNIQUE(project_id,material_id,source_order));
CREATE TABLE IF NOT EXISTS runs(project_id TEXT NOT NULL,id TEXT NOT NULL,body TEXT NOT NULL,summary TEXT NOT NULL,
    status TEXT NOT NULL,material_id TEXT NOT NULL,current_round INTEGER NOT NULL DEFAULT 0,
    selected INTEGER NOT NULL DEFAULT 0,processed INTEGER NOT NULL DEFAULT 0,
    running INTEGER NOT NULL DEFAULT 0,pending INTEGER NOT NULL DEFAULT 0,failed INTEGER NOT NULL DEFAULT 0,
    succeeded INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL,
    rows_selected INTEGER NOT NULL DEFAULT 0,rows_succeeded INTEGER NOT NULL DEFAULT 0,
    rows_running INTEGER NOT NULL DEFAULT 0,rows_pending INTEGER NOT NULL DEFAULT 0,rows_failed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(project_id,id));
CREATE INDEX IF NOT EXISTS runs_project_status ON runs(project_id,status);
CREATE TABLE IF NOT EXISTS run_records(project_id TEXT NOT NULL,run_id TEXT NOT NULL,row_id TEXT NOT NULL,
    source_order INTEGER NOT NULL,source_row INTEGER NOT NULL,status TEXT NOT NULL,
    PRIMARY KEY(project_id,run_id,row_id));
CREATE INDEX IF NOT EXISTS records_order ON run_records(project_id,run_id,source_order);
CREATE INDEX IF NOT EXISTS records_status ON run_records(project_id,run_id,status,source_order);
CREATE TABLE IF NOT EXISTS group_tasks(project_id TEXT NOT NULL,run_id TEXT NOT NULL,row_id TEXT NOT NULL,
    group_id TEXT NOT NULL,group_order INTEGER NOT NULL,source_order INTEGER NOT NULL,status TEXT NOT NULL,round_no INTEGER NOT NULL,
    latest_attempt_id TEXT,attempt_count INTEGER NOT NULL DEFAULT 0,auto_retry_count INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '{}',error_stage TEXT,
    PRIMARY KEY(project_id,run_id,row_id,group_id));
CREATE INDEX IF NOT EXISTS tasks_pending ON group_tasks(project_id,run_id,status,source_order,group_order);
CREATE INDEX IF NOT EXISTS tasks_errors ON group_tasks(project_id,run_id,group_id,error_stage,row_id);
CREATE TABLE IF NOT EXISTS attempts(project_id TEXT NOT NULL,run_id TEXT NOT NULL,row_id TEXT NOT NULL,
    group_id TEXT NOT NULL,id TEXT NOT NULL,ordinal INTEGER NOT NULL,round_no INTEGER,body TEXT NOT NULL,
    PRIMARY KEY(project_id,id),UNIQUE(project_id,run_id,row_id,group_id,ordinal));
CREATE INDEX IF NOT EXISTS attempts_record ON attempts(project_id,run_id,row_id,ordinal);
CREATE TABLE IF NOT EXISTS attempt_metrics(project_id TEXT NOT NULL,id TEXT NOT NULL,body TEXT NOT NULL,
    PRIMARY KEY(project_id,id));
CREATE TABLE IF NOT EXISTS run_rounds(project_id TEXT NOT NULL,run_id TEXT NOT NULL,number INTEGER NOT NULL,
    status TEXT NOT NULL,created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,kind TEXT NOT NULL DEFAULT 'initial',
    selected INTEGER NOT NULL,processed INTEGER NOT NULL DEFAULT 0,running INTEGER NOT NULL DEFAULT 0,
    pending INTEGER NOT NULL,failed INTEGER NOT NULL DEFAULT 0,succeeded INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,PRIMARY KEY(project_id,run_id,number));
CREATE TABLE IF NOT EXISTS object_refs(project_id TEXT NOT NULL,owner_id TEXT NOT NULL,
    owner_kind TEXT NOT NULL,owner_version INTEGER NOT NULL,target_id TEXT NOT NULL,target_kind TEXT NOT NULL,
    PRIMARY KEY(project_id,owner_id,owner_version,target_id,target_kind));
CREATE INDEX IF NOT EXISTS references_target ON object_refs(project_id,target_id,target_kind);
CREATE TABLE IF NOT EXISTS lifecycle(project_id TEXT NOT NULL,id TEXT NOT NULL,archived_at TEXT,
    PRIMARY KEY(project_id,id));
CREATE TABLE IF NOT EXISTS exports(project_id TEXT NOT NULL,id TEXT NOT NULL,run_id TEXT NOT NULL,body TEXT NOT NULL,
    PRIMARY KEY(project_id,id));
CREATE INDEX IF NOT EXISTS exports_run ON exports(project_id,run_id);
CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT,object_id TEXT NOT NULL,
    created_at TEXT NOT NULL,action TEXT NOT NULL,details TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS audit_project ON audit(project_id,seq);
'''


def install(db):
    # executescript commits implicitly; individual statements keep initialization transactional.
    for statement in DDL.split(';'):
        if statement.strip():
            db.execute(statement)


def entity_summary(value):
    if value['kind'] == 'mapping':
        return dict(value)
    if value['kind'] == 'dataset':
        keys = ('id', 'project_id', 'kind', 'version', 'filename', 'row_count', 'created_at')
        return {k: value[k] for k in keys}
    keys = ('id', 'project_id', 'kind', 'version', 'title', 'created_at', 'status', 'purpose',
            'name', 'method', 'draft')
    return {k: value[k] for k in keys if k in value}


def references(value):
    kind = value.get('kind', 'run')
    refs = []
    if kind == 'dataset' and value.get('material_id'):
        refs.append(('material', value['material_id']))
    if kind == 'mapping':
        refs.append(('dataset', value['dataset_id']))
    elif kind == 'execution_plan':
        refs.extend(('standard', r['standard_id']) for g in value['groups'] for r in g['standards'])
    elif kind == 'standard_test':
        refs.extend((r['kind'], r['id']) for r in value.get('references', []))
    elif kind == 'run':
        snap = value['snapshot']
        refs.extend((k, snap[key]['id']) for k, key in (('dataset','dataset'),('execution_plan','plan')))
        refs.extend(('mapping', g['mapping']['id']) for g in snap['groups'] if g['mapping'].get('id'))
        refs.extend(('standard', s['id']) for g in snap['groups'] for s in g['standards'])
        if value.get('parent_run_id'):
            refs.append(('run', value['parent_run_id']))
        refs.append(('material', value['snapshot']['dataset']['material_id']))
    return set(refs)


def save_references(db, value):
    for kind, target in references(value):
        db.execute('INSERT OR IGNORE INTO object_refs VALUES(?,?,?,?,?,?)',
                   (value['project_id'], value['id'], value.get('kind', 'run'), value.get('version', 0), target, kind))


def attempt_summary(attempt):
    keys = ('id', 'round_no', 'status', 'criteria', 'output', 'error', 'error_stage', 'finished_at')
    return {k: attempt.get(k) for k in keys}


def run_summary(value):
    result = {k: v for k, v in value.items() if k != 'snapshot'}
    snap = value['snapshot']
    result['snapshot'] = {'dataset': {'filename': snap['dataset']['filename']},
                          'execution_config': snap.get('execution_config', {'batch_concurrency': 1})}
    return result


def counts(status):
    return {'processed': int(status not in ('pending', 'running')), 'running': int(status == 'running'),
            'pending': int(status == 'pending'), 'failed': int(status.endswith('_failed') or status == 'interrupted'),
            'succeeded': int(status == 'completed')}
