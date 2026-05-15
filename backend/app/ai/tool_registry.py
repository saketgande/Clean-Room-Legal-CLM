from dataclasses import dataclass
from typing import Type

from pydantic import BaseModel, Field

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
_register("run_workflow", "Run an approved workflow.", AssistantToolCategory.MUTATING, "workflow:read", WorkflowRunInput)
_register("generate_contract_docx", "Generate a new contract DOCX plan.", AssistantToolCategory.DRAFT_OR_PROPOSE, "contract:create", GenerateContractInput, feature_flag="feature.ai.docx_generation")
_register(
    "edit_contract",
    "Create tracked-change-ready edit suggestions.",
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
    "Run a playbook and create a tracked-change redline proposal.",
    AssistantToolCategory.MUTATING,
    "contract:redline",
    PlaybookToolInput,
    confirmation_policy="required",
)

for future_tool_name, permission in [
    ("ask_contract_brain", "assistant:use"),
    ("submit_for_approval", "contract:approve"),
    ("send_for_signature", "contract:sign"),
    ("extract_obligations", "obligation:update"),
    ("create_tabular_review", "assistant:use_ai_tools"),
    ("read_table_cells", "assistant:use"),
    ("external_share", "contract_file:share"),
    ("archive_contract", "contract:archive"),
]:
    _register(
        future_tool_name,
        "Registered for a later product phase.",
        AssistantToolCategory.MUTATING,
        permission,
        EmptyInput,
        confirmation_policy="required" if future_tool_name in {"submit_for_approval", "send_for_signature", "external_share", "archive_contract"} else "none",
        feature_flag=f"feature.ai.{future_tool_name}",
        enabled_by_default=False,
    )
