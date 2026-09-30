"""Pydantic v2 request/response shapes for the approval_chains domain.

Every shape here matches, byte-for-byte, the JSON frozen in plan.md's
"Interface freeze" section — do not "improve" field names or add fields not
in that contract without updating plan.md first.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Module = Literal["contract", "intake_request"]
Operator = Literal["gt", "lt", "eq", "in", "contains"]

ConditionValue = float | int | str | bool | list[float | int | str | bool] | None


# ---------- Condition field catalog ----------


class ConditionExpressionIn(BaseModel):
    """The untrusted, structured condition definition accepted from a client.

    ``extra="forbid"`` is load-bearing (FR-2/FR-4/AC-3): a smuggled "and",
    "or", "not" or "code" key must be rejected as a 422 by Pydantic itself,
    before this payload ever reaches ``conditions.py``'s evaluator.
    """

    model_config = ConfigDict(extra="forbid")

    field: str
    operator: Operator
    value: ConditionValue = None


class ConditionFieldDescriptor(BaseModel):
    name: str
    type: Literal["number", "string", "boolean"]
    label: str


class ConditionFieldCatalogResponse(BaseModel):
    module: Module
    operators: list[Operator]
    fields: list[ConditionFieldDescriptor]


# ---------- Chain definitions ----------


class ChainDefinitionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    module: Module
    version: int = 1
    is_active: bool = True


class ChainDefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class ChainStepRuleResponse(BaseModel):
    id: str
    org_id: str
    step_id: str
    is_base_requirement: bool
    condition_expression: dict[str, Any] | None
    condition_text: str | None
    required_role_id: str
    required_role_name: str
    sequence_order: int
    description: str | None
    is_active: bool
    created_at: datetime
    created_by_user_id: str | None
    updated_at: datetime
    updated_by_user_id: str | None


class ChainStepResponse(BaseModel):
    id: str
    org_id: str
    definition_id: str
    step_key: str
    name: str
    sequence_order: int
    step_type: Literal["action", "approval"]
    approval_mode: Literal["sequential", "parallel"]
    rules: list[ChainStepRuleResponse] = Field(default_factory=list)
    created_at: datetime
    created_by_user_id: str | None
    updated_at: datetime
    updated_by_user_id: str | None


class ChainDefinitionResponse(BaseModel):
    id: str
    org_id: str
    name: str
    module: Module
    version: int
    is_active: bool
    is_default_seeded: bool
    steps: list[ChainStepResponse] = Field(default_factory=list)
    created_at: datetime
    created_by_user_id: str | None
    updated_at: datetime
    updated_by_user_id: str | None


# ---------- Chain steps ----------


class ChainStepCreate(BaseModel):
    step_key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    sequence_order: int
    step_type: Literal["action", "approval"] = "approval"
    approval_mode: Literal["sequential", "parallel"] = "sequential"


class ChainStepUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    sequence_order: int | None = None
    approval_mode: Literal["sequential", "parallel"] | None = None


# ---------- Chain step rules (base requirements + condition rules) ----------


class ChainStepRuleCreate(BaseModel):
    is_base_requirement: bool
    condition_expression: ConditionExpressionIn | None = None
    required_role_id: str
    sequence_order: int = 1
    description: str | None = Field(default=None, max_length=300)
    is_active: bool = True


class ChainStepRuleUpdate(BaseModel):
    condition_expression: ConditionExpressionIn | None = None
    sequence_order: int | None = None
    description: str | None = None
    is_active: bool | None = None


# ---------- Chain instances ----------


class ChainInstanceCreate(BaseModel):
    definition_id: str
    module: Module
    module_record_id: str
    org_unit_id: str | None = None


class ChainInstanceSummary(BaseModel):
    id: str
    org_id: str
    definition_id: str
    definition_name: str
    module: Module
    module_record_id: str
    module_record_label: str
    org_unit_id: str
    org_unit_name: str
    current_step_id: str | None
    current_step_key: str | None
    status: Literal["pending", "approved", "rejected", "cancelled"]
    is_blocked: bool
    pending_requirement_count: int
    started_by_user_id: str
    created_at: datetime
    updated_at: datetime


class ChainConditionExplanation(BaseModel):
    rule_id: str
    field: str
    operator: Operator
    value: Any
    actual: Any
    text: str


class ChainRequirementResponse(BaseModel):
    id: str
    instance_id: str
    step_id: str
    required_role_id: str
    required_role_name: str
    sequence_order: int
    is_base_requirement: bool
    triggered_by_rule_ids: list[str] = Field(default_factory=list)
    condition_explanations: list[ChainConditionExplanation] = Field(default_factory=list)
    explanation: str | None
    status: Literal["pending", "approved", "rejected", "cancelled"]
    counts_toward_completion: bool
    superseded_at: datetime | None
    acted_by_user_id: str | None
    acted_by_label: str | None
    acted_as_role_id: str | None
    delegated_from_user_id: str | None
    acted_at: datetime | None
    comment: str | None
    is_unfulfillable: bool
    eligible_user_count: int
    can_decide: bool
    blocked_by_sequence: bool
    materialized_at: datetime


class ChainInstanceStep(BaseModel):
    step_id: str
    step_key: str
    name: str
    sequence_order: int
    approval_mode: Literal["sequential", "parallel"]
    is_current: bool
    is_complete: bool
    is_blocked: bool
    requirements: list[ChainRequirementResponse] = Field(default_factory=list)


class ChainBlockingEntry(BaseModel):
    requirement_id: str
    step_id: str
    step_key: str
    required_role_id: str
    required_role_name: str
    sequence_order: int
    org_unit_id: str
    org_unit_name: str


class ChainHistoryEntry(BaseModel):
    id: str
    instance_id: str
    step_id: str | None
    requirement_id: str | None
    action: Literal[
        "instance_created",
        "materialized",
        "approved",
        "rejected",
        "recalculated",
        "blocked_no_eligible_approver",
        "instance_completed",
        "instance_rejected",
    ]
    acted_by_user_id: str | None
    acted_by_label: str | None
    acted_as_role_id: str | None
    acted_as_role_name: str | None
    delegated_from_user_id: str | None
    delegated_from_label: str | None
    comments: str | None
    before_json: dict[str, Any] | list[Any] | None
    after_json: dict[str, Any] | list[Any] | None
    acted_at: datetime


class ChainInstanceDetailResponse(BaseModel):
    instance: ChainInstanceSummary
    steps: list[ChainInstanceStep] = Field(default_factory=list)
    blocking: list[ChainBlockingEntry] = Field(default_factory=list)
    blocking_visible: bool
    can_recalculate: bool
    history: list[ChainHistoryEntry] = Field(default_factory=list)


class ChainBlockedResponse(BaseModel):
    instance_id: str
    blocking: list[ChainBlockingEntry] = Field(default_factory=list)


# ---------- Decisions / recalculation ----------


class ChainDecisionPayload(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = None


class ChainRecalculatePayload(BaseModel):
    reason: str | None = None
