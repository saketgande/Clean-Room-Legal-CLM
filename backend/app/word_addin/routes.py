from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user, get_db
from app.core.rate_limit import limiter
from app.word_addin.schemas import LinkResponse
from app.word_addin.service import resolve_contract_for_document

router = APIRouter(prefix="/word", tags=["word-addin"])


@router.get("/ping")
def ping(current_user=Depends(get_current_user)):
    """Auth + connectivity check the task pane calls right after sign-in."""
    return {
        "ok": True,
        "user_id": current_user.id,
        "org_id": current_user.org_id,
        "full_name": current_user.full_name,
        "email": current_user.email,
    }


@router.post("/link", response_model=LinkResponse)
@limiter.limit(settings.rate_limit_contract_upload)
async def link(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Auto-link the open document to a contract — match by content, else create."""
    _ = response
    return await resolve_contract_for_document(
        db, upload=file, user=current_user, request_id=getattr(request.state, "request_id", None)
    )
