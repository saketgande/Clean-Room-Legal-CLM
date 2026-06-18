from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user, get_db
from app.core.rate_limit import limiter
from app.word_addin.schemas import (
    AskRequest,
    AskResponse,
    LinkResponse,
    ReviewRequest,
    ReviewResponse,
)
from app.word_addin.service import (
    resolve_contract_for_document,
    run_contract_question,
    run_contract_review,
)

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


@router.post("/review", response_model=ReviewResponse)
@limiter.limit(settings.rate_limit_ai_skill)
async def review(
    payload: ReviewRequest,
    request: Request,
    response: Response,
    current_user=Depends(get_current_user),
):
    # ``request``/``response`` are required by slowapi's @limiter.limit so it
    # can read the client key and inject X-RateLimit-* headers — they're unused
    # in the body otherwise.
    _ = (request, response)
    return await run_contract_review(payload)


@router.post("/ask", response_model=AskResponse)
@limiter.limit(settings.rate_limit_ai_skill)
async def ask(
    payload: AskRequest,
    request: Request,
    response: Response,
    current_user=Depends(get_current_user),
):
    _ = (request, response)
    return await run_contract_question(payload)


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
