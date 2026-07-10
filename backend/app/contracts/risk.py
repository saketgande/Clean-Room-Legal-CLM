"""Weighted, explainable contract risk score.

The old `risk_level` was a single opaque AI-set label. This replaces it with a
transparent score: each extracted clause gets an AI risk judgment (low/medium/
high + a one-line reason), multiplied by a deterministic business-impact weight
(liability/IP/data hurt far more than boilerplate). The document score is the
weighted average, and every point of it traces to the specific clauses driving
it — so a lawyer can verify the reasoning instead of trusting a badge.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.controller import ai_controller
from app.ai.schemas import ContractRiskOutput
from app.contract_brain.clause_taxonomy import (
    canonical_clause_type,
    clause_weight,
    display_label,
)
from app.contract_brain.models import ClauseExtraction
from app.contracts.models import Contract
from app.core.database import utcnow

logger = logging.getLogger(__name__)

# How adverse each risk level is, 0-1. Multiplied by the clause weight.
_SEVERITY = {"low": 0.15, "medium": 0.55, "high": 0.95}


def _band(score: int) -> str:
    return "high" if score >= 65 else "medium" if score >= 35 else "low"


async def compute_contract_risk(
    db: Session,
    *,
    contract: Contract,
    user,
    request_id: str | None = None,
) -> dict:
    """Compute + persist the weighted risk score and its drivers."""
    clauses = db.scalars(
        select(ClauseExtraction).where(
            ClauseExtraction.org_id == contract.org_id,
            ClauseExtraction.contract_id == contract.id,
            ClauseExtraction.is_stale.is_(False),
        )
    ).all()
    if not clauses:
        summary = {
            "score": None,
            "band": "unknown",
            "drivers": [],
            "counts": {"high": 0, "medium": 0, "low": 0},
            "clause_count": 0,
            "note": "No clauses extracted yet — run contract analysis first.",
            "computed_at": utcnow().isoformat(),
        }
        contract.risk_score = None
        contract.risk_band = None
        contract.risk_summary = summary
        contract.updated_by_user_id = user.id
        db.commit()
        return summary

    clause_block = "\n\n".join(
        f"[{canonical_clause_type(c.clause_type)}] {(c.text or '')[:800]}" for c in clauses
    )
    out = await ai_controller.run_structured_skill(
        db,
        skill_name="contract_risk_assessment",
        org_id=contract.org_id,
        created_by_user_id=user.id,
        input_payload={"clauses": clause_block, "contract_title": contract.title},
        request_id=request_id,
        resource_type="contract",
        resource_id=contract.id,
    )
    out = out if isinstance(out, ContractRiskOutput) else ContractRiskOutput.model_validate(out)

    drivers: list[dict] = []
    numerator = 0.0
    denominator = 0.0
    for cr in out.clause_risks:
        canon = canonical_clause_type(cr.clause_type)
        weight = clause_weight(canon)
        severity = _SEVERITY.get(cr.risk, 0.15)
        contribution = weight * severity
        numerator += contribution
        denominator += weight
        drivers.append(
            {
                "clause_type": canon,
                "label": display_label(canon),
                "weight": weight,
                "risk": cr.risk,
                "rationale": cr.rationale,
                "quote": cr.quote,
                "contribution": round(contribution, 2),
            }
        )
    score = round(100 * numerator / denominator) if denominator else 0
    drivers.sort(key=lambda d: d["contribution"], reverse=True)
    counts = {
        level: sum(1 for d in drivers if d["risk"] == level)
        for level in ("high", "medium", "low")
    }
    summary = {
        "score": score,
        "band": _band(score),
        "drivers": drivers[:8],
        "counts": counts,
        "clause_count": len(out.clause_risks),
        "summary": out.summary,
        "computed_at": utcnow().isoformat(),
    }
    contract.risk_score = score
    contract.risk_band = _band(score)
    contract.risk_level = _band(score)  # keep the legacy badge field in sync
    contract.risk_summary = summary
    contract.updated_by_user_id = user.id
    db.commit()
    db.refresh(contract)
    return summary
