"""A PDF read as a Word document that flows.

Not a photocopy of the page. pdf2docx rebuilds each PDF page as a box of its
own with every paragraph pinned to a point on it, and then one line that Word
wraps differently — a font it does not have, a space it measures at a hair more
— pushes the box past the paper and the page splits in two. Five contracts came
out 2, 42, 8, 6 and 39 pages long against 2, 36, 5, 6 and 8.

This reads the same PDF for what a Word document is made of — paragraphs, each
in its own fonts, sizes, weights and alignment, indented where the page
indented them — and lets Word lay them out. The words, their look and their
order are kept; where the page turns is Word's to decide, which is also what
makes the file editable: type a sentence and everything after it moves down,
as in any Word document.

What is kept: the font of every run (read out of the font file the PDF carries,
so a subset called "ABCDEF+CIDFont" is named as the Times New Roman it is), its
size, bold, italic and colour, the paragraph's alignment, indentation and the
space above it, ruled tables, pictures, blocks the page printed beside each
other, and the running header and footer — in Word's header and footer, where
they belong, not in the middle of the text.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise

import fitz

BAND = 0.12          # the top and bottom eighth of a page: where furniture sits
GAP = 0.55           # a gap this much wider than the line pitch starts a paragraph
MOVED = 18.0         # points a line must shift to count as a paragraph of its own
TAB = 18.0           # points of empty space that mean a tab stop, not word spacing
BREAK = 0.80         # a page whose text stops above this much of the text area broke on purpose
NUMBER = re.compile(r"^\(?(\d{1,2}(\.\d{1,2})*|[ivxlc]{1,5}|[a-z])[.)]\s")


class ConversionError(ValueError):
    """The PDF could not be read as a document; the message says why."""


@dataclass
class Run:
    text: str
    font: str = ""
    size: float = 11.0
    bold: bool = False
    italic: bool = False
    colour: int = 0


@dataclass
class Para:
    runs: list[Run] = field(default_factory=list)
    align: str = ""          # "", "center", "right", "both"
    left: float = 0.0        # points in from the text's left edge
    first: float = 0.0       # the first line's own offset; below zero is a hanging indent
    before: float = 0.0      # space above, in points
    pitch: float = 0.0       # the height of a line of it on the page, in points
    page_break: bool = False
    tabs: list[float] = field(default_factory=list)   # points from the left edge of the text
    top: float = 0.0         # where it sat on the page, kept for measuring the gaps
    bottom: float = 0.0

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


@dataclass
class Table:
    """A ruled table, or blocks the page printed beside each other — a signature
    row, an address pair. Side by side in Word is a table with no lines, and
    unlike a column break a table row cannot start a new page by itself."""

    rows: list[list[list[Para]]]     # rows → cells → paragraphs
    widths: list[float]              # each column's share of the text width
    ruled: bool = False


@dataclass
class Picture:
    content: bytes
    width: float
    height: float


@dataclass
class Paper:
    width: float = 612.0
    height: float = 792.0
    left: float = 72.0
    right: float = 72.0
    top: float = 72.0
    bottom: float = 72.0


@dataclass
class Doc:
    body: list = field(default_factory=list)      # Para | Table | Picture
    header: list = field(default_factory=list)      # Para | Picture
    footer: list = field(default_factory=list)
    paper: Paper = field(default_factory=Paper)
    numbered: str = ""                            # "header" or "footer": the page number goes there


# --- the words on the page --------------------------------------------------------------


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _page_number(said: str) -> bool:
    """"3", "Page 3", "3 of 11" — a number that changes from page to page."""
    return bool(re.fullmatch(r"(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?", said))


_FOUNDRY = re.compile(r"[-,_ ]?(MT|PS|PSMT|Std|Pro)$")
_STYLES = re.compile(r"[-,_ ]?(regular|bold|italic|oblique|light|medium|semibold|black|roman|bolditalic)$", re.IGNORECASE)
_KNOWN = {"arialmt": "Arial", "arial": "Arial", "arialunicodems": "Arial Unicode MS",
          "timesnewromanpsmt": "Times New Roman", "timesnewromanps": "Times New Roman",
          "timesnewroman": "Times New Roman", "couriernew": "Courier New", "calibri": "Calibri",
          "cambria": "Cambria", "helvetica": "Helvetica", "georgia": "Georgia", "verdana": "Verdana",
          "nimbusroman": "Times New Roman", "nimbussans": "Arial", "liberationserif": "Times New Roman",
          "liberationsans": "Arial"}


def _family(name: str) -> str:
    """"ABCDEF+Arial-BoldItalicMT" is the Arial family; the weight and the slant
    belong to the run, not to the family, and MT and PS are the foundry's."""
    name = re.sub(r"^[A-Z]{6}\+", "", name or "").split(";")[0]
    for _ in range(4):
        name = _STYLES.sub("", _FOUNDRY.sub("", name))
    plain = re.sub(r"[^a-z]", "", name.lower())
    if plain in _KNOWN:
        return _KNOWN[plain]
    return re.sub(r"\s+", " ", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)).strip() or "Arial"


