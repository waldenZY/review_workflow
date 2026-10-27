"""Lifecycle state is separate from immutable configuration versions."""
import shutil
import stat
from pathlib import Path

from .store import now, uid

BUSY = ('queued', 'running', 'cancelling')
KINDS = ('dataset', 'mapping', 'spec', 'evaluator', 'run', 'case', 'design_call')


class HistoryConflict(ValueError):
    def __init__(self, message, references=None):
        super().__init__(message)
        self.references = references or []


class History:
    def __init__(self, store):
        self.store = store

    def get(self, kind, object_id):
        if kind not in KINDS:
            raise ValueError('未知历史类型。')
        return self.store.get_run(object_id) if kind == 'run' else self.store.get(object_id, kind=kind)

    def references(self, kind, object_id):
        refs = {}

        def add(value, label):
            if value['id'] != object_id:
                refs[value['id']] = {'id': value['id'], 'kind': value.get('kind', 'run'), 'label': label,
                                     'archived_at': self.store.archived_at(value['id'])}

        for value in self.store.all_entities():
            k = value['kind']
            if k == 'mapping' and kind == 'dataset':
                continue  # A dataset owns its mapping versions; deletion handles them together.
            if k == 'evaluator' and kind == 'spec' and value.get('spec_id') == object_id:
                add(value, '评测器使用该标准版本')
            if k == 'case' and ((kind == 'run' and value.get('run_id') == object_id) or (kind == 'spec' and value.get('spec_id') == object_id)):
                add(value, '人工案例依赖该对象')
            if k == 'spec' and kind == 'design_call' and (object_id in value.get('design_call_ids', []) or any(t.get('design_call_id') == object_id for t in value.get('design_traces', []))):
                add(value, '标准版本关联此设计调用')
            if k == 'design_call':
                if any(ref.get('kind') == kind and ref.get('id') == object_id for ref in value.get('references', [])):
                    add(value, '设计调用引用该数据或映射')
                if kind == 'dataset':
                    for ref in value.get('references', []):
                        if ref.get('kind') == 'mapping':
                            try:
                                if self.store.get(ref['id'], kind='mapping')['dataset_id'] == object_id:
                                    add(value, '设计调用通过映射引用该数据')
                            except KeyError:
                                pass
        for run in self.store.runs(include_archived=True):
            snap = run['snapshot']
            if kind in ('dataset', 'mapping', 'spec', 'evaluator') and snap[kind]['id'] == object_id:
                add(run, '运行固定了该对象的版本快照')
            if kind == 'run' and run.get('parent_run_id') == object_id:
                add(run, '后续复测运行引用该运行')
            if kind == 'case' and run.get('validation_case_id') == object_id:
                add(run, '回归运行引用该人工案例')
        return list(refs.values())

    def listing(self, kind=None, include_archived=False):
        kinds = [kind] if kind else [k for k in KINDS if k != 'mapping']
        if any(k not in KINDS for k in kinds):
            raise ValueError('未知历史类型。')
        result = []
        for k in kinds:
            values = self.store.runs(include_archived) if k == 'run' else self.store.list(k, include_archived)
            for value in values:
                refs = self.references(k, value['id'])
                busy = value.get('status') in (*BUSY, 'design_running')
                result.append({'id': value['id'], 'kind': k, 'version': value.get('version'),
                    'name': value.get('title') or value.get('filename') or value.get('purpose') or (value.get('snapshot', {}).get('spec', {}).get('title')) or value.get('criterion_id') or value['id'],
                    'created_at': value['created_at'], 'status': value.get('status'), 'archived_at': value.get('archived_at'),
                    'references': refs, 'can_delete': not busy and not refs, 'can_archive': not busy,
                    'row_count': value.get('row_count')})
        return sorted(result, key=lambda value: value['created_at'], reverse=True)

    def archive(self, kind, object_id, restore=False):
        with self.store.lock:
            value = self.get(kind, object_id)
            if value.get('status') in (*BUSY, 'design_running'):
                raise HistoryConflict('运行中的对象不能归档或恢复。')
            timestamp = None if restore else now()
            self.store.db.execute('INSERT OR REPLACE INTO lifecycle VALUES (?,?)', (object_id, timestamp))
            self.store.db.commit()
            self.store.event('restore' if restore else 'archive', object_id, {'kind': kind})
            return {'id': object_id, 'kind': kind, 'archived_at': timestamp}

    def _owned_paths(self, kind, value):
        if kind == 'dataset':
            return [self.store.root / 'imports' / (value['id'] + Path(value['filename']).suffix.lower())]
        if kind == 'run':
            return [self.store.root / 'runs' / value['id']]
        return []

    def delete(self, kind, object_id):
        with self.store.lock:
            value = self.get(kind, object_id)
            if value.get('status') in (*BUSY, 'design_running'):
                raise HistoryConflict('运行中的对象不能删除。')
            refs = self.references(kind, object_id)
            if refs:
                raise HistoryConflict('对象仍被保留记录引用，请先处理引用或使用归档。', refs)
            paths = self._owned_paths(kind, value)
            root = self.store.root.resolve()
            def linked(path):
                return path.is_symlink() or (path.exists() and bool(getattr(path.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)))
            for path in paths:
                ancestors = [path, *path.parents]
                if not path.resolve().is_relative_to(root) or any(linked(p) for p in ancestors if p != root and p.is_relative_to(root)):
                    raise ValueError('拒绝清理不属于当前数据目录的文件。')
                if path.is_dir() and any(linked(child) or not child.resolve().is_relative_to(root) for child in path.rglob('*')):
                    raise ValueError('归属文件中包含不安全的链接。')
            quarantine = root / '.deleting' / uid('delete')
            if not quarantine.resolve().is_relative_to(root) or linked(quarantine.parent):
                raise ValueError('拒绝使用数据目录之外或包含链接的删除暂存目录。')
            moved = []
            try:
                for index, path in enumerate(paths):
                    if path.exists():
                        quarantine.mkdir(parents=True, exist_ok=True)
                        target = quarantine / str(index)
                        shutil.move(str(path), str(target))
                        moved.append((path, target))
                self.store.db.execute('BEGIN')
                if kind == 'run':
                    self.store.db.execute('DELETE FROM runs WHERE id=?', (object_id,))
                else:
                    if kind == 'dataset':
                        for mapping in self.store.list('mapping', include_archived=True):
                            if mapping['dataset_id'] == object_id:
                                self.store.db.execute('DELETE FROM entities WHERE id=?', (mapping['id'],))
                                self.store.db.execute('DELETE FROM lifecycle WHERE id=?', (mapping['id'],))
                    self.store.db.execute('DELETE FROM entities WHERE id=?', (object_id,))
                self.store.db.execute('DELETE FROM lifecycle WHERE id=?', (object_id,))
                self.store.db.commit()
            except Exception:
                self.store.db.rollback()
                for original, staged in reversed(moved):
                    if staged.exists():
                        original.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(staged), str(original))
                raise
            cleanup_pending = False
            if quarantine.exists():
                try:
                    shutil.rmtree(quarantine)
                except OSError:
                    cleanup_pending = True
            self.store.event('delete', object_id, {'kind': kind, 'file_cleanup_pending': cleanup_pending})
            return {'id': object_id, 'deleted': True, 'file_cleanup_pending': cleanup_pending}
