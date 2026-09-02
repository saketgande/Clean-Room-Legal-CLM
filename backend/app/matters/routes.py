from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approvals.models import ApprovalRequest
from app.auth.models import User
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.audit import write_audit_log, write_timeline_event
from app.core.database import utcnow
from app.core.deps import get_db, require_permission
from app.intake.models import IntakeRequest
from app.matters.access import get_project_for_user, project_scope_query
from app.matters.models import (
    Matter,
    MatterActivity,
    MatterContract,
    MatterFolder,
    MatterMember,
    MatterShare,
)
from app.matters.schemas import (
    AssignItemRequest,
    MatterContractAdd,
    MatterContractResponse,
    MatterContractUpdate,
    MatterCreate,
    MatterFolderCreate,
    MatterFolderResponse,
    MatterFolderUpdate,
    MatterMemberResponse,
    MatterMemberUpsert,
    MatterOverview,
    MatterOverviewCounts,
    MatterResponse,
    MatterRollupItem,
    MatterShareCreate,
    MatterShareResponse,
    MatterUpdate,
    UnfiledItem,
)
from app.notices.models import Notice
from app.obligations.models import Obligation

# No prefix here — main.py mounts this at both /matters (canonical) and /projects
# (deprecated alias, kept until the Matters UI ships).
router = APIRouter(tags=["matters"])


@router.get("", response_model=list[MatterResponse])
def list_projects(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:read")),
):
    return db.scalars(project_scope_query(db, user=current_user).order_by(Matter.updated_at.desc())).all()


def _get_project(db: Session, *, matter_id: str, current_user, access: str = "read") -> Matter:
    return get_project_for_user(db, matter_id=matter_id, user=current_user, access=access)


def _next_matter_number(db: Session, *, org_id: str) -> str:
    """Per-org sequential matter number: M-1000, M-1001, ...
    ponytail: lexical max over 4-digit numbers; revisit the format past M-9999."""
    last = db.scalar(
        select(Matter.matter_number)
        .where(Matter.org_id == org_id, Matter.matter_number.like("M-%"))
        .order_by(Matter.matter_number.desc())
        .limit(1)
    )
    n = 1000
    if last and last[2:].isdigit():
        n = int(last[2:]) + 1
    return f"M-{n:04d}"


