"""Group-task persistence. HTTP never runs inside these transactions."""
import json
import time
from datetime import datetime, timezone

from .storage_schema import attempt_summary, counts, run_summary, save_references


def now():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class GroupRuns:
    def row_progress(self, db, project_id, run_id):
        if db is None:
            with self.read() as reader:
                return self.row_progress(reader, project_id, run_id)
        row=db.execute('SELECT rows_selected,rows_succeeded,rows_running,rows_pending,rows_failed FROM runs WHERE project_id=? AND id=?',(project_id,run_id)).fetchone()
        return dict(zip(('selected','succeeded','running','incomplete','failed'),row))

    def create_run(self, value, selected=None):
        pid, rid = value['project_id'], value['id']
        material = value['snapshot']['dataset']['material_id']
        with self.write() as db:
            self.writable(pid)
            if selected is not None:
                self.validate_rows(pid, material, selected, value.get('parent_run_id'))
            db.execute('INSERT INTO runs(project_id,id,body,summary,status,material_id,updated_at) VALUES(?,?,?,?,?,?,?)', (pid, rid, dumps(value), dumps(run_summary(value)), 'queued', material, now()))
            query = "INSERT INTO run_records(project_id,run_id,row_id,source_order,source_row,status) SELECT project_id,?,row_id,source_order,source_row,'pending' FROM data_rows WHERE project_id=? AND material_id=?"
            if selected is None:
                db.execute(query, (rid, pid, material))
            else:
                db.executemany(query + ' AND row_id=?', ((rid, pid, material, i) for i in selected))
            for index, group in enumerate(value['snapshot']['groups']):
                db.execute("INSERT INTO group_tasks(project_id,run_id,row_id,group_id,group_order,source_order,status,round_no) SELECT project_id,run_id,row_id,?,?,source_order,'pending',1 FROM run_records WHERE project_id=? AND run_id=?", (group['id'], index, pid, rid))
            n = db.execute('SELECT COUNT(*) FROM group_tasks WHERE project_id=? AND run_id=?', (pid, rid)).fetchone()[0]
            if not n:
                raise ValueError('没有可执行的记录。')
            db.execute('UPDATE runs SET current_round=1,selected=?,pending=? WHERE project_id=? AND id=?', (n, n, pid, rid))
            db.execute('UPDATE runs SET rows_selected=?,rows_pending=? WHERE project_id=? AND id=?',(n//len(value['snapshot']['groups']),n//len(value['snapshot']['groups']),pid,rid))
            stamp = now()
            db.execute('INSERT INTO run_rounds(project_id,run_id,number,status,created_at,selected,pending,updated_at) VALUES(?,?,1,?,?,?,?,?)', (pid, rid, 'queued', stamp, n, n, stamp))
            save_references(db, value)
            self.touch(pid)
        return self.get_run(pid, rid)

    def _refresh_row(self, db, pid, rid, row_id):
        previous=db.execute('SELECT status FROM run_records WHERE project_id=? AND run_id=? AND row_id=?',(pid,rid,row_id)).fetchone()[0]
        statuses = {r[0] for r in db.execute('SELECT status FROM group_tasks WHERE project_id=? AND run_id=? AND row_id=?', (pid, rid, row_id))}
        status = 'completed' if statuses == {'completed'} else 'running' if 'running' in statuses else 'pending' if 'pending' in statuses else 'error'
        db.execute('UPDATE run_records SET status=? WHERE project_id=? AND run_id=? AND row_id=?', (status, pid, rid, row_id))
        if status!=previous:
            db.execute('UPDATE runs SET rows_succeeded=rows_succeeded+?,rows_running=rows_running+?,rows_pending=rows_pending+?,rows_failed=rows_failed+? WHERE project_id=? AND id=?',
                       tuple(int(status==value)-int(previous==value) for value in ('completed','running','pending','error'))+(pid,rid))

    def _refresh_row_counts(self,db,pid,rid):
        values=dict(db.execute('SELECT status,COUNT(*) FROM run_records WHERE project_id=? AND run_id=? GROUP BY status',(pid,rid)))
        db.execute('UPDATE runs SET rows_succeeded=?,rows_running=?,rows_pending=?,rows_failed=? WHERE project_id=? AND id=?',tuple(values.get(s,0) for s in ('completed','running','pending','error'))+(pid,rid))

    def _transition(self, db, pid, rid, task, status):
        row_id, gid = task
        old = db.execute('SELECT status,round_no FROM group_tasks WHERE project_id=? AND run_id=? AND row_id=? AND group_id=?', (pid, rid, row_id, gid)).fetchone()
        if not old:
            raise KeyError('组任务不属于本次运行。')
        a, b = counts(old[0]), counts(status)
        assignments = ','.join(f'{key}={key}+?' for key in a)
        delta = [b[k]-a[k] for k in a]
        db.execute(f'UPDATE runs SET {assignments},updated_at=? WHERE project_id=? AND id=?', (*delta, now(), pid, rid))
        db.execute(f'UPDATE run_rounds SET {assignments},updated_at=? WHERE project_id=? AND run_id=? AND number=?', (*delta, now(), pid, rid, old[1]))
        db.execute('UPDATE group_tasks SET status=? WHERE project_id=? AND run_id=? AND row_id=? AND group_id=?', (status, pid, rid, row_id, gid))
        self._refresh_row(db, pid, rid, row_id)

    def claim(self, pid, rid):
        with self.write() as db:
            if self.get_run(pid, rid, summary=True).get('cancel_requested'):
                return None
            row = db.execute("SELECT row_id,group_id FROM group_tasks WHERE project_id=? AND run_id=? AND status='pending' ORDER BY source_order,group_order LIMIT 1", (pid, rid)).fetchone()
            if row:
                db.execute("UPDATE group_tasks SET auto_retry_count=auto_retry_count+1 WHERE project_id=? AND run_id=? AND row_id=? AND group_id=? AND EXISTS(SELECT 1 FROM run_rounds w WHERE w.project_id=group_tasks.project_id AND w.run_id=group_tasks.run_id AND w.number=group_tasks.round_no AND w.kind='auto_retry')",(pid,rid,*row))
                self._transition(db, pid, rid, row, 'running')
            return tuple(row) if row else None

    def save_attempt(self, pid, rid, task, attempt):
        started = time.perf_counter()
        row_id, gid = task
        with self.write() as db:
            if db.execute('SELECT 1 FROM attempts WHERE project_id=? AND id=?', (pid, attempt['id'])).fetchone():
                return
            row = db.execute('SELECT attempt_count,round_no,auto_retry_count FROM group_tasks WHERE project_id=? AND run_id=? AND row_id=? AND group_id=?', (pid, rid, row_id, gid)).fetchone()
            if not row:
                raise KeyError('组任务不存在。')
            kind = db.execute('SELECT kind FROM run_rounds WHERE project_id=? AND run_id=? AND number=?', (pid, rid, row[1])).fetchone()[0]
            attempt.update(group_id=gid, round_no=row[1], ordinal=row[0]+1, kind=kind, auto_retry_count=row[2])
            db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?)', (pid, rid, row_id, gid, attempt['id'], row[0]+1, row[1], dumps(attempt)))
            self._transition(db, pid, rid, task, attempt['status'])
            db.execute('UPDATE group_tasks SET latest_attempt_id=?,attempt_count=attempt_count+1,summary=?,error_stage=? WHERE project_id=? AND run_id=? AND row_id=? AND group_id=?', (attempt['id'], dumps(attempt_summary(attempt)), attempt.get('error_stage'), pid, rid, row_id, gid))
        with self.metrics_lock:
            self.pending_metrics[(pid, attempt['id'])] = {'save_ms': round((time.perf_counter()-started)*1000, 2)}
            while len(self.pending_metrics) > 32:
                self.pending_metrics.popitem(last=False)

    def retry_run(self, pid, rid, automatic=False):
        from .store import BUSY, Conflict
        with self.write() as db:
            self.assert_unleased(pid, rid)
            run = self.get_run(pid, rid, True)
            if run['status'] in BUSY:
                raise Conflict('任务仍在运行。')
            if automatic and (run['status'] != 'completed_with_errors' or run.get('cancel_requested') or run['mode'] != 'batch'):
                raise Conflict('当前运行不满足自动重试条件。')
            condition = "status IN ('call_failed','parse_failed','schema_failed') AND auto_retry_count<3" if automatic else "status<>'completed'"
            number, stamp = run['current_round']+1, now()
            # SQL-wide state changes keep retries bounded in memory even for very large runs.
            n = db.execute('SELECT COUNT(*) FROM group_tasks WHERE project_id=? AND run_id=? AND '+condition, (pid, rid)).fetchone()[0]
            if not n:
                raise ValueError('没有需要重试的组任务。')
            db.execute("UPDATE group_tasks SET status='pending',round_no=? WHERE project_id=? AND run_id=? AND "+condition, (number, pid, rid))
            db.execute("UPDATE run_records SET status=CASE WHEN EXISTS(SELECT 1 FROM group_tasks g WHERE g.project_id=run_records.project_id AND g.run_id=run_records.run_id AND g.row_id=run_records.row_id AND g.status='pending') THEN 'pending' ELSE status END WHERE project_id=? AND run_id=?", (pid, rid))
            self._refresh_row_counts(db,pid,rid)
            values = dict.fromkeys(counts('pending'), 0)
            for status, count in db.execute('SELECT status,COUNT(*) FROM group_tasks WHERE project_id=? AND run_id=? GROUP BY status', (pid, rid)):
                for k, v in counts(status).items():
                    values[k] += v*count
            db.execute('UPDATE runs SET current_round=?,'+','.join(k+'=?' for k in values)+',updated_at=? WHERE project_id=? AND id=?', (number, *values.values(), stamp, pid, rid))
            kind = 'auto_retry' if automatic else 'retry'
            db.execute('INSERT INTO run_rounds(project_id,run_id,number,status,created_at,selected,pending,updated_at,kind) VALUES(?,?,?,?,?,?,?,?,?)', (pid, rid, number, 'queued', stamp, n, n, stamp, kind))
            result = self.update_run(pid, rid, lambda r: r.update(status='queued', cancel_requested=False, error=None, finished_at=None))
            self.event(pid, rid, kind, {'round': number, 'group_tasks': n})
        return result

    def finish_run(self, pid, rid, fatal=None, auto_retry=False):
        with self.write() as db:
            if fatal:
                for task in db.execute("SELECT row_id,group_id FROM group_tasks WHERE project_id=? AND run_id=? AND status='running'", (pid, rid)).fetchall():
                    self._transition(db, pid, rid, task, 'interrupted')
            run = self.get_run(pid, rid)
            status = 'failed' if fatal else 'cancelled' if run.get('cancel_requested') else 'completed' if run['progress']['succeeded']==run['progress']['selected'] else 'completed_with_errors'
            result = self.update_run(pid, rid, lambda r: r.update(status=status, error=fatal, finished_at=now()))
            db.execute('UPDATE run_rounds SET status=?,finished_at=?,updated_at=? WHERE project_id=? AND run_id=? AND number=?', (status, now(), now(), pid, rid, run['current_round']))
            eligible = db.execute("SELECT 1 FROM group_tasks WHERE project_id=? AND run_id=? AND status IN ('call_failed','parse_failed','schema_failed') AND auto_retry_count<3 LIMIT 1", (pid, rid)).fetchone()
            if auto_retry and status == 'completed_with_errors' and not run['progress']['pending'] and run['mode']=='batch' and eligible:
                result = self.retry_run(pid, rid, automatic=True)
        return result

    def interrupt_unfinished(self):
        with self.write() as db:
            for pid, rid in db.execute("SELECT project_id,id FROM runs WHERE status IN ('queued','running','cancelling')").fetchall():
                # Recovery groups counters in SQL, without materializing task IDs.
                for status, number, n in db.execute("SELECT status,round_no,COUNT(*) FROM group_tasks WHERE project_id=? AND run_id=? AND status IN ('pending','running') GROUP BY status,round_no", (pid, rid)).fetchall():
                    a, b = counts(status), counts('interrupted')
                    delta = [(b[k]-a[k])*n for k in a]
                    assignments = ','.join(k+'='+k+'+?' for k in a)
                    db.execute('UPDATE runs SET '+assignments+' WHERE project_id=? AND id=?', (*delta, pid, rid))
                    db.execute('UPDATE run_rounds SET '+assignments+' WHERE project_id=? AND run_id=? AND number=?', (*delta, pid, rid, number))
                db.execute("UPDATE group_tasks SET status='interrupted',error_stage='interrupted' WHERE project_id=? AND run_id=? AND status IN ('pending','running')", (pid, rid))
                db.execute("UPDATE run_records SET status='error' WHERE project_id=? AND run_id=? AND status IN ('pending','running')", (pid, rid))
                self._refresh_row_counts(db,pid,rid)
                run = self.update_run(pid, rid, lambda r: r.update(status='interrupted', finished_at=now(), error='服务重启，未完成组任务已中断，请人工重试。'))
                db.execute("UPDATE run_rounds SET status='interrupted',finished_at=?,updated_at=? WHERE project_id=? AND run_id=? AND number=?", (now(), now(), pid, rid, run['current_round']))

    @staticmethod
    def _group(row):
        gid, status, number, attempt_id, attempts, automatic, summary, stage = row
        value = json.loads(summary)
        previous = bool(attempt_id) and (status in ('pending','running','interrupted') or value.get('round_no') != number or value.get('status') != status)
        return {'group_id': gid, 'status': status, 'round_no': number, 'attempt_count': attempts,
                'auto_retry_count': automatic, 'auto_retry_limit': 3, 'previous_attempt': previous,
                'latest_attempt_id': attempt_id, 'output': value.get('output') if status=='completed' else None,
                'criteria': value.get('criteria',[]) if status=='completed' else [], 'last_attempt': value,
                'error': value.get('error'), 'error_stage': stage}

    def _groups(self, db, pid, rid, row_ids):
        result = {i: [] for i in row_ids}
        if not row_ids:
            return result
        query = 'SELECT row_id,group_id,status,round_no,latest_attempt_id,attempt_count,auto_retry_count,summary,error_stage FROM group_tasks WHERE project_id=? AND run_id=? AND row_id IN ('+','.join('?' for _ in row_ids)+') ORDER BY group_order'
        for row in db.execute(query, (pid, rid, *row_ids)):
            result[row[0]].append(self._group(row[1:]))
        return result

    @staticmethod
    def merge_outputs(groups):
        output = {k: v for g in groups if g['status']=='completed' for k, v in (g.get('output') or {}).items()}
        return output or None

    def record_page(self, pid, rid, offset, limit, status_group='all', error_stage='', group_id=''):
        from .data_io import MISSING, at
        run = self.get_run(pid, rid)
        clauses, args = ['r.project_id=?','r.run_id=?'], [pid,rid]
        task_clauses = ['g.project_id=r.project_id','g.run_id=r.run_id','g.row_id=r.row_id']
        if status_group == 'errors':
            task_clauses.append("g.status IN ('call_failed','parse_failed','schema_failed','interrupted')")
        elif status_group == 'pending':
            task_clauses.append("g.status='pending'")
        elif status_group != 'all':
            raise ValueError('未知结果筛选。')
        if group_id:
            if group_id not in {g['id'] for g in run['snapshot']['groups']}:
                raise ValueError('执行组不属于本次运行。')
            task_clauses.append('g.group_id=?'); args.append(group_id)
        if error_stage:
            task_clauses.append('g.error_stage=?'); args.append(error_stage)
        if len(task_clauses)>3:
            clauses.append('EXISTS(SELECT 1 FROM group_tasks g WHERE '+' AND '.join(task_clauses)+')')
        where = ' AND '.join(clauses)
        fields = list(dict.fromkeys(p for g in run['snapshot']['groups'] for p in g['mapping']['evaluation_fields']))
        with self.read() as db:
            total = db.execute('SELECT COUNT(*) FROM run_records r WHERE '+where, args).fetchone()[0]
            offset = min(offset, max(0, ((total-1)//limit)*limit))
            rows = db.execute('SELECT p.row_id,p.source_row,p.status,d.body FROM (SELECT r.* FROM run_records r WHERE '+where+' ORDER BY r.source_order LIMIT ? OFFSET ?) p JOIN data_rows d ON d.project_id=p.project_id AND d.material_id=? AND d.row_id=p.row_id ORDER BY p.source_order', (*args,limit,offset,run['snapshot']['dataset']['material_id'])).fetchall()
            groups = self._groups(db,pid,rid,[r[0] for r in rows])
        records=[]
        for row_id, source, status, raw in rows:
            original=json.loads(raw); values=groups[row_id]
            records.append({'row_id':row_id,'source_row':source,'status':status,'groups':values,'output':self.merge_outputs(values),
                            'fields':{p:at(original,p,None) for p in fields},'missing_fields':[p for p in fields if at(original,p) is MISSING]})
        return {'records':records,'total':total,'offset':offset,'limit':limit}

    def record_detail(self, pid, rid, row_id):
        run=self.get_run(pid,rid)
        with self.read() as db:
            row=db.execute('SELECT status FROM run_records WHERE project_id=? AND run_id=? AND row_id=?',(pid,rid,row_id)).fetchone()
            if not row:
                raise KeyError('记录不属于本次运行。')
            groups=self._groups(db,pid,rid,[row_id])[row_id]
            attempts=[]
            for body,metrics in db.execute('SELECT a.body,m.body FROM attempts a LEFT JOIN attempt_metrics m ON m.project_id=a.project_id AND m.id=a.id WHERE a.project_id=? AND a.run_id=? AND a.row_id=? ORDER BY a.round_no,a.group_id,a.ordinal',(pid,rid,row_id)):
                attempt=json.loads(body)
                with self.metrics_lock:
                    timing=json.loads(metrics) if metrics else self.pending_metrics.get((pid,attempt['id']),{})
                attempt.setdefault('timing',{}).update(timing)
                attempts.append(attempt)
        return {**self.data_row(pid,run['snapshot']['dataset']['material_id'],row_id),'status':row[0],'groups':groups,'attempts':attempts}

    def export_batches(self,pid,rid,batch=100):
        run=self.get_run(pid,rid); after=-1
        while True:
            with self.read() as db:
                rows=db.execute('SELECT d.row_id,d.source_row,d.source_order,d.body,r.status FROM data_rows d LEFT JOIN run_records r ON r.project_id=d.project_id AND r.run_id=? AND r.row_id=d.row_id WHERE d.project_id=? AND d.material_id=? AND d.source_order>? ORDER BY d.source_order LIMIT ?', (rid,pid,run['snapshot']['dataset']['material_id'],after,batch)).fetchall()
                groups=self._groups(db,pid,rid,[r[0] for r in rows])
            if not rows:
                return
            for row in rows:
                yield {'row_id':row[0],'source_row':row[1],'original':json.loads(row[3]),'status':row[4] or 'not_run','groups':groups[row[0]],'output':self.merge_outputs(groups[row[0]])}
            after=rows[-1][2]
