from fastapi import APIRouter, Depends, status

from app.core.deps import require_permission
from app.matters.dependencies import get_matters_service
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
    MatterResponse,
    MatterShareCreate,
    MatterShareResponse,
    MatterUpdate,
    UnfiledItem,
)
from app.matters.service import MattersService

# No prefix here — main.py mounts this at both /matters (canonical) and /projects
# (deprecated alias, kept until the Matters UI ships).
router = APIRouter(tags=["matters"])


@router.get("", response_model=list[MatterResponse])
def list_projects(
    current_user=Depends(require_permission("project:read")),
    service: MattersService = Depends(get_matters_service),
):
    return service.list_projects(current_user=current_user)


@router.post("", response_model=MatterResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: MatterCreate,
    current_user=Depends(require_permission("project:create")),
    service: MattersService = Depends(get_matters_service),
):
    return service.create_project(payload=payload, current_user=current_user)


@router.get("/unfiled", response_model=list[UnfiledItem])
def unfiled_items(
    item_type: str | None = None,
    current_user=Depends(require_permission("project:read")),
    service: MattersService = Depends(get_matters_service),
):
    """Contracts and intake requests not yet filed under any matter — the
    'awaiting a matter' tray. Registered before /{matter_id} so 'unfiled' isn't
    captured as a matter id."""
    return service.unfiled_items(item_type=item_type, current_user=current_user)


@router.get("/{matter_id}/overview", response_model=MatterOverview)
def matter_overview(
    matter_id: str,
    current_user=Depends(require_permission("project:read")),
    service: MattersService = Depends(get_matters_service),
):
    """The matter hub: counts + recent items rolled up across the matter's
    contracts (obligations/notices/approvals derive through them) and its
    intake requests."""
    return service.matter_overview(matter_id=matter_id, current_user=current_user)


@router.get("/{matter_id}/activity")
def matter_activity(
    matter_id: str,
    current_user=Depends(require_permission("project:read")),
    service: MattersService = Depends(get_matters_service),
):
    """Recent events on this matter (assignments, folders, etc.)."""
    return service.matter_activity(matter_id=matter_id, current_user=current_user)


@router.post("/{matter_id}/items", status_code=status.HTTP_200_OK)
def assign_item_to_matter(
    matter_id: str,
    payload: AssignItemRequest,
    current_user=Depends(require_permission("project:update")),
    service: MattersService = Depends(get_matters_service),
):
    """File a contract or intake request under this matter (one matter -> many)."""
    return service.assign_item_to_matter(matter_id=matter_id, payload=payload, current_user=current_user)


@router.get("/{matter_id}", response_model=MatterResponse)
def get_project(
    matter_id: str,
    current_user=Depends(require_permission("project:read")),
    service: MattersService = Depends(get_matters_service),
):
    return service.get_project(matter_id=matter_id, current_user=current_user)


@router.patch("/{matter_id}", response_model=MatterResponse)
def update_project(
    matter_id: str,
    payload: MatterUpdate,
    current_user=Depends(require_permission("project:update")),
    service: MattersService = Depends(get_matters_service),
):
    return service.update_project(matter_id=matter_id, payload=payload, current_user=current_user)


@router.delete("/{matter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    matter_id: str,
    current_user=Depends(require_permission("project:delete")),
    service: MattersService = Depends(get_matters_service),
):
    service.delete_project(matter_id=matter_id, current_user=current_user)


@router.get("/{matter_id}/folders", response_model=list[MatterFolderResponse])
def list_project_folders(
    matter_id: str,
    current_user=Depends(require_permission("project:read")),
    service: MattersService = Depends(get_matters_service),
):
    return service.list_project_folders(matter_id=matter_id, current_user=current_user)


