"""Output of the ``notice_extraction_agent`` feature: register fields read from an uploaded legal notice.

Same keys and required set as the hand-written dict in ``notices/extraction.py`` sent before, with real
types: dates are dates, ``period_count`` a whole number >= 0, the vocabularies fixed lists. Every one is
lenient on the way in (see ``_types``), so a bad field becomes empty and the others survive, exactly as the
``_clean_*`` helpers did. The deadline stays a *reading of the notice's own wording*; code, not the model,
counts the date (``notices.deadlines.compute_due``).

The three vocabularies are copies of ``notices.models.NOTICE_TYPES`` and ``notices.extraction.PERIOD_UNITS`` /
``RUNS_FROM`` (this package may not import the database layer); ``tests/test_notice_extraction_contract.py``
fails if they drift apart.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.ai.output_format._types import LenientDate, NonNegInt, Score, Text, lenient_choice

NOTICE_TYPES = ("demand", "cease_and_desist", "breach", "termination", "statutory", "recovery", "infringement", "other")
PERIOD_UNITS = ("days", "business_days", "weeks", "months")
RUNS_FROM = ("notice_date", "receipt", "other")


class NoticeDeadlineReading(BaseModel):
    stated: bool = Field(
        description="True only when the notice itself writes a deadline (a date or a period). False when none is "
        "written or one would come only from a statute or custom."
    )
    explicit_date: LenientDate = Field(
        default=None,
        description="A calendar date the notice writes out as the deadline, as YYYY-MM-DD. Null when it gives a period.",
    )
    period_count: NonNegInt = Field(default=None, description="The number in a period such as '15 days'; null when none.")
    clear_days: bool = Field(default=False, description="True only when the notice says the days are clear days.")
    period_unit: lenient_choice(PERIOD_UNITS) = Field(default=None, description="The unit of the period.")  # type: ignore[valid-type]
    runs_from: lenient_choice(RUNS_FROM) = Field(  # type: ignore[valid-type]
        default=None, description="What the period runs from: the notice date, receipt, or something else."
    )
    runs_from_text: Text = Field(default=None, description="What the period runs from, in the notice's words.")
    quote: Text = Field(default=None, description="The sentence that states the deadline.")


class NoticeExtractionOutput(BaseModel):
    counterparty_name: Text = Field(
        default=None,
        description="The party that SENT the notice (or, for an outbound notice, its recipient). Legal entity name as written.",
    )
    counterparty_ref: Text = Field(
        default=None, description="The sender's own reference/file number for this notice, if printed on it."
    )
    notice_type: lenient_choice(NOTICE_TYPES) = Field(  # type: ignore[valid-type]
        default=None, description="Best-fit category for this notice."
    )
    subject: Text = Field(default=None, description="A short factual title, under 120 characters.")
    notice_date: LenientDate = Field(default=None, description="Date printed on the notice, as YYYY-MM-DD.")
    deadline: NoticeDeadlineReading | None = Field(
        default=None,
        description="The notice's own wording of the response deadline. Copy it; never compute a date.",
    )
    demanded_action: Text = Field(
        default=None, description="One or two sentences: what the notice demands, and by when."
    )
    confidence: Score = Field(
        description="0.0-1.0 — how confident you are overall. Be honest; low for a scanned or partial document."
    )

    @field_validator("deadline", mode="before")
    @classmethod
    def _deadline_must_be_an_object(cls, value):
        # Not an object = no reading at all (None), which is different from {"stated": false}.
        return value if isinstance(value, dict) else None
