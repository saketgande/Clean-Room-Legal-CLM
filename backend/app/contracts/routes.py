import logging

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.contract_files.dependencies import get_contract_files_service
from app.contract_files.service import ContractFilesService
from app.contracts.dependencies import (
    get_contract_lifecycle_service,
    get_contract_service,
)
from app.contracts.lifecycle import ContractLifecycleService, allowed_transitions_for
from app.contracts.risk import contract_risk, current_open_findings, refresh_contract_risk
from app.contracts.schemas import (
    ContractActivityResponse,
    ContractPartyCreate,
    ContractPartyResponse,
    ContractResponse,
    ContractStageHistoryResponse,
    ContractUpdate,
    ContractUploadResponse,
    CounterpartyOption,
    LifecycleOptionsResponse,
    LifecycleTransitionRequest,
    ReviewRetryResponse,
    ReviewStatusResponse,
    SignerOption,
    VersionDiffResponse,
)
from app.contracts.service import (
    ContractService,
    list_counterparty_directory,
)
from app.core.audit import write_audit_log, write_timeline_event
from app.core.config import settings
from app.core.deps import get_db, require_permission, require_screen_level
from app.core.rate_limit import limiter
from app.core.rbac import has_permission
from app.integrations.claude import ClaudeProvider
from app.integrations.dependencies import get_claude_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contracts", tags=["contracts"])

_CONTRACTS_VIEW = require_screen_level("contracts", "VIEW")
_CONTRACTS_ADD = require_screen_level("contracts", "ADD")
_CONTRACTS_EDIT = require_screen_level("contracts", "EDIT")
_CONTRACTS_DELETE = require_screen_level("contracts", "DELETE")


@router.get("", response_model=list[ContractResponse])
def list_contracts(
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:read")),
):
    # Optional pagination; defaults preserve the historical "first 100, newest
    # first" behaviour so existing callers/tests see an unchanged list shape.
    return service.list_contracts_for_user(user=current_user, limit=limit, offset=offset)


