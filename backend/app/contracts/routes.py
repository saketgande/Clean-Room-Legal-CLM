from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.contract_files.service import create_contract_from_upload
from app.contracts.lifecycle import allowed_transitions_for, transition_contract_stage
from app.core.config import settings
from app.core.rate_limit import limiter
from app.core.rbac import has_permission
from app.contracts.schemas import (
    ContractActivityResponse,
    ContractPartyCreate,
    ContractPartyResponse,
    ContractResponse,
    ContractStageHistoryResponse,
    ContractUpdate,
    ContractUploadResponse,
    LifecycleOptionsResponse,
    LifecycleTransitionRequest,
    ReviewStatusResponse,
    SignerOption,
    VersionDiffResponse,
)
from app.contracts.service import (
    add_contract_party,
    compute_review_status,
    compute_version_diff,
    delete_contract_party,
    get_contract_for_user,
    list_contract_activity,
    list_contract_parties,
    list_contract_stage_history,
    list_contracts_for_user,
    list_signer_options,
    update_contract_metadata,
)
from app.core.deps import get_db, require_permission

router = APIRouter(prefix="/contracts", tags=["contracts"])


@router.get("", response_model=list[ContractResponse])
def list_contracts(
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    # Optional pagination; defaults preserve the historical "first 100, newest
    # first" behaviour so existing callers/tests see an unchanged list shape.
    return list_contracts_for_user(db, user=current_user, limit=limit, offset=offset)


@router.post(
    "/upload",
    response_model=ContractUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(settings.rate_limit_contract_upload)
async def upload_contract(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    counterparty_name: str | None = Form(default=None),
    project_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:create")),
):
    return await create_contract_from_upload(
        db,
        upload=file,
        user=current_user,
        project_id=project_id,
        title=title,
        counterparty_name=counterparty_name,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/{contract_id}", response_model=ContractResponse)
def get_contract(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    return get_contract_for_user(db, contract_id=contract_id, user=current_user)


@router.get("/{contract_id}/risk")
def get_contract_risk(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    """The stored weighted risk summary (score + drivers). Empty until computed."""
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return contract.risk_summary or {
        "score": contract.risk_score,
        "band": contract.risk_band,
        "drivers": [],
        "counts": {"high": 0, "medium": 0, "low": 0},
        "clause_count": 0,
        "note": "Not computed yet.",
    }


@router.get("/{contract_id}/deviations")
def get_contract_deviations(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    """Open playbook deviations for the contract, severity-sorted — powers the
    on-ticket AI analysis view."""
    from sqlalchemy import select as _select

    from app.playbooks.models import PlaybookDeviation
    from app.playbooks.schemas import PlaybookDeviationResponse

    get_contract_for_user(db, contract_id=contract_id, user=current_user)
    rows = db.scalars(
        _select(PlaybookDeviation)
        .where(
            PlaybookDeviation.org_id == current_user.org_id,
            PlaybookDeviation.contract_id == contract_id,
            PlaybookDeviation.status.in_(["open", "needs_review"]),
        )
        .order_by(PlaybookDeviation.created_at.asc())
    ).all()
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    rows = sorted(rows, key=lambda d: rank.get((d.severity or "").lower(), 9))
    return [PlaybookDeviationResponse.model_validate(d).model_dump() for d in rows]


_PLAIN_SUMMARY_SYSTEM = (
    "You explain a contract's AI legal review to a NON-LAWYER — a colleague in sales, "
    "procurement or product who just needs to know where things stand. Write in plain, "
    "everyday English with no legal jargon; if a legal term is unavoidable, explain it in "
    "a few words. Keep it short: start with ONE bottom-line sentence (is it safe to proceed "
    "and the overall risk), then 2 to 4 short bullet points ('• ' each) for the things "
    "actually worth knowing — each phrased as what it means for the business, not the clause "
    "name. End with one line on what to do next. Be calm and reassuring where the risk is "
    "low. Never invent issues or numbers."
)


@router.get("/{contract_id}/plain-summary")
async def contract_plain_summary(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    """A plain-English, non-lawyer-readable summary of the AI review (risk + playbook
    deviations). Best-effort — degrades to a deterministic summary; never raises."""
    from sqlalchemy import select as _select

    from app.core.config import settings
    from app.integrations.claude import claude_client
    from app.playbooks.models import PlaybookDeviation

    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    devs = db.scalars(
        _select(PlaybookDeviation).where(
            PlaybookDeviation.org_id == current_user.org_id,
            PlaybookDeviation.contract_id == contract_id,
            PlaybookDeviation.status.in_(["open", "needs_review"]),
        )
    ).all()
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    devs = sorted(devs, key=lambda d: rank.get((d.severity or "").lower(), 9))
    rs = contract.risk_summary or {}
    band = rs.get("band") or "unrated"
    score = rs.get("score")
    counts = rs.get("counts") or {}

    def _fallback() -> str:
        if not devs:
            return (f"Overall {band} risk. The review found no open issues — this looks like "
                    "standard, safe paper you can proceed with.")
        hi, md, lo = counts.get("high", 0), counts.get("medium", 0), counts.get("low", 0)
        head = f"Overall {band} risk — {hi} important, {md} moderate and {lo} minor point(s) worth knowing before signing."
        pts = "\n".join(f"• {d.issue}" for d in devs[:3])
        tail = ("A lawyer should glance at the important items before you sign."
                if hi else "Minor tweaks only — safe to proceed once they're addressed.")
        return f"{head}\n\n{pts}\n\n{tail}"

    if settings.mock_claude or not devs:
        return {"summary": _fallback(), "generated": False}
    try:
        dev_lines = "\n".join(
            f"- [{d.severity or 'low'}] {d.clause_type}: {d.issue}"
            + (f" (suggested fix: {d.suggested_fix})" if d.suggested_fix else "")
            for d in devs[:12]
        )
        resp = await claude_client.complete_text(
            system_prompt=_PLAIN_SUMMARY_SYSTEM,
            user_prompt=f"Contract: {contract.title}\nOverall risk: {band} (score {score}).\n\nPlaybook deviations found:\n{dev_lines}",
            max_tokens=500, temperature=0.3,
        )
        answer = "".join(b.get("text", "") for b in resp.content_blocks if b.get("type") == "text").strip()
        return {"summary": answer or _fallback(), "generated": bool(answer)}
    except Exception:
        return {"summary": _fallback(), "generated": False}


@router.post("/{contract_id}/risk")
async def compute_contract_risk_route(
    contract_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    """Compute (or recompute) the weighted, explainable risk score from the
    contract's extracted clauses."""
    from app.contracts.risk import compute_contract_risk

    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return await compute_contract_risk(
        db,
        contract=contract,
        user=current_user,
        request_id=getattr(request.state, "request_id", None),
    )


@router.patch("/{contract_id}", response_model=ContractResponse)
def update_contract(
    contract_id: str,
    payload: ContractUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:update")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return update_contract_metadata(
        db,
        contract=contract,
        user=current_user,
        updates=payload.model_dump(exclude_unset=True),
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{contract_id}/lifecycle", response_model=ContractResponse)
def transition_lifecycle(
    contract_id: str,
    payload: LifecycleTransitionRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:update")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    transition_contract_stage(
        db,
        contract=contract,
        to_stage=payload.to_stage,
        actor_user_id=current_user.id,
        reason=payload.reason,
        override=payload.override,
        override_authorized=has_permission(
            current_user.permission_values, "contract:lifecycle_override"
        ),
        signed_confirmation=payload.signed_confirmation,
        request_id=getattr(request.state, "request_id", None),
    )
    db.commit()
    db.refresh(contract)
    return contract


@router.get("/{contract_id}/lifecycle", response_model=LifecycleOptionsResponse)
def get_lifecycle_options(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    from datetime import UTC, datetime

    from sqlalchemy import func as _func, select

    from app.contracts.lifecycle import parse_stage_slas
    from app.contracts.models import ContractStageHistory
    from app.core.config import settings as _settings

    entered = db.scalar(
        select(_func.max(ContractStageHistory.changed_at)).where(
            ContractStageHistory.contract_id == contract.id
        )
    ) or contract.created_at
    days_in_stage = max(0, (datetime.now(UTC) - entered).days)
    sla = parse_stage_slas(_settings.stage_sla_days).get(contract.lifecycle_stage)
    return {
        "current_stage": contract.lifecycle_stage,
        "allowed_transitions": allowed_transitions_for(contract.lifecycle_stage),
        "days_in_stage": days_in_stage,
        "stage_sla_days": sla,
        "sla_breached": bool(sla is not None and days_in_stage > sla),
    }


@router.get("/{contract_id}/review-status", response_model=ReviewStatusResponse)
def get_review_status(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    """Guided "what to do next" for a contract's review — derived from playbook
    deviations, pending redlines, open comments, and counterparty shares."""
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return compute_review_status(db, contract=contract)


@router.get(
    "/{contract_id}/versions/{base_version_id}/diff/{target_version_id}",
    response_model=VersionDiffResponse,
)
def get_version_diff(
    contract_id: str,
    base_version_id: str,
    target_version_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    """Line-level diff between two versions (e.g. yours vs the counterparty's)."""
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return compute_version_diff(
        db,
        contract=contract,
        base_version_id=base_version_id,
        target_version_id=target_version_id,
    )


@router.get("/{contract_id}/parties", response_model=list[ContractPartyResponse])
def list_parties(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return list_contract_parties(db, contract=contract)


@router.post(
    "/{contract_id}/parties", response_model=ContractPartyResponse, status_code=status.HTTP_201_CREATED
)
def add_party(
    contract_id: str,
    payload: ContractPartyCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:update")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return add_contract_party(
        db,
        contract=contract,
        user=current_user,
        name=payload.name,
        contact_email=payload.contact_email,
        party_type=payload.party_type,
    )


@router.delete("/{contract_id}/parties/{party_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_party(
    contract_id: str,
    party_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:update")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    delete_contract_party(db, contract=contract, party_id=party_id)


@router.get("/{contract_id}/signers", response_model=list[SignerOption])
def list_signers(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    """Valid signature recipients — contract parties + active org users."""
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return list_signer_options(db, contract=contract)


@router.get("/{contract_id}/stage-history", response_model=list[ContractStageHistoryResponse])
def get_stage_history(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return list_contract_stage_history(db, contract=contract)


@router.get("/{contract_id}/activity", response_model=list[ContractActivityResponse])
def get_contract_activity(
    contract_id: str,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    return list_contract_activity(db, contract=contract, limit=limit)


