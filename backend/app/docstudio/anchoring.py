"""Finding an annotation again after the document has moved under it.

A character offset is not an anchor. "Position 4,312" means a different thing
the moment anything is inserted above it, so a system that stores only offsets
silently breaks every citation and comment on every new version. That is the
single decision this subsystem exists to reverse.

The fix is the W3C Web Annotation Data Model's: store *several* selectors and
fall back between them. Hypothesis, which annotates a web that changes
constantly underneath it, resolves in four steps; this is the same ladder with
an explicit fifth outcome.

    1. the clause anchor still holds
    2. the stored offsets still land on the stored quote
    3. the quote is elsewhere, and its surroundings agree
    4. the quote is approximately there — the text was edited
    5. nothing matched                                       -> ORPHANED

Rungs 3 and 4 refuse a match they cannot tell apart from a coincidence. A
quote whose clause lost its identity is looked for first *where that clause
should be* — between the nearest clauses around it that kept theirs — and
trusted there. Anywhere else it may move only where the text on *both* sides of
it still matches. Contracts repeat themselves — boilerplate ("as per the terms of
this Agreement"), definitions stated twice, headings one digit apart ("10.1
Termination…", "10.2 Termination…") — and on four real contracts with simulated
edits, a comment whose clause was deleted landed on such a twin 11 times in 60.
A citation pointing a lawyer at the wrong clause is worse than one marked lost.

Rung 5 is a state, not an error. An orphan is surfaced for a human to re-link
and never deleted: annotations disappearing quietly is the failure this replaces.

Every resolution records which rung answered it. That distribution is the only
honest measure of how good clause identity actually is, so it is stored rather
than logged and thrown away.
"""

import difflib
from dataclasses import dataclass

# Measured, not guessed: 2,400 comments on random words of four real contracts,
# each followed through a simulated second version (paragraphs inserted, clauses
# reworded, deleted and moved) with the right answer known. Hypothesis's
# settings — 32 characters of context, a strict fuzzy floor — put 12 comments
# on the wrong words; these put 8, while finding more of the rest:
#
#                     right place   wrong place   lost but still there
#   32 / 82 / 6          2,221          12              40
#   64 / 75 / 12         2,230           8              32
#
# What remains wrong is mostly text a contract states twice with the same
# words around it, which no amount of context separates.

# How much text either side of the quote to keep. Contracts repeat whole
# sentences; more context is what tells the copies apart.
CONTEXT_CHARS = 64

# Below this similarity a "match" is a different clause that happens to share
# vocabulary. Lower than it could be, because the sides are checked too.
FUZZY_FLOOR = 75.0

# Away from its own clause, a quote found verbatim must agree with its stored
# surroundings for at least this many characters on each side...
MIN_SIDE_AGREEMENT = 12
# ...and one found approximately needs each side at least this alike (0-100).
SIDE_FLOOR = 60.0
# Shorter quotes are never fuzzy-searched outside their own clause: "the
# Services" is approximately everywhere.
DISTINCT_CHARS = 40

OK = "ok"
MOVED = "moved"
ORPHANED = "orphaned"


@dataclass(frozen=True)
class Anchor:
    """Three ways to find the same span, captured together at creation time."""

    quote_exact: str
    quote_prefix: str = ""
    quote_suffix: str = ""
    clause_id: str | None = None
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True)
class Resolution:
    state: str
    rung: int
    clause_id: str | None = None
    start: int | None = None
    end: int | None = None
    note: str | None = None


@dataclass(frozen=True)
class ClauseRef:
    """The minimum a clause needs to expose to be anchorable. Keeping this a
    plain value rather than the ORM row means `resolve` is testable without a
    database and reusable for a version that has not been saved yet."""

    clause_id: str
    text: str
    char_start: int
    char_end: int


def capture(flat_text: str, start: int, end: int, clauses: list[ClauseRef]) -> Anchor:
    """Build all three selectors for the span `flat_text[start:end]`.

    Called once, when the annotation is created. Capturing the context here is
    what makes rung 3 possible later — it cannot be reconstructed after the
    document has already changed.
    """
    exact = flat_text[start:end]
    return Anchor(
        quote_exact=exact,
        quote_prefix=flat_text[max(0, start - CONTEXT_CHARS) : start],
        quote_suffix=flat_text[end : end + CONTEXT_CHARS],
        clause_id=_clause_containing(clauses, start, end),
        start=start,
        end=end,
    )


