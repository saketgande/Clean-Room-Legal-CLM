"""Answer shape of the clause-parent judge (documents.hierarchy). Pure Pydantic."""
from __future__ import annotations

from pydantic import BaseModel, Field, StrictInt

from ._types import lenient_items


class ParentAnswer(BaseModel):
    seq: StrictInt = Field(description="The question's number.")
    # Required, nullable: null is an answer (top level); a row with no "parent" key is unfinished and is dropped.
    parent: StrictInt | None = Field(description="Number of the block this one sits directly inside; null = top level.")


class ClauseHierarchyOutput(BaseModel):
    answers: lenient_items(ParentAnswer) = Field(description="One row per question, each answered once.")  # type: ignore[valid-type]
