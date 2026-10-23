from dataclasses import dataclass

from pydantic import BaseModel, EmailStr, Field

from app.ai.schemas import PortfolioFilter
from app.core.enums import AssistantToolCategory


class EmptyInput(BaseModel):
    pass


class ContractHandleInput(BaseModel):
    contract_handle: str | None = None
    contract_id: str | None = None


class ReadContractInput(ContractHandleInput):
    # Character offset to start reading from. Long contracts come back in
    # windows; when a result has_more, call again with its next_offset to
    # continue until you've read the whole document.
    offset: int = 0


class FindInContractInput(ContractHandleInput):
    query: str = Field(min_length=1)


class ListContractVersionsInput(ContractHandleInput):
    pass


class CompareContractVersionsInput(ContractHandleInput):
    # People say "v6" and "v7": those are version_number. Two files of one contract can each have a v6,
    # so the exact version can also be named by id (the ids come from list_contract_versions).
    base_version_number: int | None = Field(default=None, ge=1, description="the older version, e.g. 6 for 'v6'. Leave out to use the version before the target")
    target_version_number: int | None = Field(default=None, ge=1, description="the newer version, e.g. 7 for 'v7'. Leave out to use the current version")
    base_version_id: str | None = Field(default=None, description="the older version by id, only when a number is ambiguous")
    target_version_id: str | None = Field(default=None, description="the newer version by id, only when a number is ambiguous")
    context_lines: int = Field(default=2, ge=0, le=10, description="unchanged lines to show around each change")
    # Long comparisons come back in windows: when a result has_more, call again with its next_offset.
    diff_offset: int = Field(default=0, ge=0)


class PromptRunInput(BaseModel):
    workflow_id: str
    prompt: str | None = None
    contract_ids: list[str] = Field(default_factory=list)


class GenerateContractInput(BaseModel):
    title: str
    instructions: str


class EditContractInput(ContractHandleInput):
    instructions: str


class RedraftContractInput(ContractHandleInput):
    instructions: str = ""


class PlaybookToolInput(ContractHandleInput):
    playbook_id: str
    playbook_version_id: str | None = None
    test_mode: bool = False


class BrainAskInput(ContractHandleInput):
    question: str = Field(min_length=3)
    query_scope: str = Field(default="contract", pattern="^(contract|portfolio)$")


class SignatureRecipientInput(BaseModel):
    name: str
    email: EmailStr
    role: str | None = None


class SignatureSendInput(ContractHandleInput):
    recipients: list[SignatureRecipientInput] = Field(min_length=1)
    override_lifecycle: bool = False


class ExtractObligationsInput(ContractHandleInput):
    pass


class TabularColumnInput(BaseModel):
    name: str
    prompt: str = Field(min_length=3)


class TabularReviewInput(BaseModel):
    name: str
    contract_handles: list[str] = Field(default_factory=list)
    contract_ids: list[str] = Field(default_factory=list)
    columns: list[TabularColumnInput] = Field(min_length=1)


class ReadTableCellsInput(BaseModel):
    tabular_review_id: str


class ExternalShareInput(ContractHandleInput):
    expires_in_days: int | None = Field(default=7, ge=1, le=365)
    passcode: str | None = Field(default=None, min_length=8)
    download_allowed: bool = False


class ArchiveContractInput(ContractHandleInput):
    reason: str | None = None


class AttentionItemsInput(BaseModel):
    window_days: int = Field(default=7, ge=1, le=365)


class FindContractsInput(BaseModel):
    query: str = Field(min_length=1)


class ListObligationsInput(BaseModel):
    due_within_days: int | None = Field(default=None, ge=1, le=3650)
    group_by: str | None = Field(default=None)


class GenericToolOutput(BaseModel):
    result: dict = Field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    category: str
    required_permission: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    confirmation_policy: str
    feature_flag: str | None
    exposed_session_types: set[str]
    idempotency_strategy: str
    enabled_by_default: bool = True

    @property
    def requires_confirmation(self) -> bool:
        return self.confirmation_policy != "none"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise RuntimeError(f"Duplicate assistant tool registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown assistant tool: {name}") from exc

    def all(self) -> list[ToolSpec]:
        return sorted(self._tools.values(), key=lambda spec: spec.name)

    def exposed_for_session_type(self, session_type: str) -> list[ToolSpec]:
        return [
            spec
            for spec in self.all()
            if session_type in spec.exposed_session_types or "*" in spec.exposed_session_types
        ]


