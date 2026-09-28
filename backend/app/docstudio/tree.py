"""Which clause sits inside which — decided by the strongest evidence first.

Each rule may only decide what the rules before it left open, and marks what it
cannot decide rather than guessing:

1. **Word's own numbering level**, where the file has one. The author set it.
2. **Decimals.** `5.5` sits inside the clause numbered `5` — the nearest one
   before it, in the same part of the document. The number states it.
3. **List items are siblings.** `(c)` following `(b)` shares `(b)`'s parent,
   whatever sits between them. A paragraph with no number between two items
   can no longer capture the second one — the defect that put `4.(c)`-`(i)`
   under "Any required repayment…" and 5.2-5.5 under a page header.
4. **A new list belongs to what introduces it** — the numbered clause just
   above it, a heading, or a sentence ending "…the following:" / "…then:".
5. **Indentation** (Word files), for a paragraph with no number.
6. Everything else is **undecided**, and goes to the AI with a closed list of
   the clauses it could belong to.

The AI's answers come back through the same function as `answers`, applied
only where the rules left a clause undecided — so it can never override what
the numbering settled. Measured on a Franklin Madison MSA, letting it place
every clause had put 5.5 inside 5.4.

"Parts" keep numbering honest: an attached work order that continues from
16.17 is not a continuation of the main agreement's clause 16. A part begins at
an exhibit or schedule title — only once the agreement's own numbering has
begun, because "EXHIBIT 10.2" at the top of an SEC filing names the whole
document.

Numbering that starts again at 1 with no title does not begin a part: it is
asked about. "5.2 … as follows: 1. … 2. … 3." is a list inside 5.2; a second
agreement bound into the same PDF starts its own top-level clauses. Treating
every restart as a new part had put an MSA's formula terms at the top level and
cut 5.3 off from 5, and 16.11 onwards off from 16.
"""

import re
from dataclasses import dataclass, replace

from .parsing.labels import Number, parse_number

_PART_TITLE = re.compile(
    r"^(?:exhibit|schedule|annex(?:ure)?|appendix|attachment|statement of work)\b",
    re.IGNORECASE,
)
# How a sentence says that what follows belongs to it: a closing colon, or
# "the following" / "as follows" even where OCR lost the colon. Not a bare
# "below" or "then": "…the parties referenced in Item 2 below." is a finished
# sentence, and reading it as an introduction put a schedule's Items 2-9
# inside Item 1's text.
_INTRODUCER = re.compile(r"(?::\W*|\b(?:the following|as follows)\W*)$", re.IGNORECASE)
# How many unplaced neighbours one question may offer. The clauses still open
# above it are always offered in full: capping the whole list at eight had cut
# off the right answer for six lines of a design certificate, whose answers
# were then refused.
MAX_NEARBY = 8

# Who decided a parent. "undecided" is the only one the AI is asked about.
CERTAIN = frozenset({"document", "numbering", "list", "part", "top", "layout"})


@dataclass(frozen=True)
class Node:
    label: str | None
    text: str
    kind: str = "paragraph"
    declared_level: int | None = None
    declared_list: str | None = None
    # Indentation-derived nesting, for a paragraph with no number (Word).
    layout_level: int | None = None


@dataclass(frozen=True)
class Placement:
    parent: int | None
    source: str
    number: Number | None = None
    part: int = 0  # index where this node's part begins
    root: int | None = None  # the part's title, which everything in the part sits under
    # A list item decided exactly as the item before it in its list.
    follows: int | None = None


