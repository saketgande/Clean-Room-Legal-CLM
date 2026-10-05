"""Turn an intake request into a real draft contract.

This is the bridge from intake into the contract lifecycle. It picks a template
for the request's document type (one per playbook: NDA, MSA, Consultancy, SoW,
Vendor, SaaS, DPA — see app/drafting_templates), renders it
with the request's counterparty, pushes it through the normal contract-intake
pipeline (storage → text snapshot → AI analysis → lifecycle) by reusing
``create_contract_from_upload``, and links the resulting contract back to the
request. After this, the intake ticket has a real contract to open — with its
own lifecycle, risk score and playbook redlines.

Only request types that map to a draftable document get a template; litigation
and general questions keep the text-recommendation path (they are not contracts).
"""

from __future__ import annotations

import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from starlette.datastructures import Headers, UploadFile

from app.contract_files.service import ContractFilesService
from app.core.audit import write_audit_log, write_timeline_event
from app.core.config import settings
from app.core.database import utcnow
from app.drafting_templates.service import TEMPLATES as _DOC_TYPES
from app.drafting_templates.service import DraftingTemplateService, default_body
from app.intake.models import IntakeRequest
from app.organizations.models import Organization

# The templates themselves live in app/drafting_templates: a shipped default
# per key (defaults/<key>.txt), each written to pass the playbook that reviews
# its contract type, plus any version an org has saved on the Templates page.
# _DOC_TYPES is that table — contract_type is the string stored on the
# Contract, which picks the reviewing playbook (and the NDA fast-lane, which
# matches "nda").


# Which template each form (or kind of new agreement) drafts. Selling to a
# customer and "something else" have no template: Legal drafts them.
_FORM_DOC = {"sow": "sow", "dpa": "dpa"}
_KIND_DOC = {"NDA": "nda", "Services (MSA)": "msa", "Consultancy": "consultancy",
             "Buying from a vendor": "vendor", "Software or SaaS": "saas"}


def resolve_doc_type(request: IntakeRequest) -> str | None:
    """Which draftable document (if any) this request maps to. Keyword-first so
    it works regardless of the classifier; falls back to the classified category.
    Returns None for request types that are not contracts (litigation, general).

    Without a form, the structured `agreement_type`/`agreement_category` fields
    are read alongside the type label and description — a generic type_label
    carries no keyword on its own, so a type captured only as a structured
    field was otherwise missed."""
    from app.intake.agents import classify

    # A form says it outright: the New agreement form's kind of agreement, or the
    # SoW / DPA form itself. Change and records forms never draft a new paper.
    fv = request.field_values or {}
    form = fv.get("request_form")
    if form in _FORM_DOC:
        return _FORM_DOC[form]
    if form == "new_agreement":
        return _KIND_DOC.get(str(fv.get("agreement_type") or ""))
    if form:
        return None
    from app.intake.agents import NON_DRAFTABLE_TYPES

    if (request.type_label or "").strip() in NON_DRAFTABLE_TYPES:
        return None  # a question that mentions a vendor or an MSA is still a question
    field_hint = f"{fv.get('agreement_type') or ''} {fv.get('agreement_category') or ''}"
    text = f"{request.type_label} {request.description or ''} {field_hint}".lower()
    category = (classify(request.type_label, request.description or "").get("category") or "").lower()

    # A privacy incident or breach names DPAs, vendors and GDPR, but it's a response
    # to run, not a document to draft ("breach notification" terms in a DPA are fine).
    if re.search(r"\bincident\b|\bbreach\b(?!\s+notif)", text):
        return None
    if re.search(r"\b(nda|non-disclosure|non disclosure|confidentiality agreement)\b", text) or category == "nda":
        return "nda"
    # A DPA is the document itself; "data protection", GDPR or the privacy category
    # describe a privacy matter, which isn't something to draft.
    if re.search(r"\b(dpa|data processing (agreement|addendum))\b", text):
        return "dpa"
    if re.search(r"\b(msa|master service|master services|services agreement|statement of work|sow)\b", text):
        return "msa"
    if re.search(r"\b(vendor|supplier|procurement)\b", text) or category == "vendor":
        return "vendor"
    return None


