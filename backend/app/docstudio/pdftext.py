"""Words changed where they stand in a PDF page's drawing instructions.

Adding new text to a page puts it at the end of the page's instructions, and
every reader — ours included — then reads it last: "Payment is due within days
of invoice. forty". So an agreed change is written *inside* the instruction
that draws the old words: the old letters come out, the new ones go in, and a
spacing adjustment after them holds everything that follows exactly where it
was. The page's reading order does not change, nor does any other word.

Only what can be done exactly is done, else PdfTextError says why:

* The words must be drawn by one text instruction (Tj or TJ).
* The font must be a simple font in WinAnsi encoding or a two-byte Identity-H
  font with a Unicode map — what Word and most PDF writers produce.
* Every new letter must be one the file already prints in that font: fonts in
  PDFs are subsets, and a letter outside the subset prints as nothing.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pikepdf


class PdfTextError(ValueError):
    """The change cannot be written exactly; the message says why."""


@dataclass
class _Font:
    two_byte: bool
    decode: dict[bytes, str]
    widths: dict[bytes, float]  # in thousandths of an em
    default: float
    used: set[bytes]

    def encode(self, text: str) -> list[bytes]:
        reverse: dict[str, bytes] = {}
        for code in sorted(self.used):
            reverse.setdefault(self.decode.get(code, ""), code)
        codes = []
        for char in text:
            if char not in reverse:
                raise PdfTextError(f"The file never prints “{char}” in this font, so its copy of the font "
                                   "may not have it.")
            codes.append(reverse[char])
        return codes


def _hex(token: str) -> bytes:
    return bytes.fromhex(token)


def _to_unicode(data: bytes) -> dict[bytes, str]:
    """A ToUnicode CMap's bfchar and bfrange entries: code → text."""
    text = data.decode("latin-1")
    found: dict[bytes, str] = {}

    def utf16(token: str) -> str:
        return _hex(token).decode("utf-16-be", errors="replace")

    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.DOTALL):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            found[_hex(src)] = utf16(dst)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.DOTALL):
        for lo, hi, rest in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)", block):
            start, end, width = int(lo, 16), int(hi, 16), len(lo) // 2
            if rest.startswith("["):
                for k, dst in enumerate(re.findall(r"<([0-9A-Fa-f]+)>", rest)):
                    found[(start + k).to_bytes(width, "big")] = utf16(dst)
            else:
                base = int(rest[1:-1], 16)
                size = (len(rest) - 2) // 2
                for k in range(end - start + 1):
                    found[(start + k).to_bytes(width, "big")] = (base + k).to_bytes(size, "big").decode(
                        "utf-16-be", errors="replace")
    return found


def _font(obj, used: set[bytes]) -> _Font:
    subtype = str(obj.get("/Subtype"))
    if subtype == "/Type0":
        if str(obj.get("/Encoding")) != "/Identity-H" or "/ToUnicode" not in obj:
            raise PdfTextError("The font's encoding is one this cannot rewrite exactly.")
        decode = _to_unicode(obj.ToUnicode.read_bytes())
        descendant = obj.DescendantFonts[0]
        default = float(descendant.get("/DW", 1000))
        widths: dict[bytes, float] = {}
        entries = list(descendant.get("/W", []))
        i = 0
        while i < len(entries):
            first = int(entries[i])
            if isinstance(entries[i + 1], pikepdf.Array):
                for k, w in enumerate(entries[i + 1]):
                    widths[(first + k).to_bytes(2, "big")] = float(w)
                i += 2
            else:
                last, w = int(entries[i + 1]), float(entries[i + 2])
                for cid in range(first, last + 1):
                    widths[cid.to_bytes(2, "big")] = w
                i += 3
        return _Font(True, decode, widths, default, used)
    if subtype in ("/TrueType", "/Type1"):
        if str(obj.get("/Encoding")) != "/WinAnsiEncoding":
            raise PdfTextError("The font's encoding is one this cannot rewrite exactly.")
        decode = {bytes([b]): bytes([b]).decode("cp1252", errors="replace") for b in range(256)}
        if "/ToUnicode" in obj:
            decode.update(_to_unicode(obj.ToUnicode.read_bytes()))
        first = int(obj.get("/FirstChar", 0))
        widths = {bytes([first + k]): float(w) for k, w in enumerate(obj.get("/Widths", []))}
        descriptor = obj.get("/FontDescriptor")
        default = float(descriptor.get("/MissingWidth", 0)) if descriptor is not None else 0.0
        return _Font(False, decode, widths, default, used)
    raise PdfTextError("The font is of a kind this cannot rewrite exactly.")


