"""Word tracked changes, written into the file itself and resolved there.

A suggested edit is a real `<w:del>`/`<w:ins>` pair in `word/document.xml`, so
the file a lawyer downloads is a redline Word opens natively: accept and reject
work there as they do here. Every change — a person's, the AI's, or one the
other side sent — is the same thing, listed and resolved the same way.

The approach is Mike's (`mike-main/backend/src/lib/docxTrackedChanges.ts`):
match against the *accepted view* of each paragraph (tracked insertions count
as text, tracked deletions do not), then splice revision tags around exactly
the runs that hold the words. Two deliberate differences:

* **A run is split by its own children, not rebuilt from its text**, so a tab
  or a line break inside an edited run survives. Rebuilding from text drops it.
* **Words already inside someone's tracked change are refused**, not silently
  accepted to make room: whose change wins is the reader's decision.

Body text only — headers, footers and footnotes are left as they are — and one
paragraph per edit, as in Mike.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

from lxml import etree

from .annotations import locate
from .ocr import _plain

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
DOCUMENT = "word/document.xml"


def _q(tag: str) -> str:
    return f"{{{W}}}{tag}"


INSERTED = (_q("ins"), _q("moveTo"))
DELETED = (_q("del"), _q("moveFrom"))
# Text in these is part of the accepted view but not a plain run of the paragraph.
WRAPPERS = (*INSERTED, _q("hyperlink"), _q("smartTag"))


@dataclass(frozen=True)
class Change:
    """One tracked change as a reviewer sees it. A deletion immediately followed
    by an insertion from the same author is one replacement, with both ids."""

    ids: list[str]
    kind: str  # replace | insert | delete | paragraph | format
    author: str
    date: str | None
    deleted: str
    inserted: str
    before: str  # the paragraph's words either side, accepted view
    after: str


@dataclass
class Edit:
    find: str
    replace: str
    before: str = ""
    after: str = ""
    why: str = ""


@dataclass
class Applied:
    when: str = ""  # the w:date every change in this batch carries
    edits: list[tuple[Edit, list[str]]] = field(default_factory=list)  # each edit and its new w:ids
    refused: list[tuple[Edit, str]] = field(default_factory=list)  # each edit and why it was not placed


class RedlineError(ValueError):
    """An edit that cannot be written as a tracked change, said plainly."""


# --- the file ------------------------------------------------------------------


def _read(content: bytes) -> tuple[zipfile.ZipFile, str, etree._Element]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise RedlineError("This is not a Word document.") from exc
    names = archive.namelist()
    # Some older Word archives store "word\\document.xml"; the zip spec says "/".
    name = DOCUMENT if DOCUMENT in names else DOCUMENT.replace("/", "\\")
    if name not in names:
        raise RedlineError("This is not a Word document: it has no word/document.xml.")
    return archive, name, etree.fromstring(archive.read(name))


def _write(archive: zipfile.ZipFile, name: str, root: etree._Element) -> bytes:
    # Drops the xmlns:w each new element brings, but keeps every prefix the file
    # itself declares: mc:Ignorable names prefixes like w14 that nothing else
    # uses, and without their declarations Word calls the file unreadable.
    etree.cleanup_namespaces(root, keep_ns_prefixes=[prefix for prefix in root.nsmap if prefix])
    xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in archive.infolist():  # every other part exactly as it was
            target.writestr(item, xml if item.filename == name else archive.read(item.filename))
    return out.getvalue()


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_id(root: etree._Element) -> int:
    """Past every w:id in the part: revision ids must not collide with any."""
    highest = 0
    for element in root.iter():
        value = element.get(_q("id"))
        if value is not None and value.lstrip("-").isdigit():
            highest = max(highest, int(value))
    return highest + 1


def _new(tag: str) -> etree._Element:
    return etree.Element(_q(tag), nsmap=NS)


# --- the accepted view of a paragraph ---------------------------------------------


@dataclass
class _Char:
    run: etree._Element  # the w:r the character is in
    node: etree._Element  # its w:t
    offset: int  # within that w:t's text
    editable: bool  # False inside someone's change, a link…


def _run_text(run: etree._Element) -> str:
    return "".join(node.text or "" for node in run if node.tag == _q("t"))


def _paragraph(p: etree._Element) -> tuple[str, list[_Char]]:
    """The paragraph's words as Word shows them with every change accepted, and
    where each character lives. Only a plain run directly in the paragraph can
    be edited; text elsewhere still counts, so a quote spanning it is found and
    then refused with a reason, rather than simply not found."""
    text: list[str] = []
    chars: list[_Char] = []
    for child in p:
        if child.tag == _q("r"):
            runs, editable = [child], True
        elif child.tag in WRAPPERS:
            runs, editable = list(child.iter(_q("r"))), False
        else:
            continue  # deletions, bookmarks, proofing marks: not in the accepted view
        for run in runs:
            for node in run:
                if node.tag == _q("t"):
                    for i, char in enumerate(node.text or ""):
                        text.append(char)
                        chars.append(_Char(run, node, i, editable))
    return "".join(text), chars


def _accepted_length(child: etree._Element) -> int:
    if child.tag == _q("r"):
        return len(_run_text(child))
    if child.tag in WRAPPERS:
        return sum(len(_run_text(run)) for run in child.iter(_q("r")))
    return 0


def _paragraphs(root: etree._Element) -> list[etree._Element]:
    body = root.find(_q("body"))
    return list(body.iter(_q("p"))) if body is not None else []


# --- writing a change --------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    return re.findall(r"\s+|\S+", text)


def _trim(old: str, new: str) -> tuple[int, int, str]:
    """The smallest whole-word span of `old` that differs from `new`.

    "twelve (12) months" → "twenty-four (24) months" marks "twelve (12)" and
    "twenty-four (24)", not the unchanged "months": a redline shows what
    changed, word by word, as Word's own compare does.
    """
    a, b = _tokens(old), _tokens(new)

    def same(x: str, y: str) -> bool:
        return x == y or (x.isspace() and y.isspace())

    lead = 0
    while lead < len(a) and lead < len(b) and same(a[lead], b[lead]):
        lead += 1
    tail = 0
    while tail < len(a) - lead and tail < len(b) - lead and same(a[-1 - tail], b[-1 - tail]):
        tail += 1
    start = sum(len(t) for t in a[:lead])
    end = len(old) - sum(len(t) for t in a[len(a) - tail :])
    return start, end, "".join(b[lead : len(b) - tail])


def _text(tag: str, value: str) -> etree._Element:
    node = _new(tag)
    node.text = value
    node.set(XML_SPACE, "preserve")
    return node


def _run_like(run: etree._Element, children: list[etree._Element]) -> etree._Element:
    """A new run with `run`'s formatting: the inserted words look like their
    neighbours, as Word's do."""
    fresh = _new("r")
    properties = run.find(_q("rPr"))
    if properties is not None:
        fresh.append(etree.fromstring(etree.tostring(properties)))
    for child in children:
        fresh.append(child)
    return fresh


