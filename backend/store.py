"""Project storage: independent readers, one short-transaction writer, bounded row access."""
import copy
import hashlib
import json
import logging
import sqlite3
import threading
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .process_lock import ProcessLock
from .storage_schema import DATABASE, REVISION, entity_summary, install, references, run_summary, save_references

BUSY = ('queued', 'running', 'cancelling', 'design_running')
KINDS = ('dataset', 'mapping', 'standard', 'execution_plan', 'standard_test')
log = logging.getLogger(__name__)


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


def materialize(db, project_id, rows):
    """Import only; immutable materials include row identity and original ordering."""
    digest, columns = hashlib.sha256(), {}
    for row in rows:
        encoded = dumps(row).encode('utf-8')
        digest.update(len(encoded).to_bytes(8, 'big'))
        digest.update(encoded)
        columns.update(dict.fromkeys(row['original']))
    found = db.execute('SELECT id,columns_json FROM materials WHERE project_id=? AND fingerprint=?', (project_id, digest.hexdigest())).fetchone()
    if found:
        return found[0], json.loads(found[1])
    material_id = uid('material')
    db.execute('INSERT INTO materials VALUES(?,?,?,?,?)', (project_id, material_id, digest.hexdigest(), len(rows), dumps(list(columns))))
    db.executemany('INSERT INTO data_rows VALUES(?,?,?,?,?,?)',
                   ((project_id, material_id, r['row_id'], i, r['source_row'], dumps(r['original'])) for i, r in enumerate(rows)))
    return material_id, list(columns)


from .group_store import GroupRuns