@dataclass
class _Code:
    item: int  # which element of the instruction's array
    start: int  # byte range within that string
    end: int
    code: bytes
    text: str


@dataclass
class _Shown:
    index: int  # the instruction's place in the page's list
    font: _Font
    size: float
    tc: float
    tw: float
    tz: float
    items: list  # strings as bytes, adjustments as floats
    codes: list[_Code]

    @property
    def text(self) -> str:
        return "".join(c.text for c in self.codes)


def _items(instruction) -> list:
    operator = str(instruction.operator)
    if operator == "Tj":
        return [bytes(instruction.operands[0])]
    return [bytes(x) if isinstance(x, pikepdf.String) else float(x) for x in instruction.operands[0]]


def _fonts_by_name(page) -> dict[str, object]:
    resources = page.obj.get("/Resources") or {}
    fonts = resources.get("/Font") or {}
    return {str(name): fonts[name] for name in fonts}


def _used_codes(pdf) -> dict[tuple, set[bytes]]:
    """Every code each font draws anywhere in the file: the letters its subset surely has."""
    used: dict[tuple, set[bytes]] = {}
    for page in pdf.pages:
        fonts, current = _fonts_by_name(page), None
        for instruction in pikepdf.parse_content_stream(page):
            operator = str(instruction.operator)
            if operator == "Tf":
                current = fonts.get(str(instruction.operands[0]))
            elif operator in ("Tj", "TJ") and current is not None:
                width = 2 if str(current.get("/Subtype")) == "/Type0" else 1
                bucket = used.setdefault(current.objgen, set())
                for item in _items(instruction):
                    if isinstance(item, bytes):
                        bucket.update(item[k : k + width] for k in range(0, len(item) - width + 1, width))
    return used


def _shown(pdf, page_index: int) -> tuple[list, list[_Shown]]:
    page = pdf.pages[page_index]
    instructions = list(pikepdf.parse_content_stream(page))
    fonts, used = _fonts_by_name(page), _used_codes(pdf)
    cache: dict[tuple, _Font | PdfTextError] = {}
    state = {"font": None, "size": 0.0, "tc": 0.0, "tw": 0.0, "tz": 100.0}
    stack, shown = [], []
    for index, instruction in enumerate(instructions):
        operator, operands = str(instruction.operator), list(instruction.operands)
        if operator == "q":
            stack.append(dict(state))
        elif operator == "Q" and stack:
            state = stack.pop()
        elif operator == "Tf":
            state["font"], state["size"] = fonts.get(str(operands[0])), float(operands[1])
        elif operator in ("Tc", "Tw", "Tz"):
            state[operator.lower()] = float(operands[0])
        elif operator in ("Tj", "TJ") and state["font"] is not None:
            key = state["font"].objgen
            if key not in cache:
                try:
                    cache[key] = _font(state["font"], used.get(key, set()))
                except PdfTextError as exc:
                    cache[key] = exc
            font = cache[key]
            items = _items(instruction)
            codes: list[_Code] = []
            if isinstance(font, _Font):
                width = 2 if font.two_byte else 1
                for i, item in enumerate(items):
                    if isinstance(item, bytes):
                        for k in range(0, len(item) - width + 1, width):
                            code = item[k : k + width]
                            codes.append(_Code(i, k, k + width, code, font.decode.get(code, "�")))
            shown.append(_Shown(index, font, state["size"], state["tc"], state["tw"], state["tz"], items, codes))
    return instructions, shown


