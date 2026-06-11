from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.controller import ai_controller
from app.ai.schemas import PlaybookReviewOutput
from app.contracts.access import accessible_contract_filter
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.core.deps import get_db, require_permission
from app.core.enums import PlaybookStatus
from app.core.rbac import has_permission
from app.playbooks.models import (
    Playbook,
    PlaybookDeviation,
    PlaybookRule,
    PlaybookRun,
    PlaybookVersion,
)
from app.playbooks.schemas import (
    PlaybookCreate,
    PlaybookDecisionCreate,
    PlaybookDecisionResponse,
    PlaybookDeviationResponse,
    PlaybookGenerate,
    PlaybookPublishRequest,
    PlaybookResponse,
    PlaybookRuleCreate,
    PlaybookRuleResponse,
    PlaybookRuleUpdate,
    PlaybookRunCreate,
    PlaybookRunDetailResponse,
    PlaybookRunResponse,
    PlaybookUpdate,
    PlaybookVersionCreate,
    PlaybookVersionResponse,
)
from app.playbooks.service import (
    clone_playbook_version,
    create_initial_playbook,
    execute_playbook_run,
    generated_default_rules,
    get_playbook_for_user,
    get_playbook_version,
    select_run_version,
)

router = APIRouter(prefix="/playbooks", tags=["playbooks"])


@router.get("", response_model=list[PlaybookResponse])
def list_playbooks(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:read")),
):
    return db.scalars(
        select(Playbook)
        .where(Playbook.org_id == current_user.org_id, Playbook.deleted_at.is_(None))
        .order_by(Playbook.updated_at.desc())
    ).all()


@router.post("", response_model=PlaybookResponse, status_code=status.HTTP_201_CREATED)
def create_playbook(
    payload: PlaybookCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:create")),
):
    playbook = create_initial_playbook(
        db,
        user=current_user,
        name=payload.name,
        description=payload.description,
    )
    db.commit()
    db.refresh(playbook)
    return playbook


@router.post("/generate", response_model=PlaybookResponse, status_code=status.HTTP_201_CREATED)
def generate_playbook(
    payload: PlaybookGenerate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:create")),
):
    rules = generated_default_rules(
        contract_type=payload.contract_type,
        focus_areas=payload.focus_areas,
    )
    playbook = create_initial_playbook(
        db,
        user=current_user,
        name=payload.name,
        description=payload.description,
        generated_rules=rules,
    )
    db.commit()
    db.refresh(playbook)
    return playbook


@router.get("/runs/{run_id}", response_model=PlaybookRunDetailResponse)
def get_playbook_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:read")),
):
    run = db.get(PlaybookRun, run_id)
    if run is None or run.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Playbook run not found")
    get_contract_for_user(db, contract_id=run.contract_id, user=current_user)
    deviations = db.scalars(
        select(PlaybookDeviation)
        .where(PlaybookDeviation.org_id == current_user.org_id, PlaybookDeviation.playbook_run_id == run.id)
        .order_by(PlaybookDeviation.created_at.asc())
    ).all()
    return PlaybookRunDetailResponse.model_validate(run).model_copy(
        update={
            "deviations": [
                PlaybookDeviationResponse.model_validate(deviation) for deviation in deviations
            ]
        }
    )


@router.post(
    "/deviations/{deviation_id}/decisions",
    response_model=PlaybookDecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def decide_deviation(
    deviation_id: str,
    payload: PlaybookDecisionCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:run")),
):
    deviation = db.get(PlaybookDeviation, deviation_id)
    if deviation is None or deviation.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Playbook deviation not found")
    get_contract_for_user(db, contract_id=deviation.contract_id, user=current_user)
    from app.playbooks.service import record_deviation_decision

    decision = record_deviation_decision(
        db,
        deviation=deviation,
        decision=payload.decision,
        rationale=payload.rationale,
        user=current_user,
    )
    db.commit()
    db.refresh(decision)
    return decision


@router.get("/{playbook_id}", response_model=PlaybookResponse)
def get_playbook(
    playbook_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:read")),
):
    return get_playbook_for_user(db, playbook_id=playbook_id, user=current_user)


@router.patch("/{playbook_id}", response_model=PlaybookResponse)
def update_playbook(
    playbook_id: str,
    payload: PlaybookUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:update")),
):
    playbook = get_playbook_for_user(db, playbook_id=playbook_id, user=current_user)
    before = {"name": playbook.name, "description": playbook.description}
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(playbook, field, value)
    playbook.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="playbook.updated",
        resource_type="playbook",
        resource_id=playbook.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        before=before,
        after=updates,
    )
    db.commit()
    db.refresh(playbook)
    return playbook


@router.delete("/{playbook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_playbook(
    playbook_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:delete")),
):
    playbook = get_playbook_for_user(db, playbook_id=playbook_id, user=current_user)
    playbook.deleted_at = utcnow()
    playbook.deleted_by_user_id = current_user.id
    playbook.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="playbook.deleted",
        resource_type="playbook",
        resource_id=playbook.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
    )
    db.commit()


