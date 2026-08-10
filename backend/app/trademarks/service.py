from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.ai.embeddings import embed_texts
from app.auth.models import User
from app.core.audit import write_audit_log
from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.database import new_uuid
from app.core.enums import (
    DocumentExtractTemplate,
    TrademarkSource,
    TrademarkWorkflowState,
)
from app.integrations.storage import storage_service
from app.trademarks.extraction.field_capture import extract_generic_records
from app.trademarks.extraction.vision import extract_journal_page
from app.trademarks.models import DocumentExtract, Trademark
from app.trademarks.providers.similarity import search_similar as _search_similar
from app.trademarks.schemas import (
    DashboardMetrics,
    ExtractedRecord,
    ExtractRequest,
    ExtractResponse,
    IngestRequest,
    IngestResponse,
    IntakeSubmitRequest,
    IntegrationStatusEntry,
    IntegrationStatusResponse,
    IntegrationTestResponse,
    SearchSimilarRequest,
    SearchSimilarResponse,
    TrademarkCreate,
    TrademarkUpdate,
    UploadDocumentResponse,
)

# Standard trademark renewal cycle used to backfill renewal_due_on when a
# filed_on date is known but no explicit renewal date was provided.
_DEFAULT_RENEWAL_CYCLE_DAYS = 365 * 10


def _embedding_text(*, name: str, description: str | None, goods_services: str | None) -> str:
    return " ".join(part for part in (name, description or "", goods_services or "") if part).strip()


def get_trademark_for_user(db: Session, *, trademark_id: str, user: User) -> Trademark:
    trademark = db.get(Trademark, trademark_id)
    if trademark is None or trademark.org_id != user.org_id or trademark.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trademark not found")
    return trademark


def list_trademarks(db: Session, *, org_id: str) -> list[Trademark]:
    return db.scalars(
        select(Trademark)
        .where(Trademark.org_id == org_id, Trademark.deleted_at.is_(None))
        .order_by(Trademark.updated_at.desc())
    ).all()


def _create_trademark(
    db: Session,
    *,
    user: User,
    name: str,
    description: str | None,
    trademark_type: str,
    jurisdiction: str,
    jurisdictions: list[str] | None,
    nice_class: str | None,
    goods_services: str | None,
    filing_context: dict | None,
    filed_on: datetime | None,
    renewal_due_on: datetime | None,
    source: str,
    source_document_extract_id: str | None = None,
) -> Trademark:
    if renewal_due_on is None and filed_on is not None:
        renewal_due_on = filed_on + timedelta(days=_DEFAULT_RENEWAL_CYCLE_DAYS)

    embedding_text = _embedding_text(name=name, description=description, goods_services=goods_services)
    embedding = embed_texts([embedding_text])[0] if embedding_text else None

    trademark = Trademark(
        id=new_uuid(),
        org_id=user.org_id,
        created_by_user_id=user.id,
        updated_by_user_id=user.id,
        name=name,
        description=description,
        trademark_type=trademark_type,
        jurisdiction=jurisdiction,
        jurisdictions=jurisdictions or [jurisdiction],
        nice_class=nice_class,
        goods_services=goods_services,
        filing_context=filing_context,
        filed_on=filed_on,
        renewal_due_on=renewal_due_on,
        workflow_state=(
            TrademarkWorkflowState.EXTRACTION if source == TrademarkSource.EXTRACTION else TrademarkWorkflowState.INTAKE
        ),
        source=source,
        source_document_extract_id=source_document_extract_id,
        embedding_text=embedding_text or None,
        embedding=embedding,
    )
    db.add(trademark)
    db.flush()
    write_audit_log(
        db,
        action="trademark.create",
        resource_type="trademark",
        resource_id=trademark.id,
        org_id=user.org_id,
        actor_user_id=user.id,
        after={"name": trademark.name, "status": trademark.status, "source": source},
    )
    return trademark


