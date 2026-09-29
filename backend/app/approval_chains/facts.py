"""The whitelisted fact projection of a business record for condition rules.

Each subject type ("contract", "intake_request") exposes a FROZEN, named set
of facts a condition rule's ``field`` may reference (FR-2/FR-3). This is
deliberately never a live lookup into a record's arbitrary JSON payload
(``Contract.metadata_json`` / ``IntakeRequest.field_values``) — that would let
requester-supplied data smuggle in new "facts" outside the intended surface.
``MODULE_FACTS`` is the catalog; ``allowed_fields`` is what
``conditions.validate_expression``/``evaluate_condition`` check a rule's
``field`` against; ``build_facts`` is what materialization calls to get the
actual values at chain-entry time.
"""

from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

# fact name -> Contract attribute name. Verified against app/contracts/models.py.
CONTRACT_FACTS: dict[str, str] = {
    "contract_value": "value_amount",
    "currency": "currency",
    "contract_type": "contract_type",
    "jurisdiction": "jurisdiction",
    "risk_band": "risk_band",
    "risk_score": "risk_score",
    "confidentiality": "confidentiality",
    "counterparty_name": "counterparty_name",
    "lifecycle_stage": "lifecycle_stage",
    "title": "title",
    "matter_id": "matter_id",
    "owner_user_id": "owner_user_id",
    "effective_date": "effective_date",
    "expiration_date": "expiration_date",
    "renewal_due": "renewal_due",
    "archived": "archived",
}

# fact name -> IntakeRequest attribute name. Verified against
# app/intake/models.py. Plain columns only — the derived facts below
# (request_value/currency/jurisdiction/risk_band) are computed separately by
# build_facts, reusing app.intake.approval_bridge.IntakeApprovalSubject's
# existing semantics so a condition rule and the legacy routing matcher never
# disagree on the same number.
INTAKE_REQUEST_FACTS: dict[str, str] = {
    "request_ref": "ref",
    "request_subject": "subject",
    "request_type": "type_label",
    "department": "department",
    "priority": "priority",
    "source": "source",
    "status": "status",
    "stage": "stage",
    "work_status": "work_status",
    "sla_hours": "sla_hours",
    "sla_status": "sla_status",
    "requester_user_id": "requester_user_id",
    "assigned_to_user_id": "assigned_to_user_id",
    "matter_id": "matter_id",
    # Derived facts (not plain columns) — computed in build_facts below by
    # reusing IntakeApprovalSubject's value_amount/currency/jurisdiction/
    # risk_band properties directly. Listed here so allowed_fields() includes
    # them for condition-rule validation.
    "request_value": "request_value",
    "currency": "currency",
    "jurisdiction": "jurisdiction",
    "risk_band": "risk_band",
    "request_type_key": "request_type_key",
    "has_contract": "has_contract",
}

_INTAKE_DERIVED_FACTS = frozenset(
    {"request_value", "currency", "jurisdiction", "risk_band", "request_type_key", "has_contract"}
)

MODULE_FACTS: dict[str, dict[str, str]] = {
    "contract": CONTRACT_FACTS,
    "intake_request": INTAKE_REQUEST_FACTS,
}

# Per-module fact -> primitive type, driving GET /approval-chains/fields (T009).
CONTRACT_FIELD_TYPES: dict[str, str] = {
    "contract_value": "number",
    "currency": "string",
    "contract_type": "string",
    "jurisdiction": "string",
    "risk_band": "string",
    "risk_score": "number",
    "confidentiality": "string",
    "counterparty_name": "string",
    "lifecycle_stage": "string",
    "title": "string",
    "matter_id": "string",
    "owner_user_id": "string",
    "effective_date": "string",
    "expiration_date": "string",
    "renewal_due": "boolean",
    "archived": "boolean",
}

INTAKE_REQUEST_FIELD_TYPES: dict[str, str] = {
    "request_ref": "string",
    "request_subject": "string",
    "request_type": "string",
    "department": "string",
    "priority": "string",
    "source": "string",
    "status": "string",
    "stage": "string",
    "work_status": "string",
    "sla_hours": "number",
    "sla_status": "string",
    "requester_user_id": "string",
    "assigned_to_user_id": "string",
    "matter_id": "string",
    "request_value": "number",
    "currency": "string",
    "jurisdiction": "string",
    "risk_band": "string",
    "request_type_key": "string",
    "has_contract": "boolean",
}

FIELD_TYPES: dict[str, dict[str, str]] = {
    "contract": CONTRACT_FIELD_TYPES,
    "intake_request": INTAKE_REQUEST_FIELD_TYPES,
}


def allowed_fields(module: str) -> frozenset[str]:
    """The set of valid fact names for ``module``; empty for an unknown module
    (fail-closed — an unrecognized module can never validate any field)."""
    return frozenset(MODULE_FACTS.get(module, {}))


def _json_safe(value: Any) -> Any:
    """Coerce a raw attribute value to a JSON-safe scalar so a stored
    materialization/explanation never holds a Decimal/date object."""
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _build_contract_facts(db: Session, *, record_id: str, org_id: str) -> dict[str, Any]:
    from app.contracts.models import Contract

    contract = db.get(Contract, record_id)
    if contract is None or contract.org_id != org_id:
        raise HTTPException(404, "Contract not found")

    return {
        fact_name: _json_safe(getattr(contract, attr_name, None))
        for fact_name, attr_name in CONTRACT_FACTS.items()
    }


def _build_intake_request_facts(db: Session, *, record_id: str, org_id: str) -> dict[str, Any]:
    from app.intake.approval_bridge import IntakeApprovalSubject, _type_key_for
    from app.intake.models import IntakeRequest

    request = db.get(IntakeRequest, record_id)
    if request is None or request.org_id != org_id:
        raise HTTPException(404, "Request not found")

    subject = IntakeApprovalSubject(request, type_key=_type_key_for(db, request))

    facts: dict[str, Any] = {
        fact_name: _json_safe(getattr(request, attr_name, None))
        for fact_name, attr_name in INTAKE_REQUEST_FACTS.items()
        if fact_name not in _INTAKE_DERIVED_FACTS
    }
    # Derived facts, computed via IntakeApprovalSubject's own properties so
    # they can never drift from the legacy routing matcher's numbers.
    facts["request_value"] = _json_safe(subject.value_amount)
    facts["currency"] = _json_safe(subject.currency)
    facts["jurisdiction"] = _json_safe(subject.jurisdiction)
    facts["risk_band"] = _json_safe(subject.risk_band)
    facts["request_type_key"] = subject._type_key
    facts["has_contract"] = bool(request.contract_id)
    return facts


def build_facts(db: Session, *, module: str, record_id: str, org_id: str) -> dict[str, Any]:
    """Load the org-scoped record and project it through the frozen whitelist
    for its module. Raises HTTPException(404) when the record is missing or
    belongs to another org — a missing record when materializing a chain step
    is a real bug worth surfacing, not a degenerate "empty facts" case.
    """
    if module == "contract":
        return _build_contract_facts(db, record_id=record_id, org_id=org_id)
    if module == "intake_request":
        return _build_intake_request_facts(db, record_id=record_id, org_id=org_id)
    raise HTTPException(422, f"Unknown module '{module}'")
