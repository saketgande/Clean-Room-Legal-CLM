"""Reading a document whose pages are pictures.

A scanned contract holds no text. Every page is an image, so a parser finds
nothing — and a document with no text is indistinguishable downstream from a
document that says very little. OCR is what turns the picture back into words.

Two rules here, both learned from the pipeline this replaces:

**Try every configured provider, not just the best-configured one.** "Enabled"
means "has credentials", which says nothing about whether it works. One
deployment's primary provider answers 403 to every upload while a working key
sits unused in the same settings object, and the result is scanned contracts
with permanently empty text.

**Record why each provider failed.** An operator looking at an empty contract
needs to tell "one key is wrong" from "this document is genuinely unreadable",
and only the full list distinguishes them.

Providers are adapted behind `OcrProvider` rather than called directly, so
docstudio depends on an interface it owns instead of on another subsystem's
client.
"""

import asyncio
import html
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class OcrResult:
    text: str
    provider: str
    quality: float | None = None
    # One entry per block the provider recognised, each with the page and
    # rectangle it occupies. A provider that returns only a string leaves this
    # empty and the caller falls back to splitting the text.
    blocks: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class OcrOutcome:
    """What happened across every provider tried."""

    result: OcrResult | None
    errors: list[str]

    @property
    def succeeded(self) -> bool:
        return self.result is not None and bool(self.result.text.strip())


class OcrProvider(Protocol):
    name: str

    def extract(self, content: bytes, *, filename: str, mime_type: str) -> OcrResult: ...


def run_ocr(
    providers: list[OcrProvider], content: bytes, *, filename: str, mime_type: str
) -> OcrOutcome:
    """Walk the providers in order, stopping at the first that returns text."""
    errors: list[str] = []
    for provider in providers:
        try:
            result = provider.extract(content, filename=filename, mime_type=mime_type)
        except Exception as exc:
            errors.append(f"{provider.name}: {exc}")
            continue
        if result.text.strip():
            return OcrOutcome(result=result, errors=errors)
        # A provider answering 200 with an empty body has failed just as
        # completely as one that raised, and a scanned PDF is exactly the input
        # that produces it.
        errors.append(f"{provider.name}: returned no text")
    return OcrOutcome(result=None, errors=errors)


def run_sync(coro):
    """The app's OCR clients are async; ingest is not.

    Running the coroutine on its own thread when a loop is already turning
    avoids deadlocking a caller that happens to be async.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class _ClientAdapter:
    """Wraps one of the app's OCR clients behind `OcrProvider`."""

    def __init__(self, name: str, client):
        self.name = name
        self._client = client

    def extract(self, content: bytes, *, filename: str, mime_type: str) -> OcrResult:
        response = run_sync(
            self._client.extract_text(
                filename=filename, mime_type=mime_type, content=content
            )
        )
        return OcrResult(
            text=response.text or "",
            provider=getattr(response, "provider", self.name),
            quality=getattr(response, "quality_score", None),
            blocks=list(getattr(response, "blocks", None) or []),
        )


def default_providers() -> list[OcrProvider]:
    """Whatever this deployment has credentials for, best first.

    Order matters beyond "which one works": a provider that returns page
    elements can support page-level citations, and one that returns only a
    string cannot. Preferring whichever happens to be healthy today would trade
    that away silently.
    """
    providers: list[OcrProvider] = []
    try:
        from app.core.config import settings
        from app.integrations.reducto import reducto_client
    except Exception:  # pragma: no cover - integrations unavailable
        return providers

    if getattr(settings, "reducto_api_key", None):
        providers.append(_ClientAdapter("reducto", reducto_client))
    return providers


class _Stored:
    """A reading already paid for, served back instead of asking again."""

    def __init__(self, row):
        self.name = row.provider
        self._row = row

    def extract(self, content: bytes, *, filename: str, mime_type: str) -> OcrResult:
        return OcrResult(
            text=self._row.text,
            provider=self._row.provider,
            quality=(self._row.quality or {}).get("score"),
            blocks=list(self._row.blocks or []),
        )