def create_trademark(db: Session, *, user: User, payload: TrademarkCreate) -> Trademark:
    trademark = _create_trademark(
        db,
        user=user,
        name=payload.name,
        description=payload.description,
        trademark_type=payload.trademark_type,
        jurisdiction=payload.jurisdiction,
        jurisdictions=payload.jurisdictions,
        nice_class=payload.nice_class,
        goods_services=payload.goods_services,
        filing_context=payload.filing_context,
        filed_on=payload.filed_on,
        renewal_due_on=payload.renewal_due_on,
        source=TrademarkSource.INTAKE,
    )
    db.commit()
    db.refresh(trademark)
    return trademark


def create_trademark_from_intake(db: Session, *, user: User, payload: IntakeSubmitRequest) -> Trademark:
    jurisdictions = payload.jurisdictions or ["IN"]
    trademark = _create_trademark(
        db,
        user=user,
        name=payload.name,
        description=payload.description,
        trademark_type=payload.trademark_type,
        jurisdiction=jurisdictions[0],
        jurisdictions=jurisdictions,
        nice_class=payload.nice_class,
        goods_services=payload.goods_services,
        filing_context=payload.filing_context,
        filed_on=None,
        renewal_due_on=payload.renewal_due_on,
        source=TrademarkSource.INTAKE,
    )
    db.commit()
    db.refresh(trademark)
    return trademark


def update_trademark(db: Session, *, trademark: Trademark, user: User, payload: TrademarkUpdate) -> Trademark:
    before = {"status": trademark.status, "workflow_state": trademark.workflow_state}
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(trademark, field, value)
    trademark.updated_by_user_id = user.id
    db.flush()
    write_audit_log(
        db,
        action="trademark.update",
        resource_type="trademark",
        resource_id=trademark.id,
        org_id=user.org_id,
        actor_user_id=user.id,
        before=before,
        after={"status": trademark.status, "workflow_state": trademark.workflow_state},
    )
    db.commit()
    db.refresh(trademark)
    return trademark


def dashboard_metrics(db: Session, *, org_id: str) -> DashboardMetrics:
    rows = db.execute(
        select(Trademark.status, func.count(Trademark.id))
        .where(Trademark.org_id == org_id, Trademark.deleted_at.is_(None))
        .group_by(Trademark.status)
    ).all()
    by_status = {status_value: count for status_value, count in rows}
    total = sum(by_status.values())
    active_prosecutions = sum(
        count for status_value, count in by_status.items() if status_value in {"filed", "opposed"}
    )
    upcoming_cutoff = datetime.now(UTC) + timedelta(days=90)
    upcoming_renewals = db.scalar(
        select(func.count(Trademark.id)).where(
            Trademark.org_id == org_id,
            Trademark.deleted_at.is_(None),
            Trademark.renewal_due_on.is_not(None),
            Trademark.renewal_due_on <= upcoming_cutoff,
        )
    ) or 0
    return DashboardMetrics(
        total_trademarks=total,
        active_prosecutions=active_prosecutions,
        upcoming_renewals=upcoming_renewals,
        by_status=by_status,
    )


def list_upcoming_renewals(db: Session, *, org_id: str, within_days: int = 90) -> list[Trademark]:
    cutoff = datetime.now(UTC) + timedelta(days=within_days)
    return db.scalars(
        select(Trademark)
        .where(
            Trademark.org_id == org_id,
            Trademark.deleted_at.is_(None),
            Trademark.renewal_due_on.is_not(None),
            Trademark.renewal_due_on <= cutoff,
        )
        .order_by(Trademark.renewal_due_on.asc())
    ).all()


# ---------------------------------------------------------------------------
# Search-similar
# ---------------------------------------------------------------------------


def search_similar(db: Session, *, user: User, request: SearchSimilarRequest) -> SearchSimilarResponse:
    return _search_similar(db, org_id=user.org_id, request=request, settings=default_settings)


# ---------------------------------------------------------------------------
# Document extraction pipeline
# ---------------------------------------------------------------------------