def _split(run: etree._Element, marked: set[tuple[int, int]], *, deleting: bool) -> list[tuple[bool, etree._Element]]:
    """The run cut into consecutive pieces, each wholly marked or wholly not.

    Walks the run's own children, so a tab or break stays in place and goes
    with the text before it. A marked w:t becomes w:delText when deleting.
    """
    pieces: list[tuple[bool, list[etree._Element]]] = []

    def put(flag: bool, element: etree._Element) -> None:
        if pieces and pieces[-1][0] == flag:
            pieces[-1][1].append(element)
        else:
            pieces.append((flag, [element]))

    last = False
    for child in run:
        if child.tag == _q("rPr"):
            continue
        if child.tag != _q("t"):
            put(last, etree.fromstring(etree.tostring(child)))
            continue
        value = child.text or ""
        i = 0
        while i < len(value):
            flag = (id(child), i) in marked
            j = i + 1
            while j < len(value) and ((id(child), j) in marked) == flag:
                j += 1
            put(flag, _text("delText" if flag and deleting else "t", value[i:j]))
            last, i = flag, j
    return [(flag, _run_like(run, children)) for flag, children in pieces]


def _where(views: list[tuple[str, list[_Char]]], start: int) -> tuple[int, int]:
    """Which paragraph holds document offset `start`, and where that paragraph begins."""
    offset = 0
    for index, (text, _) in enumerate(views):
        if start <= offset + len(text):
            return index, offset
        offset += len(text) + 1
    raise RedlineError("Those words are not in this document.")


