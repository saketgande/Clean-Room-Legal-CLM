"""The shape every OCR provider returns.

Vendor-neutral on purpose: Reducto and Databricks both produce one of these, so
contract_files.service can swap providers without knowing which ran. It lived
in reducto.py originally, which made the Databricks client look as though it
depended on Reducto — it never did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OCRResult:
    text: str
    provider: str
    quality_score: float
    metadata: dict
    # Structured elements from the parser (type/content/confidence/page_id).
    # Optional so a provider that returns only text still fits this shape.
    elements: list[dict[str, Any]] = field(default_factory=list)
