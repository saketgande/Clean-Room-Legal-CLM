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
    title: str | None = Field(default=None, description="The contract's title as printed; null if none is stated.")
    contract_type: str | None = Field(default=None, description="What kind of agreement it is (e.g. NDA, MSA), as stated.")
    counterparty_name: str | None = Field(default=None, description="The other party's full legal name exactly as stated.")
    jurisdiction: str | None = Field(default=None, description="Governing-law jurisdiction, standardized name; null if not stated.")
    risk_level: Literal["low", "medium", "high"] | None = Field(default=None, description="Overall risk only if the text makes it plain; otherwise null.")
    value_amount: float | None = Field(default=None, description="Total or annual contract value as a plain number, no currency symbol.")
    currency: str | None = Field(default=None, max_length=3, description="ISO 4217 code of value_amount, e.g. USD.")
    effective_date: date | None = Field(default=None, description="Effective date, YYYY-MM-DD; null if not explicitly stated.")
    expiration_date: date | None = Field(default=None, description="Expiration or end date, YYYY-MM-DD; null if not explicitly stated.")
    confidence: Literal["high", "medium", "low"] = Field(default="low", description="high: explicit language and clear citations; low: sparse or conflicting.")
    citations: list[CitationInput] = Field(default_factory=list, description="At most one per filled field (8 at most): the shortest sentence that proves the value.")
    notes: str | None = Field(default=None, description="Two sentences at most: why a field is null, conflicts, or a truncated document.")

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
    clause_type: str = Field(description="Type of the clause, e.g. limitation_of_liability, governing_law.")
    heading: str | None = Field(default=None, description="The clause's heading as printed, if it has one.")
    text: str = Field(min_length=1, description="The clause's exact wording from the contract.")
    start_char: int | None = Field(default=None, description="Offset where the text starts in the contract text.")
    end_char: int | None = Field(default=None, description="Offset where the text ends in the contract text.")
    confidence: Literal["high", "medium", "low"] = Field(default="medium", description="How sure the type and boundaries are.")
    citations: list[CitationInput] = Field(default_factory=list, description="Quotes proving the clause is what you say.")


class ClauseExtractionOutput(BaseModel):
    clauses: list[ClauseOutput] = Field(default_factory=list, description="Only the materially significant clauses found.")
    extraction_notes: str | None = Field(default=None, description="Anything unusual, or a sign the text is partial.")


class ClauseLabel(BaseModel):
    """One segment of the Documents reader's segmentation, named. The text and
    offsets come from the segment, never from the model (ai/clause_segments.py)."""
    segment_id: str = Field(min_length=1, description="An id from the supplied list (S1, S2, ...); never invent one.")
    clause_type: str = Field(min_length=1, description="The clause's type; a short snake_case name if none of the usual ones fits.")
    confidence: Literal["high", "medium", "low"] = Field(default="medium", description="high: type is plain from the excerpt; low: guessed from the heading alone.")


class ClauseLabelingOutput(BaseModel):
    labels: list[ClauseLabel] = Field(default_factory=list, description="One label per significant segment, each segment at most once.")
    extraction_notes: str | None = Field(default=None, description="Anything unusual, at most three short sentences.")


class ContractDocxSection(BaseModel):
    heading: str = Field(description="Section heading, in execution-ready order.")
    body: str = Field(description="Substantive contract wording for the section.")


class ContractDocxGenerationOutput(BaseModel):
    title: str = Field(description="Title of the document.")
    sections: list[ContractDocxSection] = Field(default_factory=list, description="Every section in order, including schedules and signature blocks.")
    assumptions: list[str] = Field(default_factory=list, description="Each real drafting judgment call or placeholder for the drafter; shown as a note, never printed in the contract.")
    citations: list[CitationInput] = Field(default_factory=list, description="Quotes from source material the draft relies on.")


class ContractEditSuggestion(BaseModel):
    edit_type: str = Field(description="Kind of change, e.g. replace, insert, delete.")
    original_text: str | None = Field(default=None, description="Text copied character-for-character from the contract that the edit changes; null for a pure insertion.")
    replacement_text: str | None = Field(default=None, description="Final contract wording, never a blank or placeholder; empty for a deletion.")
    rationale: str = Field(description="Why the change is made.")
    risk_level: Literal["low", "medium", "high"] = Field(default="medium", description="Risk the edit addresses or introduces.")
    citations: list[CitationInput] = Field(default_factory=list, description="Verbatim contract quotes supporting the edit.")


