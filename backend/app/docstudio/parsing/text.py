"""Turning a plain string into blocks.

Shared by the `.txt` parser and by OCR output, so text that arrives as one long
string is cut into clauses the same way whatever produced it.
"""

import re

from .base import ParsedBlock, ParsedDocument
from .labels import LEADING_NUMBER, label_only, level_for

# A bare number glued to the front of lower-case prose is the page footer
# that OCR ran into the next page's text — "...and health," then
# "2 accident and workers' compensation benefits". Read as a clause number
# it mislabels the clause AND sits between the two halves of a sentence, so
# reflow cannot rejoin them either. On one 11-page scan that was 9 of 86
# clauses. Furniture stripping cannot catch it: OCR never made it a block of
# its own.
_BARE_NUMBER_RUN_ON = re.compile(r"^\s*(\d{1,3})\s+(?=\S)")

# The optional "- " is a markdown bullet. OCR providers return numbered
# clauses as list items — "- 1.1 \"Agreement\" means..." — and requiring the
# digit first silently drops the number from every sub-clause in the contract,
# leaving its text present but uncitable.
# OCR providers commonly return markdown, where a heading is "## TERM".
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")




def _page_number_runs_on(chunks: list[str], page_count: int | None) -> set[int]:
    """Which chunks open with a page number that OCR ran into the text.

    Decided across the whole document rather than line by line, because no
    single line tells them apart: "2 accident and workers..." and "2 Services"
    look identical. What distinguishes them is the *set* — page numbers form an
    ascending run whose highest value is the page count, one per page.

    The guard that keeps this safe is that highest value. A contract numbering
    its clauses 1, 2, 3 without dots reaches 20-odd in 36 pages and is left
    alone; a page-number run reaches exactly the page count.
    """
    if not page_count or page_count < 3:
        return set()
    found: list[tuple[int, int]] = []
    for index, chunk in enumerate(chunks):
        match = _BARE_NUMBER_RUN_ON.match(chunk)
        if match:
            found.append((index, int(match.group(1))))
    if len(found) < max(3, page_count - 3):
        return set()
    values = [n for _, n in found]
    if sorted(values) != values or len(set(values)) != len(values):
        return set()  # not a run
    if not (page_count - 1 <= max(values) <= page_count + 1):
        return set()
    return {index for index, _ in found}


def blocks_from_text(text: str, *, page_count: int | None = None) -> list[ParsedBlock]:
    """Split on blank lines — the only structural signal a flat string carries.

    Markdown headings are recognised because that is what OCR returns, and a
    heading that arrives as an ordinary paragraph loses the section structure
    the provider had already worked out.
    """
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text or "") if c.strip()]
    page_numbers = _page_number_runs_on(chunks, page_count)

    blocks: list[ParsedBlock] = []
    # A chunk that is only a clause number: the number and its clause were split
    # apart by the layout, so hold it and give it to the next chunk. Left alone
    # it becomes a clause whose entire content is "3." — no text to read, no
    # text to cite — while the real clause beside it has no number.
    pending: str | None = None
    for position, body in enumerate(chunks):
        if position in page_numbers:
            # Dropped rather than kept as a label: the digits are page
            # furniture, and removing them lets the sentence they interrupt be
            # rejoined downstream.
            body = _BARE_NUMBER_RUN_ON.sub("", body, count=1).strip()
            if not body:
                continue
        orphan = label_only(body)
        if orphan:
            pending = orphan
            continue

        heading = _MARKDOWN_HEADING.match(body.split("\n", 1)[0])
        if heading and "\n" not in body:
            # A heading still carries its clause number — "## 1. DEFINITIONS".
            # Pulling the number out here matters: the markdown level says how
            # the provider styled the line, the number says where the clause
            # actually sits in the contract, and only the second is citable.
            title = heading.group(2).strip()
            match = LEADING_NUMBER.match(title)
            label = match.group(1).strip() if match else None
            blocks.append(
                ParsedBlock(
                    text=title[match.end():].strip() if match else title,
                    kind="heading",
                    number_label=label,
                    level=level_for(label) if label else len(heading.group(1)),
                )
            )
            continue

        match = LEADING_NUMBER.match(body)
        label = match.group(1).strip() if match else None
        text_body = body[match.end():].strip() if match else body
        if pending and not label:
            label, text_body = pending, body
        pending = None
        blocks.append(
            ParsedBlock(
                text=text_body,
                kind="paragraph",
                number_label=label,
                level=level_for(label),
            )
        )
    # A trailing orphan number with nothing after it is a page number, not a
    # clause, and is dropped rather than stored as an empty one.
    return blocks


class TextParser:
    name = "text"
    # 2: the tree is built from numbering (`tree.py`), so parents differ.
    # 3: "Item 5" read as a number; a numbered run after "WHEREAS:" is asked about.
    # 4: numbering that starts again at 1 is asked about, not a new part.
    version = "4"

    def parse(self, content: bytes, *, filename: str) -> ParsedDocument:
        # `replace` rather than `strict`: a mis-declared encoding should cost a
        # few characters, not the whole document.
        raw = content.decode("utf-8", errors="replace")
        blocks = blocks_from_text(raw)
        warnings = [] if blocks else ["The file contained no text."]
        return ParsedDocument(blocks=blocks, warnings=warnings)
