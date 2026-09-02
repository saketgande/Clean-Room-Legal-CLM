import inspect
import tempfile
from pathlib import Path

from app.core.config import settings
from app.integrations.ocr import OCRResult  # re-exported: `reducto.OCRResult` still resolves


async def _maybe_await(value):
    return await value if inspect.isawaitable(value) else value


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

        from reducto import AsyncReducto

        suffix = Path(filename).suffix or ".pdf"
        client = AsyncReducto(api_key=settings.reducto_api_key)
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
                tmp.write(content)
                tmp.flush()
                upload = await _maybe_await(client.upload(file=Path(tmp.name)))
            file_input = getattr(upload, "file_id", None) or upload
            parsed = await _maybe_await(client.parse.run(input=file_input))
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                await _maybe_await(close())

        result = getattr(parsed, "result", None)
        chunks = getattr(result, "chunks", None) or []
        contents = [(getattr(c, "content", "") or "") for c in chunks]
        text = "\n".join(contents).strip()
        usage = getattr(parsed, "usage", None)
        return OCRResult(
            text=text,
            provider=self.provider,
            quality_score=0.9 if text else 0.0,
            # Reducto chunks carry no type; the structure builder infers headings
            # and clause numbers from the content itself.
            elements=[{"type": "text", "content": c} for c in contents if c.strip()],
            metadata={
                "job_id": getattr(parsed, "job_id", None),
                "num_pages": getattr(usage, "num_pages", None),
                "credits": getattr(usage, "credits", None),
            },
        )


reducto_client = ReductoClient()
