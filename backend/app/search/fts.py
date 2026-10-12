"""Shared Postgres full-text-search helpers.

Used by /search (structured search) and /contract-brain/search (hybrid
semantic + keyword search). The left(…) cap MUST match the GIN expression
indexes created in migration 0015, or Postgres won't use them.
"""

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contract_brain.models import ClauseExtraction
from app.contract_files.models import ContractTextSnapshot

FTS_TEXT_CAP = 200_000

# The empty selectors MUST be quoted: Postgres does not accept a bare empty
# value here and instead reads the separating comma as the selector, which
# injects stray commas around every matched word in the excerpt.
HEADLINE_OPTS = 'StartSel="", StopSel="", MaxFragments=3, MaxWords=28, MinWords=8'


def like_contains(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def fts_usable(db: Session, q: str) -> bool:
    """True when websearch_to_tsquery produces a non-empty query for q."""
    nodes = db.scalar(select(func.numnode(func.websearch_to_tsquery("english", q))))
    return bool(nodes)


def or_terms(q: str) -> str:
    """The same question rewritten as an explicit OR for websearch_to_tsquery.

    websearch_to_tsquery ANDs every term it finds, so ONE word that appears
    nowhere in the corpus zeroes the whole keyword lens — and a natural-language
    question ("What is the liability cap in our MSAs?") almost always carries
    one. Used only as a fallback when the AND form matched nothing, so precise
    keyword queries keep their precision. Uses websearch's own `or` operator
    rather than rewriting the generated tsquery text.
    """
    return " or ".join(re.findall(r"[A-Za-z0-9']+", q))


def snapshot_vector():
    return func.to_tsvector(
        "english", func.left(ContractTextSnapshot.text, FTS_TEXT_CAP)
    )


def clause_vector():
    return func.to_tsvector(
        "english",
        func.coalesce(ClauseExtraction.heading, "")
        + " "
        + func.left(ClauseExtraction.text, FTS_TEXT_CAP),
    )


def snapshot_headline(tsq):
    return func.ts_headline(
        "english",
        func.left(ContractTextSnapshot.text, FTS_TEXT_CAP),
        tsq,
        HEADLINE_OPTS,
    )


def text_matches(text: str, q: str) -> list[dict]:
    """Exact-substring excerpts with character offsets (up to 5)."""
    matches: list[dict] = []
    lowered = text.lower()
    needle = q.lower()
    start = lowered.find(needle)
    while start >= 0 and len(matches) < 5:
        end = start + len(q)
        excerpt_start = max(0, start - 160)
        excerpt_end = min(len(text), end + 160)
        matches.append(
            {
                "start_char": start,
                "end_char": end,
                "excerpt": text[excerpt_start:excerpt_end],
            }
        )
        start = lowered.find(needle, end)
    return matches
