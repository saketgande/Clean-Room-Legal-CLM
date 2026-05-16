from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PlaybookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class PlaybookUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class PlaybookGenerate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    contract_type: str | None = Field(default=None, max_length=160)
    focus_areas: list[str] = Field(default_factory=list, max_length=12)


class PlaybookResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    status: str
    current_version_id: str | None


class PlaybookVersionCreate(BaseModel):
    source_version_id: str | None = None
    summary: str | None = None


class PlaybookVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    playbook_id: str
    version_number: int
    status: str
    summary: str | None
    source_metadata: dict | None


class PlaybookRuleCreate(BaseModel):
    clause_type: str = Field(min_length=1, max_length=160)
    rule_type: str = Field(min_length=1, max_length=120)
    preferred_position: str | None = None
    fallback_position: str | None = None
    prohibited_language: str | None = None
    required_language: str | None = None
    risk_level: Literal["low", "medium", "high", "critical"] = "medium"
    rationale: str | None = None
    approval_required: bool = False
    escalation_role: str | None = Field(default=None, max_length=120)
    sample_clause: str | None = None
    negotiation_guidance: str | None = None


class PlaybookRuleUpdate(BaseModel):
    clause_type: str | None = Field(default=None, min_length=1, max_length=160)
    rule_type: str | None = Field(default=None, min_length=1, max_length=120)
    preferred_position: str | None = None
    fallback_position: str | None = None
    prohibited_language: str | None = None
    required_language: str | None = None
    risk_level: Literal["low", "medium", "high", "critical"] | None = None
    rationale: str | None = None
    approval_required: bool | None = None
    escalation_role: str | None = Field(default=None, max_length=120)
    sample_clause: str | None = None
    negotiation_guidance: str | None = None


class PlaybookRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    playbook_version_id: str
    clause_type: str
    rule_type: str
    preferred_position: str | None
    fallback_position: str | None
    prohibited_language: str | None
    required_language: str | None
    risk_level: str
    rationale: str | None
    approval_required: bool
    escalation_role: str | None
    sample_clause: str | None
    negotiation_guidance: str | None


class PlaybookPublishRequest(BaseModel):
    version_id: str | None = None


class PlaybookRunCreate(BaseModel):
    contract_id: str
    playbook_version_id: str | None = None
    create_redline: bool = True
    test_mode: bool = False
    use_ai: bool = True


class PlaybookRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    playbook_id: str
    playbook_version_id: str
    contract_id: str
    contract_version_id: str
    status: str
    validation_status: str | None
    model_name: str | None
    error_message: str | None
    validated_output: dict | None


class PlaybookDeviationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    playbook_run_id: str
    playbook_rule_id: str | None
    contract_id: str
    severity: str
    clause_type: str
    issue: str
    suggested_fix: str | None
    citation: dict | None
    status: str


class PlaybookRunDetailResponse(PlaybookRunResponse):
    deviations: list[PlaybookDeviationResponse] = Field(default_factory=list)


class PlaybookDecisionCreate(BaseModel):
    decision: Literal[
        "accepted",
        "accepted_fallback",
        "rejected",
        "waived",
        "escalated",
    ]
    rationale: str | None = Field(default=None, max_length=4000)


class PlaybookDecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    playbook_deviation_id: str
    decision: str
    rationale: str | None
    decided_by_user_id: str
    created_at: datetime
