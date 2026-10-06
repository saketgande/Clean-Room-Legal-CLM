"""Renewal recommendation agent.

Turns the manual renew/terminate/renegotiate dropdown into a grounded suggestion:
given the contract's own facts on file (type, value, risk band, counterparty) and
the renewal timing, it recommends one action with a short rationale and an honest
confidence. It is advisory only — the lawyer still records the decision.

Like the other agents here it never raises: under mock mode or an API failure it
falls back to a defensible heuristic so the modal always has something useful.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.core.database import utcnow

logger = logging.getLogger(__name__)

_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["renew", "renegotiate", "terminate"],
            "description": "The single recommended action.",
        },
        "rationale": {
            "type": "string",
            "description": "One or two sentences, specific to the facts given — no boilerplate.",
        },
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["decision", "rationale", "confidence"],
}


def _days_until(d: date | None) -> int | None:
    if not d:
        return None
    return (d - utcnow().date()).days


def _facts(renewal, contract) -> list[str]:
    facts = [f"Contract: {contract.title}"]
    if contract.contract_type:
        facts.append(f"Type: {contract.contract_type}")
    if contract.counterparty_name:
        facts.append(f"Counterparty: {contract.counterparty_name}")
    if contract.value_amount is not None:
        facts.append(f"Annual/contract value: {contract.value_amount:,.0f}")
    if contract.risk_band or contract.risk_score is not None:
        facts.append(
            f"Risk: {contract.risk_band or 'unknown'}"
            + (f" (score {contract.risk_score})" if contract.risk_score is not None else "")
        )
    dn = _days_until(renewal.notice_date)
    de = _days_until(renewal.expiration_date)
    if de is not None:
        facts.append(f"Expires in {de} days")
    if dn is not None:
        facts.append(
            f"Notice deadline in {dn} days"
            if dn >= 0
            else f"Notice deadline passed {-dn} days ago"
        )
    return facts


def _fallback(renewal, contract) -> dict:
    """Deterministic, defensible default when the model is unavailable: flag
    high-risk agreements for renegotiation, otherwise recommend renewal. Kept
    honest with low confidence — a heuristic is a starting point, not a verdict."""
    band = (contract.risk_band or "").lower()
    if band in {"high", "critical"}:
        return {
            "decision": "renegotiate",
            "rationale": (
                "This agreement carries elevated risk on file — use the renewal as "
                "leverage to fix the flagged terms before extending."
            ),
            "confidence": "low",
            "generated": False,
        }
    return {
        "decision": "renew",
        "rationale": (
            "No adverse signals on file — default to renewal, and confirm the "
            "commercial terms before the notice deadline."
        ),
        "confidence": "low",
        "generated": False,
    }


def recommend_renewal(db: Session, *, org_id: str, renewal, contract, claude_client=None) -> dict:
    """Return {decision, rationale, confidence, generated}. Advisory only.

    Runs through the AI gateway (feature "renewal_recommendation"): the prompt
    comes from the prompt table, and every call — success, bad answer or
    failure — is recorded in the AI ledger (on its own session)."""
    from app.core.config import settings

    if settings.mock_claude:
        return _fallback(renewal, contract)

    from app.ai.gateway import AICallContext, gateway_for

    facts = _facts(renewal, contract)
    user = "Recommend a renewal action for this contract:\n\n" + "\n".join(facts)
    try:
        result = gateway_for(claude_client).structured_sync(
            db,
            "renewal_recommendation",
            ctx=AICallContext(org_id=org_id, resource=("renewal_event", renewal.id)),
            user_prompt=user,
            input_schema=_SCHEMA,
            log_input={"renewal_id": renewal.id, "contract_id": contract.id, "facts": len(facts)},
        )
    except Exception:
        logger.warning(
            "renewal recommendation failed for %s — using heuristic", renewal.id, exc_info=True
        )
        return _fallback(renewal, contract)
    data = dict(result.data)
    if "decision" not in data:
        return _fallback(renewal, contract)
    data["generated"] = True
    return data