def _families(pdf) -> dict[str, str]:
    """Each font's family, out of the font file the PDF carries when there is
    one: a subset named "ABCDEF+CIDFont" is Times New Roman inside."""
    from fontTools.ttLib import TTFont

    found: dict[str, str] = {}
    for page in pdf:
        for xref, _, _, basefont, *_ in page.get_fonts():
            if basefont in found:
                continue
            family = ""
            try:
                family = TTFont(io.BytesIO(pdf.extract_font(xref)[3]))["name"].getDebugName(1) or ""
            except Exception:  # not embedded, or not a font file fontTools reads
                pass
            found[basefont] = found[re.sub(r"^[A-Z]{6}\+", "", basefont)] = _family(family or basefont)
    return found


def _furniture(pdf) -> tuple[dict[str, str], float, float]:
    """What the PDF prints at the top and the foot of most of its pages, and how
    deep those bands are, as a share of the page.

    The band is measured only where those words were found in the band: the same
    words halfway down a page — a signature stamp — would otherwise take a third
    of the paper for a margin.
    """
    seen: dict[str, Counter] = {"header": Counter(), "footer": Counter()}
    edges: dict[tuple[str, str], list[float]] = {}
    for page in pdf:
        height = page.rect.height or 1
        for block in page.get_text("blocks"):
            y0, y1, said = block[1], block[3], _flat(block[4])
            where = "header" if y1 < BAND * height else "footer" if y0 > (1 - BAND) * height else ""
            if not said or not where:
                continue
            seen[where][said] += 1
            band = edges.setdefault((where, said), [1.0, 0.0])
            band[0], band[1] = min(band[0], y0 / height), max(band[1], y1 / height)
    enough = max(2, round(0.5 * pdf.page_count))
    where_of = {said: where for where in seen for said, count in seen[where].items()
                if count >= enough or _page_number(said)}
    top = max([edges[("header", s)][1] for s, w in where_of.items() if w == "header"], default=0.0)
    foot = min([edges[("footer", s)][0] for s, w in where_of.items() if w == "footer"], default=1.0)
    return where_of, min(top, BAND), max(foot, 1 - BAND)


def _runs(line: dict, families: dict[str, str]) -> list[Run]:
    """A line's spans as runs, with neighbours that look alike joined."""
    made: list[Run] = []
    for span in line["spans"]:
        if not span["text"]:
            continue
        name = span["font"]
        flags = span["flags"]
        run = Run(text=span["text"], font=families.get(name) or _family(name),
                  size=round(span["size"] * 2) / 2,
                  bold=bool(flags & 16) or "bold" in name.lower() or "black" in name.lower(),
                  italic=bool(flags & 2) or "italic" in name.lower() or "oblique" in name.lower(),
                  colour=span.get("color", 0))
        if made and _alike(made[-1], run):
            made[-1].text += run.text
        else:
            made.append(run)
    return made


