"""Fill-in fields inside a Word file: find them, mark them, fill them.

A field is the visible plain text ``{name}`` — the notation the text templates already use,
with ``{{`` / ``}}`` for a literal brace. It survives Word, LibreOffice and Google Docs, and a
person can read it in the file. The work is done on the .docx itself (zip + lxml) rather than
through python-docx's ``save()``, which re-zips with fresh timestamps and so makes an
identical upload look new (FR-37).

Three traps shape the module, and each fails silently:

* **Word splits text into runs anywhere** (spell-check, revision ids, a bold first letter), so
  ``{counterparty}`` is often ``{`` + ``counter`` + ``party}`` in three runs. Everything here
  reads and rewrites a whole *paragraph* at a time, through an offset map back to its ``w:t``
  nodes — never one run at a time.
* **A rewrite that loses formatting or parts corrupts the contract.** Only the text of the
  nodes that hold a field changes, and every other zip member is copied byte for byte.
* **A .docx is an attacker-controlled zip full of XML.** The zip-bomb limits of the Documents
  reader are reused and the parser never resolves entities (a DOCTYPE is refused outright).

Problems come back two ways. ``field_uses`` *returns* them, because a person has to see all of
them at once. Every other function raises ``HTTPException(422, message)`` the way
``service.check_body`` does.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import string
import zipfile
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property
from io import BytesIO
from itertools import pairwise

from fastapi import HTTPException, status
from lxml import etree

from app.documents.reader.parsing.base import UnsupportedFormat
from app.documents.reader.parsing.docx import _guard_zip

# Where a field can be filled. Anything else (text box, footnote, endnote) is reported, never
# silently left as a leftover brace in a draft.
FILLABLE_PLACES = frozenset({"body", "table", "header", "footer"})

_DAMAGED = "This file is damaged and can't be read."
_UNSAFE = "This Word file can't be opened safely: it expands far beyond its size."
_TRACKED = (
    "This Word file has tracked changes. Accept or reject every change "
    "(Review → Accept/Reject All), save, and upload it again."
)
_COMMENTS = (
    "This Word file has comments. Delete them (Review → Delete All Comments), "
    "save, and upload it again."
)
_ACROSS = "A field can only mark text within one paragraph, not across a tab or line break."

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_PKG_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _w(name: str) -> str:
    return f"{{{_W}}}{name}"


_P, _R, _T, _TC, _PPR, _RPR = (_w(n) for n in ("p", "r", "t", "tc", "pPr", "rPr"))
_TXBX = _w("txbxContent")
# Characters a run holds that have no w:t. They show in the paragraph text (so offsets line
# up with what a person sees) but a mark or a code may not span one.
_VIRTUAL = {
    _w("tab"): "\t", _w("ptab"): "\t", _w("br"): "\n", _w("cr"): "\n",
    _w("noBreakHyphen"): "‑", _w("softHyphen"): "­",
}
_REVISIONS = tuple(
    _w(n) for n in (
        "ins", "del", "moveFrom", "moveTo", "rPrChange", "pPrChange", "sectPrChange",
        "tblPrChange", "trPrChange", "tcPrChange", "numberingChange", "cellIns", "cellDel",
        "tblGridChange", "tblPrExChange", "cellMerge",
    )
)
_COMMENT_MARKS = tuple(_w(n) for n in ("commentRangeStart", "commentRangeEnd", "commentReference"))
# A paragraph holding any of these is not "empty" even when it has no text.
_KEEP = tuple(
    _w(n) for n in (
        "drawing", "pict", "object", "fldChar", "fldSimple", "instrText", "br", "cr", "tab",
        "ptab", "sym", "sectPr", "bookmarkStart", "bookmarkEnd", "footnoteReference",
        "endnoteReference",
    )
)
_PART_KINDS = {"header": "header", "footer": "footer", "footnotes": "footnote", "endnotes": "endnote"}
_WHERE = {"body": "in the body", "table": "in a table", "header": "in the header",
          "footer": "in the footer"}
_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")
_LINE_BREAKS = re.compile(r"[\r\n\t]+")


@dataclass(frozen=True)
class ParagraphInfo:
    """One paragraph as the page shows it. ``id`` is what a mark's ``paragraph`` refers to."""

    id: int
    place: str
    fillable: bool
    text: str