def resolve_numbers(nodes: list[Node]) -> list[Number | None]:
    """Each label read as a `Number`, with (i)/(v)/(x) settled by neighbours.

    `(i)` after `(h)` is the ninth letter; followed by `(ii)`, or continuing a
    numeral run, it is a numeral. Measured on the employment agreement, which
    has both: "4.(i) Withholding" is a letter, "4.(b)(i)" a numeral.
    """
    numbers = [parse_number(node.label) for node in nodes]
    last: dict[str, int] = {}
    for index, number in enumerate(numbers):
        if number is None:
            continue
        repaired = _misread(number, last, numbers[index + 1 :])
        if repaired is not None:
            numbers[index] = number = repaired
        if number.alternative is not None:
            numeral, letter = number, number.alternative
            following = next((n for n in numbers[index + 1 :] if n is not None), None)
            if last.get(numeral.scheme) == numeral.path[0] - 1 or (
                numeral.path[0] == 1
                and following is not None
                and following.scheme == numeral.scheme
                and following.path[0] == 2
            ):
                chosen = numeral
            elif last.get(letter.scheme) == letter.path[0] - 1:
                chosen = letter
            else:
                chosen = numeral if numeral.path[0] == 1 else letter
            numbers[index] = Number(chosen.scheme, chosen.path)
        resolved = numbers[index]
        last[resolved.scheme] = resolved.path[0]
    return numbers


# What OCR reads a letter or numeral as. "(l) Counterparts" came back "(1)":
# as read it starts a new list, so (m) and (n) after it were placed inside it.
_MISREADINGS = {"(1)": (Number("(a)", (12,)), Number("(i)", (1,)))}


def _misread(number: Number, last: dict[str, int], following: list[Number | None]) -> Number | None:
    """A likelier reading of a number that fits nowhere as read.

    Only when the other reading fits exactly: it continues an open list *and*
    the next number continues that list after it. "(1)" between (k) and (m) is
    (l); a genuine "(1)" opening a new list is left alone.
    """
    if number.scheme == "decimal" or last.get(number.scheme) == number.path[0] - 1:
        return None
    upcoming = next((n for n in following if n is not None), None)
    for reading in _MISREADINGS.get(f"({number.path[0]})" if number.scheme == "(1)" else "", ()):
        continues = last.get(reading.scheme) == reading.path[0] - 1
        confirmed = (
            upcoming is not None
            and reading.scheme in (upcoming.scheme, getattr(upcoming.alternative, "scheme", None))
            and (upcoming.path[0] if upcoming.scheme == reading.scheme else upcoming.alternative.path[0])
            == reading.path[0] + 1
        )
        if continues and confirmed:
            return reading
    return None


