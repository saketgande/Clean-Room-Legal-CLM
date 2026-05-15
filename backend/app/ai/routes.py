from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.controller import ai_controller
from app.ai.embeddings import generate_embeddings_for_snapshot
from app.ai.models import AIPromptVersion, AISkillRun
from app.ai.prompt_versions import DEFAULT_SKILL_PROMPTS, create_prompt_version, hash_text
from app.ai.registry import skill_registry
from app.ai.schemas import AIPromptVersionResponse, AISkillRunResponse, SkillInfo
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.service import get_contract_for_user
from app.core.deps import get_db, require_permission
from app.core.enums import AIPromptStatus

router = APIRouter(prefix="/ai", tags=["ai"])


class PromptVersionCreate(BaseModel):
    prompt_key: str
    version: str
    prompt_text: str
    status: str = AIPromptStatus.DRAFT
    description: str | None = None


class ContractAIRerunRequest(BaseModel):
    contract_version_id: str | None = None
    text_snapshot_id: str | None = None


@router.get("/skills", response_model=list[SkillInfo])
def list_ai_skills(current_user=Depends(require_permission("assistant:use"))):
    permissions = current_user.permission_values
    return [
        info
        for info in skill_registry.public_info()
        if info.required_permission is None or info.required_permission in permissions or "*" in permissions
    ]


@router.get("/skill-runs", response_model=list[AISkillRunResponse])
def list_skill_runs(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    return db.scalars(
        select(AISkillRun)
        .where(AISkillRun.org_id == current_user.org_id)
        .order_by(AISkillRun.created_at.desc())
        .limit(100)
    ).all()


@router.get("/skill-runs/{skill_run_id}", response_model=AISkillRunResponse)
def get_skill_run(
    skill_run_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("assistant:use")),
):
    run = db.get(AISkillRun, skill_run_id)
    if run is None or run.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "AI skill run not found")
    return run


@router.get("/prompt-versions", response_model=list[AIPromptVersionResponse])
def list_prompt_versions(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("admin_panel:access")),
):
    rows = db.scalars(
        select(AIPromptVersion)
        .where(AIPromptVersion.org_id == current_user.org_id)
        .order_by(AIPromptVersion.prompt_key.asc(), AIPromptVersion.created_at.desc())
    ).all()
    if rows:
        return rows
    return [
        AIPromptVersionResponse(
            id=None,
            prompt_key=key,
            version="1.0.0",
            status=AIPromptStatus.ACTIVE,
            prompt_hash=hash_text(prompt),
            description="Built-in default prompt",
            model_name=None,
            model_config_hash=None,
        )
        for key, prompt in sorted(DEFAULT_SKILL_PROMPTS.items())
    ]


@router.post("/prompt-versions", response_model=AIPromptVersionResponse)
def create_prompt(
    payload: PromptVersionCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("admin_panel:access")),
):
    row = create_prompt_version(
        db,
        org_id=current_user.org_id,
        prompt_key=payload.prompt_key,
        version=payload.version,
        prompt_text=payload.prompt_text,
        status=payload.status,
        created_by_user_id=current_user.id,
        description=payload.description,
    )
    db.commit()
    db.refresh(row)
    return row


@router.post("/contracts/{contract_id}/metadata-extraction")
async def rerun_metadata_extraction(
    contract_id: str,
    payload: ContractAIRerunRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    get_contract_for_user(db, contract_id=contract_id, user=current_user)
    output = await ai_controller.run_structured_skill(
        db,
        skill_name="contract_metadata_extraction",
        org_id=current_user.org_id,
        created_by_user_id=current_user.id,
        input_payload={"contract_id": contract_id, **payload.model_dump(exclude_none=True)},
        request_id=getattr(request.state, "request_id", None),
        resource_type="contract",
        resource_id=contract_id,
    )
    return output


@router.post("/contracts/{contract_id}/clause-extraction")
async def rerun_clause_extraction(
    contract_id: str,
    payload: ContractAIRerunRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    get_contract_for_user(db, contract_id=contract_id, user=current_user)
    output = await ai_controller.run_structured_skill(
        db,
        skill_name="clause_extraction",
        org_id=current_user.org_id,
        created_by_user_id=current_user.id,
        input_payload={"contract_id": contract_id, **payload.model_dump(exclude_none=True)},
        request_id=getattr(request.state, "request_id", None),
        resource_type="contract",
        resource_id=contract_id,
    )
    return output


@router.post("/contracts/{contract_id}/embeddings")
def rerun_embeddings(
    contract_id: str,
    payload: ContractAIRerunRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    snapshot = db.get(ContractTextSnapshot, payload.text_snapshot_id) if payload.text_snapshot_id else None
    if snapshot is None and contract.current_authoritative_version_id:
        version = db.get(ContractVersion, contract.current_authoritative_version_id)
        snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id) if version and version.text_snapshot_id else None
    if snapshot is None or snapshot.contract_id != contract_id or snapshot.org_id != current_user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Text snapshot not found")
    rows = generate_embeddings_for_snapshot(db, snapshot=snapshot, created_by_user_id=current_user.id)
    db.commit()
    return {"contract_id": contract_id, "text_snapshot_id": snapshot.id, "embedding_count": len(rows)}