class ContractEditSuggestionsOutput(BaseModel):
    edits: list[ContractEditSuggestion] = Field(default_factory=list, description="Each proposed change; leave unrelated language alone.")
    summary: str | None = Field(default=None, description="What was changed, plus anything unresolved or assumed.")


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

    rule_index: int | None = Field(default=None, description="The rule_index exactly as supplied.")
    clause_type: str = Field(description="Clause type the rule covers.")
    severity: Literal["low", "medium", "high", "critical"] = Field(default="medium", description="How serious the deviation is for the organization.")
    issue: str = Field(description="One plain sentence: the business impact and whether senior-counsel or approval escalation is needed.")
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
    original_text: str | None = Field(default=None, description="The narrowest span copied character-for-character from the contract that captures the issue.")
    suggested_fix: str | None = Field(default=None, description="Replacement or insertion wording ready to sit in the contract, not negotiation advice; empty if deletion is the fix.")
    approval_required: bool = Field(default=False, description="True if a real deviation from this rule needs sign-off.")
    citations: list[CitationInput] = Field(default_factory=list, description="The contract quote proving the deviation.")
    confidence: Literal["high", "medium", "low"] = Field(default="medium", description="How sure the deviation is real.")

    _citations_from_text = field_validator("citations", mode="before")(_list_from_json_text)


class PlaybookReviewOutput(BaseModel):
    deviations: list[PlaybookDeviationOutput] = Field(default_factory=list, description="Material deviations only; empty if none.")
    summary: str | None = Field(default=None, description="Short overall assessment of the contract against the playbook.")
    citations: list[CitationInput] = Field(default_factory=list, description="Quotes supporting the summary.")

    _lists_from_text = field_validator("deviations", "citations", mode="before")(_list_from_json_text)


class PlaybookGenerationRule(BaseModel):
    clause_type: str = Field(description="Lowercase snake_case clause type.")
    rule_type: str = Field(default="standard_position", description="Kind of rule, e.g. standard_position, fallback_position, prohibited_position, required_protection.")
    preferred_position: str | None = Field(default=None, description="The position we want; null if the source does not establish one.")
    fallback_position: str | None = Field(default=None, description="What we accept instead; meaningfully distinct from the preferred position.")
    prohibited_language: str | None = Field(default=None, description="Wording that must not appear, concrete enough to test in a review.")
    required_language: str | None = Field(default=None, description="Wording that must appear, concrete enough to test in a review.")
    risk_level: Literal["low", "medium", "high", "critical"] | None = Field(default=None, description="Risk if the position is lost; null if it rests only on assumed market practice.")
    rationale: str | None = Field(default=None, description="Why, grounded in the source.")
    sample_clause: str | None = Field(default=None, description="Model wording for the clause, kept separate from source wording.")
    negotiation_guidance: str | None = Field(default=None, description="How to negotiate it.")
    approval_required: bool = Field(default=False, description="True only if the source states an approval requirement.")


class PlaybookGenerationOutput(BaseModel):
    suggested_name: str | None = Field(default=None, description="Concise playbook name; neutral if the role or contract type is uncertain.")
    rules: list[PlaybookGenerationRule] = Field(default_factory=list, description="Source-supported, materially significant rules; empty if none can be established.")
    notes: str | None = Field(default=None, description="Conflicts, gaps or limits in the source.")


class PlaybookRecommendation(BaseModel):
    clause_type: str = Field(description="Clause type of the rule to change.")
    rule_id: str | None = Field(default=None, description="The rule's id as supplied.")
    change_summary: str = Field(description="What should change, in one sentence.")
    proposed_preferred_position: str | None = Field(default=None, description="New preferred position, if it changes.")
    proposed_fallback_position: str | None = Field(default=None, description="New fallback position, if it changes.")
    proposed_negotiation_guidance: str | None = Field(default=None, description="New negotiation guidance, if it changes.")
    rationale: str = Field(description="Why, from how past deviations were decided.")
    # Does the change make the org MORE or LESS protected? Surfaced as a guardrail.
    risk_direction: Literal["more_protected", "less_protected", "neutral"] = Field(default="neutral", description="Whether the change leaves the organization more or less protected.")
    confidence: Literal["high", "medium", "low"] = Field(default="medium", description="How well the evidence supports the change.")


