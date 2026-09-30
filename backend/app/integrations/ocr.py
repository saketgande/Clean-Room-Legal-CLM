"""The shape every OCR provider returns.

Vendor-neutral on purpose: Reducto and Databricks both produce one of these, so
contract_files.service can swap providers without knowing which ran. It lived
in reducto.py originally, which made the Databricks client look as though it
depended on Reducto — it never did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class OCRResult:
    text: str
    provider: str
    quality_score: float
    metadata: dict
    # Structured elements from the parser (type/content/confidence/page_id).
    # Optional so a provider that returns only text still fits this shape.
    elements: list[dict[str, Any]] = field(default_factory=list)


@runtime_checkable
class OCRProvider(Protocol):
    """The shape every OCR provider (Reducto, Databricks) satisfies.

    Part of the DI migration (see backend/DI_MIGRATION.md): formalizes the
    interface `contract_files.service` already selects between structurally
    (``databricks_client if databricks_client.enabled else reducto_client``),
    so that selection can depend on this Protocol instead of the concrete
    classes and be swapped for a fake in tests.
    """

    provider: str

    async def extract_text(self, *, filename: str, mime_type: str, content: bytes) -> OCRResult: ...