def _primary_counterparty(r: IntakeRequest) -> str:
    from app.intake.screening import gather_parties

    parties = gather_parties(r)
    primary = next((p for p in parties if p.get("role") == "counterparty"), None) or (
        parties[0] if parties else None
    )
    name = (primary or {}).get("name") if primary else None
    return (name or "").strip() or "the Counterparty"


def template_values(doc_type: str, *, company: str, counterparty: str, effective: str,
                    fields: dict | None = None) -> dict:
    """Every placeholder a template may use (drafting_templates.PLACEHOLDERS),
    filled from the request's answers. A blank answer falls back to the
    playbook's preferred position, so a bare request still drafts paper the
    playbook accepts: NDA term 3 years with 5 years' survival, payment in 45
    days (30 for consultancy)."""
    f = fields or {}
    one_way = f.get("nda_kind") == "One-way"
    purpose = str(f.get("purpose") or "").strip()
    nda_term = str(f.get("nda_term") or "").strip()
    survival = str(f.get("survival_years") or "").strip()
    if one_way:
        disc, recv = (counterparty, company) if f.get("nda_direction") == "They share" else (company, counterparty)
        direction_recital = (
            f"This is a one-way disclosure in which {disc} is the Disclosing Party "
            f"and {recv} is the Receiving Party."
        )
    else:
        direction_recital = "Each Party may act as both Disclosing Party and Receiving Party."

    facts = request_facts(f)
    end = facts.get("expiration_date")
    value = facts.get("value_amount")
    term = f"continues until {end.isoformat()}" if end else "continues until terminated"
    if end and f.get("term") == "Renews automatically":
        term += (f", and then renews automatically for successive periods of {f.get('renewal_term') or '1 year'}"
                 f" unless either Party gives {f.get('notice_days') or '60 days'}' written notice of non-renewal")
    default_days = "30 days" if doc_type == "consultancy" else "45 days"
    return {
        "company": company, "counterparty": counterparty, "effective_date": effective,
        "governing_law": str(f.get("governing_law") or "").strip() or "the State of Delaware",
        "agreement_term": term,
        "payment_days": str(f.get("payment_terms") or default_days).split()[0],
        "value_sentence": (f" The total value of this Agreement shall not exceed {facts['currency']} {value:,.2f}."
                           if value else ""),
        "nda_kind_title": "ONE-WAY " if one_way else "MUTUAL ",
        "nda_kind_word": "One-Way" if one_way else "Mutual",
        "purpose_phrase": purpose or "explore a potential business relationship",
        "direction_recital": direction_recital,
        "term_clause": f"continues for the term of {nda_term}" if nda_term else "continues for three (3) years",
        "survival_clause": f"continue for {survival} year(s)" if survival else "continue for five (5) years",
    }


def render_document(doc_type: str, *, company: str, counterparty: str, effective: str,
                    fields: dict | None = None, body: str | None = None) -> str:
    """Fill a template. ``body`` is the org's saved wording (Templates page);
    without it, the shipped default for ``doc_type``."""
    text = body if body is not None else default_body(doc_type)
    return text.format(**template_values(doc_type, company=company, counterparty=counterparty,
                                         effective=effective, fields=fields))


# Form answers that are contract facts. The forms ask these outright, so they are
# the stated deal — the AI metadata extractor only fills what is still blank.
_START_KEYS = ("start_date", "services_start")
_END_KEYS = ("end_date", "services_end")


def _as_date(raw) -> date | None:
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except ValueError:
        return None


def request_facts(fv: dict) -> dict:
    """The contract facts a request's form answers state, typed for the Contract row."""
    facts: dict = {}
    try:
        value = Decimal(str(fv.get("value") or "").replace(",", "").strip())
        if value > 0:
            facts["value_amount"] = value
            facts["currency"] = fv.get("currency") or settings.default_currency
    except InvalidOperation:
        pass
    for column, keys in (("effective_date", _START_KEYS), ("expiration_date", _END_KEYS)):
        found = next((d for d in (_as_date(fv.get(k)) for k in keys if fv.get(k)) if d), None)
        if found:
            facts[column] = found
    return facts