def build_tree(
    nodes: list[Node],
    *,
    answers: dict[int, int | None] | None = None,
    pinned: dict[int, tuple[int | None, str]] | None = None,
) -> list[Placement]:
    """Place every node. `answers` fills undecided nodes; `pinned` keeps what
    evidence no longer at hand decided, such as Word's levels once stored."""
    answers = answers or {}
    pinned = pinned or {}
    numbers = resolve_numbers(nodes)
    placements: list[Placement] = []
    part_start, part_root = 0, None
    numbered_seen = False
    # The latest item of each run of top-level numbers still open, outermost
    # first: the agreement's 1-16, and a 1-4 list inside 5.2 while it lasts.
    runs: list[int] = []
    last_in_list: dict[str, int] = {}

    def decide(index: int, parent: int | None, source: str) -> None:
        if source == "undecided" and index in answers:
            parent, source = answers[index], "ai"
        placements.append(Placement(parent, source, numbers[index], part_start, part_root))
        if source == "undecided" and options(nodes, placements, index) == [part_root]:
            # Nothing before it is open to hold it — the very first block, or
            # the first line under an exhibit's title — so there is no question.
            settled = "part" if part_root is not None else "top"
            placements[-1] = replace(placements[-1], parent=part_root, source=settled)

    for index, node in enumerate(nodes):
        number = numbers[index]

        # --- parts -------------------------------------------------------
        if (
            number is None
            and numbered_seen
            and len(node.text) < 120
            and _PART_TITLE.match(node.text.strip())
        ):
            part_start, part_root = index, index
            runs.clear()
            last_in_list.clear()
            placements.append(Placement(None, "top", None, index, None))
            continue
        top_decimal = number is not None and number.scheme == "decimal" and len(number.path) == 1
        if number is not None:
            numbered_seen = True

        if index in pinned:
            parent, source = pinned[index]
            placements.append(Placement(parent, source, number, part_start, part_root))
            _remember(last_in_list, number, index)
            continue

        # --- 1. Word's own level -----------------------------------------
        # Level 1 of a list says nothing about what the list sits under, so
        # it falls through to the rules below.
        if node.declared_level and node.declared_level > 1:
            owner = _declared_owner(nodes, part_start, index, node)
            if owner is not None:
                decide(index, owner, "document")
                _remember(last_in_list, number, index)
                continue

        # --- 2. decimals --------------------------------------------------
        if top_decimal:
            continuing = [run for run in runs if numbers[run].path[0] == number.path[0] - 1]
            if len(continuing) == 1:
                # 2. goes wherever 1. went, like (b) after (a) — and a run
                # opened inside the one it continues has ended.
                leader = continuing[0]
                previous = placements[leader]
                placements.append(
                    Placement(previous.parent, previous.source, number, part_start, part_root, leader)
                )
                runs[runs.index(leader) :] = [index]
                continue
            introduced = (
                index - 1 >= part_start and index - 1 != part_root and _introduces(nodes[index - 1])
            )
            if continuing or (runs and number.path[0] == 1) or introduced:
                # Only the meaning tells these apart, so they are asked: "5.2 …
                # 1. … 2." is a list inside 5.2, a second agreement's "1." new
                # top-level numbering; "WHEREAS: 1. …" is a list inside the
                # sentence, "…agree as follows: 1. DEFINITIONS" the main clauses;
                # and a "4." that could continue two runs continues one of them.
                decide(index, part_root, "undecided")
            else:
                decide(index, part_root, "part" if part_root is not None else "top")
            # The runs this one sits inside stay open; any other has ended.
            parent = placements[-1].parent
            runs[:] = [run for run in runs if _inside(placements, parent, run)] + [index]
            continue
        if number is not None and number.scheme == "decimal":
            owner = _decimal_owner(numbers, part_start, index, number.path[:-1])
            if owner is not None:
                decide(index, owner, "numbering")
            else:
                # An orphan — 8.1.2 with no 8.1. The nearest shorter decimal is
                # a good guess and only a guess.
                prefix = _decimal_prefix(numbers, part_start, index, number.path)
                decide(index, part_root if prefix is None else prefix, "undecided")
            continue

        # --- 3/4. lists ---------------------------------------------------
        if number is not None:
            before = last_in_list.get(number.scheme)
            if before is not None and numbers[before].path[0] == number.path[0] - 1:
                previous = placements[before]
                placements.append(
                    Placement(
                        previous.parent, previous.source, number, part_start, part_root, before
                    )
                )
            else:
                owner, certain = _list_owner(nodes, numbers, index, part_start, part_root)
                decide(index, owner, "list" if certain else "undecided")
            last_in_list[number.scheme] = index
            continue

        # --- 5. indentation (Word) ----------------------------------------
        if node.kind != "heading" and node.layout_level and node.layout_level > 1:
            owner = _indented_owner(nodes, part_start, index, node.layout_level)
            if owner is not None:
                decide(index, owner, "layout")
                continue

        # --- 6. undecided -------------------------------------------------
        decide(index, part_root, "undecided")

    return placements


def _remember(last_in_list: dict[str, int], number: Number | None, index: int) -> None:
    if number is not None and number.scheme != "decimal":
        last_in_list[number.scheme] = index


def _declared_owner(nodes, part_start, index, node) -> int | None:
    """The nearest item one level up in the same Word list."""
    for j in range(index - 1, part_start - 1, -1):
        if nodes[j].declared_list != node.declared_list:
            continue
        declared = nodes[j].declared_level
        if declared == node.declared_level - 1:
            return j
        if declared is not None and declared < node.declared_level - 1:
            return None
    return None


def _decimal_owner(numbers, part_start, index, prefix) -> int | None:
    for j in range(index - 1, part_start - 1, -1):
        number = numbers[j]
        if number is not None and number.scheme == "decimal" and number.path == prefix:
            return j
    return None


def _decimal_prefix(numbers, part_start, index, path) -> int | None:
    for j in range(index - 1, part_start - 1, -1):
        number = numbers[j]
        if (
            number is not None
            and number.scheme == "decimal"
            and len(number.path) < len(path)
            and path[: len(number.path)] == number.path
        ):
            return j
    return None