@router.get("/{playbook_id}/versions", response_model=list[PlaybookVersionResponse])
def list_playbook_versions(
    playbook_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:read")),
):
    playbook = get_playbook_for_user(db, playbook_id=playbook_id, user=current_user)
    return db.scalars(
        select(PlaybookVersion)
        .where(PlaybookVersion.org_id == current_user.org_id, PlaybookVersion.playbook_id == playbook.id)
        .order_by(PlaybookVersion.version_number.desc())
    ).all()


@router.post("/{playbook_id}/versions", response_model=PlaybookVersionResponse, status_code=status.HTTP_201_CREATED)
def create_playbook_version(
    playbook_id: str,
    payload: PlaybookVersionCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:update")),
):
    playbook = get_playbook_for_user(db, playbook_id=playbook_id, user=current_user)
    source_version = None
    if payload.source_version_id:
        source_version = get_playbook_version(
            db,
            playbook=playbook,
            version_id=payload.source_version_id,
            org_id=current_user.org_id,
        )
    elif playbook.current_version_id:
        source_version = get_playbook_version(
            db,
            playbook=playbook,
            version_id=playbook.current_version_id,
            org_id=current_user.org_id,
        )
    version = clone_playbook_version(
        db,
        playbook=playbook,
        user=current_user,
        source_version=source_version,
        summary=payload.summary,
    )
    db.commit()
    db.refresh(version)
    return version


@router.post("/{playbook_id}/publish", response_model=PlaybookResponse)
def publish_playbook(
    playbook_id: str,
    payload: PlaybookPublishRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:publish")),
):
    playbook = get_playbook_for_user(db, playbook_id=playbook_id, user=current_user)
    version_id = (payload.version_id if payload else None) or playbook.current_version_id
    if not version_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Playbook has no version to publish")
    version = get_playbook_version(db, playbook=playbook, version_id=version_id, org_id=current_user.org_id)
    rule_count = db.scalar(
        select(PlaybookRule.id)
        .where(PlaybookRule.org_id == current_user.org_id, PlaybookRule.playbook_version_id == version.id)
        .limit(1)
    )
    if rule_count is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot publish a playbook version with no rules")
    playbook.status = PlaybookStatus.PUBLISHED
    playbook.current_version_id = version.id
    playbook.updated_by_user_id = current_user.id
    version.status = PlaybookStatus.PUBLISHED
    version.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="playbook.published",
        resource_type="playbook",
        resource_id=playbook.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        after={"playbook_version_id": version.id, "version_number": version.version_number},
    )
    db.commit()
    db.refresh(playbook)
    return playbook


@router.get("/{playbook_id}/versions/{version_id}/rules", response_model=list[PlaybookRuleResponse])
def list_playbook_rules(
    playbook_id: str,
    version_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:read")),
):
    playbook = get_playbook_for_user(db, playbook_id=playbook_id, user=current_user)
    version = get_playbook_version(db, playbook=playbook, version_id=version_id, org_id=current_user.org_id)
    return db.scalars(
        select(PlaybookRule)
        .where(PlaybookRule.org_id == current_user.org_id, PlaybookRule.playbook_version_id == version.id)
        .order_by(PlaybookRule.created_at.asc())
    ).all()