def apply_request_facts(contract, request: IntakeRequest) -> None:
    """Write the form's facts, parties and the auto-review flag onto a new
    contract. Runs inside create_contract_from_upload before the AI jobs are
    dispatched, so they read the facts instead of racing them. Precedence is
    person > form > AI: a value a person typed is never replaced here."""
    fv = request.field_values or {}
    contract.counterparty_id = request.counterparty_id
    contract.legal_entity_id = request.legal_entity_id
    meta = dict(contract.metadata_json or {})
    sources = dict(meta.get("field_sources") or {})
    for column, value in request_facts(fv).items():
        if sources.get(column) != "user":
            setattr(contract, column, value)
            sources[column] = "form"
    # The contract type names the kind of agreement precisely, so its playbook is
    # the one picked for the automatic review (pick_playbook_for_contract).
    from app.playbooks.library import CONTRACT_TYPE_OF_FORM, CONTRACT_TYPE_OF_KIND

    kind_type = (CONTRACT_TYPE_OF_KIND.get(fv.get("agreement_type")) if fv.get("request_form") == "new_agreement"
                 else CONTRACT_TYPE_OF_FORM.get(fv.get("request_form")))
    if kind_type and sources.get("contract_type") != "user":
        contract.contract_type = kind_type
        sources["contract_type"] = "form"
    meta["field_sources"] = sources
    if fv.get("term"):
        meta["renewal"] = {"term": fv["term"], "renews_for": fv.get("renewal_term"), "notice": fv.get("notice_days")}
    meta["intake_request_id"] = request.id
    # Once clause extraction finishes, the job runner runs risk + the matching
    # playbook so REVIEW is ready without a click.
    meta["auto_review_pending"] = True
    # A parent the requester picked (amendment, renewal, SoW, DPA…) makes lineage
    # a STATED fact the graph can assert, not a same-counterparty guess.
    parent_id = str(fv.get("parent_contract_id") or "").strip()
    if parent_id:
        meta["parent_contract_id"] = parent_id
        if fv.get("parent_contract_title"):
            meta["parent_contract_title"] = fv["parent_contract_title"]
    contract.metadata_json = meta
    # Their signer is a party on the contract, so signature allows their email.
    if fv.get("cp_signer_email") and getattr(contract, "counterparty_name", None):
        from sqlalchemy.orm import object_session

        from app.contracts.models import ContractParty

        session = object_session(contract)
        session.flush()  # the contract's id
        session.add(ContractParty(
            org_id=contract.org_id, contract_id=contract.id, name=contract.counterparty_name,
            party_type="counterparty", contact_email=str(fv["cp_signer_email"]).strip(),
            metadata_json={"signer_name": fv.get("cp_signer_name"), "source": "request_form"},
        ))


def _custom_shell(*, company: str, counterparty: str, fields: dict, label: str) -> str:
    """A starting canvas for a bespoke ('custom') draft — the captured intake
    details plus a clear attorney-fill body. NOT the standard template; the
    attorney writes the real terms in the CLM editor. Its whole point is to give
    the run a real, VISIBLE contract to work on from step one."""
    fv = fields or {}
    lines = [
        f"{label.upper()} — CUSTOM DRAFT",
        "",
        f"Between {company} and {counterparty or '[Counterparty]'}.",
        "",
        "— CAPTURED REQUIREMENTS (from the intake request) —",
    ]
    for k, v in fv.items():
        if str(k).startswith("_") or v in (None, "", []):
            continue
        lines.append(f"  • {k.replace('_', ' ')}: {v}")
    lines += [
        "",
        "— DRAFT —",
        "[This is a bespoke draft. Write the custom terms below to fit the",
        " requirements above. Do NOT use the standard template.]",
        "",
    ]
    return "\n".join(lines)


