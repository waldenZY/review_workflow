# Eval Studio 源码部署 · 存储修订 4

## 环境与启动

Python 3.10+、pip、可写数据目录。前端由 Python 服务直接提供，不需 Node.js 或构建。复制 `backend/`、`frontend/`、`scripts/`、`config/`、`docs/` 和 `requirements.txt`。

```bash
cd review_workflow
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/start.py --host 0.0.0.0 --port 8000 --data-dir "$HOME/eval-studio-data"
```

Windows 本地开发与验收使用 moto。Linux 环境名称不限。单进程、单 Uvicorn worker、单批跑 FIFO 队列；运行内部按固定并发处理组任务，单标准测试有独立单请求通道。工具无登录系统，适用于受控内网。

| 参数 | 含义 |
| --- | --- |
| `--host` | 默认 127.0.0.1，服务器可设 0.0.0.0 |
| `--port` | 默认 8000 |
| `--data-dir` | 数据根目录，优先于 `EVAL_DATA_DIR` |
| 未指定数据目录 | 仓库同级 `review_workflow_data/`，不依赖启动工作目录 |

实际存储为 `数据根目录/v1_1/workbench_v1_1.sqlite3`，上传及导出文件在 `v1_1/projects/{project_id}/`。`--data-dir` 指包含 `v1_1` 的目录，不是 `v1_1` 本身。

## 从旧版上线

修订 4 使用独立标准、批跑方案和组任务；不迁移旧业务数据，保持原目录与数据库文件名。

1. 结束任务，或取消后等待在途请求保存；导出需要保留的结果。
2. 停止旧 Python 进程并确认退出。仅更新代码而保留旧进程，可能出现新前端连接旧后端。
3. 完整备份业务目录及配套旧源码，包括 SQLite 主文件、WAL／SHM 和项目文件。
4. 由部署人员自行清空旧业务目录；单独保留源码 `config/llm_config.local.json`。程序不删除数据，也无需删除 `.git`。
5. 更新完整源码，按原数据根目录启动。空目录初始化修订 4；旧版本或未知结构拒绝启动，不迁移、不自动重建。
6. 查看 `/api/v1/health`，确认 `storage_revision: 4` 和预期 `data_dir`。浏览器刷新；静态资源使用内容哈希，选择缓存命名空间已换为 r4。

`scripts/migrate_storage.py` 仅返回“不支持迁移”说明，不访问数据库。回退时先停新服务，再配套恢复完整备份和旧源码，不混用结构。

## 模型连接

连接文件独立于业务目录，固定为源码 `config/llm_config.local.json`，已被 Git 忽略。页面可保存并热加载；手工修改文件需重启。示例为 `config/llm_config.example.json`，不含可用凭证。

文件 `schema_version: 2`，包含 `active_profile_id` 和命名 `profiles`。每份配置八字段为 `api_url`、`api_key`、`model`、`max_tokens`、`timeout`、`stream`、`enable_thinking`、`skip_special_tokens`。`api_url` 为完整请求地址；后两项放在 `extra_body`，不自动加 `response_format`。独立执行设置 `execution.batch_concurrency` 为 1～32，默认 1，不发送给模型，不参与公开模型指纹。

保存与启用分开；修改正在启用的配置影响后续调用，已提交运行保留原连接。编辑时密钥留空保留原值，新建／复制需填写。密钥只存在连接文件和内存，不进入业务快照、日志响应或导出。原八字段连接文件仍可在内存识别为“原有配置”，首次用户保存时转换；这与不支持业务库迁移相互独立。

无有效连接仍可管理项目、编辑、静态预览和执行纯程序组。保存、启动和刷新均不探测模型。连接测试是用户主动请求，不保存、不启用、不验证业务输出质量。

## 队列、重试与停止

一个数据行的一个组为调度任务；有界派发，所有组共享并发上限，网络不持有数据库写锁。不同运行 FIFO；自动重试轮次重新排到队尾。独立标准测试同时最多一个，不被批跑队列阻塞，额外占用最多一个模型请求。

批跑调用／JSON／Schema 失败的组最多追加 3 次自动重试，预算按每行每组持久化，人工重试和重启不清零。试跑不自动重试。取消停止派发并保存在途结果，未派发组保持未执行。取消、重启中断、调度或保存失败不自动续跑。重启把未完成组标记中断，用户明确重试后才继续。

停止服务不再派发或允许迟到写库，正在进行的 HTTP 仍可能等待响应或超时。维护前应尽量等待运行结束或取消收尾。

## 导入、代理与验证

单文件最大 100 MiB（100 × 1024 × 1024 字节），JSON、JSONL、XLSX。代理上传大小需允许完整文件及 multipart 开销，超时也需覆盖导入；HTTP 413 可能来自代理。导入仍需解析完整文件，压缩 XLSX 和超长字段可能额外占用内存。

分页先限制源行，再加载当前页组结果。导出分批处理整个源数据集。XLSX 将超出单元格容量的正文分段保存在「长文本」工作表。超出行列容量时可使用 JSONL。

本次验收仅用 moto、临时目录、自建记录、受控响应和本机无头浏览器；不读取现有业务库、不请求真实模型、不操作服务器。目标 Linux 实机、生产吞吐、100 MiB 各种材料形态及真实模型质量仍需上线环境验证。
