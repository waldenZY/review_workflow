"""Revision-4 project API: standard library, plans, isolated tests and group runs."""
import copy
import threading

from fastapi.responses import FileResponse

from .data_io import at, fields_for, write_xlsx
from .engine import SYSTEM, USER, check_schema, evaluate, export_records, messages_for, validate_templates
from .models import (CloneRunRequest, CompileOutputTemplate, ExportRequest, ObjectDelete,
                     PlanPreview, PlanSave, RunRequest, StandardSave, StandardTest)
from .output_templates import compile_standards, compile_template, decode_evaluation, pointer
from .store import BUSY, Conflict, dumps, now, uid


def run_meta(run):
    return {k: run.get(k) for k in ('id','project_id','title','created_at','status','archived_at','parent_run_id','mode')} | {
        'dataset_name':run['snapshot']['dataset']['filename'], 'progress':run['progress'],
        'row_progress':run.get('row_progress'), 'auto_retry_limit':3,
        'execution_config':run['snapshot']['execution_config']}


def contract(standards):
    return compile_standards(standards)


def group_messages(group,row):
    return messages_for(group,row)


def register(app, router, store):
    test_lock = threading.Lock()

    def validate_standard(value):
        if value['method']=='llm':
            compile_template(value['output_template'])
        elif not value['target_field'].startswith('/'):
            raise ValueError('程序规则必须设置 JSON Pointer 作用字段。')
        elif value['method']=='json_schema':
            check_schema(value['rule_schema'])
        return value

    @router.get('/standards')
    def standards(project_id:str, include_archived:bool=False):
        return {'items':store().entities(project_id,'standard',include_archived)}

    @router.post('/standards/compile-output-template')
    def compile_output(project_id:str,body:CompileOutputTemplate):
        store().project(project_id)
        return compile_template(body.template)

    @router.post('/standards')
    def save_standard(project_id:str,body:StandardSave):
        validate_standard(body.model_dump())
        return store().save(project_id,'standard',body.model_dump(exclude={'id'}),body.id)

    @router.get('/execution-plans/defaults')
    def defaults(project_id:str):
        store().project(project_id)
        return {'system_template':SYSTEM,'user_template':USER}

    def resolve_plan(pid, plan, active=True):
        validate_templates(plan['system_template'],plan['user_template'])
        groups=[]; names=set()
        for g in plan['groups']:
            ss=[]
            for ref in g['standards']:
                s=store().get(pid,ref['standard_id'],ref['version'],'standard',active)
                validate_standard(s)
                if s['name'] in names:
                    raise ValueError('方案中标准名称必须唯一：'+s['name'])
                names.add(s['name']); ss.append(s)
            groups.append({'id':g['id'],'name':g['name'],'standards':ss,
                'system_template':plan['system_template'],'user_template':plan['user_template'],**contract(ss)})
        return groups

    @router.get('/execution-plans')
    def plans(project_id:str,include_archived:bool=False):
        return {'items':store().entities(project_id,'execution_plan',include_archived)}

    @router.post('/execution-plans/compile')
    def compile_plan(project_id:str,body:PlanSave):
        with store().lock:
            store().project(project_id,True)
            if body.id:
                store().get(project_id,body.id,kind='execution_plan',active=True)
            return {'groups':resolve_plan(project_id,body.model_dump())}

    @router.post('/execution-plans')
    def save_plan(project_id:str,body:PlanSave):
        with store().lock:
            resolve_plan(project_id,body.model_dump())
            return store().save(project_id,'execution_plan',body.model_dump(exclude={'id'}),body.id)

    # Explicit resource routes share the same guarded lifecycle as the management dialog.
    def register_resource(path,kind):
        def get(pid,oid,version=None):
            return store().get(pid,oid,version,kind)
        def read(project_id:str,object_id:str,version:int|None=None):
            return get(project_id,object_id,version)
        def versions(project_id:str,object_id:str):
            get(project_id,object_id)
            return store().versions(project_id,object_id)
        def refs(project_id:str,object_id:str):
            get(project_id,object_id)
            return {'references':store().reference_list(project_id,kind,object_id)}
        def archive(project_id:str,object_id:str):
            return app.state.history.archive(project_id,kind,object_id)
        def restore(project_id:str,object_id:str):
            return app.state.history.archive(project_id,kind,object_id,True)
        def delete(project_id:str,object_id:str,body:ObjectDelete):
            if body.confirm_id!=object_id:
                raise ValueError('确认 ID 不匹配。')
            return app.state.history.delete(project_id,kind,object_id)
        for suffix,fn,methods in (('',read,['GET']),('/versions',versions,['GET']),('/references',refs,['GET']),
                                  ('/archive',archive,['POST']),('/restore',restore,['POST']),('',delete,['DELETE'])):
            router.add_api_route(path+'/{object_id}'+suffix,fn,methods=methods,name=kind+fn.__name__)
    register_resource('/standards','standard')
    register_resource('/execution-plans','execution_plan')

    def bind(pid,dataset,groups,bindings):
        if len(bindings)!=len({b['group_id'] for b in bindings}) or {b['group_id'] for b in bindings}!={g['id'] for g in groups}:
            raise ValueError('请为全部组设置评测字段，不可遗漏或重复。')
        by_id={b['group_id']:b for b in bindings}
        available={f['path'] for f in dataset['fields']}
        for group in groups:
            b=by_id[group['id']]
            if b['evaluation_fields'] is not None:
                mapping = {'evaluation_fields': b['evaluation_fields']}
            else:
                mapping = store().get(pid, b['mapping_id'], b['mapping_version'], 'mapping', True)
                if mapping['dataset_id'] != dataset['id']:
                    raise ValueError('组「' + group['name'] + '」的字段方案属于其他数据集，请重新选择。')
            if not set(mapping['evaluation_fields'])<=available:
                raise ValueError('组「' + group['name'] + '」选中了当前数据集中不存在的字段。')
            for s in group['standards']:
                if s['method']!='llm' and s['target_field'] not in mapping['evaluation_fields']:
                    raise ValueError('标准「'+s['name']+'」的程序作用字段未选中。')
            group['mapping']=mapping
        return groups

    def snapshot(pid,body):
        dataset=store().get(pid,body.dataset_id,body.dataset_version,'dataset',True)
        plan=store().get(pid,body.plan_id,body.plan_version,'execution_plan',True)
        groups=bind(pid,dataset,resolve_plan(pid,plan),[b.model_dump() for b in body.bindings])
        return {'project':{k:store().project(pid,True)[k] for k in ('id','name')},'dataset':dataset,'plan':plan,'groups':groups,
                'output_fields':[{**f,'group_id':g['id'],'group_name':g['name']} for g in groups for f in g['output_fields']]}

    @router.post('/execution-plans/preview')
    def preview(project_id:str,body:PlanPreview):
        with store().lock:
            store().project(project_id,True)
            if body.plan.id:
                store().get(project_id,body.plan.id,kind='execution_plan',active=True)
            dataset=store().get(project_id,body.dataset_id,body.dataset_version,'dataset',True)
            groups=bind(project_id,dataset,resolve_plan(project_id,body.plan.model_dump()),[b.model_dump() for b in body.bindings])
            row=store().data_row(project_id,dataset['material_id'],body.row_id)
            result = []
            for group in groups:
                model_required = any(standard['method'] == 'llm' for standard in group['standards'])
                messages = group_messages(group, row) if model_required else []
                result.append({**group, 'messages': messages, 'model_required': model_required,
                               'request': app.state.gateway.preview(messages, app.state.settings.current) if model_required else None})
            return {'groups': result, 'draft': True}

    @router.post('/standards/test')
    def test_standard(project_id:str,body:StandardTest):
        if not test_lock.acquire(blocking=False):
            raise Conflict('单标准测试通道正在使用，请等待当前测试结束。')
        test_id=None
        try:
            with store().lock:
                store().project(project_id,True)
                standard=validate_standard(body.standard.model_dump())
                saved=None; refs=[]
                if standard.get('id'):
                    saved=store().get(project_id,standard['id'],body.saved_version,'standard',True)
                    refs.append({'kind':'standard','id':saved['id'],'version':saved['version']})
                keys=set(StandardSave.model_fields)-{'id'}
                draft=saved is None or any(standard[k]!=saved.get(k) for k in keys)
                standard.update(id=standard.get('id') or 'unsaved_standard',version=saved['version'] if saved else None)
                if body.source=='dataset':
                    if not all((body.dataset_id,body.dataset_version,body.mapping_id,body.mapping_version,body.row_id)):
                        raise ValueError('请选择数据集版本、字段方案版本和一条记录。')
                    dataset=store().get(project_id,body.dataset_id,body.dataset_version,'dataset',True)
                    mapping=store().get(project_id,body.mapping_id,body.mapping_version,'mapping',True)
                    if mapping['dataset_id']!=dataset['id']:
                        raise ValueError('字段方案不属于所选数据集。')
                    row=store().data_row(project_id,dataset['material_id'],body.row_id)
                    refs.extend([{'kind':'dataset','id':dataset['id'],'version':dataset['version']},{'kind':'mapping','id':mapping['id'],'version':mapping['version']}])
                else:
                    original=decode_evaluation(body.json_text)
                    available={f['path'] for f in fields_for([original])}
                    fields=body.evaluation_fields if body.evaluation_fields is not None else ['/'+pointer(k) for k in original]
                    if not fields or len(fields)!=len(set(fields)) or not set(fields)<=available:
                        raise ValueError('请选择 JSON 中存在且不重复的字段。')
                    mapping={'evaluation_fields':fields}
                    row={'original':original,'row_id':None,'source_row':None}
                if standard['method']!='llm' and standard['target_field'] not in mapping['evaluation_fields']:
                    raise ValueError('程序规则的作用字段未选中。')
                group={'id':'standard_test','name':'单标准测试','standards':[standard],'mapping':mapping,
                       'system_template':SYSTEM,'user_template':USER,**contract([standard])}
                conn=app.state.settings.capture() if standard['method']=='llm' else None
                value={'title':standard['name']+' · 测试','purpose':'standard_test','status':'design_running','draft':draft,
                       'references':refs,'snapshot':{'group':group,'row':row,'model_config':conn.public() if conn else {}},'started_at':now()}
                test=store().save(project_id,'standard_test',value); test_id=test['id']
            result=evaluate(group,row,app.state.gateway,conn)
            value.update(status=result['status'],finished_at=now(),attempt=app.state.settings.sanitize(result))
            return store().save(project_id,'standard_test',value,test_id)
        except Exception as exc:
            if test_id:
                value.update(status='interrupted',finished_at=now(),error=app.state.settings.sanitize(str(exc)))
                store().save(project_id,'standard_test',value,test_id)
            raise
        finally:
            test_lock.release()

    @router.get('/standard-tests')
    def standard_tests(project_id:str,standard_id:str='',offset:int=0,limit:int=20):
        if offset<0 or limit not in (10,20,50):
            raise ValueError('无效分页参数。')
        store().project(project_id)
        condition="e.project_id=? AND e.kind='standard_test' AND e.version=(SELECT MAX(v.version) FROM entities v WHERE v.project_id=e.project_id AND v.id=e.id)"
        args=[project_id]
        if standard_id:
            condition+=' AND EXISTS(SELECT 1 FROM object_refs o WHERE o.project_id=e.project_id AND o.owner_id=e.id AND o.target_kind=\'standard\' AND o.target_id=?)'; args.append(standard_id)
        import json
        with store().read() as db:
            total=db.execute('SELECT COUNT(*) FROM entities e WHERE '+condition,args).fetchone()[0]
            items=[json.loads(r[0]) for r in db.execute('SELECT e.summary FROM entities e WHERE '+condition+' ORDER BY e.rowid DESC LIMIT ? OFFSET ?',(*args,limit,offset))]
        return {'items':items,'total':total}

    @router.get('/standard-tests/{test_id}')
    def standard_test_detail(project_id:str,test_id:str):
        return store().get(project_id,test_id,kind='standard_test')

    def check_snapshot(pid,snap):
        for key,kind in (('dataset','dataset'),('plan','execution_plan')):
            obj=snap[key];store().get(pid,obj['id'],obj['version'],kind,True)
        for g in snap['groups']:
            mapping = g['mapping']
            if mapping.get('id'):
                store().get(pid, mapping['id'], mapping['version'], 'mapping', True)
            for s in g['standards']:
                store().get(pid,s['id'],s['version'],'standard',True)

    def original_connection(run):
        cfg=run['snapshot']['model_config']
        conn=app.state.settings.capture(cfg['profile_id']) if cfg else None
        if conn and conn.public()['fingerprint']!=cfg['fingerprint']:
            raise Conflict('原模型公开参数已变化，请恢复参数或新建复测；更换密钥不影响重试。')
        return conn

    def enqueue(pid,snap,selected,mode='batch',parent=None,connection=None,preserve=False):
        if app.state.runner.stop.is_set():
            raise Conflict('执行队列已停止，请维护后重启。')
        snap=copy.deepcopy(snap)
        if not preserve:
            connection=app.state.settings.capture() if any(s['method']=='llm' for g in snap['groups'] for s in g['standards']) else None
            snap['model_config']=connection.public() if connection else {}
            snap['execution_config']={'batch_concurrency':connection.batch_concurrency if connection else 1}
        snap['auto_retry_policy']={'additional_attempts_per_group_task':3,'enabled':mode=='batch'}
        value={'id':uid('run'),'project_id':pid,'title':snap['plan']['title']+' · '+now(),'created_at':now(),
               'started_at':None,'finished_at':None,'status':'queued','mode':mode,'error':None,'cancel_requested':False,
               'snapshot':snap,'parent_run_id':parent,'auto_retry_limit':3}
        run=store().create_run(value,selected)
        app.state.runner.submit(run,connection)
        store().event(pid,run['id'],'run_created')
        return run_meta(run)

    @router.post('/runs')
    def create_run(project_id:str,body:RunRequest):
        with store().lock:
            snap=snapshot(project_id,body)
            if body.selection_mode=='all':
                if body.row_ids or body.mode=='trial':
                    raise ValueError('全量模式不能指定记录；试跑必须选定一条记录。')
                selected=None
            else:
                store().validate_rows(project_id,snap['dataset']['material_id'],body.row_ids)
                selected=body.row_ids
            if body.mode=='trial' and len(selected)!=1:
                raise ValueError('试跑只执行选中的第一条记录，请明确提交一条。')
            return enqueue(project_id,snap,selected,body.mode)

    def status(run,stages=None):
        if stages is None:
            run,stages=app.state.runner.observe(run['project_id'],run['id'],summary=True)
        return {**run_meta(run),'error':run.get('error'),'finished_at':run.get('finished_at'),
                'retryable':run['progress']['succeeded']<run['progress']['selected'],
                'current_round':run['current_round'],'round_progress':run['round_progress'],'stage_counts':stages}

    @router.get('/run-history')
    def history(project_id:str,offset:int=0,limit:int=20,status:str='',dataset_id:str='',include_archived:bool=False):
        if offset<0 or limit not in (10,20,50):
            raise ValueError('无效分页参数。')
        store().project(project_id)
        conditions=['r.project_id=?'];args=[project_id]
        if not include_archived: conditions.append('l.archived_at IS NULL')
        if status: conditions.append('r.status=?');args.append(status)
        if dataset_id:
            conditions.append("EXISTS(SELECT 1 FROM object_refs o WHERE o.project_id=r.project_id AND o.owner_id=r.id AND o.target_kind='dataset' AND o.target_id=?)");args.append(dataset_id)
        query=' FROM runs r LEFT JOIN lifecycle l ON l.project_id=r.project_id AND l.id=r.id WHERE '+' AND '.join(conditions)
        with store().read() as db:
            total=db.execute('SELECT COUNT(*)'+query,args).fetchone()[0]
            ids=[r[0] for r in db.execute('SELECT r.id'+query+' ORDER BY r.rowid DESC LIMIT ? OFFSET ?',(*args,limit,offset))]
        return {'items':[run_meta(store().get_run(project_id,i,summary=True)) for i in ids],'total':total}

    @router.get('/runs/{run_id}')
    def get_run(project_id:str,run_id:str):
        run,stages=app.state.runner.observe(project_id,run_id)
        return {**status(run,stages),'snapshot':run['snapshot'],'exports':store().exports(project_id,run_id)}

    @router.get('/runs/{run_id}/progress')
    def progress(project_id:str,run_id:str):
        run,stages=app.state.runner.observe(project_id,run_id,summary=True)
        return status(run,stages)

    @router.get('/runs/{run_id}/rounds')
    def rounds(project_id:str,run_id:str):
        return {'rounds':store().rounds(project_id,run_id)}

    @router.get('/runs/{run_id}/statistics')
    def statistics(project_id: str, run_id: str):
        return store().run_statistics(project_id, run_id)

    @router.get('/runs/{run_id}/records')
    def records(project_id:str,run_id:str,offset:int=0,limit:int=20,status_group:str='all',error_stage:str='',group_id:str=''):
        if offset<0 or limit not in (10,20,50): raise ValueError('无效分页参数。')
        return store().record_page(project_id,run_id,offset,limit,status_group,error_stage,group_id)

    @router.get('/runs/{run_id}/records/{row_id}')
    def record(project_id:str,run_id:str,row_id:str):
        return store().record_detail(project_id,run_id,row_id)

    @router.get('/runs/{run_id}/configuration')
    def configuration(project_id:str,run_id:str,include_rows:bool=True):
        run=store().get_run(project_id,run_id)
        return {'snapshot':run['snapshot'],**({'row_ids':store().row_ids(project_id,run_id)} if include_rows else {})}

    @router.post('/runs/{run_id}/cancel')
    def cancel(project_id:str,run_id:str): return status(store().cancel_run(project_id,run_id))

    @router.post('/runs/{run_id}/retry')
    def retry(project_id:str,run_id:str):
        with store().lock:
            if app.state.runner.stop.is_set(): raise Conflict('队列已停止，请维护后重启。')
            run=store().get_run(project_id,run_id,True)
            check_snapshot(project_id,run['snapshot']);conn=original_connection(run)
            run=store().retry_run(project_id,run_id)
            app.state.runner.submit(run,conn)
            return status(run)

    @router.post('/runs/{run_id}/clone')
    def clone(project_id:str,run_id:str,body:CloneRunRequest|None=None):
        with store().lock:
            run=store().get_run(project_id,run_id,True)
            if run['status'] in BUSY: raise Conflict('请等待原运行结束。')
            check_snapshot(project_id,run['snapshot']);conn=original_connection(run)
            selected=body.row_ids if body and body.row_ids else store().row_ids(project_id,run_id)
            store().validate_rows(project_id,run['snapshot']['dataset']['material_id'],selected,run_id)
            return enqueue(project_id,run['snapshot'],selected,run['mode'],run_id,conn,True)

    @router.post('/runs/{run_id}/rerun')
    def rerun(project_id:str,run_id:str,body:RunRequest):
        with store().lock:
            old=store().get_run(project_id,run_id,True)
            if old['status'] in BUSY: raise Conflict('请等待原运行结束。')
            snap=snapshot(project_id,body)
            if snap['dataset']['id']!=old['snapshot']['dataset']['id'] or body.selection_mode!='selected':
                raise ValueError('复测必须选择原数据集中的原运行记录。')
            store().validate_rows(project_id,snap['dataset']['material_id'],body.row_ids,run_id)
            if body.mode=='trial' and len(body.row_ids)!=1: raise ValueError('试跑只执行一条记录。')
            return enqueue(project_id,snap,body.row_ids,body.mode,run_id)

    @router.post('/runs/{run_id}/export')
    def export(project_id:str,run_id:str,body:ExportRequest):
        with store().export_lease(project_id,run_id) as run:
            columns,iterator=export_records(store(),run,body.original_fields,body.include_raw_response)
            folder=app.state.history.project_dir(project_id)/'runs'/run_id
            app.state.history.validate_path(folder,app.state.history.project_dir(project_id));folder.mkdir(parents=True,exist_ok=True)
            target=folder/(uid('export')+'.'+body.format);manifest=target.with_suffix('.manifest.json');count=0
            try:
                if body.format=='jsonl':
                    with target.open('w',encoding='utf-8',newline='\n') as stream:
                        for row in iterator:
                            stream.write(dumps(row)+'\n');count+=1
                else:
                    result_fields=run['snapshot']['output_fields'];groups=run['snapshot']['groups']
                    metadata=['eval.project_id','eval.run_id','eval.row_id','eval.source_row','eval.status','eval.mode','eval.output','eval.groups']
                    extras=metadata+['result'+f['path'] for f in result_fields]+[prefix+'/'+pointer(g['name'])+'/'+key for g in groups for key in ('status','error') for prefix in ('group',)]
                    headers=columns+extras
                    if len(headers)!=len(set(headers)):
                        raise ValueError('原始列与导出列名冲突，请调整原始列名或选择 JSONL。')
                    def rows():
                        nonlocal count
                        for r in iterator:
                            count+=1;e=r['evaluation'];by_id={g['group_id']:g for g in e['groups']}
                            yield [*[r['original'].get(c) for c in columns],project_id,run_id,e['row_id'],e['source_row'],e['status'],e['mode'],e['output'],e['groups'],
                                   *[at(e['output'],f['path'],None) for f in result_fields],
                                   *[by_id[g['id']].get(key) for g in groups for key in ('status','error')]]
                    write_xlsx(target,rows(),headers)
                info={'format':body.format,'file':target.name,'rows':count,'original_fields':columns,'include_raw_response':body.include_raw_response,
                      'model_config':run['snapshot']['model_config'],'execution_config':run['snapshot']['execution_config']}
                manifest.write_text(dumps(info),encoding='utf-8');store().register_export(project_id,run_id,info)
            except Exception:
                target.unlink(missing_ok=True);manifest.unlink(missing_ok=True);raise
            return FileResponse(target,filename=run_id+'.'+body.format,media_type='application/x-ndjson' if body.format=='jsonl' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    return get_run
