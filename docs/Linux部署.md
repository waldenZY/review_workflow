# Eval Studio v1：Python 源码部署

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
| --data-dir | 实际数据目录，优先于进程环境变量 EVAL_DATA_DIR |
| 未指定数据目录 | 项目仓库同级的 review_workflow_data/，不依赖启动工作目录 |

单进程、单工作队列运行，不开启多个 Uvicorn worker。工具无登录系统，适用于受控内网单用户使用。

## 全局模型连接

页面可填写并保存配置，不必登录服务器编辑。也可在服务器手动准备：

```bash
cp config/llm_config.example.json config/llm_config.local.json
# 编辑为本项目自己的八个参数和凭证，然后启动或重启服务
```

固定相对于应用根目录读取 `config/llm_config.local.json`，不读取 .env，也不读取 project1 的配置。Local 文件不纳入 Git；示例只有占位值，没有可用凭证。

八字段：api_url、api_key、model、max_tokens、timeout、stream、enable_thinking、skip_special_tokens。api_url 为完整调用地址，不自动追加路径，不嵌入用户名密码；Bearer 密钥只放 api_key。后两个参数按既定协议放在 extra_body 对象内，需供应商支持。不自动加 response_format。

页面保存先校验再原子写入，成功后热加载，影响所有项目的新调用。手动编辑文件需要重启才生效。对已提交任务，开始时捕获的连接不会因全局设置变化而改变；密钥只保存在内存和本地配置文件，不进入数据库、页面响应或导出。

GET `/api/v1/llm/config`、`/api/v1/llm/status` 返回无密钥的状态和公开参数。PUT `/api/v1/llm/config` 保存配置；POST `/api/v1/llm/clear-key` 清除密钥；POST `/api/v1/llm/test` 使用提交的设置短请求测试，不保存。保存、启动和刷新均不自动探测模型。

缺少或非法配置不阻止项目管理、数据导入、编辑、静态预览和帮助，也不阻止纯程序规则。真实模型操作会明确失败，不自动重试或降级。

## v1 持久化和旧数据

```text
数据目录/
  workbench_v1.sqlite3
  projects/
    project_<id>/
      imports/
      runs/
        run_<id>/
          export_<id>.xlsx 或 .jsonl
          export_<id>.manifest.json
  .deleting/  # 删除暂存目录，正常清理后为空
```

v1 从全新数据库开始，不迁移、读取或兼容旧 `workbench.sqlite3`，也不自动删除旧数据库、imports/、runs/ 等目录。可以沿用数据目录而不会覆盖旧库，但旧数据不会显示在 v1 页面。全局八字段模型文件仍是独立配置文件。

项目归属在数据库和接口中校验，业务接口位于 `/api/v1/projects/{project_id}/…`。项目归档后可查看和导出，恢复后才能修改执行。删除项目按名称确认整体清理，单个对象删除仍受引用保护；有未结束任务时不允许归档或删除项目。

导出清单只含格式、列选择、公开模型信息等，不另存完整业务快照。SQLite 保存运行快照和逐次尝试，可从页面历史溯源。

升级源码时保留配置文件和数据目录。备份应停止服务后复制整个数据目录，同时单独保管本地模型配置。服务重启将未完成任务及设计调用标记中断，由用户决定重新发起或原配置重试。公开模型指纹变化时不能原配置重试，仅轮换密钥不改变指纹。

## 帮助与验证

`/help/tutorial`、`/help/deployment` 服务端渲染仓库内 Markdown，支持目录、表格和代码块；页面侧边面板使用同一来源，不依赖外部 CDN。更新 docs/ 后重新打开帮助即可。只提供这两个白名单帮助页面，不开放任意路径读取。

部署检查可人工查看首页、静态资源、`/api/v1/health`、`/docs` 和帮助。功能验收只用一条记录；真实服务由用户配置后主动单条试评。当前 Linux 服务器和真实模型兼容性不能由 Windows 本地静态检查代替，实际完成范围见 [使用教程](使用教程.md) 和仓库内 v1 验收记录。
