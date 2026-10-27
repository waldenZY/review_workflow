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
        ''')
        self.db.commit()

    def save(self, kind, body, entity_id=None):
        with self.lock:
            entity_id = entity_id or uid(kind)
            existing = self.db.execute('SELECT kind, MAX(version) FROM entities WHERE id=?', (entity_id,)).fetchone()
            if existing[0] is not None and existing[0] != kind:
                raise ValueError('对象类型不匹配。')
            version = (existing[1] or 0) + 1
            value = {**copy.deepcopy(body), 'id': entity_id, 'kind': kind, 'version': version, 'created_at': now()}
            self.db.execute('INSERT INTO entities VALUES (?,?,?,?)', (entity_id, version, kind, json.dumps(value, ensure_ascii=False)))
            self.db.commit()
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
        return value

    def list(self, kind):
        with self.lock:
            rows = self.db.execute('SELECT e.body FROM entities e JOIN (SELECT id,MAX(version) v FROM entities GROUP BY id) latest ON e.id=latest.id AND e.version=latest.v WHERE kind=? ORDER BY e.rowid DESC', (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def versions(self, entity_id):
        with self.lock:
            rows = self.db.execute('SELECT body FROM entities WHERE id=? ORDER BY version DESC', (entity_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def put_run(self, value):
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO runs VALUES (?,?)', (value['id'], json.dumps(value, ensure_ascii=False)))
            self.db.commit()

    def get_run(self, run_id):
        with self.lock:
            row = self.db.execute('SELECT body FROM runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise KeyError('运行不存在。')
        return json.loads(row[0])

    def update_run(self, run_id, update):
        with self.lock:
            value = self.get_run(run_id)
            update(value)
            self.put_run(value)
            return value

    def runs(self):
        with self.lock:
            rows = self.db.execute('SELECT body FROM runs ORDER BY rowid DESC').fetchall()
        return [json.loads(row[0]) for row in rows]

    def close(self):
        self.db.close()
