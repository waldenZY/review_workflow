# Eval Studio v1

按项目管理的已有 LLM 内容评测工作台。使用 Python、FastAPI、SQLite 和原生 HTML/CSS/JavaScript，源码直接部署，无需 Node.js 或构建步骤。

**选择项目 → 导入数据 → 选择共同评测字段 → 配置标准 → 编辑消息 → 选择样本执行 → 基本预览 → 导出分析。**

## 启动

Python 3.10+，环境名称不限。当前 Windows 调试使用 moto：

```powershell
$motoPython = 'D:\Application\Anaconda3-5.3.1-Windows-x86_64\envs\moto\python.exe'
& $motoPython -m pip install -r requirements.txt
& $motoPython scripts/start.py
```

Linux：

```bash
python -m pip install -r requirements.txt
python scripts/start.py --host 0.0.0.0 --port 8000 --data-dir "$HOME/eval-studio-data"
```

打开 `http://127.0.0.1:8000` 或服务器地址。页面右上角“使用帮助”可以打开教程，不必离开编辑页；也可直接访问 `/help/tutorial`。API 文档位于 `/docs`。

## 项目与数据

- 首次先创建项目。项目内分别管理数据集、字段配置、标准方案、评测器、运行和设计调用，不增加任务层级。
- 项目之间不共享、不移动业务对象，接口和存储都检查项目归属。切换项目不会取消后台运行。
- 项目归档后可查看和导出，恢复后才能编辑执行；项目内有未完成任务时不能归档或删除。
- 删除项目先展示对象和文件范围，再输入项目名称确认；单对象删除仍受引用保护。
- 新数据库为数据目录内的 `workbench_v1.sqlite3`，文件在 `projects/{project_id}/`。默认数据目录为仓库外 `../review_workflow_data/`，可用 `--data-dir` 或 `EVAL_DATA_DIR` 指定。
- v1 不读取、迁移或兼容旧 `workbench.sqlite3`，不删除旧数据库及文件。新存储从空项目开始。

## 评测和导出

仅导入 JSONL、JSON、XLSX，不支持 CSV。全部标准共用选择的评测字段，不强制区分输入、输出、参考角色。导出专用字段不会自动发送给 Judge 或依据分析。生成任务 Prompt 仅供用户主动提取标准使用。

标准支持 LLM、非空和 JSON Schema。程序规则独立执行；一条记录的 LLM 标准共用一次真实请求。没有产品模拟、演示数据、故障注入、人工复核或回归案例模块。

System/User 模板显式使用 `{{criteria}}`、`{{fields}}`、`{{response_schema}}`，模板外无隐藏追加。消息预览和输出结构示例不调用模型。真实结果统一处理完整 JSON 代码围栏，再执行严格解析、Schema 校验和结果映射。

执行与导出在同一页，提供基本预览、取消、失败重试和选中样本新建复测。XLSX、JSONL 默认保留全部原始列，自定义时始终保留评测字段所属顶层列及额外勾选列。导出包含全部原始行及执行状态，未执行和失败行不会丢弃。

## 全局模型设置

页面“模型设置”编辑八个字段：`api_url`、`api_key`、`model`、`max_tokens`、`timeout`、`stream`、`enable_thinking`、`skip_special_tokens`。地址为完整调用 URL，不追加路径；后两个参数放入 `extra_body`，不自动添加 `response_format`。

保存到 `config/llm_config.local.json` 并立即生效，影响所有项目的后续调用；在途任务固定开始时的连接。密钥不回显、不进入数据库和导出，留空保留原密钥。也可参考 `config/llm_config.example.json` 在服务器填写后重启。Local 文件已被 Git 忽略。

保存和启动不会探测模型。“测试连接”单独触发短请求，不自动保存、不验证评测质量。没有有效配置仍可管理项目、编辑和预览，纯程序规则可执行；需要模型的操作会明确阻止。

## 文档与验收

- [完整使用教程](docs/使用教程.md)：页面同步渲染的操作文档。
- [Linux 部署](docs/Linux部署.md)：Python 源码部署、配置及持久化。
- [v1 实施与验收](docs/v1实施与验收.md)：协议、实施结果和验证边界。
- [设计文档索引](docs/README.md)：历史方案与迭代记录。

按约定移除现有自动测试和 pytest 依赖，不新建测试套件。验收只复用一条自建业务记录，采用人工操作及静态检查；不读取外部测试资料、不全量评测、不自动调用真实模型。真实接口由用户填写本项目配置后主动单条验收。
