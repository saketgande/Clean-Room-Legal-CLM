"""Did we get everything out of the file?

Text extraction cannot check itself. When a run of text is skipped, what
remains is still a grammatical sentence, still scores well on any readability
measure, and still looks exactly like a document that parsed correctly. A
counterparty's redline came out as "Liability is capped at  of fees." and
nothing in the pipeline could tell that a number was missing.

So this counts the text the file *holds*, independently of the parser that read
it, and compares. The comparison is deliberately crude — characters, not
structure — because it is looking for the one thing structure-aware checks
cannot see: content that is simply absent.

It must not share code with the parser. A check built on the same reader agrees
with it by construction and finds nothing, which is why it goes back to the raw
XML rather than through python-docx's object model — the object model is where
the bug was.
"""

import re
from dataclasses import dataclass, field
from io import BytesIO
from zipfile import ZipFile

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Below this share of the file's own text, a gap is worth a human looking at.
# Some difference is expected and healthy: whitespace is normalised, empty
# paragraphs collapse, page furniture is removed on purpose.
UNEXPLAINED_THRESHOLD = 0.02

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class AuditResult:
    source_chars: int
    extracted_chars: int
    deliberately_dropped: int
    findings: list[str] = field(default_factory=list)

    @property
    def unexplained(self) -> int:
        """Characters the file held that nothing accounts for."""
        return max(0, self.source_chars - self.extracted_chars - self.deliberately_dropped)

    @property
    def ratio(self) -> float:
        return self.unexplained / self.source_chars if self.source_chars else 0.0

    @property
    def is_suspicious(self) -> bool:
        return self.ratio > UNEXPLAINED_THRESHOLD


def _squeeze(text: str) -> int:
    """Characters ignoring all whitespace.

    Counting raw length would flag every document, because extraction collapses
    runs of spaces, drops empty paragraphs and re-joins lines. Whitespace is
    the one difference that is never a loss of content.
    """
    return len(_WHITESPACE.sub("", text or ""))


def audit(
    content: bytes,
    *,
    mime_type: str,
    extracted_text: str,
    deliberately_dropped: int = 0,
) -> AuditResult | None:
    """Compare what the file holds against what was extracted.

    Returns None for formats with no independent way to read them — an OCR'd
    scan has no source text to compare against, so silence is honest.
    """
    if mime_type == "application/pdf":
        source, findings = _pdf_source_text(content)
    elif mime_type.endswith("wordprocessingml.document"):
        source, findings = _docx_source_text(content)
    else:
        return None
    if source is None:
        return None
    return AuditResult(
        source_chars=_squeeze(source),
        extracted_chars=_squeeze(extracted_text),
        deliberately_dropped=deliberately_dropped,
        findings=findings,
    )


def _docx_source_text(content: bytes) -> tuple[str | None, list[str]]:
    """Every character `word/document.xml` would display.

    Walks the XML directly. `w:delText` is skipped because struck wording is
    not part of the document as it reads; everything else counts, including the
    wrappers (`w:ins`, `w:hyperlink`, `w:txbxContent`) whose contents a naive
    reader cannot see. Those wrappers are precisely what this exists to catch.
    """
    from lxml import etree

    try:
        with ZipFile(BytesIO(content)) as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                return None, []
            root = etree.fromstring(archive.read("word/document.xml"))
    except Exception:
        return None, []

    findings: list[str] = []
    parts: list[str] = []
    for node in root.iter(f"{W_NS}t"):
        if _inside(node, f"{W_NS}del"):
            continue
        parts.append(node.text or "")

    # Named so a gap can be explained rather than merely reported. A document
    # using these is where a text-only reader loses content without a trace.
    for tag, label in (
        ("ins", "tracked insertions"),
        ("hyperlink", "hyperlinks"),
        ("txbxContent", "text boxes"),
        ("tbl", "tables"),
    ):
        if root.find(f".//{W_NS}{tag}") is not None:
            findings.append(label)
    return "".join(parts), findings


def _pdf_source_text(content: bytes) -> tuple[str | None, list[str]]:
    """The page text as the library reads it in its simplest mode.

    Not fully independent — the same library reads both — but a different call
    path through it, which still catches a parser that mishandles blocks,
    ordering or page ranges.
    """
    try:
        import fitz

        with fitz.open(stream=content, filetype="pdf") as document:
            return "".join(page.get_text() for page in document), []
    except Exception:
        return None, []


def _inside(node, tag: str) -> bool:
    parent = node.getparent()
    while parent is not None:
        if parent.tag == tag:
            return True
        parent = parent.getparent()
    return False


def describe(result: AuditResult) -> str:
    """One line for the version's warnings and the event log."""
    percent = result.ratio * 100
    detail = f" The document uses {', '.join(result.findings)}." if result.findings else ""
    return (
        f"{result.unexplained:,} characters of {result.source_chars:,} "
        f"({percent:.1f}%) were in the file but not extracted.{detail}"
    )
