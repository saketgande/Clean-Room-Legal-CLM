"""Which parser handles which media type.

A lookup, deliberately, and not an if/elif inside the ingest path. Adding a
layout model or a different OCR provider should be an entry here; in the
previous pipeline it was a rewrite of the function every upload flowed through.
"""

from .base import Parser, UnsupportedFormat
from .docx import DocxParser
from .pdf import PdfParser
from .text import TextParser

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_PARSERS: dict[str, Parser] = {
    "application/pdf": PdfParser(),
    DOCX_MIME: DocxParser(),
    "text/plain": TextParser(),
}

# Accepted by the wider app but not readable here. Named explicitly so the
# refusal can say *why*, instead of a document quietly ending up empty.
_KNOWN_UNREADABLE = {
    "application/msword": (
        "Word 97-2003 (.doc) is a different binary format from .docx and needs "
        "server-side conversion before it can be read."
    ),
    "image/png": "An image needs OCR before it has any text to read.",
    "image/jpeg": "An image needs OCR before it has any text to read.",
}


def parser_for(mime_type: str) -> Parser:
    parser = _PARSERS.get(mime_type)
    if parser is not None:
        return parser
    reason = _KNOWN_UNREADABLE.get(mime_type)
    raise UnsupportedFormat(reason or f"No parser is registered for {mime_type!r}.")


def supported_mime_types() -> list[str]:
    return sorted(_PARSERS)
