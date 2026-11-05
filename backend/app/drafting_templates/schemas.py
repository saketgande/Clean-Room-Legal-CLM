"""Request and response shapes of the drafting-templates API (spec 006, Phase 1).

Frozen in specs/006-template-playbook-sync/plan.md ("Full request/response JSON shapes");
the TypeScript interfaces in ``frontend/src/lib/types.ts`` copy these field for field. The
routes set the ``*Out`` models as ``response_model=`` so a service that drifts from the
contract fails a test instead of the page.

A nullable field here has no default on purpose: the contract says the key is always there,
as ``null`` when empty, and the page relies on that.
"""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# --- The closed vocabularies (FR-2 statuses, FR-18 check record) ----------------------------

TemplateSource = Literal["default", "text", "docx", "pdf", "scan"]
TemplateStatusState = Literal[
    "passes",
    "has_findings",
    "checking",
    "out_of_date",
    "partly_checked",
    "couldnt_check",
    "never_checked",
    "no_playbook",
]
TemplateCheckTrigger = Literal[
    "saved",
    "uploaded",
    "restored",
    "reset",
    "type_created",
    "type_reactivated",
    "playbook_published",
    "playbook_changed",
    "manual",
    "check_all",
    "retry",
    "sample",
]
TemplateCheckState = Literal["queued", "running", "completed", "failed", "superseded"]
TemplateCheckError = Literal[
    "ai_unavailable",
    "cost_cap",
    "no_rules",
    "no_playbook",
    "unfillable",
    "wording_changed",
    "interrupted",
]
FindingSeverity = Literal["low", "medium", "high", "critical"]
TemplatePlace = Literal["body", "table", "header", "footer", "text_box", "footnote", "endnote"]
StatusChanged = Literal["wording", "playbook", "both"]
CheckAllSkipReason = Literal["checking", "no_playbook"]

# --- Requests -------------------------------------------------------------------------------


class AppliedFindingIn(BaseModel):
    """A suggestion from an earlier check that the person took into this save."""

    check_id: str
    finding_index: int


class TemplateSaveRequest(BaseModel):
    body: str = Field(max_length=200_000)
    note: str | None = Field(default=None, max_length=300)
    # The version the editor loaded (0 = never saved). Required: a save without it could
    # silently overwrite a colleague's newer version (FR-59).
    expected_version: int = Field(ge=0)
    applied_findings: list[AppliedFindingIn] = Field(default_factory=list)
    # Must be true to save wording that has no {company} or no {counterparty} (FR-35).
    acknowledge_missing_parties: bool = False


class TemplateVersionAction(BaseModel):
    """Reset, or restore an earlier version."""

    note: str | None = Field(default=None, max_length=300)
    expected_version: int = Field(ge=0)


class TemplatePreviewRequest(BaseModel):
    body: str = Field(max_length=200_000)


class TemplateCheckRequest(BaseModel):
    # The editor's text, so unsaved changes can be checked before they are saved. Left out,
    # the saved wording is checked.
    body: str | None = Field(default=None, max_length=200_000)


class TemplateStandardAdopt(BaseModel):
    """"Use the standard rules" on the playbook that reviews a template."""

    note: str | None = Field(default=None, max_length=300)


class TemplatePlaybookChange(BaseModel):
    playbook_id: str
    note: str | None = Field(default=None, max_length=300)


# --- Checks ---------------------------------------------------------------------------------


class TemplateCheckSummaryOut(BaseModel):
    id: str
    state: TemplateCheckState
    trigger: TemplateCheckTrigger
    unsaved: bool
    template_version: int
    playbook_name: str | None
    playbook_version_number: int | None
    finding_count: int | None  # null until completed
    highest_severity: FindingSeverity | None
    text_was_truncated: bool | None
    error_code: TemplateCheckError | None
    error_message: str | None  # plain words
    requested_by: str | None  # display name of the person
    on_behalf: bool  # true = the system, on behalf of requested_by
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class TemplateCheckFindingOut(BaseModel):
    index: int  # position in the check; what ``applied_findings`` names
    rule_index: int | None
    clause_type: str
    severity: FindingSeverity
    issue: str
    quote: str | None  # as the review quoted it (filled text)
    quote_template: str | None  # the same with sample values turned back into {codes}
    suggested_fix: str | None
    suggested_fix_template: str | None
    suggestion_is_wording: bool  # false = advice, never applied
    approval_required: bool


class TemplateCheckOut(TemplateCheckSummaryOut):
    template_key: str
    trigger_detail: dict[str, Any] | None
    wording_sha256: str
    playbook_id: str | None
    playbook_version_id: str | None
    rule_count: int | None
    findings: list[TemplateCheckFindingOut]
    summary: str | None
    text_length: int | None
    read_length: int | None