tool_registry = ToolRegistry()


def _register(
    name: str,
    description: str,
    category: str,
    permission: str,
    input_model: type[BaseModel],
    *,
    confirmation_policy: str | None = None,
    feature_flag: str | None = None,
    session_types: set[str] | None = None,
    enabled_by_default: bool = True,
) -> None:
    # Safe by default: a tool that changes data or leaves the app asks the user
    # first. Contract, notice and email text reaches this loop, so a side effect
    # must never run on the model's say-so alone (a hidden instruction in a
    # document could otherwise trigger it). A tool opts out only by passing
    # confirmation_policy="none" explicitly, with the reason next to it.
    if confirmation_policy is None:
        confirmation_policy = "none" if category == AssistantToolCategory.READ_ONLY else "required"
    tool_registry.register(
        ToolSpec(
            name=name,
            description=description,
            category=category,
            required_permission=permission,
            input_model=input_model,
            output_model=GenericToolOutput,
            confirmation_policy=confirmation_policy,
            feature_flag=feature_flag,
            exposed_session_types=session_types or {"*"},
            idempotency_strategy=f"{name}:session:input_hash",
            enabled_by_default=enabled_by_default,
        )
    )


# ---- Intake + workflow-engine tools (let Ask Aegis run the whole flow) ----
class CreateIntakeRequestInput(BaseModel):
    type_label: str = Field(description="matter type, e.g. 'NDA Request', 'Contract Review', 'Data Privacy (DPIA)', 'Trademark'")
    subject: str | None = None
    description: str = Field(default="", description="what the requester needs, in their own words")
    department: str | None = Field(default=None, description="requesting business unit, e.g. Sales, Product, Finance")
    priority: str = Field(default="Medium", pattern="^(Critical|High|Medium|Low)$")
    field_values: dict | None = Field(default=None, description="structured intake fields if known, e.g. {'counterparty':'Acme','draft_path':'custom'}")


class IntakeRequestRef(BaseModel):
    request_id: str = Field(description="the intake request id or ref (e.g. 'REQ-4188')")


class StartIntakeWorkflowInput(BaseModel):
    request_id: str = Field(description="intake request id or ref")
    workflow_id: str | None = Field(default=None, description="workflow to run; omit to use the AI-suggested one")


class AdvanceIntakeWorkflowInput(BaseModel):
    request_id: str = Field(description="intake request id or ref")
    note: str | None = Field(default=None, description="optional note for the completed step")


_register("create_intake_request", "Raise a new legal intake request (NDA, contract review, privacy, trademark, etc.). The triage classifies it, flags missing info, assigns an owner, and may auto-start a workflow.", AssistantToolCategory.MUTATING, "intake:create", CreateIntakeRequestInput)
_register("get_intake_request", "Get an intake request's status, owner, missing info, and current workflow stage. Accepts a ref like 'REQ-4188'.", AssistantToolCategory.READ_ONLY, "intake:read", IntakeRequestRef)
_register("start_intake_workflow", "Start / assign a governance workflow on an intake request (omit workflow_id to use the AI-suggested one).", AssistantToolCategory.MUTATING, "intake:update", StartIntakeWorkflowInput, confirmation_policy="required")
_register("advance_intake_workflow", "Move an intake request's workflow forward — complete the current human step, or re-check a pending approval/signature.", AssistantToolCategory.MUTATING, "intake:update", AdvanceIntakeWorkflowInput, confirmation_policy="required")
class DecideApprovalInput(BaseModel):
    request_id: str = Field(description="intake request id or ref that has a pending approval")
    decision: str = Field(pattern="^(approve|reject)$")
    comment: str | None = None


class ReassignRequestInput(BaseModel):
    request_id: str = Field(description="intake request id or ref")
    assignee: str = Field(description="the person to assign it to — their name or email")


