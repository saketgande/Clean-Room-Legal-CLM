"""Output of the ``flow_router`` feature: which governance workflow an intake request should ride.

Same keys and required set as the hand-written dict in ``intake/flow_agent.py`` sent before; what is
tighter is the types (``confidence`` is a 0..1 score, the alternatives are typed) and the descriptions,
which now carry what the prompt's OUTPUT block used to say in prose. The stored ``flow_suggestion`` shape
is not this model: ``suggest_flow`` maps it (adds ``flow_name``, ``source``, ``steps``), so the UI sees
no change.

``flow_id`` stays a plain string: the workflow list is live data, so "is it really one of this
org's workflows" is checked in code against the catalog, not by a fixed list here.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.ai.output_format._types import Score


class FlowAlternative(BaseModel):
    flow_id: str = Field(description="id of another catalog workflow that was a plausible fit")
    why: str = Field(description="why it was considered, in one short sentence")


class FlowRouterOutput(BaseModel):
    flow_id: str | None = Field(
        description="id of the single best-fit flow from the catalog, exactly as listed, or null if none fits "
        "(then needs_human must be true). Never invent an id."
    )
    confidence: Score = Field(
        description="0..1 certainty that this workflow is the right match for the request's matter and handling; "
        "not certainty about the underlying legal facts"
    )
    reasoning: str = Field(
        description="one or two sentences, plain English, grounded in the request and the catalog"
    )
    alternatives: list[FlowAlternative] = Field(
        default_factory=list,
        description="other catalog workflows that were plausible, best first; empty when the pick is clear",
    )
    needs_human: bool = Field(
        description="true when no workflow fits, two are equally plausible, the request is too incomplete to route, "
        "or the matter is high-stakes or outside the catalog's boundaries"
    )
