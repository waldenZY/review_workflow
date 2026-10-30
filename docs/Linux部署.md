# Eval Studio v1.1：Python 源码部署

## 环境和启动

需要 Python 3.10+、pip 和可写的数据目录。环境名称不限，moto 只约束当前 Windows 本地调试。前端由浏览器直接执行，没有 Node.js、npm、前端构建或专用发布包。

复制完整源码，保留 backend/、frontend/、scripts/、config/、docs/ 和 requirements.txt。运行依赖统一一份清单，包括 Markdown 帮助渲染；没有 pytest 依赖或自动测试套件。

```bash
cd review_workflow
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/start.py --host 0.0.0.0 --port 8000 --data-dir "$HOME/eval-studio-data"
```

也可在任意目录执行 `/path/to/python /path/to/review_workflow/scripts/start.py ...`。浏览器访问服务器地址同一端口，页面、API、帮助由同一 Python 服务提供。

| 设置 | 默认及含义 |
| --- | --- |
| --host | 127.0.0.1；服务器访问可显式设 0.0.0.0 |
| --port | 8000，可设 1024–65535 |
| --data-dir | 数据根目录，优先于 EVAL_DATA_DIR；实际 v1.1 存储在其 v1_1/ 子目录 |
| 未指定数据目录 | 项目仓库同级的 review_workflow_data/，不依赖启动工作目录 |

单进程、单工作队列运行，不开启多个 Uvicorn worker。工具无登录系统，适用于受控内网单用户使用。

## 全局模型连接

页面可填写并保存配置，不必登录服务器编辑。也可在服务器手动准备：

```bash
cp config/llm_config.example.json config/llm_config.local.json
# 为命名配置填写本项目自己的八个连接字段；随后启动或重启服务
# active_profile_id 为 null 时，在页面保存并启用；也可手动填写相应配置 ID
```

固定相对于应用根目录读取 `config/llm_config.local.json`，不读取 .env，也不读取 project1 的配置。Local 文件不纳入 Git；示例只有占位值，没有可用凭证。

八字段：api_url、api_key、model、max_tokens、timeout、stream、enable_thinking、skip_special_tokens。api_url 为完整调用地址，不自动追加路径，不嵌入用户名密码；Bearer 密钥只放 api_key。后两个参数按既定协议放在 extra_body 对象内，需供应商支持。不自动加 response_format。

文件结构为 `schema_version: 2`、`active_profile_id` 和 `profiles` 数组；每项包含系统 ID、唯一名称、八字段 `config` 及独立的 `execution`，参照仓库示例。`execution.batch_concurrency` 为 1–32 的整数，默认 1；现有配置缺少时在内存按 1 处理，下次用户配置写入时补齐。原八字段文件可在内存识别为“原有配置”，第一次用户配置写入时转换，启动不写文件。

页面支持新建、编辑、另存为、删除、启用。保存先校验再原子写入并热加载，保存与启用分开；修改正在使用的配置会影响所有项目后续调用。新增或另存为需显式填写密钥，编辑留空保留同一配置密钥，清除独立操作。手动编辑文件需要重启才生效。对已提交任务，开始时捕获的连接不会因全局设置变化而改变；密钥只保存在内存和本地配置文件，不进入数据库、页面响应或导出。

GET `/api/v1/llm/status`、`/api/v1/llm/profiles` 返回脱敏状态和配置列表；POST `/api/v1/llm/profiles` 创建，PUT/DELETE `/api/v1/llm/profiles/{id}` 修改或删除，POST `…/{id}/activate` 启用，POST `…/{id}/clear-key` 清除该配置密钥。POST `/api/v1/llm/test` 使用当前表单短请求测试，可传 profile_id 沿用该配置密钥，不保存、不启用。保存、启动和刷新均不自动探测模型。

批跑并发仅为工作台调度参数，不发送给模型供应商，也不参与模型公开指纹。同一运行采用提交时固定的并发数，标准库线程池最多维持指定数量的在途记录；不同运行仍由单个运行队列按提交顺序处理，纯程序规则串行。不增加外部队列或多个服务进程。

并发数不是每秒请求数；提取、依据分析和连接测试不使用此设置。供应商限流作为技术失败保留，不自动重试或降并发。取消停止派发新记录，等待在途请求完成和结果保存，未派发记录保持未执行。关闭时不再派发、不允许迟到写库，在途 HTTP 仍可能需要等待供应商响应或超时；重启后按中断处理。

缺少或非法配置不阻止项目管理、数据导入、编辑、静态预览和帮助，也不阻止纯程序规则。真实模型操作会明确失败，不自动重试或降级。

## v1.1 持久化和旧数据

```text
数据根目录/
  v1_1/
    workbench_v1_1.sqlite3
    projects/
      project_<id>/
        imports/
        runs/
          run_<id>/
            export_<id>.xlsx 或 .jsonl
            export_<id>.manifest.json
    .deleting/  # 本版本的删除暂存目录
  # 原有 v0/v1 数据库、项目、上传文件仍留在原位置，不由 v1.1 读取或清理
```

v1.1 业务存储从空项目开始，不读取或迁移旧记录，也不自动删除旧文件。`--data-dir`/`EVAL_DATA_DIR` 指向数据根目录，程序固定追加一次 `v1_1`；不要把版本子目录重复作为根目录传入。全局模型文件仍在源码 config/ 下，不在项目目录内。

项目归属在数据库和接口中校验，业务接口位于 `/api/v1/projects/{project_id}/…`。项目归档后可查看和导出，恢复后才能修改执行。删除项目按名称确认整体清理，单个对象删除仍受引用保护；有未结束任务时不允许归档或删除项目。

导出清单只含格式、列选择、公开模型信息、本次 execution_config 等，不另存完整业务快照。SQLite 保存运行快照和逐次尝试，可从页面历史溯源。

升级源码时保留配置文件和数据目录。备份应停止服务后复制整个数据目录，同时单独保管本地模型配置。服务重启将未完成任务及设计调用标记中断，由用户决定重新发起或原配置重试。重试查找运行绑定的配置，无需切换全局选择。绑定配置的公开参数变化、密钥缺失或配置已删除时会拒绝；仅轮换密钥不改变指纹。原配置重试沿用原运行并发数，仅调整并发设置不阻止重试；新建复测使用当时全局当前配置和并发数。

## 帮助与验证

`/help/tutorial`、`/help/deployment` 服务端渲染仓库内 Markdown，支持目录、表格和代码块；页面侧边面板使用同一来源，不依赖外部 CDN。更新 docs/ 后重新打开帮助即可。只提供这两个白名单帮助页面，不开放任意路径读取。

部署检查可人工查看首页、静态资源、`/api/v1/health`、`/docs` 和帮助。功能验收只用一条记录；真实服务由用户配置后主动单条试评。当前 Linux 服务器和真实模型兼容性不能由 Windows 本地静态检查代替，实际完成范围见 [使用教程](使用教程.md) 和仓库内 v1.1 验收记录。本轮按用户要求保持服务关闭，仅完成静态检查，未进行页面、真实模型或 Linux 实机验收。