def _get_project_folder(
    db: Session,
    *,
    folder_id: str | None,
    matter_id: str,
    org_id: str,
    required: bool = False,
) -> MatterFolder | None:
    if folder_id is None:
        if required:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
        return None
    folder = db.get(MatterFolder, folder_id)
    if (
        folder is None
        or folder.org_id != org_id
        or folder.matter_id != matter_id
        or folder.deleted_at is not None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    return folder


def _record_project_activity(
    db: Session,
    *,
    project: Matter,
    actor_user_id: str,
    activity_type: str,
    title: str,
    details: dict | None = None,
) -> None:
    db.add(
        MatterActivity(
            org_id=project.org_id,
            matter_id=project.id,
            actor_user_id=actor_user_id,
            activity_type=activity_type,
            title=title,
            details=details,
            occurred_at=utcnow(),
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
    )


@router.post("", response_model=MatterResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: MatterCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:create")),
):
    project = Matter(
        org_id=current_user.org_id,
        name=payload.name,
        description=payload.description,
        matter_type=payload.matter_type,
        owner_user_id=current_user.id,
        matter_number=_next_matter_number(db, org_id=current_user.org_id),
        client_name=payload.client_name,
        status=payload.status,
        opened_at=utcnow(),
        metadata_json=payload.metadata_json,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(project)
    db.flush()
    db.add(
        MatterMember(
            org_id=current_user.org_id,
            matter_id=project.id,
            user_id=current_user.id,
            role="owner",
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
    )
    write_audit_log(
        db,
        action="project.created",
        resource_type="project",
        resource_id=project.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
    )
    write_timeline_event(
        db,
        org_id=current_user.org_id,
        resource_type="project",
        resource_id=project.id,
        event_type="project.created",
        title="Matter created",
        actor_user_id=current_user.id,
    )
    db.commit()
    db.refresh(project)
    return project


@router.get("/unfiled", response_model=list[UnfiledItem])
def unfiled_items(
    item_type: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:read")),
):
    """Contracts and intake requests not yet filed under any matter — the
    'awaiting a matter' tray. Registered before /{matter_id} so 'unfiled' isn't
    captured as a matter id."""
    org_id = current_user.org_id
    out: list[UnfiledItem] = []
    if item_type in (None, "contract"):
        contracts = db.scalars(
            select(Contract)
            .where(
                Contract.org_id == org_id,
                Contract.matter_id.is_(None),
                Contract.deleted_at.is_(None),
            )
            .order_by(Contract.updated_at.desc())
            .limit(50)
        ).all()
        # Cheap suggestion: a matter whose client/name matches the contract's
        # counterparty (case-insensitive substring). Deterministic, not an LLM.
        matters = db.scalars(
            project_scope_query(db, user=current_user)
        ).all()
        for c in contracts:
            sid = slabel = None
            cp = (c.counterparty_name or "").strip().lower()
            if cp:
                for m in matters:
                    hay = f"{m.client_name or ''} {m.name or ''}".lower()
                    if cp in hay or (m.client_name and m.client_name.lower() in cp):
                        sid = m.id
                        slabel = f"{m.matter_number + ' · ' if m.matter_number else ''}{m.name}"
                        break
            out.append(
                UnfiledItem(
                    id=c.id,
                    kind="contract",
                    title=c.title,
                    subtitle=c.counterparty_name,
                    suggested_matter_id=sid,
                    suggested_matter_label=slabel,
                )
            )
    if item_type in (None, "intake"):
        reqs = db.scalars(
            select(IntakeRequest)
            .where(
                IntakeRequest.org_id == org_id,
                IntakeRequest.matter_id.is_(None),
                IntakeRequest.status.in_(("open", "escalated")),
            )
            .order_by(IntakeRequest.created_at.desc())
            .limit(50)
        ).all()
        out += [
            UnfiledItem(id=r.id, kind="intake", title=r.subject or r.type_label, subtitle=r.ref)
            for r in reqs
        ]
    return out


@router.get("/{matter_id}/overview", response_model=MatterOverview)
def matter_overview(
    matter_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:read")),
):
    """The matter hub: counts + recent items rolled up across the matter's
    contracts (obligations/notices/approvals derive through them) and its
    intake requests."""
    matter = _get_project(db, matter_id=matter_id, current_user=current_user)
    org_id = current_user.org_id
    today = date.today()

    contracts = db.scalars(
        select(Contract)
        .where(
            Contract.org_id == org_id,
            Contract.matter_id == matter.id,
            Contract.deleted_at.is_(None),
        )
        .order_by(Contract.updated_at.desc())
    ).all()
    cids = [c.id for c in contracts]

    obligations = (
        db.scalars(
            select(Obligation)
            .where(Obligation.org_id == org_id, Obligation.contract_id.in_(cids))
            .order_by(Obligation.due_date.asc())
        ).all()
        if cids
        else []
    )
    notices = (
        db.scalars(
            select(Notice)
            .where(Notice.org_id == org_id, Notice.contract_id.in_(cids))
            .order_by(Notice.created_at.desc())
        ).all()
        if cids
        else []
    )
    approvals = (
        db.scalars(
            select(ApprovalRequest)
            .where(ApprovalRequest.org_id == org_id, ApprovalRequest.contract_id.in_(cids))
            .order_by(ApprovalRequest.created_at.desc())
        ).all()
        if cids
        else []
    )
    intake = db.scalars(
        select(IntakeRequest)
        .where(IntakeRequest.org_id == org_id, IntakeRequest.matter_id == matter.id)
        .order_by(IntakeRequest.created_at.desc())
    ).all()

    counts = MatterOverviewCounts(
        contracts=len(contracts),
        contracts_active=sum(1 for c in contracts if not c.archived),
        obligations_open=sum(1 for o in obligations if o.status == "open"),
        obligations_overdue=sum(
            1 for o in obligations if o.status == "open" and o.due_date and o.due_date < today
        ),
        approvals_pending=sum(1 for a in approvals if a.status == "pending"),
        notices_open=sum(1 for n in notices if n.status == "open"),
        intake_open=sum(1 for r in intake if r.status in ("open", "escalated")),
    )
    return MatterOverview(
        matter=MatterResponse.model_validate(matter),
        counts=counts,
        contracts=[
            MatterRollupItem(
                id=c.id, title=c.title, status=c.lifecycle_stage, meta=c.counterparty_name
            )
            for c in contracts[:8]
        ],
        obligations=[
            MatterRollupItem(
                id=o.id,
                title=(o.description or o.obligation_type or "Obligation")[:90],
                status=o.status,
                meta=str(o.due_date) if o.due_date else None,
            )
            for o in obligations[:8]
        ],
        notices=[
            MatterRollupItem(id=n.id, title=n.subject, status=n.status) for n in notices[:6]
        ],
        approvals=[
            MatterRollupItem(id=a.id, title="Approval request", status=a.status)
            for a in approvals[:6]
        ],
        intake=[
            MatterRollupItem(
                id=r.id, title=r.subject or r.type_label, status=r.status, meta=r.ref
            )
            for r in intake[:6]
        ],
    )


@router.get("/{matter_id}/activity")
def matter_activity(
    matter_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:read")),
):
    """Recent events on this matter (assignments, folders, etc.)."""
    matter = _get_project(db, matter_id=matter_id, current_user=current_user)
    rows = db.scalars(
        select(MatterActivity)
        .where(MatterActivity.org_id == current_user.org_id, MatterActivity.matter_id == matter.id)
        .order_by(MatterActivity.occurred_at.desc().nullslast())
        .limit(50)
    ).all()
    return [
        {
            "id": r.id,
            "activity_type": r.activity_type,
            "title": r.title,
            "occurred_at": r.occurred_at,
        }
        for r in rows
    ]


@router.post("/{matter_id}/items", status_code=status.HTTP_200_OK)
def assign_item_to_matter(
    matter_id: str,
    payload: AssignItemRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    """File a contract or intake request under this matter (one matter -> many)."""
    matter = _get_project(
        db, matter_id=matter_id, current_user=current_user, access="update"
    )
    org_id = current_user.org_id
    if payload.item_type == "contract":
        obj = db.get(Contract, payload.item_id)
        if obj is None or obj.org_id != org_id or obj.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")
        obj.matter_id = matter.id
        title = obj.title
    elif payload.item_type == "intake":
        obj = db.get(IntakeRequest, payload.item_id)
        if obj is None or obj.org_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
        obj.matter_id = matter.id
        title = obj.subject or obj.type_label
    else:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "item_type must be 'contract' or 'intake'"
        )
    obj.updated_by_user_id = current_user.id
    _record_project_activity(
        db,
        project=matter,
        actor_user_id=current_user.id,
        activity_type="item.assigned",
        title=f"Filed {payload.item_type} under matter: {title}"[:255],
        details={"item_type": payload.item_type, "item_id": payload.item_id},
    )
    db.commit()
    return {
        "status": "assigned",
        "matter_id": matter.id,
        "item_type": payload.item_type,
        "item_id": payload.item_id,
    }


@router.get("/{matter_id}", response_model=MatterResponse)
def get_project(
    matter_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:read")),
):
    return _get_project(db, matter_id=matter_id, current_user=current_user)


@router.patch("/{matter_id}", response_model=MatterResponse)
def update_project(
    matter_id: str,
    payload: MatterUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    project = _get_project(
        db, matter_id=matter_id, current_user=current_user, access="update"
    )
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    project.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="project.updated",
        resource_type="project",
        resource_id=project.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
    )
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{matter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    matter_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:delete")),
):
    project = _get_project(
        db, matter_id=matter_id, current_user=current_user, access="update"
    )
    project.deleted_at = utcnow()
    project.deleted_by_user_id = current_user.id
    project.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="project.deleted",
        resource_type="project",
        resource_id=project.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
    )
    db.commit()


@router.get("/{matter_id}/folders", response_model=list[MatterFolderResponse])
def list_project_folders(
    matter_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:read")),
):
    _get_project(db, matter_id=matter_id, current_user=current_user)
    return db.scalars(
        select(MatterFolder)
        .where(
            MatterFolder.org_id == current_user.org_id,
            MatterFolder.matter_id == matter_id,
            MatterFolder.deleted_at.is_(None),
        )
        .order_by(MatterFolder.name.asc())
    ).all()


@router.post(
    "/{matter_id}/folders",
    response_model=MatterFolderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project_folder(
    matter_id: str,
    payload: MatterFolderCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    project = _get_project(
        db, matter_id=matter_id, current_user=current_user, access="update"
    )
    _get_project_folder(
        db,
        folder_id=payload.parent_folder_id,
        matter_id=matter_id,
        org_id=current_user.org_id,
    )
    folder = MatterFolder(
        org_id=current_user.org_id,
        matter_id=matter_id,
        parent_folder_id=payload.parent_folder_id,
        name=payload.name,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(folder)
    db.flush()
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.folder_created",
        title="Matter folder created",
        details={"folder_id": folder.id, "name": folder.name},
    )
    write_audit_log(
        db,
        action="project.folder_created",
        resource_type="matter_folder",
        resource_id=folder.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={"matter_id": project.id},
    )
    db.commit()
    db.refresh(folder)
    return folder


@router.patch("/{matter_id}/folders/{folder_id}", response_model=MatterFolderResponse)
def update_project_folder(
    matter_id: str,
    folder_id: str,
    payload: MatterFolderUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    project = _get_project(
        db, matter_id=matter_id, current_user=current_user, access="update"
    )
    folder = _get_project_folder(
        db,
        folder_id=folder_id,
        matter_id=matter_id,
        org_id=current_user.org_id,
        required=True,
    )
    updates = payload.model_dump(exclude_unset=True)
    if "parent_folder_id" in updates:
        if updates["parent_folder_id"] == folder_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Folder cannot be its own parent")
        _get_project_folder(
            db,
            folder_id=updates["parent_folder_id"],
            matter_id=matter_id,
            org_id=current_user.org_id,
        )
    for key, value in updates.items():
        setattr(folder, key, value)
    folder.updated_by_user_id = current_user.id
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.folder_updated",
        title="Matter folder updated",
        details={"folder_id": folder.id},
    )
    db.commit()
    db.refresh(folder)
    return folder


@router.delete("/{matter_id}/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_folder(
    matter_id: str,
    folder_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    project = _get_project(
        db, matter_id=matter_id, current_user=current_user, access="update"
    )
    folder = _get_project_folder(
        db,
        folder_id=folder_id,
        matter_id=matter_id,
        org_id=current_user.org_id,
        required=True,
    )
    folder.deleted_at = utcnow()
    folder.deleted_by_user_id = current_user.id
    folder.updated_by_user_id = current_user.id
    for matter_contract in db.scalars(
        select(MatterContract).where(
            MatterContract.org_id == current_user.org_id,
            MatterContract.matter_id == matter_id,
            MatterContract.folder_id == folder_id,
        )
    ):
        matter_contract.folder_id = None
        matter_contract.updated_by_user_id = current_user.id
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.folder_deleted",
        title="Matter folder deleted",
        details={"folder_id": folder.id},
    )
    db.commit()


@router.get("/{matter_id}/members", response_model=list[MatterMemberResponse])
def list_project_members(
    matter_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:read")),
):
    _get_project(db, matter_id=matter_id, current_user=current_user)
    return db.scalars(
        select(MatterMember)
        .where(MatterMember.org_id == current_user.org_id, MatterMember.matter_id == matter_id)
        .order_by(MatterMember.created_at.asc())
    ).all()


@router.put("/{matter_id}/members", response_model=MatterMemberResponse)
def upsert_project_member(
    matter_id: str,
    payload: MatterMemberUpsert,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:share")),
):
    project = _get_project(
        db, matter_id=matter_id, current_user=current_user, access="share"
    )
    user = db.get(User, payload.user_id)
    if user is None or user.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    member = db.scalar(
        select(MatterMember).where(
            MatterMember.org_id == current_user.org_id,
            MatterMember.matter_id == matter_id,
            MatterMember.user_id == payload.user_id,
        )
    )
    if member is None:
        member = MatterMember(
            org_id=current_user.org_id,
            matter_id=matter_id,
            user_id=payload.user_id,
            role=payload.role,
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
        db.add(member)
        action = "project.member_added"
    else:
        member.role = payload.role
        member.updated_by_user_id = current_user.id
        action = "project.member_updated"
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type=action,
        title="Matter member updated",
        details={"user_id": payload.user_id, "role": payload.role},
    )
    write_audit_log(
        db,
        action=action,
        resource_type="project",
        resource_id=project.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={"user_id": payload.user_id, "role": payload.role},
    )
    db.commit()
    db.refresh(member)
    return member


@router.delete("/{matter_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_project_member(
    matter_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:share")),
):
    project = _get_project(
        db, matter_id=matter_id, current_user=current_user, access="share"
    )
    member = db.scalar(
        select(MatterMember).where(
            MatterMember.org_id == current_user.org_id,
            MatterMember.matter_id == matter_id,
            MatterMember.user_id == user_id,
        )
    )
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Matter member not found")
    if user_id == project.owner_user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Matter owner cannot be removed")
    db.delete(member)
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.member_removed",
        title="Matter member removed",
        details={"user_id": user_id},
    )
    db.commit()


@router.get("/{matter_id}/shares", response_model=list[MatterShareResponse])
def list_project_shares(
    matter_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:share")),
):
    _get_project(db, matter_id=matter_id, current_user=current_user, access="share")
    return db.scalars(
        select(MatterShare)
        .where(
            MatterShare.org_id == current_user.org_id,
            MatterShare.matter_id == matter_id,
            MatterShare.revoked_at.is_(None),
        )
        .order_by(MatterShare.created_at.desc())
    ).all()


@router.post(
    "/{matter_id}/shares",
    response_model=MatterShareResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project_share(
    matter_id: str,
    payload: MatterShareCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:share")),
):
    project = _get_project(
        db, matter_id=matter_id, current_user=current_user, access="share"
    )
    user = db.get(User, payload.user_id)
    if user is None or user.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    share = MatterShare(
        org_id=current_user.org_id,
        matter_id=matter_id,
        shared_with_user_id=payload.user_id,
        access_level=payload.access_level,
        expires_at=payload.expires_at,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
    )
    db.add(share)
    db.flush()
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.share_created",
        title="Matter share created",
        details={"share_id": share.id, "user_id": payload.user_id, "access_level": payload.access_level},
    )
    write_audit_log(
        db,
        action="project.share_created",
        resource_type="matter_share",
        resource_id=share.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={"matter_id": matter_id, "user_id": payload.user_id, "access_level": payload.access_level},
    )
    db.commit()
    db.refresh(share)
    return share


@router.post("/{matter_id}/shares/{share_id}/revoke", response_model=MatterShareResponse)
def revoke_project_share(
    matter_id: str,
    share_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:share")),
):
    project = _get_project(
        db, matter_id=matter_id, current_user=current_user, access="share"
    )
    share = db.get(MatterShare, share_id)
    if (
        share is None
        or share.org_id != current_user.org_id
        or share.matter_id != matter_id
        or share.revoked_at is not None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Matter share not found")
    share.revoked_at = utcnow()
    share.updated_by_user_id = current_user.id
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.share_revoked",
        title="Matter share revoked",
        details={"share_id": share.id, "user_id": share.shared_with_user_id},
    )
    write_audit_log(
        db,
        action="project.share_revoked",
        resource_type="matter_share",
        resource_id=share.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={"matter_id": matter_id, "user_id": share.shared_with_user_id},
    )
    db.commit()
    db.refresh(share)
    return share


@router.get("/{matter_id}/contracts", response_model=list[MatterContractResponse])
def list_project_contracts(
    matter_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:read")),
):
    _get_project(db, matter_id=matter_id, current_user=current_user)
    return db.scalars(
        select(MatterContract)
        .where(MatterContract.org_id == current_user.org_id, MatterContract.matter_id == matter_id)
        .order_by(MatterContract.created_at.asc())
    ).all()


@router.put("/{matter_id}/contracts", response_model=MatterContractResponse)
def add_project_contract(
    matter_id: str,
    payload: MatterContractAdd,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    project = _get_project(
        db, matter_id=matter_id, current_user=current_user, access="update"
    )
    get_contract_for_user(db, contract_id=payload.contract_id, user=current_user)
    _get_project_folder(
        db,
        folder_id=payload.folder_id,
        matter_id=matter_id,
        org_id=current_user.org_id,
    )
    row = db.scalar(
        select(MatterContract).where(
            MatterContract.org_id == current_user.org_id,
            MatterContract.matter_id == matter_id,
            MatterContract.contract_id == payload.contract_id,
        )
    )
    if row is None:
        row = MatterContract(
            org_id=current_user.org_id,
            matter_id=matter_id,
            contract_id=payload.contract_id,
            folder_id=payload.folder_id,
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        )
        db.add(row)
        activity_type = "project.contract_added"
    else:
        row.folder_id = payload.folder_id
        row.updated_by_user_id = current_user.id
        activity_type = "project.contract_moved"
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type=activity_type,
        title="Matter contract updated",
        details={"contract_id": payload.contract_id, "folder_id": payload.folder_id},
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{matter_id}/contracts/{contract_id}", response_model=MatterContractResponse)
def update_project_contract(
    matter_id: str,
    contract_id: str,
    payload: MatterContractUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    project = _get_project(
        db, matter_id=matter_id, current_user=current_user, access="update"
    )
    _get_project_folder(
        db,
        folder_id=payload.folder_id,
        matter_id=matter_id,
        org_id=current_user.org_id,
    )
    row = db.scalar(
        select(MatterContract).where(
            MatterContract.org_id == current_user.org_id,
            MatterContract.matter_id == matter_id,
            MatterContract.contract_id == contract_id,
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Matter contract not found")
    row.folder_id = payload.folder_id
    row.updated_by_user_id = current_user.id
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.contract_moved",
        title="Matter contract moved",
        details={"contract_id": contract_id, "folder_id": payload.folder_id},
    )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{matter_id}/contracts/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_project_contract(
    matter_id: str,
    contract_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("project:update")),
):
    project = _get_project(
        db, matter_id=matter_id, current_user=current_user, access="update"
    )
    row = db.scalar(
        select(MatterContract).where(
            MatterContract.org_id == current_user.org_id,
            MatterContract.matter_id == matter_id,
            MatterContract.contract_id == contract_id,
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Matter contract not found")
    db.delete(row)
    _record_project_activity(
        db,
        project=project,
        actor_user_id=current_user.id,
        activity_type="project.contract_removed",
        title="Matter contract removed",
        details={"contract_id": contract_id},
    )
    db.commit()