class CreateWorkflowInput(BaseModel):
    name: str
    steps: list[dict] = Field(description="ordered steps, each {type, name, config?}. types: clm_draft, human_task, ai_task, approval, signature, counterparty, notify")
    description: str | None = None
    applies_to: str | None = Field(default=None, description="matter-type keyword this workflow should match, e.g. 'nda', 'msa' — optional")


# NOTE: workflow authoring IS exposed (gated to admins via admin_panel:access). The
# other Admin-config changes (teams, roles, authority, ethical walls, users) stay
# UI/admin-only — Ask Aegis operates the app, it does not reconfigure those.
_register("create_workflow", "Create a new governance workflow (the engine kind) with ordered steps. Step types: clm_draft, human_task, ai_task, approval, signature, counterparty, notify.", AssistantToolCategory.MUTATING, "admin_panel:access", CreateWorkflowInput, confirmation_policy="required")
_register("decide_approval", "Approve or reject the pending approval on an intake request.", AssistantToolCategory.MUTATING, "approval:decide", DecideApprovalInput, confirmation_policy="required")
_register("reassign_request", "Reassign an intake request to a different owner, given their name or email.", AssistantToolCategory.MUTATING, "intake:update", ReassignRequestInput, confirmation_policy="required")
class SignatureLinkInput(BaseModel):
    request_id: str | None = Field(default=None, description="intake request id or ref")
    contract_id: str | None = Field(default=None, description="contract id (use instead of request_id)")


class AddCommentInput(BaseModel):
    contract_id: str
    body: str = Field(min_length=1)
    visibility: str = Field(default="internal", pattern="^(internal|shared)$")


class SendForNegotiationInput(BaseModel):
    request_id: str | None = Field(default=None, description="intake request id or ref")
    contract_id: str | None = Field(default=None, description="contract id (use instead of request_id)")
    party: str = Field(default="counterparty", pattern="^(counterparty|internal)$")
    team_id: str | None = Field(default=None, description="team to notify when party=internal")


class ListNoticesInput(BaseModel):
    status: str | None = Field(default=None, description="filter, e.g. 'open', 'overdue', 'draft'")
    overdue_only: bool = False


class CreateNoticeInput(BaseModel):
    subject: str
    counterparty_name: str
    direction: str = Field(default="received", pattern="^(received|sent)$")
    notice_type: str = Field(default="other")
    description: str = ""
    contract_id: str | None = None


class NoticeRef(BaseModel):
    notice_id: str


class CompleteObligationInput(BaseModel):
    obligation_id: str


class ListRenewalsInput(BaseModel):
    limit: int = Field(default=25, ge=1, le=100)


_register("list_notices", "List legal notices (received/sent) on the register, optionally filtered by status or overdue.", AssistantToolCategory.READ_ONLY, "contract:read", ListNoticesInput)
_register("create_notice", "Log a legal notice on the register (a received demand/notice, or an outbound one).", AssistantToolCategory.MUTATING, "contract:update", CreateNoticeInput)
_register("draft_notice_response", "AI-draft a response to a notice on the register.", AssistantToolCategory.MUTATING, "contract:update", NoticeRef)
_register("complete_obligation", "Mark a contract obligation as completed.", AssistantToolCategory.MUTATING, "obligation:update", CompleteObligationInput)
_register("list_renewals", "List upcoming contract renewal events and their decisions.", AssistantToolCategory.READ_ONLY, "contract:read", ListRenewalsInput)


class CompleteTaskInput(BaseModel):
    task_id: str


class SignatureStatusInput(BaseModel):
    request_id: str | None = Field(default=None, description="intake request id or ref")
    contract_id: str | None = Field(default=None, description="contract id (use instead of request_id)")


_register("list_my_requests", "List the intake requests you raised, with their status and stage.", AssistantToolCategory.READ_ONLY, "intake:create", EmptyInput)
_register("complete_task", "Mark an intake task as done.", AssistantToolCategory.MUTATING, "intake:update", CompleteTaskInput)
_register("get_signature_status", "Check the signature status of a request's contract and its signers.", AssistantToolCategory.READ_ONLY, "contract:read", SignatureStatusInput)


