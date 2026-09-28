"""What a clause number looks like, in one place.

Every parser needs this and each had grown its own copy, which is how the Word
parser ended up without one at all: it asked Word for automatic numbering and
stopped there. On a document where the author *typed* "(i)" and "A." instead of
using Word's list feature — which is most real client paper — that returns
nothing, and a 75-clause observation notice arrives with no clause numbered and
everything flat at level 1.

Word's automatic numbering still takes precedence where it exists, because it is
authoritative: the number Word computes is the number on the page. This is the
fallback for the far more common document where someone just typed it.
"""

import re
from dataclasses import dataclass

# Optional "- " is a markdown bullet, which is how OCR providers return list
# items. A single capital letter is a recital ("A. The Company and Executive
# are parties to..."); it must be followed by whitespace so "U.S." cannot match.
_SHAPES = (
    r"\d+(?:\.\d+)*[.)]?"                      # 1.   2.1   3.4.5)
    # Two letters, because a list running past (z) continues at (aa).
    r"|\((?:[a-z]{1,2}|[ivxlcdm]+|\d+)\)"      # (a)  (aa)  (iv)  (3)
    # "Section 4.2" and "Article IV" both. The trailing (?:\.\d+)* is what
    # was missing: without it "Section 4" matched and "Section 4.2" did not,
    # so a contract citing its own sub-clauses lost every one of them.
    # "Item 5" numbers a schedule the way "Section 5" numbers an agreement.
    r"|(?:Article|ARTICLE|Section|SECTION|Item|ITEM)\s+[\dIVXLCDM]+(?:\.\d+)*[.)]?"
    r"|[IVX]{2,5}[.)]"                         # II.  IV)  — roman sections
    # And in lower case: "ii. Mindtree's compliance…" on the Franklin Madison
    # MSA arrived unnumbered and was glued onto the item before it.
    r"|[ivx]{2,5}[.)]"                         # ii.  iv)
    r"|[A-Z][.)]"                              # A.   B)
    # Lower case too: "c. Effect of Termination" was arriving unnumbered, so
    # the clause it heads could be given a parent by nothing but a guess.
    r"|[a-z][.)]"                              # a.   b)
)

LEADING_NUMBER = re.compile(
    r"^\s*(?:[-*]\s+)?(" + _SHAPES + r")"
    # Followed by a space — or, where OCR dropped it, by a capital straight
    # after the number's closing dot: "12.3.LICENSOR shall…" lost clause 12.3
    # on a patent licence. A digit before the dot, so "U.S. Government" is
    # still not clause "U.".
    r"(?:\s+|(?<=\d\.)(?=[A-Z]))"
)

# A cell holding a clause number and nothing else. Contracts are often laid out
# as a two-column table, the number in one column and the clause in the next;
# this is what tells that apart from a fee schedule whose first column happens
# to contain text.
LABEL_ONLY = re.compile(r"^\s*(" + _SHAPES + r")\s*$")


_WORD_PREFIX = re.compile(r"^(?:Article|ARTICLE|Section|SECTION|Item|ITEM)\s+")


# A bare number this large opening a paragraph is a year, not a clause.
# "2026. The year was difficult" was being read as clause 2026. Contracts do
# not number their top-level clauses in the thousands, and anything genuinely
# deep carries a dot ("1002.1"), which is allowed through.
_MAX_BARE_NUMBER = 1000


def split_label(text: str) -> tuple[str | None, str]:
    """Separate a leading clause number from the text that follows it."""
    match = LEADING_NUMBER.match(text)
    if not match:
        return None, text
    label = match.group(1).strip()
    if _is_implausible(label):
        return None, text
    return label, text[match.end():].strip()


def _is_implausible(label: str) -> bool:
    bare = label.rstrip(".)")
    return bare.isdigit() and int(bare) >= _MAX_BARE_NUMBER


def label_only(text: str) -> str | None:
    """The clause number, if this text is a clause number and nothing else."""
    match = LABEL_ONLY.match(text or "")
    return match.group(1).strip() if match else None


