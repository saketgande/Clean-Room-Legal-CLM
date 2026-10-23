"""ip_india_journal document-extraction template: renders each PDF page as an
image and asks Claude to read it directly (the same way a human would),
instead of guessing entry boundaries from plain extracted text.

The prompt lives in the prompt table (key "trademark_journal_vision", default
in app/ai/prompt_versions.py) and every call goes through the AI gateway, so it
is recorded in the AI ledger like every other AI call.

v1 intentionally drops the POC's few-shot (image, JSON) example pairs and
embedded product-image extraction/cropping - see the integration plan for
why; `has_product_image` is still captured for future use.
"""

from dataclasses import dataclass

import fitz  # PyMuPDF
from sqlalchemy.orm import Session

from app.core.config import settings

# Must match the "trademark_journal_vision" feature's tool_name (app/ai/gateway/features.py).
TOOL_NAME = "extract_journal_entries"

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "product_name": {"type": ["string", "null"]},
                    "mark_type": {
                        "type": "string",
                        "enum": ["word", "device", "combination", "sound", "other"],
                    },
                    "tm_id": {"type": "string"},
                    "tm_date": {"type": "string"},
                    "address": {"type": "string"},
                    "used_since": {"type": ["string", "null"]},
                    "proposed_to_be_used": {"type": "boolean"},
                    "jurisdiction": {"type": ["string", "null"]},
                    "goods_services": {"type": ["string", "null"]},
                    "has_product_image": {"type": "boolean"},
                },
                "required": [
                    "product_name", "mark_type", "tm_id", "tm_date", "address",
                    "used_since", "proposed_to_be_used", "jurisdiction",
                    "goods_services", "has_product_image",
                ],
            },
        }
    },
    "required": ["entries"],
}


@dataclass
class VisionEntry:
    product_name: str | None
    mark_type: str
    tm_id: str
    tm_date: str
    address: str
    used_since: str | None
    proposed_to_be_used: bool
    jurisdiction: str | None
    goods_services: str | None
    has_product_image: bool


def render_page_to_png_bytes(pdf_bytes: bytes, page_number: int, dpi: int) -> bytes:
    """page_number is 1-indexed."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[page_number - 1]
        zoom = dpi / 72  # PDF base is 72dpi
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return pix.tobytes("png")
    finally:
        doc.close()


async def extract_journal_page(
    db: Session, pdf_bytes: bytes, page_number: int, *, org_id: str, claude_client=None
) -> list[VisionEntry]:
    """Read one journal page. Raises on a failed call or an unusable answer
    (the caller reports the page as failed); both are recorded in the ledger."""
    from app.ai.gateway import AICallContext, gateway_for

    page_png = render_page_to_png_bytes(pdf_bytes, page_number, settings.trademark_vision_render_dpi)
    result = await gateway_for(claude_client).vision(
        db,
        "trademark_journal_vision",
        ctx=AICallContext(org_id=org_id),
        user_prompt="Extract every entry on this page.",
        image_bytes=page_png,
        image_media_type="image/png",
        input_schema=INPUT_SCHEMA,
        log_input={"page_number": page_number, "image_bytes": len(page_png)},
    )
    entries = result.data.get("entries", [])
    return [VisionEntry(**entry) for entry in entries]
