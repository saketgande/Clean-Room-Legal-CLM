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


_SENDGRID_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_sendgrid_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = (
    weakref.WeakKeyDictionary()
)


def _sendgrid_client() -> httpx.AsyncClient:
    """Shared HTTP client per event loop (see resend.py for why per-loop:
    Celery's asyncio.run() closes its loop after every task, so a module
    singleton dies with "Event loop is closed" on the next task)."""
    loop = asyncio.get_running_loop()
    client = _sendgrid_clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=_SENDGRID_TIMEOUT)
        _sendgrid_clients[loop] = client
    return client


async def aclose_sendgrid_client() -> None:
    """Close this loop's shared HTTP client. Called on app shutdown."""
    loop = asyncio.get_running_loop()
    client = _sendgrid_clients.pop(loop, None)
    if client is not None and not client.is_closed:
        await client.aclose()


class SendGridClient:
    async def send_email(
        self, *, to: str, subject: str, html: str, include_cc: bool = True
    ) -> EmailResult:
        if settings.mock_sendgrid:
            return EmailResult(None, "mocked", {"to": to, "subject": subject})
        if not settings.sendgrid_api_key:
            raise RuntimeError("SENDGRID_API_KEY is required when mock SendGrid mode is disabled")
        client = _sendgrid_client()

        personalization: dict = {"to": [{"email": to}]}
        cc_email = settings.sendgrid_cc_email
        if include_cc and cc_email and cc_email.strip().lower() != to.strip().lower():
            personalization["cc"] = [{"email": cc_email}]

        @resilient_call("sendgrid")
        async def _send() -> httpx.Response:
            response = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {settings.sendgrid_api_key}"},
                json={
                    "personalizations": [personalization],
                    "from": {"email": settings.sendgrid_from_email},
                    "subject": subject,
                    "content": [{"type": "text/html", "value": html}],
                },
            )
            response.raise_for_status()
            return response

        response = await _send()
        # SendGrid's /mail/send returns 202 with an empty body; the message id
        # rides the X-Message-Id response header, not a JSON payload.
        message_id = response.headers.get("X-Message-Id")
        return EmailResult(message_id, "sent", {"status_code": response.status_code})


sendgrid_client = SendGridClient()
