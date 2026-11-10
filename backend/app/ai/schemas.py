import json
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    risk_level: Literal["low", "medium", "high"] | None = None
    value_amount: float | None = None
    currency: str | None = Field(default=None, max_length=3)
    effective_date: date | None = None
    expiration_date: date | None = None
    confidence: Literal["high", "medium", "low"] = "low"
    citations: list[CitationInput] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("citations", mode="after")
    @classmethod
    def _few_citations(cls, value: list[CitationInput]) -> list[CitationInput]:
        # Nine fields need at most nine proofs; an unbounded list is how a long answer ran out of tokens.
        return value[:10]

    @field_validator("risk_level", mode="before")
    @classmethod
    def _known_risk_level(cls, value: Any) -> str | None:
        # A free-text level such as "Moderate to High" matches no rule, so drop it.
        text = str(value).strip().lower() if value is not None else ""
        return text if text in {"low", "medium", "high"} else None


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


class ClauseLabel(BaseModel):
    """One segment of the Documents reader's segmentation, named. The text and
    offsets come from the segment, never from the model (ai/clause_segments.py)."""
    segment_id: str = Field(min_length=1)
    clause_type: str = Field(min_length=1)
    confidence: Literal["high", "medium", "low"] = "medium"


class ClauseLabelingOutput(BaseModel):
    labels: list[ClauseLabel] = Field(default_factory=list)
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


def _model_must_answer_position(schema: dict[str, Any]) -> None:
    """Mark ``position`` required in the schema the model is given, while validation still accepts a
    payload without it. A field with a default is optional in the schema, and a model asked for a tool
    call leaves optional fields out; a question it may skip is a question it skips."""
    required = schema.setdefault("required", [])
    if "position" not in required:
        required.append("position")


_CLOSES_STRING = re.compile(r'\s*(?:[:}\]]|,\s*["{\[]|$)')


def _escape_stray_quotes(text: str) -> str:
    """Escape a double quote inside a JSON string that does not close it. The reviewer copies contract
    wording into the text unescaped, so ``("MSMED Act"), it shall`` ends the string early and the whole
    list was lost. A quote closes a string only when JSON structure follows it. ponytail: a defined term
    followed by ``, "`` or ``:`` in the contract text still reads as a close; the parse then fails as before."""
    out: list[str] = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and in_string:
            out.append(text[i:i + 2])
            i += 2
            continue
        if ch == '"':
            if not in_string:
                in_string = True
            elif _CLOSES_STRING.match(text, i + 1):
                in_string = False
            else:
                out.append("\\")
        out.append(ch)
        i += 1
    return "".join(out)


def _list_from_json_text(value: Any) -> Any:
    """The reviewer sometimes sends a list field as JSON text (``"[{\"quote\": ...}]"``), and a clean
    review then failed validation as 'ai_unavailable'. Read the text when it holds a list, repairing
    unescaped quotes from contract wording once; anything else goes on unchanged so it fails exactly
    as before."""
    if isinstance(value, str):
        for candidate in (value, None):
            try:
                parsed = json.loads(candidate if candidate is not None else _escape_stray_quotes(value))
            except ValueError:
                continue
            if isinstance(parsed, list):
                return parsed
    return value


class PlaybookDeviationOutput(BaseModel):
    model_config = ConfigDict(json_schema_extra=_model_must_answer_position)

    rule_index: int | None = None
    clause_type: str
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    issue: str
    # Asked for after `issue`, once the model has worked the item through. The reviewer sometimes
    # lists a clause and, writing the issue, finds it meets the rule ("no deviation to flag"; "this
    # is an approval-tracking item"). Answering 'preferred' discards the item (``kept_deviations``)
    # instead of turning it into a risk, a HIGH one when the rule needs approval. The default keeps
    # every older payload as it was.
    position: Literal["preferred", "fallback", "outside"] = Field(
        default="outside",
        description=(
            "Where the contract's wording sits against this rule. 'preferred': it meets the rule's "
            "preferred position, or is better for the org (then this is NOT a deviation and the item "
            "is discarded; an approval requirement on the rule does not change that). 'fallback': it "
            "sits at the rule's fallback position. 'outside': it is worse than the fallback, contains "
            "prohibited language, or lacks required wording."
        ),
    )
    original_text: str | None = None
    suggested_fix: str | None = None
    approval_required: bool = False
    citations: list[CitationInput] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"

    _citations_from_text = field_validator("citations", mode="before")(_list_from_json_text)