def expected_window(
    previous_ids: list[str], clause_id: str | None, clauses: list[ClauseRef]
) -> tuple[int, int] | None:
    """Where a clause should be now, if it is not here by its own identity.

    Between the nearest clauses before and after it — in the version it came
    from — that kept their identity. A sentence rewritten so much that its
    clause is no longer recognisably the same still sits after the same heading.
    None when the clause is here, was never in `previous_ids`, or its
    neighbours were reordered around it.
    """
    here = {clause.clause_id: clause for clause in clauses}
    if clause_id is None or clause_id in here or clause_id not in previous_ids:
        return None
    k = previous_ids.index(clause_id)
    lo = next((here[i].char_end for i in reversed(previous_ids[:k]) if i in here), 0)
    hi = next(
        (here[i].char_start for i in previous_ids[k + 1 :] if i in here),
        max((clause.char_end for clause in clauses), default=0),
    )
    return (lo, hi) if lo < hi else None


def resolve(
    anchor: Anchor,
    flat_text: str,
    clauses: list[ClauseRef],
    *,
    near: tuple[int, int] | None = None,
) -> Resolution:
    """Walk the ladder. Stops at the first rung that answers.

    `near` is where the anchor's clause should be when it lost its identity
    (`expected_window`): a match there is trusted as a match inside its own
    clause would be.
    """
    exact = anchor.quote_exact or ""
    if not exact:
        return Resolution(ORPHANED, 5, note="annotation stored no quote to search for")

    clause = {c.clause_id: c for c in clauses}.get(anchor.clause_id or "")
    hits = occurrences(flat_text, exact)
    home = (clause.char_start, clause.char_end) if clause is not None else near

    def found(state: str, rung: int, start: int, end: int, note: str | None = None) -> Resolution:
        return Resolution(state, rung, _clause_containing(clauses, start, end), start, end, note)

    # 1 — the clause is still here and still contains the quote. If the quote
    # is in it twice, the surroundings pick which.
    if clause is not None:
        inside = [h for h in hits if clause.char_start <= h and h + len(exact) <= clause.char_end]
        if inside:
            best, _ = _best_by_context(flat_text, inside, len(exact), anchor)
            return Resolution(OK, 1, clause.clause_id, best, best + len(exact))

    # 2 — the offsets still land on the quote. Cheap, and true whenever the
    # edit happened after this point in the document.
    if (
        anchor.start is not None
        and anchor.end is not None
        and flat_text[anchor.start : anchor.end] == exact
    ):
        return found(OK, 2, anchor.start, anchor.end)

    # 3 — the quote is still present verbatim: where its clause should be, or
    # anywhere the text on both sides of it still matches — the same place, moved.
    if hits:
        expected = [h for h in hits if home and home[0] <= h and h + len(exact) <= home[1]]
        best, _ = _best_by_context(flat_text, expected or hits, len(exact), anchor)
        before, after = _side_agreement(flat_text, best, best + len(exact), anchor)
        if expected or (
            before >= min(MIN_SIDE_AGREEMENT, len(anchor.quote_prefix))
            and after >= min(MIN_SIDE_AGREEMENT, len(anchor.quote_suffix))
        ):
            return found(OK, 3, best, best + len(exact))

    # 4 — approximately there: the words were edited. Where its clause is or
    # should be first, where a near match is almost certainly the same words
    # reworded; across the document only for a quote distinctive enough to be
    # findable, and with both sides alike.
    moved = "matched approximately; the wording changed"
    if home is not None:
        span = _fuzzy_span(flat_text[home[0] : home[1]], exact)
        if span is not None:
            return found(MOVED, 4, home[0] + span[0], home[0] + span[1], moved)
    if len(exact) >= DISTINCT_CHARS:
        span = _fuzzy_span(flat_text, exact)
        if span is not None and _sides_alike(flat_text, *span, anchor):
            return found(MOVED, 4, *span, moved)

    # 5 — gone. Say so.
    return Resolution(ORPHANED, 5, note="the quoted text is no longer in this version")


