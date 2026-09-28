"""A scanned contract written down as markdown, and rebuilt from it as Word.

A scan holds no text at all — every page is a photograph — so there is nothing
to convert. What there is, after OCR, is a reading: blocks of words with the
place on the page each was found, and what kind of block it is. That reading is
written to a markdown file, and *how it looked* is written beside each block:
the page, where it sits, how tall its lines are, whether it is a heading, and
the typeface assumed for it. The Word document is rebuilt from that file.

Markdown, rather than a hidden format, for a reason a legal team cares about:
the file is the record of what the machine read. It can be opened, read,
corrected by hand and rebuilt, and the correction is visible as a change to a
text file rather than buried in a binary.

The styling is **measured where it can be and assumed where it cannot**, and
the file says which is which. A photograph of a page does not carry its fonts:
the size is worked out from the height of the printed lines, the weight from
the kind of block OCR found, and the family is an assumption, named in the
front matter so that changing it re-makes the whole document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .toword import Doc, Paper, Para, Picture, Run

HEADINGS = {"Title": 1, "Section Header": 2}
FURNITURE = {"Header": "header", "Footer": "footer"}
SKIP = {"Page Number"}
# A line of mixed-case text inks about four fifths of its type size — cap height
# down to the descenders — and each line after the first adds a whole line's
# pitch, about a sixth more than the size. Those two turn the height of a block
# OCR found into the size of the type in it.
INK = 0.8
PITCH = 1.17


@dataclass
class Block:
    text: str
    kind: str
    page: int
    box: tuple[float, float, float, float]   # of the page, 0–1
    confidence: str = ""


def _blocks(rows) -> list[Block]:
    made: list[Block] = []
    for row in rows:
        box = row.get("bbox") or {}
        text = re.sub(r"\s+", " ", (row.get("content") or "")).strip()
        if not text or row.get("type") in SKIP:
            continue
        made.append(Block(text=text, kind=row.get("type") or "Text", page=int(row.get("page") or 1),
                          box=(float(box.get("x0", 0)), float(box.get("y0", 0)),
                               float(box.get("x1", 1)), float(box.get("y1", 0))),
                          confidence=str(row.get("confidence") or "")))
    made.sort(key=lambda block: (block.page, round(block.box[1], 4), block.box[0]))
    return made


def _size(block: Block, paper: Paper) -> float:
    """The type's size in points, from how tall the block OCR found is.

    The one measurement a photograph gives up readily. How many lines share
    that height is worked out from how many letters the block holds against how
    wide it is — at a size that is itself the answer, so it is done twice: once
    at the size of ordinary text, then again at the size that came out.
    """
    tall = max((block.box[3] - block.box[1]) * paper.height, 1.0)
    across = max((block.box[2] - block.box[0]) * paper.width, 6.0)
    size = 11.0
    for _ in range(2):
        lines = max(round(len(block.text) / max(across / (0.5 * size), 4)), 1)
        size = min(max(tall / ((lines - 1) * PITCH + INK), 6.0), 30.0)
    return round(size * 2) / 2


def _align(block: Block, column: tuple[float, float]) -> str:
    left, right = column
    width = max(right - left, 0.01)
    start, end = block.box[0] - left, right - block.box[2]
    if start > 0.07 * width and abs(start - end) < 0.06 * width:
        return "center"
    if start > 0.2 * width and end < 0.03 * width:
        return "right"
    return ""


def markdown(rows: list[dict], *, title: str, provider: str, paper: Paper,
             family: str = "Times New Roman") -> str:
    """The OCR reading as a markdown file that records how the page looked."""
    blocks = _blocks(rows)
    if not blocks:
        raise ValueError("OCR read nothing on this document, so there is nothing to rebuild.")
    lefts = sorted(b.box[0] for b in blocks if b.kind not in FURNITURE)
    rights = sorted(b.box[2] for b in blocks if b.kind not in FURNITURE)
    column = (lefts[len(lefts) // 20] if lefts else 0.1, rights[-max(len(rights) // 20, 1)] if rights else 0.9)
    body = _common([_size(b, paper) for b in blocks if b.kind in ("Text", "List Item")])

    out = [
        "---",
        f"document: {title}",
        f"read_by: {provider} (OCR — these words are a machine's reading of a photograph, not the",
        "  contract's own text; check them against the scan before relying on them)",
        f"pages: {max(b.page for b in blocks)}",
        (f"paper: {round(paper.width)}x{round(paper.height)}pt "
         f"margins {round(paper.left)},{round(paper.top)},{round(paper.right)},{round(paper.bottom)}"),
        f"font: {family} {body}pt  # assumed: a photograph of a page does not carry its fonts",
        "sizes: measured from the height of each block's printed lines",
        "---",
        "",
    ]
    said_before: set[str] = set()
    below: dict[int, float] = {}
    for block in blocks:
        if block.kind in FURNITURE:
            # Printed on every page, and Word's header and footer hold one each.
            flat = re.sub(r"[^a-z]", "", block.text.lower())
            if flat in said_before:
                continue
            said_before.add(flat)
        size = _size(block, paper)
        if not HEADINGS.get(block.kind) and size > 1.6 * body:
            # A tall block of "text" is a signature or a note written by hand,
            # not type at three times the size of the contract.
            size = body
        if block.kind in ("Text", "List Item") and abs(size - body) <= 0.25 * body:
            # A contract sets its body in one size. Measuring each block apart
            # makes the long ones a point or two out, for no reason a reader
            # would see; only a block that really is another size keeps its own.
            size = body
        where = FURNITURE.get(block.kind, "")
        marks = [f"page={block.page}", f"size={size}", f"at={block.box[0]:.3f},{block.box[1]:.3f}"]
        if where:
            marks.append(where)
        if block.kind == "Figure":
            marks.append("picture")
        align = _align(block, column)
        if align:
            marks.append(align)
        if block.confidence and block.confidence != "high":
            marks.append(f"confidence={block.confidence}")
        indent = round((block.box[0] - column[0]) * (paper.width - paper.left - paper.right))
        if indent > 6 and not align:
            marks.append(f"indent={indent}")
        gap = (block.box[1] - below.get(block.page, block.box[1])) * paper.height
        below[block.page] = max(below.get(block.page, 0.0), block.box[3])
        if not where and gap > 1:
            marks.append(f"space={round(min(gap, 18.0), 1)}")
        out.append(f"<!-- {' '.join(marks)} -->")
        level = HEADINGS.get(block.kind)
        if level:
            out.append("#" * level + " " + block.text)
        elif block.kind == "List Item":
            out.append(block.text if block.text.startswith("- ") else f"- {block.text}")
        elif block.kind == "Figure":
            out.append(f"![{block.text}](page-{block.page}-{round(block.box[1] * 1000)}.png)")
        else:
            out.append(block.text)
        out.append("")
    return "\n".join(out)


def _common(sizes: list[float]) -> float:
    from collections import Counter

    return Counter(sizes).most_common(1)[0][0] if sizes else 11.0


MARKS = re.compile(r"<!--\s*(.*?)\s*-->")


def document(text: str, *, pictures: dict[str, bytes] | None = None) -> Doc:
    """The markdown file read back as the document to write out as Word.

    Whatever the file says is what is built: correct a word, a size or the font
    in the front matter and the Word document changes with it.
    """
    head, _, rest = text.partition("---\n")[2].partition("\n---")
    settings = dict(re.findall(r"^(\w+):\s*(.+)$", head, re.MULTILINE))
    family, body = _font(settings.get("font", "Times New Roman 11"))
    doc = Doc(paper=_paper(settings.get("paper", "")))
    for marks, said in _pieces(rest):
        kind = marks.get("kind", "")
        size = float(marks.get("size", body))
        para = Para(
            runs=[Run(text=said, font=family, size=size, bold=kind.startswith("#"))],
            align=marks.get("align", ""),
            left=float(marks.get("indent", 0)),
            before=float(marks.get("space", 6.0 if kind.startswith("#") else 0.0)),
            pitch=round(size * PITCH, 1),
        )
        where = doc.header if "header" in marks else doc.footer if "footer" in marks else doc.body
        name = marks.get("picture", "")
        if name and pictures and name in pictures:
            where.append(Picture(content=pictures[name],
                                 width=float(marks.get("wide", 180.0)), height=60.0))
            continue
        if name:
            continue      # a picture nobody kept: its description is not the contract's text
        where.append(para)
    if not doc.body:
        raise ValueError("The markdown file has no text to build a document from.")
    return doc


def _font(said: str) -> tuple[str, float]:
    match = re.match(r"(.+?)\s+([\d.]+)pt", said.split("#")[0].strip())
    return (match.group(1).strip(), float(match.group(2))) if match else ("Times New Roman", 11.0)


def _paper(said: str) -> Paper:
    size = re.search(r"([\d.]+)x([\d.]+)pt", said)
    margins = re.search(r"margins\s*([\d.]+),([\d.]+),([\d.]+),([\d.]+)", said)
    paper = Paper()
    if size:
        paper.width, paper.height = float(size.group(1)), float(size.group(2))
    if margins:
        paper.left, paper.top, paper.right, paper.bottom = (float(margins.group(i)) for i in (1, 2, 3, 4))
    return paper


def _pieces(text: str):
    """Each block of the file: what the comment above it says, and its words."""
    marks: dict[str, str] = {}
    for line in text.split("\n"):
        found = MARKS.match(line.strip())
        if found:
            marks = {}
            for mark in found.group(1).split():
                key, _, value = mark.partition("=")
                marks[key] = value or key
            for name in ("center", "right"):
                if name in marks:
                    marks["align"] = name
            continue
        said = line.strip()
        if not said:
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", said)
        picture = re.match(r"^!\[(.*)\]\((.*)\)$", said)
        if heading:
            marks["kind"] = heading.group(1)
            said = heading.group(2)
        elif picture:
            marks["picture"] = picture.group(2) or "picture"
            marks["wide"] = marks.get("wide", "180")
            said = picture.group(1)
        elif said.startswith("- "):
            said = said[2:]
        yield dict(marks), said
        marks = {}


# --- the whole way, from the stored reading to the Word document -------------------------


def rebuild(db, version, content: bytes) -> tuple[bytes, str, dict]:
    """A scan as a Word document, the markdown it was built from, and how it
    was made. The markdown is kept and handed out beside the Word file: it is
    the record of what the machine read, and correcting it rebuilds the
    document."""
    from sqlalchemy import select

    from .models import DsOcrResult
    from .toword_build import build

    _, _, provider = (version.parser_name or "").partition("+ocr:")
    row = db.scalar(
        select(DsOcrResult)
        .where(DsOcrResult.sha256 == version.sha256, DsOcrResult.provider == provider)
        .order_by(DsOcrResult.created_at.desc())
        .limit(1)
    ) if provider else None
    if row is None or not (row.blocks or row.text):
        raise ValueError("This scan has not been read yet, so there is nothing to rebuild from.")
    if not row.blocks:
        raise ValueError("The reading of this scan has no places on the page, only words, so how it "
                         "looked cannot be rebuilt. It can still be read in the Original tab.")
    paper, pictures = _paper_and_pictures(content, row.blocks)
    said = markdown(row.blocks, title=version.filename or "document", provider=row.provider, paper=paper)
    made = build(document(said, pictures=pictures))
    return made, said, {"read_by": row.provider, "blocks": len(row.blocks), "pictures": len(pictures),
                        "ocr": True}


def _paper_and_pictures(content: bytes, rows: list[dict]) -> tuple[Paper, dict[str, bytes]]:
    """The scan's paper size, and each picture on it cut out of the page.

    A signature, a stamp and a logo are the parts of a scan that OCR can only
    describe ("blue circular stamp"); cut from the page they are themselves.
    """
    import fitz

    pictures: dict[str, bytes] = {}
    with fitz.open(stream=content, filetype="pdf") as pdf:
        if not pdf.page_count:
            return Paper(), pictures
        paper = Paper(width=pdf[0].rect.width, height=pdf[0].rect.height)
        for row in rows:
            if row.get("type") != "Figure":
                continue
            box, number = row.get("bbox") or {}, int(row.get("page") or 1)
            if not box or number > pdf.page_count:
                continue
            page = pdf[number - 1]
            rect = fitz.Rect(box.get("x0", 0) * page.rect.width, box.get("y0", 0) * page.rect.height,
                             box.get("x1", 1) * page.rect.width, box.get("y1", 1) * page.rect.height)
            if rect.is_empty or rect.width < 4 or rect.height < 4:
                continue
            name = f"page-{number}-{round(box.get('y0', 0) * 1000)}.png"
            try:
                pictures[name] = page.get_pixmap(clip=rect, dpi=200).tobytes("png")
            except Exception:   # a page the renderer will not draw
                continue
    return paper, pictures
