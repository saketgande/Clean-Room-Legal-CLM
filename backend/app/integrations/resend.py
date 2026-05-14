from dataclasses import dataclass

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class EmailResult:
    provider_message_id: str | None
    status: str
    metadata: dict


class ResendClient:
    async def send_email(self, *, to: str, subject: str, html: str) -> EmailResult:
        if settings.mock_resend:
            return EmailResult(None, "mocked", {"to": to, "subject": subject})
        if not settings.resend_api_key:
            raise RuntimeError("RESEND_API_KEY is required when mock Resend mode is disabled")
        async with httpx.AsyncClient(timeout=30) as client:
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
            payload = response.json()
        return EmailResult(payload.get("id"), "sent", payload)


resend_client = ResendClient()