def level_for(label: str | None) -> int:
    """Depth from the number's shape: "2." is level 1, "2.1" level 2.

    The trailing dot is punctuation, not a separator — counting it made every
    top-level clause look like a sub-clause and flattened the whole hierarchy
    by one.
    """
    if not label:
        return 1
    if label.startswith("("):
        return 3
    # "Section 4.2" is a sub-section. Reading the depth from the first
    # character alone made every one of them top-level, so a contract using
    # "Section" instead of a bare number arrived completely flat.
    trimmed = _WORD_PREFIX.sub("", label).rstrip(".)")
    return trimmed.count(".") + 1 if trimmed[:1].isdigit() else 1


# --- numbers read structurally ----------------------------------------------


@dataclass(frozen=True)
class Number:
    """A clause number as a kind and a position.

    `5.5` is decimal (5, 5); `(c)` is a bracketed letter at (3,). A decimal
    names its own parent — the clause numbered one level up — which is a fact
    about the document rather than a judgment about it. The other kinds only
    say where an item sits in its own list; which clause owns the list is
    settled by the tree, from its neighbours.

    `(i)`, `(v)` and `(x)` are letters and roman numerals at once. Both readings
    are kept, and the neighbours decide: after `(h)` it is the ninth letter;
    followed by `(ii)` it is the first numeral.
    """

    scheme: str  # decimal | (a) | (A) | (i) | (I) | (1) | a. | A. | i. | I.
    path: tuple[int, ...]
    alternative: "Number | None" = None


_ROMAN_DIGITS = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_WORD_LABEL = re.compile(r"^(?:article|section|clause|item)\s+", re.IGNORECASE)
_DECIMAL = re.compile(r"^(\d+(?:\.\d+)*)[.)]?$")


def _roman(text: str) -> int | None:
    """The value of a well-formed roman numeral, or None."""
    text = text.lower()
    if not text or any(ch not in _ROMAN_DIGITS for ch in text):
        return None
    total = 0
    for index, ch in enumerate(text):
        value = _ROMAN_DIGITS[ch]
        following = _ROMAN_DIGITS[text[index + 1]] if index + 1 < len(text) else 0
        total += -value if value < following else value
    return total if _to_roman(total) == text else None


def _to_roman(value: int) -> str:
    out = []
    for amount, numeral in (
        (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
        (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
    ):
        while value >= amount:
            out.append(numeral)
            value -= amount
    return "".join(out)


def _letter_position(text: str) -> int | None:
    """(a) is 1, (z) 26, and a list running past (z) continues (aa), (bb)."""
    text = text.lower()
    if len(text) == 1 and text.isalpha():
        return ord(text) - 96
    if len(text) == 2 and text[0] == text[1] and text.isalpha():
        return 26 + ord(text[0]) - 96
    return None


def _letter_or_numeral(value: str, letters: str, numerals: str) -> Number | None:
    lower = value.lower()
    numeral = _roman(lower)
    letter = _letter_position(lower)
    if len(lower) == 1 and lower in "ivx" and numeral is not None:
        # Both readings: the tree settles which, from the neighbours.
        return Number(numerals, (numeral,), Number(letters, (letter,)))
    if numeral is not None and len(lower) > 1:
        return Number(numerals, (numeral,))
    if letter is not None:
        return Number(letters, (letter,))
    return None


def parse_number(label: str | None) -> Number | None:
    """Read a label into a `Number`, or None when it is not one."""
    if not label:
        return None
    text = label.strip()
    worded = _WORD_LABEL.match(text)
    if worded:
        rest = text[worded.end():].rstrip(".)")
        if (match := _DECIMAL.match(rest)) is not None:
            return Number("decimal", tuple(int(p) for p in match.group(1).split(".")))
        numeral = _roman(rest)
        return Number("decimal", (numeral,)) if numeral is not None else None
    if (match := _DECIMAL.match(text)) is not None:
        return Number("decimal", tuple(int(p) for p in match.group(1).split(".")))
    if text.startswith("(") and text.endswith(")"):
        inner = text[1:-1]
        if inner.isdigit():
            return Number("(1)", (int(inner),))
        if inner.isupper():
            return _letter_or_numeral(inner, "(A)", "(I)")
        return _letter_or_numeral(inner, "(a)", "(i)")
    if len(text) >= 2 and text[-1] in ".)":
        inner = text[:-1]
        if inner.isupper():
            return _letter_or_numeral(inner, "A.", "I.")
        if inner.islower():
            return _letter_or_numeral(inner, "a.", "i.")
    return None