class PlaybookReviewOutput(BaseModel):
    deviations: list[PlaybookDeviationOutput] = Field(default_factory=list)
    summary: str | None = None
    citations: list[CitationInput] = Field(default_factory=list)

    _lists_from_text = field_validator("deviations", "citations", mode="before")(_list_from_json_text)


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


PeriodUnit = Literal["days", "business_days", "weeks", "months", "years"]
# Ten years in days; a bigger number is a misread, and it overflows date arithmetic
# long before it means anything. The tool schema also shows the model this bound.
MAX_PERIOD = 3650


class ObligationTiming(BaseModel):
    """When an obligation falls due, as the contract WORDS it. The model never
    returns a date: code counts the period from the anchor (app/core/deadlines.py)
    and shows the formula, so a wrong anchor or unit is visible, not baked in."""

    kind: Literal["deadline", "ongoing", "condition"] = "condition"
    period_number: int | None = Field(default=None, ge=0, le=MAX_PERIOD)
    period_unit: PeriodUnit | None = None
    direction: Literal["after", "before"] = "after"
    anchor: Literal[
        "effective_date", "expiration_date", "signature_date", "termination",
        "notice_received", "invoice_received", "request_received", "disclosure", "other",
    ] | None = None
    anchor_text: str | None = None
    # False when the period would come from law rather than this contract: such a
    # deadline is left for a person, never computed.
    stated_in_text: bool = False
    clear_days: bool = False
    # A calendar date the clause writes out as the deadline. Used only when its day
    # number and year are in the citation quote (core/deadlines.date_in_quote).
    explicit_date: date | None = None

    # One off-list word from the model ("recurring", "hours") must not discard a whole
    # contract's obligations. A recurring duty has no single deadline, so it reads as
    # ongoing; a unit we cannot count leaves the deadline for a person ("enter the date").
    @field_validator("kind", mode="before")
    @classmethod
    def _known_kind(cls, v):
        if v in ("deadline", "ongoing", "condition"):
            return v
        return "ongoing" if v in ("recurring", "continuous", "continuing") else "condition"

    @field_validator("period_unit", mode="before")
    @classmethod
    def _known_unit(cls, v):
        return v if v in ("days", "business_days", "weeks", "months", "years") else None


class ObligationOutput(BaseModel):
    # Bounded to the stored columns (obligation_type 160, responsible_party 255). An overlong value used to
    # fail the whole save at the INSERT; now the tool schema tells the model the limit and a breach fails
    # validation, which the job reports as an unusable answer.
    obligation_type: str | None = Field(default=None, max_length=160)
    description: str = Field(min_length=1)
    responsible_party: str | None = Field(default=None, max_length=255)
    timing: ObligationTiming = Field(default_factory=ObligationTiming)
    recurrence: Literal[
        "none", "monthly", "quarterly", "semi_annually", "annually", "per_event"
    ] = "none"
    recurrence_note: str | None = None
    source_clause_type: str | None = None
    confidence: Literal["high", "medium", "low"] = "medium"
    citations: list[CitationInput] = Field(default_factory=list)


class ObligationExtractionOutput(BaseModel):
    obligations: list[ObligationOutput] = Field(default_factory=list)
    extraction_notes: str | None = None


class TermLength(BaseModel):
    number: int = Field(ge=0, le=MAX_PERIOD)
    unit: PeriodUnit


class InitialTerm(TermLength):
    counted_from: Literal["effective_date", "signature_date", "other"] = "effective_date"


class RenewalNoticeTerm(BaseModel):
    """One notice clause. A contract can have several (either party 90 days, the
    customer 60 days); code tracks the earliest deadline among them."""

    kind: Literal["non_renewal", "termination_for_convenience"]
    who: Literal["either", "us", "counterparty", "named"] = "either"
    number: int | None = Field(default=None, ge=0, le=MAX_PERIOD)
    unit: PeriodUnit | None = None
    clear_days: bool = False
    counted: Literal["before_end_of_term", "other"] = "before_end_of_term"
    quote: str | None = None


class RenewalExtractionOutput(BaseModel):
    """The term and renewal WORDING as typed facts. No AI-computed dates: the
    expiry, notice deadline and window are counted by code from these facts."""

    term_kind: Literal["fixed", "evergreen", "unclear"] = "unclear"
    # Only a date written out in the text, never one the model worked out.
    stated_end_date: date | None = None
    stated_end_date_quote: str | None = None
    initial_term: InitialTerm | None = None
    auto_renews: bool | None = None
    renewal_term: TermLength | None = None
    max_renewals: int | None = Field(default=None, ge=0, le=100)
    notice_terms: list[RenewalNoticeTerm] = Field(default_factory=list)
    termination_rights_summary: str | None = None
    confidence: Literal["high", "medium", "low"] = "low"
    needs_review: bool = False
    citations: list[CitationInput] = Field(default_factory=list)


