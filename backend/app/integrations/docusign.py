from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import base64
from pathlib import Path

import httpx
from jose import jwt

from app.core.config import settings


@dataclass(frozen=True)
class EnvelopeResult:
    envelope_id: str | None
    status: str
    metadata: dict


class DocuSignClient:
    async def create_envelope(self, *, filename: str, recipients: list[dict], content: bytes) -> EnvelopeResult:
        if settings.mock_docusign:
            return EnvelopeResult(
                envelope_id=None,
                status="mocked",
                metadata={"filename": filename, "recipient_count": len(recipients), "size": len(content)},
            )
        required = {
            "DOCUSIGN_INTEGRATION_KEY": settings.docusign_integration_key,
            "DOCUSIGN_USER_ID": settings.docusign_user_id,
            "DOCUSIGN_ACCOUNT_ID": settings.docusign_account_id,
            "DOCUSIGN_PRIVATE_KEY_PATH": settings.docusign_private_key_path,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing DocuSign configuration: {', '.join(missing)}")
        access_token = await self._jwt_access_token()
        envelope_payload = self._envelope_payload(filename=filename, recipients=recipients, content=content)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{settings.docusign_rest_base_url.rstrip('/')}/v2.1/accounts/"
                f"{settings.docusign_account_id}/envelopes",
                headers={"Authorization": f"Bearer {access_token}"},
                json=envelope_payload,
            )
            response.raise_for_status()
        raw = response.json()
        return EnvelopeResult(
            envelope_id=raw.get("envelopeId"),
            status=raw.get("status", "sent"),
            metadata={
                "provider": "docusign",
                "filename": filename,
                "recipient_count": len(recipients),
                "envelope": raw,
            },
        )

    async def _jwt_access_token(self) -> str:
        now = datetime.now(UTC)
        private_key = Path(settings.docusign_private_key_path or "").read_text(encoding="utf-8")
        assertion = jwt.encode(
            {
                "iss": settings.docusign_integration_key,
                "sub": settings.docusign_user_id,
                "aud": settings.docusign_oauth_base_url.removeprefix("https://").rstrip("/"),
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=45)).timestamp()),
                "scope": "signature impersonation",
            },
            private_key,
            algorithm="RS256",
        )
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{settings.docusign_oauth_base_url.rstrip('/')}/oauth/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
            response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise RuntimeError("DocuSign token response did not include access_token")
        return token

    def _envelope_payload(self, *, filename: str, recipients: list[dict], content: bytes) -> dict:
        signers = []
        for index, recipient in enumerate(recipients, start=1):
            signers.append(
                {
                    "email": recipient["email"],
                    "name": recipient["name"],
                    "recipientId": str(index),
                    "routingOrder": str(index),
                    "tabs": {
                        "signHereTabs": [
                            {
                                "documentId": "1",
                                "pageNumber": "1",
                                "xPosition": "100",
                                "yPosition": "700",
                            }
                        ]
                    },
                }
            )
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "pdf"
        return {
            "emailSubject": f"Signature requested: {filename}",
            "documents": [
                {
                    "documentBase64": base64.b64encode(content).decode("ascii"),
                    "name": filename,
                    "fileExtension": extension,
                    "documentId": "1",
                }
            ],
            "recipients": {"signers": signers},
            "status": "sent",
        }


docusign_client = DocuSignClient()
