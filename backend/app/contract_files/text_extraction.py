import re
from dataclasses import dataclass
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from docx.oxml.ns import qn

from app.core.config import settings
from app.documents.reader.parsing.docx import _paragraph_text


@dataclass(frozen=True)
class TextExtractionResult:
    text: str
    method: str
    quality_score: float
    page_map: dict | None = None
    needs_ocr: bool = False
    metadata: dict | None = None


_MAX_DOCX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
_MAX_DOCX_ZIP_RATIO = 100


def _guard_docx_zip(content: bytes) -> None:
    try:
        with ZipFile(BytesIO(content)) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            uncompressed = sum(info.file_size for info in infos)
            compressed = sum(max(info.compress_size, 1) for info in infos)
    except BadZipFile as exc:
        raise ValueError("Invalid DOCX zip container") from exc
    if uncompressed > _MAX_DOCX_UNCOMPRESSED_BYTES:
        raise ValueError("DOCX expands beyond the safe extraction limit")
    if compressed and uncompressed / compressed > _MAX_DOCX_ZIP_RATIO:
        raise ValueError("DOCX compression ratio exceeds the safe extraction limit")


_ROMAN = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
          (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]


def _roman(n: int) -> str:
    out = ""
    for value, sign in _ROMAN:
        while n >= value:
            out += sign
            n -= value
    return out


def _letter(n: int) -> str:
    # Word's scheme: a..z then aa, bb, cc — the letter repeats, it does not
    # carry like a base-26 number.
    return chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)


def _format_counter(n: int, fmt: str) -> str:
    if fmt == "lowerLetter":
        return _letter(n)
    if fmt == "upperLetter":
        return _letter(n).upper()
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    if fmt == "decimalZero":
        return f"{n:02d}"
    return str(n)


