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
from app.ai.schemas import ClauseRiskOutput, ContractRiskOutput
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
# Below this share of clauses judged, the answer is partial and no score is given.
_MIN_COVERAGE = 0.8


def _band(score: int) -> str:
    return "high" if score >= 65 else "medium" if score >= 35 else "low"


def _save_unknown(db: Session, *, contract: Contract, user, note: str, clause_count: int, **extra) -> dict:
    """Persist "unknown": missing or partial coverage is never a clean bill of health."""
    summary = {
        "score": None,
        "band": "unknown",
        "drivers": [],
        "counts": {"high": 0, "medium": 0, "low": 0},
        "clause_count": clause_count,
        "note": note,
        **extra,
        "computed_at": utcnow().isoformat(),
    }
    contract.risk_score = None
    contract.risk_band = None
    contract.risk_level = None  # no stale badge while the score is unknown
    contract.risk_summary = summary
    contract.updated_by_user_id = user.id
    db.commit()
    return summary


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
        # Self-heal instead of dead-ending. If the contract has text but no
        # clauses yet, kick off extraction — it chains graph ingestion and
        # auto-review, which re-runs risk once clauses exist. Previously this
        # returned "run analysis first" and nothing ever ran.
        from app.contract_files.models import ContractTextSnapshot, ContractVersion

        note = "No clauses extracted yet."
        version = (
            db.get(ContractVersion, contract.current_authoritative_version_id)
            if contract.current_authoritative_version_id
            else None
        )
        snapshot = (
            db.get(ContractTextSnapshot, version.text_snapshot_id)
            if version and version.text_snapshot_id
            else None
        )
        if version is not None and snapshot is not None:
            from app.jobs.service import create_job, dispatch_job

            job = create_job(
                db,
                org_id=contract.org_id,
                job_type="clause_extraction",
                resource_type="contract",
                resource_id=contract.id,
                created_by_user_id=user.id,
                idempotency_key=f"clause_extraction:{version.id}:{snapshot.id}",
                metadata={"contract_version_id": version.id, "text_snapshot_id": snapshot.id},
            )
            db.flush()
            try:
                dispatch_job(db, job=job)
                note = "Clause analysis started — the risk score will populate once it completes."
            except Exception:  # pragma: no cover - dispatch is best-effort
                logger.warning("could not dispatch clause extraction for %s", contract.id, exc_info=True)
        return _save_unknown(
            db, contract=contract, user=user, note=note, clause_count=0, analysis_started="started" in note
        )

    refs = {f"C{n}": clause for n, clause in enumerate(clauses, 1)}
    clause_block = "\n\n".join(
        f"[{ref}] [{canonical_clause_type(c.clause_type)}] {(c.text or '')[:800]}" for ref, c in refs.items()
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

    # Key every judgment to a clause that was actually sent; unknown or repeated refs don't count.
    judged: dict[str, ClauseRiskOutput] = {}
    for cr in out.clause_risks:
        ref = (cr.clause_ref or "").strip().strip("[]").upper()
        if ref in refs:
            judged.setdefault(ref, cr)
    counted = {
        "clause_count": len(clauses),
        "assessed_count": len(judged),
        "coverage": round(len(judged) / len(clauses), 2),
        "summary": out.summary,
    }
    if len(judged) < _MIN_COVERAGE * len(clauses):
        # 6 judgments for a 40-clause contract would otherwise read as the whole
        # contract, and the score would change with whichever clauses came back.
        return _save_unknown(
            db,
            contract=contract,
            user=user,
            note=f"The risk assessment covered {len(judged)} of {len(clauses)} clauses, too few to score.",
            **counted,
        )

    # One judgment per clause type (its worst), in clause order, so repeated
    # clauses of one type don't outweigh the rest of the contract.
    worst: dict[str, ClauseRiskOutput] = {}
    for ref, clause in refs.items():
        cr = judged.get(ref)
        canon = canonical_clause_type(clause.clause_type)
        if cr is not None and (canon not in worst or _SEVERITY[cr.risk] > _SEVERITY[worst[canon].risk]):
            worst[canon] = cr

    drivers: list[dict] = []
    numerator = 0.0
    denominator = 0.0
    for canon, cr in worst.items():
        weight = clause_weight(canon)
        contribution = weight * _SEVERITY[cr.risk]
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
    if not denominator:
        return _save_unknown(
            db,
            contract=contract,
            user=user,
            note="The risk assessment returned no weighted clause findings, so no score was computed.",
            **counted,
        )
    score = round(100 * numerator / denominator)
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
        **counted,
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
