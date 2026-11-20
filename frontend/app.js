/* Revision 4: project library -> pinned groups -> per-run inputs. */
(() => {
  'use strict';
  const root=document.getElementById('root');
  const pretty=v=>JSON.stringify(v,null,2),clone=v=>JSON.parse(JSON.stringify(v));
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const active=v=>['queued','running','cancelling'].includes(v);
  const titles=['项目','数据与字段','标准库','批跑方案','执行与导出','历史记录','模型设置'];
  const labels={queued:'排队中',running:'执行中',cancelling:'正在取消',cancelled:'已取消',completed:'完成',completed_with_errors:'完成，有异常',call_failed:'调用失败',parse_failed:'JSON 解析失败',schema_failed:'结构校验失败',interrupted:'中断',pending:'未执行',not_run:'未选中',failed:'任务异常',error:'异常',design_running:'测试中'};
  const s={page:0,epoch:0,project:null,projects:[],catalog:{datasets:[],mappings:[],standards:[],plans:[]},busy:false,error:'',notice:'',modal:null,llm:{profiles:[]},versions:{},defaults:null};
  let templateSeq=0,previewSeq=0,rowSeq=0,templateTimer,previewTimer,polling=false;
  function uid() {
    // The randomUUID method can be unavailable when the workbench is served over plain HTTP.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return `group_${hex}`;
  }
  const blankStandard=()=>({name:'',description:'',method:'llm',output_template:'',target_field:'',rule_schema:{type:'object'}});
  function reset(){Object.assign(s,{versions:{},backModal:null,addStandard:'',upload:null,sheet:'',header:1,testsAll:false,historyArchived:false,dataset:null,mapping:{title:'字段方案',evaluation_fields:[]},mappingDirty:false,rows:[],offset:0,size:20,selected:[],standard:blankStandard(),standardDirty:false,standardVersion:null,plan:null,planDirty:false,bindings:{},groupChecks:[],templateCheck:null,testForm:null,testResult:null,tests:[],testOffset:0,testTotal:0,activeView:null,history:[],historyOffset:0,historyTotal:0,historyStatus:'',historyDataset:'',selectionMode:'selected',parentRun:null,includeRaw:false,exportFields:null});++templateSeq;++previewSeq;++rowSeq;clearTimeout(templateTimer);clearTimeout(previewTimer);}
  reset();
  const key=kind=>`eval.v1_1.r4.${s.project?.id}.${kind}`;
  const remember=(kind,v)=>localStorage.setItem(key(kind),JSON.stringify({id:v.id,version:v.version}));
  const recalled=kind=>{try{return JSON.parse(localStorage.getItem(key(kind))||'null');}catch{return null;}};
  const ro=()=>!s.project||!!s.project.archived_at;
  const badge=v=>`<span class="badge ${/^[a-z_]+$/.test(v||'')?v:''}">${esc(labels[v]||v)}</span>`;
  const json=v=>`<pre class="json-view">${esc(pretty(v))}</pre>`;
  const field=(label,control,hint='')=>`<label class="field"><span>${esc(label)}</span>${control}${hint?`<small>${esc(hint)}</small>`:''}</label>`;
  const input=(bind,v,type='text',attrs='')=>`<input type="${type}" data-bind="${esc(bind)}" value="${esc(v)}" ${attrs}>`;
  const area=(bind,v,rows=5)=>`<textarea data-bind="${esc(bind)}" rows="${rows}">${esc(v)}</textarea>`;
  const select=(bind,values,value)=>`<select data-bind="${esc(bind)}">${values.map(([v,t])=>`<option value="${esc(v)}" ${String(value)===String(v)?'selected':''}>${esc(t)}</option>`).join('')}</select>`;
  const check=(bind,v,label)=>`<label class="check-label"><input type="checkbox" data-bind="${esc(bind)}" ${v?'checked':''}>${esc(label)}</label>`;
  const btn=(action,text,disabled=false,attrs='',primary=false)=>`<button class="button ${primary?'primary':''}" data-action="${action}" ${disabled||s.busy?'disabled':''} ${attrs}>${text}</button>`;
  const panel=(title,description,body)=>`<section class="panel"><header class="panel-heading"><div><h2>${esc(title)}</h2><p>${esc(description)}</p></div></header>${body}</section>`;
  const details=(title,body)=>`<details class="advanced"><summary>${esc(title)}</summary>${body}</details>`;
  const empty=text=>`<div class="empty-state">${esc(text)}</div>`;
  const table=html=>`<div class="scroll-table"><div class="table-scroll-top" hidden><div></div></div><div class="table-scroll" tabindex="0">${html}</div></div>`;
  const cell=v=>{const text=typeof v==='string'?v:JSON.stringify(v);return `<details class="result-cell"><summary>${esc(text?.slice(0,120)??'—')}${text?.length>120?'…':''}</summary><pre>${esc(text??'—')}</pre></details>`;};
  const ptr=(v,path)=>{for(const k of path.slice(1).split('/'))v=v?.[k.replaceAll('~1','/').replaceAll('~0','~')];return v;};
  const dirty=()=>s.standardDirty||s.planDirty||s.mappingDirty;
  const allowDiscard=()=>!dirty()||confirm('当前有未保存草稿，确定切换并放弃改动？');
  const options=items=>[['','请选择'],...items.map(v=>[v.id,`${v.name||v.title||v.filename}${v.version?' · v'+v.version:''}${v.archived_at?'（归档）':''}`])];
  async function api(path,method='GET',body){const abort=method==='GET'?new AbortController():null,timer=abort?setTimeout(()=>abort.abort(),15000):null;try{const r=await fetch('/api/v1'+path,{method,signal:abort?.signal,headers:body===undefined||body instanceof FormData?undefined:{'Content-Type':'application/json'},body:body===undefined?undefined:body instanceof FormData?body:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw new Error((typeof v.detail==='string'?v.detail:pretty(v.detail||v))+(v.references?'\n引用：'+pretty(v.references):''));return v;}finally{clearTimeout(timer);}}
  async function papi(path,method='GET',body){const epoch=s.epoch,pid=s.project?.id;if(!pid)throw new Error('先打开项目。');const value=await api(`/projects/${pid}${path}`,method,body);if(epoch!==s.epoch)throw Object.assign(new Error('项目已切换'),{stale:true});return value;}
  async function action(task){if(s.busy)return;s.busy=true;s.error='';s.notice='';render();try{await task();}catch(e){if(!e.stale)s.error=e.message;}finally{s.busy=false;render();}}
  async function catalog(){const c=await papi('/catalog');s.catalog=c;s.project=c.project;}
  async function openProject(pid){++s.epoch;s.modal=null;reset();s.project=s.projects.find(p=>p.id===pid)||null;if(!s.project)return;localStorage.setItem('eval.v1_1.r4.project',pid);await catalog();s.defaults=await papi('/execution-plans/defaults');for(const [kind,items] of [['dataset',s.catalog.datasets],['standard',s.catalog.standards],['plan',s.catalog.plans]]){const old=recalled(kind),item=items.find(v=>v.id===old?.id)||items[0];if(!item)continue;const v=await papi(`/entities/${item.id}?version=${old?.id===item.id?old.version:item.version}`);if(kind==='dataset')await setDataset(v);if(kind==='standard')await setStandard(v);if(kind==='plan')await setPlan(v);}s.page=1;}
  async function setDataset(v){s.dataset=v;s.offset=0;s.selected=[];s.parentRun=null;s.bindings={};s.mapping={title:'字段方案',dataset_id:v.id,evaluation_fields:[]};s.mappingDirty=false;remember('dataset',v);await loadRows();}
  async function loadRows(){if(!s.dataset)return;const seq=++rowSeq,ds=s.dataset;const v=await papi(`/datasets/${ds.id}/rows?version=${ds.version}&offset=${s.offset}&limit=${s.size}`);if(seq===rowSeq)s.rows=v.rows;}
  async function loadVersions(v){s.versions[v.id]=await papi(`/entities/${v.id}/versions`);}
  async function setStandard(v){s.standard=clone(v);s.standardVersion=v.version;s.standardDirty=false;remember('standard',v);await loadVersions(v);scheduleTemplate();}
  const newPlan=()=>({title:'新的批跑方案',system_template:s.defaults?.system_template||'',user_template:s.defaults?.user_template||'',groups:[]});
  async function setPlan(v){s.plan=clone(v);s.planDirty=false;s.bindings={};remember('plan',v);await loadVersions(v);for(const ref of v.groups.flatMap(g=>g.standards))s.versions[ref.standard_id]=await papi(`/standards/${ref.standard_id}/versions`);schedulePlan();}
  const standardBody=()=>Object.fromEntries(['id','name','description','method','output_template','target_field','rule_schema'].filter(k=>s.standard[k]!==undefined).map(k=>[k,s.standard[k]]));
  const planBody=()=>Object.fromEntries(['id','title','system_template','user_template','groups'].filter(k=>s.plan[k]!==undefined).map(k=>[k,s.plan[k]]));
  function scheduleTemplate(){const seq=++templateSeq,epoch=s.epoch;clearTimeout(templateTimer);s.templateCheck=null;if(s.standard.method!=='llm')return;templateTimer=setTimeout(async()=>{try{const v=await papi('/standards/compile-output-template','POST',{template:s.standard.output_template});if(seq!==templateSeq||epoch!==s.epoch)return;s.templateCheck={value:v};}catch(e){if(seq!==templateSeq||epoch!==s.epoch)return;s.templateCheck={error:e.message};}const target=document.getElementById('template-check');if(target)target.innerHTML=templateCheck();},400);}
  function schedulePlan(){const seq=++previewSeq,epoch=s.epoch;clearTimeout(previewTimer);s.groupChecks=[];if(!s.plan?.groups.length)return;previewTimer=setTimeout(async()=>{try{const v=await papi('/execution-plans/compile','POST',planBody());if(seq!==previewSeq||epoch!==s.epoch)return;s.groupChecks=v.groups;}catch(e){if(seq!==previewSeq||epoch!==s.epoch)return;s.groupChecks=[{error:e.message}];}const target=document.getElementById('plan-check');if(target)target.innerHTML=planChecks();},400);}
  const templateCheck=()=>!s.templateCheck?'<p class="muted">等待合法模板…</p>':s.templateCheck.error?`<p class="message error">${esc(s.templateCheck.error)}</p>`:details('编译后的结构与示例',json(s.templateCheck.value));
  const planChecks=()=>s.groupChecks.map(g=>g.error?`<p class="message error">${esc(g.error)}</p>`:details(g.name+' · 只读返回结构',json({schema:g.response_schema,example:g.example_preview.value,columns:g.output_fields}))).join('');
  const versionControls=(kind,v)=>v.id?field('历史版本',select(`version.${kind}`,(s.versions[v.id]||[]).map(x=>[x.version,`v${x.version} · ${x.created_at.slice(0,16).replace('T',' ')}`]),v.version)):'';
  const picker=(kind,label,items,v)=>`<div class="toolbar">${field(label,select('choose.'+kind,options(items),v?.id||''))}${btn('manage','管理',!s.project,`data-kind="${kind}"`)}</div>`;
  const pager=(type,offset,total,size=20)=>`<div class="toolbar"><span>共 ${total} 条 · 第 ${Math.floor(offset/size)+1} 页</span>${btn('prev','上一页',offset===0,`data-type="${type}"`)}${btn('next','下一页',offset+size>=total,`data-type="${type}"`)}${['data','results'].includes(type)?select('size.'+type,[[10,'10 条／页'],[20,'20 条／页'],[50,'50 条／页']],size):''}</div>`;
  async function loadProjects() {
    const result = await api(`/projects?include_archived=${!!s.projectsAll}`);
    s.projects = result.projects;
  }

  let projectDialogOpener = null;

  function closeModal() {
    if (s.modal?.opener) {
      if (s.busy) return;
      projectDialogOpener = s.modal.opener;
    }
    s.modal = s.backModal || null;
    s.backModal = null;
    s.error = '';
    render();
  }

  function focusProjectDialog() {
    const dialog = root.querySelector('.project-dialog');
    if (dialog && (document.activeElement === dialog || !dialog.contains(document.activeElement))) {
      (dialog.querySelector('input, button:not(:disabled)') || dialog).focus();
    } else if (!s.modal && !s.busy && projectDialogOpener) {
      const { action, id } = projectDialogOpener;
      const opener = [...root.querySelectorAll('[data-action]')].find(button =>
        button.dataset.action === action && button.dataset.id === id);
      (opener || root.querySelector('[data-action="project-new"]'))?.focus();
      projectDialogOpener = null;
    }
  }

  async function submitProjectDialog() {
    const modal = s.modal;
    const { form, scope } = modal;
    if (modal.type === 'projectDelete') {
      if (scope.busy.length || form.confirmName !== scope.project.name) return;
      const { id } = scope.project;
      await api(`/projects/${id}`, 'DELETE', { confirm_name: form.confirmName });
      if (s.project?.id === id) {
        ++s.epoch;
        s.project = null;
        localStorage.removeItem('eval.v1_1.r4.project');
        reset();
      }
      s.projects = s.projects.filter(project => project.id !== id);
      s.notice = '项目已删除。';
    } else {
      if (!form.name.trim()) return;
      const project = await api(`/projects${form.id ? '/' + form.id : ''}`, form.id ? 'PUT' : 'POST', {
        name: form.name.trim(), description: form.description
      });
      if (s.project?.id === project.id) s.project = project;
      s.notice = form.id ? '项目已更新。' : '项目已创建。';
    }
    projectDialogOpener = modal.opener;
    s.modal = null;
    await loadProjects();
  }

  function projectCard(project) {
    const current = s.project?.id === project.id;
    const attrs = `data-id="${esc(project.id)}"`;
    const counts = [
      ['dataset', '数据集'], ['standard', '标准'], ['execution_plan', '方案'], ['run', '运行']
    ];
    return `<article class="project-card${current ? ' current' : ''}${project.archived_at ? ' archived' : ''}">
      <header><h2>${esc(project.name)}</h2>${current ? badge('当前项目') : ''}${project.archived_at ? badge('已归档') : ''}</header>
      ${project.description ? `<p>${esc(project.description)}</p>` : ''}
      <dl class="project-counts">${counts.map(([key, label]) => `<div><dt>${label}</dt><dd>${project.counts?.[key] ?? 0}</dd></div>`).join('')}</dl>
      <div class="project-card-actions">
        ${btn('project-open', '打开项目', false, attrs, true)}
        ${btn('project-edit', '编辑', !!project.archived_at, attrs)}
        ${btn('project-archive', project.archived_at ? '恢复' : '归档', false, attrs)}
        <button class="button danger" data-action="project-delete" ${attrs} ${s.busy ? 'disabled' : ''}>删除</button>
      </div>
    </article>`;
  }

  function projectsPage() {
    return `<div class="project-list-controls">
      ${check('projectsAll', s.projectsAll, '显示归档项目')}
      ${btn('project-new', '新建项目', false, '', true)}
    </div>${s.projects.length
      ? `<div class="project-grid">${s.projects.map(projectCard).join('')}</div>`
      : empty('暂无项目。创建项目后即可导入数据和配置标准。')}`;
  }
  function dataTable(){if(!s.dataset)return empty('先导入或选择数据集。');return table(`<table><thead><tr><th>选择</th><th>源行</th>${s.dataset.original_columns.map(k=>`<th>${esc(k)}</th>`).join('')}</tr></thead><tbody>${s.rows.map(r=>`<tr><td><input aria-label="选择源行 ${r.source_row}" type="checkbox" data-row="${esc(r.row_id)}" ${s.selected.includes(r.row_id)?'checked':''}></td><td>${r.source_row}</td>${s.dataset.original_columns.map(k=>`<td>${cell(r.original[k])}</td>`).join('')}</tr>`).join('')}</tbody></table>`)+pager('data',s.offset,s.dataset.row_count,s.size);}
  function dataPage(){return panel('导入数据','JSON、JSONL、XLSX，单文件最大 100 MiB。',`<div class="toolbar"><input id="upload" type="file" accept=".json,.jsonl,.xlsx" ${ro()?'disabled':''}>${field('XLSX 工作表（可留空）',input('sheet',s.sheet||''))}${field('表头行',input('header',s.header||1,'number','min="1"'))}${btn('import','导入',ro(),'',true)}</div>`)+panel('数据集与字段方案','字段方案属于数据集；各组可分别选择。',picker('dataset','数据集',s.catalog.datasets,s.dataset)+(s.dataset?picker('mapping','字段方案',s.catalog.mappings.filter(m=>m.dataset_id===s.dataset.id),s.mapping)+field('方案名称',input('mapping.title',s.mapping.title))+`<div class="field-list">${s.dataset.fields.map(f=>check('field.'+f.path,s.mapping.evaluation_fields.includes(f.path),f.path)).join('')}</div><div class="toolbar">${btn('mapping-new','新建字段方案',ro())}${btn('mapping-save','保存字段方案',ro(),'',true)}${btn('mapping-copy','另存为',ro())}</div>`:''))+panel('原始数据','展开内容查看完整值，选中记录可用于试跑。',dataTable());}

  function standardPage() {
    const standard = s.standard;
    const method = select('standard.method', [
      ['llm', 'LLM 判断'], ['non_empty', '程序：非空检查'], ['json_schema', '程序：JSON Schema']
    ], standard.method);
    const rule = standard.method === 'llm'
      ? field('输出规则模板', area('standard.output_template', standard.output_template, 7),
        '例如 {"等级":choice:["A","B"],"理由":value:string}') + `<div id="template-check">${templateCheck()}</div>`
      : field('作用字段（JSON Pointer）', input('standard.target_field', standard.target_field),
        '例如 /answer。执行时须在该组的字段方案中选中。') + (standard.method === 'json_schema'
          ? field('规则 Schema', area('standard.schemaText', standard.schemaText || pretty(standard.rule_schema), 7)) : '');
    const editor = `
      <div class="editor-controls">
        ${picker('standard', '选择标准', s.catalog.standards, standard)}
        ${versionControls('standard', standard)}
        ${btn('standard-new', '新建标准', ro())}
      </div>
      <div class="form-grid">
        ${field('标准名称', input('standard.name', standard.name))}
        ${field('评测方式', method)}
      </div>
      ${field('标准描述', area('standard.description', standard.description, 5), '填写判断依据、适用范围及信息不足时的处理方式。')}
      ${rule}
      <div class="toolbar editor-actions">
        ${btn('standard-save', '保存新版本', ro(), '', true)}
        ${btn('standard-test', '测试此标准', ro())}
        ${btn('standard-copy', '另存为', ro())}
        <small>${standard.id ? 'v' + standard.version : '未保存草稿'}${standard.id && s.standardDirty ? ' · 未保存改动' : ''}</small>
      </div>`;
    return panel('独立标准', '保存的新版本可在批跑方案中选择。', editor) +
      panel('测试记录', '查看单条标准的试评结果。', `
        <div class="toolbar">${btn('tests-load', '刷新测试记录')}${check('testsAll', s.testsAll, '显示本项目全部测试（含未保存草稿）')}</div>
        ${s.tests.map(test => `<div class="toolbar"><span>${esc(test.title)} · ${esc(test.created_at)} ${test.draft ? '（未保存草稿）' : ''}</span>${badge(test.status)}${btn('test-detail', '详情', false, `data-id="${test.id}"`)}</div>`).join('')}
        ${pager('tests', s.testOffset, s.testTotal)}`);
  }

  const selectedStandard = ref => s.catalog.standards.find(item => item.id === ref.standard_id);
  const pinnedStandard = ref => s.versions[ref.standard_id]?.find(item => item.version === ref.version) || selectedStandard(ref);

  function planGroup(group, index, groups) {
    const groupAttrs = `data-id="${esc(group.id)}"`;
    const otherGroups = groups.filter(item => item.id !== group.id).map(item => [item.id, item.name]);
    return `<article class="plan-group criterion-card">
      <div class="toolbar plan-group-controls">
        ${field('组名', input('group.' + group.id, group.name))}
        ${btn('group-up', '上移', index === 0 || ro(), groupAttrs)}
        ${btn('group-down', '下移', index === groups.length - 1 || ro(), groupAttrs)}
        ${btn('group-split', '整组拆开', group.standards.length < 2 || ro(), groupAttrs)}
      </div>
      ${group.standards.map(ref => {
        const latest = selectedStandard(ref);
        const attrs = `data-group="${esc(group.id)}" data-id="${esc(ref.standard_id)}"`;
        const versions = (s.versions[ref.standard_id] || [latest]).filter(Boolean).map(version => [version.version, 'v' + version.version]);
        return `<div class="standard-ref">
          <div class="standard-ref-name"><strong>${esc(pinnedStandard(ref)?.name || ref.standard_id)}</strong>${latest ? '' : '<small>已归档或不可用</small>'}</div>
          ${field('引用版本', select('refversion.' + ref.standard_id, versions, ref.version))}
          ${otherGroups.length ? field('移入其他组', select('move.' + ref.standard_id, [['', '选择目标组'], ...otherGroups], '')) : ''}
          <div class="standard-ref-actions">
            ${latest?.version > ref.version ? btn('ref-update', '更新引用版本', ro(), attrs) : ''}
            ${btn('ref-split', '单独执行', group.standards.length < 2 || ro(), attrs)}
            ${btn('ref-remove', '移除', ro(), attrs)}
          </div>
        </div>`;
      }).join('')}
      ${otherGroups.length ? `<div class="group-merge">${field('将本组全部合并到', select('merge.' + group.id, [['', '选择目标组'], ...otherGroups], ''))}</div>` : ''}
    </article>`;
  }

  function planPage() {
    if (!s.plan) s.plan = newPlan();
    const plan = s.plan;
    const used = new Set(plan.groups.flatMap(group => group.standards.map(ref => ref.standard_id)));
    return panel('分组批跑方案', '同组标准共用一次模型请求，执行时可为每组选择字段。', `
      <div class="editor-controls">
        ${picker('plan', '选择方案', s.catalog.plans, plan)}
        ${versionControls('plan', plan)}
        ${btn('plan-new', '新建方案', ro())}
      </div>
      ${field('方案名称', input('plan.title', plan.title))}
      <div class="toolbar">
        ${field('添加标准', select('addStandard', options(s.catalog.standards.filter(standard => !used.has(standard.id))), s.addStandard || ''))}
        ${btn('plan-add', '加入方案', ro() || !s.addStandard)}
        ${btn('merge-all', '全部合并', ro() || plan.groups.length < 2)}
        ${btn('split-all', '全部拆开', ro() || !plan.groups.some(group => group.standards.length > 1))}
      </div>
      ${plan.groups.map((group, index) => planGroup(group, index, plan.groups)).join('') || empty('从标准库添加标准，每条新增标准默认独立成组。')}
      <div class="messages-grid plan-messages">
        ${field('共用 System 消息', area('plan.system_template', plan.system_template, 10))}
        ${field('共用 User 消息', area('plan.user_template', plan.user_template, 10))}
      </div>
      <p class="muted">每组独立填入 {{criteria}}、{{fields}}、{{output_instructions}} 和 {{response_schema}}。</p>
      <div id="plan-check">${planChecks()}</div>
      <div class="toolbar editor-actions">
        ${btn('plan-save', '保存新版本', ro(), '', true)}
        ${btn('plan-copy', '另存为', ro())}
        <small>${plan.id ? 'v' + plan.version : '新方案'}${s.planDirty ? ' · 未保存改动' : ''}</small>
      </div>`);
  }

  function bindingsHTML() {
    const mappings = options(s.catalog.mappings.filter(mapping => mapping.dataset_id === s.dataset?.id));
    return (s.plan?.groups || []).map(group => {
      const binding = s.bindings[group.id];
      const versions = binding && (s.versions[binding.mapping_id] || [{version: binding.mapping_version}]);
      return `<div class="binding-card">
        <h3>${esc(group.name)}</h3>
        ${field('字段方案', select('binding.' + group.id, mappings, binding?.mapping_id || ''))}
        ${binding ? field('固定字段版本', select('bindingVersion.' + group.id, versions.map(version => [version.version, 'v' + version.version]), binding.mapping_version)) : ''}
      </div>`;
    }).join('');
  }

  function executionPage() {
    const model = esc(s.llm.active_profile_name || '未配置');
    const concurrency = s.llm.execution?.batch_concurrency || 1;
    return panel('执行配置', '切换数据集后需重新选择各组的字段方案。', `
      <div class="execution-pickers">
        ${picker('dataset', '数据集', s.catalog.datasets, s.dataset)}
        ${picker('plan', '批跑方案', s.catalog.plans, s.plan)}
      </div>
      <div class="context-grid">
        <div><span>模型</span><strong>${model}</strong></div>
        <div><span>并发</span><strong>${concurrency}</strong></div>
        <div><span>方案版本</span><strong>${s.plan?.id ? 'v' + s.plan.version : '未保存'}${s.planDirty ? '（有草稿改动）' : ''}</strong></div>
      </div>
      <div class="binding-grid">${bindingsHTML()}</div>
      <p class="muted">批跑每行每组最多自动重试 3 次。试跑和单标准测试只执行一次。</p>`) +
      panel('选择记录', '试跑只执行第一条选中记录。', `
        <div class="toolbar">
          ${btn('select-page', '选择本页')}
          ${btn('select-clear', '清空选择')}
          <small>已选 ${s.selected.length} 条</small>
          ${select('selectionMode', [['selected', '仅所选记录'], ['all', '整个数据集']], s.selectionMode)}
        </div>
        ${dataTable()}
        <div class="toolbar editor-actions">
          ${btn('preview', '预览各组请求', !s.dataset || !s.plan?.groups.length || !s.selected.length)}
          ${btn('trial', '试跑选中第 1 条', ro() || !s.selected.length)}
          ${btn('batch', '正式批跑', ro(), '', true)}
          ${s.parentRun ? '<small>已载入历史，提交为新复测。</small>' : ''}
        </div>`) + `<div id="active-results">${s.activeView ? runView(s.activeView) : ''}</div>`;
  }
  function resultTable(v){const gs=v.run.snapshot.groups,fs=v.run.snapshot.output_fields,ncols=g=>1+fs.filter(f=>f.group_id===g.id).length+(g.standards.some(c=>c.method!=='llm')?1:0);let head=`<tr><th rowspan="3">源行</th><th rowspan="3">行状态／详情</th>${gs.map(g=>`<th colspan="${ncols(g)}">${esc(g.name)}</th>`).join('')}</tr><tr>`;for(const g of gs){head+='<th rowspan="2">组状态</th>';for(const c of g.standards.filter(c=>c.method==='llm'))head+=`<th colspan="${fs.filter(f=>f.standard_id===c.id).length}">${esc(c.name)} · v${c.version}</th>`;if(g.standards.some(c=>c.method!=='llm'))head+='<th rowspan="2">程序规则</th>';}head+='</tr><tr>'+gs.flatMap(g=>fs.filter(f=>f.group_id===g.id).map(f=>`<th>${esc(f.name)}</th>`)).join('')+'</tr>';
    return table(`<table class="results-table"><thead>${head}</thead><tbody>${v.records.map(row=>`<tr><td>${row.source_row}</td><td>${badge(row.status)}${btn('record-detail','详情',false,`data-run="${v.run.id}" data-row="${esc(row.row_id)}"`)}</td>${gs.map(g=>{const r=row.groups.find(x=>x.group_id===g.id);return `<td>${badge(r?.status||'not_run')}<small>自动 ${r?.auto_retry_count||0}/3 · 尝试 ${r?.attempt_count||0}</small>${r?.auto_retry_count>=3&&r.status!=='completed'?'<p class="message warning">自动额度已用尽，等待人工处理。</p>':''}${r?.previous_attempt?'<small>上次尝试已保存；本轮尚未成功。</small>':''}${r?.error?cell(r.error):''}</td>`+fs.filter(f=>f.group_id===g.id).map(f=>`<td>${cell(ptr(r?.output,f.path))}</td>`).join('')+(g.standards.some(c=>c.method!=='llm')?`<td>${(r?.criteria||[]).map(c=>`<p>${esc(c.name)}：${c.verdict==='pass'?'通过':'不通过'}</p>`).join('')||'—'}</td>`:'');}).join('')}</tr>`).join('')||'<tr><td>暂无匹配记录</td></tr>'}</tbody></table>`);}
  function runView(v){const r=v.run,g=r.progress,rows=r.row_progress||{};return panel(r.mode==='trial'?'分组试跑结果':'批跑结果',`${r.created_at} · ${r.dataset_name}`,`<div class="context-grid"><span>状态 ${badge(r.status)}</span><span>完整成功 <strong>${rows.succeeded||0}/${rows.selected||0} 条数据</strong></span><span>组任务成功 <strong>${g.succeeded}/${g.selected}</strong></span><span>异常 ${g.failed} · 执行中 ${g.running} · 未执行 ${g.pending}</span></div>${r.error?`<p class="message error">${esc(r.error)}</p>`:''}<p class="muted">每行每组最多追加 3 次自动重试；人工重试或服务重启均不重置额度。</p><div class="toolbar">${btn('run-refresh','刷新结果',false,`data-run="${r.id}"`)}${btn('run-cancel','取消任务',!active(r.status),`data-run="${r.id}"`)}${btn('run-retry','原配置失败重试',active(r.status)||!r.retryable,`data-run="${r.id}"`)}${btn('run-clone','按原配置新建运行',active(r.status),`data-run="${r.id}"`)}${btn('run-restore','载入编辑',active(r.status),`data-run="${r.id}"`)}${btn('run-config','完整快照',false,`data-run="${r.id}"`)}${btn('run-rounds','轮次记录',false,`data-run="${r.id}"`)}</div><div class="toolbar">${field('结果筛选',select('result.filter',[['all','全部'],['errors','有失败组'],['pending','有未执行组']],v.filter))}${field('执行组',select('result.group',[['','全部组'],...r.snapshot.groups.map(g=>[g.id,g.name])],v.group))}${field('异常阶段',select('result.stage',[['','全部阶段'],['call_failed','调用'],['parse_failed','JSON 解析'],['schema_failed','结构校验'],['interrupted','中断'],['scheduler_failed','调度']],v.stage))}</div>`+`<div class="toolbar">${btn('export-jsonl','导出 JSONL',active(r.status),`data-run="${r.id}"`)}${btn('export-xlsx','导出 XLSX',active(r.status),`data-run="${r.id}"`)}${check('includeRaw',s.includeRaw,'包含全部尝试的请求及原始响应')}</div>`+details('选择导出的原始列（各组使用列必保留）',r.snapshot.dataset.original_columns.map(k=>check('exportField.'+k,(s.exportFields||r.snapshot.dataset.original_columns).includes(k),k)).join(''))+'<p class="muted">导出覆盖整个原始数据集；保留未选中、失败和取消后未执行的记录。</p>'+resultTable(v)+pager('results',v.offset,v.total,v.size));}

  function historyPage() {
    const statuses = ['completed', 'completed_with_errors', 'cancelled', 'interrupted', 'queued', 'running', 'failed'];
    const records = s.history.map(run => {
      const attrs = `data-id="${esc(run.id)}"`;
      const exportAttrs = `data-run="${esc(run.id)}"`;
      return `<tr>
        <td>${esc(run.created_at)}</td>
        <td>${run.mode === 'trial' ? '试跑' : '正式批跑'}</td>
        <td>${badge(run.status)} · ${run.row_progress?.succeeded || 0}/${run.row_progress?.selected || 0} 条完整成功</td>
        <td>${esc(run.dataset_name)}</td>
        <td><div class="button-group">
          ${btn('history-detail', '详情', false, attrs)}
          ${btn('history-export-xlsx', '导出 XLSX', active(run.status), exportAttrs)}
          ${btn('history-export-jsonl', '导出 JSONL', active(run.status), exportAttrs)}
          ${btn('history-delete', '删除', active(run.status), attrs)}
        </div></td>
      </tr>`;
    }).join('');
    return panel('批跑历史', '任务结束后可直接下载结果。打开详情可选择原始列或包含全部尝试。', `
      <div class="toolbar">
        ${field('运行状态', select('historyStatus', [['','全部状态'],...statuses.map(status => [status,labels[status]])], s.historyStatus))}
        ${field('数据集', select('historyDataset', options(s.catalog.datasets), s.historyDataset))}
        ${check('historyArchived', s.historyArchived, '包含归档运行')}
        ${btn('history-load', '刷新')}
      </div>
      ${table(`<table><thead><tr><th>运行时间</th><th>类型</th><th>情况</th><th>数据集</th><th>操作</th></tr></thead><tbody>${records}</tbody></table>`)}
      ${pager('history', s.historyOffset, s.historyTotal)}`);
  }
  function modelForm(p){s.profileId=p?.id||null;s.profileName=p?.name||'';s.connection=clone({api_url:'',api_key:'',model:'',max_tokens:4096,timeout:120,stream:false,enable_thinking:false,skip_special_tokens:false,...(p?.settings||s.llm.settings||{})});s.connection.api_key='';s.execution=clone(p?.execution||{batch_concurrency:1});}
  function modelPage(){if(!s.connection)modelForm(s.llm.profiles.find(p=>p.id===s.llm.active_profile_id));return panel('全局模型设置','后续调用共用当前配置；已提交运行保持原参数。测试另有 1 个独立通道。',`<p>${esc(s.llm.message||'')}</p><div class="toolbar">${field('命名配置',select('profile',options(s.llm.profiles),s.profileId||''))}${btn('model-new','新建配置')}${btn('model-copy','复制为新配置')}${btn('model-save','保存',false,'',true)}${btn('model-activate','启用当前配置',!s.profileId)}${btn('model-test','测试连接')}${btn('model-delete','删除配置',!s.profileId)}${btn('model-clear','清空密钥',!s.profileId)}</div>`+field('配置名称',input('profileName',s.profileName))+['api_url','api_key','model','max_tokens','timeout'].map(k=>field({api_url:'完整请求 URL',api_key:'API Key（留空保留已存密钥）',model:'模型名称',max_tokens:'最大输出 Token',timeout:'超时（秒）'}[k],input('connection.'+k,s.connection[k]??'',k==='api_key'?'password':['max_tokens','timeout'].includes(k)?'number':'text'))).join('')+['stream','enable_thinking','skip_special_tokens'].map(k=>check('connection.'+k,s.connection[k],k)).join('')+field('批跑并发上限（1～32）',input('execution.batch_concurrency',s.execution.batch_concurrency,'number','min="1" max="32"')));}
  function testResult(t){return `<p>${badge(t.status)}${t.draft?' · 未保存草稿':''}</p>${t.attempt?`<p>耗时 ${t.attempt.timing?.work_ms??'—'} ms</p>${t.attempt.error?`<p class="message error">${esc(t.attempt.error)}</p>`:''}${details('结构化输出',json(t.attempt.output))}${details('实际请求',json(t.attempt.request))}${details('原始响应与校验详情',json(t.attempt))}`:''}`;}
  function testModal(){const f=s.testForm;return '<p>使用当前标准草稿执行一次，不保存标准版本，不自动重试。</p>'+field('输入来源',select('test.source',[['json','粘贴 JSON 对象'],['dataset','已有数据集的一条记录']],f.source))+(f.source==='json'?field('JSON 对象',area('test.json_text',f.json_text,7))+field('选用字段（JSON Pointer，每行一个）',area('test.paths',f.paths||'',3),'留空则默认选择所有顶层字段。'):field('数据集',select('test.dataset_id',options(s.catalog.datasets),f.dataset_id||''))+field('字段方案',select('test.mapping_id',options(s.catalog.mappings.filter(m=>m.dataset_id===f.dataset_id)),f.mapping_id||''))+field('样本记录',select('test.row_id',[['','请选择'],...(f.rows||[]).map(r=>[r.row_id,'源行 '+r.source_row])],f.row_id||''))+pager('testRows',f.offset||0,f.total||0))+`<div class="toolbar">${btn('test-send','执行一次测试',s.testBusy,'',true)}${s.testBusy?'<span>测试中，关闭弹窗不会重复提交。</span>':''}</div>${s.testResult?testResult(s.testResult):''}`;}
  function managerBody(){const m=s.modal;return '<p>归档从日常选择中隐藏，可恢复；有引用的对象不可删除。</p>'+m.items.map(v=>`<article class="criterion-card"><strong>${esc(v.name||v.title||v.filename||v.id)}</strong> · v${v.version}${v.archived_at?' · 归档':''}<div class="toolbar">${btn('manage-refs','查看引用',false,`data-id="${v.id}"`)}${btn('manage-archive',v.archived_at?'恢复':'归档',false,`data-id="${v.id}" data-restore="${!!v.archived_at}"`)}${btn('manage-delete','删除',false,`data-id="${v.id}"`)}</div>${m.refId===v.id?json(m.references):''}</article>`).join('');}
  function projectDialog(modal) {
    const deleting = modal.type === 'projectDelete';
    const { scope, form, title } = modal;
    const blocked = deleting && scope.busy.length > 0;
    const error = s.error ? `<p role="alert" class="message error">${esc(s.error)}</p>` : '';
    const content = deleting ? `
      <p>将永久删除 <strong class="project-delete-name">${esc(scope.project.name)}</strong> 及其数据和配置。此操作无法撤销。</p>
      <dl class="project-delete-scope">
        <div><dt>对象</dt><dd>${scope.objects}</dd></div>
        <div><dt>版本</dt><dd>${scope.versions}</dd></div>
        <div><dt>文件</dt><dd>${scope.files}</dd></div>
      </dl>
      ${blocked ? '<p class="message warning">项目还有未结束的任务，请等待完成后再删除。</p>'
        : field('输入项目全名以确认', input('projectDialog.confirmName', form.confirmName, 'text', 'required autocomplete="off" spellcheck="false"'))}` : `
      ${field('项目名称', input('projectDialog.name', form.name, 'text', 'required maxlength="120" autocomplete="off"'))}
      ${field('项目说明（选填）', area('projectDialog.description', form.description, 4))}`;
    const valid = deleting ? !blocked && form.confirmName === scope.project.name : !!form.name.trim();
    return `<div class="workspace-overlay">
      <section class="workspace-dialog project-dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title" tabindex="-1">
        <header class="workspace-dialog-heading"><h2 id="dialog-title">${title}</h2></header>
        <form class="project-dialog-form" data-project-form>
          <div class="workspace-dialog-body">${error}${content}</div>
          <footer class="project-dialog-footer">
            <button type="button" class="button" data-action="modal-close" ${s.busy ? 'disabled' : ''}>${blocked ? '关闭' : '取消'}</button>
            ${blocked ? '' : `<button type="submit" class="button ${deleting ? 'danger-fill' : 'primary'}" ${s.busy || !valid ? 'disabled' : ''}>
              ${s.busy ? '正在处理…' : deleting ? '永久删除' : form.id ? '保存修改' : '创建项目'}
            </button>`}
          </footer>
        </form>
      </section>
    </div>`;
  }

  function modalHTML(){const m=s.modal;if(!m)return '';if(m.type==='projectForm'||m.type==='projectDelete')return projectDialog(m);let body=m.html||'';if(m.type==='test')body=testModal();if(m.type==='manager')body=managerBody();if(m.type==='run')body=runView(m.view);if(m.type==='refUpdate')body=`<p>引用 v${m.before.version} → v${m.after.version}。确认只修改方案草稿，还需保存方案。</p><div class="messages-grid">${json(m.before)}${json(m.after)}</div>${btn('ref-apply','采用此版本',false,'',true)}`;return `<div class="workspace-overlay"><section class="workspace-dialog${m.type==='test'?' test-dialog':''}" role="dialog" aria-modal="true" aria-labelledby="dialog-title" tabindex="-1"><header class="workspace-dialog-heading"><h2 id="dialog-title">${esc(m.title)}</h2><button class="button" data-action="modal-close">关闭</button></header><div class="workspace-dialog-body">${s.error?`<p role="alert" class="message error">${esc(s.error)}</p>`:''}${m.type === 'run' && s.notice ? `<p role="status" class="message success">${esc(s.notice)}</p>` : ''}${body}</div></section></div>`;}
  function scrollbars(){root.querySelectorAll('.scroll-table').forEach(el=>{const body=el.querySelector('.table-scroll'),top=el.querySelector('.table-scroll-top');top.hidden=body.scrollWidth<=body.clientWidth;top.firstElementChild.style.width=body.scrollWidth+'px';body.onscroll=()=>{top.scrollLeft=body.scrollLeft;};top.onscroll=()=>{body.scrollLeft=top.scrollLeft;};});}
  function render() {
    const focused = document.activeElement;
    const binding = focused?.dataset.bind;
    const start = focused?.selectionStart, end = focused?.selectionEnd;
    const opened = [...root.querySelectorAll('details[open]')].map(el => el.querySelector('summary')?.textContent);
    const page = [projectsPage, dataPage, standardPage, planPage, executionPage, historyPage, modelPage][s.page];
    const projectOptions = s.projects.some(project => project.id === s.project?.id) || !s.project
      ? s.projects : [s.project, ...s.projects];
    const subtitle = s.page === 0 ? '管理项目及其数据、标准、方案和运行。'
      : s.project ? s.project.name + (s.project.archived_at ? ' · 已归档，只读' : '') : '';
    root.innerHTML = `<div class="app-shell" ${s.modal ? 'inert' : ''}>
      <aside class="sidebar">
        <div class="brand">Eval Studio</div>
        <nav aria-label="工作区">${titles.map((title, index) => `<button class="nav-item ${s.page === index ? 'active' : ''}" data-page="${index}" ${s.page === index ? 'aria-current="page"' : ''}>${title}</button>`).join('')}</nav>
        <div class="sidebar-note"><a href="/help/tutorial" target="_blank">使用教程</a> · <a href="/help/deployment" target="_blank">部署说明</a></div>
      </aside>
      <div class="main-shell">
        <header class="topbar">
          <label class="project-picker"><span>当前项目</span>${select('project', options(projectOptions), s.project?.id || '')}</label>
          <span class="model-indicator">${esc(s.llm.active_profile_name || '未启用模型')}</span>
        </header>
        <main>
          <header class="page-heading"><div><h1>${titles[s.page]}</h1>${subtitle ? `<p>${esc(subtitle)}</p>` : ''}</div></header>
          ${s.busy && !s.modal ? '<p class="muted" role="status">正在处理…</p>' : ''}
          ${s.error && !s.modal ? `<p role="alert" class="message error preserve-lines">${esc(s.error)}</p>` : ''}
          ${s.notice ? `<p role="status" class="message success">${esc(s.notice)}</p>` : ''}
          ${s.page === 0 || s.page === 6 || s.project ? page() : empty('请先打开项目。')}
        </main>
      </div>
    </div>${modalHTML()}`;
    document.body.classList.toggle('workspace-modal-open', !!s.modal);
    scrollbars();
    for (const disclosure of root.querySelectorAll('details')) {
      if (opened.includes(disclosure.querySelector('summary')?.textContent)) disclosure.open = true;
    }
    const dialog = root.querySelector('[role="dialog"]');
    const target = binding && [...(dialog || root).querySelectorAll('[data-bind]')].find(el => el.dataset.bind === binding);
    if (target) {
      target.focus();
      if (start != null && target.setSelectionRange) target.setSelectionRange(start, end);
    } else if (dialog) {
      dialog.focus();
    }
    focusProjectDialog();
  }
  function uniqueName(base){let name=base,n=2;while(s.plan.groups.some(g=>g.name===name))name=base+' '+n++;return name;}
  function moveRef(sid,target){const from=s.plan.groups.find(g=>g.standards.some(r=>r.standard_id===sid)),ref=from.standards.find(r=>r.standard_id===sid);from.standards=from.standards.filter(r=>r!==ref);s.plan.groups.find(g=>g.id===target).standards.push(ref);s.plan.groups=s.plan.groups.filter(g=>g.standards.length);s.planDirty=true;schedulePlan();}
  function splitGroup(g){if(g.standards.length<2)return;const i=s.plan.groups.indexOf(g);const refs=g.standards;g.standards=refs.slice(0,1);s.plan.groups.splice(i+1,0,...refs.slice(1).map(r=>({id:uid(),name:uniqueName(selectedStandard(r)?.name||'独立组'),standards:[r]})));s.planDirty=true;schedulePlan();}
  async function loadHistory(){const v=await papi(`/run-history?offset=${s.historyOffset}&limit=20&status=${s.historyStatus}&dataset_id=${s.historyDataset}&include_archived=${!!s.historyArchived}`);s.history=v.items;s.historyTotal=v.total;}
  async function loadTests(){const v=await papi(`/standard-tests?offset=${s.testOffset}&limit=20&standard_id=${s.testsAll?'':s.standard.id||''}`);s.tests=v.items;s.testTotal=v.total;}
  const view=()=>s.modal?.type==='run'?s.modal.view:s.activeView;
  async function loadView(runId,target=null){const v=target||{offset:0,size:20,filter:'all',stage:'',group:'',records:[]};const seq=(v.seq||0)+1;v.seq=seq;const r=await papi('/runs/'+runId),p=await papi(`/runs/${runId}/records?offset=${v.offset}&limit=${v.size}&status_group=${v.filter}&error_stage=${v.stage}&group_id=${v.group}`);if(v.seq!==seq)return v;Object.assign(v,{run:r,records:p.records,total:p.total,offset:p.offset});return v;}
  async function startRun(mode){if(!s.dataset||!s.plan?.id||s.planDirty)throw new Error('请选择数据集并保存批跑方案。');const body={dataset_id:s.dataset.id,dataset_version:s.dataset.version,plan_id:s.plan.id,plan_version:s.plan.version,bindings:Object.values(s.bindings),selection_mode:mode==='trial'?'selected':s.selectionMode,row_ids:mode==='trial'?s.selected.slice(0,1):s.selectionMode==='selected'?s.selected:[],mode};if(mode==='batch'&&s.selectionMode==='all'&&!confirm(`确定批跑整个数据集的 ${s.dataset.row_count} 条记录？每行 ${s.plan.groups.length} 个组。`))return;const run=await papi(s.parentRun?`/runs/${s.parentRun}/rerun`:'/runs','POST',body);s.activeView=await loadView(run.id);s.notice=mode==='trial'?'已提交分组试跑（不自动重试）。':'已提交批跑。';}
  async function refreshModels(){s.llm=await api('/llm/profiles');}
  async function manage(kind){const k=kind==='plan'?'execution_plan':kind,data=await papi('/history?include_archived=true&kind='+k),items=await Promise.all(data.items.map(v=>papi('/entities/'+v.id)));s.modal={type:'manager',title:'对象管理',kind:k,items};}
  async function testRows(){const f=s.testForm,ds=s.catalog.datasets.find(d=>d.id===f.dataset_id);if(!ds){f.rows=[];return;}const v=await papi(`/datasets/${ds.id}/rows?version=${ds.version}&offset=${f.offset||0}&limit=20`);f.rows=v.rows;f.total=v.total;f.row_id=v.rows[0]?.row_id||'';}

  async function exportRun(runId, format, options = {}) {
    const epoch = s.epoch;
    const response = await fetch(`/api/v1/projects/${s.project.id}/runs/${runId}/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ format, ...options })
    });
    if (epoch !== s.epoch) return;
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      const detail = error.detail || `服务器返回 HTTP ${response.status}`;
      throw new Error('导出失败：' + (typeof detail === 'string' ? detail : pretty(detail)));
    }
    const file = await response.blob();
    if (epoch !== s.epoch) return;
    const url = URL.createObjectURL(file);
    const link = document.createElement('a');
    link.href = url;
    link.download = runId + '.' + format;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    s.notice = '已发起下载，请查看浏览器下载列表。';
  }
  async function click(name,el){const d=el.dataset;
    if (name === 'project-new' || name === 'project-edit') {
      const project = s.projects.find(item => item.id === d.id);
      s.modal = {
        type: 'projectForm', title: project ? '编辑项目' : '新建项目',
        form: { id: project?.id, name: project?.name || '', description: project?.description || '' },
        opener: { action: name, id: d.id }
      };
      return;
    }
    if(name==='project-open'){if(allowDiscard())await openProject(d.id);return;}
    if(name==='project-archive'){const p=s.projects.find(p=>p.id===d.id);await api(`/projects/${p.id}/${p.archived_at?'restore':'archive'}`,'POST',{});await loadProjects();if(s.project?.id===p.id)await catalog();return;}
    if (name === 'project-delete') {
      const scope = await api(`/projects/${d.id}/deletion-scope`);
      s.modal = {
        type: 'projectDelete', title: '删除项目', scope, form: { confirmName: '' },
        opener: { action: name, id: d.id }
      };
      return;
    }
    if(name==='import'){const file=s.upload;if(!file)throw new Error('请选择文件。');if(file.size>100*1024*1024)throw new Error('文件超过 100 MiB。');const form=new FormData();form.append('file',file);form.append('sheet',s.sheet||'');form.append('header_row',s.header||1);const ds=await papi('/datasets/import','POST',form);await catalog();await setDataset(ds);s.upload=null;s.notice='导入完成。';return;}
    if(name==='mapping-new'){s.mapping={title:'新的字段方案',dataset_id:s.dataset?.id,evaluation_fields:[]};s.mappingDirty=true;return;}
    if(name==='mapping-save'||name==='mapping-copy'){if(!s.dataset)throw new Error('先选择数据集。');const m=s.mapping;s.mapping=await papi('/mappings','POST',{...(name==='mapping-save'&&m.id?{id:m.id}:{}),title:m.title,dataset_id:s.dataset.id,evaluation_fields:m.evaluation_fields});s.mappingDirty=false;await catalog();return;}
    if(name==='standard-new'){if(!allowDiscard())return;s.standard=blankStandard();s.standardVersion=null;s.standardDirty=true;s.tests=[];scheduleTemplate();return;}
    if(name==='standard-save'||name==='standard-copy'){if(s.standard.method==='json_schema')s.standard.rule_schema=JSON.parse(s.standard.schemaText||pretty(s.standard.rule_schema));const body=standardBody();if(name==='standard-copy')delete body.id;await setStandard(await papi('/standards','POST',body));await catalog();s.notice='标准版本已保存，已有方案仍固定原引用。';return;}
    if(name==='standard-test'){s.testResult=null;s.testForm={source:'json',json_text:'{\n  "answer": "在此粘贴待测数据"\n}',paths:'',rows:[],offset:0};s.modal={type:'test',title:'测试此标准 · '+(s.standard.name||'草稿')};return;}
    if(name==='test-send'){if(s.testBusy)return;if(s.standard.method==='json_schema')s.standard.rule_schema=JSON.parse(s.standard.schemaText||pretty(s.standard.rule_schema));const f=s.testForm,body={standard:standardBody(),saved_version:s.standardVersion,source:f.source};if(f.source==='json'){body.json_text=f.json_text;if(f.paths?.trim())body.evaluation_fields=f.paths.split('\n').map(p=>p.trim()).filter(Boolean);}else{const ds=s.catalog.datasets.find(v=>v.id===f.dataset_id),m=s.catalog.mappings.find(v=>v.id===f.mapping_id);Object.assign(body,{dataset_id:ds?.id,dataset_version:ds?.version,mapping_id:m?.id,mapping_version:m?.version,row_id:f.row_id});}s.testBusy=true;render();try{s.testResult=await papi('/standards/test','POST',body);await loadTests();}finally{s.testBusy=false;}return;}
    if(name==='tests-load'){await loadTests();return;}
    if(name==='test-detail'){const t=await papi('/standard-tests/'+d.id);s.modal={type:'json',title:'单标准测试快照',html:testResult(t)+details('完整输入与配置',json(t))};return;}
    if(name==='plan-new'){if(!allowDiscard())return;s.plan=newPlan();s.planDirty=true;s.groupChecks=[];return;}
    if(name==='plan-save'||name==='plan-copy'){const body=planBody();if(name==='plan-copy')delete body.id;const bindings=clone(s.bindings);await setPlan(await papi('/execution-plans','POST',body));s.bindings=Object.fromEntries(Object.entries(bindings).filter(([id])=>s.plan.groups.some(g=>g.id===id)));await catalog();s.notice='方案已保存。';return;}
    if(name==='plan-add'){const c=s.catalog.standards.find(v=>v.id===s.addStandard);if(!c)return;s.versions[c.id]=await papi(`/standards/${c.id}/versions`);s.plan.groups.push({id:uid(),name:uniqueName(c.name),standards:[{standard_id:c.id,version:c.version}]});s.addStandard='';s.planDirty=true;schedulePlan();return;}
    if(name==='merge-all'){const [g,...rest]=s.plan.groups;if(g){g.standards.push(...rest.flatMap(g=>g.standards));s.plan.groups=[g];}s.planDirty=true;schedulePlan();return;}
    if(name==='split-all'){for(const g of [...s.plan.groups])splitGroup(g);return;}
    if(name==='group-split'){splitGroup(s.plan.groups.find(g=>g.id===d.id));return;}
    if(name==='group-up'||name==='group-down'){const i=s.plan.groups.findIndex(g=>g.id===d.id),j=i+(name==='group-up'?-1:1);[s.plan.groups[i],s.plan.groups[j]]=[s.plan.groups[j],s.plan.groups[i]];s.planDirty=true;schedulePlan();return;}
    if(name==='ref-remove'||name==='ref-split'){const g=s.plan.groups.find(g=>g.id===d.group),ref=g.standards.find(r=>r.standard_id===d.id);g.standards=g.standards.filter(r=>r!==ref);if(name==='ref-split')s.plan.groups.push({id:uid(),name:uniqueName(selectedStandard(ref)?.name||'独立组'),standards:[ref]});s.plan.groups=s.plan.groups.filter(g=>g.standards.length);s.planDirty=true;schedulePlan();return;}
    if(name==='ref-update'){const g=s.plan.groups.find(g=>g.id===d.group),ref=g.standards.find(r=>r.standard_id===d.id),before=await papi(`/standards/${d.id}?version=${ref.version}`),after=await papi('/standards/'+d.id);s.modal={type:'refUpdate',title:'更新引用版本',before,after,ref};return;}
    if(name==='ref-apply'){s.modal.ref.version=s.modal.after.version;s.modal=null;s.planDirty=true;schedulePlan();return;}
    if(name==='select-page'){s.selected=[...new Set([...s.selected,...s.rows.map(r=>r.row_id)])];return;}
    if(name==='select-clear'){s.selected=[];s.parentRun=null;return;}
    if(name==='preview'){const v=await papi('/execution-plans/preview','POST',{plan:planBody(),dataset_id:s.dataset.id,dataset_version:s.dataset.version,bindings:Object.values(s.bindings),row_id:s.selected[0]});s.modal={type:'json',title:'各组请求预览（不调用模型）',html:v.groups.map(g=>details(g.name+(g.model_required?'':'（纯程序规则，不请求模型）'),json({fields:g.mapping.evaluation_fields,request:g.request,schema:g.response_schema}))).join('')};return;}
    if(name==='trial'||name==='batch'){await startRun(name==='trial'?'trial':'batch');return;}
    if(name==='history-load'){await loadHistory();return;}
    if(name==='history-detail'){s.exportFields=null;s.modal={type:'run',title:'运行详情',view:await loadView(d.id)};return;}
    if(name==='history-delete'){if(!confirm('删除本次运行及其尝试和导出文件？'))return;await papi(`/history/run/${d.id}`,'DELETE',{confirm_id:d.id});await loadHistory();return;}
    if(name==='run-refresh'){await loadView(d.run,view());return;}
    if(name==='run-cancel'||name==='run-retry'){await papi(`/runs/${d.run}/${name==='run-cancel'?'cancel':'retry'}`,'POST',{});await loadView(d.run,view());return;}
    if(name==='run-clone'){const r=await papi(`/runs/${d.run}/clone`,'POST',{});s.modal=null;s.page=4;s.activeView=await loadView(r.id);return;}
    if(name==='run-restore'){if(!allowDiscard())return;const cfg=await papi(`/runs/${d.run}/configuration`);await setDataset(cfg.snapshot.dataset);await setPlan(cfg.snapshot.plan);s.bindings=Object.fromEntries(cfg.snapshot.groups.map(g=>[g.id,{group_id:g.id,mapping_id:g.mapping.id,mapping_version:g.mapping.version}]));for(const g of cfg.snapshot.groups)s.versions[g.mapping.id]=await papi(`/entities/${g.mapping.id}/versions`);s.planDirty=true;s.selected=cfg.row_ids;s.parentRun=d.run;s.modal=null;s.page=3;s.notice='已载入历史方案和各组字段为草稿；保存后可新建复测，不会自动执行。';return;}
    if(name==='run-config'||name==='run-rounds'){const v=await papi(`/runs/${d.run}/${name==='run-config'?'configuration':'rounds'}`);s.backModal=s.modal;s.modal={type:'json',title:name==='run-config'?'完整快照':'组任务轮次',html:json(v)};return;}
    if(name==='record-detail'){const v=await papi(`/runs/${d.run}/records/${encodeURIComponent(d.row)}`),run=view().run;s.backModal=s.modal;s.modal={type:'json',title:'源行 '+v.source_row+' · 组任务详情',html:details('原始数据',json(v.original))+details('完整有效 JSON',json(Object.assign({},...v.groups.map(g=>g.output||{}))))+run.snapshot.groups.map(g=>`<article class="criterion-card"><h3>${esc(g.name)}</h3>${json({fields:g.mapping.evaluation_fields,standards:g.standards.map(c=>({id:c.id,name:c.name,version:c.version})),result:v.groups.find(x=>x.group_id===g.id)})}${v.attempts.filter(a=>a.group_id===g.id).map(a=>details(`${a.kind==='auto_retry'?'自动重试':a.kind==='retry'?'人工重试':'首轮'} · 尝试 ${a.ordinal} · ${labels[a.status]||a.status}`,json(a))).join('')}</article>`).join('')};return;}
    if (name.startsWith('history-export-')) {
      await exportRun(d.run, name.slice('history-export-'.length));
      return;
    }
    if (name.startsWith('export-')) {
      await exportRun(d.run, name.slice('export-'.length), {
        original_fields: s.exportFields, include_raw_response: s.includeRaw
      });
      return;
    }
    if(name==='manage'){await manage(d.kind);return;}
    if(name==='manage-refs'){s.modal.refId=d.id;s.modal.references=(await papi(`/history/${s.modal.kind}/${d.id}`)).references;return;}
    if(name==='manage-archive'){const k=s.modal.kind;await papi(`/history/${k}/${d.id}/${d.restore==='true'?'restore':'archive'}`,'POST',{});await catalog();await manage(k==='execution_plan'?'plan':k);return;}
    if(name==='manage-delete'){if(!confirm('永久删除此对象全部版本？有引用时会拒绝。'))return;const k=s.modal.kind;await papi(`/history/${k}/${d.id}`,'DELETE',{confirm_id:d.id});await catalog();await manage(k==='execution_plan'?'plan':k);return;}
    if(name==='prev'||name==='next'){const dir=name==='next'?1:-1;if(d.type==='data'){s.offset+=dir*s.size;await loadRows();}if(d.type==='history'){s.historyOffset+=dir*20;await loadHistory();}if(d.type==='tests'){s.testOffset+=dir*20;await loadTests();}if(d.type==='testRows'){s.testForm.offset+=dir*20;await testRows();}if(d.type==='results'){const v=view();v.offset+=dir*v.size;await loadView(v.run.id,v);}return;}
    if(name==='model-new'){modelForm(null);s.profileName='新的模型配置';s.connection={api_url:'',api_key:'',model:'',max_tokens:4096,timeout:120,stream:false,enable_thinking:false,skip_special_tokens:false};return;}
    if(name==='model-copy'){s.profileId=null;s.profileName+=' 副本';s.connection.api_key='';return;}
    if(name==='model-save'){const v=await api('/llm/profiles'+(s.profileId?'/'+s.profileId:''),s.profileId?'PUT':'POST',{name:s.profileName,...s.connection,execution:s.execution});s.profileId=v.profile_id;await refreshModels();modelForm(s.llm.profiles.find(p=>p.id===s.profileId));return;}
    if(name==='model-activate'){s.llm=await api(`/llm/profiles/${s.profileId}/activate`,'POST',{});return;}
    if(name==='model-test'){s.modal={type:'json',title:'模型连接测试',html:json(await api('/llm/test','POST',{...s.connection,profile_id:s.profileId}))};return;}
    if(name==='model-delete'||name==='model-clear'){if(!confirm(name==='model-delete'?'删除此模型配置？':'清空此配置密钥？'))return;await api(`/llm/profiles/${s.profileId}${name==='model-clear'?'/clear-key':''}`,name==='model-delete'?'DELETE':'POST',name==='model-delete'?undefined:{});await refreshModels();modelForm(s.llm.profiles.find(p=>p.id===s.llm.active_profile_id));return;}
  }
  async function change(bind,value){
    if (bind === 'projectsAll') {
      const previous = s.projectsAll;
      s.projectsAll = value;
      try { await loadProjects(); }
      catch (error) { s.projectsAll = previous; throw error; }
      return;
    }
    if(bind==='project'){if(allowDiscard())await openProject(value);return;}
    if(bind.startsWith('choose.')){if(!allowDiscard()||!value)return;const kind=bind.slice(7),v=await papi('/entities/'+value);if(kind==='dataset')await setDataset(v);if(kind==='standard')await setStandard(v);if(kind==='plan')await setPlan(v);if(kind==='mapping'){s.mapping=v;s.mappingDirty=false;}return;}
    if(bind.startsWith('version.')){if(!allowDiscard())return;const kind=bind.slice(8),v=await papi(`/entities/${s[kind].id}?version=${value}`);if(kind==='standard')await setStandard(v);else await setPlan(v);return;}
    if(bind.startsWith('field.')){const p=bind.slice(6);s.mapping.evaluation_fields=s.mapping.evaluation_fields.filter(v=>v!==p);if(value)s.mapping.evaluation_fields.push(p);s.mappingDirty=true;return;}
    if(bind.startsWith('standard.')){s.standard[bind.slice(9)]=value;s.standardDirty=true;scheduleTemplate();return;}
    if(bind.startsWith('plan.')){s.plan[bind.slice(5)]=value;s.planDirty=true;schedulePlan();return;}
    if(bind.startsWith('group.')){s.plan.groups.find(g=>g.id===bind.slice(6)).name=value;s.planDirty=true;schedulePlan();return;}
    if(bind.startsWith('refversion.')){const ref=s.plan.groups.flatMap(g=>g.standards).find(r=>r.standard_id===bind.slice(11));ref.version=Number(value);s.planDirty=true;schedulePlan();return;}
    if(bind.startsWith('move.')){if(value)moveRef(bind.slice(5),value);return;}
    if(bind.startsWith('merge.')){if(value){const g=s.plan.groups.find(g=>g.id===bind.slice(6));for(const ref of [...g.standards])moveRef(ref.standard_id,value);}return;}
    if(bind.startsWith('bindingVersion.')){s.bindings[bind.slice(15)].mapping_version=Number(value);return;}
    if(bind.startsWith('binding.')){const g=bind.slice(8),m=s.catalog.mappings.find(m=>m.id===value);if(m){s.versions[m.id]=await papi(`/entities/${m.id}/versions`);s.bindings[g]={group_id:g,mapping_id:m.id,mapping_version:m.version};}else delete s.bindings[g];return;}
    if(bind.startsWith('test.')){s.testForm[bind.slice(5)]=value;if(bind==='test.dataset_id'){s.testForm.mapping_id='';s.testForm.offset=0;await testRows();}return;}
    if(bind.startsWith('result.')){const v=view();v[bind.slice(7)]=value;v.offset=0;await loadView(v.run.id,v);return;}
    if(bind.startsWith('size.')){const t=bind.slice(5);if(t==='data'){s.size=Number(value);s.offset=0;await loadRows();}else{const v=view();v.size=Number(value);v.offset=0;await loadView(v.run.id,v);}return;}
    if(bind.startsWith('exportField.')){const name=bind.slice(12),cols=s.exportFields||[...view().run.snapshot.dataset.original_columns];s.exportFields=cols.filter(v=>v!==name);if(value)s.exportFields.push(name);return;}
    if(bind==='profile'){modelForm(s.llm.profiles.find(p=>p.id===value));return;}
    if(bind.startsWith('connection.')){s.connection[bind.slice(11)]=value;return;}
    if(bind.startsWith('execution.')){s.execution[bind.slice(10)]=Number(value);return;}
    if(bind.startsWith('mapping.')){s.mapping[bind.slice(8)]=value;s.mappingDirty=true;return;}
    s[bind]=value;if(['historyStatus','historyDataset','historyArchived'].includes(bind)){s.historyOffset=0;await loadHistory();}if(bind==='testsAll'){s.testOffset=0;await loadTests();}
  }
  root.addEventListener('click',e=>{const p=e.target.closest('[data-page]');if(p){s.page=Number(p.dataset.page);render();if(s.page===5&&s.project)action(loadHistory);return;}const b=e.target.closest('[data-action]');if(!b)return;if(b.dataset.action==='modal-close'){closeModal();return;}action(()=>click(b.dataset.action,b));});
  root.addEventListener('submit', event => {
    if (!event.target.matches('[data-project-form]')) return;
    event.preventDefault();
    action(submitProjectDialog);
  });
  root.addEventListener('input',e=>{const el=e.target;if(el.id==='upload'){s.upload=el.files[0];return;}if(el.tagName==='SELECT'||el.type==='checkbox'||!el.dataset.bind)return;
    if (el.dataset.bind.startsWith('projectDialog.')) {
      const { form, scope } = s.modal;
      form[el.dataset.bind.split('.')[1]] = el.value;
      const submit = el.form.querySelector('[type="submit"]');
      submit.disabled = s.busy || (scope ? form.confirmName !== scope.project.name : !form.name.trim());
      return;
    }
    change(el.dataset.bind,el.type==='number'?Number(el.value):el.value).catch(e=>{s.error=e.message;});});
  root.addEventListener('change',e=>{const el=e.target;if(el.dataset.row){s.selected=s.selected.filter(v=>v!==el.dataset.row);if(el.checked)s.selected.push(el.dataset.row);render();return;}if(el.dataset.bind&&(el.tagName==='SELECT'||el.type==='checkbox'))action(()=>change(el.dataset.bind,el.type==='checkbox'?el.checked:el.value));});
  document.addEventListener('keydown',e=>{if(!s.modal)return;if(e.key==='Escape'){closeModal();return;}if(e.key==='Tab'){const nodes=[...root.querySelectorAll('[role=dialog] :is(button,input,select,textarea):not(:disabled),[role=dialog] summary')].filter(node=>node.getClientRects().length),first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}});
  window.addEventListener('beforeunload',e=>{if(dirty()){e.preventDefault();e.returnValue='';}});
  setInterval(async()=>{if(polling||s.busy||!s.project)return;const v=view();if(!v||!active(v.run.status))return;polling=true;const epoch=s.epoch;try{await loadView(v.run.id,v);if(epoch===s.epoch){const target=s.modal?.type==='run'?root.querySelector('.workspace-dialog-body'):s.page===4?document.getElementById('active-results'):null;if(target){target.innerHTML=runView(v);scrollbars();}}}catch(e){if(!e.stale)s.error='读取进度失败：'+e.message;}finally{polling=false;}},1500);
  (async()=>{render();const initialEpoch=s.epoch;try{await Promise.all([api('/projects').then(v=>{s.projects=v.projects;}),refreshModels()]);const last=localStorage.getItem('eval.v1_1.r4.project');if(initialEpoch===s.epoch&&!s.project&&s.projects.some(p=>p.id===last))await openProject(last);}catch(e){s.error=e.message;}render();})();
})();
