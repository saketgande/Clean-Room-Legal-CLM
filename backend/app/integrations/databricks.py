"""Databricks document extraction — OCR plus structured field extraction.

Two capabilities, both reached over the SQL Statement Execution API so we need
no cluster, no driver and no Spark on our side:

  * ``extract_text``  — ai_parse_document. Returns the shared OCRResult so it
    drops into the same seam Reducto used; no Reducto code is involved.
  * ``extract_fields`` — ai_extract, which Reducto does not do: reads the
    counterparty, dates, value and key terms straight into contract metadata.

Files are pushed to a Unity Catalog Volume first (Files API), because both SQL
functions read from a Volume rather than from bytes we hold locally.

ponytail: one statement per document, executed synchronously with a wait.
Batch the whole Volume in a scheduled Databricks Job if throughput ever matters
more than per-upload latency.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.integrations._http_retry import resilient_call
from app.integrations.ocr import OCRResult

_API_TIMEOUT = 300.0      # per-HTTP-call timeout
_POLL_SECONDS = 5.0       # how often to ask whether the statement finished
_STATEMENT_MAX_WAIT = 600.0  # ten minutes, then it belongs in a batch job



# ai_parse_document returns {document:{pages,elements}, error_status, metadata}.
# Elements carry the WHOLE document — body text, titles, section headers, tables
# (as HTML), footnotes — each with its page. This cast pulls them out in order.
_ELEMENTS_SQL = (
    "from_json(to_json(parsed:document:elements), "
    "'ARRAY<STRUCT<id:INT, type:STRING, content:STRING, confidence:DOUBLE, "
    "bbox:ARRAY<STRUCT<page_id:INT>>>>')"
)


@dataclass(frozen=True)
class ParsedDocument:
    """The complete document, not a summary of it."""

    text: str                      # every element, in reading order
    elements: list[dict[str, Any]] # each block with its type and page
    page_count: int
    errors: list[dict[str, Any]]

    def text_by_page(self) -> dict[int, str]:
        """Page-by-page text — what you need to cite 'page 14, clause 9.2'."""
        pages: dict[int, list[str]] = {}
        for el in self.elements:
            if el.get("content"):
                pages.setdefault(el.get("page_id") or 0, []).append(el["content"])
        return {p: "\n".join(v) for p, v in pages.items()}

    def tables(self) -> list[str]:
        """Tables come back as HTML — keep them intact rather than flattening
        a payment schedule into a wall of numbers."""
        return [e["content"] for e in self.elements if e.get("type") == "table" and e.get("content")]


@dataclass(frozen=True)
class ExtractedFields:
    """What ai_extract found, plus the per-field confidence that decides
    whether a human needs to look at it."""

    fields: dict[str, Any]
    confidence: dict[str, float]
    citations: dict[str, Any]
    error: str | None = None

    def needs_review(self, threshold: float = 0.7) -> list[str]:
        """Fields the model was unsure about — surface these, don't silently
        write them onto the contract."""
        return sorted(k for k, v in self.confidence.items() if v is not None and v < threshold)


# The fields we want off a contract. Descriptions are not decoration: they are
# the cheapest accuracy win available in ai_extract.
CONTRACT_SCHEMA: dict[str, dict[str, str]] = {
    "counterparty": {"type": "string", "description": "Legal name of the other party, from the signature block"},
    "our_entity": {"type": "string", "description": "Our own contracting entity"},
    "agreement_type": {"type": "string", "description": "e.g. Master Services Agreement, NDA, SoW, vendor agreement"},
    "effective_date": {"type": "string", "description": "Commencement date as YYYY-MM-DD"},
    "end_date": {"type": "string", "description": "Expiry date as YYYY-MM-DD"},
    "total_value": {"type": "number", "description": "Total value over the full term, digits only"},
    "currency": {"type": "string", "description": "ISO currency code, e.g. INR, USD"},
    "auto_renews": {"type": "boolean", "description": "True if the agreement renews automatically"},
    "notice_days": {"type": "integer", "description": "Days of notice required to terminate"},
    "governing_law": {"type": "string", "description": "Governing law and jurisdiction"},
    "liability_cap": {"type": "string", "description": "The cap on aggregate liability, quoted as written"},
}



# A canned parse/extract pair used when MOCK_DATABRICKS=true. Same shapes the
# real service returns, so every line of app code downstream is exercised for
# real — only the network call is faked.
_MOCK_ELEMENTS = [
    {"id": 0, "type": "title", "content": "MASTER SERVICES AGREEMENT", "confidence": 0.99, "page_id": 0},
    {"id": 1, "type": "text", "confidence": 0.97, "page_id": 0,
     "content": "This Master Services Agreement is entered into between Acme Laboratories Limited "
                "and Contoso Consulting LLP with effect from 1 September 2026."},
    {"id": 2, "type": "section_header", "content": "2. Term", "confidence": 0.96, "page_id": 1},
    {"id": 3, "type": "text", "confidence": 0.95, "page_id": 1,
     "content": "This Agreement shall remain in force until 31 August 2029 and shall not renew "
                "automatically. Either party may terminate on ninety (90) days written notice."},
    {"id": 4, "type": "section_header", "content": "9. Limitation of Liability", "confidence": 0.94, "page_id": 5},
    {"id": 5, "type": "text", "confidence": 0.93, "page_id": 5,
     "content": "Aggregate liability shall not exceed twelve (12) months of fees paid under this Agreement."},
    {"id": 6, "type": "table", "confidence": 0.87, "page_id": 8,
     "content": "<table><tr><th>Year</th><th>Fees</th></tr><tr><td>1</td><td>INR 100000</td></tr></table>"},
    {"id": 7, "type": "text", "content": "Governed by the laws of India.", "confidence": 0.92, "page_id": 9},
]

_MOCK_FIELDS = {
    "counterparty": ("Contoso Consulting LLP", 0.96),
    "our_entity": ("Acme Laboratories Limited", 0.94),
    "agreement_type": ("Master Services Agreement", 0.98),
    "effective_date": ("2026-09-01", 0.91),
    "end_date": ("2029-08-31", 0.90),
    "total_value": (100000, 0.62),          # deliberately low — exercises review routing
    "currency": ("INR", 0.88),
    "auto_renews": (False, 0.85),
    "notice_days": (90, 0.93),
    "governing_law": ("India", 0.89),
    "liability_cap": ("12 months of fees", 0.58),  # deliberately low
}


class DatabricksClient:
    provider = "databricks"

    @property
    def enabled(self) -> bool:
        if settings.mock_databricks:
            return True
        return bool(
            settings.databricks_host
            and settings.databricks_token
            and settings.databricks_warehouse_id
        )

    # ---- transport ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.databricks_token}"}

    async def _put_file(self, volume_path: str, content: bytes) -> None:
        """Upload the document into a Unity Catalog Volume. Overwrites, so a
        re-run of the same contract is idempotent rather than duplicating."""
        url = f"{settings.databricks_host}/api/2.0/fs/files{volume_path}?overwrite=true"
        async with httpx.AsyncClient(timeout=_API_TIMEOUT) as client:
            resp = await client.put(url, headers=self._headers(), content=content)
            resp.raise_for_status()

    @resilient_call("databricks")
    async def _sql(self, statement: str) -> list[list[Any]]:
        """Run one SQL statement on the serverless warehouse and return its rows.

        Long documents in precision mode routinely run past the 50s the API
        will hold a request open for, so we ask Databricks to CONTINUE and then
        poll. Cancelling at the timeout — the first thing I tried — kills
        exactly the documents precision mode exists for.
        """
        base = f"{settings.databricks_host}/api/2.0/sql/statements"
        payload = {
            "warehouse_id": settings.databricks_warehouse_id,
            "statement": statement,
            "wait_timeout": "50s",
            "on_wait_timeout": "CONTINUE",
        }
        async with httpx.AsyncClient(timeout=_API_TIMEOUT) as client:
            resp = await client.post(base, headers=self._headers(), json=payload)
            resp.raise_for_status()
            body = resp.json()

            state = (body.get("status") or {}).get("state")
            statement_id = body.get("statement_id")
            waited = 0.0
            while state in ("PENDING", "RUNNING") and waited < _STATEMENT_MAX_WAIT:
                await asyncio.sleep(_POLL_SECONDS)
                waited += _POLL_SECONDS
                poll = await client.get(f"{base}/{statement_id}", headers=self._headers())
                poll.raise_for_status()
                body = poll.json()
                state = (body.get("status") or {}).get("state")

        if state in ("PENDING", "RUNNING"):
            raise RuntimeError(
                f"Databricks statement still {state} after {int(waited)}s — move this "
                "document to a batch job rather than an inline call"
            )
        if state != "SUCCEEDED":
            message = ((body.get("status") or {}).get("error") or {}).get("message", state)
            raise RuntimeError(f"Databricks statement {state}: {message}")
        return (body.get("result") or {}).get("data_array") or []

    def _volume_path(self, filename: str) -> str:
        """A unique path per upload so two contracts with the same filename
        cannot overwrite each other."""
        safe = filename.replace("'", "").replace("\\", "").replace("..", "")
        return f"{settings.databricks_volume.rstrip('/')}/{uuid.uuid4().hex}-{safe}"

    # ---- capabilities ------------------------------------------------------

    async def parse_document(self, *, filename: str, content: bytes) -> ParsedDocument:
        """Everything in the document: full text, every block with its type and
        page, tables as HTML, plus any per-page parse errors."""
        if settings.mock_databricks:
            return ParsedDocument(
                text="\n".join(e["content"] for e in _MOCK_ELEMENTS),
                elements=list(_MOCK_ELEMENTS),
                page_count=10,
                errors=[],
            )
        if not self.enabled:
            return ParsedDocument(text="", elements=[], page_count=0, errors=[])

        path = self._volume_path(filename)
        await self._put_file(path, content)
        rows = await self._sql(
                "SELECT "
                f"  concat_ws('\\n', transform({_ELEMENTS_SQL}, e -> coalesce(e.content, ''))), "
                f"  to_json({_ELEMENTS_SQL}), "
                "  size(from_json(to_json(parsed:document:pages), 'ARRAY<STRUCT<id:INT>>')), "
                "  to_json(parsed:error_status) "
                "FROM (SELECT ai_parse_document(content, map('version', '2.0')) AS parsed "
                f"      FROM READ_FILES('{path}', format => 'binaryFile'))"
        )
        if not rows:
            return ParsedDocument(text="", elements=[], page_count=0, errors=[])

        text, elements_json, page_count, errors_json = (list(rows[0]) + [None] * 4)[:4]
        elements = []
        for el in json.loads(elements_json or "[]"):
            bbox = el.get("bbox") or []
            elements.append({
                "id": el.get("id"),
                "type": el.get("type"),
                "content": el.get("content"),
                "confidence": el.get("confidence"),
                "page_id": (bbox[0] or {}).get("page_id") if bbox else None,
            })
        return ParsedDocument(
            text=(text or "").strip(),
            elements=elements,
            page_count=int(page_count) if page_count not in (None, "") else 0,
            errors=json.loads(errors_json or "[]") or [],
        )

    async def extract_text(self, *, filename: str, mime_type: str, content: bytes) -> OCRResult:
        """OCR a document. Same signature and return type as ReductoClient, so
        this can be swapped in at the existing call site. Wraps parse_document,
        which is where the real work happens."""
        if not settings.mock_databricks and not self.enabled:
            return OCRResult(
                text="",
                provider=self.provider,
                quality_score=0.0,
                metadata={"mode": "disabled", "filename": filename, "mime_type": mime_type},
            )
        parsed = await self.parse_document(filename=filename, content=content)
        return OCRResult(
            text=parsed.text,
            provider=self.provider,
            quality_score=0.9 if parsed.text else 0.0,
            elements=parsed.elements,  # keep the structure instead of flattening it away
            metadata={
                "filename": filename,
                "mime_type": mime_type,
                "page_count": parsed.page_count,
                "element_count": len(parsed.elements),
                "table_count": len(parsed.tables()),
                "parse_errors": parsed.errors,
            },
        )

    async def extract_fields(
        self,
        *,
        filename: str,
        content: bytes,
        schema: dict[str, dict[str, str]] | None = None,
        precision: bool | None = None,
    ) -> ExtractedFields:
        """Read the contract's key terms straight off the document."""
        if settings.mock_databricks:
            return ExtractedFields(
                fields={k: v for k, (v, _) in _MOCK_FIELDS.items()},
                confidence={k: c for k, (_, c) in _MOCK_FIELDS.items()},
                citations={k: [i] for i, k in enumerate(_MOCK_FIELDS)},
            )
        if not self.enabled:
            return ExtractedFields(fields={}, confidence={}, citations={}, error="databricks disabled")

        schema_json = json.dumps(schema or CONTRACT_SCHEMA).replace("'", "''")
        use_precision = settings.databricks_precision_mode if precision is None else precision
        options = [
            "'version', '2.1'",
            "'enableCitations', 'true'",
            "'enableConfidenceScores', 'true'",
        ]
        if use_precision:
            options.append("'mode', 'precision'")

        path = self._volume_path(filename)
        await self._put_file(path, content)
        rows = await self._sql(
            "SELECT to_json(ai_extract(ai_parse_document(content), "
            f"  '{schema_json}', map({', '.join(options)}))) "
            f"FROM READ_FILES('{path}', format => 'binaryFile')"
        )
        if not rows or not rows[0]:
            return ExtractedFields(fields={}, confidence={}, citations={}, error="no rows returned")

        payload = json.loads(rows[0][0])
        if payload.get("error_message"):
            return ExtractedFields(fields={}, confidence={}, citations={}, error=payload["error_message"])

        response = payload.get("response") or {}
        fields, confidence, citations = {}, {}, {}
        for key, node in response.items():
            if isinstance(node, dict):
                fields[key] = node.get("value")
                if node.get("confidence_score") is not None:
                    confidence[key] = node["confidence_score"]
                if node.get("citation_ids"):
                    citations[key] = node["citation_ids"]
            else:
                fields[key] = node
        return ExtractedFields(fields=fields, confidence=confidence, citations=citations)


databricks_client = DatabricksClient()
