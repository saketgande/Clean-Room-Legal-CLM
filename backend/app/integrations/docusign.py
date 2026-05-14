from dataclasses import dataclass

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
        raise NotImplementedError("DocuSign JWT envelope creation will be wired after credentials are added")


docusign_client = DocuSignClient()
