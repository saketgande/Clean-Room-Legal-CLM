"""Ingest: bytes in, a versioned structured document out.

The whole pipeline in one readable function, on purpose. The thing this
replaces grew into a 200-line orchestrator that nobody could hold in their head,
and every silent failure hid inside one of its `except` blocks.
"""

import hashlib
import logging
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .annotations import reanchor
from .audit import audit, describe
from .models import DsClause, DsDocument, DsEvent, DsVersion
from .ocr import OcrProvider, cached, default_providers, run_ocr
from .parsing.base import ParsedBlock, ParsedDocument, UnsupportedFormat
from .parsing.cleanup import clean
from .parsing.labels import level_for, split_label
from .parsing.reflow import join_wrapped_blocks
from .parsing.registry import parser_for
from .parsing.text import blocks_from_text
from .structure import body, build, verify_offsets

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestResult:
    document_id: str
    version_id: str
    version_number: int
    clause_count: int
    warnings: list[str]
    deduplicated: bool = False
    # Which provider read the pages, when the file needed OCR at all.
    ocr_provider: str | None = None


def record_event(
    db: Session,
    *,
    org_id: str,
    document_id: str,
    event_type: str,
    version_id: str | None = None,
    details: dict | None = None,
    actor_user_id: str | None = None,
) -> None:
    db.add(
        DsEvent(
            org_id=org_id,
            document_id=document_id,
            version_id=version_id,
            event_type=event_type,
            details=details,
            actor_user_id=actor_user_id,
        )
    )



# The provider's label for a block, where it says something the words cannot.
# Everything else is a paragraph. "Header" is deliberately absent: it is the
# page's running header, not a heading, and `cleanup.py` removes it.
_KIND_FOR_ROLE = {
    "title": "heading",
    "section header": "heading",
    "list item": "list_item",
    "table": "table",
}


def _blocks_from_ocr(result) -> list[ParsedBlock]:
    """Turn a provider's blocks into parsed blocks, keeping their geometry and
    what the provider says each one is.

    Falls back to nothing — not to a guess — when the provider returned no
    blocks, so the caller can split the flat text instead.
    """
    out: list[ParsedBlock] = []
    for entry in getattr(result, "blocks", None) or []:
        text = (entry.get("content") or "").strip()
        if not text:
            continue
        role = (entry.get("type") or "").strip() or None
        label, remainder = split_label(text)
        out.append(
            ParsedBlock(
                text=remainder if label else text,
                kind=_KIND_FOR_ROLE.get((role or "").lower(), "paragraph"),
                number_label=label,
                level=level_for(label),
                page_number=entry.get("page"),
                bbox=entry.get("bbox"),
                role=role,
            )
        )
    return out


def _apply_ocr(
    parsed: ParsedDocument,
    content: bytes,
    *,
    filename: str,
    mime_type: str,
    providers: list[OcrProvider] | None,
) -> tuple[ParsedDocument, str | None, list[str]]:
    """Replace a picture-only parse with what OCR could read.

    Only runs when the parser said the pages are images, because OCR is charged
    per page and a document that already has text gains nothing from it.

    On failure the original parse is returned untouched, carrying its warnings.
    That is deliberate: a document nobody could read must keep saying so rather
    than quietly becoming a short document.
    """
    if not parsed.needs_ocr:
        return parsed, None, []
    candidates = default_providers() if providers is None else providers
    if not candidates:
        return parsed, None, ["No OCR provider is configured, so the pages were not read."]

    outcome = run_ocr(candidates, content, filename=filename, mime_type=mime_type)
    if not outcome.succeeded:
        return parsed, None, [f"OCR failed — {'; '.join(outcome.errors)}"]

    result = outcome.result
    # Prefer the provider's own blocks: they carry the page and rectangle each
    # piece of text occupies, which splitting a flat string cannot recover. A
    # citation into a scan is quotable either way, but only this way can it be
    # shown on the page it came from.
    blocks = _blocks_from_ocr(result) or blocks_from_text(
        result.text, page_count=parsed.page_count
    )
    # OCR reads the whole page, so the running header, the page number and even
    # a caption describing the company seal come back as blocks. They are not
    # clauses, and leaving them in means a third of the clause list is furniture.
    blocks, removed, removed_chars = clean(blocks, parsed.page_count)
    # A contract does not stop at the bottom of a page, but OCR does: a clause
    # running across a page boundary comes back as two blocks. Joined only after
    # cleanup, or the footer or stamp between the halves keeps them apart.
    blocks, joined = join_wrapped_blocks(blocks)
    if not blocks:
        return parsed, None, [f"OCR ({result.provider}) returned text that held no blocks."]

    notes = [f"Pages were read by OCR ({result.provider})."]
    notes.extend(f"OCR fell back past {e}" for e in outcome.errors)
    return (
        ParsedDocument(
            blocks=blocks,
            page_count=parsed.page_count,
            warnings=[],
            needs_ocr=False,
            dropped_chars=removed_chars,
            artifacts={"removed": removed, "joined": joined},
        ),
        result.provider,
        notes,
    )


