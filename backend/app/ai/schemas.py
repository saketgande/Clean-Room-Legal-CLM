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


class ClauseRiskOutput(BaseModel):
    clause_type: str
    risk: Literal["low", "medium", "high"] = "low"
    rationale: str = Field(min_length=1)
    # Required, not optional: an eval scorecard's LLM judge found "low" risk
    # calls shipping with no quote at all, so a lawyer had no way to verify
    # they weren't invented. Every risk call — including "low" — must now
    # point to real supporting text.
    quote: str = Field(min_length=1)


class ContractRiskOutput(BaseModel):
    clause_risks: list[ClauseRiskOutput] = Field(default_factory=list)
    summary: str | None = None


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


class PlaybookGenerationRule(BaseModel):
    clause_type: str
    rule_type: str = "standard_position"
    preferred_position: str | None = None
    fallback_position: str | None = None
    prohibited_language: str | None = None
    required_language: str | None = None
    risk_level: Literal["low", "medium", "high", "critical"] | None = None
    rationale: str | None = None
    sample_clause: str | None = None
    negotiation_guidance: str | None = None
    approval_required: bool = False


class PlaybookGenerationOutput(BaseModel):
    suggested_name: str | None = None
    rules: list[PlaybookGenerationRule] = Field(default_factory=list)
    notes: str | None = None


class PlaybookRecommendation(BaseModel):
    clause_type: str
    rule_id: str | None = None
    change_summary: str
    proposed_preferred_position: str | None = None
    proposed_fallback_position: str | None = None
    proposed_negotiation_guidance: str | None = None
    rationale: str
    # Does the change make the org MORE or LESS protected? Surfaced as a guardrail.
    risk_direction: Literal["more_protected", "less_protected", "neutral"] = "neutral"
    confidence: Literal["high", "medium", "low"] = "medium"


class PlaybookRecommendationsOutput(BaseModel):
    recommendations: list[PlaybookRecommendation] = Field(default_factory=list)
    summary: str | None = None


class PlaybookChatBuildOutput(BaseModel):
    reply: str
    suggested_name: str | None = None
    # The COMPLETE current draft after applying the user's request (replace, not delta).
    rules: list[PlaybookGenerationRule] = Field(default_factory=list)


class ObligationOutput(BaseModel):
    obligation_type: str | None = None
    description: str = Field(min_length=1)
    responsible_party: str | None = None
    due_date: date | None = None
    recurrence: str | None = None
    source_clause_type: str | None = None
    confidence: Literal["high", "medium", "low"] = "medium"
    citations: list[CitationInput] = Field(default_factory=list)


class ObligationExtractionOutput(BaseModel):
    obligations: list[ObligationOutput] = Field(default_factory=list)
    extraction_notes: str | None = None


class RenewalExtractionOutput(BaseModel):
    expiration_date: date | None = None
    auto_renewal: bool = False
    renewal_term: str | None = None
    notice_date: date | None = None
    notice_period_days: int | None = None
    termination_rights_summary: str | None = None
    confidence: Literal["high", "medium", "low"] = "low"
    needs_review: bool = False
    citations: list[CitationInput] = Field(default_factory=list)


class BrainQueryParseOutput(BaseModel):
    query_scope: Literal["contract", "project", "portfolio"] = "portfolio"
    target_clause_types: list[str] = Field(default_factory=list)
    party_filters: list[str] = Field(default_factory=list)
    needs_vector_search: bool = True
    needs_graph_search: bool = True
    needs_full_text_search: bool = True


class BrainAnswerOutput(BaseModel):
    answer: str = Field(min_length=1)
    citations: list[CitationInput] = Field(default_factory=list)
    related_contract_ids: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"
    limitations: str | None = None


class TabularCellOutput(BaseModel):
    answer: str = ""
    reasoning: str | None = None
    not_found: bool = False
    confidence: Literal["high", "medium", "low"] = "low"
    citations: list[CitationInput] = Field(default_factory=list)


class TabularChatOutput(BaseModel):
    answer: str = Field(min_length=1)
    citations: list[CitationInput] = Field(default_factory=list)


class PrivacyIncidentAssessmentOutput(BaseModel):
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    notification_required: bool = False
    # Hours from discovery, e.g. 72 for a DPDP-style breach clock — null when no
    # statutory notification clock applies.
    notification_deadline_hours: int | None = None
    affected_data_categories: list[str] = Field(default_factory=list)
    estimated_affected_count: str | None = None
    recommended_immediate_actions: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    # Numeric, not high/medium/low — this feeds the flow engine's
    # escalate_below_confidence gate directly, unlike this file's other skills.
    # No default: this is the one field the whole gate depends on, so a model
    # response that omits it must fail loudly (a validation error), not silently
    # pass as an unearned "medium confidence" verdict.
    confidence: float = Field(ge=0.0, le=1.0)


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
