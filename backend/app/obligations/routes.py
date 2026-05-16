from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.service import get_contract_for_user
from app.core.audit import write_audit_log, write_timeline_event
from app.core.database import utcnow
from app.core.deps import get_db, require_permission
from app.integrations.resend import resend_client
from app.jobs.models import JobRun
from app.jobs.service import create_job, dispatch_job
from app.obligations.models import Obligation, ObligationReminder

router = APIRouter(prefix="/obligations", tags=["obligations"])

DUE_SOON_DAYS = 7


class ObligationUpdate(BaseModel):
    owner_user_id: str | None = None
    responsible_party: str | None = None
    obligation_type: str | None = None
    status: str | None = Field(default=None, pattern="^(open|due_soon|overdue|completed|cancelled)$")
    due_date: date | None = None
    recurrence: str | None = None


def _get_obligation(db: Session, *, obligation_id: str, current_user: User) -> Obligation:
    ob = db.get(Obligation, obligation_id)
    if ob is None or ob.org_id != current_user.org_id or ob.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Obligation not found")
    return ob


@router.get("")
def list_obligations(
    contract_id: str | None = None,
    status_filter: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("obligation:read")),
):
    query = select(Obligation).where(
        Obligation.org_id == current_user.org_id,
        Obligation.deleted_at.is_(None),
    )
    if contract_id:
        query = query.where(Obligation.contract_id == contract_id)
    if status_filter:
        query = query.where(Obligation.status == status_filter)
    return db.scalars(query.order_by(Obligation.due_date.asc())).all()


@router.get("/{obligation_id}")
def get_obligation(
    obligation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("obligation:read")),
):
    return _get_obligation(db, obligation_id=obligation_id, current_user=current_user)


@router.patch("/{obligation_id}")
def update_obligation(
    obligation_id: str,
    payload: ObligationUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("obligation:update")),
):
    ob = _get_obligation(db, obligation_id=obligation_id, current_user=current_user)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(ob, key, value)
    ob.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="obligation.updated",
        resource_type="obligation",
        resource_id=ob.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        after=payload.model_dump(exclude_unset=True, mode="json"),
    )
    db.commit()
    db.refresh(ob)
    return ob


@router.post("/{obligation_id}/complete")
def complete_obligation(
    obligation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("obligation:update")),
):
    ob = _get_obligation(db, obligation_id=obligation_id, current_user=current_user)
    ob.status = "completed"
    ob.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="obligation.completed",
        resource_type="obligation",
        resource_id=ob.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        after={"contract_id": ob.contract_id},
    )
    db.commit()
    db.refresh(ob)
    return ob


@router.post("/extract", status_code=status.HTTP_202_ACCEPTED)
def trigger_obligation_extraction(
    contract_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("obligation:update")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    from app.contract_files.models import ContractTextSnapshot, ContractVersion

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
    if version is None or snapshot is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Contract has no extractable authoritative version")
    job = create_job(
        db,
        org_id=current_user.org_id,
        job_type="obligation_extraction",
        resource_type="contract",
        resource_id=contract.id,
        created_by_user_id=current_user.id,
        idempotency_key=f"obligation_extraction:{version.id}:{snapshot.id}:manual:{utcnow().timestamp()}",
        metadata={"contract_version_id": version.id, "text_snapshot_id": snapshot.id},
    )
    db.commit()
    job = db.get(JobRun, job.id)
    dispatch_job(db, job=job)
    db.commit()
    return {"job_id": job.id, "status": job.status}


@router.post("/run-reminders")
async def run_obligation_reminders(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("obligation:update")),
):
    today = utcnow().date()
    # Recompute overdue / due-soon statuses for open obligations.
    open_obligations = db.scalars(
        select(Obligation).where(
            Obligation.org_id == current_user.org_id,
            Obligation.deleted_at.is_(None),
            Obligation.status.in_(["open", "due_soon", "overdue"]),
            Obligation.due_date.is_not(None),
        )
    ).all()
    overdue = due_soon = 0
    for ob in open_obligations:
        if ob.due_date < today:
            ob.status = "overdue"
            overdue += 1
        elif ob.due_date <= today + timedelta(days=DUE_SOON_DAYS):
            ob.status = "due_soon"
            due_soon += 1
        else:
            ob.status = "open"

    due_reminders = db.scalars(
        select(ObligationReminder).where(
            ObligationReminder.org_id == current_user.org_id,
            ObligationReminder.sent_at.is_(None),
            ObligationReminder.remind_at <= today,
        )
    ).all()
    sent = 0
    for reminder in due_reminders:
        ob = db.get(Obligation, reminder.obligation_id)
        if ob is None or ob.deleted_at is not None or ob.status in {"completed", "cancelled"}:
            continue
        owner = db.get(User, ob.owner_user_id) if ob.owner_user_id else None
        if owner is not None:
            await resend_client.send_email(
                to=owner.email,
                subject=f"Obligation due: {ob.obligation_type or 'contract obligation'}",
                html=f"<p>Reminder: <b>{ob.description[:300]}</b> (due {ob.due_date}).</p>",
            )
        reminder.sent_at = today
        reminder.updated_by_user_id = current_user.id
        sent += 1
    write_audit_log(
        db,
        action="obligation.reminders_run",
        resource_type="org",
        resource_id=current_user.org_id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        request_id=getattr(request.state, "request_id", None),
        after={"reminders_sent": sent, "overdue": overdue, "due_soon": due_soon},
    )
    db.commit()
    return {"reminders_sent": sent, "marked_overdue": overdue, "marked_due_soon": due_soon}
