from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.contract_files.service import create_contract_from_upload
from app.contracts.lifecycle import allowed_transitions_for, transition_contract_stage
from app.contracts.schemas import (
    ContractActivityResponse,
    ContractResponse,
    ContractStageHistoryResponse,
    ContractUpdate,
    ContractUploadResponse,
    LifecycleOptionsResponse,
    LifecycleTransitionRequest,
)
from app.contracts.service import (
    contract_hub_summary,
    get_contract_for_user,
    list_contract_activity,
    list_contract_stage_history,
    list_contracts_for_user,
    update_contract_metadata,
)
from app.core.deps import get_db, require_permission

router = APIRouter(prefix="/contracts", tags=["contracts"])
hub_router = APIRouter(prefix="/contract-hub", tags=["contract-hub"])


@router.get("", response_model=list[ContractResponse])
def list_contracts(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    return list_contracts_for_user(db, user=current_user)


@router.post(
    "/upload",
    response_model=ContractUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_contract(
    request: Request,
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
    return {
        "current_stage": contract.lifecycle_stage,
        "allowed_transitions": allowed_transitions_for(contract.lifecycle_stage),
    }


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


@hub_router.get("")
def contract_hub(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract:read")),
):
    return contract_hub_summary(db, user=current_user)
