"""Phase 1 end to end, for one file: cut, arrange, check, report.

The command line (`python -m app.docstudio`) and the dev page (`devui.py`) both
call `run`, so the two can never drift into running different pipelines — a
test page that exercises code the real path does not is worse than none.

* **Code cuts** the file into clauses and places each one the numbering or
  the file itself can place (`service.ingest`, `tree.py`).
* **The AI settles** only the clauses nothing in the file could place, from a
  closed list of options each, once per version (`hierarchy.arrange_version`).
* **The report** says who placed what and lists what a person should look at.

The same file twice is free: the earlier ingest is found by its SHA-256, and a
scan's OCR reading is stored, so a contract is OCR'd once and arranged once
however often it is looked at.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .annotations import for_document
from .hierarchy import arrange_version
from .models import DsDocument, DsVersion
from .parsing.registry import DOCX_MIME
from .report import findings, report
from .service import IngestResult, clauses_for, ingest

# Runs from the command line and the dev page get their own scope: they are
# experiments, and keeping them apart makes them easy to find and to wipe.
# docstudio treats org_id as a plain string, so nothing else is involved.
TOOL_ORG = "docstudio-cli"

_MIME = {".pdf": "application/pdf", ".docx": DOCX_MIME, ".txt": "text/plain"}


def mime_for(name: str) -> str | None:
    return _MIME.get(Path(name).suffix.lower())


@dataclass(frozen=True)
class RunOutput:
    name: str
    result: IngestResult
    how: str
    structure: str
    clause_count: int
    page_count: int | None
    finding_count: int
    report_md: str
    notes: str = ""


def run(
    db: Session, *, content: bytes, name: str, ai: bool = True, version_of: str | None = None
) -> RunOutput:
    """Ingest `content`, arrange it, commit, and render what came out.

    `version_of` names a document this file is a new version of: its notes are
    then looked for again in the new text. Without it, the same bytes seen
    before reuse their document, and anything else is a new document.

    Raises `UnsupportedFormat` for a type no parser reads, before anything is
    written — `ingest` checks that first — and `LookupError` for a
    `version_of` that is not one of these runs' documents.
    """
    mime = mime_for(name)
    if mime is None:
        from .parsing.base import UnsupportedFormat

        suffix = Path(name).suffix or "a file with no extension"
        raise UnsupportedFormat(f"cannot read {suffix}: use .pdf, .docx or .txt")

    if version_of is not None:
        document = db.get(DsDocument, version_of)
        if document is None or document.org_id != TOOL_ORG:
            raise LookupError(f"no such document: {version_of}")
        document_id = document.id
    else:
        document_id = db.scalar(
            select(DsVersion.document_id)
            .where(DsVersion.org_id == TOOL_ORG, DsVersion.sha256 == hashlib.sha256(content).hexdigest())
            .limit(1)
        )
    result = ingest(
        db, org_id=TOOL_ORG, content=content, filename=name, mime_type=mime, document_id=document_id
    )
    db.commit()

    structure = arrange_version(db, result.version_id) if ai else "rules only — AI switched off"
    db.commit()

    version = db.get(DsVersion, result.version_id)
    clauses = clauses_for(db, result.version_id)
    found = len(findings(clauses, page_count=version.page_count)) + len(version.parse_warnings or [])
    if result.deduplicated:
        how = f"already ingested — reused version {result.version_number}, nothing re-read"
    elif result.ocr_provider:
        how = f"{version.parser_name}, OCR by {result.ocr_provider}"
    else:
        how = f"{version.parser_name} v{version.parser_version}, no OCR needed"
    if version.version_number > 1 and not result.deduplicated:
        how += f" — version {version.version_number} of this document"

    notes = for_document(db, result.document_id)
    lost = sum(note.anchor_state == "orphaned" for note in notes)
    return RunOutput(
        name=name,
        result=result,
        how=how,
        structure=structure,
        clause_count=len(clauses),
        page_count=version.page_count,
        finding_count=found,
        report_md=report(db, result.version_id),
        notes=f"{len(notes)}" + (f", {lost} lost — re-link them" if lost else ""),
    )