class PlaybookRecommendationsOutput(BaseModel):
    recommendations: list[PlaybookRecommendation] = Field(default_factory=list, description="A small number of well-evidenced recommendations; never presented as already adopted.")
    summary: str | None = Field(default=None, description="Short overview of what the evidence shows.")


class PlaybookChatBuildOutput(BaseModel):
    reply: str = Field(description="1-3 sentences saying what changed or answering the question.")
    suggested_name: str | None = Field(default=None, description="Concise playbook name.")
    # The COMPLETE current draft after applying the user's request (replace, not delta).
    rules: list[PlaybookGenerationRule] = Field(default_factory=list, description="The COMPLETE rule set after applying the request, never a delta.")


PeriodUnit = Literal["days", "business_days", "weeks", "months", "years"]
# Ten years in days; a bigger number is a misread, and it overflows date arithmetic
# long before it means anything. The tool schema also shows the model this bound.
MAX_PERIOD = 3650


class ObligationTiming(BaseModel):
    """When an obligation falls due, as the contract WORDS it. The model never
    returns a date: code counts the period from the anchor (app/core/deadlines.py)
    and shows the formula, so a wrong anchor or unit is visible, not baked in."""

    kind: Literal["deadline", "ongoing", "condition"] = Field(default="condition", description="deadline: a stated time limit; ongoing: a continuing or recurring duty; condition: depends on an event.")
    period_number: int | None = Field(default=None, ge=0, le=MAX_PERIOD, description="The number in the stated period, e.g. 30 for a 30-day limit.")
    period_unit: PeriodUnit | None = Field(default=None, description="Unit of the stated period; keep business days distinct from days.")
    direction: Literal["after", "before"] = Field(default="after", description="Whether the period runs after or before the anchor.")
    anchor: Literal[
        "effective_date", "expiration_date", "signature_date", "termination",
        "notice_received", "invoice_received", "request_received", "disclosure", "other",
    ] | None = None
    anchor_text: str | None = Field(default=None, description="The contract's own words for what the period counts from.")
    # False when the period would come from law rather than this contract: such a
    # deadline is left for a person, never computed.
    stated_in_text: bool = Field(default=False, description="True only if this contract itself states the period (not law).")
    clear_days: bool = Field(default=False, description="True if the contract says clear days (excluding both end days).")
    # A calendar date the clause writes out as the deadline. Used only when its day
    # number and year are in the citation quote (core/deadlines.date_in_quote).
    explicit_date: date | None = Field(default=None, description="A calendar date the clause writes out as the deadline, YYYY-MM-DD; never one you worked out.")

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
    obligation_type: str | None = Field(default=None, max_length=160, description="Short kind of duty, e.g. payment, notice, reporting.")
    description: str = Field(min_length=1, description="The duty in concise plain language.")
    responsible_party: str | None = Field(default=None, max_length=255, description="Who must do it, exactly as the contract names them; null if no clear owner.")
    timing: ObligationTiming = Field(default_factory=ObligationTiming, description="When it falls due, as the contract words it.")
    recurrence: Literal[
        "none", "monthly", "quarterly", "semi_annually", "annually", "per_event"
    ] = "none"
    recurrence_note: str | None = Field(default=None, description="How the repeat works, in the contract's words.")
    source_clause_type: str | None = Field(default=None, description="Type of the clause the duty comes from.")
    confidence: Literal["high", "medium", "low"] = Field(default="medium", description="How clearly the text imposes this duty.")
    citations: list[CitationInput] = Field(default_factory=list, description="The exact quotation that proves the duty.")


class ObligationExtractionOutput(BaseModel):
    obligations: list[ObligationOutput] = Field(default_factory=list, description="Every concrete duty the text imposes; empty if none.")
    extraction_notes: str | None = Field(default=None, description="Why the list is empty, or a sign the text is partial.")


