"""Fill-in blanks left in contract text: "[INSERT CORRECT EXPIRY DATE]", "[●]", "[TBD]", "[Role]".

A blank is a promise that someone will supply a fact later. It must not reach a counterparty,
and the AI must not write one into a contract as if it were a fix (the automatic re-review
flags it as a new issue, proposes another fix with another blank, and each round is a paid
call). This is one detector for all three places that care: the edit engine, accepting an
edit, and sending for signature.

The rule is "a bracket with words in it is a blank, unless it is a known editorial mark".
The first version listed the words that make a blank (Insert, Date, Company Name…) and was
wrong within minutes on real data: the AI wrote "[Org's Home State/Country]" and the
Statement of Work template holds "[Role]", "[YYYY-MM-DD]" and "[List known exclusions, if any.]".
A list of blank-words can never be complete; the list of ordinary brackets in contracts is short.
"""

from __future__ import annotations

import re

MAX_LISTED = 20
_MAX_INSIDE = 120

_BRACKET = re.compile(r"\[([^\[\]\n]{1," + str(_MAX_INSIDE) + r"})\]")

# Marks that mean "this is a blank": [●]  [•]  [___]
_MARKER = re.compile(r"[●•]+|_{2,}")

# Brackets that are ordinary contract writing, so are not blanks.
_EDITORIAL = (
    re.compile(r"^\W*$"),                                     # [ ] [*] [***] [...] [-]
    re.compile(r"^\d[\d.,\-/]*$"),                            # [1] [2019] [3.2]
    re.compile(r"^[A-Za-z]$"),                                # [t]he  [x]  [a]
    re.compile(r"^(?:sic|redacted|omitted|reserved|confidential|logo|seal|signature|signed)$", re.IGNORECASE),
    re.compile(r"^emphasis\s+(?:added|supplied|omitted|in original)$", re.IGNORECASE),
    re.compile(r"^(?:intentionally|deliberately)\s+(?:omitted|left blank|deleted)$", re.IGNORECASE),
    re.compile(r"^signature\s+pages?\s+(?:follows?|attached)$", re.IGNORECASE),
    re.compile(r"^(?:remainder|rest)\s+of\s+(?:this\s+)?page\s+(?:is\s+)?intentionally\s+left\s+blank$", re.IGNORECASE),
    re.compile(r"^(?:schedule|exhibit|annex|annexure|appendix|addendum|attachment)s?\s+[\w.\-]{1,12}$", re.IGNORECASE),
    # [the Company]  [a Party's]: a lower-case article first means a quotation was altered to
    # fit the sentence.
    re.compile(r"^(?:the|a|an|its|their|our|his|her|this|that)\b"),
)


def _is_blank(inside: str) -> bool:
    inside = inside.strip()
    if _MARKER.fullmatch(inside.replace(" ", "")):
        return True
    return not any(rule.match(inside) for rule in _EDITORIAL)


def find_blanks(text: str | None) -> list[str]:
    """Each different blank in the text, in the order it first appears (at most MAX_LISTED)."""
    if not text:
        return []
    seen: dict[str, None] = {}
    for match in _BRACKET.finditer(text):
        if _is_blank(match.group(1)):
            seen.setdefault(match.group(0), None)
            if len(seen) >= MAX_LISTED:
                break
    return list(seen)


def first_blank(text: str | None) -> str | None:
    found = find_blanks(text)
    return found[0] if found else None