def _change_facts(fv: dict) -> tuple[dict, dict]:
    """What a finished change request writes onto its contract: (column values,
    metadata notes). Only answers the form asked for this change are used."""
    form, cols, notes = fv.get("request_form"), {}, {}
    money_key = {"amendment": "new_value", "renewal": "renew_value"}.get(form)
    if money_key and fv.get(money_key) not in (None, ""):
        try:
            cols["value_amount"] = Decimal(str(fv[money_key]).replace(",", ""))
            cols["currency"] = fv.get("currency") or settings.default_currency
        except InvalidOperation:
            pass
    end = {"amendment": "new_end_date", "renewal": "renew_end", "termination": "termination_date"}.get(form)
    if end and _as_date(fv.get(end)):
        cols["expiration_date"] = _as_date(fv[end])
    if form == "termination":
        notes["terminated"] = {"on": fv.get("termination_date"), "grounds": fv.get("grounds")}
    if form == "novation" and fv.get("incoming_party"):
        cols["counterparty_name"] = str(fv["incoming_party"])[:255]
        notes["novated"] = {"on": fv.get("effective_date"), "transferring": fv.get("transferring")}
    return cols, notes


class DraftingService:
    """Turns an intake request into a real, analysed draft contract, and writes a
    finished change request back onto its contract.

    Part of the DI migration (see backend/DI_MIGRATION.md). Constructed with
    a ``db`` session; ``files`` defaults to a ``ContractFilesService`` built
    from the same session (composition — drafting reuses the normal
    contract-intake pipeline). Templates, the doc-type resolver, request facts
    and the other pure rendering helpers stay module-level above.
    """

    def __init__(self, db, *, files: ContractFilesService | None = None):
        self.db = db
        self.files = files or ContractFilesService(db)

    def _party_details(self, request: IntakeRequest, org_name: str) -> tuple[str, str, list[str]]:
        """(our entity's name, the counterparty's name, extra lines for an AI draft).

        The register records picked on the request win: they carry the legal name,
        jurisdiction, address and signatory a contract needs. Requests filed before
        the register existed fall back to the typed names, then the organisation."""
        from app.parties import service as parties

        db = self.db
        fv = request.field_values or {}
        entity = parties.get(db, org_id=request.org_id, kind="legal_entity", record_id=request.legal_entity_id)
        cp = parties.get(db, org_id=request.org_id, kind="counterparty", record_id=request.counterparty_id)
        company = entity.name if entity else (str(fv.get("entity") or "").strip() or org_name)
        counterparty = cp.name if cp else (str(fv.get("counterparty") or "").strip() or _primary_counterparty(request))
        lines: list[str] = []
        if entity:
            bits = [f"incorporated in {entity.jurisdiction}" if entity.jurisdiction else "",
                    f"registered office: {entity.registered_address}" if entity.registered_address else "",
                    f"authorised signatory: {entity.authorised_signatory}" if entity.authorised_signatory else ""]
            lines.append(f"Our contracting entity: {entity.name}" + "".join(f"; {b}" for b in bits if b) + ".")
        if cp:
            bits = [f"incorporated in {cp.jurisdiction}" if cp.jurisdiction else "",
                    f"address: {cp.address}" if cp.address else "",
                    f"notices / signature email: {cp.contact_email}" if cp.contact_email else ""]
            lines.append(f"Counterparty: {cp.name}" + "".join(f"; {b}" for b in bits if b) + ".")
        return company, counterparty, lines

    async def _ai_draft_text(self, *, actor, request: IntakeRequest, company: str, counterparty: str, label: str,
                             fields: dict, party_lines: list[str] | tuple = ()) -> str | None:
        """Generate a REAL bespoke draft with the drafting skill (the same
        contract_docx_generation the assistant uses), grounded on the intake
        request's type, counterparty and captured requirements. Returns the rendered
        body text, or None so the caller can fall back to the skeleton if the model
        is unavailable — intake must never hard-fail on a drafting miss."""
        import logging

        db = self.db
        reqs = [
            f"- {str(k).replace('_', ' ')}: {v}"
            for k, v in (fields or {}).items()
            if not str(k).startswith("_") and v not in (None, "", [])
        ]
        purpose = (request.description or "").strip()
        instructions = "\n".join(
            line
            for line in [
                f"Draft a {label} between {company} (our organization) and {counterparty or '[Counterparty]'}.",
                *party_lines,
                f"Matter type: {request.type_label}.",
                f"Purpose / context: {purpose}" if purpose else "",
                "Captured requirements from the intake request:" if reqs else "",
                *reqs,
                (
                    "Produce complete, professional contract sections with real operative "
                    "language a lawyer can review and refine. Where a term wasn't specified, "
                    "use a sensible market-standard default and record it as an assumption."
                ),
            ]
            if line
        )
        try:
            from app.ai.controller import ai_controller
            from app.ai.tool_runtime import _render_structured_contract_docx

            drafted = await ai_controller.run_structured_skill(
                db,
                skill_name="contract_docx_generation",
                org_id=actor.org_id,
                created_by_user_id=actor.id,
                input_payload={"title": f"{label} — {counterparty or 'Counterparty'}", "instructions": instructions},
                commit=False,
            )
            if drafted is None or not getattr(drafted, "sections", None):
                return None
            body_text, _content = _render_structured_contract_docx(
                title=getattr(drafted, "title", None) or f"{label} — {counterparty or 'Counterparty'}",
                sections=[(s.heading, s.body) for s in drafted.sections],
                assumptions=list(getattr(drafted, "assumptions", []) or []),
            )
            return body_text
        except Exception:
            logging.getLogger(__name__).warning(
                "intake AI drafting failed for request %s; falling back to skeleton", request.id, exc_info=True
            )
            return None

    async def draft_contract_for_request(
        self, *, actor, request: IntakeRequest, http_request_id: str | None = None, custom: bool = False
    ):
        """Render the right template for the request and create a real, analysed
        contract linked back to it. Idempotent: returns the existing contract if
        already drafted. Raises HTTPException(422) if the request has no template."""
        from fastapi import HTTPException, status

        from app.contracts.models import Contract

        db = self.db
        if request.contract_id:
            existing = db.get(Contract, request.contract_id)
            if existing is not None:
                return existing

        doc_type = resolve_doc_type(request)
        if doc_type is None and not custom:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "This request type has no draft template — handle it manually.",
            )
        # A custom draft doesn't need a template doc_type — it gets a generic shell.
        spec = _DOC_TYPES[doc_type] if doc_type else {"label": (request.type_label or "Agreement"), "contract_type": "other"}

        org = db.get(Organization, actor.org_id)
        fv = request.field_values or {}
        # Our side and theirs come from the register records picked on the request.
        company, counterparty, party_lines = self._party_details(
            request, org.name if org and org.name else "Company")
        effective = str(fv.get("start_date") or fv.get("services_start") or "").strip() or utcnow().date().isoformat()
        if custom:
            # Real AI generation for bespoke drafts (was a placeholder skeleton).
            # Skeleton only survives as the safety net if the model is unavailable.
            text = await self._ai_draft_text(
                actor=actor, request=request, company=company,
                counterparty=counterparty, label=spec["label"], fields=fv, party_lines=party_lines,
            )
            if not text:
                text = _custom_shell(company=company, counterparty=counterparty, fields=fv, label=spec["label"])
        else:
            # Only the NDA form asks governing law; for everything else our
            # contracting entity's jurisdiction is the sensible law, not the
            # template's last-resort default (Delaware for an Indian entity drew a
            # high finding on every MSA and SaaS draft).
            if not str(fv.get("governing_law") or "").strip() and request.legal_entity_id:
                from app.parties.models import LegalEntity

                entity = db.get(LegalEntity, request.legal_entity_id)
                if entity is not None and (entity.jurisdiction or "").strip():
                    fv = {**fv, "governing_law": entity.jurisdiction.strip()}
            # The org's own wording from the Templates page, else the shipped default.
            body = DraftingTemplateService(db).body_for(org_id=actor.org_id, key=doc_type)
            text = render_document(doc_type, company=company, counterparty=counterparty, effective=effective,
                                   fields=fv, body=body)
        # One-way NDAs get a clearer label than the generic template label.
        label = spec["label"]
        if doc_type == "nda" and fv.get("nda_kind") == "One-way":
            label = "One-Way NDA"
        if custom:
            label = f"{label} (Custom draft)"
        title = f"{label} — {counterparty}"

        data = text.encode("utf-8")
        upload = UploadFile(
            file=io.BytesIO(data),
            size=len(data),
            filename=f"{spec['label']} - {counterparty}.txt",
            headers=Headers({"content-type": "text/plain"}),
        )
        result = await self.files.create_contract_from_upload(
            upload=upload,
            user=actor,
            title=title,
            counterparty_name=counterparty,
            contract_type=spec["contract_type"],
            request_id=http_request_id,
            on_created=lambda c: apply_request_facts(c, request),
        )
        contract = result["contract"]

        # Link both directions and record it on the request's timeline.
        request.contract_id = contract.id
        request.updated_by_user_id = actor.id
        write_audit_log(
            db, action="intake.contract_drafted", resource_type="intake_request",
            resource_id=request.id, org_id=actor.org_id, actor_user_id=actor.id,
            request_id=http_request_id, after={"contract_id": contract.id, "doc_type": doc_type},
        )
        write_timeline_event(
            db, org_id=actor.org_id, resource_type="intake_request", resource_id=request.id,
            event_type="intake.contract_drafted", title=f"Drafted the {spec['label']}",
            actor_user_id=actor.id, request_id=http_request_id,
            details={"contract_id": contract.id, "title": contract.title, "contract_type": spec["contract_type"]},
        )
        db.commit()
        db.refresh(request)
        return contract

    async def ingest_attachment_as_contract(
        self, *, actor, request: IntakeRequest, http_request_id: str | None = None
    ):
        """Create a contract FROM the request's most recent attachment (its extracted
        text) instead of a template — the 'review an existing contract' path.
        Idempotent; flags the contract for auto AI review."""
        from fastapi import HTTPException, status
        from sqlalchemy import select

        from app.contracts.models import Contract
        from app.intake.models import IntakeDocument

        db = self.db
        if request.contract_id:
            existing = db.get(Contract, request.contract_id)
            if existing is not None:
                return existing

        doc = db.scalars(
            select(IntakeDocument)
            .where(IntakeDocument.request_id == request.id, IntakeDocument.extracted_text.isnot(None))
            .order_by(IntakeDocument.created_at.desc())
        ).first()
        if doc is None or not (doc.extracted_text or "").strip():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "No attached document with extractable text to use as the contract.",
            )

        from app.parties import service as parties

        cp = parties.get(db, org_id=request.org_id, kind="counterparty", record_id=request.counterparty_id)
        counterparty = cp.name if cp else _primary_counterparty(request)
        doc_type = resolve_doc_type(request)
        contract_type = _DOC_TYPES[doc_type]["contract_type"] if doc_type else None
        base = doc.filename.rsplit(".", 1)[0] if "." in doc.filename else doc.filename
        title = base.strip() or f"Contract — {counterparty}"

        data = (doc.extracted_text or "").encode("utf-8")
        upload = UploadFile(
            file=io.BytesIO(data), size=len(data),
            filename=f"{base or 'contract'}.txt", headers=Headers({"content-type": "text/plain"}),
        )
        result = await self.files.create_contract_from_upload(
            upload=upload, user=actor, title=title,
            counterparty_name=counterparty, contract_type=contract_type, request_id=http_request_id,
            on_created=lambda c: apply_request_facts(c, request),
        )
        contract = result["contract"]
        request.contract_id = contract.id
        request.updated_by_user_id = actor.id
        write_audit_log(
            db, action="intake.contract_from_attachment", resource_type="intake_request",
            resource_id=request.id, org_id=actor.org_id, actor_user_id=actor.id,
            request_id=http_request_id, after={"contract_id": contract.id, "document_id": doc.id},
        )
        write_timeline_event(
            db, org_id=actor.org_id, resource_type="intake_request", resource_id=request.id,
            event_type="intake.contract_from_attachment", title="Created the contract from the attachment",
            actor_user_id=actor.id, request_id=http_request_id,
            details={"contract_id": contract.id, "title": title, "document": doc.filename},
        )
        db.commit()
        db.refresh(request)
        return contract

    def apply_change_request(self, *, request: IntakeRequest, actor_id: str | None) -> dict | None:
        """When an amendment, renewal, termination or novation workflow finishes,
        write what it changed onto the agreement it names, so the register shows the
        new value / end date / party instead of the request only recording it."""
        from app.contracts.models import Contract

        db = self.db
        fv = request.field_values or {}
        parent = db.get(Contract, fv.get("parent_contract_id")) if fv.get("parent_contract_id") else None
        if parent is None or parent.org_id != request.org_id:
            return None
        cols, notes = _change_facts(fv)
        if not cols and not notes:
            return None
        before = {k: str(getattr(parent, k)) if getattr(parent, k) is not None else None for k in cols}
        meta = dict(parent.metadata_json or {})
        sources = dict(meta.get("field_sources") or {})
        for k, v in cols.items():
            setattr(parent, k, v)
            sources[k] = "form"
        meta["field_sources"] = sources
        meta.update(notes)
        parent.metadata_json = meta
        after = {k: str(v) for k, v in cols.items()}
        write_audit_log(db, action="contract.changed_by_request", resource_type="contract", resource_id=parent.id,
                        org_id=parent.org_id, actor_user_id=actor_id, before=before,
                        after={**after, "request": request.ref, **notes})
        write_timeline_event(db, org_id=parent.org_id, resource_type="contract", resource_id=parent.id,
                             event_type="contract.changed_by_request",
                             title=f"Updated by {request.ref} ({request.type_label})", actor_user_id=actor_id,
                             details={**after, **notes})
        return after