@router.post(
    "/{matter_id}/folders",
    response_model=MatterFolderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project_folder(
    matter_id: str,
    payload: MatterFolderCreate,
    current_user=Depends(require_permission("project:update")),
    service: MattersService = Depends(get_matters_service),
):
    return service.create_project_folder(matter_id=matter_id, payload=payload, current_user=current_user)


@router.patch("/{matter_id}/folders/{folder_id}", response_model=MatterFolderResponse)
def update_project_folder(
    matter_id: str,
    folder_id: str,
    payload: MatterFolderUpdate,
    current_user=Depends(require_permission("project:update")),
    service: MattersService = Depends(get_matters_service),
):
    return service.update_project_folder(
        matter_id=matter_id, folder_id=folder_id, payload=payload, current_user=current_user
    )


@router.delete("/{matter_id}/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_folder(
    matter_id: str,
    folder_id: str,
    current_user=Depends(require_permission("project:update")),
    service: MattersService = Depends(get_matters_service),
):
    service.delete_project_folder(matter_id=matter_id, folder_id=folder_id, current_user=current_user)


@router.get("/{matter_id}/members", response_model=list[MatterMemberResponse])
def list_project_members(
    matter_id: str,
    current_user=Depends(require_permission("project:read")),
    service: MattersService = Depends(get_matters_service),
):
    return service.list_project_members(matter_id=matter_id, current_user=current_user)


@router.put("/{matter_id}/members", response_model=MatterMemberResponse)
def upsert_project_member(
    matter_id: str,
    payload: MatterMemberUpsert,
    current_user=Depends(require_permission("project:share")),
    service: MattersService = Depends(get_matters_service),
):
    return service.upsert_project_member(matter_id=matter_id, payload=payload, current_user=current_user)


@router.delete("/{matter_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_project_member(
    matter_id: str,
    user_id: str,
    current_user=Depends(require_permission("project:share")),
    service: MattersService = Depends(get_matters_service),
):
    service.remove_project_member(matter_id=matter_id, user_id=user_id, current_user=current_user)


@router.get("/{matter_id}/shares", response_model=list[MatterShareResponse])
def list_project_shares(
    matter_id: str,
    current_user=Depends(require_permission("project:share")),
    service: MattersService = Depends(get_matters_service),
):
    return service.list_project_shares(matter_id=matter_id, current_user=current_user)


@router.post(
    "/{matter_id}/shares",
    response_model=MatterShareResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project_share(
    matter_id: str,
    payload: MatterShareCreate,
    current_user=Depends(require_permission("project:share")),
    service: MattersService = Depends(get_matters_service),
):
    return service.create_project_share(matter_id=matter_id, payload=payload, current_user=current_user)


@router.post("/{matter_id}/shares/{share_id}/revoke", response_model=MatterShareResponse)
def revoke_project_share(
    matter_id: str,
    share_id: str,
    current_user=Depends(require_permission("project:share")),
    service: MattersService = Depends(get_matters_service),
):
    return service.revoke_project_share(matter_id=matter_id, share_id=share_id, current_user=current_user)


@router.get("/{matter_id}/contracts", response_model=list[MatterContractResponse])
def list_project_contracts(
    matter_id: str,
    current_user=Depends(require_permission("project:read")),
    service: MattersService = Depends(get_matters_service),
):
    return service.list_project_contracts(matter_id=matter_id, current_user=current_user)


@router.put("/{matter_id}/contracts", response_model=MatterContractResponse)
def add_project_contract(
    matter_id: str,
    payload: MatterContractAdd,
    current_user=Depends(require_permission("project:update")),
    service: MattersService = Depends(get_matters_service),
):
    return service.add_project_contract(matter_id=matter_id, payload=payload, current_user=current_user)


@router.patch("/{matter_id}/contracts/{contract_id}", response_model=MatterContractResponse)
def update_project_contract(
    matter_id: str,
    contract_id: str,
    payload: MatterContractUpdate,
    current_user=Depends(require_permission("project:update")),
    service: MattersService = Depends(get_matters_service),
):
    return service.update_project_contract(
        matter_id=matter_id, contract_id=contract_id, payload=payload, current_user=current_user
    )


@router.delete("/{matter_id}/contracts/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_project_contract(
    matter_id: str,
    contract_id: str,
    current_user=Depends(require_permission("project:update")),
    service: MattersService = Depends(get_matters_service),
):
    service.remove_project_contract(matter_id=matter_id, contract_id=contract_id, current_user=current_user)
