from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any


# 如果 extapi 不在当前项目中，把这里改成 extapi 所在项目的根目录。
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]

# 三类调用暂时共用同一个模型。请替换成实际的 model 参数。
MODEL_NAME = "请填写模型名称"


class Gateway:
    def generate(
        self,
        *,
        messages: list[dict[str, str]],
        purpose: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workspace = str(WORKSPACE_ROOT)
        if workspace not in sys.path:
            sys.path.append(workspace)

        try:
            from extapi.call_model_ import chat_completion_
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                f"Cannot import extapi.call_model_ from WORKSPACE_ROOT: "
                f"{WORKSPACE_ROOT}"
            ) from exc

        if MODEL_NAME == "请填写模型名称":
            raise ValueError(
                "Set MODEL_NAME in "
                "review_workflow/external_model_gateway.py first."
            )

        time.sleep(1)
        raw_output, _, __ = chat_completion_(
            messages=messages,
            model=MODEL_NAME,
        )

        return {
            "raw_output": raw_output,
            "metadata": {
                "model_alias": MODEL_NAME,
                "purpose": purpose,
            },
        }