class AdvanceContractStageInput(BaseModel):
    contract_handle: str | None = Field(default=None, description="the contract's handle, e.g. contract-0 (the contract this chat is about)")
    request_id: str | None = Field(default=None, description="intake request id or ref")
    contract_id: str | None = Field(default=None, description="contract id (use instead of request_id)")
    to_stage: str = Field(description="intake, drafting, review, approval, signature, active or closed")
    reason: str | None = Field(default=None, description="why; required when pulling a contract back from Approval or Signature to Review")


_register(
    "advance_contract_stage",
    "Move a contract to another lifecycle stage. To send it for approval use to_stage='approval': that "
    "completes the workflow's current review step and the workflow creates the approval chain. It needs a "
    "workflow waiting on a review step, and every suggested change accepted or rejected first; if either is "
    "missing it says so, so tell the user what is missing and where to fix it (Review tab, Lifecycle tab). "
    "Pulling a contract back to Review from Approval or Signature needs a reason. Approval to Signature and "
    "Signature to Active happen on their own when approval and signing finish. It never overrides a rule: "
    "sending a contract on with suggestions still open is done by the user in the Lifecycle tab. Call it as "
    "soon as the user asks for the move: the app shows its own Confirm / Cancel card before anything moves, "
    "so do not ask \"shall I go ahead?\" in the chat first.",
    AssistantToolCategory.MUTATING, "contract:update", AdvanceContractStageInput, confirmation_policy="required",
)
_register(
    "list_contract_suggestions",
    "List the suggested changes on a contract that nobody has accepted or rejected yet (before and after "
    "wording, why, and who made them). Use it to answer what is still open before the contract can leave Review.",
    AssistantToolCategory.READ_ONLY, "contract:read", ContractHandleInput,
)
_register("list_my_approvals", "List approvals pending your decision.", AssistantToolCategory.READ_ONLY, "contract:approve", EmptyInput)
_register("read_notice", "Read a legal notice's full details.", AssistantToolCategory.READ_ONLY, "contract:read", NoticeRef)
_register("get_signature_link", "Get the link to sign a request's contract — the user clicks it to review and sign in the app.", AssistantToolCategory.READ_ONLY, "contract:read", SignatureLinkInput)
_register("add_contract_comment", "Add a comment to a contract (internal, or shared with the counterparty).", AssistantToolCategory.MUTATING, "contract:update", AddCommentInput, confirmation_policy="required")
_register("send_for_negotiation", "Send a request's contract out for negotiation — create a counterparty share link, or notify an internal team.", AssistantToolCategory.EXTERNAL_ACTION, "contract_file:share", SendForNegotiationInput, confirmation_policy="required")
_register("read_contract", "Read a contract's full text and metadata. Long contracts return in windows: if the result has_more is true, call again with next_offset to keep reading until you've seen the whole document before analysing it.", AssistantToolCategory.READ_ONLY, "contract:read", ReadContractInput)
_register("find_in_contract", "Find text in a contract.", AssistantToolCategory.READ_ONLY, "contract:read", FindInContractInput)
_register("get_contract_status", "Read contract lifecycle and risk metadata.", AssistantToolCategory.READ_ONLY, "contract:read", ContractHandleInput)
_register(
    "list_contract_versions",
    "List a contract's versions: number (the 'v6' people say), where it came from, whether it is the one in use "
    "(current), an earlier one, or a newer proposal that was never applied, and whether its text was read. Call this "
    "before comparing when the user names versions you are unsure of.",
    AssistantToolCategory.READ_ONLY,
    "contract_file:read",
    ListContractVersionsInput,
)
_register(
    "compare_contract_versions",
    "Compare two versions of a contract line by line and return what changed (lines starting '+ ' were added in the "
    "newer version, '- ' removed from the older, two spaces unchanged). Name the versions by number ('compare v6 and "
    "v7' = base_version_number 6, target_version_number 7); with no numbers it compares the version before the current "
    "one with the current one. Long comparisons come back in windows: while the result has_more is true, call again "
    "with its next_offset as diff_offset until you have read it all, and never summarise a partial comparison as the "
    "whole. If found is false, tell the user the reason in the message and use the versions it lists; do not guess.",
    AssistantToolCategory.READ_ONLY,
    "contract_file:read",
    CompareContractVersionsInput,
)
_register("list_workflows", "List reusable workflows.", AssistantToolCategory.READ_ONLY, "workflow:read", EmptyInput)
_register(
    "list_playbooks",
    "List the org's playbooks and each playbook's versions (id, version "
    "number, status). Call this when the user wants to redline against a "
    "playbook but has not named one — present the playbooks and versions and "
    "ask them to choose before calling redline_against_playbook.",
    AssistantToolCategory.READ_ONLY,
    "playbook:read",
    EmptyInput,
)
_register("run_workflow", "Run an approved workflow.", AssistantToolCategory.MUTATING, "workflow:read", PromptRunInput)
_register("generate_contract_docx", "Generate a new contract DOCX plan.", AssistantToolCategory.DRAFT_OR_PROPOSE, "contract:create", GenerateContractInput, feature_flag="feature.ai.docx_generation")
_register(
    "edit_contract",
    "Create tracked-change edit suggestions for a contract from a natural-"
    "language instruction. Call this DIRECTLY whenever the user asks to "
    "remove, revise, change, shorten, lengthen, tighten, soften, add, or "
    "rewrite contract language — even if they did not name an exact section "
    "or quote the exact words. Pass their instruction as `instructions`; the "
    "skill locates the relevant clause itself. Do NOT interrogate the user "
    "with multiple clarifying questions (which section / what words / what "
    "goal) — make a reasonable interpretation and propose the edits; the "
    "user reviews and accepts/rejects each one. Ask a clarifying question "
    "only if the request is genuinely ambiguous (e.g. it could mean two "
    "opposite changes).",
    AssistantToolCategory.MUTATING,
    "contract:redline",
    EditContractInput,
    confirmation_policy="required",
    feature_flag="feature.ai.edit_suggestions",
)
_register(
    "redraft_contract",
    "Rewrite an ENTIRE contract into a complete, professional, properly-"
    "structured agreement, saved as a NEW version that becomes the current "
    "document. Use this when the user asks to redraft/rewrite the whole "
    "contract, flesh out a thin or placeholder draft, or 'make it a proper "
    "contract with all the required sections/details' — i.e. a wholesale "
    "rewrite. This is the right tool when edit_contract cannot anchor edits "
    "because there is little existing language to quote. For small, targeted "
    "changes to specific existing clauses, use edit_contract instead. Pass any "
    "emphasis, party details, or requirements as `instructions`.",
    AssistantToolCategory.MUTATING,
    "contract:redline",
    RedraftContractInput,
    confirmation_policy="required",
)
_register("replicate_contract_version", "Replicate a contract version.", AssistantToolCategory.DRAFT_OR_PROPOSE, "contract_file:create", ContractHandleInput)
_register(
    "run_playbook_review",
    "Run a governed playbook against a contract and store deviations.",
    AssistantToolCategory.MUTATING,
    "playbook:run",
    PlaybookToolInput,
    # Opt-out: adds a new review run for the user to read; changes no existing
    # record and sends nothing. Turning findings into edits is
    # redline_against_playbook, which asks first.
    confirmation_policy="none",
)
_register(
    "redline_against_playbook",
    "Run a playbook and create a tracked-change redline proposal. Requires a "
    "playbook_id (and optionally playbook_version_id). If the user has not "
    "chosen a playbook, call list_playbooks first and ask them to pick a "
    "playbook and version. For free-form redlines not tied to a playbook, use "
    "edit_contract with the user's instructions instead.",
    AssistantToolCategory.MUTATING,
    "contract:redline",
    PlaybookToolInput,
    confirmation_policy="required",
)