def _apply(root: etree._Element, edit: Edit, author: str, when: str) -> list[str]:
    """Write one edit into the part. Returns its new w:ids; raises RedlineError."""
    paragraphs = _paragraphs(root)
    views = [_paragraph(p) for p in paragraphs]
    try:
        start, end = locate("\n".join(t for t, _ in views), edit.find, prefix=edit.before, suffix=edit.after)
    except LookupError as exc:
        raise RedlineError(str(exc)) from exc
    index, offset = _where(views, start)
    p, (text, chars) = paragraphs[index], views[index]
    lo, hi = start - offset, end - offset
    if hi > len(text):
        raise RedlineError("Those words run across two paragraphs. Suggest an edit within one paragraph.")
    cut_lo, cut_hi, inserted = _trim(text[lo:hi], edit.replace)
    lo, hi = lo + cut_lo, lo + cut_hi
    if lo == hi and not inserted:
        raise RedlineError("The new wording is the same as the old: there is nothing to change.")
    span = chars[lo:hi]
    at = chars[min(lo, len(chars) - 1)]  # where an insertion lands: before it, or after it at the end
    if not all(c.editable for c in span) or not at.editable:
        raise RedlineError(
            "Those words are part of a tracked change or a link already. "
            "Accept or reject that first, then suggest yours."
        )

    next_id, ids = _next_id(root), []

    def mark(tag: str, runs: list[etree._Element]) -> etree._Element:
        nonlocal next_id
        wrapper = _new(tag)
        for name, value in (("id", str(next_id)), ("author", author), ("date", when)):
            wrapper.set(_q(name), value)
        for run in runs:
            wrapper.append(run)
        ids.append(str(next_id))
        next_id += 1
        return wrapper

    def insertion() -> etree._Element:
        return mark("ins", [_run_like(at.run if not span else span[0].run, [_text("t", inserted)])])

    replacement: list[etree._Element] = []
    if span:
        # The deleted words become one w:del; the new words follow it.
        runs = list(dict.fromkeys(c.run for c in span))
        doomed = {(id(c.node), c.offset) for c in span}
        held: list[etree._Element] = []
        for run in runs:
            for is_deleted, piece in _split(run, doomed, deleting=True):
                if is_deleted:
                    held.append(piece)
                    continue
                if held:
                    replacement.append(mark("del", held))
                    if inserted:
                        replacement.append(insertion())
                    held = []
                replacement.append(piece)
        if held:
            replacement.append(mark("del", held))
            if inserted:
                replacement.append(insertion())
    else:
        # A pure insertion: the run is cut before the character the words go in
        # front of — or, at the paragraph's end, the words follow the last run.
        runs = [at.run]
        after = {(id(c.node), c.offset) for c in chars[lo:] if c.run is at.run} if lo < len(chars) else set()
        placed = False
        for is_after, piece in _split(at.run, after, deleting=False):
            if is_after and not placed:
                replacement.append(insertion())
                placed = True
            replacement.append(piece)
        if not placed:
            replacement.append(insertion())

    position = list(p).index(runs[0])
    for run in runs:
        p.remove(run)
    for k, element in enumerate(replacement):
        p.insert(position + k, element)
    return ids


def propose(content: bytes, edits: list[Edit], *, author: str, when: str | None = None) -> tuple[bytes, Applied]:
    """Write each edit as a tracked change by `author`. An edit that cannot be
    placed is reported, never guessed at, and the others still go in."""
    archive, name, root = _read(content)
    when = when or _now()
    result = Applied(when=when)
    for edit in edits:
        try:
            result.edits.append((edit, _apply(root, edit, author, when)))
        except RedlineError as exc:
            result.refused.append((edit, str(exc)))
    return (_write(archive, name, root) if result.edits else content), result