class TermLength(BaseModel):
    number: int = Field(ge=0, le=MAX_PERIOD, description="Length of the period.")
    unit: PeriodUnit = Field(description="Unit of the period.")


class InitialTerm(TermLength):
    counted_from: Literal["effective_date", "signature_date", "other"] = Field(default="effective_date", description="What the initial term counts from.")


class RenewalNoticeTerm(BaseModel):
    """One notice clause. A contract can have several (either party 90 days, the
    customer 60 days); code tracks the earliest deadline among them."""

    kind: Literal["non_renewal", "termination_for_convenience"] = Field(description="Which notice this clause is.")
    who: Literal["either", "us", "counterparty", "named"] = Field(default="either", description="Who may give the notice.")
    number: int | None = Field(default=None, ge=0, le=MAX_PERIOD, description="Length of the notice period.")
    unit: PeriodUnit | None = Field(default=None, description="Unit of the notice period.")
    clear_days: bool = Field(default=False, description="True if the contract says clear days.")
    counted: Literal["before_end_of_term", "other"] = Field(default="before_end_of_term", description="What the notice period is counted back from.")
    quote: str | None = Field(default=None, description="The exact clause wording.")


class RenewalExtractionOutput(BaseModel):
    """The term and renewal WORDING as typed facts. No AI-computed dates: the
    expiry, notice deadline and window are counted by code from these facts."""

    term_kind: Literal["fixed", "evergreen", "unclear"] = Field(default="unclear", description="fixed: has an end; evergreen: continues until terminated; unclear: cannot tell.")
    # Only a date written out in the text, never one the model worked out.
    stated_end_date: date | None = Field(default=None, description="End date written out in the text, YYYY-MM-DD.")
    stated_end_date_quote: str | None = Field(default=None, description="The exact words giving that end date.")
    initial_term: InitialTerm | None = Field(default=None, description="Length of the first term, if stated.")
    auto_renews: bool | None = Field(default=None, description="True or false only if the contract says; null if unclear.")
    renewal_term: TermLength | None = Field(default=None, description="Length of each renewal term, if stated.")
    max_renewals: int | None = Field(default=None, ge=0, le=100, description="Maximum number of renewals, if capped.")
    notice_terms: list[RenewalNoticeTerm] = Field(default_factory=list, description="Every notice clause, one entry each.")
    termination_rights_summary: str | None = Field(default=None, description="Short summary of termination rights.")
    confidence: Literal["high", "medium", "low"] = Field(default="low", description="How clearly the text states the term and renewal.")
    needs_review: bool = Field(default=False, description="True when a material field is null, conflicting or the text is partial.")
    citations: list[CitationInput] = Field(default_factory=list, description="Exact quotes for each populated date or renewal field.")


class BrainAnswerOutput(BaseModel):
    answer: str = Field(min_length=1, description="The direct answer, concise and evidence-led, naming the contracts it rests on.")
    citations: list[CitationInput] = Field(default_factory=list, description="One per contract-specific claim: 8-25 words copied character-for-character from the supplied context.")
    related_contract_ids: list[str] = Field(default_factory=list, description="Ids of the contracts the answer is about, as given in the context.")
    confidence: Literal["high", "medium", "low"] = Field(default="low", description="high: explicit quoted language supports the key claims; medium: a limited inference from quoted facts; low: thin, ambiguous or conflicting.")
    limitations: str | None = Field(default=None, description="Only the specific gap in the context, if any; must not contradict the answer.")


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
    contract_types: list[str] = Field(default_factory=list, description="Kinds of contract as the user said them: NDA, MSA, statement of work.")
    counterparty: str | None = Field(default=None, description="The other party's name as the user said it.")
    governing_law: str | None = Field(default=None, description="A place: England and Wales, Singapore, Delaware.")
    stages: list[Literal["intake", "drafting", "review", "approval", "signature", "active", "closed"]] = Field(
        default_factory=list, description="Lifecycle stages, only when the question names one."
    )
    risk_bands: list[Literal["low", "medium", "high", "unknown"]] = Field(
        default_factory=list, description="Risk levels, only when named; unknown means not yet reviewed."
    )
    effective: DateRange | None = Field(default=None, description="Condition on the start date, as the user said it.")
    expires: DateRange | None = Field(default=None, description="Condition on the end date, as the user said it.")
    value_min: float | None = Field(default=None, ge=0, description="Lowest contract value, written in full (1 crore = 10000000).")
    value_max: float | None = Field(default=None, ge=0, description="Highest contract value, written in full.")
    currency: str | None = Field(default=None, description="ISO-4217 code; required with a value bound. Use the default currency if none is said.")
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
    count_only: bool = Field(default=False, description="True for how many / count / number of (the list is still returned).")


