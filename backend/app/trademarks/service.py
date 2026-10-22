import hashlib
import time
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.ai.cost_guard import enforce_daily_token_cap
from app.ai.embeddings import embed_texts
from app.auth.models import User
from app.core.audit import write_audit_log
from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.database import new_uuid
from app.core.enums import (
    DocumentExtractTemplate,
    TrademarkSource,
    TrademarkStatus,
    TrademarkWorkflowState,
)
from app.intake.models import IntakeRequest, IntakeRequestType
from app.integrations.claude import ClaudeProvider
from app.integrations.claude import claude_client as _default_claude_client
from app.integrations.storage import StorageBackend, storage_service
from app.trademarks.clearance_service import CLEARANCE_MARKER_FIELD, TrademarkClearanceService
from app.trademarks.extracted_fields import find_jurisdiction, find_name
from app.trademarks.extraction.field_capture import extract_generic_records
from app.trademarks.extraction.vision import extract_journal_page
from app.trademarks.models import DocumentExtract, PortfolioDigest, Trademark
from app.trademarks.nice_classes import NICE_CLASS_BY_NUMBER, NICE_CLASSES, VALID_NICE_CLASSES
from app.trademarks.providers.similarity import search_similar as _search_similar
from app.trademarks.schemas import (
    ClassSuggestionRequest,
    ClassSuggestionResponse,
    DashboardMetrics,
    ExplainConflictRequest,
    ExplainConflictResponse,
    ExtractedRecord,
    ExtractRequest,
    ExtractResponse,
    IngestRequest,
    IngestResponse,
    IntakeSubmitRequest,
    IntegrationStatusEntry,
    IntegrationStatusResponse,
    IntegrationTestResponse,
    PortfolioDigestResponse,
    PortfolioRenewalsResponse,
    PortfolioStatsResponse,
    RenewalCalendarEntry,
    SearchSimilarRequest,
    SearchSimilarResponse,
    TrademarkCreate,
    TrademarkFromIntakeRequest,
    TrademarkUpdate,
    UploadDocumentResponse,
)

# --- NICE-class suggestion / conflict explanation / portfolio digest -------
# Ported from the standalone Trademark Suite module (see
# backend/TRADEMARKS_INTEGRATION.md) — originally OpenAI (gpt-4o-mini),
# now Claude via self.claude_client, same pattern as every other leaf
# LLM call in this app.

_CLASS_LIST_TEXT = "\n".join(f'{c["class"]}: {c["heading"]}' for c in NICE_CLASSES)

_CLASSIFY_SYSTEM_PROMPT = (
    "You classify a trademark's goods/services description into the single "
    "best-fit class from WIPO's Nice Classification. Here is the full list "
    f"of 45 classes:\n\n{_CLASS_LIST_TEXT}\n\n"
    "Given a trademark name and description, propose exactly one class "
    "number and a one-sentence reasoning grounded in the description given "
    "— no preamble, no legal disclaimers."
)

_CLASSIFY_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "nice_class": {
            "type": "string",
            "description": "The single best-fit class number, 1-45, as a string.",
        },
        "reasoning": {"type": "string", "description": "One short, concrete sentence."},
    },
    "required": ["nice_class", "reasoning"],
}

_EXPLAIN_SYSTEM_PROMPT = (
    "You are an IP paralegal assistant. Given the facts of one trademark "
    "similarity-search conflict, explain in 1-2 plain-English sentences "
    "whether it is a meaningful risk for someone about to file the searched "
    "mark, and why. Be concrete and grounded only in the facts given — refer "
    "to the match strength, status, and jurisdiction where relevant. No "
    "legal disclaimers, no preamble, just the explanation."
)

_DIGEST_SYSTEM_PROMPT = (
    "You write a short daily digest for in-house IP counsel, summarizing "
    "new trademark journal entries ingested into their monitoring system "
    "in the last 24 hours. "
    "Given the stats and sample marks below, write 2-4 plain-English "
    "sentences: how much came in, from where, and call out anything worth "
    "a human look (an unusual jurisdiction, a notably large batch, or a "
    "mark name that looks close to a well-known brand). No headers, no "
    "bullet points, no preamble."
)

_CLASSIFY_CACHE_TTL_SECONDS = 600  # 10 min — matches the typing-pause debounce reuse window
_EXPLAIN_CACHE_TTL_SECONDS = 60 * 60 * 24  # 24h — the same facts always produce the same explanation
_DIGEST_LOOKBACK_HOURS = 24


