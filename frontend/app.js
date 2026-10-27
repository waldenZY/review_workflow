/* Browser-native UI. No dependencies, transpilation, bundler or Node.js required. */
(() => {
  'use strict';
  const root = document.getElementById('root');
  const pretty = value => JSON.stringify(value, null, 2);
  const clone = value => JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const short = value => typeof value === 'string' ? value : JSON.stringify(value);
  const labels = { pass: '通过', fail: '不通过', insufficient_evidence: '依据不足', not_applicable: '不适用', queued: '排队中', running: '运行中', cancelling: '取消中', cancelled: '已取消', completed: '已完成', completed_with_errors: '完成，有异常', interrupted: '已中断', call_failed: '调用失败', parse_failed: '解析失败', schema_failed: '结构不匹配', projection_failed: '路径映射失败', skipped: '已跳过', pending: '未运行', not_run: '未运行', failed: '任务异常' };
  const verdicts = ['pass', 'fail', 'insufficient_evidence', 'not_applicable'];
  const active = status => ['queued', 'running', 'cancelling'].includes(status);
  const steps = [
    ['数据管理', '导入与字段映射', '让已有数据成为可追溯的评测输入。先导入，再明确每个字段的角色。'],
    ['评测标准', '定义判断依据', '把质量要求转成独立标准，并确认判断所需的依据。'],
    ['评测器', '配置与请求预览', '构建评测器，检查实际请求，然后开始试评。'],
    ['执行任务', '试评与批量运行', '先看一条，再决定运行范围。每次运行固定数据与配置版本。'],
    ['结果与复核', '查看、校准与导出', '保留原始响应、人工修订与每一次执行尝试。'],
  ];
  const criterion = id => ({ id, name: '', description: '', enabled: true, method: 'llm', required_roles: ['output'], target_field: '', rule_schema: { type: 'object' }, schemaText: '{"type":"object"}' });
  const s = {
    page: 0, catalog: { datasets: [], mappings: [], specs: [], evaluators: [], runs: [], cases: [] },
    dataset: null, rows: [], offset: 0, selected: [], mapping: { inputs: [], outputs: [], references: [] }, mappingDirty: true,
    savedSpec: null, title: '我的评测方案', generationPrompt: '', designNotes: '', criteria: [criterion('C001')], specDirty: true,
    traces: [], extractMock: '', analysisMock: '', analysis: null, specVersions: [], evaluatorVersions: [],
    evaluator: null, evaluatorDirty: true, schemaText: '', projectionText: '', parametersText: '{}', preview: null,
    run: null, rowIndex: 0, attemptIndex: -1, exportFields: [], selectionMode: 'selected', allConfirmed: false,
    reviewCriterion: '', reviewVerdict: 'insufficient_evidence', reviewReason: '', saveCase: false, confirmHuman: false,
    file: null, sheets: [], sheet: '', headerRow: 1, busy: '', notice: '', error: '', errorTrace: null, online: false,
  };
  let pollTimer;

  function icon(name = 'grid', size = 17) {
    const paths = { grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>', arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>', plus: '<path d="M12 5v14M5 12h14"/>', upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>', play: '<path d="m8 4 13 8-13 8Z"/>', check: '<path d="m5 12 4 4L19 6"/>', save: '<path d="M5 3h12l4 4v14H3V3h2Zm2 0v6h10V3M7 21v-8h10v8"/>', lab: '<path d="M9 3h6M10 3v7L4 20h16l-6-10V3M8 14h8"/>', file: '<path d="M13 2H4v20h16V9Zm0 0v7h7M8 13h8M8 17h8"/>', refresh: '<path d="M20 7a9 9 0 1 0 1 8M20 2v6h-6"/>', trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>', download: '<path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/>', shield: '<path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6Zm-5 10 3 3 7-7"/>', code: '<path d="m8 5-6 7 6 7m8-14 6 7-6 7M14 2l-4 20"/>' };
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.grid}</svg>`;
  }
  function button(action, text, style = '', disabled = false, attributes = '') {
    return `<button class="button ${style}" data-action="${action}" ${disabled || s.busy ? 'disabled' : ''} ${attributes}>${text}</button>`;
  }
  function badge(value) { return `<span class="badge ${/^[a-z_]+$/.test(value || '') ? value : ''}">${esc(labels[value] || value)}</span>`; }
  function json(value) { return `<pre class="json-view">${esc(pretty(value))}</pre>`; }
  function field(title, input, hint = '') { return `<label class="field"><span>${esc(title)}</span>${input}${hint ? `<small>${esc(hint)}</small>` : ''}</label>`; }
  function input(bind, value, type = 'text', attributes = '') { return `<input data-bind="${bind}" type="${type}" value="${esc(value)}" ${attributes}>`; }
  function textarea(bind, value, rows = 4, code = false, placeholder = '') { return `<textarea data-bind="${bind}" rows="${rows}" ${code ? 'class="code-input" spellcheck="false"' : ''} placeholder="${esc(placeholder)}">${esc(value)}</textarea>`; }
  function options(values, selected) { return values.map(([value, text]) => `<option value="${esc(value)}" ${String(value) === String(selected) ? 'selected' : ''}>${esc(text)}</option>`).join(''); }
  function select(bind, values, selected, title = '') { return `<select data-bind="${bind}" aria-label="${esc(title)}">${options(values, selected)}</select>`; }
  function checkbox(bind, checked, label) { return `<label class="check-label"><input type="checkbox" data-bind="${bind}" ${checked ? 'checked' : ''}>${esc(label)}</label>`; }
  function details(id, title, content) { return `<details id="${id}" class="advanced"><summary>${esc(title)}</summary>${content}</details>`; }
  function panel(title, description, content, control = '') { return `<section class="panel"><div class="panel-heading"><div><h2>${title}</h2><p>${esc(description)}</p></div>${control}</div>${content}</section>`; }
  function next(page, text) { return `<div class="step-footer"><span>${esc(text)}</span><button class="button primary" data-page="${page}">下一步 · ${steps[page][0]}${icon('arrow')}</button></div>`; }
  function ready() { return Boolean(s.mapping.id && !s.mappingDirty && s.evaluator?.id && !s.evaluatorDirty && !s.specDirty); }
  function previewRow() { return s.selected[0] || s.rows[0]?.row_id; }
  function requireReady() { if (!ready()) throw new Error('请先保存字段映射、标准和评测器，未保存的修改不能用于运行。'); }

  async function api(path, method = 'GET', body) {
    const response = await fetch('/api/v1' + path, { method, headers: body === undefined || body instanceof FormData ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body) });
    const value = await response.json();
    if (!response.ok) { const error = new Error(typeof value.detail === 'string' ? value.detail : pretty(value.detail || value)); error.trace = value.trace; throw error; }
    return value;
  }
  async function action(label, task) {
    if (s.busy) return;
    s.busy = label; s.error = ''; s.errorTrace = null; s.notice = ''; render();
    try { await task(); } catch (error) { s.error = error.message || String(error); s.errorTrace = error.trace; }
    finally { s.busy = ''; render(); }
  }
  async function refresh() { s.catalog = await api('/catalog'); }
  async function loadDataset(value, offset = 0) {
    const changed = s.dataset?.id !== value.id;
    const result = await api(`/datasets/${value.id}/rows?offset=${offset}&limit=50`);
    s.dataset = value; s.rows = result.rows; s.offset = offset; s.preview = null;
    if (changed) {
      s.mapping = clone(s.catalog.mappings.find(m => m.dataset_id === value.id) || { dataset_id: value.id, inputs: [], outputs: [], references: [] });
      s.mappingDirty = !s.mapping.id; s.selected = result.rows.length ? [result.rows[0].row_id] : []; s.allConfirmed = false;
      localStorage.setItem('eval.dataset', value.id);
    }
  }
  function loadSpec(value) {
    s.savedSpec = value; s.title = value.title; s.generationPrompt = value.generation_prompt; s.designNotes = value.design_notes || '';
    s.criteria = clone(value.criteria).map(c => ({ ...c, schemaText: pretty(c.rule_schema) })); s.traces = clone(value.design_traces || []);
    s.specDirty = false; s.analysis = null; s.specVersions = []; localStorage.setItem('eval.spec', value.id);
  }
  function loadEvaluator(value) {
    s.evaluator = clone(value); s.schemaText = pretty(value.response_schema); s.projectionText = pretty(value.projection); s.parametersText = pretty(value.config.parameters);
    s.evaluatorDirty = !value.id; s.preview = null; s.evaluatorVersions = [];
    if (value.id) localStorage.setItem('eval.evaluator', value.id);
  }
  async function loadRun(id, onlyIfCurrent = false) {
    const value = await api(`/runs/${id}`);
    if (onlyIfCurrent && s.run?.id !== id) return;
    if (s.run?.id !== id) { s.rowIndex = 0; s.attemptIndex = -1; s.reviewCriterion = ''; s.reviewReason = ''; }
    if (s.run?.id !== id || pretty(s.run.export_fields) !== pretty(value.export_fields)) s.exportFields = [...value.export_fields];
    s.run = value; localStorage.setItem('eval.run', id);
    clearTimeout(pollTimer);
    if (active(value.status)) pollTimer = setTimeout(() => poll(id), 700);
  }
  async function poll(id) {
    if (s.run?.id !== id) return;
    try { await loadRun(id, true); if (s.run?.id !== id) return; if (!active(s.run.status)) await refresh(); if (s.page >= 3) render(); }
    catch (error) { s.error = error.message; render(); }
  }
  async function chooseFile(file) {
    if (!file) return;
    if (!/\.(jsonl|json|xlsx)$/i.test(file.name)) throw new Error('仅支持 JSONL、JSON、XLSX，不支持 CSV。');
    s.file = file; s.sheets = []; s.sheet = '';
    if (/\.xlsx$/i.test(file.name)) { const data = new FormData(); data.append('file', file); const value = await api('/datasets/inspect', 'POST', data); s.sheets = value.sheets; s.sheet = value.sheets[0]; }
  }
  async function importFile(file, demo = false) {
    if (!file) throw new Error('请先选择文件。');
    const data = new FormData(); data.append('file', file); data.append('sheet', s.sheet); data.append('header_row', s.headerRow);
    const value = await api('/datasets/import', 'POST', data); await refresh(); await loadDataset(value); s.file = null;
    if (demo) { s.mapping = { dataset_id: value.id, inputs: ['/task_input'], outputs: ['/generated_output'], references: ['/reference'] }; s.mappingDirty = true; }
    s.notice = demo ? '已载入一条独立模拟记录，请确认并保存字段映射。没有调用生成模型。' : '导入完成，请配置字段角色。';
  }
  async function startRun(single) {
    requireReady(); const mode = single ? 'selected' : s.selectionMode;
    if (mode === 'all' && !s.allConfirmed) throw new Error('全量运行需要明确确认；开发验证请只选一条。');
    const rowIds = mode === 'all' ? [] : single ? [previewRow()].filter(Boolean) : s.selected;
    const value = await api('/runs', 'POST', { mapping_id: s.mapping.id, mapping_version: s.mapping.version, evaluator_id: s.evaluator.id, evaluator_version: s.evaluator.version, selection_mode: mode, row_ids: rowIds });
    await loadRun(value.id); await refresh(); s.page = 3; s.notice = '任务已提交，所有模型步骤均为模拟。';
  }
  async function exportRun() {
    const response = await fetch(`/api/v1/runs/${s.run.id}/export`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ fields: s.exportFields }) });
    if (!response.ok) throw new Error((await response.json()).detail);
    const url = URL.createObjectURL(await response.blob()); const link = document.createElement('a'); link.href = url; link.download = `${s.run.id}_mock.xlsx`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    s.notice = '已导出 XLSX，完整快照同时保存在外部运行目录。';
  }

  function dataPage() {
    let html = `<div class="stat-row"><div class="stat-card">${icon('grid', 22)}<span>已保存数据集<strong>${s.catalog.datasets.length}<small>个数据集</small></strong></span></div><div class="stat-card">${icon('file', 22)}<span>当前记录数<strong>${s.dataset?.row_count ?? '—'}<small>条记录</small></strong></span></div><div class="stat-card">${icon('shield', 22)}<span>数据保护<strong class="text-stat">原始数据保留</strong><small>运行数据存于仓库外</small></span></div></div>`;
    html += panel('导入已有数据', '支持 JSONL、JSON 和 XLSX，单文件上限 25 MB。', `<div class="import-grid"><div class="drop-zone" data-drop="file"><div class="upload-icon">${icon('upload', 26)}</div><h3>${esc(s.file?.name || '拖放文件到这里，或点击选择')}</h3><p>只读取已有内容，不调用生成模型</p><label class="button file-button">选择文件<input type="file" data-file="dataset" accept=".jsonl,.json,.xlsx" hidden></label><div class="file-types"><span>JSONL</span><span>JSON</span><span>XLSX</span></div></div><div class="import-help"><span class="tiny-label">首次体验</span><h3>先用一条记录走通流程</h3><p>载入包含任务输入、已有输出与参考资料的独立模拟记录，不读取你的测试文件。</p>${button('demo', icon('lab') + '载入一条模拟记录', 'secondary')}<div class="inline-note">${icon('shield')}示例业务规则不会固化进产品</div></div></div>${s.file ? `<div class="toolbar">${s.sheets.length ? field('工作表', select('sheet', s.sheets.map(v => [v, v]), s.sheet)) + field('表头所在行', input('headerRow', s.headerRow, 'number', 'min="1"')) : ''}${button('import', '确认导入', 'primary')}</div>` : ''}`);
    if (s.dataset) {
      const keys = Object.keys(s.rows[0]?.original || {});
      const table = `<div class="table-scroll"><table class="data-table"><thead><tr><th>选择</th><th>原始行</th>${keys.map(k => `<th>${esc(k)}</th>`).join('')}</tr></thead><tbody>${s.rows.map(row => `<tr><td><input type="checkbox" data-row="${esc(row.row_id)}" aria-label="选择第 ${row.source_row} 条记录" ${s.selected.includes(row.row_id) ? 'checked' : ''}></td><td>${row.source_row}</td>${keys.map(k => `<td><span class="cell-preview" title="${esc(short(row.original[k]))}">${esc(short(row.original[k]))}</span></td>`).join('')}</tr>`).join('')}</tbody></table></div><div class="table-footer"><span>已选 ${s.selected.length} 条 · 共 ${s.dataset.row_count} 条 · 显示 ${s.offset + 1}–${s.offset + s.rows.length}</span><div class="button-group">${button('previous', '上一页', '', s.offset === 0)}${button('next', '下一页', '', s.offset + s.rows.length >= s.dataset.row_count)}</div></div>`;
      const mapping = `<div class="mapping-table"><div class="mapping-header"><span>字段路径</span><span>任务输入</span><span>已有输出 *</span><span>参考依据</span></div>${s.dataset.fields.map(f => `<div class="mapping-row"><div><code>${esc(f.path)}</code><small>${esc(f.types.join(' / '))}</small></div>${['inputs', 'outputs', 'references'].map(role => `<input type="checkbox" data-map="${role}" data-path="${esc(f.path)}" aria-label="${esc(f.path)} ${role}" ${s.mapping[role].includes(f.path) ? 'checked' : ''}>`).join('')}</div>`).join('')}</div><div class="panel-footer"><span>${s.mapping.id && !s.mappingDirty ? `映射 v${s.mapping.version} 已保存` : '至少选择一个已有输出字段并保存映射。'}</span>${button('save-mapping', icon('save') + '保存字段映射', 'primary', !s.mapping.outputs.length)}</div>`;
      html += panel('数据预览与字段映射', '每个字段只能映射一个角色，嵌套对象使用路径选择，数组保留原结构。', table + mapping, select('datasetChoice', s.catalog.datasets.map(d => [d.id, `${d.filename} · ${d.row_count} 条`]), s.dataset.id, '当前数据集'));
    }
    return html + next(1, '数据准备好后，开始定义评测标准。');
  }

  function versionButtons(type, versions) {
    return versions.length ? `<div class="version-list">${versions.map(v => button('load-version', `v${v.version} · ${new Date(v.created_at).toLocaleString('zh-CN')}`, '', false, `data-type="${type}" data-version="${v.version}"`)).join('')}</div>` : '';
  }
  function standardPage() {
    let html = panel('评测方案', '没有生成 Prompt 也可以手动定义标准。', field('方案名称', input('title', s.title)) + field('生成任务 Prompt（可选）', textarea('generationPrompt', s.generationPrompt, 5), '只用作候选标准来源，不执行其中的任务。') + `<div class="toolbar"><label class="button file-button">读取 .md / .txt<input type="file" data-file="prompt" accept=".md,.txt" hidden></label>${button('extract', icon('lab') + '模拟提取候选标准（替换草稿）', 'secondary', !s.generationPrompt.trim())}</div>` + details('extract-response', '高级 · 提取步骤的模拟 JSON 响应', textarea('extractMock', s.extractMock, 5, true, '留空使用通用模拟候选。自定义格式：{"criteria": [...]}')), `<div class="button-group">${select('specChoice', [['', '选择已有方案'], ...s.catalog.specs.map(v => [v.id, `${v.title} · v${v.version}`])], s.savedSpec?.id || '', '打开标准方案')}${button('new-spec', icon('plus') + '新建')}</div>`);
    const cards = s.criteria.map((c, i) => `<article class="criterion-card ${c.enabled ? '' : 'disabled-card'}"><div class="criterion-top"><span class="criterion-order">${String(i + 1).padStart(2, '0')}</span><input data-bind="criteria.${i}.id" class="id-input mono" aria-label="标准 ${i + 1} ID" value="${esc(c.id)}"><input data-bind="criteria.${i}.name" class="criterion-name" aria-label="标准 ${i + 1} 名称" placeholder="为这条标准命名" value="${esc(c.name)}">${checkbox(`criteria.${i}.enabled`, c.enabled, '启用')}${button('delete-criterion', icon('trash'), '', false, `data-index="${i}" aria-label="删除标准 ${i + 1}"`)}</div>${textarea(`criteria.${i}.description`, c.description, 2, false, '说明判断范围、合格条件和边界…')}<div class="criterion-options">${field('评测方式', select(`criteria.${i}.method`, [['llm', 'LLM 判断（模拟）'], ['non_empty', '程序规则 · 非空'], ['json_schema', '程序规则 · JSON Schema']], c.method))}<div class="field"><span>必要依据</span><div class="role-options">${[['input', '任务输入'], ['output', '已有输出'], ['reference', '参考依据']].map(([value, label]) => `<label class="check-label"><input type="checkbox" data-criterion-role="${value}" data-index="${i}" ${c.required_roles.includes(value) ? 'checked' : ''}>${label}</label>`).join('')}</div></div>${c.method !== 'llm' ? field('规则作用字段', select(`criteria.${i}.target_field`, [['', '第一个已映射输出'], ...s.mapping.outputs.map(v => [v, v])], c.target_field)) : ''}</div>${c.method === 'json_schema' ? field('待评测输出 JSON Schema', textarea(`criteria.${i}.schemaText`, c.schemaText, 5, true)) : ''}</article>`).join('');
    html += panel(`独立评测标准 <span class="count-tag">${s.criteria.length}</span>`, '标准 ID 用于版本间追踪。', `<div class="criteria-list">${cards}</div>` + field('人工确认与设计备注', textarea('designNotes', s.designNotes, 2)) + `<div class="panel-footer"><span data-save-state="spec">${s.specDirty ? '草稿有未保存修改' : `标准 v${s.savedSpec.version} 已保存`}</span>${button('save-spec', icon('save') + (s.savedSpec ? `保存为 v${s.savedSpec.version + 1}` : '保存标准'), 'primary', !s.criteria.length)}</div>${s.savedSpec ? details('spec-versions', '标准历史版本', button('spec-versions', '刷新历史版本') + versionButtons('spec', s.specVersions)) : ''}`, button('add-criterion', icon('plus') + '添加标准'));
    const analysis = s.analysis ? `<div class="analysis-grid">${s.analysis.reports.map(r => `<div class="analysis-card"><strong>${esc(r.criterion_id)}</strong>${badge(r.missing_roles.length ? 'insufficient_evidence' : '角色内容已提供')}<p>需要：${esc(r.required_roles.join(' / '))}</p>${r.missing_roles.length ? `<p>缺少：${esc(r.missing_roles.join(' / '))}</p>` : ''}<small>${esc(r.risk)}</small></div>`).join('')}</div>${json(s.analysis.advice)}` : '<div class="empty-inline">先保存字段映射和标准，再选一条记录检查依据。</div>';
    html += panel('依据与可评测性', '真实检查字段角色是否有内容，分析建议使用模拟响应；不保证语义充分。', analysis + details('analysis-response', '高级 · 分析步骤的模拟 JSON 响应', textarea('analysisMock', s.analysisMock, 4, true)) + (s.traces.length ? details('design-traces', `设计请求与模拟响应（${s.traces.length}）`, json(s.traces)) : ''), button('analyze', icon('shield') + '检查依据（模拟建议）', 'secondary', !s.savedSpec || s.specDirty || !s.mapping.id || s.mappingDirty || !previewRow()));
    return html + next(2, '保存标准后，构建绑定该版本的评测器。');
  }

  function evaluatorPage() {
    const ev = s.evaluator;
    let content = `<div class="build-banner"><div>${icon()}<span>当前标准：<strong>${esc(s.savedSpec?.title || '尚未保存')}</strong>${s.savedSpec ? ` · v${s.savedSpec.version}` : ''}${s.specDirty ? '（草稿未保存）' : ''}</span></div>${button('build', icon('lab') + '按标准构建草稿', 'secondary', !s.savedSpec || s.specDirty)}</div>`;
    if (ev) {
      content += `<div class="binding-note">本评测器绑定标准 <code>${esc(ev.spec_id)}</code> · v${ev.spec_version}${ev.version ? ` / 评测器 v${ev.version}` : ''}</div>` + field('Judge Prompt', textarea('evaluator.prompt', ev.prompt, 6));
      content += `<div class="form-grid">${field('Base URL（仅预览）', input('evaluator.config.base_url', ev.config.base_url))}${field('模型名称（模拟）', input('evaluator.config.model', ev.config.model))}${field('API Key 环境变量名', input('evaluator.config.api_key_env', ev.config.api_key_env), '只保存变量名，不读取密钥。')}${field('预设输出模式', select('evaluator.config.output_mode', [['json_object', 'JSON Object'], ['json_schema', 'JSON Schema'], ['text', 'Text（本地仍校验 Schema）']], ev.config.output_mode))}${field('预设超时（秒）', input('evaluator.config.timeout_seconds', ev.config.timeout_seconds, 'number', 'min="1" max="600"'))}${field('模型参数 JSON', textarea('parametersText', s.parametersText, 2, true))}</div>`;
      content += `<div class="form-grid">${field('评测返回 JSON Schema', textarea('schemaText', s.schemaText, 13, true))}${field('结果路径映射', textarea('projectionText', s.projectionText, 13, true), '使用 JSON Pointer。未映射标准不参与统计。')}</div><div class="mock-config"><h3>${icon('lab')}模拟响应配置</h3><p>预设结论不分析内容，用于验证解析与失败处理。</p>${field('模拟场景', select('evaluator.mock_scenario', [...verdicts.map(v => [v, labels[v]]), ['invalid_json', '非法 JSON'], ['schema_error', 'Schema 不匹配'], ['request_error', '调用失败']], ev.mock_scenario))}${field('自定义模拟响应正文（可选）', textarea('evaluator.mock_response', ev.mock_response || '', 5, true), '修改返回 Schema 后，请同步填写兼容的模拟 JSON。故障场景优先于自定义正文。')}</div><div class="panel-footer"><span data-save-state="evaluator">${s.evaluatorDirty ? '评测器有未保存修改' : `评测器 v${ev.version} 已保存`}</span>${button('save-evaluator', icon('save') + '保存评测器版本', 'primary')}</div>${ev.id ? details('evaluator-versions', '评测器历史版本', button('evaluator-versions', '刷新历史版本') + versionButtons('evaluator', s.evaluatorVersions)) : ''}`;
    } else content += '<div class="empty-state"><h3>还没有评测器</h3><p>先保存标准，再按标准构建草稿。</p></div>';
    return panel('评测器配置', '标准决定评什么，评测器决定如何执行。', content, select('evaluatorChoice', [['', '选择已有评测器'], ...s.catalog.evaluators.map(v => [v.id, `${v.id.slice(-8)} · v${v.version} · 标准 v${v.spec_version}`])], ev?.id || '', '打开评测器')) + panel('实际请求预览', '使用选中的第一条记录，预览不触发调用。', s.preview ? json(s.preview) : '<div class="empty-inline">保存配置后，可核对完整请求及版本。</div>', button('preview', icon('code') + '预览请求', 'secondary', !ready() || !previewRow())) + next(3, 'v0 不会向预设地址发送真实模型请求。');
  }

  function executionPage() {
    const mode = s.selectionMode;
    let html = panel('创建评测任务', '运行固定当前保存版本，不受后续编辑影响。', `<div class="run-config"><div><span class="tiny-label">数据集</span><strong>${esc(s.dataset?.filename || '尚未选择')}</strong></div><div><span class="tiny-label">字段映射</span><strong>${s.mapping.id ? `v${s.mapping.version}${s.mappingDirty ? ' · 有修改' : ''}` : '尚未保存'}</strong></div><div><span class="tiny-label">标准 / 评测器</span><strong>${s.evaluator?.id ? `标准 v${s.evaluator.spec_version} / 评测器 v${s.evaluator.version}` : '尚未保存'}</strong></div></div><div class="selection-options">${[['selected', '选中的样本', `当前选择 ${s.selected.length} 条`], ['all', '全部记录', `共 ${s.dataset?.row_count || 0} 条，需要明确确认`]].map(([value, title, hint]) => `<label class="selection-card ${mode === value ? 'chosen' : ''}"><input type="radio" name="selection" data-bind="selectionMode" value="${value}" ${mode === value ? 'checked' : ''}><span><strong>${title}</strong><small>${hint}</small></span></label>`).join('')}</div>${mode === 'all' ? `<div class="toolbar">${checkbox('allConfirmed', s.allConfirmed, `我确认运行全部 ${s.dataset?.row_count || 0} 条记录。开发验证只选一条。`)}</div>` : ''}${!ready() ? '<div class="inline-note">请先保存字段映射、标准与评测器。</div>' : ''}<div class="panel-footer"><span>串行执行 · 逐条保存 · 支持取消与重试</span><div class="button-group">${button('trial', icon('lab') + '试评一条', '', !ready() || !previewRow())}${button('run', icon('play') + '开始评测', 'primary', !ready() || (mode === 'all' ? !s.allConfirmed : !s.selected.length))}</div></div>`, '<span class="mock-pill">仅模拟</span>');
    html += panel(`运行记录 <span class="count-tag">${s.catalog.runs.length}</span>`, '刷新和重启后仍可打开已保存的运行。', s.catalog.runs.length ? `<div class="run-list">${s.catalog.runs.map(r => `<button class="run-list-item ${s.run?.id === r.id ? 'selected' : ''}" data-action="load-run" data-id="${r.id}"><span class="run-icon">${icon('play')}</span><span><strong>${esc(r.spec_title)}</strong><small>${esc(r.dataset_name)} · ${new Date(r.created_at).toLocaleString('zh-CN')} · ${r.id.slice(-8)}</small></span>${badge(s.run?.id === r.id ? s.run.status : r.status)}${icon('arrow')}</button>`).join('')}</div>` : '<div class="empty-state"><h3>开始第一次试评</h3><p>先用一条记录检查输入、响应和结果。</p></div>', button('refresh-runs', icon('refresh') + '刷新'));
    if (s.run) {
      const r = s.run; const percent = Math.round(r.summary.processed / r.summary.selected * 100);
      html += panel(`当前任务 ${badge(r.status)}`, r.id, `<div class="progress-label"><span>已处理 ${r.summary.processed} / ${r.summary.selected} 条</span><span>${percent}%</span></div><div class="progress-bar"><span style="width:${percent}%"></span></div><div class="status-counts">${Object.entries(r.summary.statuses).map(([k, v]) => `<span>${badge(k)} ${v}</span>`).join('')}</div>${r.error ? `<div class="message error">${esc(r.error)}</div>` : ''}`, `<div class="button-group">${active(r.status) ? button('cancel', '取消') : r.records.some(v => v.status !== 'completed') ? button('retry', icon('refresh') + '原配置重试') : ''}<button class="button primary" data-page="4">查看结果${icon('arrow')}</button></div>`);
    }
    return html;
  }

  function resultsPage() {
    const r = s.run;
    if (!r) return panel('还没有评测结果', '先完成一次单条试评。', '<button class="button primary" data-page="3">前往执行任务</button>');
    const record = r.records[s.rowIndex]; const attempt = record?.attempts.at(s.attemptIndex);
    const source = r.snapshot.dataset.rows.find(v => v.row_id === record?.row_id);
    let html = `<div class="result-toolbar">${select('runChoice', s.catalog.runs.map(v => [v.id, `${v.spec_title} · ${v.id.slice(-8)}`]), r.id, '选择运行结果')}${badge(r.status)}<span class="mock-pill">模拟运行 · 不代表真实质量</span></div><div class="verdict-stats">${Object.entries(r.summary.verdict_counts).map(([k, v]) => `<div class="verdict-stat ${k}"><span>${labels[k]}</span><strong>${v}</strong><small>标准判断次数</small></div>`).join('')}</div><p class="statistics-note">${esc(r.summary.denominator_note)}</p>`;
    let content = `<div class="result-context"><span class="mono">${esc(record.row_id)}</span>${badge(record.status)}<span>已尝试 ${record.attempts.length} 次</span>${record.attempts.length ? select('attemptIndex', record.attempts.map((a, i) => [i, `第 ${i + 1} 次 · ${labels[a.status] || a.status}`]), s.attemptIndex < 0 ? record.attempts.length - 1 : s.attemptIndex, '选择执行尝试') : ''}</div>`;
    if (attempt?.error) content += `<div class="message error">${esc(attempt.error)}</div>`;
    if (r.validation_case) content += `<div class="message loading">人工案例预期：${esc(r.validation_case.criterion_id)} = ${labels[r.validation_case.expected]}；当前判断：${esc(labels[attempt?.criteria.find(c => c.criterion_id === r.validation_case.criterion_id)?.verdict] || '暂无判断')}。标准版本变化后需重新确认适用性。</div>`;
    content += attempt?.criteria.length ? `<div class="verdict-list">${attempt.criteria.map(c => `<article class="verdict-item"><div class="verdict-item-title"><code>${esc(c.criterion_id)}</code><strong>${esc(c.name)}</strong>${badge(c.verdict || '未映射')}<span class="source-label">${c.source === 'llm' ? 'LLM · 模拟' : c.source === 'evidence_check' ? '依据检查' : '内置规则'}</span></div><p>${esc(c.reason)}</p>${c.evidence.length ? `<blockquote>${esc(c.evidence.join('\n'))}</blockquote>` : ''}</article>`).join('')}</div>` : '<div class="empty-inline">暂无标准判定。技术异常不转换为内容不合格。</div>';
    content += `<div class="details-grid">${details('original-record', '原始记录与已有输出', json(source?.original))}${details('raw-response', '实际请求与原始响应', json({ request: attempt?.request, raw_response: attempt?.raw_response, raw_output: attempt?.raw_output }))}${details('parsed-payload', '解析后的 JSON 载荷', json(attempt?.parsed))}${details('run-snapshot', '固定的运行配置', json({ mapping: r.snapshot.mapping, spec: r.snapshot.spec, evaluator: r.snapshot.evaluator }))}</div>`;
    content += `<div class="subsection"><h3>人工复核</h3><p>修订追加保存，保留原始判断及对应的执行尝试。</p><div class="form-grid three">${field('标准', select('reviewCriterion', [['', '选择标准'], ...r.snapshot.spec.criteria.filter(c => c.enabled).map(c => [c.id, `${c.id} · ${c.name}`])], s.reviewCriterion))}${field('人工结论', select('reviewVerdict', verdicts.map(v => [v, labels[v]]), s.reviewVerdict))}${field('修订理由', input('reviewReason', s.reviewReason, 'text', 'placeholder="为什么修订或确认此判断"'))}</div><div class="toolbar">${checkbox('saveCase', s.saveCase, '保存为回归案例')}${s.saveCase ? checkbox('confirmHuman', s.confirmHuman, '我已独立人工确认，未把模拟判定当作正确答案') : ''}${button('review', icon('save') + '保存复核', 'primary', active(r.status) || !attempt || !s.reviewCriterion || !s.reviewReason.trim() || (s.saveCase && !s.confirmHuman))}</div>${record.reviews.length ? `<div class="review-history">${record.reviews.map(v => `<div><span class="human-tag">人工</span><code>${esc(v.criterion_id)}</code>${badge(v.verdict)}<span>${esc(v.reason)}</span><small>${new Date(v.created_at).toLocaleString('zh-CN')}</small></div>`).join('')}</div>` : ''}</div>`;
    html += panel('逐条结果与证据', `标准 v${r.snapshot.spec.version} · 评测器 v${r.snapshot.evaluator.version} · ${r.snapshot.dataset.filename}`, content, select('rowIndex', r.records.map((v, i) => [i, `${v.row_id.split(':').at(-1)} · ${labels[v.status] || v.status}`]), s.rowIndex, '选择结果样本'));
    html += panel('导出 XLSX', '原列与行序保留，评测列追加；未参与运行的行标记为未运行。', `<div class="export-options">${r.export_fields.length ? r.export_fields.map(path => `<label class="check-label"><input type="checkbox" data-export="${esc(path)}" ${s.exportFields.includes(path) ? 'checked' : ''}><code>${esc(path)}</code></label>`).join('') : '<span class="muted">无可展开载荷，仍可导出执行状态、标准判断与人工复核。</span>'}</div>`, button('export', icon('download') + '导出结果', 'primary', active(r.status)));
    const cases = s.catalog.cases.length ? `<div class="case-list">${s.catalog.cases.map(c => `<div><div><strong>${esc(c.criterion_id)}</strong>${badge(c.expected)}<small>标准 v${c.spec_version} · 人工确认 · 来源为模拟运行</small><p>${esc(c.reason)}</p></div>${button('rerun-case', '复测此案例' + icon('arrow'), '', !s.evaluator?.id || s.evaluatorDirty, `data-id="${c.id}"`)}</div>`).join('')}</div>` : '<div class="empty-inline">独立人工确认后可保存案例。模拟结论本身不作为正确答案。</div>';
    return html + panel('回归案例与复测', '使用当前已保存的评测器新建运行，历史记录保持不变。', cases, button('rerun', icon('refresh') + '同批样本复测', '', !s.evaluator?.id || s.evaluatorDirty));
  }

  function render() {
    const opened = [...document.querySelectorAll('details[open][id]')].map(node => node.id);
    const focus = document.activeElement?.getAttribute('data-bind');
    const selection = focus && typeof document.activeElement.selectionStart === 'number' ? [document.activeElement.selectionStart, document.activeElement.selectionEnd] : null;
    const pages = [dataPage, standardPage, evaluatorPage, executionPage, resultsPage];
    root.innerHTML = `<div class="app-shell"><aside class="sidebar"><a class="brand" href="#" data-page="0"><span class="brand-mark">${icon('grid', 23)}</span><span>Eval Studio<small>LLM 评测工作台</small></span></a><div class="workspace-label">工作空间 <span>v0</span></div><nav aria-label="主要功能">${steps.map((v, i) => `<button class="nav-item ${s.page === i ? 'active' : ''}" data-page="${i}">${icon(i === 3 ? 'play' : 'grid')}<span>${v[0]}<small>${v[1]}</small></span></button>`).join('')}</nav><div class="sidebar-note">${icon('lab', 20)}<strong>v0 · 流程验证</strong><p>模型响应全部模拟。<br>每个判断都可以追溯。</p><span class="mini-dot"></span>不发送真实模型请求</div><a class="api-link" href="/api/v1/health" target="_blank" rel="noreferrer">服务状态与存储位置</a></aside><div class="main-shell"><header class="topbar"><div class="breadcrumb">工作空间 › <span>${steps[s.page][0]}</span></div><div class="topbar-right"><span class="connection ${s.online ? 'connected' : ''}"></span><span>${s.online ? '服务已连接' : '等待服务连接'}</span><span class="mock-pill">${icon('lab', 13)}模拟模式</span></div></header><main><div class="page-heading"><div><div class="eyebrow">EVALUATION WORKSPACE / 0${s.page + 1}</div><h1>${steps[s.page][0]}</h1><p>${steps[s.page][2]}</p></div><span class="version-tag">WORKBENCH v0</span></div><div class="flow-strip">${steps.map((v, i) => `<button class="${s.page === i ? 'current' : ''}" data-page="${i}"><span>${i + 1}</span>${v[0]}</button>`).join('')}</div>${s.busy ? `<div class="message loading">${esc(s.busy)}…</div>` : ''}${s.notice ? `<div class="message success">${icon('check')}<span>${esc(s.notice)}</span><button data-action="dismiss-notice" aria-label="关闭提示">×</button></div>` : ''}${s.error ? `<div class="message error"><div>${esc(s.error)}${s.errorTrace ? details('error-trace', '查看失败请求与响应', json(s.errorTrace)) : ''}</div><button data-action="dismiss-error" aria-label="关闭错误">×</button></div>` : ''}${pages[s.page]()}<footer class="app-footer"><span>Eval Studio · 每一步都可以检查，每个版本都有记录。</span><span>Python 服务 · 模拟模式</span></footer></main></div></div>`;
    for (const id of opened) { const node = document.getElementById(id); if (node) node.open = true; }
    if (focus) {
      const node = [...root.querySelectorAll('[data-bind]')].find(node => node.dataset.bind === focus);
      if (node) { node.focus(); if (selection && node.setSelectionRange) node.setSelectionRange(...selection); }
    }
  }

  const actions = {
    demo: async () => { const row = { sample_id: 'DEMO-001', task_input: { instruction: '用一句话概括参考内容。', material: '该工具用于导入已有输出、配置评测标准并查看结果。' }, generated_output: '这是一条模拟已有输出，用于验证评测流程。', reference: '评测对象为已有输出。', provenance: { is_mock: true, note: '流程演示，不是人工确认答案。' } }; await importFile(new File([pretty([row])], 'demo_one_record.json', { type: 'application/json' }), true); },
    import: () => importFile(s.file),
    previous: () => loadDataset(s.dataset, Math.max(0, s.offset - 50)),
    next: () => loadDataset(s.dataset, s.offset + 50),
    'save-mapping': async () => { s.mapping = await api('/mappings', 'POST', { id: s.mapping.id, dataset_id: s.dataset.id, inputs: s.mapping.inputs, outputs: s.mapping.outputs, references: s.mapping.references }); s.mappingDirty = false; await refresh(); s.notice = `映射 v${s.mapping.version} 已保存。`; },
    'new-spec': () => { s.savedSpec = null; s.title = '新的评测方案'; s.generationPrompt = ''; s.designNotes = ''; s.criteria = [criterion('C001')]; s.traces = []; s.analysis = null; s.specDirty = true; s.specVersions = []; },
    'add-criterion': () => { let n = s.criteria.length + 1; while (s.criteria.some(c => c.id === `C${String(n).padStart(3, '0')}`)) n++; s.criteria.push(criterion(`C${String(n).padStart(3, '0')}`)); s.specDirty = true; },
    'delete-criterion': el => { s.criteria.splice(Number(el.dataset.index), 1); s.specDirty = true; },
    'save-spec': async () => { const criteria = s.criteria.map(({ schemaText, ...c }) => ({ ...c, rule_schema: c.method === 'json_schema' ? JSON.parse(schemaText) : c.rule_schema })); loadSpec(await api('/specs', 'POST', { id: s.savedSpec?.id, title: s.title, generation_prompt: s.generationPrompt, design_notes: s.designNotes, criteria, design_traces: s.traces })); await refresh(); s.notice = `标准 v${s.savedSpec.version} 已保存。评测器仍使用其原绑定版本，可重新构建。`; },
    extract: async () => { const value = await api('/specs/extract', 'POST', { generation_prompt: s.generationPrompt, mock_response: s.extractMock || null }); s.criteria = value.criteria.map(c => ({ ...c, schemaText: pretty(c.rule_schema) })); s.traces.push(value.trace); s.specDirty = true; s.notice = value.notice; },
    analyze: async () => { s.analysis = await api('/evidence/analyze', 'POST', { mapping_id: s.mapping.id, mapping_version: s.mapping.version, spec_id: s.savedSpec.id, spec_version: s.savedSpec.version, row_id: previewRow(), mock_response: s.analysisMock || null }); s.traces.push(s.analysis.trace); s.specDirty = true; s.notice = '依据分析已完成，可修改标准或备注并再次保存。'; },
    build: async () => { const value = await api('/evaluators/build', 'POST', { spec_id: s.savedSpec.id, spec_version: s.savedSpec.version }); if (s.evaluator?.id && s.evaluator.spec_id === value.spec_id) value.id = s.evaluator.id; loadEvaluator(value); s.evaluatorDirty = true; s.notice = '评测器草稿已构建，编辑并保存后继续。'; },
    'save-evaluator': async () => { const value = { ...s.evaluator, response_schema: JSON.parse(s.schemaText), projection: JSON.parse(s.projectionText), config: { ...s.evaluator.config, parameters: JSON.parse(s.parametersText) }, mock_response: s.evaluator.mock_response || null }; loadEvaluator(await api('/evaluators', 'POST', value)); await refresh(); s.notice = `评测器 v${s.evaluator.version} 已保存。`; },
    preview: async () => { requireReady(); s.preview = await api('/requests/preview', 'POST', { mapping_id: s.mapping.id, mapping_version: s.mapping.version, evaluator_id: s.evaluator.id, evaluator_version: s.evaluator.version, row_id: previewRow() }); },
    'spec-versions': async () => { s.specVersions = await api(`/entities/${s.savedSpec.id}/versions`); },
    'evaluator-versions': async () => { s.evaluatorVersions = await api(`/entities/${s.evaluator.id}/versions`); },
    'load-version': async el => { const isSpec = el.dataset.type === 'spec'; const id = isSpec ? s.savedSpec.id : s.evaluator.id; const value = await api(`/entities/${id}?version=${Number(el.dataset.version)}`); if (isSpec) loadSpec(value); else loadEvaluator(value); },
    trial: () => startRun(true),
    run: () => startRun(false),
    'refresh-runs': async () => { await refresh(); if (s.run) await loadRun(s.run.id); },
    'load-run': el => loadRun(el.dataset.id),
    cancel: async () => { await api(`/runs/${s.run.id}/cancel`, 'POST'); await loadRun(s.run.id); },
    retry: async () => { await api(`/runs/${s.run.id}/retry`, 'POST'); await loadRun(s.run.id); await refresh(); },
    review: async () => { const record = s.run.records[s.rowIndex]; await api(`/runs/${s.run.id}/rows/${encodeURIComponent(record.row_id)}/review`, 'POST', { criterion_id: s.reviewCriterion, verdict: s.reviewVerdict, reason: s.reviewReason, save_case: s.saveCase, confirm_mock: s.confirmHuman }); await loadRun(s.run.id); await refresh(); s.reviewReason = ''; s.saveCase = false; s.confirmHuman = false; s.notice = '人工复核已追加保存，原判断未覆盖。'; },
    export: exportRun,
    rerun: async () => { const value = await api(`/runs/${s.run.id}/rerun`, 'POST', { evaluator_id: s.evaluator.id, evaluator_version: s.evaluator.version }); await loadRun(value.id); await refresh(); s.page = 3; },
    'rerun-case': async el => { const value = await api(`/cases/${el.dataset.id}/rerun`, 'POST', { evaluator_id: s.evaluator.id, evaluator_version: s.evaluator.version }); await loadRun(value.id); await refresh(); s.page = 3; },
  };

  function bind(target) {
    const path = target.dataset.bind;
    if (!path || ['datasetChoice', 'specChoice', 'evaluatorChoice', 'runChoice'].includes(path)) return;
    let value = target.type === 'checkbox' ? target.checked : target.type === 'number' || ['rowIndex', 'attemptIndex'].includes(path) ? Number(target.value) : target.value;
    const keys = path.split('.'); let object = s;
    for (const key of keys.slice(0, -1)) object = object[key];
    object[keys.at(-1)] = value;
    if (path.startsWith('criteria.') || ['title', 'generationPrompt', 'designNotes'].includes(path)) s.specDirty = true;
    if (path.startsWith('evaluator.') || ['schemaText', 'projectionText', 'parametersText'].includes(path)) { s.evaluatorDirty = true; s.preview = null; }
  }
  function syncControls() {
    const record = s.run?.records[s.rowIndex];
    const disabled = {
      extract: !s.generationPrompt.trim(),
      analyze: !s.savedSpec || s.specDirty || !s.mapping.id || s.mappingDirty || !previewRow(),
      build: !s.savedSpec || s.specDirty,
      preview: !ready() || !previewRow(),
      trial: !ready() || !previewRow(),
      run: !ready() || (s.selectionMode === 'all' ? !s.allConfirmed : !s.selected.length),
      review: !s.run || active(s.run.status) || !record?.attempts.length || !s.reviewCriterion || !s.reviewReason.trim() || (s.saveCase && !s.confirmHuman),
    };
    for (const node of document.querySelectorAll('button[data-action]')) {
      if (Object.hasOwn(disabled, node.dataset.action)) node.disabled = Boolean(s.busy || disabled[node.dataset.action]);
    }
    for (const node of document.querySelectorAll('[data-save-state]')) {
      node.textContent = node.dataset.saveState === 'spec'
        ? (s.specDirty ? '草稿有未保存修改' : `标准 v${s.savedSpec?.version} 已保存`)
        : (s.evaluatorDirty ? '评测器有未保存修改' : `评测器 v${s.evaluator?.version} 已保存`);
    }
  }
  document.addEventListener('input', event => { bind(event.target); syncControls(); });
  document.addEventListener('change', async event => {
    const target = event.target; const d = target.dataset;
    if (d.file) { const file = target.files[0]; if (!file) return; await action('读取文件', async () => { if (d.file === 'prompt') { s.generationPrompt = await file.text(); s.specDirty = true; } else await chooseFile(file); }); return; }
    const loaders = { datasetChoice: () => loadDataset(s.catalog.datasets.find(v => v.id === target.value)), specChoice: () => loadSpec(s.catalog.specs.find(v => v.id === target.value)), evaluatorChoice: () => loadEvaluator(s.catalog.evaluators.find(v => v.id === target.value)), runChoice: () => loadRun(target.value) };
    if (loaders[d.bind]) { if (target.value) await action('打开记录', loaders[d.bind]); return; }
    if (d.map) { for (const role of ['inputs', 'outputs', 'references']) s.mapping[role] = s.mapping[role].filter(v => v !== d.path); if (target.checked) s.mapping[d.map].push(d.path); s.mappingDirty = true; s.preview = null; }
    if (d.row) s.selected = target.checked ? [...new Set([...s.selected, d.row])] : s.selected.filter(v => v !== d.row);
    if (d.criterionRole) { const c = s.criteria[Number(d.index)]; c.required_roles = c.required_roles.filter(v => v !== d.criterionRole); if (target.checked) c.required_roles.push(d.criterionRole); s.specDirty = true; }
    if (d.export !== undefined) s.exportFields = target.checked ? [...new Set([...s.exportFields, d.export])] : s.exportFields.filter(v => v !== d.export);
    bind(target); if (d.bind === 'rowIndex') { s.attemptIndex = -1; s.reviewCriterion = ''; s.reviewReason = ''; }
    // Replacing a button during a text field's blur/change can cancel its click.
    // Text edits update state and button availability without rebuilding the DOM.
    if (target.tagName === 'SELECT' || ['checkbox', 'radio'].includes(target.type)) render(); else syncControls();
  });
  document.addEventListener('click', async event => {
    const nav = event.target.closest?.('[data-page]');
    if (nav) { event.preventDefault(); s.page = Number(nav.dataset.page); render(); return; }
    const el = event.target.closest?.('[data-action]');
    if (!el || el.disabled) return;
    event.preventDefault();
    const name = el.dataset.action;
    if (name === 'dismiss-error') { s.error = ''; render(); return; }
    if (name === 'dismiss-notice') { s.notice = ''; render(); return; }
    if (actions[name]) await action(el.textContent.trim() || '处理操作', () => actions[name](el));
  });
  document.addEventListener('dragover', event => { if (event.target.closest?.('[data-drop]')) event.preventDefault(); });
  document.addEventListener('drop', async event => { if (event.target.closest?.('[data-drop]')) { event.preventDefault(); await action('读取文件', () => chooseFile(event.dataTransfer.files[0])); } });

  render();
  action('连接工作台', async () => {
    await api('/health'); s.online = true; await refresh();
    const pick = (values, key) => values.find(v => v.id === localStorage.getItem('eval.' + key)) || values[0];
    const ds = pick(s.catalog.datasets, 'dataset'); if (ds) await loadDataset(ds);
    const sp = pick(s.catalog.specs, 'spec'); if (sp) loadSpec(sp);
    const ev = pick(s.catalog.evaluators, 'evaluator'); if (ev) loadEvaluator(ev);
    const run = pick(s.catalog.runs, 'run'); if (run) await loadRun(run.id);
  });
})();