_register(
    "ask_contract_brain",
    "Answer a legal question about what contracts SAY, from the best-matching passages. It looks at "
    "a sample of passages, not every contract, so it cannot count or list contracts: for 'which / how "
    "many contracts ...' use query_contracts.",
    AssistantToolCategory.READ_ONLY,
    "assistant:use",
    BrainAskInput,
)
_register(
    "send_for_signature",
    "Send a contract to DocuSign for signature.",
    AssistantToolCategory.EXTERNAL_ACTION,
    "contract:sign",
    SignatureSendInput,
    confirmation_policy="required",
)
_register("extract_obligations", "Queue obligation extraction for a contract.", AssistantToolCategory.MUTATING, "obligation:update", ExtractObligationsInput)
# Opt-out: creates a new review table for the user to read; changes no existing record.
_register("create_tabular_review", "Create a tabular review from selected contracts.", AssistantToolCategory.MUTATING, "assistant:use_ai_tools", TabularReviewInput, confirmation_policy="none")
_register("read_table_cells", "Read a tabular review's generated cells.", AssistantToolCategory.READ_ONLY, "assistant:use", ReadTableCellsInput)
_register(
    "external_share",
    "Create an expiring external contract share link.",
    AssistantToolCategory.EXTERNAL_ACTION,
    "contract_file:share",
    ExternalShareInput,
    confirmation_policy="required",
)
_register(
    "archive_contract",
    "Archive a contract.",
    AssistantToolCategory.DESTRUCTIVE,
    "contract:archive",
    ArchiveContractInput,
    confirmation_policy="required",
)
# --- Spec A: read-only portfolio tools (let the assistant answer cross-contract questions) ---
_register(
    "my_attention_items",
    "List the contracts that need the user's attention soon — upcoming renewals, pending approvals assigned to them, obligations due, and signatures in flight — within window_days. Use for 'what needs my attention this week'.",
    AssistantToolCategory.READ_ONLY,
    "contract:read",
    AttentionItemsInput,
)
_register(
    "find_contracts",
    "Find contracts by title or counterparty name and return matches with handles for further tool use. Use to resolve a contract the user named (e.g. 'the TechCorp MSA'). Shows at most 8 matches; total says how many matched. For 'which / how many contracts ...' use query_contracts.",
    AssistantToolCategory.READ_ONLY,
    "contract:read",
    FindContractsInput,
)
_register(
    "list_obligations",
    "List obligations across the whole portfolio, optionally only those due within due_within_days, optionally grouped by counterparty. Use for 'what obligations are due in the next 30 days'.",
    AssistantToolCategory.READ_ONLY,
    "obligation:read",
    ListObligationsInput,
)


