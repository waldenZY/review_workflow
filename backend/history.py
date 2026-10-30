"""Project-scoped history and guarded, transactional file ownership cleanup."""
import shutil
import stat
from pathlib import Path

from .store import BUSY, KINDS, Conflict, dumps, now, uid


class History:
    def __init__(self, store):
        self.store = store

    def get(self, project_id, kind, object_id):
        if kind == 'run':
            return self.store.get_run(project_id, object_id)
        if kind not in KINDS:
            raise ValueError('未知历史类型。')
        return self.store.get(project_id, object_id, kind=kind)

    def references(self, project_id, kind, object_id):
        refs = {}

        def add(value, label):
            if value['id'] != object_id:
                refs[value['id']] = {'id': value['id'], 'kind': value.get('kind', 'run'), 'label': label}

        for value in self.store.entities(project_id, include_archived=True, all_versions=True):
            k = value['kind']
            if k == 'evaluator' and kind == 'spec' and value['spec_id'] == object_id:
                add(value, '评测器引用标准版本')
            if k == 'spec' and kind == 'design_call' and object_id in value.get('design_call_ids', []):
                add(value, '标准版本关联设计调用')
            if k == 'design_call' and any(r['kind'] == kind and r['id'] == object_id for r in value.get('references', [])):
                add(value, '设计调用引用材料')
        for run in self.store.runs(project_id, True):
            if kind in ('dataset', 'mapping', 'spec', 'evaluator') and run['snapshot'][kind]['id'] == object_id:
                add(run, '运行固定该对象快照')
            if kind == 'run' and run.get('parent_run_id') == object_id:
                add(run, '后续复测关联原运行')
        return list(refs.values())

    def listing(self, project_id, kind=None, include_archived=False):
        kinds = [kind] if kind else [*KINDS, 'run']
        if any(k not in (*KINDS, 'run') for k in kinds):
            raise ValueError('未知历史类型。')
        values = []
        for k in kinds:
            objects = self.store.runs(project_id, include_archived) if k == 'run' else self.store.entities(project_id, k, include_archived)
            for v in objects:
                refs = self.references(project_id, k, v['id'])
                values.append({'id': v['id'], 'kind': k, 'name': v.get('title') or v.get('filename') or v.get('purpose') or v['id'],
                               'version': v.get('version'), 'created_at': v['created_at'], 'status': v.get('status'),
                               'archived_at': v.get('archived_at'), 'references': refs, 'can_delete': not refs and v.get('status') not in BUSY})
        return sorted(values, key=lambda v: v['created_at'], reverse=True)

    def busy(self, project_id):
        return [v for v in [*self.store.runs(project_id, True), *self.store.entities(project_id, 'design_call', True)] if v.get('status') in BUSY]

    @staticmethod
    def linked(path):
        return path.is_symlink() or (path.exists() and bool(getattr(path.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)))

    def validate_path(self, path, boundary):
        boundary = Path(boundary)
        path = Path(path)
        root = self.store.root
        if not boundary.is_relative_to(root) or not path.is_relative_to(boundary) or not path.resolve().is_relative_to(boundary.resolve()):
            raise ValueError('拒绝操作所属目录之外的文件。')
        for item in [path, *path.parents]:
            if item == root:
                break
            if self.linked(item):
                raise ValueError('所属文件路径包含链接或重解析点。')
        if path.is_dir() and any(self.linked(child) or not child.resolve().is_relative_to(boundary.resolve()) for child in path.rglob('*')):
            raise ValueError('所属目录包含不安全的链接。')
        return path

    def project_dir(self, project_id):
        self.store.project(project_id)
        path = self.store.root / 'projects' / project_id
        return self.validate_path(path, path)

    def deletion_scope(self, project_id):
        with self.store.lock:
            project = self.store.project(project_id)
            values = self.store.entities(project_id, include_archived=True, all_versions=True)
            runs = self.store.runs(project_id, True)
            folder = self.project_dir(project_id)
            files = [p for p in folder.rglob('*') if p.is_file()] if folder.exists() else []
            return {'project': project, 'objects': len({v['id'] for v in values}) + len(runs), 'versions': len(values),
                    'runs': len(runs), 'files': len(files), 'bytes': sum(p.stat().st_size for p in files),
                    'busy': [{'id': v['id'], 'status': v['status']} for v in self.busy(project_id)]}

    def archive_project(self, project_id, restore=False):
        with self.store.lock:
            value = self.store.project(project_id)
            if self.busy(project_id):
                raise Conflict('项目存在未结束的任务或设计调用，不能归档。')
            value.update(archived_at=None if restore else now(), updated_at=now())
            self.store.db.execute('UPDATE projects SET body=? WHERE id=?', (dumps(value), project_id))
            self.store.db.commit()
            self.store.event(project_id, project_id, 'project_restored' if restore else 'project_archived')
            return value

    def archive(self, project_id, kind, object_id, restore=False):
        with self.store.lock:
            self.store.writable(project_id)
            value = self.get(project_id, kind, object_id)
            refs = self.references(project_id, kind, object_id)
            busy_ids = {v['id'] for v in self.busy(project_id)}
            if value.get('status') in BUSY or any(r['id'] in busy_ids for r in refs):
                raise Conflict('对象或引用它的任务仍在运行，不能归档。')
            self.store.db.execute('INSERT OR REPLACE INTO lifecycle VALUES(?,?,?)', (project_id, object_id, None if restore else now()))
            self.store.touch(project_id)
            self.store.db.commit()
            self.store.event(project_id, object_id, 'restored' if restore else 'archived', {'kind': kind})
            return self.get(project_id, kind, object_id)

    def _delete(self, paths, mutate, event):
        root = self.store.root
        staging = root / '.deleting' / uid('delete')
        self.validate_path(staging, root)
        moved = []
        try:
            for index, path in enumerate(paths):
                if path.exists():
                    staging.mkdir(parents=True, exist_ok=True)
                    target = staging / str(index)
                    shutil.move(str(path), str(target))
                    moved.append((path, target))
            self.store.db.execute('BEGIN')
            mutate()
            self.store.db.commit()
        except Exception:
            self.store.db.rollback()
            for original, target in reversed(moved):
                if target.exists():
                    original.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(target), str(original))
            raise
        pending = False
        if staging.exists():
            try:
                shutil.rmtree(staging)
            except OSError:
                pending = True
        project_id, object_id, action = event
        self.store.event(project_id, object_id, action, {'file_cleanup_pending': pending})
        return {'id': object_id, 'deleted': True, 'file_cleanup_pending': pending}

    def delete_project(self, project_id, confirm_name):
        with self.store.lock:
            scope = self.deletion_scope(project_id)
            if scope['project']['name'] != confirm_name:
                raise ValueError('项目名称确认不匹配。')
            if scope['busy']:
                raise Conflict('项目存在未结束的任务或设计调用。', scope['busy'])
            folder = self.project_dir(project_id)

            def mutate():
                for table in ('entities', 'runs', 'exports', 'lifecycle', 'audit'):
                    self.store.db.execute(f'DELETE FROM {table} WHERE project_id=?', (project_id,))
                self.store.db.execute('DELETE FROM projects WHERE id=?', (project_id,))

            return self._delete([folder], mutate, (None, project_id, 'project_deleted'))

    def delete(self, project_id, kind, object_id):
        with self.store.lock:
            self.store.writable(project_id)
            value = self.get(project_id, kind, object_id)
            refs = self.references(project_id, kind, object_id)
            if value.get('status') in BUSY:
                raise Conflict('运行中的对象不能删除。')
            if refs:
                raise Conflict('对象仍被保留记录引用，可先归档。', refs)
            folder = self.project_dir(project_id)
            paths = []
            if kind == 'dataset':
                paths = [folder / 'imports' / (object_id + Path(value['filename']).suffix.lower())]
            if kind == 'run':
                paths = [folder / 'runs' / object_id]
            for path in paths:
                self.validate_path(path, folder)

            def mutate():
                if kind == 'run':
                    self.store.db.execute('DELETE FROM runs WHERE project_id=? AND id=?', (project_id, object_id))
                    self.store.db.execute('DELETE FROM exports WHERE project_id=? AND run_id=?', (project_id, object_id))
                else:
                    if kind == 'dataset':
                        for mapping in self.store.entities(project_id, 'mapping', True):
                            if mapping['dataset_id'] == object_id:
                                self.store.db.execute('DELETE FROM entities WHERE project_id=? AND id=?', (project_id, mapping['id']))
                                self.store.db.execute('DELETE FROM lifecycle WHERE project_id=? AND id=?', (project_id, mapping['id']))
                    self.store.db.execute('DELETE FROM entities WHERE project_id=? AND id=?', (project_id, object_id))
                self.store.db.execute('DELETE FROM lifecycle WHERE project_id=? AND id=?', (project_id, object_id))
                self.store.touch(project_id)

            return self._delete(paths, mutate, (project_id, object_id, 'deleted'))
