"""Answer shape of the renewal recommendation (GET /renewals/{id}/recommendation). Pure Pydantic."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RenewalRecommendationOutput(BaseModel):
    decision: Literal["renew", "renegotiate", "terminate"] = Field(description="The single recommended action.")
    rationale: str = Field(description="One or two sentences, specific to the facts given — no boilerplate.")
    confidence: Literal["low", "medium", "high"] = Field(
        description="high: need, performance, economics, risk and timing clearly support one action; "
        "medium: one action is better supported but material facts are missing; "
        "low: the record is sparse, conflicting or closely balanced."
    )
