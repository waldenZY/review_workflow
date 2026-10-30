/* Browser-native v1.1. Project scopes and draft responses are guarded independently. */
(() => {
  'use strict';
  const root = document.getElementById('root');
  const pretty = v => JSON.stringify(v, null, 2);
  const clone = v => JSON.parse(JSON.stringify(v));
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const short = v => typeof v === 'string' ? v : JSON.stringify(v);
  const active = status => ['queued', 'running', 'cancelling'].includes(status);
  const labels = {pass:'通过',fail:'不通过',insufficient_evidence:'依据不足',not_applicable:'不适用',queued:'排队中',running:'执行中',cancelling:'正在取消',cancelled:'已取消',completed:'已完成',completed_with_errors:'完成，有异常',call_failed:'调用失败',parse_failed:'JSON 解析失败',schema_failed:'Schema 不匹配',projection_failed:'结果映射失败',interrupted:'已中断',pending:'未执行',not_run:'未执行',failed:'任务异常',design_running:'调用中'};
  const pages = [['项目','按项目组织工作'],['数据与字段','导入及共同评测字段'],['评测标准','定义判断要求'],['评测器','编辑实际消息与返回结构'],['执行与导出','选择样本、预览并导出'],['历史记录','版本、调用及生命周期'],['模型设置','所有项目共用']];
  const helpSections = ['projects','data','standards','evaluator','execution','history','model'];
  const blankCriterion = () => ({name:'',description:'',enabled:true,method:'llm',target_field:'',rule_schema:{type:'object'},schemaText:'{"type":"object"}'});
  const s = {page:0,projects:[],project:null,projectForm:null,projectQuery:'',showArchivedProjects:false,epoch:0,busy:null,
    notice:'',error:'',errorTrace:null,help:null,online:false,llm:{configured:false,message:'正在读取配置状态',profiles:[]},connectionForm:null,executionForm:{batch_concurrency:1},connectionDirty:false,connectionTest:null,profileId:null,profileName:''};
  let pollTimer,previewTimer,previewSequence=0;
  class StaleResponse extends Error {}
  function resetWorkspace() {
    clearTimeout(previewTimer);++previewSequence;
    Object.assign(s,{catalog:{datasets:[],mappings:[],specs:[],evaluators:[],runs:[]},dataset:null,rows:[],offset:0,pageSize:20,sampleId:'',selected:[],file:null,sheets:[],sheet:'',headerRow:1,
      mapping:{evaluation_fields:[]},mappingDirty:false,savedSpec:null,title:'新的评测方案',generationPrompt:'',designNotes:'',criteria:[blankCriterion()],
      designCallIds:[],specDirty:false,analysis:null,evaluator:null,evaluatorDirty:false,schemaText:'',projectionText:'',exampleText:'',example:null,preview:null,
      previewPending:false,previewError:'',specVersions:[],evaluatorVersions:[],compare:{},run:null,activeRunId:null,runRows:[],runOffset:0,runPageSize:20,runTotal:0,runDetails:{},runLineage:null,runLoadSequence:0,rowLoadSequence:0,rowPageSequence:0,rerunRows:[],selectionMode:'selected',allConfirmed:false,
      customExport:false,exportColumns:[],includeRaw:false,historyKind:'dataset',includeArchived:false,historyItems:[],historyDetail:null});
  }
  resetWorkspace();
  const storageKey = kind => `eval.v1_1.${s.project?.id}.${kind}`;
  const remember = (kind,v) => localStorage.setItem(storageKey(kind),JSON.stringify({id:v.id,version:v.version}));
  function remembered(kind) { try{return JSON.parse(localStorage.getItem(storageKey(kind)) || 'null');}catch{return null;} }
  function dirty() {return s.mappingDirty || s.specDirty || s.evaluatorDirty;}
  function readonly() {return !s.project || !!s.project.archived_at;}
  function ready() {return !readonly() && s.dataset && !s.dataset.archived_at && s.mapping.id && !s.mapping.archived_at && !s.mappingDirty && s.savedSpec && !s.savedSpec.archived_at && !s.specDirty && s.evaluator?.id && !s.evaluator.archived_at && !s.evaluatorDirty && s.evaluator.spec_id===s.savedSpec.id && s.evaluator.spec_version===s.savedSpec.version;}
  function needsModel() {return s.criteria.some(c=>c.enabled&&c.method==='llm');}
  const canExecute = () => ready() && (!needsModel() || s.llm.configured);
  const canPreview = () => !readonly() && s.dataset && s.sampleId && s.evaluator && s.savedSpec && s.evaluator.spec_id===s.savedSpec.id && s.evaluator.spec_version===s.savedSpec.version;
  function getAt(value,path) {for(const token of path.slice(1).split('/')){if(value==null)return undefined;value=value[token.replace(/~1/g,'/').replace(/~0/g,'~')];}return value;}
  function badge(v) {return `<span class="badge ${/^[a-z_]+$/.test(v||'')?v:''}">${esc(labels[v]||v)}</span>`;}
  function json(v) {return `<pre class="json-view">${esc(pretty(v))}</pre>`;}
  function field(label,control,hint='') {return `<label class="field"><span>${esc(label)}</span>${control}${hint?`<small>${esc(hint)}</small>`:''}</label>`;}
  function input(bind,v,type='text',attrs='') {return `<input data-bind="${bind}" type="${type}" value="${esc(v)}" ${attrs}>`;}
  function textarea(bind,v,rows=4,attrs='') {return `<textarea data-bind="${bind}" rows="${rows}" ${attrs}>${esc(v)}</textarea>`;}
  function select(bind,options,value,attrs='') {return `<select data-bind="${bind}" ${attrs}>${options.map(([v,t])=>`<option value="${esc(v)}" ${String(v)===String(value)?'selected':''}>${esc(t)}</option>`).join('')}</select>`;}
  function check(bind,value,label) {return `<label class="check-label"><input type="checkbox" data-bind="${bind}" ${value?'checked':''}>${esc(label)}</label>`;}
  function button(action,text,style='',disabled=false,attrs='') {return `<button class="button ${style}" data-action="${action}" ${disabled||s.busy?'disabled':''} ${attrs}>${s.busy?.key===action?'<span class="spinner" aria-hidden="true"></span>':''}${text}</button>`;}
  function detail(id,title,content) {return `<details class="advanced" id="${esc(id)}"><summary>${esc(title)}</summary>${content}</details>`;}
  function moreActions(id,content){return `<details class="more-actions" id="${esc(id)}"><summary>更多操作</summary><div><p>归档：隐藏且只读，可恢复。<br>删除：永久移除，无法恢复。</p>${content}</div></details>`;}
  function analysisView(){const a=s.analysis,status={sufficient:'材料充分',partial:'部分可判断',missing:'缺少依据',unclear:'定义不明确'};return `<p class="inline-note">${esc(a.scope)} · 样本 ${esc(a.row_id)}</p>${a.advice.map(v=>`<article class="advice-card"><strong>${esc(v.criterion)}</strong> ${badge(status[v.material_status])}<p>缺少内容：${esc(v.missing.join('；')||'未指出')}</p><p>影响：${esc(v.impact)}</p><p>建议：${esc(v.suggestion)}</p></article>`).join('')}${detail('field-checks','程序发现的字段缺失与空值',json(a.field_checks))}`;}
  function loading(key) {return s.busy?.key===key?'<span class="loading-inline" role="status"><span class="spinner"></span>正在处理…</span>':'';}
  function panel(title,description,content,controls='',key='') {return `<section class="panel"><header class="panel-heading"><div><h2>${title}${loading(key)}</h2><p>${esc(description)}</p></div><div class="controls">${controls}</div></header>${content}</section>`;}
  function empty(text) {return `<div class="empty-state">${esc(text)}</div>`;}
  function sampleSelect() {return field('消息预览 / 依据分析样本',select('sampleId',s.rows.map(r=>[r.row_id,`原始行 ${r.source_row} · ${r.row_id.slice(-9)}`]),s.sampleId),'从当前预览页选一条，不会自动执行。');}
  function next(page,text) {return `<div class="panel-footer"><span>${esc(text)}</span><button class="button primary" data-page="${page}">下一步 · ${pages[page][0]} →</button></div>`;}
  async function api(path,method='GET',body) {
    const response = await fetch('/api/v1'+path,{method,headers:body===undefined||body instanceof FormData?undefined:{'Content-Type':'application/json'},body:body===undefined?undefined:body instanceof FormData?body:JSON.stringify(body)});
    let value;try{value=await response.json();}catch{throw new Error(`服务返回了非 JSON 响应（HTTP ${response.status}），请检查服务端日志。`);}
    if(!response.ok){const e=new Error(typeof value.detail==='string'?value.detail:pretty(value.detail||value));e.trace=value.trace||(value.references?{references:value.references}:null);throw e;}
    return value;
  }
  async function papi(path,method='GET',body) {
    if(!s.project)throw new Error('请先打开项目。');
    const epoch=s.epoch,pid=s.project.id;
    try {const value=await api(`/projects/${pid}${path}`,method,body);if(epoch!==s.epoch)throw new StaleResponse();return value;}
    catch(e){if(epoch!==s.epoch)throw new StaleResponse();throw e;}
  }
  async function action(key,label,task) {
    if(s.busy)return;
    s.busy={key,label};s.error='';s.errorTrace=null;s.notice='';render();
    try{await task();}catch(e){if(!(e instanceof StaleResponse)){s.error=e.message||String(e);s.errorTrace=e.trace;}}
    finally{s.busy=null;render();if(s.page===3&&key!=='preview'&&key!=='file')schedulePreview();}
  }
  async function loadProjects(){s.projects=(await api('/projects?include_archived='+s.showArchivedProjects)).projects;}
  async function refresh(){const c=await papi('/catalog');s.catalog=c;s.project=c.project;}
  function setSpec(v){invalidatePreview();s.savedSpec=v;s.title=v.title;s.generationPrompt=v.generation_prompt;s.designNotes=v.design_notes;s.criteria=clone(v.criteria).map(c=>({...c,schemaText:pretty(c.rule_schema)}));s.designCallIds=[...v.design_call_ids];s.specDirty=false;s.analysis=null;s.specVersions=[];remember('spec',v);}
  function setEvaluator(v){invalidatePreview();s.evaluator=clone(v);s.schemaText=pretty(v.response_schema);s.projectionText=pretty(v.projection);s.exampleText=v.output_example==null?'':pretty(v.output_example);s.example=v.example_preview||null;s.evaluatorDirty=!v.id;s.evaluatorVersions=[];if(v.id)remember('evaluator',v);}
  async function openEvaluator(v,syncSpec=true){if(v.id)v=await papi(`/entities/${v.id}?version=${v.version}`);if(syncSpec&&(s.savedSpec?.id!==v.spec_id||s.savedSpec?.version!==v.spec_version))setSpec(await papi(`/entities/${v.spec_id}?version=${v.spec_version}`));setEvaluator(v);}
  async function loadDataset(v,offset=0){
    invalidatePreview();const sequence=++s.rowLoadSequence;
    const result=await papi(`/datasets/${v.id}/rows?offset=${offset}&limit=${s.pageSize}`);if(sequence!==s.rowLoadSequence)return;const changed=s.dataset?.id!==v.id;
    s.dataset=v;s.rows=result.rows;s.offset=offset;s.preview=null;
    if(changed){s.mapping=clone(s.catalog.mappings.find(m=>m.dataset_id===v.id)||{dataset_id:v.id,evaluation_fields:[]});s.mappingDirty=false;s.selected=[];s.allConfirmed=false;s.sampleId='';remember('dataset',v);}
    if(!s.rows.some(r=>r.row_id===s.sampleId))s.sampleId=s.rows[0]?.row_id||'';
  }
  async function switchProject(id){
    if(dirty()&&!window.confirm('当前项目有未保存修改。继续切换会放弃这些草稿，是否继续？'))return;
    const epoch=++s.epoch;clearTimeout(pollTimer);resetWorkspace();s.project=null;
    if(!id){s.page=0;localStorage.removeItem('eval.v1_1.project');return;}
    const catalog=await api(`/projects/${id}/catalog`);if(epoch!==s.epoch)return;
    s.project=catalog.project;s.catalog=catalog;localStorage.setItem('eval.v1_1.project',id);
    const pick=(list,kind)=>{const saved=remembered(kind);return list.find(v=>v.id===saved?.id)||list[0];};
    const ds=pick(catalog.datasets,'dataset');if(ds)await loadDataset(ds);
    const map=remembered('mapping');if(map&&catalog.mappings.some(v=>v.id===map.id&&v.dataset_id===ds?.id))s.mapping=await papi(`/entities/${map.id}?version=${map.version}`);
    const sp=pick(catalog.specs,'spec');if(sp){const r=remembered('spec');setSpec(r?.id===sp.id?await papi(`/entities/${sp.id}?version=${r.version}`):sp);}
    const ev=pick(catalog.evaluators,'evaluator');if(ev){const r=remembered('evaluator');await openEvaluator(r?.id===ev.id?{id:ev.id,version:r.version}:ev,false);}
    const run=pick(catalog.runs,'run');if(run)await loadRun(run.id);
    s.page=1;
  }
  async function loadRun(id,poll=false){
    if(!poll&&s.activeRunId!==id){s.activeRunId=id;s.rerunRows=[];s.runOffset=0;s.runDetails={};s.runLineage=null;}clearTimeout(pollTimer);
    const sequence=++s.runLoadSequence;
    const value=await papi(`/runs/${id}${poll?'/progress':''}`);if(s.activeRunId!==id||sequence!==s.runLoadSequence)return;
    s.run=poll?{...s.run,...value}:value;remember('run',s.run);
    await loadRunRows();if(s.activeRunId!==id||sequence!==s.runLoadSequence)return;
    if(active(value.status)){const epoch=s.epoch;pollTimer=setTimeout(async()=>{try{if(epoch!==s.epoch||s.activeRunId!==id)return;await loadRun(id,true);if(!active(s.run.status))await refresh();if(s.page===4)render();}catch(e){if(!(e instanceof StaleResponse)){s.error=e.message;render();}}},900);}
  }
  async function loadRunRows(offset=s.runOffset){
    if(!s.run)return;const id=s.run.id,sequence=++s.rowPageSequence;
    s.runOffset=offset;
    const value=await papi(`/runs/${id}/records?offset=${offset}&limit=${s.runPageSize}`);
    if(s.activeRunId!==id||sequence!==s.rowPageSequence)return;
    s.runOffset=offset;s.runRows=value.records;s.runTotal=value.total;
    for(const row of s.runRows){const cached=s.runDetails[row.row_id];if(cached&&(cached.attempts.length!==row.attempt_count||cached.status!==row.status))delete s.runDetails[row.row_id];}
  }
  function invalidatePreview(){clearTimeout(previewTimer);++previewSequence;s.preview=null;s.previewError='';s.previewPending=false;}
  function criteriaPayload(){return s.criteria.map(({schemaText,...c})=>({...c,rule_schema:c.method==='json_schema'?JSON.parse(schemaText):c.rule_schema}));}
  function evaluatorPayload(){const v=s.evaluator;if(!v)throw new Error('先保存标准并构建评测器。');return {id:v.id||null,title:v.title,spec_id:v.spec_id,spec_version:v.spec_version,system_template:v.system_template,user_template:v.user_template,response_schema:JSON.parse(s.schemaText),projection:JSON.parse(s.projectionText),output_example:s.exampleText.trim()?JSON.parse(s.exampleText):null};}
  function previewMarkup(){return `${s.previewPending?'<p class="loading-inline" role="status">正在刷新草稿预览…</p>':''}${s.previewError?`<p class="message warning">${esc(s.previewError)}</p>`:''}${s.preview?`<p class="inline-note">${s.preview.draft?'草稿预览：未保存，不代表执行版本。':`已保存版本预览：字段 v${s.preview.versions.mapping} · 标准 v${s.preview.versions.spec} · 评测器 v${s.preview.versions.evaluator}。`}本次不调用模型。</p><div class="messages-grid">${s.preview.messages.map(m=>`<div><h3>${esc(m.role)}</h3><pre class="message-view">${esc(m.content)}</pre></div>`).join('')}</div>${detail('http-preview','完整 HTTP 请求体与配置',json(s.preview.request))}`:!s.previewPending&&!s.previewError?empty('选择样本并构建评测器后可预览，模板修改约 500ms 后自动刷新。'):''}`;}
  function updatePreviewPanel(){const node=document.getElementById('preview-content');if(node){const open=!!node.querySelector('#http-preview[open]');node.innerHTML=previewMarkup();if(open&&node.querySelector('#http-preview'))node.querySelector('#http-preview').open=true;}const example=document.getElementById('example-content');if(example)example.innerHTML=exampleMarkup();}
  async function refreshPreview(sequence=++previewSequence){
    if(sequence!==previewSequence)return;
    clearTimeout(previewTimer);
    const epoch=s.epoch;
    if(!canPreview()){s.previewError='先选择样本、保存标准并构建绑定该版本的评测器；模板草稿无需保存。';s.previewPending=false;updatePreviewPanel();return;}
    s.previewPending=true;s.previewError='';updatePreviewPanel();
    try{const value=ready()?await papi('/requests/preview','POST',{...contextBody(),row_id:s.sampleId}):await papi('/requests/preview-draft','POST',{dataset_id:s.dataset.id,row_id:s.sampleId,evaluation_fields:s.mapping.evaluation_fields,spec:{id:s.savedSpec.id,title:s.title,criteria:criteriaPayload()},evaluator:evaluatorPayload()});
      if(sequence!==previewSequence||epoch!==s.epoch)return;s.preview=value;s.example=value.example;
    }catch(e){if(sequence!==previewSequence||epoch!==s.epoch)return;if(!(e instanceof StaleResponse)){s.preview=null;s.previewError=e instanceof SyntaxError?'高级配置含非法 JSON，请修正 Schema、映射或示例。':e.message;}}
    finally{if(sequence===previewSequence&&epoch===s.epoch){s.previewPending=false;updatePreviewPanel();}}
  }
  function schedulePreview(){invalidatePreview();if(s.page!==3)return;s.previewPending=!!canPreview();const sequence=previewSequence;previewTimer=setTimeout(()=>refreshPreview(sequence),500);updatePreviewPanel();}
  function exampleMarkup(){return s.example?(s.example.value!==null?json(s.example.value):`<p class="muted">${esc(s.example.message)}</p>`):'<p class="muted">构建或预览后生成结构示例；不是评测结论。</p>';}
  async function chooseFile(file){
    if(!file)return;if(!/\.(jsonl|json|xlsx)$/i.test(file.name))throw new Error('仅支持 JSONL、JSON、XLSX，不支持 CSV。');
    s.file=file;s.sheets=[];s.sheet='';s.headerRow=1;
    if(/\.xlsx$/i.test(file.name)){const form=new FormData();form.append('file',file);s.sheets=(await papi('/datasets/inspect','POST',form)).sheets;s.sheet=s.sheets[0];}
  }
  function contextBody(){if(!ready())throw new Error('请先保存字段配置、标准和绑定该标准版本的评测器。');return {mapping_id:s.mapping.id,mapping_version:s.mapping.version,evaluator_id:s.evaluator.id,evaluator_version:s.evaluator.version};}
  async function startRun(single){const body=contextBody();if(!single&&s.selectionMode==='all'&&!s.allConfirmed)throw new Error('请明确确认全量运行。');const all=!single&&s.selectionMode==='all';const ids=all?[]:single?s.selected.slice(0,1):s.selected;if(!all&&!ids.length)throw new Error('请在本页勾选样本。');const run=await papi('/runs','POST',{...body,selection_mode:all?'all':'selected',row_ids:ids});await loadRun(run.id);await refresh();s.notice='任务已提交。结果将在本页更新，模型调用不会自动重试。';}
  async function loadHistory(){s.historyItems=(await papi(`/history?kind=${s.historyKind}&include_archived=${s.includeArchived}`)).items;if(s.historyDetail){const v=s.historyDetail.object;s.historyDetail=await papi(`/history/${v.kind||'run'}/${v.id}`);}}
  function projectPage(){
    let form='';if(s.projectForm){const p=s.projectForm;form=panel(p.id?'修改项目':'新建项目','每个项目独立保存数据、配置和运行。模型连接全局共用。',field('项目名称',input('projectForm.name',p.name))+field('项目说明（可选）',textarea('projectForm.description',p.description,3))+`<div class="toolbar">${button('save-project','保存项目','primary')}${button('cancel-project-form','取消')}</div>`);}
    const list=s.projects.filter(p=>(p.name+' '+p.description).toLowerCase().includes(s.projectQuery.toLowerCase()));
    return `<div class="toolbar">${field('查找项目',input('projectQuery',s.projectQuery,'search','placeholder="按名称或说明查找"'))}${check('showArchivedProjects',s.showArchivedProjects,'包含已归档项目')}${button('filter-projects','筛选 / 刷新')}${button('new-project','＋ 新建项目','primary')}</div>${form}${list.length?`<div class="project-grid">${list.map(p=>`<article class="project-card ${p.archived_at?'archived':''}"><h2>${esc(p.name)} ${p.archived_at?badge('已归档'):''}</h2><p>${esc(p.description||'暂无说明')}</p><div class="project-counts">${[['dataset','数据集'],['spec','标准方案'],['evaluator','评测器'],['run','运行']].map(([k,t])=>`<span>${t} ${p.counts[k]}</span>`).join('')}</div><small>更新于 ${esc(new Date(p.updated_at).toLocaleString())}</small><code class="project-id">${esc(p.id)}</code><div class="button-group">${button('open-project','打开','primary',false,`data-id="${p.id}"`)}${!p.archived_at?button('edit-project','编辑','',false,`data-id="${p.id}"`):''}${moreActions('project-more-'+p.id,button('project-lifecycle',p.archived_at?'恢复项目':'归档项目','',false,`data-id="${p.id}" data-restore="${!!p.archived_at}"`)+button('delete-project','永久删除','danger',false,`data-id="${p.id}"`))}</div></article>`).join('')}</div>`:empty('还没有项目。先创建项目，再导入自己的已有数据。')}`;
  }
  function dataPage(){
    let result=panel('导入已有数据','JSONL / JSON / XLSX，最大 25 MB；不生成待评测内容。',`<div class="toolbar"><label class="button">选择文件<input type="file" data-file="dataset" accept=".jsonl,.json,.xlsx" hidden ${readonly()?'disabled':''}></label><span class="muted">${esc(s.file?.name||'尚未选择文件')}</span></div>${s.file?`<div class="toolbar">${s.sheets?.length?field('工作表',select('sheet',s.sheets.map(v=>[v,v]),s.sheet))+field('表头行',input('headerRow',s.headerRow,'number','min="1"')):''}${button('import','导入到当前项目','primary',readonly())}</div>`:''}`);
    if(!s.dataset)return result+empty('导入后可以选择共同使用的评测字段。需要说明时点击右上角“使用帮助”。');
    const keys=[...new Set(s.rows.flatMap(r=>Object.keys(r.original)))];
    const pathsFor=k=>s.mapping.evaluation_fields.filter(p=>p.slice(1).split('/')[0].replace(/~1/g,'/').replace(/~0/g,'~')===k);
    result+=panel('数据预览',`${s.dataset.filename} · ${s.dataset.row_count} 条 · ${s.dataset.id}`,`${pagination()}<p class="inline-note">浅色列包含已选评测路径；嵌套路径仅发送所选内容，列高亮不代表整列都被选中。</p><div class="table-scroll"><table><thead><tr><th>源行</th>${keys.map(k=>`<th class="${pathsFor(k).length?'evaluation-column':''}" title="${esc(pathsFor(k).join('、'))}">${esc(k)}${pathsFor(k).length?`<small class="selected-paths">${pathsFor(k).map(esc).join('<br>')}</small>`:''}</th>`).join('')}</tr></thead><tbody>${s.rows.map(r=>`<tr><td>${r.source_row}</td>${keys.map(k=>`<td class="${pathsFor(k).length?'evaluation-column':''}"><span class="cell-preview" title="${esc(short(r.original[k]))}">${esc(short(r.original[k]))}</span></td>`).join('')}</tr>`).join('')}</tbody></table></div>${pagination()}`,select('datasetChoice',s.catalog.datasets.map(d=>[d.id,`${d.filename} · ${d.id.slice(-6)}`]),s.dataset.id));
    result+=panel('参与评测的字段','全部标准共用这些字段；未选中的字段不会自动发送给 Judge 或依据分析。',`<div class="toolbar">${field("已保存字段配置",select("mappingChoice",[["","选择字段配置"],...s.catalog.mappings.filter(m=>m.dataset_id===s.dataset.id).map(m=>[m.id,`#${m.id.slice(-6)} · v${m.version}`])],s.mapping.id||""))}${button("new-mapping","＋ 新建字段配置","",readonly())}</div><div class="field-list">${s.dataset.fields.map(f=>`<label class="field-option ${s.mapping.evaluation_fields.includes(f.path)?'selected':''}"><input type="checkbox" data-field="${esc(f.path)}" ${s.mapping.evaluation_fields.includes(f.path)?'checked':''} ${readonly()||s.dataset.archived_at?'disabled':''}><span><code>${esc(f.path)}</code><small>${esc(f.types.join(' / '))}</small></span></label>`).join('')}</div><div class="panel-footer"><span data-save-state="mapping">${s.mappingDirty?'有未保存修改':s.mapping.id?`已保存 v${s.mapping.version}`:'尚未保存'}</span>${button('save-mapping','保存字段配置','primary',readonly()||!s.mapping.evaluation_fields.length)}</div>`);
    return result+next(2,'字段选择只决定评测材料；导出默认仍保留全部原始列。');
  }
  function pagination(run=false){const offset=run?s.runOffset:s.offset,count=run?s.runRows.length:s.rows.length,total=run?s.runTotal:s.dataset.row_count,size=run?s.runPageSize:s.pageSize;return `<div class="table-footer"><span>当前 ${count?offset+1:0}–${offset+count} / ${total} 条</span><div class="button-group">${field('每页',select(run?'runPageSize':'pageSize',[10,20,50].map(n=>[n,n+' 条']),size))}${button(run?'run-previous':'previous','上一页','',offset===0)}${button(run?'run-next':'next','下一页','',offset+count>=total)}</div></div>`;}
  function versionView(kind,list){if(!list.length)return '';const values=list.map(v=>[v.version,`v${v.version}`]);const c=s.compare[kind]||{left:list.at(-1).version,right:list[0].version};s.compare[kind]=c;return `<div class="version-list">${list.map(v=>button('load-version',`v${v.version} · ${new Date(v.created_at).toLocaleString()}`,'',false,`data-kind="${kind}" data-version="${v.version}"`)).join('')}</div><div class="toolbar">${field('版本 A',select(`compare.${kind}.left`,values,c.left))}${field('版本 B',select(`compare.${kind}.right`,values,c.right))}${button('compare-versions','比较内容差异','',false,`data-kind="${kind}"`)}</div>${c.result?json(c.result.differences):''}`;}
  function standardPage(){
    let result=panel('标准方案','没有生成 Prompt 时可直接手动填写。',field('方案名称',input('title',s.title))+field('生成任务 Prompt（可选）',textarea('generationPrompt',s.generationPrompt,5),'仅供主动提取标准使用，不自动加入 Judge 请求。')+`<div class="toolbar"><label class="button">读取 .md / .txt<input type="file" data-file="prompt" accept=".md,.txt" hidden ${readonly()?'disabled':''}></label>${button('extract','调用模型提取候选','',readonly()||!s.llm.configured||!s.generationPrompt.trim())}</div>`,select('specChoice',[['','选择标准方案'],...s.catalog.specs.map(v=>[v.id,`${v.title} · v${v.version}`])],s.savedSpec?.id||'')+button('new-spec','＋ 新建','',readonly())+button('copy-spec','复制为新方案','',readonly()||!s.criteria.length),'extract');
    result+=panel('独立评测标准','标准描述判断要求；所有标准使用同一份评测字段。',s.criteria.map((c,i)=>`<article class="criterion-card ${c.enabled?'':'disabled-card'}"><div class="criterion-top"><span class="criterion-number" aria-label="标准序号">${String(i+1).padStart(2,'0')}</span>${input(`criteria.${i}.name`,c.name,'text','placeholder="标准名称" aria-label="标准名称"')}${check(`criteria.${i}.enabled`,c.enabled,'启用')}${button('delete-criterion','删除','',readonly(),`data-index="${i}"`)}</div>${textarea(`criteria.${i}.description`,c.description,4,'class="criterion-description" placeholder="判断要求、合格条件、边界与依据不足时如何处理"')}<div class="form-grid">${field('评测方式',select(`criteria.${i}.method`,[['llm','LLM 内容判断'],['non_empty','程序规则 · 非空'],['json_schema','程序规则 · JSON Schema']],c.method))}${c.method!=='llm'?field('规则作用字段',select(`criteria.${i}.target_field`,[['','选择评测字段'],...s.mapping.evaluation_fields.map(p=>[p,p])],c.target_field)):''}</div>${c.method==='json_schema'?detail(`rule-${i}`,'待校验内容的 JSON Schema',textarea(`criteria.${i}.schemaText`,c.schemaText,7,'class="code-input"')):''}</article>`).join('')+field('设计备注（可选）',textarea('designNotes',s.designNotes,3),'只记录设计思路，随版本保存，不参与请求与判定。')+`<div class="panel-footer"><span data-save-state="spec">${s.specDirty?'草稿有未保存修改':s.savedSpec?`已保存 v${s.savedSpec.version}`:'尚未保存'}</span>${button('save-spec',s.savedSpec?'保存新版本':'保存标准','primary',readonly())}</div>`+(s.savedSpec?detail('spec-versions','标准历史与版本对比',button('spec-versions','载入历史版本')+versionView('spec',s.specVersions)):''),button('add-criterion','＋ 添加标准','',readonly()));
    result+=panel('可选：依据与可评测性','仅分析当前样本的标准定义和依据；建议可忽略，不阻止保存或执行。存在字段不代表依据充分。',s.rows.length?sampleSelect()+(s.analysis?analysisView():'<p class="muted">先保存字段配置和标准，再选择样本。此步骤不作为执行前的强制审批。</p>'):empty('先导入数据。'),button('analyze','调用模型分析依据','',readonly()||!s.llm.configured||!s.savedSpec||s.specDirty||!s.mapping.id||s.mappingDirty||!s.sampleId),'analyze');
    if(s.designCallIds.length)result+=detail('design-links','当前关联的设计调用',json(s.designCallIds));
    return result+next(3,'标准保存后，构建与该版本绑定的评测器。');
  }
  function evaluatorPage(){
    const ev=s.evaluator;let content=`<div class="toolbar"><span>当前标准：${esc(s.savedSpec?.title||'尚未保存')}${s.savedSpec?` · v${s.savedSpec.version}`:''}</span>${button('build','按标准构建草稿','',readonly()||!s.savedSpec||s.specDirty)}</div>`;
    if(ev){content+=`<p class="inline-note">绑定标准 ${esc(ev.spec_id)} · v${ev.spec_version}。模板之外不会追加隐藏消息。</p>`+field('评测器名称',input('evaluator.title',ev.title))+`<div class="messages-grid">${field('System 模板',textarea('evaluator.system_template',ev.system_template,14,'class="code-input"'))}${field('User 模板',textarea('evaluator.user_template',ev.user_template,14,'class="code-input"'))}</div><p class="inline-note">必需变量：<code>{{criteria}}</code>、<code>{{fields}}</code>；返回约束至少选用 <code>{{output_instructions}}</code>（默认简洁说明）或 <code>{{response_schema}}</code>（完整 Schema）。自定义 Schema 使用后者。变量只替换一次。</p>`;
      content+=panel('预期输出结构示例','结构示例，不是本次评测结果，也不是默认正确答案。',`<div id="example-content">${exampleMarkup()}</div>`);
      content+=detail('advanced-evaluator','高级：返回 Schema、结果映射和自定义结构示例',field('评测返回 JSON Schema',textarea('schemaText',s.schemaText,13,'class="code-input"'))+field('结果路径映射',textarea('projectionText',s.projectionText,11,'class="code-input"'),'路径使用 JSON Pointer；只映射启用的 LLM 标准，程序结果独立保存。')+field('自定义输出示例（可选）',textarea('exampleText',s.exampleText,7,'class="code-input"'),'必须是符合 Schema 的 JSON 对象，只用于说明结构。'));
      content+=`<div class="panel-footer"><span data-save-state="evaluator">${s.evaluatorDirty?'有未保存修改':`已保存 v${ev.version}`}</span>${button('save-evaluator','保存评测器版本','primary',readonly())}</div>`+(ev.id?detail('evaluator-versions','评测器历史与版本对比',button('evaluator-versions','载入历史版本')+versionView('evaluator',s.evaluatorVersions)):'');
    }else content+=empty('先保存标准，再构建消息模板。');
    let result=panel('评测器','标准决定评什么；消息模板和返回结构决定怎样调用与解析。',content,select('evaluatorChoice',[['','选择评测器'],...s.catalog.evaluators.map(v=>[v.id,`${v.title} · v${v.version}`])],ev?.id||'')+button('new-evaluator','＋ 新建','',readonly())+button('copy-evaluator','复制为新评测器','',readonly()||!ev));
    result+=panel('实际消息预览','预览当前草稿，不保存、不调用模型；模板修改约 500ms 后刷新。执行使用已保存版本。',(s.rows.length?sampleSelect():'')+`<div id="preview-content">${previewMarkup()}</div>`,button('preview','刷新草稿预览','',!canPreview()),'preview');
    return result+next(4,'先选择一条样本试评，再按需要批量执行并导出。');
  }
  function executionPage(){
    let result=panel('创建评测任务','只在你点击试评或运行后执行；所有 LLM 标准共同使用评测字段。',`<div class="context-grid">${[['项目',s.project.name],['数据集',s.dataset?.filename||'尚未选择'],['字段配置',s.mapping.id?`v${s.mapping.version}`:'尚未保存'],['标准 / 评测器',s.evaluator?.id?`标准 v${s.evaluator.spec_version} / 评测器 v${s.evaluator.version}`:'尚未保存'],['模型',needsModel()?s.llm.settings?.model||'未配置':'纯程序规则，不调用模型'],['本次提交并发数',needsModel()?String(s.llm.execution?.batch_concurrency||1):'1（纯程序规则）'],['服务地址',needsModel()?s.llm.settings?.api_url||'未配置':'无需模型连接']].map(([k,v])=>`<div><span>${k}</span><strong>${esc(v)}</strong></div>`).join('')}</div>${!ready()?'<p class="inline-note">请先保存字段配置、标准及绑定该标准版本的评测器。加载运行配置后可恢复当时的选择。</p>':''}${needsModel()&&!s.llm.configured?'<p class="inline-note">需要先在全局“模型设置”配置连接。</p>':''}`);
    if(s.dataset){result+=panel('选择样本','勾选可跨页保留；试评使用勾选顺序中的第 1 条。',`${pagination()}<div class="table-scroll"><table><thead><tr><th>选择</th><th>源行 / ID</th><th>评测材料摘要</th></tr></thead><tbody>${s.rows.map(r=>`<tr><td><input type="checkbox" data-row="${esc(r.row_id)}" ${s.selected.includes(r.row_id)?'checked':''} ${readonly()?'disabled':''} aria-label="选择原始行 ${r.source_row}"></td><td>${r.source_row}<br><code>${esc(r.row_id.slice(-14))}</code></td><td><small>共 ${s.mapping.evaluation_fields.length} 个评测字段</small>${s.mapping.evaluation_fields.slice(0,2).map(p=>`<div class="sample-summary"><code>${esc(p)}</code> ${esc((short(getAt(r.original,p))??'（缺失）').slice(0,100))}</div>`).join('')}${detail('sample-'+r.row_id,'查看全部评测字段',json(Object.fromEntries(s.mapping.evaluation_fields.map(p=>[p,getAt(r.original,p)??null]))))}</td></tr>`).join('')}</tbody></table></div>${pagination()}<div class="toolbar">${field('运行范围',select('selectionMode',[['selected',`勾选的 ${s.selected.length} 条`],['all',`全部 ${s.dataset.row_count} 条`]],s.selectionMode))}${s.selectionMode==='all'?check('allConfirmed',s.allConfirmed,`我确认执行全部 ${s.dataset.row_count} 条记录`):''}</div><div class="panel-footer"><span>已勾选 ${s.selected.length} 条${s.selected[0]?`；试评首条 ID：${esc(s.selected[0])}`:''}</span><div class="button-group">${button('trial','试评选中的第 1 条','',!canExecute()||!s.selected.length)}${button('run','运行所选范围','primary',!canExecute()||(s.selectionMode==='all'?!s.allConfirmed:!s.selected.length))}</div></div>`,'','run');}
    result+=panel('运行与基本预览','在这里查验结果后导出分析；执行失败与内容不通过分别显示。',s.run?runView():empty('尚未执行任务，可从右侧选择已有运行。'),select('runChoice',[['','选择项目内运行'],...s.catalog.runs.map(v=>[v.id,`${v.title} · ${labels[v.status]||v.status}`])],s.run?.id||'')+button('refresh-run','刷新'),'trial');
    if(s.run){const r=s.run;const cols=r.original_columns;const locked=new Set(r.snapshot.mapping.evaluation_fields.map(p=>p.slice(1).split('/')[0].replace(/~1/g,'/').replace(/~0/g,'~')));
      result+=panel('导出结果','导出保留原始行序和全部记录；未执行与失败行也会保留。',check('customExport',s.customExport,'自定义保留的原始列（默认保留全部）')+(s.customExport?`<p class="inline-note">评测字段对应的顶层列固定保留，包括该列中的其他嵌套内容。</p><div class="check-grid">${cols.map(k=>`<label class="check-label"><input type="checkbox" data-export-column="${esc(k)}" ${locked.has(k)||s.exportColumns.includes(k)?'checked':''} ${locked.has(k)?'disabled':''}>${esc(k)}${locked.has(k)?'（评测列）':''}</label>`).join('')}</div>`:'')+`<div class="toolbar">${check('includeRaw',s.includeRaw,'包含原始模型响应（默认不含）')}</div>`,button('export-xlsx','导出 XLSX','primary',active(r.status))+button('export-jsonl','导出 JSONL','',active(r.status)));}
    return result;
  }
  function runView(){
    const r=s.run,p=r.progress,percent=p.selected?Math.round(p.processed/p.selected*100):0;
    return `<div class="toolbar">${badge(r.status)}<code>${esc(r.id)}</code><span class="muted">已处理 ${p.processed}/${p.selected} · 执行中 ${p.running} · ${['queued','running'].includes(r.status)?'待执行':'未执行'} ${p.pending} · 执行异常 ${p.failed}</span></div><div class="progress"><span style="width:${percent}%"></span></div>${r.error?`<div class="message error">${esc(r.error)}</div>`:''}<p class="inline-note">本次配置：${esc(r.snapshot.model_config.profile_name||'纯程序规则')} · 固定批跑并发数 ${r.execution_config.batch_concurrency}。原配置重试使用本次绑定配置；新建复测使用全局当前配置。</p><div class="toolbar">${active(r.status)?button('cancel','取消任务','',readonly()):button('retry','原配置失败重试','',readonly()||r.archived_at||!r.retryable)}${button('load-run-config','载入本次运行配置','',readonly())}${button('rerun','选中样本新建复测','',!canExecute()||r.archived_at||active(r.status)||!s.rerunRows.length||s.dataset?.id!==r.snapshot.dataset.id)}</div>${pagination(true)}<div class="table-scroll"><table><thead><tr><th>复测</th><th>源行</th><th>执行状态</th><th>逐标准结论与理由</th><th>详情</th></tr></thead><tbody>${s.runRows.map(record=>`<tr><td><input type="checkbox" data-rerun-row="${esc(record.row_id)}" ${s.rerunRows.includes(record.row_id)?'checked':''} ${active(r.status)||readonly()||r.archived_at?'disabled':''} aria-label="复测原始行 ${record.source_row}"></td><td>${record.source_row}<br><code>${esc(record.row_id.slice(-12))}</code></td><td>${badge(record.status==='pending'&&['queued','running'].includes(r.status)?'等待中':record.status)}<br><small>${record.attempt_count} 次尝试</small></td><td>${record.criteria.length?record.criteria.map(c=>`<div class="verdict-line"><strong>${esc(c.name)}</strong>${badge(c.verdict||'未映射')}<small>${c.source==='llm'?'LLM':'程序规则'}</small></div><p class="reason">${esc(c.reason)}</p>`).join(''):'—'}${record.error?`<p class="reason error-text">${esc(record.error)}</p>`:''}</td><td>${detail('result-'+record.row_id,'记录与历次尝试',button('record-detail','加载 / 刷新详情','',false,`data-row-id="${esc(record.row_id)}"`)+(s.runDetails[record.row_id]?json(s.runDetails[record.row_id]):'<p class="muted">按需读取原始记录、响应及解析过程。</p>'))}</td></tr>`).join('')}</tbody></table></div>${pagination(true)}${detail('run-lineage','数据、配置版本及模型指纹',button('load-lineage','加载配置详情')+(s.runLineage?json(s.runLineage):json(r.snapshot)))}`;
  }
  function historyPage(){
    const kinds=[['dataset','数据集'],['mapping','字段配置'],['spec','标准方案'],['evaluator','评测器'],['run','运行'],['design_call','设计调用']];
    let result=panel('当前项目历史','归档后从日常列表隐藏且只读，可恢复；删除永久移除。归档不级联，引用保护包含归档记录。',`<div class="toolbar">${field('对象类型',select('historyKind',kinds,s.historyKind))}${check('includeArchived',s.includeArchived,'包含已归档对象')}${button('history-refresh','刷新')}</div><div class="table-scroll"><table><thead><tr><th>名称 / ID</th><th>时间 / 版本</th><th>状态</th><th>引用</th><th>操作</th></tr></thead><tbody>${s.historyItems.map(v=>`<tr><td>${esc(v.name)}<br><code>${esc(v.id)}</code></td><td>${esc(new Date(v.created_at).toLocaleString())}<br>${v.version?`v${v.version}`:'运行'}</td><td>${v.archived_at?'已归档':'未归档'} ${v.status?badge(v.status):''}</td><td>${v.references.length}</td><td>${button('history-detail','查看','',false,`data-kind="${v.kind}" data-id="${v.id}"`)} ${moreActions('history-more-'+v.id,button('history-lifecycle',v.archived_at?'恢复':'归档','',readonly()||active(v.status)||v.status==='design_running',`data-kind="${v.kind}" data-id="${v.id}" data-restore="${!!v.archived_at}"`))}</td></tr>`).join('')}</tbody></table></div>`);
    if(s.historyDetail){const d=s.historyDetail,v=d.object,kind=v.kind||'run';const open=['dataset','mapping','spec','evaluator','run'].includes(kind)||(kind==='design_call'&&v.purpose==='extract'&&v.status==='completed');result+=panel('历史详情',`${v.id} · ${v.archived_at?'已归档':'未归档'}`,`<div class="toolbar">${open?button('history-open',kind==='design_call'?'恢复候选为未保存草稿':'重新打开','primary',!!v.archived_at&&kind!=='run'):''}${moreActions('history-delete-more-'+v.id,button('history-delete','永久删除','danger',readonly()||active(v.status)||v.status==='design_running',`data-kind="${kind}" data-id="${v.id}"`))}</div>${detail('history-references',`引用此对象的记录（${d.references.length}）`,json(d.references))}${detail('history-content','内容、请求响应和快照',json(v))}${detail('history-versions','全部版本',json(d.versions))}${detail('history-events','保存及生命周期记录',json(d.events))}`);}
    return result;
  }
  function settingsPage(){
    const c=s.connectionForm;if(!c)return empty('正在读取模型设置。');
    const selected=s.llm.profiles.find(p=>p.id===s.profileId);
    const list=s.llm.profiles.length?`<div class="table-scroll"><table><thead><tr><th>配置名称</th><th>模型</th><th>批跑并发</th><th>状态</th><th>操作</th></tr></thead><tbody>${s.llm.profiles.map(p=>`<tr><td>${esc(p.name)}</td><td>${esc(p.settings.model)}</td><td>${p.execution.batch_concurrency}</td><td>${p.active?badge('当前使用'):'未启用'} · ${p.api_key_configured?'密钥已配置':'缺少密钥'}</td><td>${button('edit-profile','编辑','',false,`data-profile-id="${esc(p.id)}"`)}</td></tr>`).join('')}</tbody></table></div>`:empty('还没有命名配置。填写并保存后，再点击“设为当前使用”。');
    return panel('命名模型配置','全局当前配置供所有项目后续调用使用；保存与启用分开。',`<div class="message ${s.llm.configured?'success':'warning'}">${esc(s.llm.message)}<br>当前使用：${esc(s.llm.active_profile_name||'未选择')}</div>${list}<div class="toolbar">${button('new-profile','＋ 新建配置')}${button('save-as-profile','另存为新配置','',!s.connectionForm)}${button('reload-connection','重新读取')}</div>`)
      +panel(s.profileId?'编辑配置':'新配置草稿','已有任务不会被本页修改影响；仅轮换密钥不改变公开参数指纹。',field('配置名称',input('profileName',s.profileName))+`<div class="form-grid">${field('完整 api_url',input('connectionForm.api_url',c.api_url,'url','autocomplete="off" placeholder="https://provider.example/v1/chat/completions"'),'不自动追加路径。')}${field('api_key',input('connectionForm.api_key',c.api_key,'password',`autocomplete="new-password" placeholder="${selected?.api_key_configured?'已配置；留空保留该配置密钥':'新增或密钥缺失时必须填写'}"`),'密钥不回显，不进入运行和导出；另存为必须显式填写。')}${field('model',input('connectionForm.model',c.model))}${field('max_tokens',input('connectionForm.max_tokens',c.max_tokens,'number','min="1"'))}${field('timeout（秒）',input('connectionForm.timeout',c.timeout,'number','min="0.1" step="any"'))}</div><div class="check-grid">${check('connectionForm.stream',c.stream,'stream（SSE）')}${check('connectionForm.enable_thinking',c.enable_thinking,'enable_thinking')}${check('connectionForm.skip_special_tokens',c.skip_special_tokens,'skip_special_tokens')}</div><p class="inline-note">后两个参数通过 extra_body 对象发送，请确认供应商支持。测试只检查当前表单，不保存、不启用；连接成功不代表评测格式或质量通过。</p><section class="execution-settings"><h3>批跑执行设置</h3>${field('批跑并发数',input('executionForm.batch_concurrency',s.executionForm.batch_concurrency,'number','min="1" max="32" step="1"'),'1–32，默认 1。控制同一次批跑同时处理的记录数，不是每秒请求数。')}<p class="inline-note">不同批跑按提交顺序排队；提取、依据分析及连接测试不使用此设置。运行提交后固定，原配置重试沿用原值，新建复测使用当前值。此设置不会发送给模型供应商，也不改变模型指纹。</p></section><p class="inline-note" data-connection-state>${s.connectionDirty?'表单有未保存修改。':s.profileId?'正在查看已保存配置。':'保存后仍需手动启用。'}</p><div class="toolbar">${button('save-connection','保存配置','primary',!s.profileName.trim())}${button('activate-profile','设为当前使用','',!s.profileId||s.connectionDirty||!selected?.api_key_configured)}${button('test-connection','测试当前表单')}${button('clear-key','清除此配置密钥','danger',!selected?.api_key_configured)}${button('delete-profile','删除配置','danger',!s.profileId)}</div>${s.connectionTest?`<div class="message ${s.connectionTest.success?'success':'error'}">${esc(s.connectionTest.message)}</div>${detail('connection-test','查看本次连接测试详情',json(s.connectionTest.trace))}`:''}${detail('public-config','当前使用配置的公开参数与指纹',json(s.llm.parameters))}`,'','test-connection');
  }
  function render(){
    const opened=[...document.querySelectorAll('details[open][id]')].map(n=>n.id);const focus=document.activeElement?.dataset?.bind;const focusIndex=focus?[...document.querySelectorAll('[data-bind]')].filter(n=>n.dataset.bind===focus).indexOf(document.activeElement):0;const start=focus&&document.activeElement.selectionStart;const end=focus&&document.activeElement.selectionEnd;
    const needsProject=s.page>0&&s.page<6;const views=[projectPage,dataPage,standardPage,evaluatorPage,executionPage,historyPage,settingsPage];
    const projectOptions=[...s.projects];if(s.project&&!projectOptions.some(p=>p.id===s.project.id))projectOptions.push(s.project);
    root.innerHTML=`<div class="app-shell"><aside class="sidebar"><a class="brand" href="#" data-page="0">Eval Studio<small>LLM 评测工作台 · v1.1</small></a><nav aria-label="主要功能">${pages.map((p,i)=>`<button class="nav-item ${s.page===i?'active':''}" data-page="${i}" ${i>0&&i<6&&!s.project?'disabled':''}>${p[0]}</button>`).join('')}</nav><div class="sidebar-note">项目独立保存业务记录<br>模型连接全局共用<br>执行后基本预览，导出后分析<br><a href="/help/deployment" target="_blank" rel="noreferrer">Python 部署说明 ↗</a></div></aside><div class="main-shell"><header class="topbar"><div class="project-picker"><span class="muted">当前项目</span>${select('projectChoice',[['','选择项目'],...projectOptions.map(p=>[p.id,p.name+(p.archived_at?'（已归档）':'')])],s.project?.id||'')}</div><div class="topbar-actions"><span class="muted">${s.online?'服务已连接':'等待连接'} · 当前模型配置：${esc(s.llm.active_profile_name||'未选择')}</span><button class="button small" data-help="${helpSections[s.page]}">? 使用帮助</button></div></header><main><header class="page-heading"><div><div class="eyebrow">EVALUATION WORKSPACE / V1.1</div><h1>${pages[s.page][0]}</h1><p>${pages[s.page][1]}</p></div>${s.project&&needsProject?badge(s.project.name):''}</header>${s.project?.archived_at&&needsProject?'<div class="message warning">项目已归档，仅可查看历史和导出。恢复项目后才能编辑或执行。</div>':''}${s.notice?`<div class="message success" role="status"><div>${esc(s.notice)}</div><button data-dismiss="notice" aria-label="关闭提示">×</button></div>`:''}${s.error?`<div class="message error" role="alert"><div>${esc(s.error)}${s.errorTrace?detail('error-trace','错误与调用详情',json(s.errorTrace)):''}</div><button data-dismiss="error" aria-label="关闭错误">×</button></div>`:''}${s.busy?`<div class="loading-inline" role="status"><span class="spinner"></span>${esc(s.busy.label)}</div>`:''}${needsProject&&!s.project?empty('请先创建或打开项目。'):views[s.page]()}<footer class="app-footer"><span>Eval Studio · 数据与配置版本随运行保存</span><span>Python 服务 · 源码直接部署</span></footer></main></div></div>${s.help?`<div class="help-overlay" data-help-overlay="true"><aside class="help-drawer" role="dialog" aria-modal="true" aria-label="使用帮助"><header class="help-heading"><strong>使用帮助</strong><div class="button-group"><a class="button small" href="/help/tutorial#${esc(s.help)}" target="_blank" rel="noreferrer">新标签打开 ↗</a><button class="button small" data-help-close="true" aria-label="关闭帮助">关闭 ×</button></div></header><iframe title="Eval Studio 操作教程" src="/help/tutorial#${esc(s.help)}"></iframe></aside></div>`:''}`;
    for(const id of opened){const el=document.getElementById(id);if(el)el.open=true;}
    const archivedEdit=(s.page===1&&(s.dataset?.archived_at||s.mapping.archived_at))||(s.page===2&&s.savedSpec?.archived_at)||(s.page===3&&s.evaluator?.archived_at);
    if((readonly()||archivedEdit)&&s.page>0&&s.page<5){for(const node of root.querySelectorAll('[data-bind]')){const bind=node.dataset.bind;if(!['projectChoice','datasetChoice','specChoice','evaluatorChoice','runChoice','sampleId','customExport','includeRaw','pageSize','runPageSize'].includes(bind)&&!bind.startsWith('compare.'))node.disabled=true;}}
    if(focus){const nodes=[...root.querySelectorAll('[data-bind]')].filter(n=>n.dataset.bind===focus);const node=nodes[focusIndex]||nodes[0];if(node){node.focus();if(typeof start==='number'&&node.setSelectionRange&&['text','search','url','password','textarea'].includes(node.type))node.setSelectionRange(start,end);}}
    syncControls();
  }
  function setConnection(value,id=undefined){s.llm=value;const selected=value.profiles.find(p=>p.id===(id===undefined?(s.profileId||value.active_profile_id):id))||value.profiles[0];if(selected){s.profileId=selected.id;s.profileName=selected.name;s.connectionForm={...selected.settings,api_key:''};s.executionForm=clone(selected.execution||{batch_concurrency:1});}else{s.profileId=null;s.profileName='';s.connectionForm={api_url:'',api_key:'',model:'',max_tokens:4096,timeout:120,stream:false,enable_thinking:false,skip_special_tokens:false};s.executionForm={batch_concurrency:1};}s.connectionDirty=false;s.connectionTest=null;}
  function chooseProfile(id){if(s.connectionDirty&&!window.confirm('切换编辑对象会放弃未保存的模型配置，是否继续？'))return;setConnection(s.llm,id);}
  const actions={
    'new-mapping':()=>{invalidatePreview();s.mapping={dataset_id:s.dataset.id,evaluation_fields:[...s.mapping.evaluation_fields]};s.mappingDirty=true;},
    'new-evaluator':()=>{invalidatePreview();s.evaluator=null;s.evaluatorDirty=false;s.schemaText='';s.projectionText='';s.exampleText='';s.example=null;s.preview=null;s.evaluatorVersions=[];},
    'new-project':()=>{s.projectForm={name:'',description:''};},
    'cancel-project-form':()=>{s.projectForm=null;},
    'filter-projects':loadProjects,
    'edit-project':el=>{const p=s.projects.find(p=>p.id===el.dataset.id);s.projectForm={id:p.id,name:p.name,description:p.description};},
    'save-project':async()=>{const p=s.projectForm;const v=await api('/projects'+(p.id?'/'+p.id:''),p.id?'PUT':'POST',{name:p.name,description:p.description});s.projectForm=null;await loadProjects();if(s.project?.id===v.id)s.project=v;s.notice='项目已保存。';},
    'open-project':el=>switchProject(el.dataset.id),
    'project-lifecycle':async el=>{const restore=el.dataset.restore==='true';const value=await api(`/projects/${el.dataset.id}/${restore?'restore':'archive'}`,'POST');if(s.project?.id===value.id)s.project=value;await loadProjects();s.notice=restore?'项目已恢复。':'项目已归档；单独归档的子对象保留各自状态。';},
    'delete-project':async el=>{const scope=await api(`/projects/${el.dataset.id}/deletion-scope`);if(scope.busy.length)throw new Error('项目仍有运行中任务或设计调用，不能删除。');const name=window.prompt(`永久删除项目「${scope.project.name}」？\n范围：${scope.objects} 个对象、${scope.versions} 个版本、${scope.files} 个文件。\n输入项目名称确认，删除不可恢复：`);if(name===null)return;const result=await api('/projects/'+el.dataset.id,'DELETE',{confirm_name:name});if(s.project?.id===el.dataset.id){++s.epoch;clearTimeout(pollTimer);s.project=null;resetWorkspace();localStorage.removeItem('eval.v1_1.project');}await loadProjects();s.notice=result.file_cleanup_pending?'项目已删除，部分暂存文件待清理，请检查数据目录 .deleting。':'项目及所属记录、文件已删除。';},
    import:async()=>{if(!s.file)throw new Error('请选择文件。');const form=new FormData();form.append('file',s.file);form.append('sheet',s.sheet||'');form.append('header_row',s.headerRow||1);const value=await papi('/datasets/import','POST',form);await refresh();await loadDataset(value);s.file=null;s.notice='已导入当前项目，请选择评测字段并保存。';},
    previous:()=>loadDataset(s.dataset,Math.max(0,s.offset-s.pageSize)),next:()=>loadDataset(s.dataset,s.offset+s.pageSize),
    'run-previous':()=>loadRunRows(Math.max(0,s.runOffset-s.runPageSize)),'run-next':()=>loadRunRows(s.runOffset+s.runPageSize),
    'save-mapping':async()=>{s.mapping=await papi('/mappings','POST',{id:s.mapping.id||null,dataset_id:s.dataset.id,evaluation_fields:s.mapping.evaluation_fields});s.mappingDirty=false;remember('mapping',s.mapping);await refresh();s.notice='字段配置已保存，所有标准共用这份清单。';},
    'new-spec':()=>{invalidatePreview();s.savedSpec=null;s.title='新的评测方案';s.generationPrompt='';s.designNotes='';s.criteria=[blankCriterion()];s.designCallIds=[];s.specDirty=false;s.analysis=null;s.specVersions=[];},
    'copy-spec':()=>{invalidatePreview();s.savedSpec=null;s.title+=' 副本';s.criteria=s.criteria.map(({id,...c})=>clone(c));s.specDirty=true;s.specVersions=[];s.analysis=null;s.notice='已复制为未保存的新方案；原方案不变。保存后从 v1 开始，再构建评测器。';},
    'copy-evaluator':()=>{const value=clone(s.evaluator);for(const key of ['id','version','project_id','kind','created_at','archived_at'])delete value[key];value.title+=' 副本';s.evaluator=value;s.evaluatorDirty=true;s.evaluatorVersions=[];invalidatePreview();s.notice='已复制为新评测器草稿，仍绑定原标准版本。保存后从 v1 开始。';schedulePreview();},
    'add-criterion':()=>{s.criteria.push(blankCriterion());s.specDirty=true;},
    'delete-criterion':el=>{s.criteria.splice(Number(el.dataset.index),1);s.specDirty=true;},
    'save-spec':async()=>{const criteria=s.criteria.map(({schemaText,...c})=>({...c,rule_schema:c.method==='json_schema'?JSON.parse(schemaText):c.rule_schema}));setSpec(await papi('/specs','POST',{id:s.savedSpec?.id||null,title:s.title,generation_prompt:s.generationPrompt,design_notes:s.designNotes,criteria,design_call_ids:s.designCallIds}));await refresh();s.notice='标准版本已保存。评测器仍绑定原版本，需要时重新构建并保存。';},
    extract:async()=>{if(s.criteria.some(c=>c.name||c.description)&&!window.confirm('提取候选将替换当前标准草稿。是否继续？'))return;const v=await papi('/specs/extract','POST',{generation_prompt:s.generationPrompt});s.criteria=v.criteria.map(c=>({...c,schemaText:pretty(c.rule_schema)}));s.designCallIds.push(v.design_call_id);s.specDirty=true;s.notice='候选已提取并保存为设计调用；请编辑确认后保存标准。';},
    analyze:async()=>{s.analysis=await papi('/evidence/analyze','POST',{mapping_id:s.mapping.id,mapping_version:s.mapping.version,spec_id:s.savedSpec.id,spec_version:s.savedSpec.version,row_id:s.sampleId});s.notice='分析已独立保存为设计调用，仅针对当前样本；无需修改或重新保存标准即可继续执行。';},
    build:async()=>{const v=await papi('/evaluators/build','POST',{spec_id:s.savedSpec.id,spec_version:s.savedSpec.version});if(s.evaluator?.spec_id===v.spec_id&&s.evaluator.id)v.id=s.evaluator.id;setEvaluator(v);s.evaluatorDirty=true;schedulePreview();},
    'save-evaluator':async()=>{setEvaluator(await papi('/evaluators','POST',evaluatorPayload()));await refresh();s.notice='评测器已保存，执行将使用这个版本。';schedulePreview();},
    preview:()=>refreshPreview(),
    'spec-versions':async()=>{s.specVersions=await papi(`/entities/${s.savedSpec.id}/versions`);},
    'evaluator-versions':async()=>{s.evaluatorVersions=await papi(`/entities/${s.evaluator.id}/versions`);},
    'load-version':async el=>{const kind=el.dataset.kind;const id=kind==='spec'?s.savedSpec.id:s.evaluator.id;const v=await papi(`/entities/${id}?version=${el.dataset.version}`);if(kind==='spec')setSpec(v);else await openEvaluator(v);},
    'compare-versions':async el=>{const kind=el.dataset.kind;const id=kind==='spec'?s.savedSpec.id:s.evaluator.id;const c=s.compare[kind];c.result=await papi(`/entities/${id}/compare?left=${c.left}&right=${c.right}`);},
    trial:()=>startRun(true),run:()=>startRun(false),
    'refresh-run':async()=>{await refresh();if(s.run)await loadRun(s.run.id);},
    cancel:async()=>{await papi(`/runs/${s.run.id}/cancel`,'POST');await loadRun(s.run.id);},
    retry:async()=>{await papi(`/runs/${s.run.id}/retry`,'POST');await loadRun(s.run.id);await refresh();},
    rerun:async()=>{const v=await papi(`/runs/${s.run.id}/rerun`,'POST',{...contextBody(),row_ids:s.rerunRows});await loadRun(v.id);await refresh();},
    'load-run-config':async()=>{const config=await papi(`/runs/${s.run.id}/configuration`),snap=config.snapshot;await loadDataset(await papi(`/entities/${snap.dataset.id}`));s.mapping=await papi(`/entities/${snap.mapping.id}?version=${snap.mapping.version}`);s.mappingDirty=false;remember('mapping',s.mapping);setSpec(await papi(`/entities/${snap.spec.id}?version=${snap.spec.version}`));await openEvaluator(await papi(`/entities/${snap.evaluator.id}?version=${snap.evaluator.version}`));s.selected=config.row_ids;s.notice='已载入本次运行的业务配置。全局模型选择保持不变；归档对象需恢复后才能执行。';},
    'record-detail':async el=>{const id=s.run.id,row=el.dataset.rowId;const value=await papi(`/runs/${id}/records/${encodeURIComponent(row)}`);if(s.run?.id===id)s.runDetails[row]=value;},
    'load-lineage':async()=>{const id=s.run.id;const value=await papi(`/runs/${id}/configuration`);if(s.run?.id===id)s.runLineage=value.snapshot;},
    'export-xlsx':()=>download('xlsx'),'export-jsonl':()=>download('jsonl'),
    'history-refresh':loadHistory,
    'history-detail':async el=>{s.historyDetail=await papi(`/history/${el.dataset.kind}/${el.dataset.id}`);},
    'history-lifecycle':async el=>{await papi(`/history/${el.dataset.kind}/${el.dataset.id}/${el.dataset.restore==='true'?'restore':'archive'}`,'POST');await refresh();await loadHistory();for(const v of [s.dataset,s.mapping,s.savedSpec,s.evaluator,s.run])if(v?.id===el.dataset.id)v.archived_at=el.dataset.restore==='true'?null:new Date().toISOString();},
    'history-delete':async el=>{const owned=el.dataset.kind==='dataset'?'包括该数据集的字段配置版本和上传副本':el.dataset.kind==='run'?'包括运行的全部尝试和所属导出文件':'包括此对象的全部版本';if(!window.confirm(`永久删除 ${el.dataset.id}？\n${owned}。存在引用会拒绝删除。`))return;const result=await papi(`/history/${el.dataset.kind}/${el.dataset.id}`,'DELETE',{confirm_id:el.dataset.id});if(s.dataset?.id===el.dataset.id){s.dataset=null;s.rows=[];s.mapping={evaluation_fields:[]};s.mappingDirty=false;s.selected=[];}if(s.mapping?.id===el.dataset.id){s.mapping={evaluation_fields:[]};s.mappingDirty=false;}if(s.savedSpec?.id===el.dataset.id)actions['new-spec']();if(s.evaluator?.id===el.dataset.id){s.evaluator=null;s.evaluatorDirty=false;}if(s.run?.id===el.dataset.id){s.run=null;s.activeRunId=null;}s.historyDetail=null;await refresh();await loadHistory();s.notice=result.file_cleanup_pending?'对象已删除，部分暂存文件待清理。':'对象及所属文件已删除。';},
    'history-open':async()=>{const v=s.historyDetail.object,kind=v.kind||'run';if(dirty()&&!window.confirm('打开历史对象将替换相关草稿，是否继续？'))return;if(kind==='dataset'){await loadDataset(v);s.page=1;}else if(kind==='mapping'){await loadDataset(await papi(`/entities/${v.dataset_id}`));s.mapping=clone(v);s.mappingDirty=false;remember('mapping',v);s.page=1;}else if(kind==='spec'){setSpec(v);s.page=2;}else if(kind==='evaluator'){await openEvaluator(v);s.page=3;}else if(kind==='run'){await loadRun(v.id);s.page=4;}else if(kind==='design_call'){actions['new-spec']();s.generationPrompt=v.input_snapshot?.generation_prompt||'';s.criteria=v.result.criteria.map(c=>({...c,schemaText:pretty(c.rule_schema)}));s.designCallIds=[v.id];s.specDirty=true;s.page=2;s.notice='已恢复为未保存候选草稿，尚未创建标准版本。';}},
    'edit-profile':el=>chooseProfile(el.dataset.profileId),
    'new-profile':()=>{if(s.connectionDirty&&!window.confirm('新建将放弃当前未保存配置，是否继续？'))return;s.profileId=null;s.profileName='';s.connectionForm={api_url:'',api_key:'',model:'',max_tokens:4096,timeout:120,stream:false,enable_thinking:false,skip_special_tokens:false};s.executionForm={batch_concurrency:1};s.connectionDirty=true;s.connectionTest=null;},
    'save-as-profile':()=>{s.profileId=null;s.profileName=(s.profileName||'配置')+' 副本';s.connectionForm={...s.connectionForm,api_key:''};s.connectionDirty=true;s.connectionTest=null;s.notice='已复制为新配置草稿，请填写新名称和密钥后保存；不会自动启用。';},
    'save-connection':async()=>{const value=await api('/llm/profiles'+(s.profileId?'/'+s.profileId:''),s.profileId?'PUT':'POST',{name:s.profileName,...s.connectionForm,execution:s.executionForm});setConnection(value,value.profile_id);s.notice='配置已保存。是否用于后续调用由“设为当前使用”决定；编辑当前配置会更新后续调用参数。';},
    'activate-profile':async()=>{if(s.connectionDirty)throw new Error('请先保存修改再启用。');setConnection(await api(`/llm/profiles/${s.profileId}/activate`,'POST'),s.profileId);s.notice='已切换全局当前配置，已提交任务继续使用原连接。';invalidatePreview();},
    'delete-profile':async()=>{if(!window.confirm(`删除配置「${s.profileName}」？${s.profileId===s.llm.active_profile_id?'当前全局选择将被清除。':''}已提交任务继续执行；历史运行保留公开快照，但此配置将不能用于原配置重试。`))return;setConnection(await api(`/llm/profiles/${s.profileId}`,'DELETE'),null);},
    'test-connection':async()=>{s.connectionTest=await api('/llm/test','POST',{...s.connectionForm,profile_id:s.profileId});},
    'reload-connection':async()=>{if(s.connectionDirty&&!window.confirm('重新读取将丢弃未保存的模型设置，是否继续？'))return;setConnection(await api('/llm/profiles'));},
    'clear-key':async()=>{if(!window.confirm('清除此配置密钥？绑定该配置的后续模型调用及原配置重试将不可用，其他配置不受影响。'))return;setConnection(await api(`/llm/profiles/${s.profileId}/clear-key`,'POST'),s.profileId);},
  };
  async function download(format){const epoch=s.epoch,pid=s.project.id;const response=await fetch(`/api/v1/projects/${pid}/runs/${s.run.id}/export`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({format,original_fields:s.customExport?s.exportColumns:null,include_raw_response:s.includeRaw})});if(epoch!==s.epoch)throw new StaleResponse();if(!response.ok)throw new Error((await response.json()).detail);const blob=await response.blob();if(epoch!==s.epoch)throw new StaleResponse();const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=`${s.run.id}.${format}`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);s.notice='已导出，并在当前项目的运行中登记文件。';await loadRun(s.run.id);}
  function bind(target){
    const path=target.dataset.bind;if(!path||['projectChoice','datasetChoice','mappingChoice','specChoice','evaluatorChoice','runChoice'].includes(path))return;
    const value=target.type==='checkbox'?target.checked:target.type==='number'?Number(target.value):target.value;
    let obj=s;const keys=path.split('.');for(const k of keys.slice(0,-1))obj=obj[k];obj[keys.at(-1)]=value;
    if(path.startsWith('criteria.')||['title','generationPrompt','designNotes'].includes(path)){s.specDirty=true;if(path.startsWith('criteria.'))schedulePreview();}
    if(path.startsWith('evaluator.')||['schemaText','projectionText','exampleText'].includes(path)){s.evaluatorDirty=true;if(path!=='evaluator.title')s.example=null;schedulePreview();}
    if(path.startsWith('connectionForm.')||path.startsWith('executionForm.')||path==='profileName'){s.connectionDirty=true;s.connectionTest=null;}
  }
  function syncControls(){const disabled={
    'save-project':!s.projectForm?.name?.trim(),
    'save-connection':!s.profileName.trim()||!Number.isInteger(s.executionForm.batch_concurrency)||s.executionForm.batch_concurrency<1||s.executionForm.batch_concurrency>32,
    'activate-profile':!s.profileId||s.connectionDirty||!s.llm.profiles.find(p=>p.id===s.profileId)?.api_key_configured,
    'save-mapping':readonly()||s.dataset?.archived_at||!s.mapping.evaluation_fields.length,
    'save-spec':readonly()||s.savedSpec?.archived_at||!s.title.trim()||!s.criteria.length||s.criteria.some(c=>!c.name.trim()),
    'save-evaluator':readonly()||s.evaluator?.archived_at||!s.evaluator?.title?.trim(),
    extract:readonly()||!s.llm.configured||!s.generationPrompt.trim(),
    analyze:readonly()||!s.llm.configured||!s.savedSpec||s.specDirty||!s.mapping.id||s.mappingDirty||!s.sampleId,
    build:readonly()||!s.savedSpec||s.savedSpec.archived_at||s.specDirty,
    preview:!canPreview(),trial:!canExecute()||!s.selected.length,
    run:!canExecute()||(s.selectionMode==='all'?!s.allConfirmed:!s.selected.length),
    rerun:!canExecute()||!s.run||s.run.archived_at||active(s.run.status)||!s.rerunRows.length||s.dataset?.id!==s.run.snapshot.dataset.id,
  };for(const node of document.querySelectorAll('[data-connection-state]'))node.textContent=s.connectionDirty?'表单有未保存修改。':s.profileId?'正在查看已保存配置。':'保存后仍需手动启用。';for(const el of document.querySelectorAll('button[data-action]'))if(Object.hasOwn(disabled,el.dataset.action))el.disabled=!!(s.busy||disabled[el.dataset.action]);for(const el of document.querySelectorAll('[data-save-state]')){const k=el.dataset.saveState;const v=k==='spec'?s.savedSpec:s[k];el.textContent=s[k+'Dirty']?'有未保存修改':v?.id?`已保存 v${v.version}`:'尚未保存';}}
  document.addEventListener('input',e=>{bind(e.target);syncControls();});
  document.addEventListener('change',async e=>{const t=e.target,d=t.dataset;
    if(d.file){const file=t.files[0];if(file)await action('file','读取文件',async()=>{if(d.file==='prompt'){s.generationPrompt=await file.text();s.specDirty=true;}else await chooseFile(file);});return;}
    if(d.bind==='projectChoice'){await action('open-project','打开项目',()=>switchProject(t.value));return;}
    const loaders={mappingChoice:()=>{s.mapping=clone(s.catalog.mappings.find(v=>v.id===t.value));s.mappingDirty=false;s.preview=null;remember('mapping',s.mapping);},datasetChoice:()=>loadDataset(s.catalog.datasets.find(v=>v.id===t.value)),specChoice:()=>setSpec(s.catalog.specs.find(v=>v.id===t.value)),evaluatorChoice:()=>openEvaluator(s.catalog.evaluators.find(v=>v.id===t.value)),runChoice:()=>loadRun(t.value)};
    if(loaders[d.bind]){if(t.value)await action('open','加载保存内容',loaders[d.bind]);return;}
    if(d.field!==undefined){invalidatePreview();s.mapping.evaluation_fields=s.mapping.evaluation_fields.filter(v=>v!==d.field);if(t.checked)s.mapping.evaluation_fields.push(d.field);s.mappingDirty=true;s.preview=null;}
    if(d.row)s.selected=t.checked?[...new Set([...s.selected,d.row])]:s.selected.filter(v=>v!==d.row);
    if(d.rerunRow)s.rerunRows=t.checked?[...new Set([...s.rerunRows,d.rerunRow])]:s.rerunRows.filter(v=>v!==d.rerunRow);
    if(d.exportColumn!==undefined)s.exportColumns=t.checked?[...new Set([...s.exportColumns,d.exportColumn])]:s.exportColumns.filter(v=>v!==d.exportColumn);
    bind(t);
    if(d.bind==='pageSize'){s.pageSize=Number(t.value);await action('page-size','加载数据页',()=>loadDataset(s.dataset,0));return;}
    if(d.bind==='runPageSize'){s.runPageSize=Number(t.value);await action('run-page-size','加载结果页',()=>loadRunRows(0));return;}
    if(d.bind==='sampleId'||d.field!==undefined)schedulePreview();
    if(['historyKind','includeArchived'].includes(d.bind)){s.historyDetail=null;await action('history-refresh','加载历史',loadHistory);return;}
    if(d.bind==='showArchivedProjects'){await action('filter-projects','加载项目',loadProjects);return;}
    if(t.tagName==='SELECT'||['checkbox','radio'].includes(t.type))render();else syncControls();
  });
  document.addEventListener('click',async e=>{
    const close=e.target.closest?.('[data-help-close]');if(close||e.target.dataset.helpOverlay){s.help=null;render();return;}
    const help=e.target.closest?.('[data-help]');if(help){s.help=help.dataset.help;render();document.querySelector('[data-help-close]')?.focus();return;}
    const dismiss=e.target.closest?.('[data-dismiss]');if(dismiss){s[dismiss.dataset.dismiss]='';render();return;}
    const nav=e.target.closest?.('[data-page]');if(nav&&!nav.disabled){e.preventDefault();invalidatePreview();s.page=Number(nav.dataset.page);if(s.page===0)await action('projects','加载项目',loadProjects);else if(s.page===5)await action('history-refresh','加载历史',loadHistory);else{render();if(s.page===3)schedulePreview();}return;}
    const el=e.target.closest?.('[data-action]');if(!el||el.disabled)return;e.preventDefault();if(actions[el.dataset.action])await action(el.dataset.action,el.textContent.trim(),()=>actions[el.dataset.action](el));
  });
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&s.help){s.help=null;render();}});
  window.addEventListener('beforeunload',e=>{if(dirty()||s.connectionDirty){e.preventDefault();e.returnValue='';}});
  render();
  action('init','连接工作台',async()=>{await api('/health');s.online=true;setConnection(await api('/llm/profiles'));await loadProjects();const last=localStorage.getItem('eval.v1_1.project');if(last&&s.projects.some(p=>p.id===last))await switchProject(last);});
})();
