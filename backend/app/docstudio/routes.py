"""Thin handlers. Every decision lives in the service modules beside them.

Four things the document view needs: the documents you have, a way to add one,
everything stored about a version, and the file itself. Nothing here reaches
into another subsystem — see `filesource.py` for why.
"""

import logging
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission

from .annotations import for_document
from .models import DsClause, DsDocument, DsVersion
from .parsing.base import UnsupportedFormat
from .runner import mime_for
from .schemas import AnnotationOut, ClauseOut, DocumentOut, VersionDetail, VersionSummary
from .service import ingest

log = logging.getLogger(__name__)
router = APIRouter(prefix="/docstudio", tags=["docstudio"])

# The largest contract in the sample corpus is 6 MB. Generous, and still stops
# an accidental multi-gigabyte upload being read into memory.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _summary(version: DsVersion) -> VersionSummary:
    name, _, provider = version.parser_name.partition("+ocr:")
    return VersionSummary(
        id=version.id,
        document_id=version.document_id,
        version_number=version.version_number,
        filename=version.filename,
        mime_type=version.mime_type,
        byte_size=version.byte_size,
        page_count=version.page_count,
        has_file=bool(version.storage_key),
        has_text=bool((version.flat_text or "").strip()),
        read_by=f"{name} v{version.parser_version}"
        + (f", then OCR by {provider}" if provider else ""),
        created_at=version.created_at,
    )


def _document(db: Session, document_id: str, org_id: str) -> DsDocument:
    document = db.get(DsDocument, document_id)
    if document is None or document.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such document")
    return document


def _version(db: Session, version_id: str, org_id: str) -> DsVersion:
    version = db.get(DsVersion, version_id)
    # Checked against the document, not the version's own org_id: one query
    # either way, and the document is what a reader is given access to.
    if version is None or _document(db, version.document_id, org_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such version")
    return version


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract_file:read")),
) -> list[DocumentOut]:
    """Every document in this organisation, most recently touched first."""
    documents = list(
        db.scalars(
            select(DsDocument)
            .where(DsDocument.org_id == current_user.org_id)
            .order_by(DsDocument.updated_at.desc())
        )
    )
    counts = dict(
        db.execute(
            select(DsVersion.document_id, func.count())
            .where(DsVersion.document_id.in_([d.id for d in documents] or [""]))
            .group_by(DsVersion.document_id)
        ).all()
    )
    out = []
    for document in documents:
        current = db.get(DsVersion, document.current_version_id) if document.current_version_id else None
        out.append(
            DocumentOut(
                id=document.id,
                title=document.title,
                external_ref=document.external_ref,
                version_count=counts.get(document.id, 0),
                current=_summary(current) if current is not None else None,
            )
        )
    return out


@router.post("/documents", response_model=VersionSummary, status_code=status.HTTP_201_CREATED)
def upload_document(
    file: UploadFile = File(...),
    document_id: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract_file:create")),
) -> VersionSummary:
    """Read a file into clauses and store it as a version.

    `document_id` makes it a new version of that document, which is what lets
    its notes be found again in the new text (`annotations.reanchor`).
    """
    filename = (file.filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if not filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The upload has no file name.")
    mime_type = mime_for(filename)
    if mime_type is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Cannot read that file type: use .pdf, .docx or .txt."
        )
    content = _read_capped(file)
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The file is empty.")
    if document_id:
        _document(db, document_id, current_user.org_id)

    try:
        result = ingest(
            db,
            org_id=current_user.org_id,
            content=content,
            filename=filename,
            mime_type=mime_type,
            document_id=document_id or None,
            actor_user_id=current_user.id,
        )
    except UnsupportedFormat as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    db.commit()
    return _summary(db.get(DsVersion, result.version_id))


@router.get("/versions/{version_id}", response_model=VersionDetail)
def read_version(
    version_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract_file:read")),
) -> VersionDetail:
    """Everything stored about one version: its text, its clauses, its notes."""
    version = _version(db, version_id, current_user.org_id)
    clauses = db.scalars(
        select(DsClause).where(DsClause.version_id == version.id).order_by(DsClause.seq)
    )
    return VersionDetail(
        **_summary(version).model_dump(),
        flat_text=version.flat_text or "",
        parse_warnings=version.parse_warnings,
        clauses=[
            ClauseOut(**{c: getattr(clause, c) for c in ClauseOut.model_fields if c != "regions"},
                      regions=clause.source_regions)
            for clause in clauses
        ],
        annotations=[AnnotationOut.model_validate(note) for note in for_document(db, version.document_id)],
    )


@router.get("/versions/{version_id}/file")
def read_version_file(
    version_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("contract_file:read")),
) -> Response:
    """The bytes this version was read from.

    Always as an attachment, and never rendered by the browser itself: an
    uploaded HTML or SVG file wearing an allowed media type would otherwise run
    in this origin. The document view fetches these bytes and draws them itself.
    """
    version = _version(db, version_id, current_user.org_id)
    if not version.storage_key:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "The file itself was not kept for this version — it was read before file storage.",
        )
    from app.integrations.storage import storage_service

    try:
        content = storage_service.read_bytes(version.storage_key)
    except Exception as exc:
        log.exception("docstudio could not read %s", version.storage_key)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The stored file could not be read.") from exc

    # A quote or control character here would corrupt the header for the client.
    safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", version.filename or "")[:80].strip() or "document"
    return Response(
        content=content,
        media_type=version.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def _read_capped(upload: UploadFile) -> bytes:
    chunks, size = [], 0
    while chunk := upload.file.read(1024 * 1024):
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"The file is over the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)