def _alike(one: Run, other: Run) -> bool:
    return (one.font, one.size, one.bold, one.italic, one.colour) == (
        other.font, other.size, other.bold, other.italic, other.colour)


def _lines(block: dict) -> list[dict]:
    return [line for line in block.get("lines", []) if any(s["text"].strip() for s in line["spans"])]


def _paragraphs(rows: list[dict], column: tuple[float, float], families: dict[str, str]) -> list[Para]:
    """Printed rows gathered into paragraphs, where the page shows one starting:
    a gap taller than the line pitch, a line the page ended on purpose, a line
    that opens with a clause number, or one indented away from the rest."""
    if not rows:
        return []
    right = column[1]
    steps = [round(b["bbox"][1] - a["bbox"][1], 1) for a, b in pairwise(rows)]
    steps = [step for step in steps if step > 1]
    pitch = sorted(steps)[len(steps) // 2] if steps else (rows[0]["bbox"][3] - rows[0]["bbox"][1]) or 12.0

    # How wide the lines of each block actually run: a paragraph set narrower
    # than the page has not ended just because its line stops short of the
    # margin — it ends where the next word would have fitted and did not follow.
    measures: dict[object, float] = {}
    for line in rows:
        key = line.get("block")
        measures[key] = max(measures.get(key, 0.0), line["bbox"][2])

    made: list[Para] = []
    group: list[dict] = []
    for line in rows:
        if group:
            before = group[-1]
            text = "".join(span["text"] for span in line["spans"]).lstrip()
            edge = min(right, max(measures.get(before.get("block"), right),
                                  measures.get(line.get("block"), right)))
            if (line["bbox"][1] - before["bbox"][1] > pitch * (1 + GAP)
                    or _ended(before, line, edge) or NUMBER.match(text) is not None
                    or abs(line["bbox"][0] - group[0]["bbox"][0]) > MOVED
                    or bool(line.get("stops")) != bool(before.get("stops"))):
                made.append(_gather(group, column, families, pitch))
                group = []
        group.append(line)
    if group:
        made.append(_gather(group, column, families, pitch))
    return [para for para in made if para.text.strip()]


def _ended(line: dict, following: dict, right: float) -> bool:
    """Did the page end a paragraph here, or only run out of room?

    The test is the one a typesetter would make: the next line's first word —
    would it have fitted on this one? If it would have, the page broke the line
    on purpose and a paragraph ended; if it would not, the line simply wrapped,
    and joining them back is what makes the text editable. Splitting on a short
    line alone turned 2,495 lines of a contract into 3,244.
    """
    room = right - line["bbox"][2]
    if room < 6:
        return False
    text = "".join(span["text"] for span in following["spans"]).strip()
    word = text.split(" ")[0] if text else ""
    if not word:
        return True
    letters = len(text) or 1
    wide = (following["bbox"][2] - following["bbox"][0]) / letters      # this line's own letters
    return room > wide * (len(word) + 1)


def _gather(lines: list[dict], column: tuple[float, float], families: dict[str, str], pitch: float) -> Para:
    """One paragraph out of the lines the page printed for it, with the shape
    the page gave it: where it starts, where it ends, how it is lined up."""
    left, right = column
    runs: list[Run] = []
    for index, line in enumerate(lines):
        for place, run in enumerate(_runs(line, families)):
            if runs and index and not place and not runs[-1].text.endswith((" ", "-")):
                runs[-1].text += " "     # a line break inside a paragraph is a space
            if runs and _alike(runs[-1], run):
                runs[-1].text += run.text
            else:
                runs.append(run)
    starts = [line["bbox"][0] for line in lines]
    ends = [line["bbox"][2] for line in lines]
    body = min(starts[1:]) if len(starts) > 1 else starts[0]
    width = (right - left) or 1
    para = Para(runs=runs, pitch=round(pitch, 1), left=max(round(body - left, 1), 0.0),
                first=round(starts[0] - body, 1),
                tabs=sorted({round(stop - left, 1) for line in lines for stop in line.get("stops", [])}),
                top=min(line["bbox"][1] for line in lines),
                bottom=max(line["bbox"][3] for line in lines))
    if len(lines) > 1 and all(end > right - 0.02 * width for end in ends[:-1]):
        para.align = "both"
    elif all(abs((start - left) - (right - end)) < 0.06 * width and start - left > 0.06 * width
             for start, end in zip(starts, ends, strict=False)):
        para.align, para.left, para.first = "center", 0.0, 0.0
    elif all(end > right - 0.03 * width for end in ends) and body - left > 0.15 * width:
        para.align, para.left, para.first = "right", 0.0, 0.0
    return para


def _ruled(found, families: dict[str, str]) -> Table | None:
    """A table the page ruled, cell by cell. None when reading it would lose
    words — a "table" the finder saw in the rules of a form, whose text it then
    cannot give back."""
    try:
        read = found.extract()
    except Exception:
        return None
    rows = [[[Para(runs=[Run(text=piece)]) for piece in str(cell or "").split("\n") if piece.strip()]
             for cell in line] for line in read]
    rows = [row for row in rows if any(row)]
    if not rows:
        return None
    columns = max(len(row) for row in rows)
    for row in rows:
        row.extend([] for _ in range(columns - len(row)))
    return Table(rows=rows, widths=[1 / columns] * columns, ruled=True)


def _letters(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.lower())


def _rows(fragments: list[dict]) -> list[dict]:
    """Every printed row of the page, whole.

    A PDF draws words, not lines, and a reader splits them into blocks wherever
    the gaps are wide — so one justified line of a contract can arrive as four
    blocks, and read block by block it becomes four paragraphs pinned to the
    right. Rows are put back together by the line they were printed on, and a
    wide gap inside one becomes a tab stop where the page had it: which is how
    a signature row, or "Name: ______ Date: ______", is written in Word.
    """
    rows: list[list[dict]] = []
    for line in sorted(fragments, key=lambda l: (round(l["bbox"][1], 1), l["bbox"][0])):
        top, bottom = line["bbox"][1], line["bbox"][3]
        for row in rows:
            other_top = min(f["bbox"][1] for f in row)
            other_bottom = max(f["bbox"][3] for f in row)
            overlap = min(bottom, other_bottom) - max(top, other_top)
            tall = min(bottom - top, other_bottom - other_top) or 1
            if overlap > 0.6 * tall:
                row.append(line)
                break
        else:
            rows.append([line])
    made: list[dict] = []
    for row in rows:
        row.sort(key=lambda l: l["bbox"][0])
        spans: list[dict] = []
        stops: list[float] = []
        for index, line in enumerate(row):
            if index:
                before = row[index - 1]
                letters = max(len("".join(s["text"] for s in before["spans"])), 1)
                wide = (before["bbox"][2] - before["bbox"][0]) / letters
                gap = line["bbox"][0] - before["bbox"][2]
                if gap > max(6 * wide, TAB):
                    # A form line or a signature row leaves a gap like this. A
                    # justified line only spreads its words, and those gaps —
                    # three or four letters wide — are spaces, not tab stops.
                    spans.append({**line["spans"][0], "text": "\t"})
                    stops.append(line["bbox"][0])
                elif gap > 0.6 * wide and not _spaced(spans, line):
                    spans.append({**line["spans"][0], "text": " "})
            spans.extend(line["spans"])
        made.append({"spans": spans, "stops": stops, "block": row[0].get("block"),
                     "bbox": (row[0]["bbox"][0], min(f["bbox"][1] for f in row),
                              max(f["bbox"][2] for f in row), max(f["bbox"][3] for f in row))})
    made.sort(key=lambda line: (round(line["bbox"][1], 1), line["bbox"][0]))
    return made


def _gutter(pages: list[list[dict]], width: float, least: int = 0) -> float | None:
    """Where the document's columns are divided, if it has columns.

    Found once for the document, from the pages that have columns: a contract
    set in two columns still opens on a full-width title page and ends on a
    full-width signature page, and counted together those pages hide the
    gutter. A page that crosses it keeps its own single column.
    """
    if sum(len(fragments) for fragments in pages) < 40:
        return None
    for share in (0.5, 0.45, 0.55):
        middle = width * share
        columned = 0
        for fragments in pages:
            if len(fragments) < 8:
                continue
            across = sum(1 for f in fragments if f["bbox"][0] < middle - 6 < middle + 6 < f["bbox"][2])
            left = sum(1 for f in fragments if f["bbox"][2] <= middle + 6)
            right = sum(1 for f in fragments if f["bbox"][0] >= middle - 6)
            if across < 0.05 * len(fragments) and min(left, right) > 0.2 * len(fragments):
                columned += 1
        if columned >= (least or max(2, round(0.34 * len(pages)))):
            return middle
    return None


def _spaced(spans: list[dict], line: dict) -> bool:
    """Is there already a space where these two fragments meet?

    A PDF that spreads a justified line writes the space itself — "AGGREGATE "
    — and a second one added for the gap leaves "AGGREGATE  LIABILITY", which
    reads wrong in Word and matches nothing when a citation searches for it.
    """
    tail = spans[-1]["text"] if spans else " "
    head = line["spans"][0]["text"] if line["spans"] else " "
    return tail.endswith((" ", "\t")) or head.startswith((" ", "\t"))


def _sides(fragments: list[dict], gutter: float | None) -> list[list[dict]]:
    """A page's fragments in reading order: what runs across the page where it
    stands, and each run of two-column text as its left column then its right.

    A page is not all one thing — a contract's first page opens full width with
    the parties and turns to two columns for the definitions — so the split is
    made between the lines that cross the gutter, not once for the page.
    """
    if gutter is None or len(fragments) < 8:
        return [fragments]
    groups: list[list[dict]] = []
    run: list[dict] = []
    for line in sorted(fragments, key=lambda f: (round(f["bbox"][1], 1), f["bbox"][0])):
        if line["bbox"][0] < gutter - 6 < gutter + 6 < line["bbox"][2]:
            groups.extend(_two(run, gutter))
            run = []
            groups.append([line])
        else:
            run.append(line)
    groups.extend(_two(run, gutter))
    return [group for group in groups if group]


def _two(run: list[dict], gutter: float) -> list[list[dict]]:
    # By its middle, so that a fragment reaching just past the gutter lands in
    # one column and not in both.
    left = [f for f in run if (f["bbox"][0] + f["bbox"][2]) / 2 < gutter]
    right = [f for f in run if (f["bbox"][0] + f["bbox"][2]) / 2 >= gutter]
    return [left, right] if left and right else [run]










def _tables_that_hold(page, found, families: dict[str, str]) -> list[tuple[tuple, Table]]:
    """Only the tables that give back what stands inside them.

    The finder reads the ruled lines of a form as a table and then hands back a
    fraction of its words; the words the page covered would be gone from the
    document. A table that does not hold its own text is not used, and its words
    are read as ordinary paragraphs.
    """
    kept: list[tuple[tuple, Table]] = []
    blocks = [b for b in page.get_text("dict")["blocks"] if b.get("type") != 1]
    for table in found:
        made = _ruled(table, families)
        if made is None:
            continue
        bx0, by0, bx1, by1 = table.bbox
        inside = "".join("".join(span["text"] for line in b.get("lines", []) for span in line["spans"])
                         for b in blocks
                         if b["bbox"][0] >= bx0 - 2 and b["bbox"][1] >= by0 - 2
                         and b["bbox"][2] <= bx1 + 2 and b["bbox"][3] <= by1 + 2)
        gave = "".join(para.text for row in made.rows for cell in row for para in cell)
        want, got = Counter(_letters(inside)), Counter(_letters(gave))
        if sum(want.values()) and sum((want & got).values()) < 0.95 * sum(want.values()):
            continue
        kept.append((table.bbox, made))
    return kept


ENDS = tuple(".:;!?\u2026\"')]")


def _rejoin(doc: Doc) -> None:
    """A sentence that runs from one column into the next, or from one page to
    the next, put back into one paragraph.

    The page has to break it — there is no more room — but a Word document has
    no reason to: split, the clause is two clauses, the citation that quotes it
    matches neither, and a change to it re-wraps only half. Joined only where
    the page plainly continued: no full stop before, a small letter after, same
    type, and not where the page broke on purpose.
    """
    made: list = []
    for piece in doc.body:
        if made and isinstance(piece, Para) and isinstance(made[-1], Para) and _continues(made[-1], piece):
            before = made[-1]
            if before.runs and not before.runs[-1].text.endswith((" ", "-")):
                before.runs[-1].text += " "
            for run in piece.runs:
                if before.runs and _alike(before.runs[-1], run):
                    before.runs[-1].text += run.text
                else:
                    before.runs.append(run)
            before.bottom = piece.bottom
            continue
        made.append(piece)
    doc.body = made


def _shouted(said: str) -> bool:
    letters = [c for c in said if c.isalpha()]
    return len(letters) > 8 and sum(c.isupper() for c in letters) > 0.9 * len(letters)


def _continues(before: Para, after: Para) -> bool:
    said, next_said = before.text.rstrip(), after.text.lstrip()
    if not said or not next_said or after.page_break:
        return False
    if said.endswith(ENDS) or NUMBER.match(next_said):
        return False
    if not next_said[:1].islower() and not (_shouted(said) and _shouted(next_said)):
        # A new clause starts with a capital — but a liability clause set in
        # capitals carries on in capitals, and those are the ones most often
        # quoted back.
        return False
    first = before.runs[-1] if before.runs else None
    second = after.runs[0] if after.runs else None
    return bool(first and second and first.size == second.size and first.bold == second.bold)


def read(content: bytes) -> Doc:
    """The PDF as paragraphs, tables, pictures and page furniture."""
    with fitz.open(stream=content, filetype="pdf") as pdf:
        if not pdf.page_count:
            raise ConversionError("The PDF has no pages.")
        families = _families(pdf)
        where_of, top_edge, foot_edge = _furniture(pdf)
        doc = Doc()
        doc.paper = Paper(width=pdf[0].rect.width, height=pdf[0].rect.height)
        lefts: list[float] = []
        rights: list[float] = []
        tops: list[float] = []
        bottoms: list[float] = []
        pages = []

        for page in pdf:
            height = page.rect.height or 1
            try:
                tables = list(page.find_tables())
            except Exception:       # a page whose rules confuse the finder
                tables = []
            tables = _tables_that_hold(page, tables, families)
            covered = [bbox for bbox, _ in tables]
            body: list[dict] = []
            furniture: list[tuple[str, dict, str]] = []
            for block in page.get_text("dict")["blocks"]:
                x0, y0, x1, y1 = block["bbox"]
                if block.get("type") == 1:
                    # A logo printed above the text belongs in Word's header,
                    # where it will be drawn on every page as the PDF drew it.
                    if y1 <= top_edge * height:
                        furniture.append(("header", block, f"picture at {round(x0)},{round(y0)}"))
                    elif y0 >= foot_edge * height:
                        furniture.append(("footer", block, f"picture at {round(x0)},{round(y0)}"))
                    else:
                        body.append(block)
                    continue
                said = _flat("".join(span["text"] for line in block.get("lines", [])
                                     for span in line["spans"]))
                if not said:
                    continue
                where = where_of.get(said, "")
                if not where and _page_number(said):
                    where = "footer" if y0 > 0.5 * height else "header"
                if where:
                    furniture.append((where, block, said))
                    continue
                if any(x0 >= bx0 - 2 and y0 >= by0 - 2 and x1 <= bx1 + 2 and y1 <= by1 + 2
                       for bx0, by0, bx1, by1 in covered):
                    continue        # these words belong to the table
                body.append(block)
                lefts.append(x0)
                rights.append(x1)
                tops.append(y0)
                bottoms.append(y1)
            pages.append((page, body, tables, furniture, height))

        if not lefts:
            raise ConversionError("The PDF has no text to read — it is a picture of a page.")
        left = sorted(lefts)[len(lefts) // 20]            # the column, not the odd block outside it
        right = sorted(rights)[-max(len(rights) // 20, 1)]
        column = (left, right)
        doc.paper.left = round(left, 1)
        doc.paper.right = round(max(doc.paper.width - right, 18.0), 1)
        doc.paper.top = round(min(tops), 1)
        doc.paper.bottom = round(max(doc.paper.height - max(bottoms), 18.0), 1)
        if doc.footer:
            doc.paper.bottom = max(doc.paper.bottom, round(doc.paper.height * (1 - foot_edge), 1))

        gutter = _gutter([[line for block in blocks if block.get("type") != 1
                           for line in block.get("lines", [])
                           if any(span["text"].strip() for span in line["spans"])]
                          for _, blocks, _, _, _ in pages], doc.paper.width)
        said_before: set[str] = set()
        numbered_taken: set[str] = set()
        ended_early = False
        for index, (page, blocks, tables, furniture, height) in enumerate(pages):
            for where, block, said in furniture:
                if said in said_before or (_page_number(said) and where in numbered_taken):
                    continue      # the number differs on every page; one of them is the footer
                said_before.add(said)
                if _page_number(said):
                    numbered_taken.add(where)
                    doc.numbered = where
                into = doc.header if where == "header" else doc.footer
                if block.get("type") == 1:
                    into.append(Picture(content=block.get("image") or b"",
                                        width=block["bbox"][2] - block["bbox"][0],
                                        height=block["bbox"][3] - block["bbox"][1]))
                else:
                    into.extend(_paragraphs(_rows(block.get("lines", [])), column, families))
            started = len(doc.body)
            _page(doc, blocks, tables, column, families, gutter, doc.paper.width)
            if index and ended_early and len(doc.body) > started and isinstance(doc.body[started], Para):
                doc.body[started].page_break = True
            text_bottom = max((b["bbox"][3] for b in blocks if b.get("type") != 1), default=0.0)
            ended_early = bool(blocks) and text_bottom < doc.paper.top + BREAK * (
                height - doc.paper.top - doc.paper.bottom)
    if doc.header:
        doc.paper.top = max(doc.paper.top, round(doc.paper.height * top_edge + 6, 1))
    _rejoin(doc)
    return doc


def _page(doc: Doc, blocks: list[dict], tables, column, families, gutter: float | None,
          width: float) -> None:
    """One page's rows, tables and pictures added to the document, in the order
    it printed them — column by column where the page has columns — each
    paragraph carrying the space the page left above it."""
    fragments = []
    for block in blocks:
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            if any(span["text"].strip() for span in line["spans"]):
                line["block"] = block.get("number")
                fragments.append(line)
    # A page of its own can be in columns — a schedule, a definitions page —
    # in a document that is otherwise a single column.
    groups = _sides(fragments, gutter if gutter is not None else _gutter([fragments], width, least=1))
    pieces: list[tuple[tuple, object]] = []
    for bbox, made in tables:
        pieces.append(((0, bbox[1], bbox[3]), made))
    for block in blocks:
        if block.get("type") == 1:
            pieces.append(((0, block["bbox"][1], block["bbox"][3]),
                           Picture(content=block.get("image") or b"",
                                   width=block["bbox"][2] - block["bbox"][0],
                                   height=block["bbox"][3] - block["bbox"][1])))
    for number, group in enumerate(groups):
        if not group:
            continue
        bounds = column if len(groups) == 1 else (min(f["bbox"][0] for f in group),
                                                  max(f["bbox"][2] for f in group))
        for para in _paragraphs(_rows(group), bounds, families):
            pieces.append(((number, para.top, para.bottom), para))
    pieces.sort(key=lambda piece: piece[0])
    bottom: float | None = None
    for (_, top, foot), piece in pieces:
        if isinstance(piece, Para) and bottom is not None:
            piece.before = round(min(max(top - bottom - 0.25 * piece.pitch, 0.0), 24.0), 1)
        doc.body.append(piece)
        bottom = foot