async def save_uploaded_document(db: Session, *, user: User, file: UploadFile) -> UploadDocumentResponse:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only PDF files are supported")
    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The uploaded file is empty")

    from io import BytesIO

    from pypdf import PdfReader

    try:
        total_pages = len(PdfReader(BytesIO(content)).pages)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Couldn't read that PDF") from exc

    stored = storage_service.save_bytes(
        org_id=user.org_id, filename=file.filename, mime_type="application/pdf", content=content
    )
    return UploadDocumentResponse(doc_id=stored.storage_key, filename=stored.filename, total_pages=total_pages)


async def extract_fields(db: Session, *, user: User, request: ExtractRequest) -> ExtractResponse:
    content = storage_service.read_bytes(request.doc_id)
    filename = request.doc_id.rsplit("/", 1)[-1]
    warnings: list[str] = []

    if request.template == DocumentExtractTemplate.IP_INDIA_JOURNAL:
        records: list[ExtractedRecord] = []
        for page_number in range(request.page_start, request.page_end + 1):
            try:
                entries = await extract_journal_page(content, page_number)
            except Exception as exc:
                warnings.append(f"Page {page_number}: vision extraction failed ({exc})")
                continue
            for entry_index, entry in enumerate(entries):
                records.append(
                    ExtractedRecord(
                        page_number=page_number,
                        entry_index=entry_index,
                        fields={
                            "product_name": entry.product_name,
                            "mark_type": entry.mark_type,
                            "tm_id": entry.tm_id,
                            "tm_date": entry.tm_date,
                            "address": entry.address,
                            "used_since": entry.used_since,
                            "proposed_to_be_used": entry.proposed_to_be_used,
                            "jurisdiction": entry.jurisdiction,
                            "goods_services": entry.goods_services,
                            "has_product_image": entry.has_product_image,
                        },
                        used_ocr=False,
                    )
                )
    else:
        records = await extract_generic_records(
            content=content,
            filename=filename,
            page_start=request.page_start,
            page_end=request.page_end,
            field_definitions=request.field_schema,
        )
        for record in records:
            warnings.extend(record.warnings)

    return ExtractResponse(doc_id=request.doc_id, records=records, warnings=warnings)


_IP_INDIA_JOURNAL_MAP = {
    "name": "product_name",
    "jurisdiction": "jurisdiction",
    "goods_services": "goods_services",
    "filed_on": "tm_date",
}


def _record_to_trademark_fields(record: ExtractedRecord, template: str) -> dict | None:
    fields = record.fields
    if template == DocumentExtractTemplate.IP_INDIA_JOURNAL:
        name = fields.get("product_name")
        if not name:
            return None
        filed_on = None
        raw_date = fields.get("tm_date")
        if raw_date:
            for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
                try:
                    filed_on = datetime.strptime(raw_date, fmt).replace(tzinfo=UTC)
                    break
                except ValueError:
                    continue
        return {
            "name": name,
            "jurisdiction": fields.get("jurisdiction") or "IN",
            "goods_services": fields.get("goods_services"),
            "filed_on": filed_on,
            "description": fields.get("address"),
        }
    # generic template: best-effort — look for a "name"-ish field.
    name = fields.get("name") or fields.get("product_name") or fields.get("mark") or fields.get("trademark")
    if not name:
        return None
    return {
        "name": name,
        "jurisdiction": fields.get("jurisdiction") or "IN",
        "goods_services": fields.get("goods_services"),
        "filed_on": None,
        "description": None,
    }


