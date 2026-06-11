"""Client-side error ingestion.

The browser ships uncaught errors / promise rejections / React error-boundary
failures here so they land in the SAME structured stdout logs as the backend
(tagged logger ``app.client``) — making frontend errors visible on the VM via
``journalctl -u aegis-api``. Unauthenticated (errors happen pre-login too) but
rate-limited and size-capped so it can't flood the logs.
"""

import logging

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

from app.core.rate_limit import limiter

logger = logging.getLogger("app.client")

router = APIRouter(prefix="/client-logs", tags=["observability"])


class ClientLogIn(BaseModel):
    level: str = Field(default="error", max_length=10)
    message: str = Field(default="", max_length=2000)
    stack: str | None = Field(default=None, max_length=8000)
    url: str | None = Field(default=None, max_length=2000)
    kind: str | None = Field(default=None, max_length=48)


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def ingest_client_log(
    payload: ClientLogIn, request: Request, response: Response
) -> None:
    ua = request.headers.get("user-agent", "")[:300]
    request_id = getattr(request.state, "request_id", None)
    # Everything goes into the message string because the JSON log formatter only
    # serialises a fixed allow-list of extra fields (route / error_class are on it).
    detail = (
        f"client_error kind={payload.kind or 'error'} "
        f"url={payload.url or '-'} ua={ua!r} :: {payload.message}"
    )
    if payload.stack:
        detail += "\n" + payload.stack
    emit = logger.error if payload.level == "error" else logger.warning
    emit(
        detail,
        extra={
            "route": "client",
            "error_class": "ClientError",
            "request_id": request_id,
        },
    )
    return None
