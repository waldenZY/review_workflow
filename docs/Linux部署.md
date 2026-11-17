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

导入单文件上限为 100 MB（100 × 1024 × 1024 字节），XLSX 工作表检查与正式导入共用此上限。若经过反向代理，需要同时检查代理上传大小与超时设置，允许完整文件及 multipart 表单的额外开销；HTTP 413 也可能由代理返回。导入仍完整解析文件，XLSX 解压及大字段可能额外占用内存；提高上限不代表已完成所有 100 MB 数据形态的容量验收。更新后需重启 Python 后台进程并刷新浏览器，无新增依赖或数据库迁移。

## 存储修订 3：旧数据不兼容

目录仍为 `数据根目录/v1_1/workbench_v1_1.sqlite3`。`--data-dir` 指包含 `v1_1` 的根目录，不是 `v1_1` 本身；优先于 `EVAL_DATA_DIR`。

上线顺序：

1. 等当前任务结束，或明确取消并等待在途请求保存；导出需要的结果。
2. 停止旧版 Python 后台进程并确认退出。只更新源码可能出现新前端连接旧后端的情况。
3. 完整备份原业务数据目录，包括 SQLite 主文件、相关 WAL／SHM 及项目文件；保存配套旧源码以便回退。
4. 由部署人员自行清空旧业务目录，保留需要继续使用的源码 `config/llm_config.local.json`。程序不执行清理。
5. 更新完整源码并按原启动方式启动。空目录自动建立修订 3；旧库或未知结构会拒绝启动，不自动覆盖。
6. 打开 `/api/v1/health`，确认 `storage_revision` 为 3、`data_dir` 为预期路径。浏览器强制刷新；新的选择缓存不会恢复旧对象 ID。

不提供旧项目、标准、评测器或历史运行迁移。`scripts/migrate_storage.py` 已停用，执行时只返回说明，不读取或修改数据库。需要回退时，先停新服务，再配套恢复备份目录和旧版源码，不混用数据库文件。

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

页面通过下拉框加载参数，主要操作为保存修改、使用所选配置、测试连通性、新建配置；另存为、删除及清除密钥在更多操作。保存先校验再原子写入并热加载，保存与启用分开；修改正在使用的配置会影响所有项目后续调用。新增或另存为需显式填写密钥，编辑留空保留同一配置密钥，清除独立操作。手动编辑文件需要重启才生效。对已提交任务，开始时捕获的连接不会因全局设置变化而改变；密钥只保存在内存和本地配置文件，不进入数据库、页面响应或导出。

GET `/api/v1/llm/status`、`/api/v1/llm/profiles` 返回脱敏状态和配置列表；POST `/api/v1/llm/profiles` 创建，PUT/DELETE `/api/v1/llm/profiles/{id}` 修改或删除，POST `…/{id}/activate` 启用，POST `…/{id}/clear-key` 清除该配置密钥。POST `/api/v1/llm/test` 使用当前表单短请求测试，可传 profile_id 沿用该配置密钥，不保存、不启用。保存、启动和刷新均不自动探测模型。

批跑并发仅为工作台调度参数，不发送给模型供应商，也不参与模型公开指纹。同一运行采用提交时固定的并发数，标准库线程池最多维持指定数量的在途记录；不同运行仍由单个运行队列按提交顺序处理，纯程序规则串行。不增加外部队列或多个服务进程。

并发数不是每秒请求数；提取、依据分析和连接测试不使用此设置。供应商限流作为技术失败保留，不自动重试或降并发。取消停止派发新记录，等待在途请求完成和结果保存，未派发记录保持未执行。关闭时不再派发、不允许迟到写库，在途 HTTP 仍可能需要等待供应商响应或超时；重启后按中断处理。

缺少或非法配置不阻止项目管理、数据导入、编辑、静态预览和帮助，也不阻止纯程序规则。真实模型操作会明确失败，不自动重试或降级。

## 运行与验证

保持单个 Uvicorn worker、单运行队列；各运行按提交顺序排队，当前运行内部并发数为 1–32。取消后停止派发，等待在途工作保存；技术失败不自动重试。重启将未完成任务标为中断，需用户明确重试。

本地按标准模板的受控接口、导出、取消与重试，以及无头浏览器交互已验收；没有操作现有业务数据或请求真实模型。100 MB 密集业务数据、压缩 XLSX、生产吞吐及目标 Linux 容量仍需实测。历史性能改造记录见 `性能与运行体验修正.md`，其中修订 2 的迁移说明不适用于当前版本。
