# Batch Review Workflow V1

工作流按配置、Excel、模型调用和流程控制拆成少量模块。每个审查任务放在独立的
`tasks` 子目录中。V1 不支持断点续跑、并发、Agent、Skill 或结构化评估结果。

## 使用

进入目录：

```powershell
cd D:\wwc\matter
$python = 'D:\Application\Anaconda3-5.3.1-Windows-x86_64\envs\moto\python.exe'
```

生成评估 Prompt：

```powershell
& $python -m review_workflow build-prompt tasks\outline_review\review.yaml --gateway mock
```

检查或修改 `tasks\outline_review\evaluation_prompt.md`，然后运行审查：

```powershell
& $python -m review_workflow run tasks\outline_review\review.yaml --gateway mock
```

结果位于 `tasks\outline_review\runs\<run_id>\`。

`mock` 只用于验证流程。接入真实模型时，使用
`--gateway package.module:create_gateway`；工厂返回的对象只需实现
`generate(messages=..., purpose=..., metadata=...)`，并返回包含
`raw_output` 和 `metadata` 的字典。

## 使用 extapi

项目已包含 `review_workflow/external_model_gateway.py`。先修改其中的：

```python
WORKSPACE_ROOT = Path("extapi 所在项目根目录")
MODEL_NAME = "实际模型名称"
```

然后使用：

```powershell
& $python -m review_workflow build-prompt tasks\outline_review\review.yaml `
  --gateway review_workflow.external_model_gateway:Gateway

& $python -m review_workflow run tasks\outline_review\review.yaml `
  --gateway review_workflow.external_model_gateway:Gateway
```