def clause_edits(old: str, new: str, *, context: int = 60) -> list[Edit]:
    """What a person changed when they retyped a clause, as edits the engine can
    place: each changed stretch of words with the text either side, so it lands
    where it was typed and nowhere else. A pure insertion takes the word before
    it (the word after, at the start) as its anchor — an edit needs words to find.
    """
    from difflib import SequenceMatcher

    a, b = _tokens(old), _tokens(new)
    hunks: list[list[int]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if hunks and all(t.isspace() for t in a[hunks[-1][1] : i1]):
            hunks[-1][1], hunks[-1][3] = i2, j2  # one change, split only by a space
        else:
            hunks.append([i1, i2, j1, j2])
    edits = []
    for i1, i2, j1, j2 in hunks:
        if all(t.isspace() for t in a[i1:i2]) and all(t.isspace() for t in b[j1:j2]):
            continue  # spacing only
        if all(t.isspace() for t in a[i1:i2]):  # nothing old to find: widen to a neighbour
            back = next((k for k in range(i1 - 1, -1, -1) if not a[k].isspace()), None)
            if back is not None:
                j1 -= i1 - back
                i1 = back
            else:
                ahead = next((k for k in range(i2, len(a)) if not a[k].isspace()), None)
                if ahead is None:
                    continue  # the clause was empty
                j2 += ahead + 1 - i2
                i2 = ahead + 1
        start = sum(len(t) for t in a[:i1])
        end = start + sum(len(t) for t in a[i1:i2])
        edits.append(Edit(find=old[start:end].strip(), replace="".join(b[j1:j2]).strip(),
                          before=old[max(0, start - context) : start], after=old[end : end + context]))
    return edits


# Word's formatting as run properties, in the order the schema requires: one
# written out of place makes the file invalid, as a namespace did (see _write).
_RPR_ORDER = (
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
    "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing",
    "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText",
    "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath",
)
STYLES = {  # style: (property, value when on, value when off)
    "bold": ("b", None, "0"),
    "italic": ("i", None, "0"),
    "underline": ("u", "single", "none"),
    "highlight": ("highlight", "yellow", "none"),
}


def _styled(rpr, tag: str) -> bool:
    found = rpr.find(_q(tag)) if rpr is not None else None
    return found is not None and found.get(_q("val"), "1") not in ("0", "false", "none")


def _set_style(rpr: etree._Element, tag: str, value: str | None) -> None:
    for old in rpr.findall(_q(tag)):
        rpr.remove(old)
    element = _new(tag)
    if value is not None:
        element.set(_q("val"), value)
    rank = _RPR_ORDER.index(tag)
    for i, child in enumerate(rpr):
        name = etree.QName(child).localname
        if name == "rPrChange" or (name in _RPR_ORDER and _RPR_ORDER.index(name) > rank):
            rpr.insert(i, element)
            return
    rpr.append(element)


def restyle(content: bytes, edit: Edit, style: str, *, author: str, when: str | None = None) -> tuple[bytes, list[str]]:
    """Bold, italic, underline or highlight on exactly these words, as a tracked
    formatting change — Word's B button with Track Changes on. Like that button
    it toggles: words that all have it lose it."""
    if style not in STYLES:
        raise RedlineError(f"Formatting must be one of {', '.join(STYLES)}.")
    tag, on_value, off_value = STYLES[style]
    archive, name, root = _read(content)
    paragraphs = _paragraphs(root)
    views = [_paragraph(p) for p in paragraphs]
    try:
        start, end = locate("\n".join(t for t, _ in views), edit.find, prefix=edit.before, suffix=edit.after)
    except LookupError as exc:
        raise RedlineError(str(exc)) from exc
    index, offset = _where(views, start)
    p, (text, chars) = paragraphs[index], views[index]
    lo, hi = start - offset, end - offset
    if hi > len(text):
        raise RedlineError("Those words run across two paragraphs. Format one paragraph at a time.")
    span = chars[lo:hi]
    if not span or not all(c.editable for c in span):
        raise RedlineError("Those words are part of a tracked change or a link already. Accept or reject that first.")
    runs = list(dict.fromkeys(c.run for c in span))
    if any(run.find(f"{_q('rPr')}/{_q('rPrChange')}") is not None for run in runs):
        raise RedlineError("Those words already carry a tracked formatting change. Accept or reject it first.")
    marked = {(id(c.node), c.offset) for c in span}
    pieces = [piece for run in runs for piece in _split(run, marked, deleting=False)]
    targets = [run for flag, run in pieces if flag]
    value = off_value if all(_styled(run.find(_q("rPr")), tag) for run in targets) else on_value
    when, next_id, ids = when or _now(), _next_id(root), []
    for run in targets:
        rpr = run.find(_q("rPr"))
        if rpr is None:
            rpr = _new("rPr")
            run.insert(0, rpr)
        before = etree.fromstring(etree.tostring(rpr))
        _set_style(rpr, tag, value)
        change = _new("rPrChange")
        for key, val in (("id", str(next_id)), ("author", author), ("date", when)):
            change.set(_q(key), val)
        change.append(before)
        rpr.append(change)
        ids.append(str(next_id))
        next_id += 1
    position = list(p).index(runs[0])
    for run in runs:
        p.remove(run)
    for k, (_, piece) in enumerate(pieces):
        p.insert(position + k, piece)
    return _write(archive, name, root), ids


# --- reading changes -----------------------------------------------------------------


def _change_text(element: etree._Element) -> str:
    return "".join(node.text or "" for node in element.iter(_q("t"), _q("delText")))


def changes(content: bytes) -> list[Change]:
    """Every tracked change in the body, in reading order, as a reviewer sees them."""
    _, _, root = _read(content)
    found: list[Change] = []
    for p in _paragraphs(root):
        text, _ = _paragraph(p)
        position = 0  # accepted-view characters before the current child
        previous: etree._Element | None = None
        for child in p:
            if child.tag in INSERTED or child.tag in DELETED:
                words, inserted = _change_text(child), child.tag in INSERTED
                author, date, wid = child.get(_q("author"), ""), child.get(_q("date")), child.get(_q("id"), "")
                before = text[max(0, position - 60) : position]
                after = text[position + (len(words) if inserted else 0) :][:60]
                last = found[-1] if found else None
                # A deletion and an insertion side by side, by one author, are one
                # replacement — in whichever order the file keeps them (Word
                # writes both orders; the software licence here has insert first).
                pair = (
                    last is not None and last.author == author and previous is not None
                    and last.kind == ("delete" if inserted else "insert")
                    and previous.tag in (DELETED if inserted else INSERTED)
                )
                if pair:
                    found[-1] = Change(
                        last.ids + [wid], "replace", author, last.date,
                        last.deleted if inserted else words, words if inserted else last.inserted,
                        last.before, after,
                    )
                else:
                    found.append(Change([wid], "insert" if inserted else "delete", author, date,
                                        "" if inserted else words, words if inserted else "", before, after))
            position += _accepted_length(child)
            previous = child
        properties = p.find(_q("pPr"))
        mark = properties.find(_q("rPr")) if properties is not None else None
        for tag in ("del", "ins"):
            node = mark.find(_q(tag)) if mark is not None else None
            if node is not None:
                found.append(Change([node.get(_q("id"), "")], "paragraph", node.get(_q("author"), ""),
                                    node.get(_q("date")), "¶" if tag == "del" else "", "¶" if tag == "ins" else "",
                                    text[-60:], ""))
        grouped: Change | None = None
        for node in p.iter(_q("rPrChange"), _q("pPrChange")):
            run = node.getparent().getparent()
            words = _run_text(run) if run.tag == _q("r") else ""
            author, date = node.get(_q("author"), ""), node.get(_q("date"))
            # One formatting click on words split across runs is one change.
            if grouped is not None and (grouped.author, grouped.date) == (author, date) and words:
                grouped = Change(grouped.ids + [node.get(_q("id"), "")], "format", author, date, "",
                                 grouped.inserted + words, grouped.before, "")
                found[-1] = grouped
                continue
            grouped = Change([node.get(_q("id"), "")], "format", author, date, "", words, text[:60], "")
            found.append(grouped)
    return found


# --- resolving changes ------------------------------------------------------------------


def _attached(element: etree._Element, root: etree._Element) -> bool:
    while element is not None:
        if element is root:
            return True
        element = element.getparent()
    return False


def _merge_into_next(p: etree._Element) -> None:
    """A paragraph mark gone: this paragraph's words join the next paragraph, as
    Word joins them. The last paragraph of its container keeps its words."""
    following = p.getnext()
    while following is not None and following.tag != _q("p"):
        following = following.getnext()
    if following is None:
        return
    properties = following.find(_q("pPr"))
    position = 0 if properties is None else following.index(properties) + 1
    for k, child in enumerate([c for c in p if c.tag != _q("pPr")]):
        following.insert(position + k, child)
    p.getparent().remove(p)


def _restore(change: etree._Element) -> None:
    """A formatting change rejected: the properties go back to what they were.
    A paragraph keeps its mark's own formatting and section, which the old
    properties inside a w:pPrChange do not carry."""
    owner = change.getparent()
    old = change.find(_q("rPr") if change.tag == _q("rPrChange") else _q("pPr"))
    kept = [c for c in owner if c.tag in (_q("rPr"), _q("sectPr"))] if change.tag == _q("pPrChange") else []
    for child in list(owner):
        owner.remove(child)
    for child in list(old) if old is not None else []:
        owner.append(child)
    for child in kept:
        owner.append(child)


def resolve(content: bytes, ids: list[str] | None, *, accept: bool) -> tuple[bytes, int]:
    """Accept or reject these changes — all of them with `ids=None`.
    Returns the new file and how many were resolved."""
    archive, name, root = _read(content)
    wanted = None if ids is None else {str(i) for i in ids}

    def picked(element: etree._Element) -> bool:
        return wanted is None or element.get(_q("id")) in wanted

    count = 0
    # Paragraph marks first: joining paragraphs moves runs the next pass meets.
    for p in _paragraphs(root):
        properties = p.find(_q("pPr"))
        mark = properties.find(_q("rPr")) if properties is not None else None
        for tag in ("del", "ins"):
            node = mark.find(_q(tag)) if mark is not None else None
            if node is None or not picked(node):
                continue
            count += 1
            mark.remove(node)
            if (tag == "del") == accept:  # a removed mark stays gone, an added one is taken out
                _merge_into_next(p)

    for element in list(root.iter(*INSERTED, *DELETED)):
        if not _attached(element, root) or element.getparent().tag == _q("rPr") or not picked(element):
            continue
        count += 1
        parent = element.getparent()
        if (element.tag in INSERTED) == accept:  # its words stay, as ordinary text
            position = parent.index(element)
            for k, child in enumerate(list(element)):
                for node in child.iter(_q("delText"), _q("delInstrText")):
                    node.tag = _q("t") if node.tag == _q("delText") else _q("instrText")
                parent.insert(position + k, child)
        parent.remove(element)

    for change in list(root.iter(_q("rPrChange"), _q("pPrChange"))):
        if not _attached(change, root) or not picked(change):
            continue
        count += 1
        if accept:
            change.getparent().remove(change)
        else:
            _restore(change)
    return (_write(archive, name, root) if count else content), count


# --- a Word version of a document that is not one ---------------------------------------


def word_copy(clauses: list, title: str) -> bytes:
    """A .docx built from a version's clauses, so a PDF can be redlined.

    Built from the text, so it carries the words, numbering and order but not
    the PDF's look — which stays in the version it came from. Tables read by OCR
    become Word tables.
    """
    from docx import Document
    from docx.shared import Inches

    document = Document()
    document.core_properties.title = title
    for clause in clauses:
        raw = clause.text or ""
        rows = re.findall(r"<tr>(.*?)</tr>", raw, flags=re.IGNORECASE | re.DOTALL)
        if rows:
            cells = [[_plain(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.IGNORECASE | re.DOTALL)] for row in rows]
            width = max((len(row) for row in cells), default=0)
            if width:
                table = document.add_table(rows=len(cells), cols=width)
                table.style = "Table Grid"
                for r, row in enumerate(cells):
                    for c, value in enumerate(row):
                        table.cell(r, c).text = value
                continue
        text = _plain(raw)
        if not text:
            continue
        paragraph = document.add_paragraph()
        run = paragraph.add_run(text)
        run.bold = clause.clause_type == "heading"
        paragraph.paragraph_format.left_indent = Inches(0.3 * min(max((clause.level or 1) - 1, 0), 5))
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def lookalike_copy(title: str, *, fetch: str, key: str, secret: str, editor: str) -> bytes:
    """A Word copy that looks like the PDF, by the document editor's own
    converter: it lays every line into its own floating box, so the page looks
    right and the pages stay as they were — and the text no longer flows, which
    makes it a copy to send, not one to edit. `toword` is the other way round:
    real paragraphs to edit, in the PDF's own fonts.

    `fetch` is where the editor can read the PDF from, on its own network.
    """
    import httpx
    import jwt

    payload = {"async": False, "filetype": "pdf", "outputtype": "docx", "title": title,
               "key": key[:20], "url": fetch}
    try:
        answer = httpx.post(f"{editor}/ConvertService.ashx",
                            json={**payload, "token": jwt.encode(payload, secret, algorithm="HS256")},
                            headers={"Accept": "application/json"}, timeout=300)
        answer.raise_for_status()
        body = answer.json()
        if "fileUrl" not in body:
            raise RedlineError(f"The document editor could not convert this PDF ({body.get('error', body)}).")
        if urlsplit(body["fileUrl"]).netloc != urlsplit(editor).netloc:
            raise RedlineError("The document editor pointed the download somewhere else; it was not followed.")
        made = httpx.get(body["fileUrl"], timeout=300)
        made.raise_for_status()
    except httpx.HTTPError as exc:
        raise RedlineError("The document editor is not running, so the look-alike copy could not be made. "
                           "Start it with: docker compose --profile editor up -d onlyoffice") from exc
    if not made.content.startswith(b"PK"):
        raise RedlineError("The document editor sent back something that is not a Word file.")
    return made.content


# --- each action is the document's next version ----------------------------------------


def _file(version) -> bytes:
    if not version.storage_key:
        raise RedlineError("The file itself was not kept for this version. Add it again first.")
    from app.integrations.storage import storage_service

    return storage_service.read_bytes(version.storage_key)


def _word(version) -> bytes:
    from .parsing.registry import DOCX_MIME

    if version.mime_type != DOCX_MIME:
        raise RedlineError(
            "Only a Word file can carry tracked changes. Make an editable Word version of it first."
        )
    return _file(version)


def _next_version(db, version, content: bytes, *, filename: str, event: str, details: dict):
    """The changed file as the document's next version: read again, clause ids
    carried where the words did not change, every note looked for again — the
    same path as any new version, so a redline is never a special case."""
    from .parsing.registry import DOCX_MIME
    from .service import ingest, record_event

    result = ingest(
        db,
        org_id=version.org_id,
        content=content,
        filename=filename,
        mime_type=DOCX_MIME,
        document_id=version.document_id,
    )
    record_event(
        db,
        org_id=version.org_id,
        document_id=version.document_id,
        version_id=result.version_id,
        event_type=event,
        details=details,
    )
    return result


def suggest(db, version, edits: list[Edit], *, author: str):
    """Edits written as tracked changes by `author`, as the next version.
    Returns the new version and what was placed and refused."""
    content, applied = propose(_word(version), edits, author=author)
    if not applied.edits:
        raise RedlineError(applied.refused[0][1] if applied.refused else "There was nothing to change.")
    details = {
        "author": author,
        "date": applied.when,
        "changes": [
            {"ids": ids, "find": edit.find[:300], "replace": edit.replace[:300], "why": edit.why[:600]}
            for edit, ids in applied.edits
        ],
        "refused": [{"find": edit.find[:300], "why": why} for edit, why in applied.refused],
    }
    result = _next_version(db, version, content, filename=version.filename or "document.docx",
                           event="redline.suggested", details=details)
    return result, applied


def format_words(db, version, edit: Edit, style: str, *, author: str):
    """`restyle` as the document's next version."""
    content, ids = restyle(_word(version), edit, style, author=author)
    return _next_version(
        db, version, content, filename=version.filename or "document.docx", event="redline.formatted",
        details={"author": author, "style": style, "ids": ids, "find": edit.find[:300]},
    )


def settle(db, version, ids: list[str] | None, *, accept: bool):
    """Accept or reject these changes — or all of them — as the next version."""
    content, count = resolve(_word(version), ids, accept=accept)
    if not count:
        raise RedlineError("That change is no longer in the document.")
    return _next_version(
        db, version, content, filename=version.filename or "document.docx",
        event="redline.accepted" if accept else "redline.rejected",
        details={"ids": ids if ids is not None else "all", "count": count},
    )


SCANNED = ("This is a scan of a signed document. Signed documents are not redlined: "
           "changes to it go in a separate amendment.")


def is_scan(version) -> bool:
    return version.mime_type == "application/pdf" and "ocr" in (version.parser_name or "")


def word_content(db, version) -> tuple[bytes, dict]:
    """A typed PDF or text file as Word bytes, and how it was made: read as a
    document (`toword`) or, when that is refused, its words only with the
    reason. Refuses a scan and a Word file."""
    from .parsing.registry import DOCX_MIME
    from .service import clauses_for

    if version.mime_type == DOCX_MIME:
        raise RedlineError("This is already a Word document.")
    if is_scan(version):
        # A scan holds no text to convert; what it has is a reading of the
        # picture, written down as markdown that records how the page looked
        # and rebuilt from there. Its words are OCR's, and the note says so.
        from .scanmd import rebuild

        try:
            content, said, how = rebuild(db, version, _file(version))
        except ValueError as exc:
            raise RedlineError(str(exc)) from exc
        stem = (version.filename or "document").rsplit(".", 1)[0]
        return content, {
            "from_version": version.id, "clauses": 0, "look": "rebuilt", "markdown": said,
            "note": (f"Rebuilt from what {how['read_by']} read on the scan ({how['blocks']} blocks, "
                     f"{how['pictures']} pictures cut from the pages). These words are a machine's "
                     "reading of a photograph, not the contract's own text — check them against the "
                     "scan before sending it."),
            "filename": f"{stem} (rebuilt from the scan).docx", **how,
        }
    clauses = clauses_for(db, version.id)
    if not clauses:
        raise RedlineError("There is no text to build a Word version from.")
    stem = (version.filename or "document").rsplit(".", 1)[0]
    content, look, note = None, "words", ""  # plain text has no look to keep
    pdf = version.mime_type == "application/pdf"
    if pdf:
        from .toword import ConversionError
        from .toword_build import convert

        try:
            content, made = convert(_file(version))
            look, note = "kept", ""
            if made["words_kept"] < 1:
                note = f"{1 - made['words_kept']:.1%} of the PDF's letters are spacing Word writes differently."
        except ConversionError as exc:
            note = f"{exc} So this Word version has the words only, not the PDF's look."
    if content is None:
        content = word_copy(clauses, stem)
    return content, {
        "from_version": version.id, "clauses": len(clauses), "look": look, "note": note,
        "filename": f"{stem} (converted from PDF).docx" if pdf else f"{stem}.docx",
    }


def make_editable(db, version):
    """A typed PDF or text file as a Word document, the next version, so it can
    be redlined. The original stays as the version before it.

    A typed PDF keeps its look (`toword`). A scan is refused, as other legal
    tools do: it is almost always the signed contract, which is changed by an
    amendment, not a redline — and a Word copy of it would carry every OCR
    misreading as its text.
    """
    content, made = word_content(db, version)
    filename = made.pop("filename")
    return _next_version(db, version, content, filename=filename, event="redline.made_editable", details=made)


def reasons(db, document_id: str) -> dict[str, str]:
    """Why each suggested change was made, by w:id and date: kept on the event
    log, since a Word file has nowhere to put a reason. The date guards against
    an id reused after an earlier change was accepted away."""
    from sqlalchemy import select

    from .models import DsEvent

    found: dict[str, str] = {}
    for event in db.scalars(
        select(DsEvent).where(DsEvent.document_id == document_id, DsEvent.event_type == "redline.suggested")
    ):
        details = event.details or {}
        for change in details.get("changes", []):
            if change.get("why"):
                for wid in change.get("ids", []):
                    found[f"{wid}|{details.get('date')}"] = change["why"]
    return found
