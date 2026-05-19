from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import base64
import hashlib
import hmac
import threading
from pathlib import Path

import httpx
import jwt

from app.core.config import settings


@dataclass(frozen=True)
class EnvelopeResult:
    envelope_id: str | None
    status: str
    metadata: dict


_PRIVATE_KEY_CACHE: dict[str, str] = {}
_PRIVATE_KEY_LOCK = threading.Lock()

_DOCUSIGN_TIMEOUT = httpx.Timeout(30.0)
_shared_docusign_client: httpx.AsyncClient | None = None


def _docusign_client() -> httpx.AsyncClient:
    """Long-lived HTTP client with connection pooling."""
    global _shared_docusign_client
    if _shared_docusign_client is None or _shared_docusign_client.is_closed:
        _shared_docusign_client = httpx.AsyncClient(timeout=_DOCUSIGN_TIMEOUT)
    return _shared_docusign_client


async def aclose_docusign_client() -> None:
    global _shared_docusign_client
    if _shared_docusign_client is not None and not _shared_docusign_client.is_closed:
        await _shared_docusign_client.aclose()
    _shared_docusign_client = None


def _load_private_key(path: str) -> str:
    """Cache the PEM by path so we don't re-read it on every JWT mint."""
    with _PRIVATE_KEY_LOCK:
        cached = _PRIVATE_KEY_CACHE.get(path)
        if cached is not None:
            return cached
        key = Path(path).read_text(encoding="utf-8")
        _PRIVATE_KEY_CACHE[path] = key
        return key


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
        client = _docusign_client()
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

    async def void_envelope(self, *, envelope_id: str, reason: str = "internal_rollback") -> None:
        """Best-effort void of a previously-created envelope.

        Used to clean up orphaned envelopes when the local DB commit fails
        after a successful DocuSign API call. We swallow errors because the
        caller is already in a failure path — leaving an envelope alive is
        bad but raising over it is worse.
        """
        if settings.mock_docusign or not envelope_id:
            return
        try:
            access_token = await self._jwt_access_token()
            client = _docusign_client()
            await client.put(
                f"{settings.docusign_rest_base_url.rstrip('/')}/v2.1/accounts/"
                f"{settings.docusign_account_id}/envelopes/{envelope_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"status": "voided", "voidedReason": reason[:200]},
            )
        except Exception:
            # Reconciliation job picks up orphaned envelopes; do not raise.
            pass

    async def _jwt_access_token(self) -> str:
        now = datetime.now(UTC)
        key_path = settings.docusign_private_key_path or ""
        if not key_path:
            raise RuntimeError("DOCUSIGN_PRIVATE_KEY_PATH is not configured")
        private_key = _load_private_key(key_path)
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
        client = _docusign_client()
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


def verify_connect_signature(*, body: bytes, signature_header: str | None) -> bool:
    """Verify a DocuSign Connect HMAC (Base64 HMAC-SHA256 of the raw body).

    Returns False when no shared key is configured or the header is absent —
    the webhook must reject anything it cannot cryptographically attribute.
    """
    key = settings.docusign_connect_hmac_key
    if not key or not signature_header:
        return False
    expected = base64.b64encode(
        hmac.new(key.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected, signature_header.strip())
