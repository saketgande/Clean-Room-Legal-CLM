"""Output of the ``email_triage_agent`` feature: is an inbound email a legal matter, and which kind.

Same three keys and required set as the hand-written dict in ``intake/email_triage_agent.py`` sent before.
The categories are the intake categories (``intake_triage.Category``); a category outside the list becomes
null, as the old ``in _CATEGORIES`` check did.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.ai.output_format._types import Score, lenient_choice
from app.ai.output_format.intake_triage import Category

CATEGORIES = tuple(c.value for c in Category)


class EmailTriageOutput(BaseModel):
    is_clm_related: bool = Field(
        description="True for a genuine legal/contract matter for an in-house legal team. False for "
        "newsletters, marketing, personal correspondence, receipts, or anything not actually a legal request."
    )
    category: lenient_choice(CATEGORIES) = Field(  # type: ignore[valid-type]
        description="Best-fit category when is_clm_related is true; null when false."
    )
    confidence: Score = Field(
        description="0.0-1.0 confidence in this classification. High only when the message clearly states its purpose."
    )