class _DocxNumbering:
    """Reconstructs the clause numbers Word never stores in the text.

    A numbered paragraph holds only "item of list N at level L"; the visible
    "2.1" is counted by Word while it lays out the page, so `paragraph.text`
    for `2.1 Confidential Information...` is just `Confidential
    Information...`. That loss is not cosmetic: `split_blocks` finds clause
    boundaries by the number at the start of a line, so a contract whose
    numbering is automatic arrives as ONE block — no clause is separable, and
    nothing downstream can cite a section by its number.

    Counting is stateful (a level increments, deeper levels restart), which is
    why no library hands it over and why this walks the document in order.
    """

    def __init__(self, document):
        self._levels: dict[str, dict[int, dict]] = {}
        self._abstract_of: dict[str, str] = {}
        self._overrides: dict[str, dict[int, int]] = {}
        self._counters: dict[str, dict[int, int]] = {}
        self._style_cache: dict[str, tuple[str, int] | None] = {}
        try:
            root = document.part.numbering_part.element
        except Exception:
            # No numbering part at all — nothing in the document is numbered.
            return
        for abstract in root.findall(qn("w:abstractNum")):
            abstract_id = abstract.get(qn("w:abstractNumId"))
            levels: dict[int, dict] = {}
            for lvl in abstract.findall(qn("w:lvl")):
                ilvl = int(lvl.get(qn("w:ilvl")) or 0)
                levels[ilvl] = {
                    "fmt": _child_val(lvl, "w:numFmt") or "decimal",
                    "text": _child_val(lvl, "w:lvlText") or "",
                    "start": int(_child_val(lvl, "w:start") or 1),
                    # isLgl ("legal numbering") renders every parent level as an
                    # arabic numeral whatever its own format — common in
                    # contracts that mix "Article IV" with "4.1".
                    "is_lgl": lvl.find(qn("w:isLgl")) is not None,
                }
            if abstract_id is not None:
                self._levels[abstract_id] = levels
        for num in root.findall(qn("w:num")):
            num_id = num.get(qn("w:numId"))
            abstract = num.find(qn("w:abstractNumId"))
            if num_id is None or abstract is None:
                continue
            self._abstract_of[num_id] = abstract.get(qn("w:val"))
            for override in num.findall(qn("w:lvlOverride")):
                start = override.find(qn("w:startOverride"))
                if start is not None:
                    ilvl = int(override.get(qn("w:ilvl")) or 0)
                    self._overrides.setdefault(num_id, {})[ilvl] = int(start.get(qn("w:val")) or 1)

    def prefix(self, paragraph) -> str:
        """The number Word would draw for this paragraph, or "" — and it must
        be called once per paragraph in document order, because it counts."""
        if not self._levels:
            return ""
        found = _num_pr(paragraph._p) or self._from_style(paragraph)
        if found is None:
            return ""
        num_id, ilvl = found
        # numId 0 is Word's explicit "this paragraph is not numbered", used to
        # lift numbering a style applied.
        if num_id == "0":
            return ""
        abstract_id = self._abstract_of.get(num_id)
        levels = self._levels.get(abstract_id or "")
        if not levels:
            return ""
        level = levels.get(ilvl)
        if level is None or level["fmt"] in {"bullet", "none"}:
            # ponytail: bullets are dropped, not rendered as "•". They carry no
            # clause identity and the marker would land inside every citation.
            # Render them the day a playbook needs to cite a bulleted item.
            return ""
        # Counters key on the abstract definition, not numId, so the several
        # numIds Word emits for one visual list share a sequence.
        counters = self._counters.setdefault(abstract_id, {})
        overrides = self._overrides.get(num_id, {})
        start = overrides.get(ilvl, level["start"])
        counters[ilvl] = counters.get(ilvl, start - 1) + 1
        for deeper in [k for k in counters if k > ilvl]:
            del counters[deeper]
        label = level["text"]
        for position in range(ilvl + 1):
            parent = levels.get(position) or level
            fmt = "decimal" if (level["is_lgl"] and position < ilvl) else parent["fmt"]
            value = counters.get(position, parent["start"])
            label = label.replace(f"%{position + 1}", _format_counter(value, fmt))
        return label.strip()

    def _from_style(self, paragraph) -> tuple[str, int] | None:
        """Numbering frequently lives on the paragraph's style rather than the
        paragraph — Word's own "List Number" is defined that way — and a style
        can inherit it from the one it is based on."""
        style = paragraph.style
        key = getattr(style, "style_id", None)
        if key in self._style_cache:
            return self._style_cache[key]
        found = None
        seen = 0
        while style is not None and seen < 20:  # cycle guard on basedOn
            found = _num_pr(getattr(style, "element", None))
            if found is not None:
                break
            style = style.base_style
            seen += 1
        if key is not None:
            self._style_cache[key] = found
        return found


def _child_val(element, tag: str) -> str | None:
    child = element.find(qn(tag))
    return None if child is None else child.get(qn("w:val"))


def _num_pr(element) -> tuple[str, int] | None:
    """(numId, ilvl) from a paragraph or style element's pPr, if it has one."""
    if element is None:
        return None
    p_pr = element.find(qn("w:pPr"))
    num_pr = None if p_pr is None else p_pr.find(qn("w:numPr"))
    if num_pr is None:
        return None
    num_id = _child_val(num_pr, "w:numId")
    if num_id is None:
        return None
    return num_id, int(_child_val(num_pr, "w:ilvl") or 0)


def _iter_body_items(parent_elm, parent):
    """Yield a container's paragraphs and tables in document order.

    ``Document.paragraphs`` is top-level body paragraphs ONLY — it silently
    omits every table. In a contract that is the payment schedule, the fee
    table, the SLA tiers, the liability cap and usually the signature block,
    so the omission is invisible to the quality score and lands as a clause
    that exists nowhere in the system. python-docx keeps paragraphs and tables
    in separate collections with no ordering between them, so the only way to
    read the document as written is to walk the XML body itself.
    """
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in parent_elm.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            yield Table(child, parent)


