"""Explicit, offline legacy conversion. Importing this module does not open a database."""
import json
import shutil
import sqlite3
import stat
from datetime import datetime
from pathlib import Path

from .process_lock import ProcessLock
from .storage_schema import DATABASE, REVISION, attempt_summary, counts, entity_summary, install, run_summary, save_references
from .store import BUSY, dumps, materialize, now


def inspect(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0, REVISION):
        raise ValueError(f'不支持的结构版本：{version}')
    required = {'projects', 'entities', 'runs', 'lifecycle', 'exports', 'audit'}
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required <= tables:
        raise ValueError('不是受支持的 v1.1 数据库，拒绝转换。')
    if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise ValueError('数据库完整性检查失败，请先恢复或检查备份。')
    return {'schema_revision': version, **{t: db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in sorted(required)}}


def convert(db, report=print):
    """Caller owns process lock + backup. All DDL and data changes share one transaction."""
    before = inspect(db)
    if before['schema_revision'] == REVISION:
        report('数据库已升级，无需转换。')
        return before
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('ALTER TABLE runs RENAME TO legacy_runs')
        db.execute("ALTER TABLE entities ADD COLUMN summary TEXT NOT NULL DEFAULT '{}'")
        db.execute('DROP INDEX IF EXISTS entities_project_kind')
        install(db)
        identities = db.execute('SELECT project_id,id,version FROM entities ORDER BY rowid').fetchall()
        report(f'转换对象版本：{len(identities)}')
        for index, identity in enumerate(identities, 1):
            project_id, object_id, version = identity
            raw = db.execute('SELECT body FROM entities WHERE project_id=? AND id=? AND version=?', identity).fetchone()[0]
            value = json.loads(raw)
            if (value['project_id'], value['id'], value['version']) != identity:
                raise ValueError('对象身份与数据库索引不一致。')
            if value['kind'] == 'dataset':
                rows = value.pop('rows')
                if value['row_count'] != len(rows):
                    raise ValueError('数据集行数不一致。')
                value['material_id'], value['original_columns'] = materialize(db, project_id, rows)
                verify_material(db, project_id, value['material_id'], rows)
            db.execute('UPDATE entities SET body=?,summary=? WHERE project_id=? AND id=? AND version=?',
                       (dumps(value), dumps(entity_summary(value)), project_id, object_id, version))
            save_references(db, value)
            report(f'对象版本 {index}/{len(identities)} 已核对')

        run_keys = db.execute('SELECT project_id,id FROM legacy_runs ORDER BY rowid').fetchall()
        total_records = total_attempts = 0
        for index, key in enumerate(run_keys, 1):
            project_id, run_id = key
            run = json.loads(db.execute('SELECT body FROM legacy_runs WHERE project_id=? AND id=?', key).fetchone()[0])
            if (run['project_id'], run['id']) != key:
                raise ValueError('运行身份不一致。')
            records = run.pop('records')
            dataset = run['snapshot']['dataset']
            rows = dataset.pop('rows')
            if dataset['row_count'] != len(rows):
                raise ValueError('运行快照行数不一致。')
            dataset['material_id'], dataset['original_columns'] = materialize(db, project_id, rows)
            verify_material(db, project_id, dataset['material_id'], rows)
            material = dataset['material_id']
            sources = {r['row_id']: (i, r['source_row']) for i, r in enumerate(rows)}
            unfinished = run['status'] in BUSY
            if unfinished:
                run.update(status='interrupted', finished_at=now(), error='离线升级，未结束运行已标记中断；已完成结果保留，请明确选择重试。')
            totals = dict.fromkeys(('processed', 'running', 'pending', 'failed', 'succeeded'), 0)
            for record in records:
                row_id = record['row_id']
                if row_id not in sources:
                    raise ValueError('运行记录找不到原始材料。')
                status = 'interrupted' if unfinished and record['status'] in ('pending', 'running') else record['status']
                for k, v in counts(status).items():
                    totals[k] += v
                attempts = record['attempts']
                for ordinal, attempt in enumerate(attempts, 1):
                    db.execute('INSERT INTO attempts(project_id,run_id,row_id,id,ordinal,round_no,body) VALUES(?,?,?,?,?,NULL,?)',
                               (project_id, run_id, row_id, attempt['id'], ordinal, dumps(attempt)))
                    stored = db.execute('SELECT body FROM attempts WHERE project_id=? AND id=?', (project_id, attempt['id'])).fetchone()[0]
                    if json.loads(stored) != attempt:
                        raise ValueError('尝试正文核对失败。')
                last = attempts[-1] if attempts else {}
                db.execute('INSERT INTO run_records(project_id,run_id,row_id,source_order,source_row,status,round_no,latest_attempt_id,attempt_count,summary,error_stage) VALUES(?,?,?,?,?,?,NULL,?,?,?,?)',
                           (project_id, run_id, row_id, *sources[row_id], status, last.get('id'), len(attempts), dumps(attempt_summary(last)), last.get('error_stage')))
                total_attempts += len(attempts)
            total_records += len(records)
            db.execute('INSERT INTO runs(project_id,id,body,summary,status,material_id,current_round,selected,processed,running,pending,failed,succeeded,updated_at) VALUES(?,?,?,?,?,?,0,?,?,?,?,?,?,?)',
                       (project_id, run_id, dumps(run), dumps(run_summary(run)), run['status'], material, len(records), totals['processed'], totals['running'], totals['pending'], totals['failed'], totals['succeeded'], now()))
            save_references(db, run)
            for kind in ('dataset', 'mapping', 'spec', 'evaluator'):
                bound = run['snapshot'][kind]
                if not db.execute('SELECT 1 FROM entities WHERE project_id=? AND id=? AND version=? AND kind=?',
                                  (project_id, bound['id'], bound['version'], kind)).fetchone():
                    raise ValueError('运行绑定的对象版本不存在。')
            if json.loads(db.execute('SELECT body FROM runs WHERE project_id=? AND id=?', key).fetchone()[0]) != run:
                raise ValueError('运行快照核对失败。')
            actual = dict(db.execute('SELECT status,COUNT(*) FROM run_records WHERE project_id=? AND run_id=? GROUP BY status', key))
            if sum(actual.values()) != len(records):
                raise ValueError('运行记录数量核对失败。')
            for name, expected in totals.items():
                if sum(counts(status)[name] * number for status, number in actual.items()) != expected:
                    raise ValueError('运行状态计数核对失败。')
            report(f'运行 {index}/{len(run_keys)} 已转换：{len(records)} 条；历史轮次保留为未记录')

        for table in ('projects', 'entities', 'runs', 'lifecycle', 'exports', 'audit'):
            if db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] != before[table]:
                raise ValueError(f'{table} 数量核对失败。')
        if db.execute('SELECT COUNT(*) FROM run_records').fetchone()[0] != total_records or db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] != total_attempts:
            raise ValueError('逐行或尝试总数核对失败。')
        # Validate retained object references without discarding archived owners or versions.
        for p, owner, owner_kind, target, target_kind in db.execute('SELECT project_id,owner_id,owner_kind,target_id,target_kind FROM object_refs'):
            table = 'runs' if target_kind == 'run' else 'materials' if target_kind == 'material' else 'entities'
            if not db.execute(f'SELECT 1 FROM {table} WHERE project_id=? AND id=?', (p, target)).fetchone():
                raise ValueError(f'发现无法解析的保留引用：{owner_kind} → {target_kind}；转换已回滚。')
        db.execute('DROP TABLE legacy_runs')
        db.execute(f'PRAGMA user_version={REVISION}')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('转换后数据库完整性检查失败。')
        db.commit()
        report(f'转换提交完成：{total_records} 条运行记录，{total_attempts} 次尝试。')
        return {'records': total_records, 'attempts': total_attempts, 'schema_revision': REVISION}
    except Exception:
        db.rollback()
        report('转换失败，数据库事务已回滚，备份保留。')
        raise