@router.get("/counterparties", response_model=list[CounterpartyOption])
def counterparty_directory(
    q: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    return list_counterparty_directory(db, org_id=current_user.org_id, q=q)


@router.post(
    "/upload",
    response_model=ContractUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit(settings.rate_limit_contract_upload)
async def upload_contract(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    counterparty_name: str | None = Form(default=None),
    files_service: ContractFilesService = Depends(get_contract_files_service),
    current_user=Depends(require_permission("contract:create")),
    _screen=Depends(_CONTRACTS_ADD),
):
    return await files_service.create_contract_from_upload(
        upload=file,
        user=current_user,
        title=title,
        counterparty_name=counterparty_name,
        request_id=getattr(request.state, "request_id", None),
        defer_processing=True,  # text extraction, OCR and metadata run in a background job
    )


@router.get("/{contract_id}", response_model=ContractResponse)
def get_contract(
    contract_id: str,
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:read")),
):
    return service.get_contract_for_user(contract_id=contract_id, user=current_user)


@router.get("/{contract_id}/risk")
def get_contract_risk(
    contract_id: str,
    db: Session = Depends(get_db),
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:read")),
):
    """The risk band computed now from the playbook review of the current text, with
    the findings behind it (contracts.risk.contract_risk). Never the cached column,
    which a missed refresh could leave describing older text."""
    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    return contract_risk(db, contract)


@router.get("/{contract_id}/deviations")
def get_contract_deviations(
    contract_id: str,
    db: Session = Depends(get_db),
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:read")),
):
    """Open playbook deviations for the contract, severity-sorted — powers the
    on-ticket AI analysis view and the editor's Review tab.

    Only the findings of the review that counts for the current text
    (contracts.risk.current_open_findings), the same set the risk band is computed from.
    Listing every open row of every review ever run showed each unfixed problem twice
    after a re-check, next to a header that counted it once."""
    from app.playbooks.schemas import PlaybookDeviationResponse

    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    rows = current_open_findings(db, contract)
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    rows = sorted(rows, key=lambda d: rank.get((d.severity or "").lower(), 9))
    return [PlaybookDeviationResponse.model_validate(d).model_dump() for d in rows]


@router.get("/{contract_id}/plain-summary")
async def contract_plain_summary(
    contract_id: str,
    db: Session = Depends(get_db),
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:read")),
    claude_client: ClaudeProvider = Depends(get_claude_client),
):
    """A plain-English, non-lawyer-readable summary of the AI review (risk + playbook
    deviations). Best-effort — degrades to a deterministic summary; never raises."""
    from sqlalchemy import select as _select

    from app.ai.gateway import AICallContext, gateway_for
    from app.core.config import settings
    from app.playbooks.models import PlaybookDeviation

    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    risk = contract_risk(db, contract)
    band = risk["band"]
    counts = risk["counts"]
    # The band's own findings, at the severity the band used: every version's open rows
    # (the old query) mixed in stale issues and dropped the conceded highs that still
    # drive the band, so the summary could disagree with the risk it states.
    rows = {
        d.id: d
        for d in db.scalars(
            _select(PlaybookDeviation).where(
                PlaybookDeviation.org_id == current_user.org_id,
                PlaybookDeviation.id.in_([d["deviation_id"] for d in risk["drivers"]]),
            )
        ).all()
    } if risk["drivers"] else {}

    def _fallback() -> str:
        if band == "unknown":
            # Nothing reviewed this text, so nothing here may read as "safe".
            return (f"Risk not assessed: {risk['note']} Until a playbook review of this text "
                    "runs, nothing here says it is safe to sign.")
        hi = counts.get("critical", 0) + counts.get("high", 0)
        md, lo = counts.get("medium", 0), counts.get("low", 0)
        if not risk["drivers"]:
            return (f"Overall {band} risk. The playbook review of this text found no "
                    "unresolved issues.")
        head = f"Overall {band} risk — {hi} important, {md} moderate and {lo} minor point(s) worth knowing before signing."
        # Drivers include conceded issues: the wording is still in the contract.
        pts = "\n".join(
            f"• {d['issue']}" + (" (conceded — the wording stays)" if d["conceded"] else "")
            for d in risk["drivers"][:3]
        )
        tail = ("A lawyer should glance at the important items before you sign."
                if hi else "Minor tweaks only — safe to proceed once they're addressed.")
        return f"{head}\n\n{pts}\n\n{tail}"

    if settings.mock_claude or band == "unknown" or not risk["drivers"]:
        return {"summary": _fallback(), "generated": False}
    try:
        dev_lines = "\n".join(
            f"- [{d['severity']}] {getattr(rows.get(d['deviation_id']), 'clause_type', None) or 'clause'}: "
            f"{d['issue']}"
            + (" (conceded: the wording stays in the contract)" if d["conceded"] else "")
            + (f" (suggested fix: {rows[d['deviation_id']].suggested_fix})"
               if rows.get(d["deviation_id"]) and rows[d["deviation_id"]].suggested_fix else "")
            for d in risk["drivers"]
        )
        user_prompt = f"Contract: {contract.title}\nOverall risk: {band}.\n\nPlaybook deviations found:\n{dev_lines}"
        # Feature "plain_language_summary" via the AI gateway. An empty answer
        # raises into the except below, which keeps the deterministic summary.
        result = await gateway_for(claude_client).text(
            db, "plain_language_summary",
            ctx=AICallContext(org_id=current_user.org_id, user_id=current_user.id, resource=("contract", contract_id)),
            user_prompt=user_prompt,
            log_input={"contract_id": contract_id},
        )
        return {"summary": result.data, "generated": True}
    except Exception:
        # Degrading to the deterministic summary is intentional (never block a
        # lawyer from seeing risk info over an AI hiccup) — but doing so
        # silently meant an expired key or an outage was indistinguishable from
        # normal operation in the logs. Log loudly; still never raise.
        logger.warning("plain-language contract summary failed for %s — using fallback", contract_id, exc_info=True)
        return {"summary": _fallback(), "generated": False}


@router.post("/{contract_id}/risk")
def compute_contract_risk_route(
    contract_id: str,
    request: Request,
    db: Session = Depends(get_db),
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:read")),
    _screen=Depends(_CONTRACTS_VIEW),
):
    """Recompute the cached risk band from the playbook findings. Free: no AI call
    (the band is code over the review's findings; re-run the review to change them)."""
    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    before = contract.risk_band
    summary = refresh_contract_risk(db, contract)
    contract.updated_by_user_id = current_user.id
    request_id = getattr(request.state, "request_id", None)
    write_audit_log(
        db, action="contract.risk_recomputed", resource_type="contract", resource_id=contract.id,
        org_id=contract.org_id, actor_user_id=current_user.id, request_id=request_id,
        before={"band": before}, after={"band": summary["band"]},
    )
    write_timeline_event(
        db, org_id=contract.org_id, resource_type="contract", resource_id=contract.id,
        event_type="contract.risk_recomputed", title=f"Risk band recomputed: {summary['band']}",
        actor_user_id=current_user.id, request_id=request_id,
        details={"band": summary["band"], "note": summary["note"]},
    )
    db.commit()
    return summary


@router.patch("/{contract_id}", response_model=ContractResponse)
def update_contract(
    contract_id: str,
    payload: ContractUpdate,
    request: Request,
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:update")),
    _screen=Depends(_CONTRACTS_EDIT),
):
    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    return service.update_contract_metadata(
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
    service: ContractService = Depends(get_contract_service),
    lifecycle_service: ContractLifecycleService = Depends(get_contract_lifecycle_service),
    current_user=Depends(require_permission("contract:update")),
    _screen=Depends(_CONTRACTS_EDIT),
):
    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    lifecycle_service.transition_contract_stage(
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
    service.db.commit()
    service.db.refresh(contract)
    return contract


@router.get("/{contract_id}/lifecycle", response_model=LifecycleOptionsResponse)
def get_lifecycle_options(
    contract_id: str,
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:read")),
):
    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    from datetime import UTC, datetime

    from sqlalchemy import func as _func
    from sqlalchemy import select

    from app.contracts.lifecycle import parse_stage_slas
    from app.contracts.models import ContractStageHistory
    from app.core.config import settings as _settings

    entered = service.db.scalar(
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
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:read")),
):
    """Guided "what to do next" for a contract's review — derived from playbook
    deviations, pending redlines, open comments, and counterparty shares."""
    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    return service.compute_review_status(contract=contract)


@router.post("/{contract_id}/review/retry", response_model=ReviewRetryResponse)
async def retry_contract_review(
    contract_id: str,
    db: Session = Depends(get_db),
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:redline")),
):
    """Run the AI review again when it was interrupted (or never produced a usable review). Safe to
    press twice: while a review is in progress the second press returns it instead of starting
    another. If the contract's workflow stopped to ask a person because the review never finished,
    it is set going again to wait for this one."""
    from app.contracts import auto_review

    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    result = auto_review.retry_review(db, contract=contract, actor=current_user)
    resumed = False
    if result["status"] in ("queued", "already_running", "not_needed"):
        from app.workflows.service import reopen_review_step_for_contract

        try:
            resumed = await reopen_review_step_for_contract(db, contract=contract, actor=current_user)
        except Exception:
            # The review itself is queued; the workflow step can still be reopened from its own button.
            db.rollback()
            logger.warning("could not reopen the workflow's review step for %s", contract_id, exc_info=True)
    return {**result, "workflow_resumed": resumed}


@router.get(
    "/{contract_id}/versions/{base_version_id}/diff/{target_version_id}",
    response_model=VersionDiffResponse,
)
def get_version_diff(
    contract_id: str,
    base_version_id: str,
    target_version_id: str,
    context: int | None = Query(default=None, ge=0, le=50),
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:read")),
):
    """Line-level diff between two versions (e.g. yours vs the counterparty's).
    `context` keeps only that many unchanged lines around each change (see
    ContractService.compute_version_diff); left out, every line comes back."""
    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    return service.compute_version_diff(
        contract=contract,
        base_version_id=base_version_id,
        target_version_id=target_version_id,
        context=context,
    )


@router.get("/{contract_id}/parties", response_model=list[ContractPartyResponse])
def list_parties(
    contract_id: str,
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:read")),
):
    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    return service.list_contract_parties(contract=contract)


@router.post(
    "/{contract_id}/parties", response_model=ContractPartyResponse, status_code=status.HTTP_201_CREATED
)
def add_party(
    contract_id: str,
    payload: ContractPartyCreate,
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:update")),
    _screen=Depends(_CONTRACTS_ADD),
):
    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    return service.add_contract_party(
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
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:update")),
    _screen=Depends(_CONTRACTS_DELETE),
):
    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    service.delete_contract_party(contract=contract, party_id=party_id)


@router.get("/{contract_id}/signers", response_model=list[SignerOption])
def list_signers(
    contract_id: str,
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:read")),
):
    """Valid signature recipients — contract parties + active org users."""
    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    return service.list_signer_options(contract=contract)


@router.get("/{contract_id}/stage-history", response_model=list[ContractStageHistoryResponse])
def get_stage_history(
    contract_id: str,
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:read")),
):
    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    return service.list_contract_stage_history(contract=contract)


@router.get("/{contract_id}/activity", response_model=list[ContractActivityResponse])
def get_contract_activity(
    contract_id: str,
    limit: int = 100,
    service: ContractService = Depends(get_contract_service),
    current_user=Depends(require_permission("contract:read")),
):
    contract = service.get_contract_for_user(contract_id=contract_id, user=current_user)
    return service.list_contract_activity(contract=contract, limit=limit)
