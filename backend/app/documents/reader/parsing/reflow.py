"""Rejoining a sentence that a page break cut in half.

A contract does not stop at the bottom of a page, but a parser does. Both the
native reader and OCR return one block per page region, so a clause running
across a page boundary arrives as two:

    "...Mindtree personnel shall observe all safety and other applicable rules in"
    "effect at such Client Sites, provided that reasonable notice of the rules..."

Two halves of one sentence, stored as two clauses. Neither can be quoted, cited
or reviewed on its own, and a reader scanning the clause list sees a sentence
that simply stops.

This must run **after** page furniture is removed. Otherwise the footer sits
between the two halves — in a real MSA, "©Mindtree Limited 2017" landed in the
middle of a confidentiality clause — and there is nothing adjacent left to join.
"""

from dataclasses import replace

from .base import ParsedBlock

# A block ending in any of these finished its thought. Anything else was cut
# off. The colon matters: "...of two (2) types of projects:" introduces a list
# and must not absorb the first item.
SENTENCE_ENDS = '.:;!?"”)'

# A sentence cannot end on any of these, so a block that does was cut — and
# the half after the break belongs to it whatever letter it opens with. That is
# what the lower-case test alone missed: "...retain all Deliverables and a" was
# followed by "Deliverables developed…", a capital, because the word is a
# defined term. Every one of the four split clauses on the Franklin Madison MSA
# ended this way: "…rules in", "…engaged by", "…given by".
DANGLING_WORDS = {
    "a", "an", "the", "this", "these", "those", "such", "any", "all", "both",
    "of", "in", "on", "at", "to", "for", "with", "by", "from", "as", "into",
    "upon", "under", "over", "between", "during", "without", "within",
    "that", "which", "who", "whom", "whose", "where", "when",
    "shall", "will", "may", "must", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "not", "no", "its", "their", "his", "her", "our",
}
# A block ending "…; or" or "…, and" is a list item continuing into the next
# item by design. It is not cut, and joining it would merge two items.
LIST_CONJUNCTIONS = {"or", "and", "nor"}

_HYPHENATED = "a word hyphenated across the break"

# How many times one clause may swallow the next. A sentence crossing a page
# boundary is one join; crossing two boundaries is two. Beyond that the parser
# is not reading the layout at all, and merging further turns a page of badly
# split text into a single unreadable clause instead of leaving the damage
# visible. Ten unterminated fragments merged into one block before this.
MAX_CONSECUTIVE_JOINS = 3


def join_reason(previous: ParsedBlock, current: ParsedBlock) -> str | None:
    """Why `current` is the rest of `previous` — or None when it is not.

    Returned as a reason, not a yes, because every join changes a clause's
    text and a reviewer should be able to see why each one happened.
    """
    # A numbered clause is a new clause by definition, whatever precedes it.
    if current.number_label:
        return None
    # Headings and tables are self-contained; a table joined to a paragraph
    # would destroy the row structure that makes it readable.
    if previous.kind in {"heading", "table"} or current.kind in {"heading", "table"}:
        return None
    before, after = previous.text.rstrip(), current.text.lstrip()
    if not before or not after or before[-1] in SENTENCE_ENDS:
        return None
    if before.endswith("-") and not before.endswith(" -") and after[0].isalpha():
        return _HYPHENATED
    words = before.split()
    last = words[-1].lower().strip(",;:\"'“”‘’")
    if last in LIST_CONJUNCTIONS and len(words) > 1 and words[-2][-1] in ";,":
        # "…the Deliverables; or" finishes an item and hands on to the next
        # one. Only the conjunction *after* a comma or semicolon says so: a
        # bare "…the goods and" is a sentence cut in half like any other.
        return None
    if last in DANGLING_WORDS and last not in LIST_CONJUNCTIONS:
        return f"ended on “{last}”"
    # An opening lower-case letter is still good evidence: a new sentence
    # starts with a capital. A block starting with one is far more likely a
    # new paragraph the parser split for layout reasons.
    if after[0].islower():
        return "the sentence runs on in lower case"
    return None


def join_wrapped_blocks(blocks: list[ParsedBlock]) -> tuple[list[ParsedBlock], list[dict]]:
    """Merge blocks that are two halves of one sentence.

    Returns the merged blocks and a record of every join — worth keeping,
    because a document needing many of them is one whose layout the parser is
    reading badly, and because each join changed a clause's text.
    """
    if not blocks:
        return blocks, []
    merged: list[ParsedBlock] = [blocks[0]]
    records: list[dict] = []
    run = 0  # consecutive joins onto the block currently being built
    for block in blocks[1:]:
        previous = merged[-1]
        reason = None if run >= MAX_CONSECUTIVE_JOINS else join_reason(previous, block)
        if reason is None:
            merged.append(block)
            run = 0
            continue
        before, after = previous.text.rstrip(), block.text.lstrip()
        # A trailing hyphen is a word broken across the boundary
        # ("compli-" + "ance"), so it joins with no space and the hyphen goes.
        text = before[:-1] + after if reason == _HYPHENATED else f"{before} {after}"
        # The clause starts where it starts — its number, its first page — but
        # it now also sits where the rest of it sits. Keeping only the first
        # half's position would light up half a clause in a viewer.
        merged[-1] = replace(
            previous,
            text=text,
            regions=tuple(previous.all_regions + block.all_regions),
            joins=previous.joins + (reason,),
        )
        pages = sorted({r["page"] for r in merged[-1].all_regions if r.get("page") is not None})
        records.append({"reason": reason, "pages": pages, "text": f"…{before[-40:]} ⏎ {after[:40]}…"})
        run += 1
    return merged, records
