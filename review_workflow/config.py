from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .utils import hash_json


RESERVED_COLUMNS = {"review_status", "review_raw_output"}


def _required_mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"'{key}' must be a mapping.")
    return value


def _required_string(data: dict[str, Any], key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{context}.{key}' must be a non-empty string.")
    return value.strip()


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve()


def load_config(config_path: Path) -> dict[str, Any]:
    config_path = config_path.resolve()
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot load config: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Config root must be a mapping.")

    base = config_path.parent
    source = _required_mapping(raw, "source")
    fields = _required_mapping(raw, "fields")
    generation = _required_mapping(raw, "generation_prompt")
    evaluation = _required_mapping(raw, "evaluation_prompt")
    report = _required_mapping(raw, "report")
    output = _required_mapping(raw, "output")
    selection = _required_mapping(raw, "selection")

    def parse_fields(name: str) -> list[dict[str, str]]:
        items = fields.get(name)
        if not isinstance(items, list) or not items:
            raise ValueError(f"'fields.{name}' must be a non-empty list.")
        result = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"'fields.{name}[{index}]' is invalid.")
            result.append(
                {
                    "field": _required_string(
                        item, "field", f"fields.{name}[{index}]"
                    ),
                    "label": _required_string(
                        item, "label", f"fields.{name}[{index}]"
                    ),
                }
            )
        return result

    criteria_raw = raw.get("criteria")
    if not isinstance(criteria_raw, list) or not criteria_raw:
        raise ValueError("'criteria' must be a non-empty list.")
    criteria = []
    for index, item in enumerate(criteria_raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"'criteria[{index}]' is invalid.")
        criteria.append(
            {
                "name": _required_string(item, "name", f"criteria[{index}]"),
                "instruction": _required_string(
                    item, "instruction", f"criteria[{index}]"
                ),
            }
        )

    config = {
        "version": raw.get("version"),
        "source": {
            "file": _resolve(base, _required_string(source, "file", "source")),
            "sheet": _required_string(source, "sheet", "source"),
            "header_row": source.get("header_row"),
        },
        "selection": dict(selection),
        "fields": {
            "inputs": parse_fields("inputs"),
            "outputs": parse_fields("outputs"),
        },
        "generation_prompt": {
            "file": _resolve(
                base, _required_string(generation, "file", "generation_prompt")
            )
        },
        "evaluation_prompt": {
            "file": _resolve(
                base, _required_string(evaluation, "file", "evaluation_prompt")
            )
        },
        "criteria": criteria,
        "report": {"enabled": report.get("enabled")},
        "output": {
            "directory": _resolve(
                base, _required_string(output, "directory", "output")
            )
        },
    }
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    if config["version"] != 1:
        raise ValueError("'version' must be 1.")
    source = config["source"]
    if not isinstance(source["header_row"], int) or source["header_row"] < 1:
        raise ValueError("'source.header_row' must be an integer >= 1.")
    if source["file"].suffix.lower() != ".xlsx":
        raise ValueError("'source.file' must be a .xlsx file.")
    for path in (source["file"], config["generation_prompt"]["file"]):
        if not path.is_file():
            raise ValueError(f"Required file does not exist: {path}")

    selection = config["selection"]
    modes = [key for key in ("all", "excel_rows", "key") if key in selection]
    if len(modes) != 1 or len(selection) != 1:
        raise ValueError(
            "'selection' must contain exactly one of all, excel_rows, key."
        )
    mode = modes[0]
    if mode == "all" and selection["all"] is not True:
        raise ValueError("'selection.all' must be true.")
    if mode == "excel_rows":
        rows = selection["excel_rows"]
        if (
            not isinstance(rows, list)
            or not rows
            or any(not isinstance(row, int) for row in rows)
            or len(rows) != len(set(rows))
            or any(row <= source["header_row"] for row in rows)
        ):
            raise ValueError("'selection.excel_rows' is invalid.")
    if mode == "key":
        key = selection["key"]
        if not isinstance(key, dict) or set(key) != {"field", "values"}:
            raise ValueError("'selection.key' is invalid.")
        _required_string(key, "field", "selection.key")
        if not isinstance(key["values"], list) or not key["values"]:
            raise ValueError("'selection.key.values' cannot be empty.")

    configured = config["fields"]["inputs"] + config["fields"]["outputs"]
    names = [item["field"] for item in configured]
    labels = [item["label"] for item in configured]
    if len(names) != len(set(names)):
        raise ValueError("Configured Excel fields must be unique.")
    if len(labels) != len(set(labels)):
        raise ValueError("Field labels must be unique.")
    if RESERVED_COLUMNS.intersection(names):
        raise ValueError("Reserved review columns cannot be configured.")
    criterion_names = [item["name"] for item in config["criteria"]]
    if len(criterion_names) != len(set(criterion_names)):
        raise ValueError("Criterion names must be unique.")
    if not isinstance(config["report"]["enabled"], bool):
        raise ValueError("'report.enabled' must be true or false.")


def task_spec_hash(config: dict[str, Any]) -> str:
    return hash_json(
        {"fields": config["fields"], "criteria": config["criteria"]}
    )
