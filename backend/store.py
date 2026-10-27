import copy
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return f'{prefix}_{uuid4().hex[:16]}'


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / 'workbench.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL,
                body TEXT NOT NULL, PRIMARY KEY(id, version));
            CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS lifecycle (id TEXT PRIMARY KEY, archived_at TEXT);
            CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL, action TEXT NOT NULL, object_id TEXT NOT NULL, details TEXT NOT NULL);
        ''')
        self.db.commit()

    def event(self, action, object_id, details=None):
        with self.lock:
            self.db.execute('INSERT INTO audit(created_at,action,object_id,details) VALUES (?,?,?,?)',
                            (now(), action, object_id, json.dumps(details or {}, ensure_ascii=False)))
            self.db.commit()

    def archived_at(self, object_id):
        with self.lock:
            row = self.db.execute('SELECT archived_at FROM lifecycle WHERE id=?', (object_id,)).fetchone()
        return row[0] if row else None

    def ensure_active(self, object_id):
        if self.archived_at(object_id):
            raise ValueError('对象已归档，请先在历史管理中恢复：' + object_id)

    def all_entities(self):
        with self.lock:
            rows = self.db.execute('SELECT body FROM entities').fetchall()
        return [json.loads(row[0]) for row in rows]

    def events(self, object_id=None):
        with self.lock:
            rows = self.db.execute('SELECT created_at,action,object_id,details FROM audit' +
                (' WHERE object_id=?' if object_id else '') + ' ORDER BY seq DESC LIMIT 500', (object_id,) if object_id else ()).fetchall()
        return [{'created_at': row[0], 'action': row[1], 'object_id': row[2], 'details': json.loads(row[3])} for row in rows]

    def save(self, kind, body, entity_id=None):
        with self.lock:
            entity_id = entity_id or uid(kind)
            self.ensure_active(entity_id)
            # Validate links under the same lock used by permanent deletion.
            links = []
            if kind == 'mapping':
                links.append(('dataset', body['dataset_id']))
            elif kind == 'evaluator':
                links.append(('spec', body['spec_id']))
                self.get(body['spec_id'], body['spec_version'], 'spec')
            elif kind == 'case':
                self.get_run(body['run_id'])
                self.ensure_active(body['run_id'])
                links.append(('spec', body['spec_id']))
            elif kind == 'spec':
                links.extend(('design_call', call_id) for call_id in set(body.get('design_call_ids', []) +
                    [t['design_call_id'] for t in body.get('design_traces', []) if t.get('design_call_id')]))
            elif kind == 'design_call':
                links.extend((ref['kind'], ref['id']) for ref in body.get('references', []))
            for linked_kind, linked_id in links:
                self.get(linked_id, kind=linked_kind)
                # Finishing an in-flight design call may retain an archived input.
                if kind != 'design_call' or not body.get('finished_at'):
                    self.ensure_active(linked_id)
            existing = self.db.execute('SELECT kind, MAX(version) FROM entities WHERE id=?', (entity_id,)).fetchone()
            if existing[0] is not None and existing[0] != kind:
                raise ValueError('对象类型不匹配。')
            version = (existing[1] or 0) + 1
            value = {**copy.deepcopy(body), 'id': entity_id, 'kind': kind, 'version': version, 'created_at': now()}
            self.db.execute('INSERT INTO entities VALUES (?,?,?,?)', (entity_id, version, kind, json.dumps(value, ensure_ascii=False)))
            self.db.commit()
            self.event('save', entity_id, {'kind': kind, 'version': version})
            return value

    def get(self, entity_id, version=None, kind=None):
        with self.lock:
            if version is None:
                row = self.db.execute('SELECT body FROM entities WHERE id=? ORDER BY version DESC LIMIT 1', (entity_id,)).fetchone()
            else:
                row = self.db.execute('SELECT body FROM entities WHERE id=? AND version=?', (entity_id, version)).fetchone()
        if not row:
            raise KeyError('记录或版本不存在。')
        value = json.loads(row[0])
        if kind and value['kind'] != kind:
            raise ValueError('对象类型不匹配。')
        value['archived_at'] = self.archived_at(entity_id)
        return value

    def list(self, kind, include_archived=False):
        with self.lock:
            rows = self.db.execute('SELECT e.body FROM entities e JOIN (SELECT id,MAX(version) v FROM entities GROUP BY id) latest ON e.id=latest.id AND e.version=latest.v WHERE kind=? ORDER BY e.rowid DESC', (kind,)).fetchall()
        values = [json.loads(row[0]) for row in rows]
        for value in values:
            value['archived_at'] = self.archived_at(value['id'])
        return values if include_archived else [value for value in values if not value['archived_at']]

    def versions(self, entity_id):
        with self.lock:
            rows = self.db.execute('SELECT body FROM entities WHERE id=? ORDER BY version DESC', (entity_id,)).fetchall()
        return [{**json.loads(row[0]), 'archived_at': self.archived_at(entity_id)} for row in rows]

    def put_run(self, value):
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO runs VALUES (?,?)', (value['id'], json.dumps(value, ensure_ascii=False)))
            self.db.commit()

    def get_run(self, run_id):
        with self.lock:
            row = self.db.execute('SELECT body FROM runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise KeyError('运行不存在。')
        value = json.loads(row[0])
        value['archived_at'] = self.archived_at(run_id)
        return value

    def update_run(self, run_id, update):
        with self.lock:
            value = self.get_run(run_id)
            update(value)
            self.put_run(value)
            return value

    def runs(self, include_archived=False):
        with self.lock:
            rows = self.db.execute('SELECT body FROM runs ORDER BY rowid DESC').fetchall()
        values = [json.loads(row[0]) for row in rows]
        for value in values:
            value['archived_at'] = self.archived_at(value['id'])
        return values if include_archived else [value for value in values if not value['archived_at']]

    def close(self):
        self.db.close()
