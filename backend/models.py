from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

TemplateText = Annotated[str, StringConstraints(strip_whitespace=False)]

class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ProjectSave(Input):
    name: str = Field(min_length=1, max_length=120)
    description: str = ''


class ProjectDelete(Input):
    confirm_name: str


class ObjectDelete(Input):
    confirm_id: str


class CloneRunRequest(Input):
    row_ids: list[str] | None = Field(default=None, min_length=1)


class MappingSave(Input):
    id: str | None = None
    title: str = Field(default='字段方案', min_length=1, max_length=120)
    dataset_id: str
    evaluation_fields: list[str] = Field(min_length=1)


class StandardSave(Input):
    id: str | None = None
    name: str = Field(min_length=1, max_length=120)
    description: str = ''
    method: Literal['llm', 'non_empty', 'json_schema'] = 'llm'
    output_template: TemplateText = ''
    target_field: str = ''
    rule_schema: dict[str, Any] = Field(default_factory=lambda: {'type': 'object'})


class CompileOutputTemplate(Input):
    template: TemplateText


class StandardRef(Input):
    standard_id: str
    version: int = Field(ge=1)


class PlanGroup(Input):
    id: str = Field(default_factory=lambda: 'group_' + uuid4().hex, min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    name: str = Field(min_length=1, max_length=120)
    standards: list[StandardRef] = Field(min_length=1)


class PlanSave(Input):
    id: str | None = None
    title: str = Field(min_length=1, max_length=120)
    system_template: TemplateText = Field(min_length=1)
    user_template: TemplateText = Field(min_length=1)
    groups: list[PlanGroup] = Field(min_length=1)

    @model_validator(mode='after')
    def unique(self):
        for values, label in (([g.id for g in self.groups], '组 ID'), ([g.name for g in self.groups], '组名'),
                              ([r.standard_id for g in self.groups for r in g.standards], '标准')):
            if len(values) != len(set(values)):
                raise ValueError(label + '不可重复。')
        return self


class GroupInputBinding(Input):
    group_id: str
    evaluation_fields: list[str] | None = Field(default=None, min_length=1)
    mapping_id: str | None = None
    mapping_version: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def check_input_source(self):
        if self.evaluation_fields is not None:
            if self.mapping_id is None and self.mapping_version is None:
                if len(self.evaluation_fields) == len(set(self.evaluation_fields)):
                    return self
                raise ValueError('评测字段不可重复。')
        elif self.mapping_id and self.mapping_version is not None:
            return self
        raise ValueError('请直接选择评测字段，或提供完整的旧字段方案引用。')


class RunRequest(Input):
    dataset_id: str
    dataset_version: int = Field(ge=1)
    plan_id: str
    plan_version: int = Field(ge=1)
    bindings: list[GroupInputBinding] = Field(min_length=1)
    selection_mode: Literal['selected', 'all'] = 'selected'
    row_ids: list[str] = Field(default_factory=list)
    mode: Literal['trial', 'batch'] = 'batch'


class PlanPreview(Input):
    plan: PlanSave
    dataset_id: str
    dataset_version: int = Field(ge=1)
    bindings: list[GroupInputBinding] = Field(min_length=1)
    row_id: str


class StandardTest(Input):
    standard: StandardSave
    saved_version: int | None = Field(default=None, ge=1)
    source: Literal['dataset', 'json']
    dataset_id: str | None = None
    dataset_version: int | None = Field(default=None, ge=1)
    mapping_id: str | None = None
    mapping_version: int | None = Field(default=None, ge=1)
    row_id: str | None = None
    json_text: TemplateText = ''
    evaluation_fields: list[str] | None = None


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
