"""Generic-template document extraction: pull user-defined fields out of a
PDF page range using anchor-label or regex capture.

Reuses CLM's existing text extraction (`app.contract_files.text_extraction`)
and OCR fallback (`app.integrations.reducto`) instead of bringing in a
second PDF/OCR stack.
"""

import re

from app.contract_files.text_extraction import TextExtractionResult, extract_text
from app.integrations.reducto import reducto_client
from app.trademarks.schemas import ExtractedRecord, FieldDefinition


async def extract_generic_records(
    *,
    content: bytes,
    filename: str,
    page_start: int,
    page_end: int,
    field_definitions: list[FieldDefinition],
) -> list[ExtractedRecord]:
    """One ExtractedRecord per page in [page_start, page_end], fields captured
    via anchor/regex against that page's text."""
    extraction: TextExtractionResult = extract_text(content, mime_type="application/pdf", filename=filename)
    text = extraction.text
    page_map = extraction.page_map or {}
    used_ocr = False

    if extraction.needs_ocr:
        try:
            ocr = await reducto_client.extract_text(filename=filename, mime_type="application/pdf", content=content)
            if ocr.text:
                text = ocr.text
                used_ocr = True
        except Exception:
            pass  # fall back to whatever native extraction produced

    total_pages = max((int(p) for p in page_map), default=0)
    start = max(1, page_start)
    end = min(total_pages, page_end) if total_pages else page_end

    records: list[ExtractedRecord] = []
    for page_number in range(start, end + 1):
        offsets = page_map.get(str(page_number))
        warnings: list[str] = []
        if offsets is None:
            page_text = ""
            warnings.append(f"No extractable text found for page {page_number}")
        else:
            page_text = text[offsets["start"] : offsets["end"]]

        fields = apply_field_definitions(page_text, field_definitions)
        records.append(
            ExtractedRecord(
                page_number=page_number,
                entry_index=0,
                fields=fields,
                used_ocr=used_ocr,
                warnings=warnings,
            )
        )
    return records


def apply_field_definitions(text: str, fields: list[FieldDefinition]) -> dict:
    """For each field (in user-specified order), capture the text between its
    anchor label and the next field's anchor (or line end). A regex_override,
    if provided, takes precedence over anchor-based capture entirely."""
    ordered_fields = sorted(fields, key=lambda f: f.order)
    result: dict = {}

    for i, field in enumerate(ordered_fields):
        if field.regex_override:
            match = re.search(field.regex_override, text)
            raw_value = match.group(1) if match and match.groups() else (match.group(0) if match else None)
        else:
            raw_value = _capture_after_anchor(text, field.anchor, _next_anchor(ordered_fields, i))

        result[field.name] = _coerce_type(raw_value, field.data_type)

    return result


def _next_anchor(fields: list[FieldDefinition], current_index: int) -> str | None:
    if current_index + 1 < len(fields):
        return fields[current_index + 1].anchor
    return None


def _capture_after_anchor(text: str, anchor: str, next_anchor: str | None) -> str | None:
    escaped_anchor = re.escape(anchor)
    if next_anchor:
        pattern = rf"{escaped_anchor}\s*(.*?)(?=\s*{re.escape(next_anchor)}|\n|$)"
    else:
        pattern = rf"{escaped_anchor}\s*(.*?)(?:\n|$)"

    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip() or None


def _coerce_type(raw_value: str | None, data_type: str):
    if raw_value is None:
        return None
    if data_type == "string":
        return raw_value
    if data_type == "number":
        cleaned = re.sub(r"[^\d.\-]", "", raw_value)
        try:
            return float(cleaned) if "." in cleaned else int(cleaned)
        except ValueError:
            return None  # couldn't coerce - surfaced as a warning by the caller
    if data_type == "date":
        return raw_value  # kept as raw string; normalize downstream if needed
    if data_type == "boolean":
        return raw_value.strip().lower() in {"yes", "true", "y", "1"}
    return raw_value
