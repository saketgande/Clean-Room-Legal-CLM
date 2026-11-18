"""Answer shape of the Nice-class suggester (POST /trademarks/classify-goods). Pure Pydantic."""
from __future__ import annotations

from pydantic import BaseModel, Field


class NiceClassSuggestion(BaseModel):
    nice_class: str = Field(description="The single best-fit class number, 1-45, as a string.")
    reasoning: str = Field(description="One short, concrete sentence.")