class _Storing:
    """A provider whose reading is kept once it has been paid for."""

    def __init__(self, db, sha256: str, provider: OcrProvider):
        self.name = provider.name
        self._db, self._sha256, self._provider = db, sha256, provider

    def extract(self, content: bytes, *, filename: str, mime_type: str) -> OcrResult:
        from .models import DsOcrResult

        result = self._provider.extract(content, filename=filename, mime_type=mime_type)
        if result.text.strip():
            self._db.add(
                DsOcrResult(
                    sha256=self._sha256,
                    provider=self.name,
                    text=result.text,
                    blocks=result.blocks or None,
                    quality={"score": result.quality},
                )
            )
        return result


def cached(db, sha256: str, providers: list[OcrProvider]) -> list[OcrProvider]:
    """These providers, reading any given bytes at most once.

    OCR is charged per page and is not deterministic — the same scan read twice
    came back 39,209 and 39,916 characters long — so a second reading costs
    money *and* mints different clauses, orphaning everything anchored to the
    first. A stored reading from any configured provider wins, in the order the
    providers are preferred; only bytes never read before reach a provider.
    """
    from sqlalchemy import select

    from .models import DsOcrResult

    names = [provider.name for provider in providers]
    rows = db.scalars(
        select(DsOcrResult)
        .where(DsOcrResult.sha256 == sha256, DsOcrResult.provider.in_(names))
        .order_by(DsOcrResult.created_at.desc())
    ).all()
    if rows:
        return [_Stored(min(rows, key=lambda row: names.index(row.provider)))]
    return [_Storing(db, sha256, provider) for provider in providers]


# The provider's own markup, by name. Not every angle bracket: contract
# templates print fill-ins such as "<Date>" and "<Customer Name>", and a
# generic tag pattern would delete words that are on the page.
_MARKUP = re.compile(
    r"</?(?:b|i|u|sup|sub|br|signature|empty|table|thead|tbody|tr|th|td)(?:\s[^<>]*=[^<>]*)?\s*/?>", re.IGNORECASE
)


def _plain(content: str) -> str:
    """A block's words as printed: its cells spaced, its rows on lines."""
    text = re.sub(r"</t[dh]\s*>", " ", content, flags=re.IGNORECASE)
    text = re.sub(r"</tr\s*>|<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = html.unescape(_MARKUP.sub("", text))
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def page_text(db, version) -> list[dict]:
    """What OCR read on a scan, and where: one entry per paragraph.

    A scan holds no words, so a viewer has nothing on the page to select or
    search. These are laid over the picture, invisible — the "searchable
    image" Acrobat makes of a scan. By paragraph, because a paragraph's box is
    the finest the stored reading has; a provider with word boxes would make it
    word-exact.

    Only what is printed. A "Figure" block is the provider's *description* of
    a picture ("blue circular stamp…"): words nobody could find on the page.
    """
    from sqlalchemy import select

    from .models import DsOcrResult

    _, _, provider = (version.parser_name or "").partition("+ocr:")
    if not provider:
        return []  # read from the file's own text, which the viewer already has
    row = db.scalar(
        select(DsOcrResult)
        .where(DsOcrResult.sha256 == version.sha256, DsOcrResult.provider == provider)
        .order_by(DsOcrResult.created_at.desc())
        .limit(1)
    )
    regions = []
    for block in (row.blocks or []) if row is not None else []:
        box, page = block.get("bbox"), block.get("page")
        text = _plain(block.get("content") or "")
        if block.get("type") == "Figure" or not (text and box and page):
            continue
        regions.append(
            {"page": page, "bbox": {k: round(box[k], 4) for k in ("x0", "y0", "x1", "y1")}, "text": text}
        )
    return regions