class BrainAnswerOutput(BaseModel):
    answer: str = Field(min_length=1)
    citations: list[CitationInput] = Field(default_factory=list)
    related_contract_ids: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"
    limitations: str | None = None


class DateRange(BaseModel):
    """A date condition as the user said it. Code turns it into from/to; the model never counts days."""

    # Descriptions are what the chat model sees for the query_contracts tool.
    from_date: date | None = Field(default=None, description="On or after this date, as the user wrote it.")
    to_date: date | None = Field(default=None, description="On or before this date, as the user wrote it.")
    year: int | None = Field(default=None, description="A calendar year: 'in 2027'.")
    within_next_days: int | None = Field(default=None, ge=1, le=3650, description="'in the next 90 days'.")
    within_next_months: int | None = Field(default=None, ge=1, le=120, description="'in the next 6 months'.")
    within_last_days: int | None = Field(default=None, ge=1, le=3650, description="'in the last 30 days'.")
    period: Literal["this_month", "next_month", "this_quarter", "next_quarter", "this_year", "next_year"] | None = (
        Field(default=None, description="A calendar period around today: 'this quarter', 'next month'.")
    )


class PortfolioFilter(BaseModel):
    """What a "which / how many contracts" question asks. Every condition must hold (AND)."""

    # As the user said it ("NDA", "statement of work"); matched by the phrases that name each
    # kind (contract_brain/portfolio.py _KINDS).
    contract_types: list[str] = Field(default_factory=list)
    counterparty: str | None = None
    governing_law: str | None = None  # a place: "England and Wales", "Singapore", "Delaware"
    stages: list[Literal["intake", "drafting", "review", "approval", "signature", "active", "closed"]] = Field(
        default_factory=list
    )
    risk_bands: list[Literal["low", "medium", "high", "unknown"]] = Field(default_factory=list)
    effective: DateRange | None = None
    expires: DateRange | None = None
    value_min: float | None = Field(default=None, ge=0)
    value_max: float | None = Field(default=None, ge=0)
    currency: str | None = None  # ISO-4217; a value bound needs one ("1 crore" = 10000000 INR)
    wording: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Yes/no questions about ONE contract's text that no field answers, YES being what the "
        "user wants: 'Does this contract renew automatically?'. The AI reads each candidate contract.",
    )
    lacks: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="What a contract must NOT have, asked as the presence question: 'no limitation of "
        "liability clause' is 'Does this contract have a limitation of liability clause?'. A contract "
        "matches when the AI's reading says no.",
    )
    not_checkable: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Parts of the question no field or question can check (anything about today, e.g. "
        "'notice deadline this quarter'), in the user's words. Shown as not checked, never dropped.",
    )
    count_only: bool = False  # "how many" (the list is still returned)


class PortfolioFilterOutput(PortfolioFilter):
    # False when the question is not "which / how many / list contracts ..." (for example
    # "what is our usual liability cap?"): such a question goes to search instead.
    is_list_question: bool


class TabularCellOutput(BaseModel):
    answer: str = ""
    reasoning: str | None = None
    not_found: bool = False
    # Set only for a column that asks a yes/no question. Code decides from this, never
    # from the answer's prose (portfolio filter: contract_brain/portfolio.py).
    verdict: Literal["yes", "no", "not_stated"] | None = None
    confidence: Literal["high", "medium", "low"] = "low"
    citations: list[CitationInput] = Field(default_factory=list)


class TabularRowAnswer(TabularCellOutput):
    column_id: str


class TabularRowOutput(BaseModel):
    answers: list[TabularRowAnswer] = Field(default_factory=list)


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


_CONFIDENCE_LEVELS = {"high": 0.9, "medium": 0.6, "low": 0.3}


def confidence_score(value: Any) -> float | None:
    """The one place model confidence becomes a number. Most skills answer
    high/medium/low, a few agents give a 0-1 float, and workflow gates compare
    against a 0-1 threshold. Missing stays None; anything unreadable (such as
    "Moderate to High") counts as 0, so it can never clear a threshold."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _CONFIDENCE_LEVELS:
            return _CONFIDENCE_LEVELS[text]
        try:
            value = float(text)
        except ValueError:
            return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return min(max(float(value), 0.0), 1.0)
