"""Output of the ``litigation_intake_agent`` feature: the case sheet for a dispute plus its workflow pick.

Same keys and required set as the hand-written dict in ``intake/litigation_agent.py`` sent before. Tighter:
  * ``settlement_posture`` is a fixed list (it adds "unknown", the fourth word the prompt always offered
    but the old schema left out); an unlisted word becomes "unknown", never a guess,
  * each deadline gains ``due_date``, a real calendar date, next to ``date``. ``date`` keeps the request's
    own wording ("15 days from receipt"), which is what the workflow step shows, so the screen is unchanged.
The stored ``litigation_assessment`` shape is not this model: ``assess_litigation`` maps it.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.ai.output_format._types import LenientDate, Score, Text, lenient_choice

SETTLEMENT_POSTURES = ("none", "proposed", "likely", "unknown")


class StatutoryDeadline(BaseModel):
    what: str = Field(description="the deadline, in a few words, e.g. what must be done")
    date: Text = Field(
        default=None,
        description="the date or period exactly as the request words it, e.g. '15 days from receipt of the "
        "notice' or '2026-12-01'; copy the wording, never an explanation",
    )
    due_date: LenientDate = Field(
        default=None,
        description="the calendar date as YYYY-MM-DD, only when the request states a calendar date or gives an "
        "explicit start date and period that make it certain; null for a period with no known start, or unknown",
    )
    source: Text = Field(default=None, description="the phrase in the request, or the rule, that gives the deadline")


class LitigationIntakeOutput(BaseModel):
    matter_type: str = Field(description="short label for the kind of matter, e.g. a demand notice, a hold, a claim")
    statutory_deadlines: list[StatutoryDeadline] = Field(
        default_factory=list, description="every deadline the request states or clearly implies; empty when none"
    )
    legal_hold_required: bool = Field(
        description="true when litigation, an investigation, a claim or a preservation demand is actual or "
        "reasonably anticipated"
    )
    outside_counsel_likely: bool = Field(
        description="true when complexity, forum, stakes or an active proceeding make outside counsel likely"
    )
    settlement_posture: lenient_choice(SETTLEMENT_POSTURES, fallback="unknown", fold_case=True) = Field(  # type: ignore[valid-type]
        description="none, proposed, likely or unknown. Use proposed or likely only on an explicit proposal or "
        "facts supporting settlement talks; a demand alone is not one."
    )
    key_parties: list[str] = Field(default_factory=list, description="the parties and their roles")
    summary: str = Field(description="two or three sentences on what happened and what is urgent")
    assessment_confidence: Score = Field(
        description="0-1: confidence in the EXTRACTED FACTS above (matter type, deadlines, hold/counsel calls), by "
        "how much concrete detail the request gives — distinct from flow_confidence, which is only about the "
        "workflow pick."
    )
    flow_id: str | None = Field(description="best-fit flow id from the catalog, exactly as listed, or null")
    flow_confidence: Score = Field(description="0-1: certainty in the workflow pick only")
    flow_reasoning: str = Field(description="one or two sentences on why that workflow")