def _clause_containing(clauses: list[ClauseRef], start: int, end: int) -> str | None:
    for clause in clauses:
        if clause.char_start <= start and end <= clause.char_end:
            return clause.clause_id
    # Spanning two clauses is legitimate (a quote across a paragraph break), so
    # fall back to whichever clause the span starts in rather than returning
    # nothing and forcing the caller to guess.
    for clause in clauses:
        if clause.char_start <= start < clause.char_end:
            return clause.clause_id
    return None


def occurrences(haystack: str, needle: str) -> list[int]:
    out: list[int] = []
    index = haystack.find(needle) if needle else -1
    while index != -1:
        out.append(index)
        index = haystack.find(needle, index + 1)
    return out


def _best_by_context(haystack: str, hits: list[int], length: int, anchor: Anchor) -> tuple[int, int]:
    """The occurrence whose neighbours look most like the ones we stored, and
    how many characters of them agree.

    This is the entire reason prefix and suffix exist. "Confidential
    Information" appears a dozen times in an NDA; without context, rung 3 would
    attach the comment to whichever one came first.
    """

    def score(position: int) -> int:
        before = haystack[max(0, position - CONTEXT_CHARS) : position]
        after = haystack[position + length : position + length + CONTEXT_CHARS]
        return _common_suffix(before, anchor.quote_prefix) + _common_prefix(
            after, anchor.quote_suffix
        )

    best = max(hits, key=score)
    return best, score(best)


def _side_agreement(haystack: str, start: int, end: int, anchor: Anchor) -> tuple[int, int]:
    """Characters of the stored surroundings that still sit either side, exactly."""
    before = haystack[max(0, start - CONTEXT_CHARS) : start]
    after = haystack[end : end + CONTEXT_CHARS]
    return _common_suffix(before, anchor.quote_prefix), _common_prefix(after, anchor.quote_suffix)


def _sides_alike(haystack: str, start: int, end: int, anchor: Anchor) -> bool:
    """Is the text either side of an approximate match like the stored text?"""
    from rapidfuzz import fuzz

    before = haystack[max(0, start - CONTEXT_CHARS) : start]
    after = haystack[end : end + CONTEXT_CHARS]
    return all(
        not stored.strip() or fuzz.ratio(seen, stored) >= SIDE_FLOOR
        for seen, stored in ((before, anchor.quote_prefix), (after, anchor.quote_suffix))
    )


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _common_suffix(a: str, b: str) -> int:
    return _common_prefix(a[::-1], b[::-1])


def _fuzzy_span(haystack: str, needle: str) -> tuple[int, int] | None:
    """Where the quote approximately sits, or None.

    None rather than a best guess: a wrong span is a citation pointing a lawyer
    at the wrong clause, which is worse than admitting the anchor is lost.
    """
    if not haystack or not needle:
        return None
    try:
        from rapidfuzz import fuzz
    except ImportError:  # pragma: no cover - rapidfuzz is a declared dependency
        return None
    alignment = fuzz.partial_ratio_alignment(needle.lower(), haystack.lower())
    if alignment is None or alignment.score < FUZZY_FLOOR:
        return None
    # partial_ratio compares windows the quote's own length, so words inserted
    # into the quote push its end outside the window: "indirect damages" became
    # "indirect or cons". Re-align over a wider stretch and span from the first
    # matching run to the last. Runs under three characters are stray letters.
    lo = max(0, alignment.dest_start - len(needle))
    hi = min(len(haystack), alignment.dest_end + len(needle))
    matcher = difflib.SequenceMatcher(None, needle.lower(), haystack[lo:hi].lower(), autojunk=False)
    runs = [block for block in matcher.get_matching_blocks() if block.size >= 3]
    # The whole re-aligned phrase first; the window only if that fails. And
    # the words finally chosen must themselves read like the quote: a window
    # can score well on shared vocabulary alone — in a redrafted section, "The
    # consideration for the said supply is twofold:" matched "the financial
    # institution is to be treated as consideration", 56% alike as a whole.
    spans = [(lo + runs[0].b, lo + runs[-1].b + runs[-1].size)] if runs else []
    spans.append((alignment.dest_start, alignment.dest_end))
    chosen = next(
        (s for s in spans if fuzz.ratio(needle.lower(), haystack[s[0] : s[1]].lower()) >= FUZZY_FLOOR),
        None,
    )
    if chosen is None:
        return None
    start, end = chosen
    while start < end and haystack[start].isspace():
        start += 1
    while end > start and haystack[end - 1].isspace():
        end -= 1
    return start, end
