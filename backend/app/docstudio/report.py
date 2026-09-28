"""One markdown page per version, for a person or a model to read.

Phase 1 records everything it learns across five tables and a warnings list, so
the only way to ask "did this document come out cleanly?" is to query rows.
Nothing assembled the answer anywhere, which is how OCR reading a rubber stamp
as clause text sat in the database looking exactly like a clause.

**The checks run in code, not in a model, and that is the point.** The two
defects worth finding most — a clause cut in half, a missing clause number — are
both *absences*, and absence is the one thing language models measurably cannot
see. AbsenceBench (arXiv 2506.11440) puts Claude 3.7 Sonnet at 69.6% F1 on
spotting removed content while it is near-superhuman at finding content that is
present; the paper's explanation is architectural, in that attention has no
token to attend to for something that is not there. A study of LLM judges on
clinical notes (arXiv 2608.31016) measured 0.79-0.94 on added or altered text
against 0.50-0.63 on omissions, and 0.50 is a coin flip.

What the same work found does help is stating the gap outright, so this page
reports conclusions rather than leaving them to be inferred. It is the split
Great Expectations uses — expectations are code, Data Docs are the render — and
the one Litera's Contract Companion has shipped for twenty years, checking
numbering, cross-references and defined terms as rules.

A model reading this page should spend its attention on what code cannot judge:
whether two clauses that are both present contradict each other.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import pairwise

from sqlalchemy import select
from sqlalchemy.orm import Session

from .annotations import for_document
from .hierarchy import ARRANGED, NOT_ARRANGED, broken_runs
from .models import DsAnnotation, DsClause, DsDocument, DsEvent, DsVersion
from .parsing.reflow import DANGLING_WORDS, LIST_CONJUNCTIONS, SENTENCE_ENDS

# A clause shorter than this that ends mid-sentence is a fragment, not a
# truncation — `_fragments` reports it, and reporting both would be noise.
MIN_TRUNCATED_CHARS = 40
MAX_FRAGMENT_CHARS = 25
# A one-off block this short, or one with no letters in it, is not words. Above
# it, a short unnumbered block is usually real content the parser typed as a
# paragraph instead of a table cell or a heading — "WITNESSETH", "SAP ABAP HR",
# "Representative". Reporting every one of those put 234 findings on the corpus
# and buried the 40 that meant something. Repetition is the other signal, and
# it is handled separately: the same fragment on six pages is furniture.
MAX_DEBRIS_CHARS = 3
# Beyond this many missing numbers in one run, the numbering was not read at
# all. Listing 40 absent clauses buries every other finding on the page.
MAX_LISTED_GAPS = 5
# A label repeating this far away is a schedule restarting its own numbering,
# which is normal. Two of the same number close together is a clause read twice.
NEARBY_SEQ = 30
# How close two blocks must sit to count as the same spot, as a fraction of the
# page. The stamp in the sample corpus drifts ~0.04 across pages as the scan
# skews, so anything tighter splits one stamp into five clusters.
STAMP_TOLERANCE = 0.05
MIN_STAMP_PAGES = 3
OUTLINE_CHARS = 60
# Enough to read a warning or an audit count without the 403 URL in the
# event log pushing the clauses off the page.
EVENT_VALUE_CHARS = 300


@dataclass(frozen=True)
class Finding:
    code: str
    detail: str
    page: int | None = None
    seq: int | None = None


# --- reading labels ---------------------------------------------------------

_DOTTED = re.compile(r"^(\d+(?:\.\d+)*)")
# Section and Clause only, and numeric only. "(i)" is genuinely ambiguous
# between roman one and letter i, and guessing produces confident nonsense.
_REF = re.compile(r"\b[Ss]ection\s+(\d+(?:\.\d+)*)|\b[Cc]lause\s+(\d+(?:\.\d+)*)")
# "Section 12 of the Companies Act" points outside this document, and flagging
# every statutory reference as broken would make the check worthless. The
# optional group is the subsection: real citations are written "section 45(1) of
# the Criminal Finances Act 2017" and "Section 2(31) of the CGST Act", and
# matching only a bare "of" let every one of those through as a broken
# reference. Same for "Clause 4.3(a) of the Supplementary Agreement".
_EXTERNAL = re.compile(r"^\s*(?:\([0-9a-zA-Z]{1,4}\)\s*)?(?:[A-Z]\s+)?of\s+(?:the\s+)?[A-Z0-9]")
# Below this share of clauses carrying a numeric label, cross-referencing is
# not answerable: "the reference is broken" cannot be told apart from "we never
# read the clause it points at". One corpus document whose numbering barely
# parsed produced 28 findings this way, all of them restating one root cause.
MIN_NUMBERED_SHARE_FOR_REFS = 0.10
_HTML = re.compile(r"</?(?:table|tr|td|th|b|u|i|br|p|empty|signature|figure|checkbox)\s*/?>", re.IGNORECASE)


def _dotted(label: str | None) -> tuple[int, ...] | None:
    """`"2.1(a)"` -> `(2, 1)`. None when the label is not numeric."""
    if not label:
        return None
    match = _DOTTED.match(label.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _canonical(parts: tuple[int, ...]) -> tuple[int, ...]:
    """`(6, 0)` -> `(6,)`, so "Section 6.0" finds the clause labelled "6.".

    A trailing zero is a numbering style, not a level. Comparing the tuples
    literally reported every reference in a 6.0-style document as broken.
    """
    while len(parts) > 1 and parts[-1] == 0:
        parts = parts[:-1]
    return parts


def _join(parts: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in parts)


def _where(clause: DsClause) -> str:
    label = clause.number_label.strip() if clause.number_label else f"#{clause.seq}"
    page = f" (p{clause.page_number})" if clause.page_number else ""
    return f"clause {label}{page}"


def _gist(clause: DsClause, limit: int = 32) -> str:
    text = " ".join(clause.text.split())
    return f'"{text[:limit]}"' + ("…" if len(text) > limit else "")


def _runs(numbers: list[int]) -> str:
    """`[1,2,3,7]` -> `"1-3, 7"`, so a long list of pages stays readable."""
    out: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:] + [None]:
        if number == previous + 1:
            previous = number
            continue
        out.append(str(start) if start == previous else f"{start}-{previous}")
        if number is None:
            break
        start = previous = number
    return ", ".join(out)


# --- the checks -------------------------------------------------------------


def _truncated(clauses: list[DsClause]) -> list[Finding]:
    """A clause that stops mid-sentence at a page break.

    `reflow.py` rejoins these when the halves are adjacent, so what survives to
    here is a join that could not be made — and a clause ending "...shall
    observe all safety rules in" cannot be quoted or reviewed.
    """
    out = []
    for clause in clauses:
        if clause.clause_type in {"heading", "table"}:
            continue
        text = clause.text.rstrip()
        if len(text) < MIN_TRUNCATED_CHARS or text[-1] in SENTENCE_ENDS:
            continue
        last = text.split()[-1].lower().strip(",;:\u2019'\"")
        # "...; or" and "..., and" end a list item deliberately — the sentence
        # continues into the next item by design, which is correct drafting.
        # Only a dangling word counts, the same list the joiner uses: matching
        # merely "no full stop" fired 203 times on the corpus and was right
        # about a quarter of the time, and 150 false alarms get a report ignored.
        if last in LIST_CONJUNCTIONS or last not in DANGLING_WORDS:
            continue
        out.append(
            Finding(
                "CUT OFF",
                f'{_where(clause)} ends "…{text[-45:]}" with no terminator',
                clause.page_number,
                clause.seq,
            )
        )
    return out


def _numbering_gaps(clauses: list[DsClause]) -> list[Finding]:
    """A number the document uses that never arrived.

    Pure arithmetic: sort what came out, subtract from the run it should be.
    Worth doing in code precisely because it needs no judgement.
    """
    runs: dict[tuple[int, ...], dict[int, DsClause]] = defaultdict(dict)
    for clause in clauses:
        parts = _dotted(clause.number_label)
        if parts:
            runs[parts[:-1]].setdefault(parts[-1], clause)

    out = []
    for prefix, found in sorted(runs.items()):
        if len(found) < 2:
            continue
        low, high = min(found), max(found)
        missing = [n for n in range(low, high + 1) if n not in found]
        if not missing:
            continue
        where = f"{_join(prefix)}.x" if prefix else "top level"
        if len(missing) > MAX_LISTED_GAPS:
            out.append(
                Finding(
                    "NUMBERING BROKEN",
                    f"{where}: {len(missing)} of {high - low + 1} numbers between {low} and "
                    f"{high} are missing — the numbering was probably not read at all",
                )
            )
            continue
        for number in missing:
            label = _join(prefix + (number,))
            before, after = found.get(number - 1), found.get(number + 1)
            context = ""
            if before is not None and after is not None:
                context = f", between {_gist(before)} and {_gist(after)}"
            out.append(
                Finding(
                    "NUMBERING GAP",
                    f"no clause {label}{context}",
                    before.page_number if before is not None else None,
                )
            )
    return out


def _duplicate_numbers(clauses: list[DsClause]) -> list[Finding]:
    """The same number twice, close together — one clause read as two."""
    by_label: dict[str, list[DsClause]] = defaultdict(list)
    for clause in clauses:
        # Multi-level numeric labels only. A repeated letter is how a contract
        # is written — every clause has its own (a), (b), (c) — and a repeated
        # top-level number is a recital, exhibit or sub-list restarting, which
        # is also normal. All seventeen findings this raised across the corpus
        # before the restriction were one of those two, including a date read
        # as a clause number ("16 JAN 2019" against "16 Services will be…").
        # A repeated *dotted* number is different: nothing legitimately has two
        # clause 4.2s, and every cross-reference to it is then ambiguous.
        parts = _dotted(clause.number_label) if clause.number_label else None
        if parts and len(parts) > 1:
            by_label[clause.number_label.strip()].append(clause)

    out = []
    for label, group in sorted(by_label.items()):
        if len(group) < 2:
            continue
        group.sort(key=lambda clause: clause.seq)
        close = [(a, b) for a, b in pairwise(group) if b.seq - a.seq <= NEARBY_SEQ]
        if not close:
            continue
        first, second = close[0]
        out.append(
            Finding(
                "DUPLICATE NUMBER",
                f'"{label}" appears {len(group)} times; two of them {second.seq - first.seq} '
                f"clauses apart ({_gist(first)} and {_gist(second)})",
                first.page_number,
                first.seq,
            )
        )
    return out


def _orphans(clauses: list[DsClause]) -> list[Finding]:
    """`2.1` arrived but `2` did not — usually a heading that was missed."""
    present = {parts for parts in (_dotted(c.number_label) for c in clauses) if parts}
    out, reported = [], set()
    for clause in clauses:
        parts = _dotted(clause.number_label)
        if not parts or len(parts) < 2:
            continue
        parent = parts[:-1]
        if parent in present or parent in reported:
            continue
        reported.add(parent)
        out.append(
            Finding(
                "ORPHAN",
                f"{_join(parts)} exists but {_join(parent)} was never found",
                clause.page_number,
                clause.seq,
            )
        )
    return out


def _dangling_refs(clauses: list[DsClause]) -> list[Finding]:
    """A cross-reference pointing at a clause that is not in the document.

    Either the reference is wrong — which a reviewer must see — or the target
    was lost in extraction, which is our bug. Both are worth a line.
    """
    numbered = [c for c in clauses if c.number_label and _dotted(c.number_label)]
    if len(numbered) < max(3, len(clauses) * MIN_NUMBERED_SHARE_FOR_REFS):
        return []
    present = {_canonical(_dotted(c.number_label)) for c in numbered}
    # A citation numbered far above anything this document contains is a
    # statute, not a clause: "Section 409A" in a contract with thirteen
    # sections is the Internal Revenue Code.
    highest = max(parts[0] for parts in present)
    citing: dict[tuple[int, ...], list[DsClause]] = defaultdict(list)
    quoted: dict[tuple[int, ...], str] = {}

    for clause in clauses:
        for match in _REF.finditer(clause.text):
            if _EXTERNAL.match(clause.text[match.end() : match.end() + 40]):
                continue
            target = _canonical(
                tuple(int(p) for p in (match.group(1) or match.group(2)).split("."))
            )
            if target in present or target[0] > highest:
                continue
            citing[target].append(clause)
            quoted.setdefault(target, match.group(0))

    out = []
    for target, sources in sorted(citing.items()):
        others = f" (and {len(sources) - 1} other clauses)" if len(sources) > 1 else ""
        out.append(
            Finding(
                "NO TARGET",
                f'{_where(sources[0])} cites "{quoted[target]}", which does not exist in '
                f"this document{others}",
                sources[0].page_number,
                sources[0].seq,
            )
        )
    return out


def _fragment_groups(clauses: list[DsClause], *, skip: set[int]) -> list[tuple[str, list[DsClause]]]:
    """The fragments worth reporting, grouped by their text.

    `skip` holds what the position check already reported: naming the same
    stamp twice on one page makes the page look worse than the document is.
    """
    grouped: dict[str, list[DsClause]] = defaultdict(list)
    for clause in clauses:
        if clause.seq in skip:
            continue
        if clause.number_label or clause.clause_type in {"heading", "table"}:
            continue
        text = clause.text.strip()
        if len(text) > MAX_FRAGMENT_CHARS:
            continue
        # A block that is nothing but provider markup ("<signature>") is
        # already reported as markup, and naming it twice pads the list.
        if not _HTML.sub("", text).strip():
            continue
        grouped[text].append(clause)
    return [
        (text, group)
        for text, group in sorted(grouped.items(), key=lambda item: -len(item[1]))
        # Repeated, or not words. A one-off short phrase is real content the
        # parser typed as a paragraph — see MAX_DEBRIS_CHARS.
        if len(group) > 1 or len(text) <= MAX_DEBRIS_CHARS or not any(ch.isalpha() for ch in text)
    ]


def _fragments(clauses: list[DsClause], *, skip: set[int]) -> list[Finding]:
    """Too short to be a clause. Almost always OCR debris."""
    out = []
    for text, group in _fragment_groups(clauses, skip=skip):
        first = group[0]
        if len(group) > 1:
            # A fragment repeating across the document is page furniture that
            # stripping missed — a DMS stamp ("MDC\\757175_1") or a footer
            # ("Page 4 of 13"). Reporting each copy separately turned one
            # furniture bug into eighteen findings and hid everything else.
            pages = sorted({c.page_number for c in group if c.page_number})
            where = f", pages {_runs(pages)}" if pages else ""
            detail = (
                f"{text!r} appears as its own clause {len(group)} times{where} — "
                "repeated page furniture that stripping did not remove"
            )
        else:
            detail = f"clause #{first.seq} is {len(text)} characters: {text!r}"
        out.append(Finding("FRAGMENT", detail, first.page_number, first.seq))
    return out


def _markup(clauses: list[DsClause]) -> list[Finding]:
    """Provider markup that reached the clause text.

    Reducto returns some blocks as HTML, and `<table>`, `<b>` and `<empty>` are
    arriving inside stored clauses. Quoting one back to a reviewer shows them
    tags; embedding one puts tags in the vector. Given its own finding rather
    than left to surface as a clause that ends oddly, which is where it was
    hiding — the tags are the problem, not the missing full stop.
    """
    hits = [c for c in clauses if _HTML.search(c.text)]
    if not hits:
        return []
    pages = sorted({c.page_number for c in hits if c.page_number})
    where = f" on pages {_runs(pages)}" if pages else ""
    return [
        Finding(
            "MARKUP IN TEXT",
            f"{len(hits)} clauses contain HTML tags from the OCR provider{where}: "
            f"{_gist(hits[0], 60)}",
            hits[0].page_number,
            hits[0].seq,
        )
    ]


def _centre(bbox: dict) -> tuple[float, float]:
    return (bbox["x0"] + bbox["x1"]) / 2, (bbox["y0"] + bbox["y1"]) / 2


def _stamp_clusters(clauses: list[DsClause]) -> list[list[DsClause]]:
    """Short blocks landing in the same spot on several pages.

    `furniture.py` already strips repeated page furniture, but it matches on
    *text*: a DocuSign stamp OCRs identically every time and is caught, while a
    rubber stamp OCRs differently every time and is not. Position repeats when
    the characters do not — and 8 of the 12 errors in the hand-scored accuracy
    run were a stamp read as clause text.

    Reported rather than removed. A clause deleted by a geometric guess is
    unrecoverable; a clause flagged is a reviewer's call.
    """
    candidates = [
        clause
        for clause in clauses
        # A numbered block is a clause by definition, whatever else is true of
        # it — the same rule reflow.py uses to refuse a join. Without this, a
        # document that opens each section near the top of a page had its real
        # headings clustered as a stamp: "1. DEFINITIONS", "3. PROJECT TEAM",
        # "11. WORKPLACE" and "16.6 Entire Agreement" came back as one finding.
        # Headings for the same reason, as cleanup.py treats them: a patent
        # licence's "Exhibit A", "Exhibit B" and schedule titles sit in one spot
        # at the top of their pages and were reported as a stamp.
        if not clause.number_label
        and clause.clause_type != "heading"
        and isinstance(clause.bbox, dict)
        and all(key in clause.bbox for key in ("x0", "y0", "x1", "y1"))
        # Boxes are page fractions, and STAMP_TOLERANCE is meaningless against
        # anything else. Rows written before both parsers normalised hold PDF
        # points, and clustering those with a 0.05 tolerance groups nothing.
        and all(0.0 <= clause.bbox[key] <= 1.0 for key in ("x0", "y0", "x1", "y1"))
        and len(clause.text.strip()) <= 40
    ]

    clusters: list[list[DsClause]] = []
    for clause in candidates:
        x, y = _centre(clause.bbox)
        for cluster in clusters:
            ax, ay = _centre(cluster[0].bbox)
            if abs(x - ax) <= STAMP_TOLERANCE and abs(y - ay) <= STAMP_TOLERANCE:
                cluster.append(clause)
                break
        else:
            clusters.append([clause])

    return [
        cluster
        for cluster in clusters
        if len({c.page_number for c in cluster if c.page_number}) >= MIN_STAMP_PAGES
    ]


def _stamps(clusters: list[list[DsClause]]) -> list[Finding]:
    out = []
    for cluster in clusters:
        pages = sorted({c.page_number for c in cluster if c.page_number})
        x, y = _centre(cluster[0].bbox)
        samples = "; ".join(repr(c.text.strip()[:20]) for c in cluster[:4])
        out.append(
            Finding(
                "REPEATED BY POSITION",
                f"{len(cluster)} short blocks at the same spot (x {x:.2f}, y {y:.2f}) on pages "
                f"{_runs(pages)} — a stamp or watermark, not clause text: {samples}",
            )
        )
    return out


def _page_coverage(clauses: list[DsClause], page_count: int | None) -> list[Finding]:
    """A page that produced nothing, and the case where no page is known."""
    if not page_count:
        return []
    seen = {c.page_number for c in clauses if c.page_number}
    if not seen:
        return [
            Finding(
                "NO PAGE NUMBERS",
                f"no clause records which of the {page_count} pages it came from, so a "
                "citation cannot be shown on the page it belongs to",
            )
        ]
    empty = [page for page in range(1, page_count + 1) if page not in seen]
    if not empty:
        return []
    return [
        Finding(
            "EMPTY PAGE",
            f"{len(empty)} of {page_count} pages produced no clauses: {_runs(empty)}",
        )
    ]


def findings(clauses: list[DsClause], *, page_count: int | None = None) -> list[Finding]:
    """Every check, in a fixed order so two runs of the same document agree."""
    clusters = _stamp_clusters(clauses)
    stamped = {clause.seq for cluster in clusters for clause in cluster}
    return [
        *_page_coverage(clauses, page_count),
        *_numbering_gaps(clauses),
        *_orphans(clauses),
        *_duplicate_numbers(clauses),
        *_dangling_refs(clauses),
        *_truncated(clauses),
        *_markup(clauses),
        *_stamps(clusters),
        *_fragments(clauses, skip=stamped),
    ]


# --- rendering --------------------------------------------------------------


def _bytes(count: int) -> str:
    for unit, size in (("MB", 1024 * 1024), ("KB", 1024)):
        if count >= size:
            return f"{count:,} bytes ({count / size:.1f} {unit})"
    return f"{count:,} bytes"


def _read_by(version: DsVersion) -> str:
    """`"pdf+ocr:reducto"` -> `"pdf v2, then OCR by reducto"`.

    The version stores one string because that is what identifies the parse,
    but a reader needs to know OCR happened — it is the single biggest factor
    in how much to trust everything below.
    """
    name, _, provider = version.parser_name.partition("+ocr:")
    base = f"{name} v{version.parser_version}"
    return f"{base}, then OCR by {provider}" if provider else base


def _rows(pairs: list[tuple[str, object]]) -> str:
    body = "\n".join(f"| {key} | {value} |" for key, value in pairs if value is not None)
    return f"| | |\n|---|---|\n{body}\n"


def _when(value) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else None


def _file_section(document: DsDocument, version: DsVersion) -> str:
    return "## File\n\n" + _rows(
        [
            ("Filename", version.filename),
            ("Title", document.title),
            ("External ref", document.external_ref),
            ("Document id", f"`{document.id}`"),
            ("Version id", f"`{version.id}`"),
            ("Version", version.version_number),
            ("Size", _bytes(version.byte_size)),
            ("Type", f"`{version.mime_type}`"),
            # The file's real identity. Two versions with the same hash are the
            # same bytes however they were named or uploaded.
            ("SHA-256", f"`{version.sha256}`"),
            ("Read by", _read_by(version)),
            ("Pages", version.page_count),
            ("Ingested", _when(version.created_at)),
            ("Ingested by", version.created_by_user_id),
        ]
    )


def _extraction_section(version: DsVersion, clauses: list[DsClause], audit: dict | None) -> str:
    kinds: dict[str, int] = defaultdict(int)
    for clause in clauses:
        kinds[clause.clause_type] += 1
    numbered = sum(1 for clause in clauses if clause.number_label)
    lengths = sorted(len(clause.text) for clause in clauses)

    pairs: list[tuple[str, object]] = [
        ("Clauses", len(clauses) or "**none**"),
        ("Characters", f"{len(version.flat_text):,}"),
        ("Kinds", ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items())) or None),
        ("Numbered / unnumbered", f"{numbered} / {len(clauses) - numbered}" if clauses else None),
        ("Deepest level", max((clause.level for clause in clauses), default=None)),
        (
            "With a page number",
            f"{sum(1 for c in clauses if c.page_number)} of {len(clauses)}" if clauses else None,
        ),
        (
            "With a box on the page",
            f"{sum(1 for c in clauses if c.bbox)} of {len(clauses)}" if clauses else None,
        ),
        (
            "Shortest / longest clause",
            f"{lengths[0]} / {lengths[-1]} characters" if lengths else None,
        ),
    ]
    if audit:
        pairs.append(
            (
                "Coverage audit",
                (
                    f"{audit.get('extracted_chars', 0):,} of "
                    f"{audit.get('source_chars', 0):,} characters extracted, "
                    f"{audit.get('unexplained', 0):,} unexplained "
                    f"({audit.get('ratio', 0) * 100:.1f}%)"
                ),
            )
        )
        if audit.get("uses"):
            pairs.append(("Document uses", ", ".join(audit["uses"])))
    else:
        # Silence here is honest rather than clean: an OCR'd scan has no source
        # text to count against, so there is no coverage figure to give.
        pairs.append(("Coverage audit", "not available (no independent source text)"))
    return "## Extraction\n\n" + _rows(pairs)


def _findings_section(found: list[Finding], warnings: list[str]) -> str:
    if not found and not warnings:
        return "## Findings\n\nNone. Every check passed.\n"

    lines = [f"## Findings — {len(found) + len(warnings)}\n"]
    for index, finding in enumerate(found, start=1):
        page = f" (p{finding.page})" if finding.page else ""
        lines.append(f"{index}. **{finding.code}**{page} — {finding.detail}")
    for offset, warning in enumerate(warnings, start=len(found) + 1):
        lines.append(f"{offset}. **PARSER WARNING** — {warning}")
    return "\n".join(lines) + "\n"


def _cite(by_seq: dict[int, DsClause], seq: int | None) -> str:
    clause = by_seq.get(seq) if seq is not None else None
    if clause is None:
        return "—"
    text = " ".join(clause.text.split()).replace("|", "\\|")
    return f"#{seq} {text[:60]}" + ("…" if len(text) > 60 else "")


# Who placed a clause, strongest evidence first — the order `tree.py` asks.
_DECIDED_BY = {
    "document": "Word's own numbering levels",
    "numbering": "decimal numbering — 5.2 sits in 5",
    "list": "list order — (b) sits beside (a); a list sits in what introduces it",
    "part": "top of an exhibit or schedule",
    "top": "top level",
    "layout": "indentation",
    "ai": "the AI, choosing from a closed list of options",
    "undecided": "**not decided** — nothing in the file settles it",
}


def _structure_section(events: list[DsEvent], clauses: list[DsClause]) -> str:
    """Who decided which clause sits inside which, and what to check.

    Likely mistakes come first because they are the point: they are the one
    kind of error the numbering can prove, and they are recomputed from the
    tree as it stands, whoever placed it.
    """
    if not clauses:
        return ""
    by_seq = {clause.seq: clause for clause in clauses}
    counts = Counter(clause.structure_source for clause in clauses)
    lines = ["## Structure\n"]
    if set(counts) == {None}:
        lines.append(
            "Built before the tree recorded who decided each placement. Upload the "
            "file again to rebuild it with the current rules.\n"
        )
    else:
        lines += [
            "Who decided where each clause sits, strongest evidence first:\n",
            "| Decided by | Clauses |",
            "|---|---|",
            *[
                f"| {_DECIDED_BY.get(source, source)} | {counts[source]} |"
                for source in _DECIDED_BY
                if counts.get(source)
            ],
            "",
        ]

    done = next((e for e in reversed(events) if e.event_type == ARRANGED), None)
    failed = next((e for e in reversed(events) if e.event_type == NOT_ARRANGED), None)
    details = (done.details or {}) if done else {}
    if done and not details.get("asked"):
        lines.append("The AI was not needed: the rules placed every clause they could be asked about.\n")
    elif done:
        refused = details.get("refused") or []
        lines.append(
            f"The AI (`{details.get('judge')}`) was asked about {_n(details.get('asked'), 'clause')} "
            f"the rules could not place, each with a closed list of options, and settled "
            f"{details.get('answered', 0)}"
            + (f"; {_n(len(refused), 'answer')} refused as not among the options" if refused else "")
            + ". It cannot move a clause the numbering placed.\n"
        )
    elif failed:
        reason = (failed.details or {}).get("reason")
        lines.append(f"The AI's answer was not used: {reason}.\n")
    elif counts.get("undecided"):
        lines.append("The AI has not been asked about the undecided clauses.\n")

    seq_of = {clause.clause_id: clause.seq for clause in clauses}
    parents = {clause.seq: seq_of.get(clause.parent_clause_id) for clause in clauses}
    broken = broken_runs(clauses, parents)
    if broken:
        lines += [
            (
                f"**⚠ Check first — {_n(len(broken), 'likely mistake')}.** These follow each other in "
                "the numbering, so they belong in the same folder, but were placed apart.\n"
            ),
            "| Clause | Problem | Next to |",
            "|---|---|---|",
            *[f"| {_cite(by_seq, s)} | {why} | {_cite(by_seq, p)} |" for s, p, why in broken],
            "",
        ]
    else:
        lines.append("**No likely mistakes:** every list — (a), (b), (c) and 1., 2., 3. — sits together.\n")

    for source, heading, note in (
        ("ai", "Placed by the AI", "Worth a glance: the numbering cannot confirm these."),
        ("undecided", "Still undecided", "Shown at the top of their part until someone places them."),
    ):
        placed = [clause for clause in clauses if clause.structure_source == source]
        if not placed:
            continue
        lines.append(f"**{heading} — {len(placed)}.** {note}\n")
        # A title line or signature left at the top level is rarely worth a
        # row of its own; one line keeps the table to real nesting decisions.
        top = [c for c in placed if parents[c.seq] is None]
        inside = [c for c in placed if parents[c.seq] is not None]
        if top:
            lines.append(f"At the top level: {', '.join(f'#{c.seq}' for c in top)}.\n")
        if inside:
            lines += [
                "| Clause | Placed inside |",
                "|---|---|",
                *[f"| {_cite(by_seq, c.seq)} | {_cite(by_seq, parents[c.seq])} |" for c in inside],
                "",
            ]
    return "\n".join(lines)


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _cell(text: str | None, limit: int | None = None) -> str:
    flat = " ".join((text or "").split())
    return (flat[:limit] if limit else flat).replace("|", "\\|")


def _pages(pages) -> str:
    pages = sorted({page for page in pages if page})
    return _runs(pages) if pages else "—"


def _cleanup_section(version: DsVersion) -> str:
    """What extraction removed or rejoined before cutting clauses.

    Nothing here is in the clause text, so this is the only place it shows. A
    footer or a stamp wrongly removed would otherwise be invisible — the
    clause list would simply be shorter.
    """
    artifacts = version.artifacts or {}
    removed, joined = artifacts.get("removed") or [], artifacts.get("joined") or []
    if not removed and not joined:
        return ""
    lines = ["## Cleanup\n"]
    if removed:
        by_reason: dict[str, list[dict]] = defaultdict(list)
        for record in removed:
            by_reason[record.get("reason") or "?"].append(record)
        lines += [
            f"**Removed — {len(removed)} blocks** that were not contract text.\n",
            "| Why | Blocks | Pages | Example |",
            "|---|---|---|---|",
        ]
        for reason, records in sorted(by_reason.items(), key=lambda item: -len(item[1])):
            pages = _pages(r.get("page") for r in records)
            example = _cell(records[0].get("text"), 60)
            lines.append(f"| {reason} | {len(records)} | {pages} | {example} |")
        lines.append("")
    if joined:
        lines += [
            f"**Rejoined — {len(joined)}** pieces of a sentence a page break or a line cut apart.\n",
            "| Why | Pages | Where |",
            "|---|---|---|",
            *[
                f"| {j.get('reason')} | {_pages(j.get('pages') or [])} | {_cell(j.get('text'))} |"
                for j in joined
            ],
            "",
        ]
    return "\n".join(lines)


# How the ladder in anchoring.py found each annotation in this version.
_FOUND_BY = {
    1: "in its clause, unchanged",
    2: "at its old position",
    3: "its exact words, moved elsewhere",
    4: "words close to its own — they were edited",
}


def _annotations_section(annotations: list[DsAnnotation], clauses: list[DsClause]) -> str:
    """Every note on the document: lost ones first, then where the rest are.

    A lost note keeps the words it was on, so a reader can see what it was
    about and where it might belong — that is what re-linking needs.
    """
    if not annotations:
        return ""
    by_clause = {clause.clause_id: clause for clause in clauses}
    lost = [a for a in annotations if a.anchor_state == "orphaned"]
    placed = [a for a in annotations if a.anchor_state != "orphaned"]
    lines = ["## Annotations\n", f"{_n(len(annotations), 'note')} on this document.\n"]
    if lost:
        lines += [
            (
                f"**⚠ Lost — {_n(len(lost), 'note')} to re-link.** Their words are not in "
                "this version. Nothing was deleted: each keeps the words it was on.\n"
            ),
            "| Note | Kind | It was on |",
            "|---|---|---|",
            *[
                f"| `{a.id[:8]}` {_cell(a.body, 60)} | {a.kind} | “{_cell(a.anchor_quote_exact, 90)}” |"
                for a in lost
            ],
            "",
        ]
    if placed:
        rows = []
        for a in placed:
            clause = by_clause.get(a.anchor_clause_id or "")
            where = (
                "—"
                if clause is None
                else f"#{clause.seq} {clause.number_label.strip() if clause.number_label else ''}".strip()
            )
            found = _FOUND_BY.get(a.anchor_rung, "placed by a person")
            rows.append(
                f"| `{a.id[:8]}` {_cell(a.body, 60)} | {a.kind} | {where} | "
                f"“{_cell(a.anchor_quote_exact, 70)}” | {found} |"
            )
        lines += [
            "| Note | Kind | On clause | Words | How it was found |",
            "|---|---|---|---|---|",
            *rows,
            "",
        ]
    return "\n".join(lines)


def _page_span(clause: DsClause) -> str | None:
    """"p3", or "p3-4" for a clause rejoined across a page break."""
    pages = sorted({r.get("page") for r in clause.source_regions or [] if r.get("page")})
    if len(pages) > 1:
        return f"p{pages[0]}-{pages[-1]}"
    return f"p{clause.page_number}" if clause.page_number else None


# Who placed a clause, where a reader should know it. Numbering and the file's
# own evidence are the default and go unmarked, or every line would carry one.
_TREE_TAGS = {"ai": " [AI]", "undecided": " [?]"}


def _tree_line(clause: DsClause) -> str:
    label = clause.number_label.strip() if clause.number_label else ""
    text = " ".join(clause.text.split())
    if label and text.startswith(label):
        text = text[len(label) :].lstrip()
    snippet = text[:OUTLINE_CHARS] + ("…" if len(text) > OUTLINE_CHARS else "")
    where = ", ".join(part for part in (_page_span(clause), f"#{clause.seq}") if part)
    tag = _TREE_TAGS.get(clause.structure_source or "", "")
    return f"{label + ' ' if label else ''}{snippet}  ({where}){tag}"


def _tree_section(clauses: list[DsClause], title: str) -> str:
    """The clauses as a folder tree, the map a reader orients by.

    Drawn from `parent_clause_id`, not from `level`, so what is drawn is exactly
    what is stored: a clause shown inside another is inside it.
    """
    if not clauses:
        return ""
    ids = {clause.clause_id for clause in clauses}
    children: dict[str | None, list[DsClause]] = defaultdict(list)
    for clause in clauses:  # in reading order, so every folder lists in reading order
        parent = clause.parent_clause_id if clause.parent_clause_id in ids else None
        children[parent].append(clause)

    lines = [
        "## Clause tree\n",
        (
            "One line per clause; a clause drawn under another sits inside it. `#n` is the "
            "clause's number in this report, `[AI]` marks a placement the AI made, and `[?]` "
            "one nothing in the file could decide.\n"
        ),
        "```",
        title,
    ]

    def draw(parent: str | None, prefix: str) -> None:
        folder = children.get(parent, [])
        for position, clause in enumerate(folder):
            last = position == len(folder) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}{_tree_line(clause)}")
            draw(clause.clause_id, prefix + ("    " if last else "│   "))

    draw(None, "")
    drawn = len(lines) - 4
    lines.append("```")
    if drawn < len(clauses):
        # Only a loop in the parent links can hide a clause from the tree.
        lines.append(
            f"\n**{len(clauses) - drawn} clauses are not drawn:** their parent links loop."
        )
    return "\n".join(lines) + "\n"


def _clauses_section(clauses: list[DsClause]) -> str:
    """Every clause in full, with everything stored about it.

    The tree above is the map; this is the territory. It makes the page
    larger than the document it describes, which is the point — a reader, human
    or model, gets the text, the structure, the offsets and the geometry
    together, without a second lookup to resolve a clause id.

    The text is written raw rather than fenced. A fence would render more
    safely, but the primary reader is a model and fence markers around 221
    blocks are noise it has to see past.
    """
    if not clauses:
        return ""
    lines = ["## Clauses\n"]
    for clause in clauses:
        label = clause.number_label.strip() if clause.number_label else "unnumbered"
        head = f"### #{clause.seq} · {label} · level {clause.level} · {clause.clause_type}"
        if span := _page_span(clause):
            head += f" · {span}"
        lines.append(head + "\n")

        # The clause id first: it is the stable identity an annotation anchors
        # to, and the one field a reader cannot derive from anything else here.
        meta = [f"`{clause.clause_id}`"]
        if clause.parent_clause_id:
            meta.append(f"parent `{clause.parent_clause_id}`")
        if clause.structure_source:
            meta.append(f"placed by {clause.structure_source}")
        meta.append(f"chars {clause.char_start:,}-{clause.char_end:,}")
        if isinstance(clause.bbox, dict) and all(
            key in clause.bbox for key in ("x0", "y0", "x1", "y1")
        ):
            box = clause.bbox
            meta.append(
                f"box x {box['x0']:.3f}-{box['x1']:.3f}, y {box['y0']:.3f}-{box['y1']:.3f}"
            )
        regions = clause.source_regions or []
        if len(regions) > 1:
            # Rejoined across a break: a viewer must light up every piece.
            meta.append(f"{len(regions)} pieces, on pages {_pages(r.get('page') for r in regions)}")
        lines.append(" · ".join(meta) + "\n")
        lines.append(clause.text + "\n")
    return "\n".join(lines)


def _detail_value(value) -> str:
    text = str(value)
    return text if len(text) <= EVENT_VALUE_CHARS else text[:EVENT_VALUE_CHARS] + "…"


def _history_section(events: list[DsEvent]) -> str:
    """Every event, with the details it recorded.

    The ingest event holds the audit counts and the parser identity at the time
    it ran, which is the only record of how a version was produced once the
    code has moved on.
    """
    if not events:
        return ""
    lines = ["## History\n"]
    for event in events:
        lines.append(f"- `{_when(event.created_at)}` **{event.event_type}**")
        if isinstance(event.details, dict):
            for key, value in sorted(event.details.items()):
                if value in (None, [], {}, ""):
                    continue
                lines.append(f"  - {key}: {_detail_value(value)}")
    return "\n".join(lines) + "\n"


def report(db: Session, version_id: str) -> str:
    """The whole page for one version.

    Read-only and derived: it stores nothing, so it can be regenerated after
    any change to the checks without a migration or a backfill.
    """
    version = db.get(DsVersion, version_id)
    if version is None:
        raise LookupError(f"no such version: {version_id}")
    document = db.get(DsDocument, version.document_id)
    clauses = list(
        db.scalars(
            select(DsClause).where(DsClause.version_id == version_id).order_by(DsClause.seq)
        )
    )
    events = list(
        db.scalars(
            select(DsEvent)
            .where(DsEvent.version_id == version_id)
            .order_by(DsEvent.created_at)
        )
    )
    audit = next(
        (
            event.details["audit"]
            for event in reversed(events)
            if isinstance(event.details, dict) and event.details.get("audit")
        ),
        None,
    )

    title = version.filename or (document.title if document else None) or version_id
    return "\n".join(
        section
        for section in (
            f"# {title}\n",
            _file_section(document, version),
            _extraction_section(version, clauses, audit),
            _findings_section(
                findings(clauses, page_count=version.page_count),
                list(version.parse_warnings or []),
            ),
            _annotations_section(for_document(db, version.document_id), clauses),
            _structure_section(events, clauses),
            _tree_section(clauses, title),
            _cleanup_section(version),
            _clauses_section(clauses),
            _history_section(events),
        )
        if section
    )