def verify_material(db, project_id, material, rows):
    stored = db.execute('SELECT row_id,source_row,body FROM data_rows WHERE project_id=? AND material_id=? ORDER BY source_order', (project_id, material))
    count = 0
    for expected, actual in zip(rows, stored):
        if expected != {'row_id': actual[0], 'source_row': actual[1], 'original': json.loads(actual[2])}:
            raise ValueError('原始材料逐行核对失败。')
        count += 1
    actual_count = db.execute('SELECT COUNT(*) FROM data_rows WHERE project_id=? AND material_id=?', (project_id, material)).fetchone()[0]
    if count != len(rows) or actual_count != len(rows):
        raise ValueError('原始材料数量核对失败。')


def migrate(data_root, apply=False, report=print):
    root = Path(data_root).expanduser().resolve()
    source = root / 'v1_1'
    path = source / DATABASE
    if not path.is_file():
        raise ValueError('数据根目录下没有 v1_1 数据库；不要把 v1_1 本身作为 --data-dir。')
    with ProcessLock(source):
        check = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            result = inspect(check)
        finally:
            check.close()
        report(dumps(result))
        if not apply or result['schema_revision'] == REVISION:
            return result
        for p in [source, *source.rglob('*')]:
            if p.is_symlink() or bool(getattr(p.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
                raise ValueError('备份源含链接或重解析点，拒绝自动复制。')
        backup = root / 'backups' / datetime.now().strftime('%Y%m%d_%H%M%S_%f') / 'v1_1'
        report('备份完整目录：' + str(backup))
        shutil.copytree(source, backup, ignore=shutil.ignore_patterns('.workbench.lock'))
        db = sqlite3.connect(path, timeout=5)
        try:
            db.execute('PRAGMA busy_timeout=5000')
            return convert(db, report)
        finally:
            db.close()
