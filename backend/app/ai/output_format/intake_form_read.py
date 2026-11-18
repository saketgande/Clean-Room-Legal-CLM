"""Output of the ``intake_form_read`` feature: the model's advisory note on a request filed on a form.

Same four keys, same nested mismatch keys and same required set as the hand-written dict in
``intake/triage_agent.py`` sent before; the descriptions now carry what the prompt's ``what_to_return`` block
used to say in prose. The stored ``ai_triage.read`` shape is not this model: ``aegis_read`` maps it (it drops
the mismatches where ``same`` is true and caps lengths), so the UI sees no change.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class FormMismatch(BaseModel):
    field: str = Field(description="which form field the text contradicts, e.g. agreement type, value, date, party")
    form_says: str = Field(description="what the form says, briefly")
    text_says: str = Field(description="what the description or attachment states instead")
    same: bool = Field(description="true if the two actually agree")


class IntakeFormReadOutput(BaseModel):
    summary: str = Field(description="one or two sentences: what the requester actually wants")
    mismatches: list[FormMismatch] = Field(
        description="facts the description or an attachment states differently from the form (type, value, date, "
        "party). Only real contradictions; things that agree, or that the text does not mention, are not "
        "mismatches. Usually empty."
    )
    bespoke_asks: list[str] = Field(
        description="asks the standard template will not cover, e.g. the counterparty's own paper, custom terms, "
        "unusual structures; empty when there are none"
    )
    negotiation_points: list[str] = Field(
        description="terms the requester or an attachment signals will be negotiated (liability, payment, IP, "
        "exclusivity, termination); empty when there are none"
    )