class _TTLCache:
    """Tiny in-process cache. Single-Uvicorn-worker only, same caveat the
    standalone module already carried — swap for Redis if this ever runs
    with multiple workers."""

    def __init__(self, ttl_seconds: int):
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        entry = self._store.get(key)
        if not entry:
            return None
        stored_at, value = entry
        if time.time() - stored_at > self._ttl:
            return None
        return value

    def set(self, key: str, value) -> None:
        self._store[key] = (time.time(), value)


_classify_cache = _TTLCache(_CLASSIFY_CACHE_TTL_SECONDS)
_explain_cache = _TTLCache(_EXPLAIN_CACHE_TTL_SECONDS)


def _cache_key(*parts: str) -> str:
    raw = "|".join(p.strip().lower() for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()

# Standard trademark renewal cycle used to backfill renewal_due_on when a
# filed_on date is known but no explicit renewal date was provided.
_DEFAULT_RENEWAL_CYCLE_DAYS = 365 * 10


def _embedding_text(*, name: str, description: str | None, goods_services: str | None) -> str:
    return " ".join(part for part in (name, description or "", goods_services or "") if part).strip()


class TrademarksService:
    def __init__(
        self,
        db: Session,
        *,
        storage: StorageBackend | None = None,
        reducto=None,
        claude_client: ClaudeProvider | None = None,
    ):
        self.db = db
        self.storage = storage or storage_service
        self.reducto = reducto
        self.claude_client = claude_client or _default_claude_client
        self.clearance = TrademarkClearanceService(db)

    def get_trademark_for_user(self, *, trademark_id: str, user: User) -> Trademark:
        trademark = self.db.get(Trademark, trademark_id)
        if trademark is None or trademark.org_id != user.org_id or trademark.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Trademark not found")
        self._attach_cases([trademark])
        return trademark

    def list_trademarks(self, *, org_id: str) -> list[Trademark]:
        rows = list(
            self.db.scalars(
                select(Trademark)
                .where(Trademark.org_id == org_id, Trademark.deleted_at.is_(None))
                .order_by(Trademark.updated_at.desc())
            ).all()
        )
        self._attach_cases(rows)
        return rows

    def _attach_cases(self, trademarks: list[Trademark]) -> None:
        """Sets the transient ``case`` attribute (where the clearance workflow
        stands) that TrademarkResponse serializes; marks without a request get None."""
        if not trademarks:
            return
        request_ids = [t.source_intake_request_id for t in trademarks if t.source_intake_request_id]
        cases = self.clearance.case_summaries(org_id=trademarks[0].org_id, request_ids=request_ids)
        for trademark in trademarks:
            trademark.case = cases.get(trademark.source_intake_request_id)

    def _create_trademark(
        self,
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
    ) -> Trademark:
        db = self.db
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
            workflow_state=TrademarkWorkflowState.INTAKE,
            source=TrademarkSource.INTAKE,
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
            after={"name": trademark.name, "status": trademark.status, "source": trademark.source},
        )
        return trademark

    def create_trademark(self, *, user: User, payload: TrademarkCreate) -> Trademark:
        db = self.db
        trademark = self._create_trademark(
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
        )
        db.commit()
        db.refresh(trademark)
        return trademark

    def create_trademark_from_intake(self, *, user: User, payload: IntakeSubmitRequest) -> Trademark:
        """The wizard: files the mark, then opens its clearance case - a Legal
        Intake request that the trademark clearance workflow runs on."""
        db = self.db
        jurisdictions = payload.jurisdictions or ["IN"]
        requisition = {
            key: value
            for key, value in (
                ("business_unit", payload.business_unit),
                ("business_unit_spoc", payload.business_unit_spoc),
            )
            if value
        }
        trademark = self._create_trademark(
            user=user,
            name=payload.name,
            description=payload.description,
            trademark_type=payload.trademark_type,
            jurisdiction=jurisdictions[0],
            jurisdictions=jurisdictions,
            nice_class=payload.nice_class,
            goods_services=payload.goods_services,
            filing_context={**(payload.filing_context or {}), **requisition} or None,
            filed_on=None,
            renewal_due_on=payload.renewal_due_on,
        )
        self.clearance.attach_run(org_id=user.org_id, query_id=payload.search_query_id, trademark_id=trademark.id)
        self._open_clearance_case(user=user, trademark=trademark, payload=payload)
        db.commit()
        db.refresh(trademark)
        self._attach_cases([trademark])
        return trademark

    def _open_clearance_case(self, *, user: User, trademark: Trademark, payload: IntakeSubmitRequest) -> None:
        """Opens the Legal Intake request that carries the clearance and starts
        the trademark clearance workflow on it. Delegates to the intake engine
        rather than re-implementing routing, SLA or steps, the same way
        notices/service.py::escalate does. If no clearance flow has been seeded
        for the org the request still exists and can be started from Legal Intake."""
        from app.intake.schemas import RequestCreate
        from app.intake.service import IntakeService
        from app.integrations.claude import run_coro_blocking
        from app.workflows.models import Workflow
        from app.workflows.service import start_flow

        db = self.db
        request_type = db.scalar(
            select(IntakeRequestType).where(
                IntakeRequestType.org_id == user.org_id,
                IntakeRequestType.key == "trademark",
                IntakeRequestType.active.is_(True),
            )
        )
        answers: dict = {"mark": trademark.name, CLEARANCE_MARKER_FIELD: True}
        if payload.business_unit_spoc:
            answers["business_unit_spoc"] = payload.business_unit_spoc
        details = [f'Trademark clearance for "{trademark.name}" ({trademark.trademark_type.replace("_", " ")}).']
        if payload.description:
            details.append(payload.description)
        if payload.goods_services:
            details.append(f"Goods & services: {payload.goods_services}")
        details.append(f"Jurisdictions: {', '.join(trademark.jurisdictions or [trademark.jurisdiction])}")
        if payload.nice_class:
            details.append(f"Nice class: {payload.nice_class}")
        if payload.business_unit_spoc:
            details.append(f"Business Unit SPOC: {payload.business_unit_spoc}")

        ticket = IntakeService(db).create_request(
            actor=user,
            payload=RequestCreate(
                type_label=request_type.name if request_type else "Trademark",
                request_type_id=request_type.id if request_type else None,
                subject=f"Trademark clearance: {trademark.name}"[:200],
                department=payload.business_unit,
                description="\n".join(details),
                field_values=answers,
                source="api",
            ),
        )
        trademark.source_intake_request_id = ticket["id"]
        self.clearance.log_event(
            user=user,
            trademark=trademark,
            event_type="trademark.case_opened",
            title=f"Clearance case opened ({ticket.get('ref') or ticket['id']})",
            details={"request_id": ticket["id"], "business_unit": payload.business_unit},
        )
        flow = next(
            (
                f
                for f in db.scalars(
                    select(Workflow)
                    .where(Workflow.org_id == user.org_id, Workflow.enabled.is_(True))
                    .order_by(Workflow.eval_order.asc())
                ).all()
                if (f.criteria or {}).get("field") == CLEARANCE_MARKER_FIELD
            ),
            None,
        )
        if flow is not None:
            request = db.get(IntakeRequest, ticket["id"])
            run_coro_blocking(lambda: start_flow(db, actor=user, request=request, flow=flow))

    def update_trademark(self, *, trademark: Trademark, user: User, payload: TrademarkUpdate) -> Trademark:
        db = self.db
        before = {"status": trademark.status, "workflow_state": trademark.workflow_state}
        updates = payload.model_dump(exclude_unset=True)
        for field, value in updates.items():
            setattr(trademark, field, value)
        trademark.updated_by_user_id = user.id
        db.flush()
        if trademark.status != before["status"]:
            self.clearance.log_event(
                user=user,
                trademark=trademark,
                event_type="trademark.status_changed",
                title=f"Status changed from {str(before['status']).title()} to {str(trademark.status).title()}",
                details={"from": before["status"], "to": trademark.status},
            )
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

    def dashboard_metrics(self, *, org_id: str, user: User | None = None) -> DashboardMetrics:
        db = self.db
        rows = db.scalars(
            select(Trademark).where(Trademark.org_id == org_id, Trademark.deleted_at.is_(None))
        ).all()
        by_status: dict[str, int] = {}
        for row in rows:
            by_status[row.status] = by_status.get(row.status, 0) + 1
        total = len(rows)
        active_prosecutions = sum(
            count
            for status_value, count in by_status.items()
            if status_value in {TrademarkStatus.FILED, TrademarkStatus.PROSECUTING, TrademarkStatus.OPPOSED}
        )
        now = datetime.now(UTC)
        upcoming_renewals = sum(
            1
            for row in rows
            if (due := self._effective_renewal_due(row)) is not None and due <= now + timedelta(days=90)
        )
        return DashboardMetrics(
            total_trademarks=total,
            active_prosecutions=active_prosecutions,
            upcoming_renewals=upcoming_renewals,
            by_status=by_status,
            cases_by_step=self.clearance.step_counts(
                org_id=org_id, request_ids=[r.source_intake_request_id for r in rows if r.source_intake_request_id]
            ),
            awaiting_my_action=self.clearance.awaiting_action(user=user) if user is not None else [],
        )

    @staticmethod
    def _effective_renewal_due(trademark: Trademark) -> datetime | None:
        """The date the Calendar/Dashboard/Reports treat as this
        trademark's renewal due date: the stored renewal_due_on when set,
        else a fallback projection of filed_on + the source module's
        statutory 10-year term (ported from its digest.py RENEWAL_TERM_YEARS
        / _add_years) so records that only have a filing date aren't
        silently invisible to every renewal view."""
        if trademark.renewal_due_on is not None:
            return trademark.renewal_due_on
        if trademark.filed_on is None:
            return None
        filed = trademark.filed_on
        try:
            return filed.replace(year=filed.year + 10)
        except ValueError:
            # Feb 29 filed in a leap year, +10 years lands on a non-leap one.
            return filed.replace(month=2, day=28, year=filed.year + 10)

    def list_portfolio_renewals(self, *, org_id: str) -> PortfolioRenewalsResponse:
        """Every trademark with a computable renewal date, sorted
        soonest-due first, plus portfolio-level totals — ports the source
        module's GET /api/portfolio/renewals (digest.py) verbatim, computed
        against this org's rows instead of a single-tenant table scan."""
        rows = self.db.scalars(
            select(Trademark).where(Trademark.org_id == org_id, Trademark.deleted_at.is_(None))
        ).all()
        now = datetime.now(UTC)
        records: list[RenewalCalendarEntry] = []
        skipped = 0
        for row in rows:
            due = self._effective_renewal_due(row)
            if due is None:
                skipped += 1
                continue
            records.append(
                RenewalCalendarEntry(
                    id=row.id,
                    name=row.name,
                    status=row.status,
                    jurisdiction=row.jurisdiction,
                    nice_class=row.nice_class,
                    filed_on=row.filed_on,
                    description=row.description,
                    source=row.source,
                    renewal_due_on=due,
                    overdue=due < now,
                )
            )
        records.sort(key=lambda r: r.renewal_due_on)
        overdue_count = sum(1 for r in records if r.overdue)
        due_next_90d = sum(
            1 for r in records if not r.overdue and (r.renewal_due_on - now) <= timedelta(days=90)
        )
        return PortfolioRenewalsResponse(
            records=records,
            total_with_dates=len(records),
            skipped_unparseable=skipped,
            overdue_count=overdue_count,
            due_next_90d=due_next_90d,
        )

    def list_at_risk(self, *, org_id: str) -> list[Trademark]:
        """Trademarks in the source module's at-risk statuses — backs the
        Reports "Renewal & risk pipeline" panel (RiskPanel in its
        reports/page.tsx), same filter, same two statuses."""
        return self.db.scalars(
            select(Trademark).where(
                Trademark.org_id == org_id,
                Trademark.deleted_at.is_(None),
                Trademark.status.in_([TrademarkStatus.RENEWAL_PENDING, TrademarkStatus.LAPSED]),
            )
        ).all()

    # --- Search-similar --------------------------------------------------

    def search_similar(self, *, user: User, request: SearchSimilarRequest) -> SearchSimilarResponse:
        response = _search_similar(self.db, org_id=user.org_id, request=request, settings=default_settings)
        self.clearance.save_search_run(user=user, request=request, response=response)
        return response

    # --- Document extraction pipeline -------------------------------------

    async def save_uploaded_document(self, *, user: User, file: UploadFile) -> UploadDocumentResponse:
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

        stored = self.storage.save_bytes(
            org_id=user.org_id, filename=file.filename, mime_type="application/pdf", content=content
        )
        return UploadDocumentResponse(doc_id=stored.storage_key, filename=stored.filename, total_pages=total_pages)

    async def extract_fields(self, *, user: User, request: ExtractRequest) -> ExtractResponse:
        content = self.storage.read_bytes(request.doc_id)
        filename = request.doc_id.rsplit("/", 1)[-1]
        warnings: list[str] = []

        if request.template == DocumentExtractTemplate.IP_INDIA_JOURNAL:
            records: list[ExtractedRecord] = []
            for page_number in range(request.page_start, request.page_end + 1):
                try:
                    entries = await extract_journal_page(content, page_number, claude_client=self.claude_client)
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
                reducto=self.reducto,
            )
            for record in records:
                warnings.extend(record.warnings)

        return ExtractResponse(doc_id=request.doc_id, records=records, warnings=warnings)

    def ingest_extraction(self, *, user: User, request: IngestRequest) -> IngestResponse:
        """Stores every extracted record in document_extract and nowhere else -
        no trademark row is created from it, so extractions never appear in
        My trademarks. They are found through the similarity search instead
        (providers/similarity.py). A record with no non-empty field has nothing
        to store or embed, so it is skipped."""
        db = self.db
        record_ids: list[str] = []

        for record in request.records:
            embedding_text = " ".join(
                f"{k}: {v}" for k, v in record.fields.items() if k != "product_image_paths" and v
            )
            if not embedding_text:
                continue

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
                embedding_text=embedding_text,
                embedding=embed_texts([embedding_text])[0],
            )
            db.add(extract_row)
            record_ids.append(extract_row.id)

        write_audit_log(
            db,
            action="trademark.document_extract.ingest",
            resource_type="document_extract",
            org_id=user.org_id,
            actor_user_id=user.id,
            after={"ingested_count": len(record_ids), "record_ids": record_ids},
        )
        db.commit()
        return IngestResponse(ingested_count=len(record_ids), record_ids=record_ids)

    def get_integration_status(self, *, settings: Settings) -> IntegrationStatusResponse:
        db = self.db
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

    def test_integration(self, *, service_name: str, settings: Settings) -> IntegrationTestResponse:
        import time

        db = self.db
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

    # --- NICE-class suggestion, conflict explanation, portfolio digest/stats ---

    async def suggest_nice_class(
        self, *, org_id: str, request: ClassSuggestionRequest
    ) -> ClassSuggestionResponse:
        cache_key = _cache_key(request.name, request.description)
        cached = _classify_cache.get(cache_key)
        if cached is not None:
            nice_class, reasoning = cached
            return ClassSuggestionResponse(
                nice_class=nice_class, heading=NICE_CLASS_BY_NUMBER.get(nice_class, ""),
                reasoning=reasoning, cached=True,
            )
        enforce_daily_token_cap(org_id)
        response = await self.claude_client.complete_structured(
            system_prompt=_CLASSIFY_SYSTEM_PROMPT,
            user_prompt=f'Trademark name: "{request.name}"\nDescription: "{request.description}"',
            tool_name="suggest_nice_class",
            input_schema=_CLASSIFY_INPUT_SCHEMA,
            max_tokens=120,
            temperature=0.1,
        )
        if not response.tool_use_blocks:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Classifier returned no result")
        parsed = response.tool_use_blocks[0].get("input") or {}
        nice_class = str(parsed.get("nice_class", "")).strip()
        reasoning = str(parsed.get("reasoning", "")).strip()
        if nice_class not in VALID_NICE_CLASSES:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"Classifier returned an invalid class number: {nice_class!r}"
            )
        _classify_cache.set(cache_key, (nice_class, reasoning))
        return ClassSuggestionResponse(
            nice_class=nice_class, heading=NICE_CLASS_BY_NUMBER.get(nice_class, ""),
            reasoning=reasoning, cached=False,
        )

    async def explain_conflict(
        self, *, org_id: str, request: ExplainConflictRequest
    ) -> ExplainConflictResponse:
        cache_key = _cache_key(
            request.trademark_name, request.source, request.conflict_name,
            str(request.jurisdiction), str(request.status), str(request.match_score), str(request.risk_level),
        )
        cached = _explain_cache.get(cache_key)
        if cached is not None:
            return ExplainConflictResponse(explanation=cached, cached=True)
        enforce_daily_token_cap(org_id)
        lines = [
            f'Searched mark: "{request.trademark_name}"'
            + (f" — {request.description}" if request.description else ""),
            f'Conflict: "{request.conflict_name}" (source: {request.source})',
        ]
        if request.match_score is not None:
            lines.append(f"Match strength: {round(request.match_score * 100)}%")
        if request.risk_level:
            lines.append(f"Computed risk level: {request.risk_level}")
        if request.status:
            lines.append(f"Status: {request.status}")
        if request.jurisdiction:
            lines.append(f"Jurisdiction: {request.jurisdiction}")
        response = await self.claude_client.complete_text(
            system_prompt=_EXPLAIN_SYSTEM_PROMPT, user_prompt="\n".join(lines), max_tokens=120, temperature=0.3,
        )
        explanation = "".join(
            b.get("text", "") for b in response.content_blocks if b.get("type") == "text"
        ).strip()
        _explain_cache.set(cache_key, explanation)
        return ExplainConflictResponse(explanation=explanation, cached=False)

    def _gather_digest_stats(self, *, org_id: str, lookback_hours: int) -> tuple[dict, list[dict]]:
        cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
        rows = self.db.scalars(
            select(DocumentExtract)
            .where(DocumentExtract.org_id == org_id, DocumentExtract.created_at >= cutoff)
            .order_by(DocumentExtract.created_at.desc())
        ).all()
        by_jurisdiction: dict[str, int] = {}
        by_source_file: dict[str, int] = {}
        samples: list[dict] = []
        for row in rows:
            fields = row.extracted_fields or {}
            jurisdiction = find_jurisdiction(fields) or "Unspecified"
            by_jurisdiction[jurisdiction] = by_jurisdiction.get(jurisdiction, 0) + 1
            by_source_file[row.source_filename] = by_source_file.get(row.source_filename, 0) + 1
            if len(samples) < 8 and (mark_name := find_name(fields)):
                samples.append({"name": mark_name, "jurisdiction": jurisdiction})
        stats = {
            "new_extracts_count": len(rows),
            "by_jurisdiction": by_jurisdiction,
            "by_source_file": by_source_file,
        }
        return stats, samples

    async def get_or_create_portfolio_digest(
        self, *, user: User, force: bool = False
    ) -> PortfolioDigestResponse:
        """One row per org per day — see PortfolioDigest's docstring. Summarizes
        the document extracts ingested in the last 24 hours, as the standalone
        module's digest does."""
        db = self.db
        org_id = user.org_id
        today = datetime.now(UTC).date()
        if not force:
            existing = db.scalar(
                select(PortfolioDigest).where(
                    PortfolioDigest.org_id == org_id, PortfolioDigest.digest_date == today
                )
            )
            if existing is not None:
                return PortfolioDigestResponse(
                    digest_date=existing.digest_date.isoformat(), summary=existing.summary,
                    stats=existing.stats, generated=False,
                )

        stats, samples = self._gather_digest_stats(org_id=org_id, lookback_hours=_DIGEST_LOOKBACK_HOURS)
        if stats["new_extracts_count"] == 0:
            # Nothing new — skip the LLM call entirely, same cost discipline
            # as the standalone module.
            summary = "No new document extracts were ingested in the last 24 hours — nothing to report today."
        else:
            enforce_daily_token_cap(org_id)
            lines = [
                f"New document extracts in the last {_DIGEST_LOOKBACK_HOURS}h: {stats['new_extracts_count']}",
                f"By jurisdiction: {stats['by_jurisdiction']}",
                f"By source file: {stats['by_source_file']}",
            ]
            if samples:
                lines.append("Sample of newly ingested marks:")
                lines.extend(f"- {s['name']} ({s['jurisdiction']})" for s in samples)
            response = await self.claude_client.complete_text(
                system_prompt=_DIGEST_SYSTEM_PROMPT, user_prompt="\n".join(lines), max_tokens=200, temperature=0.4,
            )
            summary = "".join(
                b.get("text", "") for b in response.content_blocks if b.get("type") == "text"
            ).strip()

        row = db.scalar(
            select(PortfolioDigest).where(PortfolioDigest.org_id == org_id, PortfolioDigest.digest_date == today)
        )
        if row is None:
            row = PortfolioDigest(
                id=new_uuid(), org_id=org_id, digest_date=today, summary=summary, stats=stats,
                created_by_user_id=user.id, updated_by_user_id=user.id,
            )
            db.add(row)
        else:
            row.summary = summary
            row.stats = stats
            row.updated_by_user_id = user.id
        db.commit()
        db.refresh(row)
        return PortfolioDigestResponse(
            digest_date=row.digest_date.isoformat(), summary=row.summary, stats=row.stats, generated=True
        )

    def portfolio_stats(self, *, org_id: str) -> PortfolioStatsResponse:
        """Pure SQL aggregates for the Reports page — no LLM spend, safe to
        call on every page load (unlike the digest, deliberately once-a-day)."""
        db = self.db
        rows = db.scalars(
            select(Trademark).where(Trademark.org_id == org_id, Trademark.deleted_at.is_(None))
        ).all()
        by_status: dict[str, int] = {}
        by_jurisdiction: dict[str, int] = {}
        by_nice_class: dict[str, int] = {}
        now = datetime.now(UTC)
        filed_last_30d = 0
        filed_last_90d = 0
        by_decision: dict[str, int] = {}
        for row in rows:
            by_status[row.status] = by_status.get(row.status, 0) + 1
            if row.decision:
                by_decision[row.decision] = by_decision.get(row.decision, 0) + 1
            for jurisdiction in row.jurisdictions or [row.jurisdiction]:
                jurisdiction = (jurisdiction or "").strip()
                if jurisdiction:
                    by_jurisdiction[jurisdiction] = by_jurisdiction.get(jurisdiction, 0) + 1
            nice_class = (row.nice_class or "").strip()
            if nice_class:
                by_nice_class[nice_class] = by_nice_class.get(nice_class, 0) + 1
            if row.created_at:
                age = now - row.created_at
                if age <= timedelta(days=30):
                    filed_last_30d += 1
                if age <= timedelta(days=90):
                    filed_last_90d += 1
        upcoming_cutoff = now + timedelta(days=90)
        upcoming_renewals_90d = sum(
            1
            for row in rows
            if (due := self._effective_renewal_due(row)) is not None and due <= upcoming_cutoff
        )
        return PortfolioStatsResponse(
            total=len(rows), by_status=by_status, by_jurisdiction=by_jurisdiction, by_nice_class=by_nice_class,
            filed_last_30d=filed_last_30d, filed_last_90d=filed_last_90d,
            upcoming_renewals_90d=upcoming_renewals_90d,
            renewal_pending=by_status.get(TrademarkStatus.RENEWAL_PENDING, 0),
            lapsed=by_status.get(TrademarkStatus.LAPSED, 0),
            by_step=self.clearance.step_counts(
                org_id=org_id, request_ids=[r.source_intake_request_id for r in rows if r.source_intake_request_id]
            ),
            by_decision=by_decision,
        )

    # --- Legal Intake bridge ----------------------------------------------

    def create_trademark_from_intake_request(
        self, *, intake_request_id: str, user: User, overrides: TrademarkFromIntakeRequest
    ) -> Trademark:
        """"Continue as Trademark" — the counterpart to
        intake/drafting.py's draft_contract_for_request for the trademark
        module. Reuses the intake ticket's own subject/description; nothing
        the ticket doesn't already capture is asked for beyond `overrides`.
        Does not touch the intake ticket's own routing/triage/SLA state —
        same "the ticket stays the unit of work, the created record is the
        system of record" split already established for
        Notice.escalated_intake_request_id (0035_notice_response_escalation)."""
        db = self.db
        req = db.get(IntakeRequest, intake_request_id)
        if req is None or req.org_id != user.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Intake request not found")

        # A request carries one mark: a second "Continue as Trademark" (or a request
        # the wizard already opened a case for) returns it instead of duplicating.
        existing = db.scalar(
            select(Trademark).where(
                Trademark.org_id == user.org_id,
                Trademark.source_intake_request_id == req.id,
                Trademark.deleted_at.is_(None),
            )
        )
        if existing is not None:
            return existing

        jurisdictions = overrides.jurisdictions or ["IN"]
        trademark = self._create_trademark(
            user=user,
            name=req.subject or req.type_label,
            description=req.description,
            trademark_type=overrides.trademark_type,
            jurisdiction=jurisdictions[0],
            jurisdictions=jurisdictions,
            nice_class=overrides.nice_class,
            goods_services=overrides.goods_services,
            filing_context=overrides.filing_context,
            filed_on=None,
            renewal_due_on=overrides.renewal_due_on,
        )
        trademark.source_intake_request_id = req.id
        db.commit()
        db.refresh(trademark)
        return trademark
