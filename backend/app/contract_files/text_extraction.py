from dataclasses import dataclass
from io import BytesIO

from app.core.config import settings


@dataclass(frozen=True)
class TextExtractionResult:
    text: str
    method: str
    quality_score: float
    page_map: dict | None = None
    needs_ocr: bool = False
    metadata: dict | None = None


def extract_text(content: bytes, *, mime_type: str, filename: str) -> TextExtractionResult:
    max_bytes = settings.pdf_max_extracted_text_bytes
    if mime_type.startswith("text/"):
        text = content.decode("utf-8", errors="replace")
        text, truncated = _cap_text(text, max_bytes)
        return _with_quality(text, "plain_text", metadata={"truncated": truncated} if truncated else None)

    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            from docx import Document

            document = Document(BytesIO(content))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
            text, truncated = _cap_text(text, max_bytes)
            return _with_quality(text, "docx", metadata={"truncated": truncated} if truncated else None)
        except Exception as exc:
            return TextExtractionResult("", "docx_failed", 0.0, needs_ocr=True, metadata={"error": str(exc)})

    if mime_type == "application/pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content))
            pages: list[str] = []
            page_map: dict[str, dict[str, int]] = {}
            offset = 0
            total_bytes = 0
            truncated = False
            for page_number, page in enumerate(reader.pages, start=1):
                page_text = page.extract_text() or ""
                # Hard cap per contract so a single PDF cannot OOM the worker.
                if total_bytes + len(page_text.encode("utf-8")) > max_bytes:
                    remaining = max(0, max_bytes - total_bytes)
                    page_text = page_text.encode("utf-8")[:remaining].decode("utf-8", errors="ignore")
                    pages.append(page_text)
                    page_map[str(page_number)] = {"start": offset, "end": offset + len(page_text)}
                    truncated = True
                    break
                pages.append(page_text)
                page_map[str(page_number)] = {"start": offset, "end": offset + len(page_text)}
                offset += len(page_text) + 1
                total_bytes += len(page_text.encode("utf-8"))
            text = "\n".join(pages)
            result = _with_quality(
                text,
                "pdf_text",
                page_map=page_map,
                metadata={"truncated": truncated} if truncated else None,
            )
            return result
        except Exception as exc:
            return TextExtractionResult("", "pdf_failed", 0.0, needs_ocr=True, metadata={"error": str(exc)})

    if mime_type.startswith("image/"):
        return TextExtractionResult("", "image_requires_ocr", 0.0, needs_ocr=True)

    return TextExtractionResult("", "unsupported", 0.0, needs_ocr=True, metadata={"filename": filename})


def _cap_text(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _with_quality(
    text: str, method: str, page_map: dict | None = None, metadata: dict | None = None
) -> TextExtractionResult:
    quality = score_extraction_quality(text)
    return TextExtractionResult(
        text=text,
        method=method,
        quality_score=quality,
        page_map=page_map,
        needs_ocr=quality < 0.55,
        metadata=metadata,
    )


def score_extraction_quality(text: str) -> float:
    stripped = text.strip()
    if len(stripped) < 80:
        return 0.0
    printable = sum(1 for char in stripped if char.isprintable() or char.isspace())
    alphabetic = sum(1 for char in stripped if char.isalpha())
    unreadable = stripped.count("\ufffd")
    printable_ratio = printable / max(len(stripped), 1)
    alpha_ratio = alphabetic / max(len(stripped), 1)
    unreadable_penalty = min(unreadable / max(len(stripped), 1), 0.5)
    return max(0.0, min(1.0, printable_ratio * 0.55 + alpha_ratio * 0.45 - unreadable_penalty))
