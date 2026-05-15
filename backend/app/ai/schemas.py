from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CitationInput(BaseModel):
    label: str | None = None
    quote: str = Field(min_length=1)
    page_number: int | None = None
    start_char: int | None = None
    end_char: int | None = None


class CitationValidationResult(BaseModel):
    citation: CitationInput
    validation_status: Literal["valid", "invalid", "needs_review"]
    similarity_score: float | None = None
    normalized_quote: str | None = None
    message: str | None = None


class ContractMetadataOutput(BaseModel):
    title: str | None = None
    contract_type: str | None = None
    counterparty_name: str | None = None
    jurisdiction: str | None = None
    risk_level: str | None = None
    value_amount: float | None = None
    currency: str | None = Field(default=None, max_length=3)
    effective_date: date | None = None
    expiration_date: date | None = None
    confidence: Literal["high", "medium", "low"] = "low"
    citations: list[CitationInput] = Field(default_factory=list)
    notes: str | None = None


class ClauseOutput(BaseModel):
    clause_type: str
    heading: str | None = None
    text: str = Field(min_length=1)
    start_char: int | None = None
    end_char: int | None = None
    confidence: Literal["high", "medium", "low"] = "medium"
    citations: list[CitationInput] = Field(default_factory=list)


class ClauseExtractionOutput(BaseModel):
    clauses: list[ClauseOutput] = Field(default_factory=list)
    extraction_notes: str | None = None


class ContractDocxSection(BaseModel):
    heading: str
    body: str


class ContractDocxGenerationOutput(BaseModel):
    title: str
    sections: list[ContractDocxSection] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    citations: list[CitationInput] = Field(default_factory=list)


class ContractEditSuggestion(BaseModel):
    edit_type: str
    original_text: str | None = None
    replacement_text: str | None = None
    rationale: str
    risk_level: Literal["low", "medium", "high"] = "medium"
    citations: list[CitationInput] = Field(default_factory=list)


class ContractEditSuggestionsOutput(BaseModel):
    edits: list[ContractEditSuggestion] = Field(default_factory=list)
    summary: str | None = None


class PlaybookDeviationOutput(BaseModel):
    rule_index: int | None = None
    clause_type: str
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    issue: str
    original_text: str | None = None
    suggested_fix: str | None = None
    approval_required: bool = False
    citations: list[CitationInput] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"


class PlaybookReviewOutput(BaseModel):
    deviations: list[PlaybookDeviationOutput] = Field(default_factory=list)
    summary: str | None = None
    citations: list[CitationInput] = Field(default_factory=list)


class AssistantAnswerOutput(BaseModel):
    answer: str
    citations: list[CitationInput] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)


class AISkillRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    skill_name: str
    skill_version: str
    execution_mode: str
    status: str
    resource_type: str | None
    resource_id: str | None
    validation_status: str
    error_message: str | None


class AIPromptVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str | None = None
    prompt_key: str
    version: str
    status: str
    prompt_hash: str
    description: str | None = None
    model_name: str | None = None
    model_config_hash: str | None = None


class SkillInfo(BaseModel):
    name: str
    version: str
    execution_mode: str
    prompt_key: str
    prompt_version: str
    output_schema_name: str
    required_permission: str | None = None
    feature_flag: str | None = None
    enabled_by_default: bool
    requires_citations: bool
    allows_mutation: bool
