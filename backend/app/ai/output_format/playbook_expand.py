"""Answer shape of "Expand playbook" (draft rules for missing standard clauses). Pure Pydantic."""
from __future__ import annotations

from pydantic import BaseModel, Field

from ._types import Text, lenient_choice, lenient_items

RISK_LEVELS = ("low", "medium", "high")


class DraftedRule(BaseModel):
    clause_type: str = Field(description="One of the requested clause types, exactly as given.")
    preferred_position: str = Field(description="The company-favourable position, concrete and commercially reasonable.")
    fallback_position: Text = Field(default=None, description="What we accept if the preferred position is refused.")
    prohibited_language: Text = Field(default=None, description="Wording that must not appear in this clause.")
    risk_level: lenient_choice(RISK_LEVELS, fallback="medium") = Field(  # type: ignore[valid-type]
        description="Risk if the preferred position is lost: low, medium or high."
    )
    rationale: str = Field(description="One line on why.")
    approval_required: bool = Field(default=False, description="True when giving way needs sign-off.")


class PlaybookExpandOutput(BaseModel):
    rules: lenient_items(DraftedRule) = Field(description="One rule for each requested clause type.")  # type: ignore[valid-type]
