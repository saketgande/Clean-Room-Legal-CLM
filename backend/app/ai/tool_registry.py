from dataclasses import dataclass
from typing import Type

from pydantic import BaseModel, EmailStr, Field

from app.core.enums import AssistantToolCategory


class EmptyInput(BaseModel):
    pass


class ContractHandleInput(BaseModel):
    contract_handle: str | None = None
    contract_id: str | None = None


class FindInContractInput(ContractHandleInput):
    query: str = Field(min_length=1)


class ProjectContractsInput(BaseModel):
    project_id: str


class WorkflowRunInput(BaseModel):
    workflow_id: str
    prompt: str | None = None
    contract_ids: list[str] = Field(default_factory=list)


class GenerateContractInput(BaseModel):
    title: str
    instructions: str
    project_id: str | None = None


class EditContractInput(ContractHandleInput):
    instructions: str


class PlaybookToolInput(ContractHandleInput):
    playbook_id: str
    playbook_version_id: str | None = None
    test_mode: bool = False


class BrainAskInput(ContractHandleInput):
    question: str = Field(min_length=3)
    query_scope: str = Field(default="contract", pattern="^(contract|project|portfolio)$")
    project_id: str | None = None


class ApprovalSubmitInput(ContractHandleInput):
    approver_user_id: str | None = None
    approver_role: str | None = None


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
    project_id: str | None = None
    contract_handles: list[str] = Field(default_factory=list)
    contract_ids: list[str] = Field(default_factory=list)
    columns: list[TabularColumnInput] = Field(min_length=1)


class ReadTableCellsInput(BaseModel):
    tabular_review_id: str


class ExternalShareInput(ContractHandleInput):
    expires_in_days: int | None = Field(default=7, ge=1, le=365)
    passcode: str | None = Field(default=None, min_length=4)
    download_allowed: bool = False


class ArchiveContractInput(ContractHandleInput):
    reason: str | None = None


class GenericToolOutput(BaseModel):
    result: dict = Field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    category: str
    required_permission: str
    input_model: Type[BaseModel]
    output_model: Type[BaseModel]
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
    input_model: Type[BaseModel],
    *,
    confirmation_policy: str = "none",
    feature_flag: str | None = None,
    session_types: set[str] | None = None,
    enabled_by_default: bool = True,
) -> None:
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


_register("read_contract", "Read contract text and metadata.", AssistantToolCategory.READ_ONLY, "contract:read", ContractHandleInput)
_register("find_in_contract", "Find text in a contract.", AssistantToolCategory.READ_ONLY, "contract:read", FindInContractInput)
_register("list_project_contracts", "List contracts in a project.", AssistantToolCategory.READ_ONLY, "project:read", ProjectContractsInput)
_register("get_contract_status", "Read contract lifecycle and risk metadata.", AssistantToolCategory.READ_ONLY, "contract:read", ContractHandleInput)
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
_register("run_workflow", "Run an approved workflow.", AssistantToolCategory.MUTATING, "workflow:read", WorkflowRunInput)
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
_register("replicate_contract_version", "Replicate a contract version.", AssistantToolCategory.DRAFT_OR_PROPOSE, "contract_file:create", ContractHandleInput)
_register(
    "run_playbook_review",
    "Run a governed playbook against a contract and store deviations.",
    AssistantToolCategory.MUTATING,
    "playbook:run",
    PlaybookToolInput,
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

_register("ask_contract_brain", "Retrieve Contract Brain context for a legal question.", AssistantToolCategory.READ_ONLY, "assistant:use", BrainAskInput)
_register(
    "submit_for_approval",
    "Submit a contract for approval.",
    AssistantToolCategory.MUTATING,
    "contract:approve",
    ApprovalSubmitInput,
    confirmation_policy="required",
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
_register("create_tabular_review", "Create a tabular review from selected contracts.", AssistantToolCategory.MUTATING, "assistant:use_ai_tools", TabularReviewInput)
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