@router.post(
    "/{playbook_id}/versions/{version_id}/rules",
    response_model=PlaybookRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_playbook_rule(
    playbook_id: str,
    version_id: str,
    payload: PlaybookRuleCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:update")),
):
    playbook = get_playbook_for_user(db, playbook_id=playbook_id, user=current_user)
    version = get_playbook_version(db, playbook=playbook, version_id=version_id, org_id=current_user.org_id)
    _ensure_draft_version(version)
    rule = PlaybookRule(
        org_id=current_user.org_id,
        playbook_version_id=version.id,
        created_by_user_id=current_user.id,
        updated_by_user_id=current_user.id,
        **payload.model_dump(),
    )
    db.add(rule)
    db.flush()
    write_audit_log(
        db,
        action="playbook.rule_created",
        resource_type="playbook_rule",
        resource_id=rule.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        after=payload.model_dump(),
    )
    db.commit()
    db.refresh(rule)
    return rule


@router.patch("/{playbook_id}/versions/{version_id}/rules/{rule_id}", response_model=PlaybookRuleResponse)
def update_playbook_rule(
    playbook_id: str,
    version_id: str,
    rule_id: str,
    payload: PlaybookRuleUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:update")),
):
    playbook = get_playbook_for_user(db, playbook_id=playbook_id, user=current_user)
    version = get_playbook_version(db, playbook=playbook, version_id=version_id, org_id=current_user.org_id)
    _ensure_draft_version(version)
    rule = _get_rule(db, rule_id=rule_id, version=version, org_id=current_user.org_id)
    before = PlaybookRuleResponse.model_validate(rule).model_dump()
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(rule, field, value)
    rule.updated_by_user_id = current_user.id
    write_audit_log(
        db,
        action="playbook.rule_updated",
        resource_type="playbook_rule",
        resource_id=rule.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        before=before,
        after=updates,
    )
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/{playbook_id}/versions/{version_id}/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_playbook_rule(
    playbook_id: str,
    version_id: str,
    rule_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:update")),
):
    playbook = get_playbook_for_user(db, playbook_id=playbook_id, user=current_user)
    version = get_playbook_version(db, playbook=playbook, version_id=version_id, org_id=current_user.org_id)
    _ensure_draft_version(version)
    rule = _get_rule(db, rule_id=rule_id, version=version, org_id=current_user.org_id)
    db.delete(rule)
    write_audit_log(
        db,
        action="playbook.rule_deleted",
        resource_type="playbook_rule",
        resource_id=rule.id,
        org_id=current_user.org_id,
        actor_user_id=current_user.id,
        metadata={"playbook_id": playbook.id, "playbook_version_id": version.id},
    )
    db.commit()


@router.post("/{playbook_id}/runs", response_model=PlaybookRunResponse, status_code=status.HTTP_201_CREATED)
async def run_playbook(
    playbook_id: str,
    payload: PlaybookRunCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:run")),
):
    playbook = get_playbook_for_user(db, playbook_id=playbook_id, user=current_user)
    _require_permission(current_user, "contract:read")
    if payload.create_redline:
        _require_permission(current_user, "contract:redline")
    contract = get_contract_for_user(db, contract_id=payload.contract_id, user=current_user)
    version = select_run_version(
        db,
        playbook=playbook,
        org_id=current_user.org_id,
        version_id=payload.playbook_version_id,
        test_mode=payload.test_mode,
    )
    rules = _rule_payloads_for_ai(db, org_id=current_user.org_id, playbook_version_id=version.id)
    ai_output = None
    ai_error = None
    if payload.use_ai:
        try:
            ai_output = await ai_controller.run_structured_skill(
                db,
                skill_name="playbook_review",
                org_id=current_user.org_id,
                created_by_user_id=current_user.id,
                input_payload={
                    "contract_id": contract.id,
                    "contract_version_id": contract.current_authoritative_version_id,
                    "playbook_id": playbook.id,
                    "playbook_version_id": version.id,
                    "rules": rules,
                },
                request_id=getattr(request.state, "request_id", None),
                resource_type="contract",
                resource_id=contract.id,
            )
            ai_output = PlaybookReviewOutput.model_validate(ai_output)
        except Exception as exc:
            ai_error = f"{exc.__class__.__name__}: {exc}"
    artifacts = execute_playbook_run(
        db,
        user=current_user,
        playbook=playbook,
        version=version,
        contract=contract,
        create_redline=payload.create_redline,
        ai_output=ai_output,
        ai_error=ai_error,
    )
    db.commit()
    db.refresh(artifacts.run)
    return artifacts.run


@router.get("/{playbook_id}/runs", response_model=list[PlaybookRunResponse])
def list_playbook_runs(
    playbook_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("playbook:read")),
):
    playbook = get_playbook_for_user(db, playbook_id=playbook_id, user=current_user)
    return db.scalars(
        select(PlaybookRun)
        .join(Contract, Contract.id == PlaybookRun.contract_id)
        .where(PlaybookRun.org_id == current_user.org_id, PlaybookRun.playbook_id == playbook.id)
        .where(Contract.org_id == current_user.org_id, Contract.deleted_at.is_(None), accessible_contract_filter(current_user))
        .order_by(PlaybookRun.created_at.desc())
        .limit(100)
    ).all()


def _require_permission(user, permission: str) -> None:
    if not has_permission(user.permission_values, permission):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {permission}")


def _ensure_draft_version(version: PlaybookVersion) -> None:
    if version.status != PlaybookStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft playbook versions can be edited")


def _get_rule(db: Session, *, rule_id: str, version: PlaybookVersion, org_id: str) -> PlaybookRule:
    rule = db.get(PlaybookRule, rule_id)
    if rule is None or rule.org_id != org_id or rule.playbook_version_id != version.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Playbook rule not found")
    return rule


def _rule_payloads_for_ai(db: Session, *, org_id: str, playbook_version_id: str) -> list[dict]:
    rules = db.scalars(
        select(PlaybookRule)
        .where(PlaybookRule.org_id == org_id, PlaybookRule.playbook_version_id == playbook_version_id)
        .order_by(PlaybookRule.created_at.asc())
    ).all()
    return [
        {
            "rule_index": index,
            "clause_type": rule.clause_type,
            "rule_type": rule.rule_type,
            "preferred_position": rule.preferred_position,
            "fallback_position": rule.fallback_position,
            "prohibited_language": rule.prohibited_language,
            "required_language": rule.required_language,
            "risk_level": rule.risk_level,
            "rationale": rule.rationale,
            "approval_required": rule.approval_required,
            "escalation_role": rule.escalation_role,
            "sample_clause": rule.sample_clause,
            "negotiation_guidance": rule.negotiation_guidance,
        }
        for index, rule in enumerate(rules, start=1)
    ]