class PortfolioFilterOutput(PortfolioFilter):
    # False when the question is not "which / how many / list contracts ..." (for example
    # "what is our usual liability cap?"): such a question goes to search instead.
    is_list_question: bool = Field(description="False when the question is not which / how many / list contracts; then leave every other field empty.")


class TabularCellOutput(BaseModel):
    answer: str = Field(default="", description="Concise answer to the precise question, with qualifiers kept; empty when not_found.")
    reasoning: str | None = Field(default=None, description="Brief reasoning; when not_found, the specific gap.")
    not_found: bool = Field(default=False, description="True when the contract is silent on the question. Silence is not 'no'.")
    # Set only for a column that asks a yes/no question. Code decides from this, never
    # from the answer's prose (portfolio filter: contract_brain/portfolio.py).
    verdict: Literal["yes", "no", "not_stated"] | None = Field(
        default=None,
        description="Only for a yes/no question: yes or no if the text says so explicitly, not_stated if it is silent. Null otherwise.",
    )
    confidence: Literal["high", "medium", "low"] = Field(default="low", description="Low for partial, ambiguous, conflicting or OCR-damaged text.")
    citations: list[CitationInput] = Field(default_factory=list, description="A short verbatim quote that directly supports the answer, with its section when known.")


class TabularRowAnswer(TabularCellOutput):
    column_id: str = Field(description="The column_id exactly as supplied.")


class TabularRowOutput(BaseModel):
    answers: list[TabularRowAnswer] = Field(default_factory=list, description="Exactly one answer per supplied question, in the same order.")


class TabularChatOutput(BaseModel):
    answer: str = Field(min_length=1, description="Bottom line first; a compact table when comparing contracts. Missing data is never 'no'.")
    citations: list[CitationInput] = Field(default_factory=list, description="The contract and cell evidence for each material claim.")


class PrivacyIncidentAssessmentOutput(BaseModel):
    severity: Literal["low", "medium", "high", "critical"] = Field(default="medium", description="From data type, scale, cause, access and harm: critical = sensitive data at scale or ongoing compromise; low = near miss or internal-only.")
    notification_required: bool = Field(default=False, description="True only when the facts and a stated jurisdiction or contract support a notification duty.")
    # Hours from discovery, e.g. 72 for a DPDP-style breach clock — null when no
    # statutory notification clock applies.
    notification_deadline_hours: int | None = Field(default=None, description="Hours from discovery under a clock the facts support; null when none applies. Never assume 72.")
    affected_data_categories: list[str] = Field(default_factory=list, description="Plain terms drawn only from the input.")
    estimated_affected_count: str | None = Field(default=None, description="As stated, or 'unknown — under investigation' when the input says it is uncertain; never invented.")
    recommended_immediate_actions: list[str] = Field(default_factory=list, description="2-5 concrete steps specific to this incident, supported by the facts.")
    rationale: str = Field(min_length=1, description="1-3 sentences tied to data type, scale, cause, access and harm.")
    # Numeric, not high/medium/low — this feeds the flow engine's
    # escalate_below_confidence gate directly, unlike this file's other skills.
    # No default: this is the one field the whole gate depends on, so a model
    # response that omits it must fail loudly (a validation error), not silently
    # pass as an unearned "medium confidence" verdict.
    confidence: float = Field(ge=0.0, le=1.0, description="Evidentiary completeness, not seriousness: 0.85-1.0 only when category, scale, cause, access and timing are clear; below 0.4 when too sparse.")


class AssistantAnswerOutput(BaseModel):
    answer: str = Field(description="The reply shown to the user.")
    citations: list[CitationInput] = Field(default_factory=list, description="Verbatim quotes for each contract-specific claim.")
    tool_results: list[dict[str, Any]] = Field(default_factory=list, description="Results of tools called while answering.")


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