# --- DI-MIGRATION: temporary wrappers ---------------------------------------
# draft_contract_for_request, ingest_attachment_as_contract and
# apply_change_request are imported directly (deferred) by
# app.workflows.service and app.intake.routes. Tracked in backend/DI_MIGRATION.md.

def _party_details(db, request: IntakeRequest, org_name: str) -> tuple[str, str, list[str]]:
    return DraftingService(db)._party_details(request, org_name)


async def draft_contract_for_request(
    db, *, actor, request: IntakeRequest, http_request_id: str | None = None, custom: bool = False
):
    return await DraftingService(db).draft_contract_for_request(
        actor=actor, request=request, http_request_id=http_request_id, custom=custom
    )


async def ingest_attachment_as_contract(
    db, *, actor, request: IntakeRequest, http_request_id: str | None = None
):
    return await DraftingService(db).ingest_attachment_as_contract(
        actor=actor, request=request, http_request_id=http_request_id
    )


def apply_change_request(db, *, request: IntakeRequest, actor_id: str | None) -> dict | None:
    return DraftingService(db).apply_change_request(request=request, actor_id=actor_id)

if __name__ == "__main__":  # pragma: no cover - template self-check
    for dt in _DOC_TYPES:
        out = render_document(dt, company="Acme Legal", counterparty="Globex Corporation", effective="2026-07-12")
        assert "Globex Corporation" in out and "Acme Legal" in out, dt
        assert "{" not in out and "}" not in out, f"unfilled placeholder in {dt}"
        assert "GOVERN" in out.upper() or "GOVERNED" in out.upper() or dt == "dpa", dt

    class _R:
        def __init__(self, tl, desc, field_values=None):
            self.type_label, self.description = tl, desc
            self.field_values = field_values or {}

    assert resolve_doc_type(_R("NDA Request", "mutual nda with Globex")) == "nda"
    assert resolve_doc_type(_R("Vendor Due Diligence", "onboard a new supplier")) == "vendor"
    assert resolve_doc_type(_R("Privacy Question", "need a DPA for GDPR")) == "dpa"
    assert resolve_doc_type(_R("Contract Review", "draft an MSA / master services agreement")) == "msa"
    assert resolve_doc_type(
        _R("New agreement Request", "NA", {"agreement_type": "Master Services Agreement"})
    ) == "msa"
    assert resolve_doc_type(_R("Litigation hold", "preserve documents")) is None
    print("drafting templates + resolver self-check passed")