@dataclass(frozen=True)
class Mark:
    """A passage to turn into a field (``field``) or keep as ordinary text (``literal``).

    ``start``/``end`` are character offsets (Python code points) into the paragraph's text.
    ``apply_marks`` reads attributes, so a pydantic model with the same names works too."""

    paragraph: int
    start: int
    end: int
    field: str | None = None
    literal: bool = False


@dataclass(frozen=True)
class FieldUse:
    """A field used in the file, once per (name, place)."""

    name: str
    known: bool
    label: str | None
    place: str
    count: int


@dataclass(frozen=True)
class Problem:
    """Something that stops the wording from being filled. Shape of TemplateWordingProblem."""

    kind: str  # unknown_field | unpaired_brace | unfillable_place
    paragraph: int
    start: int
    end: int
    text: str
    place: str
    message: str


def _damaged() -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _DAMAGED)


def _refuse(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)


# --- the zip and its XML -------------------------------------------------------------------


def _open(docx: bytes) -> zipfile.ZipFile:
    """The package as a zip, after the Documents reader's zip-bomb limits."""
    try:
        archive = zipfile.ZipFile(BytesIO(docx))
    except (zipfile.BadZipFile, EOFError, OSError, ValueError) as exc:
        raise _damaged() from exc
    try:
        _guard_zip(docx)
    except UnsupportedFormat as exc:
        archive.close()
        raise _refuse(_UNSAFE) from exc
    return archive


def _read(archive: zipfile.ZipFile, member: str | zipfile.ZipInfo) -> bytes:
    try:
        return archive.read(member)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise _damaged() from exc  # RuntimeError is zipfile's "password required"


def _parser() -> etree.XMLParser:
    """Entities are never resolved and nothing is fetched (XXE)."""
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, load_dtd=False)


def _parse(data: bytes) -> etree._Element:
    # A DOCTYPE is refused as well: Word never writes one, and an entity left unresolved would
    # be written back out as a dangling reference once the DOCTYPE is gone from the
    # serialised part.
    try:
        root = etree.fromstring(data, _parser())
    except etree.XMLSyntaxError as exc:
        raise _damaged() from exc
    if root.getroottree().docinfo.doctype:
        raise _damaged()
    return root


def _relationships(archive: zipfile.ZipFile, source: str) -> list[tuple[str, str]]:
    """(kind, part name) for each internal relationship of ``source`` ("" is the package)."""
    folder, base = posixpath.split(source)
    try:
        data = archive.read(posixpath.join(folder, "_rels", base + ".rels"))
    except KeyError:
        return []
    found = []
    for rel in _parse(data).iter(f"{{{_PKG_RELS}}}Relationship"):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target") or ""
        name = (
            target.lstrip("/") if target.startswith("/")
            else posixpath.normpath(posixpath.join(folder, target))
        )
        found.append(((rel.get("Type") or "").rsplit("/", 1)[-1], name))
    return found


def _rezip(docx: bytes, changed: dict[str, bytes]) -> bytes:
    """The same zip with only ``changed`` members swapped: names, order, dates and
    compression of every member are kept, and every other member's bytes are copied as is."""
    if not changed:
        return docx
    out = BytesIO()
    with _open(docx) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            data = changed.get(info.filename)
            if data is None:
                data = _read(source, info)
            clone = zipfile.ZipInfo(info.filename)
            clone.date_time = info.date_time
            clone.compress_type = info.compress_type
            clone.external_attr = info.external_attr
            clone.create_system = info.create_system
            clone.comment = info.comment
            target.writestr(clone, data)
    return out.getvalue()


