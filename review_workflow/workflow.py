from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import load_config
from .excel_io import open_review_workbook, select_cases, validate_source
from .model_io import (
    BUILD_PROMPT_FILE,
    REPORT_PROMPT_FILE,
    build_case_messages,
    build_evaluation_messages,
    build_report_messages,
    call_gateway,
    load_evaluation_prompt,
    save_prompt_metadata,
    validate_templates,
)
from .utils import (
    EventLogger,
    create_run_id,
    hash_file,
    hash_json,
    hash_text,
    jsonable,
    read_text,
    serialize_error,
    write_text,
)


def _create_run(
    config: dict[str, Any]
) -> tuple[str, Path, EventLogger]:
    run_id = create_run_id()
    run_dir = config["output"]["directory"] / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    logger = EventLogger(run_dir / "workflow_events.jsonl", run_id)
    return run_id, run_dir, logger


def _started_payload(
    config_path: Path, config: dict[str, Any], run_dir: Path
) -> dict[str, Any]:
    return {
        "config_file": str(config_path.resolve()),
        "config_snapshot": jsonable(config),
        "source_file_hash": hash_file(config["source"]["file"]),
        "generation_prompt_hash": hash_file(
            config["generation_prompt"]["file"]
        ),
        "builder_prompt_hash": hash_file(BUILD_PROMPT_FILE),
        "report_prompt_hash": hash_file(REPORT_PROMPT_FILE),
        "output_directory": str(run_dir),
    }


def build_prompt(
    config_path: Path, gateway: Any, force: bool = False
) -> Path:
    config = load_config(config_path)
    validate_templates()
    run_id, run_dir, logger = _create_run(config)
    stage = "initialization"
    try:
        logger.emit(
            "workflow_started",
            "started",
            _started_payload(config_path, config, run_dir),
        )
        stage = "source_validation"
        validate_source(config)

        prompt_file = config["evaluation_prompt"]["file"]
        if prompt_file.exists() and not force:
            raise ValueError(
                f"Evaluation prompt exists: {prompt_file}. "
                "Use --force to replace it."
            )

        stage = "evaluation_prompt_build"
        builder_prompt = read_text(BUILD_PROMPT_FILE)
        generation_prompt = read_text(config["generation_prompt"]["file"])
        messages = build_evaluation_messages(
            config, builder_prompt, generation_prompt
        )
        logger.emit(
            "evaluation_prompt_build_requested",
            "started",
            {
                "request": {"messages": messages},
                "input_hash": hash_json(messages),
            },
        )
        output, model_metadata = call_gateway(
            gateway,
            messages,
            "evaluation_prompt_build",
            {"run_id": run_id},
        )
        write_text(prompt_file, output)
        metadata_file = save_prompt_metadata(
            config,
            build_run_id=run_id,
            builder_prompt=builder_prompt,
            generation_prompt=generation_prompt,
            generated_prompt=output,
        )
        logger.emit(
            "evaluation_prompt_saved",
            "success",
            {
                "file": str(prompt_file),
                "metadata_file": str(metadata_file),
                "content_hash": hash_text(output),
                "model_metadata": model_metadata,
            },
        )
        logger.emit(
            "workflow_completed",
            "success",
            {
                "completion_reason": "evaluation_prompt_generated",
                "evaluation_prompt": str(prompt_file),
            },
        )
        return run_dir
    except Exception as exc:
        logger.emit(
            "workflow_failed",
            "failed",
            {"stage": stage, "error": serialize_error(exc)},
        )
        raise
    finally:
        logger.close()


def _review_cases(
    *,
    config: dict[str, Any],
    gateway: Any,
    run_id: str,
    logger: EventLogger,
    evaluation_prompt: str,
    evaluation_hash: str,
    sheet: Any,
    cases: list[dict[str, Any]],
    status_column: int,
    output_column: int,
) -> tuple[int, int, list[str]]:
    completed = 0
    failed = 0
    report_outputs: list[str] = []
    for case in cases:
        messages = build_case_messages(evaluation_prompt, case)
        logger.emit(
            "case_review_started",
            "started",
            {
                "case_key": case["case_key"],
                "sheet": case["sheet"],
                "excel_row": case["excel_row"],
                "evaluation_prompt_hash": evaluation_hash,
                "request_input_hash": hash_json(messages),
            },
        )
        try:
            output, model_metadata = call_gateway(
                gateway,
                messages,
                "case_review",
                {
                    "run_id": run_id,
                    "case_key": case["case_key"],
                    "excel_row": case["excel_row"],
                },
            )
            if len(output) > 32767:
                raise ValueError("Review output exceeds the Excel cell limit.")
            sheet.cell(case["excel_row"], status_column, "completed")
            sheet.cell(case["excel_row"], output_column, output)
            report_outputs.append(output)
            completed += 1
            logger.emit(
                "case_review_completed",
                "success",
                {
                    "case_key": case["case_key"],
                    "sheet": case["sheet"],
                    "excel_row": case["excel_row"],
                    "raw_output_hash": hash_text(output),
                    "raw_output_character_count": len(output),
                    "model_metadata": model_metadata,
                },
            )
        except Exception as exc:
            sheet.cell(case["excel_row"], status_column, "failed")
            sheet.cell(case["excel_row"], output_column, None)
            failed += 1
            logger.emit(
                "case_review_failed",
                "failed",
                {
                    "case_key": case["case_key"],
                    "sheet": case["sheet"],
                    "excel_row": case["excel_row"],
                    "error": serialize_error(exc),
                },
            )
    return completed, failed, report_outputs


