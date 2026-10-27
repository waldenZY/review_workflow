# Linux 部署：直接运行源码

## 环境与文件

服务器需要 Python 3.10+、pip 和可写的数据目录。环境名称不限，moto 只用于当前 Windows 机器的本地调试。

前端为浏览器直接执行的 HTML、CSS、JavaScript，后端为 FastAPI。开发、修改页面和服务器运行均没有前端构建步骤，不需要 Node.js、npm、React 或专用发布包。

直接复制项目或拉取源码即可，保留 backend/、frontend/、scripts/start.py 和 requirements.txt。frontend/ 只包含 index.html、app.js、style.css，均为需要版本管理的源码。不要复制本地 Python 虚拟环境或运行数据来替代服务器环境安装。

## 安装与启动

在服务器选定的 Python 环境中执行：

```bash
cd review_workflow
python --version
python -m pip install -r requirements.txt
python scripts/start.py --host 0.0.0.0 --port 8000 --data-dir "$HOME/eval-studio-data"
```

没有独立环境时，可先使用 `python3 -m venv .venv` 和 `. .venv/bin/activate` 准备环境。依赖清单只有一份，也包含 pytest、httpx 两项测试工具。pip 需要可用的 Python 包源，当前不提供离线依赖集合。

浏览器打开 `http://服务器地址:8000`。Python 在同一地址提供页面、`/static` 资源及 `/api/v1` 接口；接口文档位于 `/docs`，健康检查位于 `/api/v1/health`。没有额外前端服务或开发代理端口。

## 参数与数据

| 参数 | 行为 |
| --- | --- |
| `--host` | 默认 127.0.0.1；服务器直接访问可显式使用 0.0.0.0 |
| `--port` | 默认 8000，可指定 1024–65535 |
| `--data-dir` | 指定可写数据目录，优先于环境变量 EVAL_DATA_DIR |
| 未指定数据目录 | 使用应用目录同级的 review_workflow_data/，不依赖启动时的工作目录 |

允许从任意目录执行 `python /path/to/review_workflow/scripts/start.py ...`。缺少三个前端文件中的任何一个时，启动入口会明确报错。

SQLite、导入副本和导出产物在数据目录中。更新程序时保留数据目录，替换源码并重启；原始测试资料继续位于项目外部。修改页面源码后刷新浏览器即可，修改后端后重启服务。

当前采用单进程、单队列，不开启多个 Uvicorn worker。v0 是无登录的单用户工具，适用于受控内网；进程常驻方式可沿用服务器已有管理工具。

## 验证边界

Python 测试直接复制后端、启动脚本及真实前端源码，在 PATH 不包含 Node.js、工作目录位于应用之外的情况下启动服务，检查 HTML、JavaScript、CSS、API 及独立数据目录。整个过程不构建页面、不生成专用发布包，不读取外部业务测试数据。

当前尚未连接实际 Linux 服务器，也未完成真实浏览器视觉验收。所有模型步骤继续使用模拟响应，不在本次修改中接入真实模型。
