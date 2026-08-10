"""ip_india_journal document-extraction template: renders each PDF page as an
image and asks Claude to read it directly (the same way a human would),
instead of guessing entry boundaries from plain extracted text.

v1 intentionally drops the POC's few-shot (image, JSON) example pairs and
embedded product-image extraction/cropping - see the integration plan for
why; `has_product_image` is still captured for future use.
"""

from dataclasses import dataclass

import fitz  # PyMuPDF

from app.core.config import settings
from app.integrations.claude import claude_client

SYSTEM_PROMPT = """You are extracting structured trademark data from a page \
of the India Trade Marks Journal. A single page may contain MORE THAN ONE \
trademark entry - read the whole page and return every entry you find, in \
top-to-bottom reading order.

For each entry, extract these fields exactly:
- product_name: the word mark's name, in the exact case/spelling shown. If \
this is a device/label mark with no separate text mark name (the mark IS an \
image/logo), set this to null.
- mark_type: "word" if it's a plain text mark, "device" if it's a logo/label \
image, "combination" if both a name and a distinct logo are shown together.
- tm_id: the numeric application/registration number, as a string.
- tm_date: the date on the same line as tm_id, in DD/MM/YYYY format exactly \
as printed.
- address: ALL of the proprietor name, address, business type, incorporation \
details, and attorney/service address lines, concatenated with \\n between \
lines, exactly as printed. Do not summarize or shorten this.
- used_since: the date from a "Used Since" line, in DD/MM/YYYY format. null \
if this entry instead says "Proposed to be Used".
- proposed_to_be_used: true if the entry says "Proposed to be Used" instead \
of giving a Used Since date, false otherwise.
- jurisdiction: the single city name shown after the used-since/proposed \
line (e.g. MUMBAI, CHENNAI, DELHI, KOLKATA, AHMEDABAD, or any other city \
actually printed - do not assume it must be one of a fixed list).
- goods_services: the full goods/services description text, including any \
"subject to" or disclaimer clause printed immediately after it, concatenated \
with \\n between lines.
- has_product_image: true if this entry has an embedded product photo, \
label, or device/logo image anywhere in its block (not just for device \
marks - a word mark can still have an accompanying product photo).

Be precise and complete - do not paraphrase, summarize, or omit any part of \
the address or goods/services text. If a field genuinely isn't present, use \
null (or false for booleans) rather than guessing.
"""

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


async def extract_journal_page(pdf_bytes: bytes, page_number: int) -> list[VisionEntry]:
    page_png = render_page_to_png_bytes(pdf_bytes, page_number, settings.trademark_vision_render_dpi)

    response = await claude_client.complete_vision_structured(
        system_prompt=SYSTEM_PROMPT,
        user_prompt="Extract every entry on this page.",
        image_bytes=page_png,
        image_media_type="image/png",
        tool_name=TOOL_NAME,
        input_schema=INPUT_SCHEMA,
        max_tokens=4000,
        temperature=0.0,
    )
    if not response.tool_use_blocks:
        return []
    payload = response.tool_use_blocks[0].get("input") or {}
    entries = payload.get("entries", [])
    return [VisionEntry(**entry) for entry in entries]