class Store(GroupRuns):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.process_lock = ProcessLock(self.root).acquire()
        self.lock, self.local = threading.RLock(), threading.local()
        self.pending_metrics = OrderedDict()
        self.metrics_lock = threading.Lock()
        self.exporting = set()
        self.path = self.root / DATABASE
        try:
            self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if (version == 0 and tables) or version not in (0, REVISION):
                raise RuntimeError('不支持旧数据或未知存储版本。请停服、备份并自行清空业务数据目录后重新启动；不提供迁移，程序不会删除旧数据。')
            if version == REVISION:
                expected = sqlite3.connect(':memory:')
                try:
                    install(expected)
                    required = {r[0] for r in expected.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                    if required != tables or any(
                        self.db.execute(f'PRAGMA table_info("{table}")').fetchall()
                        != expected.execute(f'PRAGMA table_info("{table}")').fetchall()
                        for table in required
                    ):
                        raise RuntimeError('存储结构不完整或未知，拒绝自动重建。请检查备份和数据目录。')
                    schema_sql = "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
                    if self.db.execute(schema_sql).fetchall() != expected.execute(schema_sql).fetchall():
                        raise RuntimeError('存储约束或索引结构未知，拒绝自动重建。请检查备份和数据目录。')
                finally:
                    expected.close()
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA busy_timeout=5000')
            with self.write():
                install(self.db)
                self.db.execute(f'PRAGMA user_version={REVISION}')
        except Exception:
            if hasattr(self, 'db'):
                self.db.close()
            self.process_lock.close()
            raise

    @contextmanager
    def write(self):
        with self.lock:
            nested = bool(getattr(self.local, 'depth', 0))
            self.local.depth = getattr(self.local, 'depth', 0) + 1
            try:
                if not nested:
                    self.db.execute('BEGIN IMMEDIATE')
                yield self.db
                if not nested:
                    # Piggyback on a business transaction. Never commit solely for timing data.
                    with self.metrics_lock:
                        pending = tuple(self.pending_metrics.items())
                    flushed = ()
                    if pending:
                        self.db.execute('SAVEPOINT timing_metrics')
                        try:
                            for (project_id, attempt_id), metrics in pending:
                                self.db.execute('INSERT OR REPLACE INTO attempt_metrics(project_id,id,body) SELECT ?,?,? WHERE EXISTS (SELECT 1 FROM attempts WHERE project_id=? AND id=?)',
                                                (project_id, attempt_id, dumps(metrics), project_id, attempt_id))
                            self.db.execute('RELEASE timing_metrics')
                            flushed = pending
                        except sqlite3.Error:
                            self.db.execute('ROLLBACK TO timing_metrics')
                            self.db.execute('RELEASE timing_metrics')
                            log.warning('pending_metrics_deferred count=%s', len(pending))
                    self.db.commit()
                    # A rollback leaves all pending metrics available to the next business write.
                    with self.metrics_lock:
                        for key, metrics in flushed:
                            if self.pending_metrics.get(key) is metrics:
                                del self.pending_metrics[key]
            except Exception:
                if not nested:
                    self.db.rollback()
                raise
            finally:
                self.local.depth -= 1

    @contextmanager
    def read(self):
        if getattr(self.local, 'depth', 0):
            yield self.db
            return
        db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA busy_timeout=5000')
            db.execute('BEGIN')
            yield db
        finally:
            db.close()

    def event(self, project_id, object_id, action, details=None):
        with self.write() as db:
            db.execute('INSERT INTO audit(project_id,object_id,created_at,action,details) VALUES(?,?,?,?,?)', (project_id, object_id, now(), action, dumps(details or {})))

    def project(self, project_id, active=False):
        with self.read() as db:
            row = db.execute('SELECT body FROM projects WHERE id=?', (project_id,)).fetchone()
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
        with self.write() as db:
            value = self.project(project_id, True) if project_id else {'id': uid('project'), 'created_at': now(), 'archived_at': None}
            value.update(body, updated_at=now())
            db.execute('INSERT OR REPLACE INTO projects VALUES(?,?)', (value['id'], dumps(value)))
            self.event(value['id'], value['id'], 'project_saved')
        return value

    def projects(self, include_archived=False, query=''):
        with self.read() as db:
            values = [json.loads(r[0]) for r in db.execute('SELECT body FROM projects')]
            result = []
            for value in values:
                if (not include_archived and value.get('archived_at')) or query.lower() not in (value['name'] + ' ' + value['description']).lower():
                    continue
                totals = dict.fromkeys(KINDS, 0)
                totals.update(dict(db.execute('SELECT kind,COUNT(DISTINCT id) FROM entities WHERE project_id=? GROUP BY kind', (value['id'],))))
                totals['run'] = db.execute('SELECT COUNT(*) FROM runs WHERE project_id=?', (value['id'],)).fetchone()[0]
                result.append({**value, 'counts': totals})
        return sorted(result, key=lambda p: p['updated_at'], reverse=True)

    def archived_at(self, project_id, object_id):
        with self.read() as db:
            row = db.execute('SELECT archived_at FROM lifecycle WHERE project_id=? AND id=?', (project_id, object_id)).fetchone()
        return row[0] if row else None

    def writable(self, project_id, object_id=None):
        self.project(project_id, True)
        if object_id and self.archived_at(project_id, object_id):
            raise Conflict('对象已归档，请先恢复。')

    def get(self, project_id, object_id, version=None, kind=None, active=False):
        self.project(project_id)
        args = [project_id, object_id]
        sql = 'SELECT body FROM entities WHERE project_id=? AND id=?'
        if version is not None:
            sql += ' AND version=?'
            args.append(version)
        with self.read() as db:
            row = db.execute(sql + ' ORDER BY version DESC LIMIT 1', args).fetchone()
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
        self.get(project_id, object_id)
        with self.read() as db:
            values = [json.loads(r[0]) for r in db.execute('SELECT body FROM entities WHERE project_id=? AND id=? ORDER BY version DESC', (project_id, object_id))]
        archived = self.archived_at(project_id, object_id)
        return [{**v, 'archived_at': archived} for v in values]

    def entities(self, project_id, kind=None, include_archived=False, all_versions=False, summary=False):
        self.project(project_id)
        column = 'summary' if summary else 'body'
        sql = f'SELECT e.{column},l.archived_at FROM entities e LEFT JOIN lifecycle l ON l.project_id=e.project_id AND l.id=e.id WHERE e.project_id=?'
        args = [project_id]
        if kind:
            sql += ' AND e.kind=?'
            args.append(kind)
        if not all_versions:
            sql += ' AND e.version=(SELECT MAX(v.version) FROM entities v WHERE v.project_id=e.project_id AND v.id=e.id)'
        if not include_archived:
            sql += ' AND l.archived_at IS NULL'
        with self.read() as db:
            rows = db.execute(sql + ' ORDER BY e.rowid DESC', args).fetchall()
        return [{**json.loads(r[0]), 'archived_at': r[1]} for r in rows]

    def save(self, project_id, kind, body, object_id=None, new_id=None):
        if kind not in KINDS:
            raise ValueError('未知对象类型。')
        with self.write() as db:
            self.writable(project_id, object_id)
            if object_id:
                old = self.get(project_id, object_id, kind=kind)
                if kind == 'mapping' and old['dataset_id'] != body['dataset_id']:
                    raise ValueError('字段配置不能更换所属数据集。')
            value = copy.deepcopy(body)
            object_id = object_id or new_id or uid(kind)
            version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM entities WHERE project_id=? AND id=?', (project_id, object_id)).fetchone()[0]
            value.update(id=object_id, project_id=project_id, kind=kind, version=version, created_at=now())
            value.pop('archived_at', None)
            for ref_kind, ref_id in references(value):
                if ref_kind == 'material':
                    if not db.execute('SELECT 1 FROM materials WHERE project_id=? AND id=?', (project_id, ref_id)).fetchone():
                        raise ValueError('材料版本不存在。')
                    continue
                ref_version = None
                if kind == 'standard_test':
                    ref_version = next((r.get('version') for r in value.get('references', []) if r['kind'] == ref_kind and r['id'] == ref_id), None)
                self.get(project_id, ref_id, ref_version, ref_kind, True)
            if kind == 'dataset' and 'rows' in value:
                value['material_id'], value['original_columns'] = materialize(db, project_id, value.pop('rows'))
            db.execute('INSERT INTO entities VALUES(?,?,?,?,?,?)', (project_id, object_id, version, kind, dumps(value), dumps(entity_summary(value))))
            save_references(db, value)
            self.touch(project_id)
            self.event(project_id, object_id, 'saved', {'kind': kind, 'version': version})
        return value

    def data_page(self, project_id, material_id, offset=0, limit=20):
        with self.read() as db:
            rows = db.execute('SELECT row_id,source_row,body FROM data_rows WHERE project_id=? AND material_id=? ORDER BY source_order LIMIT ? OFFSET ?', (project_id, material_id, limit, offset)).fetchall()
        return [{'row_id': r[0], 'source_row': r[1], 'original': json.loads(r[2])} for r in rows]

    def data_row(self, project_id, material_id, row_id):
        with self.read() as db:
            row = db.execute('SELECT source_row,body FROM data_rows WHERE project_id=? AND material_id=? AND row_id=?', (project_id, material_id, row_id)).fetchone()
        if row is None:
            raise ValueError('样本不属于当前数据集。')
        return {'row_id': row_id, 'source_row': row[0], 'original': json.loads(row[1])}

    def validate_rows(self, project_id, material_id, row_ids, parent_run_id=None):
        if not row_ids or len(row_ids) != len(set(row_ids)):
            raise ValueError('请明确选择有效且不重复的样本。')
        with self.read() as db:
            for row_id in row_ids:
                if not db.execute('SELECT 1 FROM data_rows WHERE project_id=? AND material_id=? AND row_id=?', (project_id, material_id, row_id)).fetchone():
                    raise ValueError('样本不属于当前数据集。')
                if parent_run_id and not db.execute('SELECT 1 FROM run_records WHERE project_id=? AND run_id=? AND row_id=?', (project_id, parent_run_id, row_id)).fetchone():
                    raise ValueError('复测样本必须属于原运行。')

    def get_run(self, project_id, run_id, active=False, summary=False):
        self.project(project_id)
        with self.read() as db:
            column = 'summary' if summary else 'body'
            row = db.execute(f'SELECT {column},current_round,selected,processed,running,pending,failed,succeeded,updated_at FROM runs WHERE project_id=? AND id=?', (project_id, run_id)).fetchone()
            if not row:
                raise KeyError('运行不属于当前项目，或已删除。')
            current = db.execute('SELECT number,status,created_at,started_at,finished_at,selected,processed,running,pending,failed,succeeded,updated_at,kind FROM run_rounds WHERE project_id=? AND run_id=? AND number=?', (project_id, run_id, row[1])).fetchone()
            rows_progress=self.row_progress(db,project_id,run_id)
        value = json.loads(row[0])
        value.update(row_progress=rows_progress, current_round=row[1], progress=dict(zip(('selected', 'processed', 'running', 'pending', 'failed', 'succeeded'), row[2:8])),
                     updated_at=row[8], round_progress=self._round(current) if current else None,
                     archived_at=self.archived_at(project_id, run_id))
        if active:
            self.writable(project_id, run_id)
        return value

    @staticmethod
    def _round(row):
        return dict(zip(('number', 'status', 'created_at', 'started_at', 'finished_at', 'selected', 'processed', 'running', 'pending', 'failed', 'succeeded', 'updated_at', 'kind'), row))

    def runs(self, project_id, include_archived=False):
        self.project(project_id)
        with self.read() as db:
            rows = db.execute('SELECT r.summary,l.archived_at,r.current_round,r.selected,r.processed,r.running,r.pending,r.failed,r.succeeded,r.updated_at FROM runs r LEFT JOIN lifecycle l ON l.project_id=r.project_id AND l.id=r.id WHERE r.project_id=?' + ('' if include_archived else ' AND l.archived_at IS NULL') + ' ORDER BY r.rowid DESC', (project_id,)).fetchall()
        return [{**json.loads(r[0]), 'archived_at': r[1], 'current_round': r[2],
                 'progress': dict(zip(('selected', 'processed', 'running', 'pending', 'failed', 'succeeded'), r[3:9])),
                 'updated_at': r[9]} for r in rows]

    def update_run(self, project_id, run_id, update):
        """Rewrite only the small header, never material, row results or response bodies."""
        with self.write() as db:
            value = self.get_run(project_id, run_id)
            update(value)
            for key in ('progress', 'row_progress', 'round_progress', 'current_round', 'archived_at', 'updated_at'):
                value.pop(key, None)
            db.execute('UPDATE runs SET body=?,summary=?,status=?,updated_at=? WHERE project_id=? AND id=?', (dumps(value), dumps(run_summary(value)), value['status'], now(), project_id, run_id))
        return self.get_run(project_id, run_id)

    def start_run(self, project_id, run_id):
        with self.write() as db:
            run = self.update_run(project_id, run_id, lambda r: r.update(status='cancelling' if r.get('cancel_requested') else 'running', started_at=r.get('started_at') or now()))
            db.execute('UPDATE run_rounds SET status=?,started_at=COALESCE(started_at,?),updated_at=? WHERE project_id=? AND run_id=? AND number=?', (run['status'], now(), now(), project_id, run_id, run['current_round']))
        return run

    def cancel_run(self, project_id, run_id):
        with self.write() as db:
            run = self.get_run(project_id, run_id, True)
            if run['status'] not in BUSY:
                raise ValueError('任务已结束。')
            value = self.update_run(project_id, run_id, lambda r: r.update(cancel_requested=True, status='cancelling'))
            db.execute("UPDATE run_rounds SET status='cancelling',updated_at=? WHERE project_id=? AND run_id=? AND number=?", (now(), project_id, run_id, run['current_round']))
        return value

    def rounds(self, project_id, run_id):
        self.get_run(project_id, run_id)
        with self.read() as db:
            rows = db.execute('SELECT number,status,created_at,started_at,finished_at,selected,processed,running,pending,failed,succeeded,updated_at,kind FROM run_rounds WHERE project_id=? AND run_id=? ORDER BY number DESC', (project_id, run_id)).fetchall()
        return [self._round(r) for r in rows]

    def row_ids(self, project_id, run_id):
        with self.read() as db:
            return [r[0] for r in db.execute('SELECT row_id FROM run_records WHERE project_id=? AND run_id=? ORDER BY source_order', (project_id, run_id))]

    def assert_unleased(self, project_id, run_id=None):
        if any(p == project_id and (run_id is None or r == run_id) for p, r in self.exporting):
            raise Conflict('正在导出，请等待导出结束。')

    @contextmanager
    def export_lease(self, project_id, run_id):
        with self.lock:
            run = self.get_run(project_id, run_id)
            self.assert_unleased(project_id, run_id)
            if run['status'] in BUSY:
                raise Conflict('请在任务结束后导出。')
            self.exporting.add((project_id, run_id))
        try:
            yield run
        finally:
            with self.lock:
                self.exporting.discard((project_id, run_id))

    def reference_list(self, project_id, kind, object_id):
        with self.read() as db:
            rows = db.execute('SELECT DISTINCT owner_id,owner_kind FROM object_refs WHERE project_id=? AND target_id=? AND target_kind=? AND owner_id<>?', (project_id, object_id, kind, object_id)).fetchall()
            if kind == 'dataset':
                # Mappings are owned by the dataset; references to any owned mapping also protect deletion.
                mappings = [r[0] for r in rows if r[1] == 'mapping']
                rows = [r for r in rows if r[1] != 'mapping']
                for mapping_id in mappings:
                    rows.extend(db.execute('SELECT DISTINCT owner_id,owner_kind FROM object_refs WHERE project_id=? AND target_id=? AND target_kind=?', (project_id, mapping_id, 'mapping')).fetchall())
                rows = list(set(rows))
            result=[]
            for owner_id,owner_kind in sorted(set(rows)):
                if owner_kind=='run':
                    value=db.execute('SELECT summary FROM runs WHERE project_id=? AND id=?',(project_id,owner_id)).fetchone()
                else:
                    value=db.execute('SELECT summary FROM entities WHERE project_id=? AND id=? ORDER BY version DESC LIMIT 1',(project_id,owner_id)).fetchone()
                summary=json.loads(value[0]) if value else {}
                name=summary.get('name') or summary.get('title') or summary.get('filename') or owner_id
                versions=[v[0] for v in db.execute('SELECT DISTINCT owner_version FROM object_refs WHERE project_id=? AND owner_id=? ORDER BY owner_version',(project_id,owner_id))]
                result.append({'id':owner_id,'kind':owner_kind,'name':name,'label':name,'versions':versions})
        return result

    def events(self, project_id, object_id=None):
        if project_id:
            self.project(project_id)
        sql, args = 'SELECT object_id,created_at,action,details FROM audit WHERE project_id IS ?', [project_id]
        if object_id:
            sql += ' AND object_id=?'
            args.append(object_id)
        with self.read() as db:
            rows = db.execute(sql + ' ORDER BY seq DESC LIMIT 500', args).fetchall()
        return [{'object_id': r[0], 'created_at': r[1], 'action': r[2], 'details': json.loads(r[3])} for r in rows]

    def register_export(self, project_id, run_id, body):
        with self.write() as db:
            self.get_run(project_id, run_id)
            value = {**body, 'id': uid('export'), 'project_id': project_id, 'run_id': run_id, 'created_at': now()}
            db.execute('INSERT INTO exports VALUES(?,?,?,?)', (project_id, value['id'], run_id, dumps(value)))
            self.event(project_id, run_id, 'exported', {'export_id': value['id']})
        return value

    def exports(self, project_id, run_id):
        self.get_run(project_id, run_id)
        with self.read() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM exports WHERE project_id=? AND run_id=?', (project_id, run_id))]

    def close(self):
        with self.lock:
            self.db.close()
            self.process_lock.close()