class TemplateStatusOut(BaseModel):
    state: TemplateStatusState
    label: str  # "Has findings"
    reason: str  # "2 findings, highest High."
    finding_count: int | None
    highest_severity: FindingSeverity | None
    changed: StatusChanged | None  # out_of_date only
    error_code: TemplateCheckError | None  # couldnt_check only
    results_vary: bool
    decided_by: TemplateCheckSummaryOut | None
    last_completed: TemplateCheckSummaryOut | None
    active_check: TemplateCheckSummaryOut | None


# --- Templates ------------------------------------------------------------------------------


class TemplateBindingOut(BaseModel):
    form_key: str
    form_name: str
    agreement_kind: str | None
    label: str


class TemplatePlaybookRefOut(BaseModel):
    id: str
    name: str
    version_id: str | None  # its latest published version (null when it has none)
    version_number: int | None
    pinned: bool  # false = today's name match, locked at the template's first check or draft
    available: bool  # false = deleted, or no published version


class TemplateFileRefOut(BaseModel):
    id: str
    filename: str
    mime_type: str
    size_bytes: int


class TemplateStandardOut(BaseModel):
    revision: str  # "014": the standard this release ships
    updated_on: date
    wording_behind: bool  # the organisation's own saved wording predates the standard
    playbook_behind: bool  # the reviewing playbook differs from the standard and predates it


class DraftingTemplateSummaryOut(BaseModel):
    key: str
    name: str
    label: str
    contract_type: str
    binding: TemplateBindingOut
    playbook: TemplatePlaybookRefOut | None  # the playbook that reviews its drafts (null = none)
    shipped: bool
    active: bool
    source: TemplateSource
    version: int  # 0 = shipped wording, never saved
    original_filename: str | None
    updated_at: datetime | None
    updated_by: str | None
    editable: bool  # false for a Word-based template (FR-40)
    status: TemplateStatusOut
    standard: TemplateStandardOut | None  # null for a type that was not shipped


class DraftingTemplatePlaceholderOut(BaseModel):
    name: str
    label: str
    about: str
    sample: str


class TemplateFieldUseOut(BaseModel):
    name: str
    label: str
    place: TemplatePlace
    count: int


class DraftingTemplateOut(DraftingTemplateSummaryOut):
    body: str
    default_body: str | None  # null for a type that was not shipped
    placeholders: list[DraftingTemplatePlaceholderOut]
    fields: list[TemplateFieldUseOut]
    original_file: TemplateFileRefOut | None


class AppliedFindingOut(BaseModel):
    check_id: str
    finding_index: int
    clause_type: str
    severity: FindingSeverity


class DraftingTemplateVersionOut(BaseModel):
    version: int
    note: str | None
    source: TemplateSource
    is_default: bool
    body: str
    created_at: datetime | None  # null for the synthetic version 0
    created_by: str | None
    original_file: TemplateFileRefOut | None
    applied_findings: list[AppliedFindingOut]
    restored_from_version: int | None
    ocr_confirmed_by: str | None
    ocr_confirmed_at: datetime | None
    checks: list[TemplateCheckSummaryOut]  # of this version, saved wording only, newest first


class TemplatePreviewOut(BaseModel):
    text: str


class TemplateMutationResultOut(BaseModel):
    """Save, reset, restore, change playbook (and, in Phase 2, upload, create, reactivate).

    ``changed`` false means no version was made and no check started (FR-37); ``check`` is
    null then, and also when no playbook covers the template."""

    template: DraftingTemplateOut
    changed: bool
    check: TemplateCheckSummaryOut | None


class CheckAllStartedOut(BaseModel):
    key: str
    check_id: str


class CheckAllSkippedOut(BaseModel):
    key: str
    reason: CheckAllSkipReason


class CheckAllResultOut(BaseModel):
    started: list[CheckAllStartedOut]
    skipped: list[CheckAllSkippedOut]


# --- Options (the Change playbook picker in Phase 1; the create form's bindings in Phase 2) --


class TemplateBindingOptionHolderOut(BaseModel):
    key: str
    name: str


class TemplateSuggestedPlaybookOut(BaseModel):
    id: str
    name: str


class TemplateBindingOptionOut(BaseModel):
    form_key: str
    agreement_kind: str | None
    label: str
    contract_type: str
    held_by: TemplateBindingOptionHolderOut | None
    suggested_playbook: TemplateSuggestedPlaybookOut | None  # today's name match; preselect only


class TemplatePlaybookOptionOut(BaseModel):
    id: str
    name: str
    version_number: int


class TemplateTypeOptionsOut(BaseModel):
    bindings: list[TemplateBindingOptionOut]
    playbooks: list[TemplatePlaybookOptionOut]  # published, this org
