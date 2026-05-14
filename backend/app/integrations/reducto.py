from dataclasses import dataclass

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class OCRResult:
    text: str
    provider: str
    quality_score: float
    metadata: dict


class ReductoClient:
    provider = "reducto"

    async def extract_text(self, *, filename: str, mime_type: str, content: bytes) -> OCRResult:
        if settings.mock_reducto:
            return OCRResult(
                text="",
                provider=self.provider,
                quality_score=0.0,
                metadata={"mode": "mock", "filename": filename, "mime_type": mime_type},
            )
        if not settings.reducto_api_key:
            raise RuntimeError("REDUCTO_API_KEY is required when mock Reducto mode is disabled")

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                "https://platform.reducto.ai/v1/parse",
                headers={"Authorization": f"Bearer {settings.reducto_api_key}"},
                files={"file": (filename, content, mime_type)},
            )
            response.raise_for_status()
            payload = response.json()
        text = payload.get("text") or payload.get("content") or ""
        return OCRResult(
            text=text,
            provider=self.provider,
            quality_score=0.9 if text else 0.0,
            metadata=payload,
        )


reducto_client = ReductoClient()
