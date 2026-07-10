import asyncio
import weakref
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.integrations._http_retry import resilient_call


@dataclass(frozen=True)
class EmailResult:
    provider_message_id: str | None
    status: str
    metadata: dict


_RESEND_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_resend_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = (
    weakref.WeakKeyDictionary()
)


def _resend_client() -> httpx.AsyncClient:
    """Shared HTTP client per event loop (see claude.py for why per-loop:
    Celery's asyncio.run() closes its loop after every task, so a module
    singleton dies with "Event loop is closed" on the next task)."""
    loop = asyncio.get_running_loop()
    client = _resend_clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=_RESEND_TIMEOUT)
        _resend_clients[loop] = client
    return client


async def aclose_resend_client() -> None:
    """Close this loop's shared HTTP client. Called on app shutdown."""
    loop = asyncio.get_running_loop()
    client = _resend_clients.pop(loop, None)
    if client is not None and not client.is_closed:
        await client.aclose()


class ResendClient:
    async def send_email(self, *, to: str, subject: str, html: str) -> EmailResult:
        if settings.mock_resend:
            return EmailResult(None, "mocked", {"to": to, "subject": subject})
        if not settings.resend_api_key:
            raise RuntimeError("RESEND_API_KEY is required when mock Resend mode is disabled")
        client = _resend_client()

        @resilient_call("resend")
        async def _send() -> dict:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={
                    "from": settings.resend_from_email,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                },
            )
            response.raise_for_status()
            return response.json()

        payload = await _send()
        return EmailResult(payload.get("id"), "sent", payload)


resend_client = ResendClient()
