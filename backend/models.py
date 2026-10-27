from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, model_validator

Verdict = Literal['pass', 'fail', 'insufficient_evidence', 'not_applicable']
VERDICTS = get_args(Verdict)
Role = Literal['input', 'output', 'reference']


class MappingSave(BaseModel):
    id: str | None = None
    dataset_id: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(min_length=1)
    references: list[str] = Field(default_factory=list)


class Criterion(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    name: str = Field(min_length=1)
    description: str = ''
    enabled: bool = True
    method: Literal['llm', 'non_empty', 'json_schema'] = 'llm'
    required_roles: list[Role] = Field(default_factory=lambda: ['output'])
    target_field: str = ''
    rule_schema: dict[str, Any] = Field(default_factory=lambda: {'type': 'object'})


class SpecSave(BaseModel):
    id: str | None = None
    title: str = Field(min_length=1)
    generation_prompt: str = ''
    design_notes: str = ''
    criteria: list[Criterion] = Field(min_length=1)
    design_traces: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode='after')
    def unique_ids(self):
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError('标准 ID 不可重复。')
        if not any(c.enabled for c in self.criteria):
            raise ValueError('至少启用一条标准。')
        return self


class ModelConfig(BaseModel):
    base_url: str = 'https://api.openai.com/v1'
    model: str = 'mock-judge'
    api_key_env: str = 'OPENAI_API_KEY'
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    parameters: dict[str, Any] = Field(default_factory=dict)
    output_mode: Literal['json_object', 'json_schema', 'text'] = 'json_object'

    @model_validator(mode='after')
    def reserved_parameters(self):
        if set(self.parameters) & {'model', 'messages', 'stream', 'response_format', 'api_key', 'headers'}:
            raise ValueError('参数不能覆盖模型、消息、输出模式或鉴权配置。')
        return self


class Projection(BaseModel):
    criterion_id: str
    verdict_path: str
    reason_path: str = ''
    evidence_path: str = ''


class BuildRequest(BaseModel):
    spec_id: str
    spec_version: int = Field(ge=1)


class EvaluatorSave(BuildRequest):
    id: str | None = None
    prompt: str = Field(min_length=1)
    config: ModelConfig = Field(default_factory=ModelConfig)
    response_schema: dict[str, Any]
    projection: list[Projection] = Field(default_factory=list)
    mock_scenario: Literal['pass', 'fail', 'insufficient_evidence', 'not_applicable', 'invalid_json', 'schema_error', 'request_error'] = 'insufficient_evidence'
    mock_response: str | None = None


class PreviewRequest(BaseModel):
    mapping_id: str
    mapping_version: int = Field(ge=1)
    evaluator_id: str
    evaluator_version: int = Field(ge=1)
    row_id: str


class RunRequest(BaseModel):
    mapping_id: str
    mapping_version: int = Field(ge=1)
    evaluator_id: str
    evaluator_version: int = Field(ge=1)
    selection_mode: Literal['selected', 'all'] = 'selected'
    row_ids: list[str] = Field(default_factory=list)


class ReviewSave(BaseModel):
    criterion_id: str
    verdict: Verdict
    reason: str = Field(min_length=1)
    save_case: bool = False
    confirm_mock: bool = False


class ExtractRequest(BaseModel):
    generation_prompt: str = Field(min_length=1)
    mock_response: str | None = None


class AnalysisRequest(BaseModel):
    mapping_id: str
    mapping_version: int = Field(ge=1)
    spec_id: str
    spec_version: int = Field(ge=1)
    row_id: str
    mock_response: str | None = None


class ExportRequest(BaseModel):
    fields: list[str] | None = None


class RetryRequest(BaseModel):
    evaluator_id: str | None = None
    evaluator_version: int | None = Field(default=None, ge=1)