def _advance(shown: _Shown, codes: list[bytes], adjustments: list[float]) -> float:
    """How far these codes and adjustments move the pen, in points (before the text matrix)."""
    font, scale = shown.font, shown.tz / 100
    total = 0.0
    for code in codes:
        spacing = shown.tw if (not font.two_byte and code == b" ") else 0.0
        total += (font.widths.get(code, font.default) / 1000 * shown.size + shown.tc + spacing) * scale
    return total - sum(a / 1000 * shown.size * scale for a in adjustments)


def _tokens(text: str) -> list[str]:
    return [t for t in (re.sub(r"\W+", "", w.lower()) for w in text.split()) if t]


def replace(pdf_bytes: bytes, page_index: int, old: str, new: str, prefix: str = "") -> tuple[bytes, float, float]:
    """The page's drawing instructions with `old` changed to `new` inside the
    instruction that draws it, and the width the old and new words take, in
    points. Raises PdfTextError when that cannot be done exactly."""
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        instructions, shown = _shown(pdf, page_index)
        pattern = re.compile(r"\s+".join(re.escape(w) for w in old.split()))
        hits = []
        for position, item in enumerate(shown):
            if isinstance(item.font, PdfTextError):
                if pattern.search(item.text or ""):
                    raise item.font
                continue
            for match in pattern.finditer(item.text):
                hits.append((position, match.start(), match.end()))
        if not hits:
            raise PdfTextError("The words are not drawn by one instruction on the page; they cannot be "
                               "changed in place.")
        if len(hits) > 1:
            wanted = _tokens(prefix)[-6:]

            def agree(hit):
                before = "".join(s.text for s in shown[: hit[0]]) + shown[hit[0]].text[: hit[1]]
                got = _tokens(before)
                return sum(1 for a, b in zip(reversed(got), reversed(wanted), strict=False) if a == b)
            best = max(agree(h) for h in hits)
            hits = [h for h in hits if agree(h) == best]
            if len(hits) != 1:
                raise PdfTextError("The words appear more than once on the page and nothing tells them apart.")
        position, lo, hi = hits[0]
        target = shown[position]
        font = target.font
        # Characters to codes: a code's text is wholly inside the change or wholly outside it.
        offsets, at = [], 0
        for code in target.codes:
            offsets.append((at, at + len(code.text)))
            at += len(code.text)
        chosen = [k for k, (a, b) in enumerate(offsets) if a >= lo and b <= hi]
        if not chosen or offsets[chosen[0]][0] != lo or offsets[chosen[-1]][1] != hi:
            raise PdfTextError("The words share a drawn character with their neighbours.")
        first, last = target.codes[chosen[0]], target.codes[chosen[-1]]
        dropped = [x for x in target.items[first.item + 1 : last.item] if not isinstance(x, bytes)]
        old_width = _advance(target, [target.codes[k].code for k in chosen], dropped)
        new_codes = font.encode(new)
        new_width = _advance(target, new_codes, [])
        compensation = (new_width - old_width) * 1000 / (target.size * target.tz / 100)
        items = target.items
        rebuilt = list(items[: first.item])
        if first.start:
            rebuilt.append(items[first.item][: first.start])
        rebuilt.append(b"".join(new_codes))
        if abs(compensation) > 0.001:
            rebuilt.append(compensation)
        if last.end < len(items[last.item]):
            rebuilt.append(items[last.item][last.end :])
        rebuilt += items[last.item + 1 :]
        array = pikepdf.Array([pikepdf.String(x) if isinstance(x, bytes) else x for x in rebuilt])
        instructions[target.index] = pikepdf.ContentStreamInstruction([array], pikepdf.Operator("TJ"))
        return pikepdf.unparse_content_stream(instructions), old_width, new_width