def content_hash(docx: bytes) -> str:
    """sha256 over the sorted (member name, member bytes). Zip timestamps, order and
    compression don't change it, so re-saving identical content never looks like a change
    (FR-37)."""
    digest = hashlib.sha256()
    with _open(docx) as archive:
        for info in sorted((i for i in archive.infolist() if not i.is_dir()),
                           key=lambda i: i.filename):
            name = info.filename.encode("utf-8")
            data = _read(archive, info)
            digest.update(len(name).to_bytes(4, "big") + name)
            digest.update(len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


# --- paragraphs ----------------------------------------------------------------------------


@dataclass
class _Para:
    id: int
    part: str
    place: str
    el: etree._Element
    text: str
    # [w:t node, start, end] in text order. Mutable: _rewrite keeps the starts valid.
    nodes: list[list]

    @property
    def fillable(self) -> bool:
        return self.place in FILLABLE_PLACES


def _read_paragraph(p: etree._Element) -> tuple[str, list[list]]:
    """The text of one w:p and the offset map back to its w:t nodes.

    A nested w:p (a text box inside a run) is its own paragraph, so it is skipped here."""
    pieces: list[str] = []
    nodes: list[list] = []
    pos = 0

    def walk(parent: etree._Element) -> None:
        nonlocal pos
        for child in parent:
            tag = child.tag
            if tag == _T:
                text = child.text or ""
                nodes.append([child, pos, pos + len(text)])
                pieces.append(text)
                pos += len(text)
            elif tag in _VIRTUAL and parent.tag == _R:
                pieces.append(_VIRTUAL[tag])
                pos += 1
            elif tag not in (_P, _PPR, _RPR):  # properties hold no text
                walk(child)

    walk(p)
    return "".join(pieces), nodes


def _place(p: etree._Element, part_kind: str) -> str:
    in_cell = False
    for ancestor in p.iterancestors():
        if ancestor.tag == _TXBX:
            return "text_box"
        in_cell = in_cell or ancestor.tag == _TC
    if part_kind != "body":
        return part_kind
    return "table" if in_cell else "body"


class _Package:
    """A .docx opened for reading and editing in place. ``dump`` writes the result."""

    def __init__(self, docx: bytes) -> None:
        self.docx = docx
        self.dirty: set[str] = set()
        # part name -> (kind, root), in paragraph order
        self.parts: dict[str, tuple[str, etree._Element]] = {}
        self.comment_roots: list[etree._Element] = []
        with _open(docx) as archive:
            names = {i.filename for i in archive.infolist()}
            main = next(
                (n for k, n in _relationships(archive, "") if k == "officeDocument" and n in names),
                "word/document.xml" if "word/document.xml" in names else None,
            )
            if main is None:
                raise _damaged()
            by_kind: dict[str, list[str]] = {k: [] for k in (*_PART_KINDS, "comments")}
            for kind, name in _relationships(archive, main):
                if kind in by_kind and name in names and name not in by_kind[kind]:
                    by_kind[kind].append(name)
            body = _parse(_read(archive, main))
            if body.tag != _w("document"):  # a zip, but not a Word file (e.g. a renamed .xlsx)
                raise _damaged()
            self.parts[main] = ("body", body)
            # header1, header2, header10: shortest name first is the natural order
            for rel_kind, place in _PART_KINDS.items():
                for name in sorted(by_kind[rel_kind], key=lambda n: (len(n), n)):
                    self.parts[name] = (place, _parse(_read(archive, name)))
            self.comment_roots = [_parse(_read(archive, n)) for n in by_kind["comments"]]

    @cached_property
    def paras(self) -> list[_Para]:
        found: list[_Para] = []
        for name, (kind, root) in self.parts.items():
            for p in root.iter(_P):
                text, nodes = _read_paragraph(p)
                found.append(_Para(len(found), name, _place(p, kind), p, text, nodes))
        return found

    def dump(self) -> bytes:
        changed = {
            name: etree.tostring(
                self.parts[name][1], xml_declaration=True, encoding="UTF-8", standalone=True
            )
            for name in self.dirty
        }
        return _rezip(self.docx, changed)


def paragraphs(docx: bytes) -> list[ParagraphInfo]:
    """Every paragraph, in the order marks refer to it: the body (tables, content controls and
    text boxes in document order), then headers, footers, footnotes and endnotes."""
    return [ParagraphInfo(p.id, p.place, p.fillable, p.text) for p in _Package(docx).paras]


def _within_runs(para: _Para, start: int, end: int) -> bool:
    """True when [start, end) is covered by w:t text with no tab or line break inside it."""
    pos = start
    for _, node_start, node_end in para.nodes:
        if node_end <= pos:
            continue
        if node_start > pos:
            return False  # a virtual character sits in the gap
        pos = node_end
        if pos >= end:
            return True
    return False


def _rewrite(para: _Para, spans: Iterable[tuple[int, int, str]]) -> None:
    """Replace each (start, end, replacement) of the paragraph's text.

    The one primitive used to mark and to fill. Spans go right to left so earlier offsets stay
    valid. The replacement goes into the w:t where the span starts, so it takes that run's
    formatting (FR-42); the rest of the covered text is cut from the following nodes, which
    are emptied, never removed."""
    for start, end, replacement in sorted(spans, reverse=True):
        started = False
        for node in para.nodes:
            t, node_start, node_end = node
            if node_end <= start:
                continue
            if node_start >= end:
                break
            text = t.text or ""
            lo, hi = max(start, node_start) - node_start, min(end, node_end) - node_start
            t.text = text[:lo] + ("" if started else replacement) + text[hi:]
            t.set(_XML_SPACE, "preserve")
            started = True


# --- field notation ------------------------------------------------------------------------


def _scan(text: str) -> list[tuple[str, int, int]]:
    """Where the braces are: ``{{`` and ``}}`` (open/close literals), a ``{...}`` group up to its
    matching brace (field), and a lone brace (bad). A group is a usable code only when what is
    between the braces is a bare known name (``_inspect``): ``{company!r}`` or ``{company:>10}``
    is an unknown field, as in ``service.check_body`` and the page."""
    found = []
    i, size = 0, len(text)
    while i < size:
        char = text[i]
        if char not in "{}":
            i += 1
        elif text[i + 1:i + 2] == char:
            found.append(("open" if char == "{" else "close", i, i + 2))
            i += 2
        elif char == "}":
            found.append(("bad", i, i + 1))
            i += 1
        else:
            depth, j = 1, i + 1
            while j < size and depth:
                depth += (text[j] == "{") - (text[j] == "}")
                j += 1
            if depth:
                found.append(("bad", i, i + 1))
                i += 1
            else:
                found.append(("field", i, j))
                i = j
    return found


def _known_fields() -> dict[str, str]:
    """Every name drafting fills, with its plain label. Read late: the service module's
    callers import this one."""
    from app.drafting_templates.service import PLACEHOLDERS

    return {p["name"]: p.get("label") or p["about"] for p in PLACEHOLDERS}


def _where(place: str) -> str:
    return _WHERE.get(place) or "in a " + place.replace("_", " ")


def _unknown_message(code: str, place: str) -> str:
    literal = code.replace("{", "{{").replace("}", "}}")
    return (
        f"Unknown placeholder {code} ({_where(place)}). Use one from the list, "
        f"or keep it as ordinary text by writing it as {literal}."
    )


def _unpaired_message(place: str) -> str:
    return f"The braces don't pair up ({_where(place)}). Write a literal brace as {{{{ or }}}}."


def _unfillable_message(place: str) -> str:
    name = place.replace("_", " ")
    return f"Text in {'an' if name[0] in 'aeiou' else 'a'} {name} can't be filled; keep it as ordinary text."


@dataclass(frozen=True)
class _Code:
    start: int
    end: int
    name: str
    text: str


@dataclass
class _Findings:
    codes: list[_Code]  # well-formed {...} groups in a place that can be filled
    literals: list[tuple[int, int, str]]  # ``{{`` / ``}}`` with the single brace they become
    problems: list[Problem]


def _inspect(para: _Para, known: Iterable[str]) -> _Findings:
    found = _Findings([], [], [])
    if "{" not in para.text and "}" not in para.text:
        return found
    known = set(known)

    def problem(kind: str, start: int, end: int, message: str) -> None:
        found.problems.append(
            Problem(kind, para.id, start, end, para.text[start:end], para.place, message)
        )

    for kind, start, end in _scan(para.text):
        raw = para.text[start:end]
        if kind in ("open", "close"):
            found.literals.append((start, end, raw[0]))
            continue
        name = raw[1:-1] if kind == "field" else None
        if name is None:
            problem("unpaired_brace", start, end, _unpaired_message(para.place))
        elif not para.fillable:
            problem("unfillable_place", start, end, _unfillable_message(para.place))
        else:
            found.codes.append(_Code(start, end, name, raw))
            # a tab or line break inside the braces can't be rewritten as one piece of text
            if name not in known or not _within_runs(para, start, end):
                problem("unknown_field", start, end, _unknown_message(raw, para.place))
    return found


def field_uses(docx: bytes) -> tuple[list[FieldUse], list[Problem]]:
    """The fields the file uses, and what stops it from being filled.

    Tokenised like ``service.check_body`` for the text path (a code is a bare ``{name}``) and
    limited to the names drafting fills. A code in a text box, footnote or endnote is a
    problem, not a use."""
    package = _Package(docx)
    known = _known_fields()
    counts: dict[tuple[str, str], int] = {}
    problems: list[Problem] = []
    for para in package.paras:
        found = _inspect(para, known)
        problems.extend(found.problems)
        for code in found.codes:
            counts[(code.name, para.place)] = counts.get((code.name, para.place), 0) + 1
    uses = [
        FieldUse(name, name in known, known.get(name), place, count)
        for (name, place), count in sorted(counts.items())
    ]
    return uses, problems


# --- marking -------------------------------------------------------------------------------


def apply_marks(docx: bytes, marks: Iterable[Mark]) -> bytes:
    """Turn marked passages into ``{field}``, or double their braces to keep them as ordinary
    text (``literal``). Every mark refers to the paragraph ids of ``paragraphs(docx)``.

    A mark outside its paragraph, across a tab or line break, naming a field drafting doesn't
    fill, putting a field where it can't be filled, or overlapping another mark is 422. A
    literal mark is allowed anywhere: it is how a person keeps a brace in a text box as text."""
    package = _Package(docx)
    paras = package.paras
    known = _known_fields()
    wanted: dict[int, dict[tuple[int, int], str]] = {}
    for mark in marks:
        n = mark.paragraph
        if not 0 <= n < len(paras):
            raise _refuse(f"That mark points outside the text of paragraph {n}.")
        para = paras[n]
        if not 0 <= mark.start < mark.end <= len(para.text):
            raise _refuse(f"That mark points outside the text of paragraph {n}.")
        if not _within_runs(para, mark.start, mark.end):
            raise _refuse(_ACROSS)
        if getattr(mark, "literal", False):
            replacement = para.text[mark.start:mark.end].replace("{", "{{").replace("}", "}}")
        else:
            name = getattr(mark, "field", None) or ""
            if name not in known:
                raise _refuse(_unknown_message("{" + name + "}", para.place))
            if not para.fillable:
                raise _refuse(_unfillable_message(para.place))
            replacement = "{" + name + "}"
        wanted.setdefault(n, {})[(mark.start, mark.end)] = replacement
    for n, spans in wanted.items():
        ordered = sorted(spans.items())
        for ((_, previous_end), _), ((start, _), _) in pairwise(ordered):
            if start < previous_end:
                raise _refuse(f"Two marks overlap in paragraph {n}.")
        _rewrite(paras[n], [(s, e, replacement) for (s, e), replacement in ordered])
        package.dirty.add(paras[n].part)
    return package.dump()


# --- filling -------------------------------------------------------------------------------


def _plain(value: object) -> str:
    """A value as it goes into a w:t: one line, and nothing XML can't hold."""
    return _XML_ILLEGAL.sub("", _LINE_BREAKS.sub(" ", str(value)))


def _widen(text: str, start: int, end: int) -> tuple[int, int]:
    """An empty value leaves a gap: take one of the two spaces beside it along with it, and a
    space that would be left before a closing punctuation mark or at the paragraph's edge."""
    left = text[start - 1] if start else None
    right = text[end] if end < len(text) else None
    if right == " " and left in (None, " "):
        return start, end + 1
    if left == " " and (right is None or right in ",.;:)"):
        return start - 1, end
    return start, end


def _merge(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    merged: list[tuple[int, int, str]] = []
    for start, end, replacement in sorted(spans):
        if merged and start < merged[-1][1]:
            previous = merged.pop()
            start, end, replacement = (
                previous[0], max(end, previous[1]), previous[2] + replacement
            )
        merged.append((start, end, replacement))
    return merged


def _is_empty(para: _Para) -> bool:
    if "".join(n[0].text or "" for n in para.nodes).strip():
        return False
    for el in para.el.iter(*_KEEP):
        # w:tab is also a tab-stop definition in the paragraph properties; only a run's counts
        if el.tag != _w("tab") or el.getparent().tag == _R:
            return False
    return True


def _removable(p: etree._Element) -> bool:
    """A cell, header or footer must still end in a paragraph once ``p`` is gone."""
    parent = p.getparent()
    if parent is None:
        return False
    if parent.tag in (_TC, _w("hdr"), _w("ftr")):
        rest = [c for c in parent if c is not p and c.tag != _w("tcPr")]
        return bool(rest) and rest[-1].tag == _P
    return True


def fill(docx: bytes, values: Mapping[str, str]) -> bytes:
    """The file with each ``{name}`` replaced by ``values[name]`` and ``{{``/``}}`` by braces.

    The value takes the formatting of the run where its field starts (FR-42), goes in as a text
    node (so quotes, braces and non-English text are safe) and loses its line breaks. An empty
    value takes one of the spaces around it, and a paragraph that held a code and is now blank
    is removed — unless it holds a drawing, a field, a page break or section properties, or
    is the last paragraph of its cell, header or footer.

    A code that can't be filled (an unknown name, an unpaired brace, a code in a text box)
    is 422 with the same words ``field_uses`` gives: a leftover code in a draft is worse than
    no draft. A file with nothing to change comes back as the same bytes.

    ponytail: only spaces and closing punctuation are tidied around an empty value; a
    dangling "and" or a comma pair is left for the template's author to word around. Add a
    rule to ``_widen`` if real templates need more."""
    package = _Package(docx)
    for para in package.paras:
        found = _inspect(para, values)
        if found.problems:
            raise _refuse(found.problems[0].message)
        spans = [(s, e, brace) for s, e, brace in found.literals]
        for code in found.codes:
            try:
                value = _plain(string.Formatter().vformat(code.text, (), values))
            except (KeyError, IndexError, AttributeError, ValueError, TypeError) as exc:
                raise _refuse(_unknown_message(code.text, para.place)) from exc
            start, end = (
                _widen(para.text, code.start, code.end) if not value else (code.start, code.end)
            )
            spans.append((start, end, value))
        if not spans:
            continue
        _rewrite(para, _merge(spans))
        package.dirty.add(para.part)
        if found.codes and _is_empty(para) and _removable(para.el):
            para.el.getparent().remove(para.el)
    return package.dump()


# --- tracked changes and comments ----------------------------------------------------------


def refuse_revisions_and_comments(docx: bytes) -> None:
    """422 when the file has tracked changes or comments (FR-29): the wording would be
    ambiguous (is a deleted sentence in or out?) and a comment is someone's private note."""
    package = _Package(docx)
    revisions = comments = False
    for _, root in package.parts.values():
        revisions = revisions or next(root.iter(*_REVISIONS), None) is not None
        comments = comments or next(root.iter(*_COMMENT_MARKS), None) is not None
    comments = comments or any(
        next(root.iter(_w("comment")), None) is not None for root in package.comment_roots
    )
    if revisions or comments:
        raise _refuse(" ".join(m for m, hit in ((_TRACKED, revisions), (_COMMENTS, comments)) if hit))
