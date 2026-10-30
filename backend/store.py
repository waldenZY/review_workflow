"""The v1 database is independent of v0. Every business lookup requires a project."""
import copy
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

BUSY = ('queued', 'running', 'cancelling', 'design_running')
KINDS = ('dataset', 'mapping', 'spec', 'evaluator', 'design_call')


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return f'{prefix}_{uuid4().hex}'


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Conflict(ValueError):
    def __init__(self, message, references=None):
        super().__init__(message)
        self.references = references or []


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / 'workbench_v1.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS entities(project_id TEXT NOT NULL,id TEXT NOT NULL,
                version INTEGER NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,
                PRIMARY KEY(project_id,id,version));
            CREATE INDEX IF NOT EXISTS entities_project_kind ON entities(project_id,kind);
            CREATE TABLE IF NOT EXISTS runs(project_id TEXT NOT NULL,id TEXT NOT NULL,body TEXT NOT NULL,
                PRIMARY KEY(project_id,id));
            CREATE TABLE IF NOT EXISTS lifecycle(project_id TEXT NOT NULL,id TEXT NOT NULL,
                archived_at TEXT,PRIMARY KEY(project_id,id));
            CREATE TABLE IF NOT EXISTS exports(project_id TEXT NOT NULL,id TEXT NOT NULL,
                run_id TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(project_id,id));
            CREATE INDEX IF NOT EXISTS exports_run ON exports(project_id,run_id);
            CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT,object_id TEXT NOT NULL,created_at TEXT NOT NULL,
                action TEXT NOT NULL,details TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS audit_project ON audit(project_id,seq);
        ''')
        self.db.commit()

    def event(self, project_id, object_id, action, details=None):
        with self.lock:
            self.db.execute('INSERT INTO audit(project_id,object_id,created_at,action,details) VALUES(?,?,?,?,?)',
                            (project_id, object_id, now(), action, dumps(details or {})))
            self.db.commit()

    def project(self, project_id, active=False):
        with self.lock:
            row = self.db.execute('SELECT body FROM projects WHERE id=?', (project_id,)).fetchone()
            if not row:
                raise KeyError('项目不存在。')
            value = json.loads(row[0])
            if active and value.get('archived_at'):
                raise Conflict('项目已归档，请先恢复。')
            return value

    def touch(self, project_id):
        value = self.project(project_id)
        value['updated_at'] = now()
        self.db.execute('UPDATE projects SET body=? WHERE id=?', (dumps(value), project_id))

    def save_project(self, body, project_id=None):
        with self.lock:
            value = self.project(project_id, active=True) if project_id else {
                'id': uid('project'), 'created_at': now(), 'archived_at': None}
            value.update(body, updated_at=now())
            self.db.execute('INSERT OR REPLACE INTO projects VALUES(?,?)', (value['id'], dumps(value)))
            self.db.commit()
            self.event(value['id'], value['id'], 'project_saved')
            return value

    def projects(self, include_archived=False, query=''):
        with self.lock:
            values = [json.loads(r[0]) for r in self.db.execute('SELECT body FROM projects')]
            result = []
            for value in values:
                if (not include_archived and value.get('archived_at')) or query.lower() not in (value['name'] + ' ' + value['description']).lower():
                    continue
                counts = {k: 0 for k in KINDS}
                for kind, count in self.db.execute('SELECT kind,COUNT(DISTINCT id) FROM entities WHERE project_id=? GROUP BY kind', (value['id'],)):
                    counts[kind] = count
                counts['run'] = self.db.execute('SELECT COUNT(*) FROM runs WHERE project_id=?', (value['id'],)).fetchone()[0]
                result.append({**value, 'counts': counts})
            return sorted(result, key=lambda p: p['updated_at'], reverse=True)

    def archived_at(self, project_id, object_id):
        with self.lock:
            row = self.db.execute('SELECT archived_at FROM lifecycle WHERE project_id=? AND id=?', (project_id, object_id)).fetchone()
            return row[0] if row else None

    def writable(self, project_id, object_id=None):
        self.project(project_id, active=True)
        if object_id and self.archived_at(project_id, object_id):
            raise Conflict('对象已归档，请先恢复。')

    def get(self, project_id, object_id, version=None, kind=None, active=False):
        with self.lock:
            self.project(project_id)
            sql = 'SELECT body FROM entities WHERE project_id=? AND id=?'
            args = [project_id, object_id]
            if version is not None:
                sql += ' AND version=?'
                args.append(version)
            row = self.db.execute(sql + ' ORDER BY version DESC LIMIT 1', args).fetchone()
            if not row:
                raise KeyError('对象或版本不属于当前项目，或已删除。')
            value = json.loads(row[0])
            if kind and value['kind'] != kind:
                raise ValueError('对象类型不匹配。')
            value['archived_at'] = self.archived_at(project_id, object_id)
            if active:
                self.writable(project_id, object_id)
            return value

    def versions(self, project_id, object_id):
        with self.lock:
            self.get(project_id, object_id)
            rows = self.db.execute('SELECT body FROM entities WHERE project_id=? AND id=? ORDER BY version DESC', (project_id, object_id))
            return [{**json.loads(r[0]), 'archived_at': self.archived_at(project_id, object_id)} for r in rows]

    def entities(self, project_id, kind=None, include_archived=False, all_versions=False):
        with self.lock:
            self.project(project_id)
            rows = self.db.execute('SELECT body FROM entities WHERE project_id=? ORDER BY rowid DESC', (project_id,)).fetchall()
            seen, values = set(), []
            for row in rows:
                v = json.loads(row[0])
                if kind and v['kind'] != kind:
                    continue
                if not all_versions and v['id'] in seen:
                    continue
                seen.add(v['id'])
                v['archived_at'] = self.archived_at(project_id, v['id'])
                if include_archived or not v['archived_at']:
                    values.append(v)
            return values

    def save(self, project_id, kind, body, object_id=None, new_id=None):
        with self.lock:
            if kind not in KINDS:
                raise ValueError('未知对象类型。')
            self.writable(project_id, object_id)
            if object_id:
                self.get(project_id, object_id, kind=kind)
            refs = []
            if kind == 'mapping':
                refs.append(('dataset', body['dataset_id'], None))
                if object_id and self.get(project_id, object_id)['dataset_id'] != body['dataset_id']:
                    raise ValueError('字段配置不能更换所属数据集。')
            if kind == 'evaluator':
                refs.append(('spec', body['spec_id'], body['spec_version']))
            if kind == 'spec':
                refs.extend(('design_call', i, None) for i in body.get('design_call_ids', []))
            if kind == 'design_call':
                refs.extend((r['kind'], r['id'], r.get('version')) for r in body.get('references', []))
            for ref_kind, ref_id, ref_version in refs:
                self.get(project_id, ref_id, ref_version, ref_kind, active=True)
            object_id = object_id or new_id or uid(kind)
            row = self.db.execute('SELECT MAX(version) FROM entities WHERE project_id=? AND id=?', (project_id, object_id)).fetchone()
            value = {**copy.deepcopy(body), 'id': object_id, 'project_id': project_id, 'kind': kind,
                     'version': (row[0] or 0) + 1, 'created_at': now()}
            value.pop('archived_at', None)
            self.db.execute('INSERT INTO entities VALUES(?,?,?,?,?)', (project_id, object_id, value['version'], kind, dumps(value)))
            self.touch(project_id)
            self.db.commit()
            self.event(project_id, object_id, 'saved', {'kind': kind, 'version': value['version']})
            return value

    def get_run(self, project_id, run_id, active=False):
        with self.lock:
            self.project(project_id)
            row = self.db.execute('SELECT body FROM runs WHERE project_id=? AND id=?', (project_id, run_id)).fetchone()
            if not row:
                raise KeyError('运行不属于当前项目，或已删除。')
            value = json.loads(row[0])
            value['archived_at'] = self.archived_at(project_id, run_id)
            if active:
                self.writable(project_id, run_id)
            return value

    def put_run(self, value):
        with self.lock:
            self.project(value['project_id'])
            self.db.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?)', (value['project_id'], value['id'], dumps(value)))
            self.touch(value['project_id'])
            self.db.commit()

    def update_run(self, project_id, run_id, update):
        with self.lock:
            value = self.get_run(project_id, run_id)
            update(value)
            self.put_run(value)
            return value

    def runs(self, project_id, include_archived=False):
        with self.lock:
            self.project(project_id)
            values = [json.loads(r[0]) for r in self.db.execute('SELECT body FROM runs WHERE project_id=? ORDER BY rowid DESC', (project_id,))]
            for value in values:
                value['archived_at'] = self.archived_at(project_id, value['id'])
            return values if include_archived else [v for v in values if not v['archived_at']]

    def events(self, project_id, object_id=None):
        with self.lock:
            self.project(project_id)
            sql = 'SELECT object_id,created_at,action,details FROM audit WHERE project_id=?'
            args = [project_id]
            if object_id:
                sql += ' AND object_id=?'
                args.append(object_id)
            return [{'object_id': r[0], 'created_at': r[1], 'action': r[2], 'details': json.loads(r[3])}
                    for r in self.db.execute(sql + ' ORDER BY seq DESC LIMIT 500', args)]

    def register_export(self, project_id, run_id, body):
        with self.lock:
            self.get_run(project_id, run_id)
            value = {**body, 'id': uid('export'), 'project_id': project_id, 'run_id': run_id, 'created_at': now()}
            self.db.execute('INSERT INTO exports VALUES(?,?,?,?)', (project_id, value['id'], run_id, dumps(value)))
            self.touch(project_id)
            self.db.commit()
            self.event(project_id, run_id, 'exported', value)
            return value

    def exports(self, project_id, run_id):
        with self.lock:
            self.get_run(project_id, run_id)
            return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM exports WHERE project_id=? AND run_id=?', (project_id, run_id))]

    def close(self):
        with self.lock:
            self.db.close()