def _render_table(table, numbering=None) -> str:
    """One row per line, cells tab-separated — what pasting the table into a
    text editor gives you. No markdown pipes: the text is embedded and quoted
    back as citations, so added syntax becomes noise in both."""
    lines: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        seen: set[int] = set()
        for cell in row.cells:
            # A merged cell is returned once per grid position it spans, as the
            # same underlying element. Without this, a header merged across
            # three columns is emitted three times.
            key = id(cell._tc)
            if key in seen:
                continue
            seen.add(key)
            # A cell holds paragraphs and can hold further tables; flatten its
            # own newlines so a nested table cannot break the row structure.
            inner = " ".join(_render_container(cell._tc, cell, numbering)).replace("\n", " ")
            cells.append(" ".join(inner.split()))
        line = "\t".join(cells)
        if line.strip():
            lines.append(line)
    return "\n".join(lines)


def _render_container(parent_elm, parent, numbering=None) -> list[str]:
    parts: list[str] = []
    for item in _iter_body_items(parent_elm, parent):
        if hasattr(item, "rows"):  # Table
            rendered = _render_table(item, numbering)
            if rendered:
                parts.append(rendered)
        else:  # Paragraph
            # Empty paragraphs are kept deliberately: they are what puts blank
            # lines in the text, and blank lines are what `split_blocks` splits
            # clauses on. Dropping them would merge the whole document into one
            # block.
            prefix = numbering.prefix(item) if numbering is not None else ""
            # Not `item.text`: it reads only runs sitting directly in the
            # paragraph, so a counterparty's tracked insertion (and hyperlink
            # text) vanished — "capped at <ins>twelve months</ins> of fees" came
            # out as "capped at  of fees". The Documents reader keeps insertions
            # and drops deletions: the text as they propose it.
            text = _paragraph_text(item)
            # A space, not a tab: `_CLAUSE_START` accepts either, and the number
            # is quoted back inside citations, where a tab reads as damage.
            parts.append(f"{prefix} {text}" if prefix else text)
    return parts


def extract_docx_text(document) -> str:
    """Body text in document order, tables included.

    Automatic clause numbers are reconstructed and prepended, because Word
    keeps them out of the text entirely (see ``_DocxNumbering``).

    This changes the text — and therefore every character offset — relative to
    what an older extractor produced. That is safe going forward because a
    snapshot's offsets are computed from the same text in the same pass, but it
    is why re-extracting an EXISTING snapshot in place would invalidate its
    stored citations. Re-extraction needs a new snapshot, not an overwrite.
    """
    return "\n".join(
        _render_container(document.element.body, document, _DocxNumbering(document))
    )


# A clause number alone on its line, with the clause it belongs to on the next.
# PDFs indent the number with a tab stop, which puts it in its own text block,
# so "1." and "IDENTIFICATION OF PARTIES" arrive as two lines.
_NUMBER_ONLY_LINE = re.compile(
    # The next line must begin with a letter — a clause heading always does.
    # Anything else (another number, a signature rule) is a separate block, and
    # joining it would put two clause markers on one line where the splitter
    # can only see the first.
    r"(?m)^([ \t]*(?:\d+(?:\.\d+)*[.)]?|\((?:[a-z]|[ivxlcdm]+|\d+)\)))[ \t]*\n[ \t]*(?=[A-Za-z])"
)


def _join_number_lines(text: str) -> str:
    """Re-join a clause number to its heading. Cosmetic in the viewer, but
    `split_blocks` and the element builder read the number as the clause's
    label, and a label separated from its text reads as an empty clause."""
    return _NUMBER_ONLY_LINE.sub(r"\1 ", text)