def _build_report(
    *,
    config: dict[str, Any],
    gateway: Any,
    run_id: str,
    run_dir: Path,
    logger: EventLogger,
    review_outputs: list[str],
) -> tuple[str, Path | None]:
    if not config["report"]["enabled"]:
        return "disabled", None
    if not review_outputs:
        logger.emit(
            "report_failed",
            "failed",
            {
                "error": {
                    "type": "no_completed_reviews",
                    "message": "No completed review output.",
                }
            },
        )
        return "failed", None

    try:
        messages = build_report_messages(review_outputs)
        logger.emit(
            "report_build_requested",
            "started",
            {
                "request": {"messages": messages},
                "included_case_count": len(review_outputs),
                "input_hash": hash_json(messages),
            },
        )
        output, model_metadata = call_gateway(
            gateway, messages, "report_build", {"run_id": run_id}
        )
        report_file = run_dir / "report.md"
        write_text(report_file, output)
        logger.emit(
            "report_saved",
            "success",
            {
                "file": str(report_file),
                "content_hash": hash_text(output),
                "model_metadata": model_metadata,
            },
        )
        return "success", report_file
    except Exception as exc:
        logger.emit(
            "report_failed",
            "failed",
            {"error": serialize_error(exc)},
        )
        return "failed", None


def run_review(config_path: Path, gateway: Any) -> Path:
    config = load_config(config_path)
    validate_templates()
    run_id, run_dir, logger = _create_run(config)
    workbook = None
    stage = "initialization"
    try:
        logger.emit(
            "workflow_started",
            "started",
            _started_payload(config_path, config, run_dir),
        )
        stage = "source_validation"
        validate_source(config)

        stage = "evaluation_prompt_load"
        evaluation_prompt, provenance = load_evaluation_prompt(config)
        evaluation_hash = provenance["used_prompt_hash"]
        logger.emit(
            "evaluation_prompt_loaded",
            "success",
            {
                "file": str(config["evaluation_prompt"]["file"]),
                "content_hash": evaluation_hash,
                "generated_content_hash": provenance.get(
                    "generated_prompt_hash"
                ),
                "manually_modified": provenance["manually_modified"],
                "build_run_id": provenance.get("build_run_id"),
            },
        )

        stage = "workbook_prepare"
        reviewed_file = run_dir / "reviewed_result.xlsx"
        (
            workbook,
            sheet,
            headers,
            status_column,
            output_column,
        ) = open_review_workbook(config, reviewed_file)
        cases = select_cases(config, sheet, headers)

        stage = "case_review"
        completed, failed, report_outputs = _review_cases(
            config=config,
            gateway=gateway,
            run_id=run_id,
            logger=logger,
            evaluation_prompt=evaluation_prompt,
            evaluation_hash=evaluation_hash,
            sheet=sheet,
            cases=cases,
            status_column=status_column,
            output_column=output_column,
        )

        stage = "workbook_save"
        workbook.save(reviewed_file)
        workbook.close()
        workbook = None
        logger.emit(
            "workbook_saved",
            "success",
            {
                "file": str(reviewed_file),
                "file_hash": hash_file(reviewed_file),
                "completed_case_count": completed,
                "failed_case_count": failed,
            },
        )

        stage = "report_build"
        report_status, report_file = _build_report(
            config=config,
            gateway=gateway,
            run_id=run_id,
            run_dir=run_dir,
            logger=logger,
            review_outputs=report_outputs,
        )
        logger.emit(
            "workflow_completed",
            "success",
            {
                "completion_reason": "review_finished",
                "selected_case_count": len(cases),
                "completed_case_count": completed,
                "failed_case_count": failed,
                "reviewed_workbook": str(reviewed_file),
                "evaluation_prompt_hash": evaluation_hash,
                "report": {
                    "enabled": config["report"]["enabled"],
                    "status": report_status,
                    "file": str(report_file) if report_file else None,
                },
            },
        )
        return run_dir
    except Exception as exc:
        logger.emit(
            "workflow_failed",
            "failed",
            {"stage": stage, "error": serialize_error(exc)},
        )
        raise
    finally:
        if workbook is not None:
            workbook.close()
        logger.close()
