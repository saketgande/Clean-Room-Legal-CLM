"""Answer shape of the trademark journal page reader (one page image per call). Pure Pydantic."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class JournalEntry(BaseModel):
    product_name: str | None = Field(description="Exact word mark as printed; null for a device/label with no text name.")
    mark_type: Literal["word", "device", "combination", "sound", "other"] = Field(
        description="word: plain text only; device: image/logo with no word-mark name; combination: name plus distinct logo."
    )
    tm_id: str = Field(description="Application or registration number as a string, leading zeros kept.")
    tm_date: str = Field(description="Date printed on the same line as tm_id, DD/MM/YYYY as printed.")
    address: str = Field(description="Proprietor name, address and attorney lines, joined with newlines in printed order.")
    used_since: str | None = Field(description="Used Since date, DD/MM/YYYY; null when Proposed to be Used or absent.")
    proposed_to_be_used: bool = Field(description="True only when 'Proposed to be Used' is printed.")
    jurisdiction: str | None = Field(description="The single city printed after the used-since/proposed line, as printed.")
    goods_services: str | None = Field(description="Complete goods/services wording incl. conditions, newline-joined.")
    has_product_image: bool = Field(description="True if the entry shows any product photo, label, logo or mark image.")


class JournalPageOutput(BaseModel):
    entries: list[JournalEntry] = Field(description="Every separate entry on the page, top to bottom.")