class QueryContractsInput(PortfolioFilter):
    question: str = Field(
        min_length=3,
        description="The user's question in a few words; it names the table when the AI has to read the contracts.",
    )


# Opt-out: when `wording` is set it starts a table review that reads each candidate contract
# (it spends tokens). That is the same new-record-only precedent as create_tabular_review: it
# changes no existing record and sends nothing, and the answer states the estimated cost.
_register(
    "query_contracts",
    "Answer EVERY 'which contracts ...' / 'how many contracts ...' / 'list contracts ...' question. It checks "
    "every contract the user can open (not a sample) and reports 'Checked N of M contracts' with the matches "
    "and the ones it could not check. Fill only the conditions the user stated: contract_types (as said, e.g. "
    "NDA, MSA), counterparty, governing_law (a place), stages, risk_bands, effective / expires (copy the "
    "user's year, dates, 'next N days/months', 'last N days' or a period such as this quarter into the date "
    "range; never work out dates yourself), value_min / value_max with the currency (a value needs one). "
    "Anything about what a contract SAYS (renews automatically, notice period, termination for convenience, "
    "liability cap, data protection ...) goes in `wording` as a yes/no question about ONE contract where YES "
    "is what the user wants, e.g. 'Does this contract renew automatically?'. Something a contract must NOT "
    "have ('no limitation of liability clause') goes in `lacks` as the presence question ('Does this contract "
    "have a limitation of liability clause?'), never in `wording`. The AI then reads each candidate contract "
    "(slower; costs per question; the result gives the estimate and progress); asking the same question again "
    "follows that reading at no new cost. The reader does not know today's date, so anything about today "
    "(a notice deadline this quarter) and anything no field or question can check goes in `not_checkable`, "
    "in the user's words. Set count_only for 'how many'. Then report the counts, the matches, the contracts "
    "that could not be checked, what was not checked and, when the AI read contracts, the cost estimate, "
    "exactly as returned.",
    AssistantToolCategory.MUTATING,
    "assistant:use",
    QueryContractsInput,
    confirmation_policy="none",
)