def extract_text(content: bytes, *, mime_type: str, filename: str) -> TextExtractionResult:
    max_bytes = settings.pdf_max_extracted_text_bytes
    if mime_type.startswith("text/"):
        text = content.decode("utf-8", errors="replace")
        text, truncated = _cap_text(text, max_bytes)
        return _with_quality(text, "plain_text", metadata={"truncated": truncated} if truncated else None)

    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            _guard_docx_zip(content)
            from docx import Document

            document = Document(BytesIO(content))
            text = extract_docx_text(document)
            text, truncated = _cap_text(text, max_bytes)
            return _with_quality(text, "docx", metadata={"truncated": truncated} if truncated else None)
        except Exception as exc:
            return TextExtractionResult("", "docx_failed", 0.0, needs_ocr=True, metadata={"error": str(exc)})

    if mime_type == "application/pdf":
        try:
            # PyMuPDF, not pypdf. pypdf reconstructs word spacing from each
            # glyph's position, and on a PDF that kerns its text it inserts
            # spaces inside words: this corpus's "LEGAL S ERVICES AGRE EM ENT"
            # and "requi res lawyers to have w ith thei r clients". Measured on
            # that file, 20.2% of pypdf's words were fragments against 0.9%
            # here. Nothing downstream can recover a word split in three — not
            # search, not the clause splitter, not a citation quoted to a
            # lawyer. PyMuPDF was already a dependency (trademarks/vision.py).
            import fitz  # PyMuPDF

            pages: list[str] = []
            page_map: dict[str, dict[str, int]] = {}
            offset = 0
            total_bytes = 0
            truncated = False
            with fitz.open(stream=content, filetype="pdf") as document:
                for page_number, page in enumerate(document, start=1):
                    page_text = _join_number_lines(page.get_text() or "")
                    # Hard cap per contract so a single PDF cannot OOM the worker.
                    if total_bytes + len(page_text.encode("utf-8")) > max_bytes:
                        remaining = max(0, max_bytes - total_bytes)
                        page_text = page_text.encode("utf-8")[:remaining].decode("utf-8", errors="ignore")
                        pages.append(page_text)
                        page_map[str(page_number)] = {"start": offset, "end": offset + len(page_text)}
                        truncated = True
                        break
                    pages.append(page_text)
                    page_map[str(page_number)] = {"start": offset, "end": offset + len(page_text)}
                    offset += len(page_text) + 1
                    total_bytes += len(page_text.encode("utf-8"))
            text = "\n".join(pages)
            result = _with_quality(
                text,
                "pdf_text",
                page_map=page_map,
                metadata={"truncated": truncated} if truncated else None,
            )
            return result
        except Exception as exc:
            return TextExtractionResult("", "pdf_failed", 0.0, needs_ocr=True, metadata={"error": str(exc)})

    if mime_type.startswith("image/"):
        return TextExtractionResult("", "image_requires_ocr", 0.0, needs_ocr=True)

    return TextExtractionResult("", "unsupported", 0.0, needs_ocr=True, metadata={"filename": filename})


def _cap_text(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _with_quality(
    text: str, method: str, page_map: dict | None = None, metadata: dict | None = None
) -> TextExtractionResult:
    quality = score_extraction_quality(text)
    return TextExtractionResult(
        text=text,
        method=method,
        quality_score=quality,
        page_map=page_map,
        needs_ocr=quality < 0.55,
        metadata=metadata,
    )


def score_extraction_quality(text: str) -> float:
    stripped = text.strip()
    if len(stripped) < 80:
        return 0.0
    printable = sum(1 for char in stripped if char.isprintable() or char.isspace())
    alphabetic = sum(1 for char in stripped if char.isalpha())
    unreadable = stripped.count("\ufffd")
    printable_ratio = printable / max(len(stripped), 1)
    alpha_ratio = alphabetic / max(len(stripped), 1)
    unreadable_penalty = min(unreadable / max(len(stripped), 1), 0.5)
    return max(0.0, min(1.0, printable_ratio * 0.55 + alpha_ratio * 0.45 - unreadable_penalty))