def _list_owner(nodes, numbers, index, part_start, part_root):
    """Who owns a list starting here, and whether that is certain."""
    previous = index - 1
    if previous < part_start:
        return part_root, part_root is not None
    before = numbers[previous]
    if previous == part_root:
        return previous, True
    if before is not None and before.scheme != numbers[index].scheme:
        return previous, True  # a list right under the numbered clause above it
    if _introduces(nodes[previous]) or nodes[previous].kind == "heading":
        return previous, True  # "…the following:", "RECITALS:", a heading
    # Otherwise the likeliest owner is the nearest numbered clause of another
    # kind — a guess, so the AI is asked.
    for j in range(previous, part_start - 1, -1):
        if numbers[j] is not None and numbers[j].scheme != numbers[index].scheme:
            return j, False
    return part_root, False


def _inside(placements: list[Placement], node: int | None, ancestor: int) -> bool:
    """Is `node` the clause `ancestor`, or somewhere inside it?"""
    while node is not None:
        if node == ancestor:
            return True
        node = placements[node].parent
    return False


def _introduces(node: Node) -> bool:
    """Does this sentence announce what follows — "…the following:", "WHEREAS:"."""
    return bool(_INTRODUCER.search(node.text.rstrip()))


def _holds(node: Node) -> bool:
    """Can other clauses sit inside this one?

    A numbered clause or a heading can. A plain paragraph holds only what it
    introduces: offered as a parent for whatever came next, the AI stacked a
    design certificate's thirteen lines each inside the one before, and put
    "NOW, THEREFORE" inside the last recital.
    """
    return node.label is not None or node.kind == "heading" or _introduces(node)


def _indented_owner(nodes, part_start, index, level) -> int | None:
    for j in range(index - 1, part_start - 1, -1):
        if (nodes[j].layout_level or 1) < level:
            return j
    return None


def depths(placements: list[Placement]) -> list[int]:
    """Level = depth in the tree, 1 for the top — never the label's shape."""
    out: list[int] = []
    for placement in placements:
        out.append(1 if placement.parent is None else out[placement.parent] + 1)
    return out


def options(nodes: list[Node], placements: list[Placement], index: int) -> list[int | None]:
    """What an undecided node could belong to, nearest first.

    The nearby unplaced lines — up to `MAX_NEARBY` of them — then every clause
    still open at that point: from the nearest placed clause up its chain of
    parents, so "Any required repayment…" after `4.(b)(ii)` is offered (ii),
    (b) and 4., and not only the nearest. Offering only the nearest was how
    the first re-parenting design put it under (ii). Only clauses that can
    hold others are offered (`_holds`). The last option is the top of the
    node's part: the top level, or the exhibit's title — never outside the
    exhibit it is printed in.
    """
    placement = placements[index]
    out: list[int | None] = []
    j = index - 1
    while j >= placement.part and placements[j].source == "undecided":
        if len(out) < MAX_NEARBY and _holds(nodes[j]):
            out.append(j)
        j -= 1
    chain = j if j >= placement.part else None
    while chain is not None:
        if _holds(nodes[chain]):
            out.append(chain)
        chain = placements[chain].parent
    seen: list[int | None] = []
    for candidate in out:
        if candidate not in seen and candidate != placement.root:
            seen.append(candidate)
    return seen + [placement.root]


def violations(nodes: list[Node], placements: list[Placement]) -> list[str]:
    """Rules the finished tree must satisfy. Built by construction, so any
    violation here is a bug in this module, never a data quirk."""
    numbers = [placement.number for placement in placements]
    found = []
    for index, placement in enumerate(placements):
        parent = placement.parent
        if parent is not None and not 0 <= parent < index:
            found.append(f"#{index}: its parent #{parent} does not come before it")
        number = numbers[index]
        if number is not None and number.scheme == "decimal" and len(number.path) > 1:
            owner = _decimal_owner(numbers, placement.part, index, number.path[:-1])
            if owner is not None and parent != owner:
                found.append(f"#{index} {nodes[index].label}: numbering says #{owner}, placed under #{parent}")
        if placement.follows is not None and parent != placements[placement.follows].parent:
            found.append(f"#{index} {nodes[index].label}: split from the list item before it")
    return found
