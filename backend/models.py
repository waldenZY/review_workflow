from typing import Any, Literal, get_args
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

Verdict = Literal['pass', 'fail', 'insufficient_evidence', 'not_applicable']
VERDICTS = get_args(Verdict)


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ProjectSave(Input):
    name: str = Field(min_length=1, max_length=120)
    description: str = ''


class ProjectDelete(Input):
    confirm_name: str


class ObjectDelete(Input):
    confirm_id: str


class MappingSave(Input):
    id: str | None = None
    dataset_id: str
    evaluation_fields: list[str] = Field(min_length=1)


class Criterion(Input):
    id: str = Field(default_factory=lambda: 'criterion_' + uuid4().hex, min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    name: str = Field(min_length=1)
    description: str = ''
    enabled: bool = True
    method: Literal['llm', 'non_empty', 'json_schema'] = 'llm'
    target_field: str = ''
    rule_schema: dict[str, Any] = Field(default_factory=lambda: {'type': 'object'})


class SpecSave(Input):
    id: str | None = None
    title: str = Field(min_length=1)
    generation_prompt: str = ''
    design_notes: str = ''
    criteria: list[Criterion] = Field(min_length=1)
    design_call_ids: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def check_criteria(self):
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError('标准 ID 不可重复。')
        names = [c.name for c in self.criteria]
        if len(names) != len(set(names)):
            raise ValueError('同一方案内的标准名称不可重复。')
        if not any(c.enabled for c in self.criteria):
            raise ValueError('至少启用一条标准。')
        return self


class Projection(Input):
    criterion_id: str
    verdict_path: str
    reason_path: str = ''
    evidence_path: str = ''


class BuildRequest(Input):
    spec_id: str
    spec_version: int = Field(ge=1)


class EvaluatorSave(BuildRequest):
    id: str | None = None
    title: str = Field(min_length=1)
    system_template: str = Field(min_length=1)
    user_template: str = Field(min_length=1)
    response_schema: dict[str, Any]
    projection: list[Projection] = Field(default_factory=list)
    output_example: dict[str, Any] | None = None


class EvaluatorBuildRequest(BuildRequest):
    source: EvaluatorSave | None = None


class ContextRequest(Input):
    mapping_id: str
    mapping_version: int = Field(ge=1)
    evaluator_id: str
    evaluator_version: int = Field(ge=1)


class PreviewRequest(ContextRequest):
    row_id: str


class DraftPreviewRequest(Input):
    dataset_id: str
    row_id: str
    evaluation_fields: list[str] = Field(min_length=1)
    spec: SpecSave
    evaluator: EvaluatorSave


class RunRequest(ContextRequest):
    selection_mode: Literal['selected', 'all'] = 'selected'
    row_ids: list[str] = Field(default_factory=list)


class RerunRequest(ContextRequest):
    row_ids: list[str] = Field(min_length=1)


class ExtractRequest(Input):
    generation_prompt: str = Field(min_length=1)


class AnalysisRequest(Input):
    mapping_id: str
    mapping_version: int = Field(ge=1)
    spec_id: str
    spec_version: int = Field(ge=1)
    row_id: str


class ExportRequest(Input):
    format: Literal['xlsx', 'jsonl'] = 'xlsx'
    original_fields: list[str] | None = None
    include_raw_response: bool = False


class ConnectionInput(Input):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)
    api_url: str
    api_key: str = Field(default='', repr=False)
    model: str
    max_tokens: int
    timeout: float
    stream: bool
    enable_thinking: bool
    skip_special_tokens: bool


class ExecutionSettings(Input):
    batch_concurrency: int = Field(default=1, ge=1, le=32, strict=True)


class ProfileSave(ConnectionInput):
    name: str = Field(min_length=1, max_length=120)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)


class ConnectionTest(ConnectionInput):
    profile_id: str | None = None