def ingest_extraction(db: Session, *, user: User, request: IngestRequest) -> IngestResponse:
    record_ids: list[str] = []
    trademark_ids: list[str] = []

    for record in request.records:
        embedding_text = " ".join(
            f"{k}: {v}" for k, v in record.fields.items() if k != "product_image_paths" and v
        )
        embedding = embed_texts([embedding_text])[0] if embedding_text else None

        extract_row = DocumentExtract(
            id=new_uuid(),
            org_id=user.org_id,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
            source_filename=request.source_filename,
            storage_key=request.doc_id,
            page_start=request.page_start,
            page_end=request.page_end,
            template=request.template,
            field_schema=[f.model_dump() for f in request.field_schema],
            extracted_fields=record.fields,
            embedding_text=embedding_text or None,
            embedding=embedding,
        )
        db.add(extract_row)
        db.flush()
        record_ids.append(extract_row.id)

        trademark_fields = _record_to_trademark_fields(record, request.template)
        if trademark_fields is not None:
            trademark = _create_trademark(
                db,
                user=user,
                name=trademark_fields["name"],
                description=trademark_fields.get("description"),
                trademark_type="word_mark",
                jurisdiction=trademark_fields["jurisdiction"],
                jurisdictions=[trademark_fields["jurisdiction"]],
                nice_class=None,
                goods_services=trademark_fields.get("goods_services"),
                filing_context=None,
                filed_on=trademark_fields.get("filed_on"),
                renewal_due_on=None,
                source=TrademarkSource.EXTRACTION,
                source_document_extract_id=extract_row.id,
            )
            extract_row.ingested_trademark_id = trademark.id
            trademark_ids.append(trademark.id)

    write_audit_log(
        db,
        action="trademark.document_extract.ingest",
        resource_type="document_extract",
        org_id=user.org_id,
        actor_user_id=user.id,
        after={"ingested_count": len(record_ids), "trademark_ids": trademark_ids},
    )
    db.commit()
    return IngestResponse(ingested_count=len(record_ids), record_ids=record_ids, trademark_ids=trademark_ids)


# ---------------------------------------------------------------------------
# Integrations status / test
# ---------------------------------------------------------------------------


def get_integration_status(db: Session, *, settings: Settings) -> IntegrationStatusResponse:
    signa = IntegrationStatusEntry(
        configured=bool(settings.signa_api_key) and not settings.mock_signa,
        status="configured" if (settings.signa_api_key and not settings.mock_signa) else "not_configured",
    )
    serper = IntegrationStatusEntry(
        configured=bool(settings.search_provider_api_key) and not settings.mock_serper,
        status="configured" if (settings.search_provider_api_key and not settings.mock_serper) else "not_configured",
    )
    try:
        db.execute(text("SELECT 1"))
        postgres = IntegrationStatusEntry(configured=True, status="connected")
    except Exception as exc:
        postgres = IntegrationStatusEntry(configured=True, status="error", detail=str(exc))
    return IntegrationStatusResponse(signa=signa, serper=serper, postgres=postgres)


def test_integration(db: Session, *, service_name: str, settings: Settings) -> IntegrationTestResponse:
    import time

    started = time.perf_counter()
    if service_name == "postgres":
        try:
            db.execute(text("SELECT 1"))
            return IntegrationTestResponse(
                status="ok", detail="Connected", latency_ms=(time.perf_counter() - started) * 1000
            )
        except Exception as exc:
            return IntegrationTestResponse(status="error", detail=str(exc))

    if service_name == "signa":
        if settings.mock_signa or not settings.signa_api_key:
            return IntegrationTestResponse(status="not_configured", detail="SIGNA_API_KEY not set")
        from app.trademarks.providers import signa as signa_provider

        try:
            signa_provider.search(
                "test",
                api_key=settings.signa_api_key,
                base_url=settings.signa_base_url,
                offices=settings.signa_offices,
                timeout_seconds=settings.signa_timeout_seconds,
                limit=1,
            )
            return IntegrationTestResponse(status="ok", latency_ms=(time.perf_counter() - started) * 1000)
        except Exception as exc:
            return IntegrationTestResponse(status="error", detail=str(exc))

    if service_name == "serper":
        if settings.mock_serper or not settings.search_provider_api_key:
            return IntegrationTestResponse(status="not_configured", detail="SEARCH_PROVIDER_API_KEY not set")
        from app.trademarks.providers import web_search as web_search_provider

        try:
            web_search_provider.search(
                "test", api_key=settings.search_provider_api_key,
                timeout_seconds=settings.search_provider_timeout_seconds, num=1,
            )
            return IntegrationTestResponse(status="ok", latency_ms=(time.perf_counter() - started) * 1000)
        except Exception as exc:
            return IntegrationTestResponse(status="error", detail=str(exc))

    raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown integration service: {service_name}")