def _store(org_id: str, filename: str, mime_type: str, content: bytes) -> str | None:
    """Keep the bytes, or None if this deployment has no storage configured.

    A failure here must not lose the parse: the text and clauses are the
    expensive part, and a version without its file still works — the view says
    the original is unavailable instead of showing nothing.
    """
    try:
        from app.integrations.storage import storage_service

        return storage_service.save_bytes(
            org_id=org_id, filename=filename, mime_type=mime_type, content=content
        ).storage_key
    except Exception:  # pragma: no cover - storage misconfigured
        log.exception("docstudio could not store the original file for %s", filename)
        return None


def ingest(
    db: Session,
    *,
    org_id: str,
    content: bytes,
    filename: str,
    mime_type: str,
    external_ref: str | None = None,
    title: str | None = None,
    actor_user_id: str | None = None,
    document_id: str | None = None,
    ocr_providers: list[OcrProvider] | None = None,
) -> IngestResult:
    """Parse `content` and store it as a new version.

    Raises `UnsupportedFormat` when no parser handles the media type. That is
    deliberate: a document nobody could read must not be stored as a document
    with no text in it, because everything downstream then treats "we could not
    read this" as "this contract says nothing".
    """
    parser = parser_for(mime_type)  # raises before anything is written

    document = db.get(DsDocument, document_id) if document_id else None
    if document is None:
        document = DsDocument(
            org_id=org_id,
            external_ref=external_ref,
            title=title or filename,
            created_by_user_id=actor_user_id,
        )
        db.add(document)
        db.flush()

    sha256 = hashlib.sha256(content).hexdigest()
    existing = db.scalar(
        select(DsVersion).where(
            DsVersion.document_id == document.id,
            DsVersion.sha256 == sha256,
            # A version read by OCR stores "pdf+ocr:reducto", so matching the
            # bare parser name never found one and **dedup was dead for every
            # scanned document** — twelve of the thirteen in the sample corpus.
            # Each re-upload therefore spent an OCR call and minted a fresh set
            # of clause ids, orphaning every annotation anchored to the old
            # ones: exactly the failure this check exists to prevent. Worse
            # than wasteful, because OCR is not deterministic (§14.5), so the
            # second parse of identical bytes is not identical text.
            or_(
                DsVersion.parser_name == parser.name,
                DsVersion.parser_name.startswith(f"{parser.name}+ocr:"),
            ),
            DsVersion.parser_version == parser.version,
        )
    )
    if existing is not None:
        # Same bytes, same parser: re-parsing would produce an identical
        # version and a second set of clause ids, silently orphaning every
        # annotation anchored to the first.
        if not existing.storage_key:
            # Read before the file itself was kept. The sha matched, so these
            # are those exact bytes — and dedup returns here, so this is the
            # only moment such a version can ever get its original back.
            existing.storage_key = _store(org_id, filename, mime_type, content)
        clause_count = db.scalar(
            select(func.count()).select_from(DsClause).where(DsClause.version_id == existing.id)
        )
        return IngestResult(
            document_id=document.id,
            version_id=existing.id,
            version_number=existing.version_number,
            clause_count=clause_count or 0,
            warnings=[],
            deduplicated=True,
        )

    parsed = parser.parse(content, filename=filename)
    if parsed.needs_ocr and ocr_providers is None:
        # The paid providers read any given bytes once; see `ocr.cached`.
        ocr_providers = cached(db, sha256, default_providers())
    parsed, ocr_provider, ocr_notes = _apply_ocr(
        parsed, content, filename=filename, mime_type=mime_type, providers=ocr_providers
    )
    # A clause recognisably the same as one in the last version keeps its id,
    # so what is anchored to it follows it (`versions.carry_ids`).
    previous = [
        (clause.clause_id, body(clause.number_label, clause.text))
        for clause in (clauses_for(db, document.current_version_id) if document.current_version_id else [])
    ]
    structure = build(parsed, previous)
    warnings = list(structure.warnings) + ocr_notes

    # A clause that cannot be found at the offsets it claims makes every
    # citation into this version wrong. Record it on the document rather than
    # discarding the ingest, but make it loud.
    problems = verify_offsets(structure)
    if problems:
        warnings.append(f"{len(problems)} clause offsets did not verify")

    # Count what the file holds against what came out. Extraction cannot check
    # itself: a run of skipped text leaves a grammatical sentence behind, so a
    # missing liability cap reads exactly like a document that parsed cleanly.
    # Skipped after OCR, where the native text is precisely what was rejected
    # and there is nothing meaningful to compare against.
    audit_result = (
        None
        if ocr_provider
        else audit(
            content,
            mime_type=mime_type,
            extracted_text=structure.flat_text,
            deliberately_dropped=parsed.dropped_chars,
        )
    )
    if audit_result is not None and audit_result.is_suspicious:
        warnings.append(describe(audit_result))

    highest = db.scalar(
        select(func.max(DsVersion.version_number)).where(DsVersion.document_id == document.id)
    )
    version = DsVersion(
        org_id=org_id,
        document_id=document.id,
        version_number=(highest or 0) + 1,
        sha256=sha256,
        mime_type=mime_type,
        filename=filename,
        byte_size=len(content),
        # The OCR provider is what actually produced this text, so it is what
        # the version must record — re-parsing with the native parser would not
        # reproduce it.
        parser_name=f"{parser.name}+ocr:{ocr_provider}" if ocr_provider else parser.name,
        parser_version=parser.version,
        # The file itself, kept so the document view can draw the real page —
        # a scan's stamps and signatures are in the bytes, not in the text.
        storage_key=_store(org_id, filename, mime_type, content),
        flat_text=structure.flat_text,
        page_count=structure.page_count,
        parse_warnings=warnings or None,
        artifacts=parsed.artifacts or None,
        created_by_user_id=actor_user_id,
    )
    db.add(version)
    db.flush()

    for clause in structure.clauses:
        db.add(
            DsClause(
                org_id=org_id,
                version_id=version.id,
                clause_id=clause.clause_id,
                seq=clause.seq,
                parent_clause_id=clause.parent_clause_id,
                number_label=clause.number_label,
                level=clause.level,
                clause_type=clause.clause_type,
                text=clause.text,
                char_start=clause.char_start,
                char_end=clause.char_end,
                page_number=clause.page_number,
                bbox=clause.bbox,
                number_scheme=clause.number_scheme,
                number_path=clause.number_path,
                structure_source=clause.structure_source,
                source_regions=clause.source_regions,
            )
        )

    document.current_version_id = version.id
    db.flush()
    # Every annotation on the document is looked for again in this version.
    anchored = reanchor(db, version)
    kept_ids = {clause_id for clause_id, _ in previous}
    record_event(
        db,
        org_id=org_id,
        document_id=document.id,
        version_id=version.id,
        event_type="version.ingested",
        actor_user_id=actor_user_id,
        details={
            "parser": f"{parser.name}/{parser.version}",
            "ocr_provider": ocr_provider,
            "clauses": len(structure.clauses),
            # Of this version's clauses, how many are the same clause as one in
            # the last version, and how the annotations were found again.
            "clauses_carried": sum(c.clause_id in kept_ids for c in structure.clauses) if previous else None,
            "annotations_found_by_rung": anchored or None,
            "undecided": sum(c.structure_source == "undecided" for c in structure.clauses),
            "removed": len((parsed.artifacts or {}).get("removed") or []),
            "joined": len((parsed.artifacts or {}).get("joined") or []),
            "chars": len(structure.flat_text),
            "pages": structure.page_count,
            # Kept on the event as well as the version: the event log is what an
            # operator reads when asking why a document looks thin.
            "warnings": warnings,
            "offset_problems": problems[:5] or None,
            # Recorded whether or not it looked wrong: the number only means
            # something as a series, and a document that starts dropping text
            # after a library upgrade is visible in the trend, not in one row.
            "audit": None
            if audit_result is None
            else {
                "source_chars": audit_result.source_chars,
                "extracted_chars": audit_result.extracted_chars,
                "deliberately_dropped": audit_result.deliberately_dropped,
                "unexplained": audit_result.unexplained,
                "ratio": round(audit_result.ratio, 4),
                "uses": audit_result.findings or None,
            },
        },
    )
    db.flush()

    return IngestResult(
        document_id=document.id,
        version_id=version.id,
        version_number=version.version_number,
        clause_count=len(structure.clauses),
        warnings=warnings,
        ocr_provider=ocr_provider,
    )


def clauses_for(db: Session, version_id: str) -> list[DsClause]:
    return list(
        db.scalars(
            select(DsClause).where(DsClause.version_id == version_id).order_by(DsClause.seq)
        )
    )


__all__ = ["IngestResult", "UnsupportedFormat", "clauses_for", "ingest", "record_event"]
