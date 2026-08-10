"""Turn an intake request into a real draft contract.

This is the bridge from intake into the contract lifecycle. It picks a template
for the request's document type (NDA / MSA / DPA / Vendor Agreement), renders it
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

from starlette.datastructures import Headers, UploadFile

from app.core.audit import write_audit_log, write_timeline_event
from app.intake.models import IntakeRequest
from app.organizations.models import Organization

# Real, clause-rich templates so the contract's clause-extraction, risk-scoring
# and playbook deviation analysis have genuine text to work on. All three
# placeholders — {company}, {counterparty}, {effective_date} — are filled via
# str.format; keep the bodies free of stray braces.

_MUTUAL_NDA = """{nda_kind_title}NON-DISCLOSURE AGREEMENT

This {nda_kind_word} Non-Disclosure Agreement (the "Agreement") is entered into as of {effective_date} (the "Effective Date") by and between {company} ("{company}") and {counterparty} ("Counterparty"). {company} and Counterparty are each a "Party" and together the "Parties."

RECITALS

The Parties wish to {purpose_phrase} (the "Purpose") and, in connection with the Purpose, certain confidential and proprietary information may be disclosed. {direction_recital} This Agreement sets out the terms on which such information will be protected.

1. DEFINITION OF CONFIDENTIAL INFORMATION

1.1 "Confidential Information" means any non-public information disclosed by one Party (the "Disclosing Party") to the other Party (the "Receiving Party"), whether disclosed orally, in writing, electronically, visually or by any other means, that is designated as confidential or that a reasonable person would understand to be confidential given its nature and the circumstances of disclosure. Confidential Information includes, without limitation, business plans, financial information, technical data, trade secrets, know-how, product plans, customer lists, pricing, and the existence and terms of this Agreement.

2. EXCLUSIONS

2.1 Confidential Information does not include information that: (a) is or becomes publicly available through no breach of this Agreement by the Receiving Party; (b) was rightfully known to the Receiving Party without restriction before receipt; (c) is rightfully received from a third party without a duty of confidentiality; or (d) is independently developed by the Receiving Party without use of or reference to the Confidential Information.

3. OBLIGATIONS OF THE RECEIVING PARTY

3.1 The Receiving Party shall (a) hold the Confidential Information in strict confidence; (b) use it solely for the Purpose; (c) not disclose it to any third party except to its employees, advisors and contractors who have a need to know for the Purpose and are bound by confidentiality obligations no less protective than those in this Agreement; and (d) protect it using at least the same degree of care it uses for its own confidential information, and in no event less than a reasonable degree of care.

4. COMPELLED DISCLOSURE

4.1 If the Receiving Party is required by law or legal process to disclose Confidential Information, it shall, to the extent legally permitted, give the Disclosing Party prompt written notice and reasonable cooperation so the Disclosing Party may seek a protective order.

5. TERM AND TERMINATION

5.1 This Agreement commences on the Effective Date and {term_clause}, unless earlier terminated by either Party on thirty (30) days' written notice. The confidentiality obligations survive termination and {survival_clause} from the date of disclosure of each item of Confidential Information; obligations with respect to trade secrets continue for as long as the information remains a trade secret.

6. RETURN OR DESTRUCTION

6.1 Upon the Disclosing Party's written request or termination of this Agreement, the Receiving Party shall promptly return or destroy all Confidential Information and certify such destruction in writing, subject to reasonable retention required by law or bona fide record-retention policies.

7. NO LICENSE; NO WARRANTY

7.1 Nothing in this Agreement grants either Party any right or license, by implication or otherwise, in or to the other Party's Confidential Information or any patent, copyright, trademark or other intellectual property right. All Confidential Information is provided "as is," without warranty of any kind.

8. REMEDIES

8.1 The Parties agree that a breach of this Agreement may cause irreparable harm for which monetary damages are an inadequate remedy, and that the Disclosing Party is entitled to seek injunctive relief in addition to any other remedies available at law or in equity.

9. GENERAL

9.1 This Agreement is governed by the laws of {governing_law}, without regard to its conflict-of-laws rules. It constitutes the entire agreement between the Parties regarding its subject matter and supersedes all prior understandings. It may be amended only in a writing signed by both Parties. Neither Party may assign this Agreement without the other's prior written consent.

IN WITNESS WHEREOF, the Parties have executed this Agreement as of the Effective Date.

{company}                                    {counterparty}

By: ______________________________          By: ______________________________
Name:                                        Name:
Title:                                       Title:
Date:                                        Date:
"""


_MSA = """MASTER SERVICES AGREEMENT

This Master Services Agreement (the "Agreement") is entered into as of {effective_date} (the "Effective Date") by and between {company} ("{company}") and {counterparty} ("Provider"). {company} and Provider are each a "Party" and together the "Parties."

1. SERVICES AND STATEMENTS OF WORK

1.1 Provider shall perform the services (the "Services") described in one or more statements of work executed by the Parties (each, an "SOW"). Each SOW is governed by and incorporated into this Agreement; in the event of a conflict, this Agreement controls except where an SOW expressly states otherwise for that SOW.

2. FEES AND PAYMENT

2.1 {company} shall pay the fees set out in the applicable SOW. Undisputed invoices are payable within forty-five (45) days of receipt. {company} may withhold payment of amounts it disputes in good faith pending resolution. Fees are exclusive of applicable taxes, other than taxes on Provider's income.

3. TERM AND TERMINATION

3.1 This Agreement begins on the Effective Date and continues until terminated. Either Party may terminate this Agreement or any SOW for material breach not cured within thirty (30) days of written notice, or immediately if the other Party becomes insolvent. {company} may terminate any SOW for convenience on thirty (30) days' notice, paying for Services performed through the termination date.

4. CONFIDENTIALITY

4.1 Each Party shall protect the other's Confidential Information with the same care it uses for its own (and no less than reasonable care), use it only to perform this Agreement, and not disclose it except to personnel with a need to know who are bound by confidentiality obligations.

5. INTELLECTUAL PROPERTY

5.1 Deliverables created for {company} under an SOW are works made for hire and, upon full payment, are owned by {company}. Provider retains ownership of its pre-existing materials and tools and grants {company} a perpetual, non-exclusive license to use them as embedded in the deliverables.

6. WARRANTIES

6.1 Provider warrants that the Services will be performed in a professional and workmanlike manner in accordance with the applicable SOW and generally accepted industry standards, and that the deliverables will not infringe the intellectual property rights of any third party.

7. LIMITATION OF LIABILITY

7.1 Except for breaches of confidentiality, indemnification obligations, and a Party's gross negligence or willful misconduct, neither Party's aggregate liability under this Agreement will exceed the fees paid or payable under the applicable SOW in the twelve (12) months preceding the claim. Neither Party is liable for indirect, incidental, special, or consequential damages.

8. INDEMNIFICATION

8.1 Provider shall defend, indemnify and hold {company} harmless from third-party claims arising from Provider's breach of this Agreement, its negligence or willful misconduct, or any allegation that a deliverable infringes a third party's intellectual property rights.

9. INSURANCE

9.1 Provider shall maintain, at its own expense, commercially reasonable insurance coverage appropriate to the Services, including commercial general liability and professional liability (errors and omissions), and shall provide certificates of insurance on request.

10. GENERAL

10.1 This Agreement is governed by the laws of the State of Delaware, without regard to its conflict-of-laws rules. It constitutes the entire agreement between the Parties regarding its subject matter, may be amended only in a signed writing, and may not be assigned by either Party without the other's prior written consent (except to a successor in a merger or sale of substantially all assets).

IN WITNESS WHEREOF, the Parties have executed this Agreement as of the Effective Date.

{company}                                    {counterparty}

By: ______________________________          By: ______________________________
Name:                                        Name:
Title:                                       Title:
Date:                                        Date:
"""


_DPA = """DATA PROCESSING AGREEMENT

This Data Processing Agreement (the "Agreement") is entered into as of {effective_date} (the "Effective Date") by and between {company} ("Controller") and {counterparty} ("Processor"). It supplements the underlying services agreement between the Parties (the "Principal Agreement") and governs the Processor's Processing of Personal Data on behalf of the Controller.

1. DEFINITIONS

1.1 "Personal Data", "Processing", "Data Subject", "Controller", "Processor", and "Supervisory Authority" have the meanings given in applicable data protection law, including the EU General Data Protection Regulation (GDPR). "Applicable Data Protection Law" means all laws and regulations applicable to the Processing of Personal Data under this Agreement.

2. SCOPE AND ROLES

2.1 The Processor shall Process Personal Data only as a Processor acting on behalf of the Controller, and only to the extent necessary to provide the services under the Principal Agreement. The subject matter, duration, nature, purpose, categories of Data Subjects and types of Personal Data are described in an annex to this Agreement.

3. PROCESSING INSTRUCTIONS

3.1 The Processor shall Process Personal Data only on the documented instructions of the Controller, including with regard to international transfers, unless required to do otherwise by law. The Processor shall promptly inform the Controller if, in its opinion, an instruction infringes Applicable Data Protection Law.

4. CONFIDENTIALITY

4.1 The Processor shall ensure that persons authorized to Process the Personal Data are bound by an appropriate obligation of confidentiality and Process the Personal Data only as instructed.

5. SECURITY MEASURES

5.1 The Processor shall implement and maintain appropriate technical and organizational measures to ensure a level of security appropriate to the risk, including, as appropriate, encryption, pseudonymization, ongoing confidentiality, integrity, availability and resilience of Processing systems, and a process for regularly testing and evaluating those measures.

6. SUB-PROCESSORS

6.1 The Processor shall not engage a Sub-processor without the Controller's prior specific or general written authorization. Where general authorization is given, the Processor shall inform the Controller of intended changes and give the Controller the opportunity to object. The Processor remains liable for its Sub-processors' compliance with obligations equivalent to those in this Agreement.

7. DATA SUBJECT RIGHTS

7.1 Taking into account the nature of the Processing, the Processor shall assist the Controller by appropriate technical and organizational measures, insofar as possible, in fulfilling the Controller's obligation to respond to requests to exercise Data Subject rights.

8. PERSONAL DATA BREACH

8.1 The Processor shall notify the Controller without undue delay, and in any event within seventy-two (72) hours, after becoming aware of a Personal Data Breach, and shall provide the Controller with sufficient information to allow the Controller to meet its breach-notification obligations.

9. INTERNATIONAL TRANSFERS

9.1 The Processor shall not transfer Personal Data outside the jurisdiction of origin unless it has taken measures required by Applicable Data Protection Law to ensure an adequate level of protection, including, where required, execution of Standard Contractual Clauses.

10. AUDIT

10.1 The Processor shall make available to the Controller information necessary to demonstrate compliance with this Agreement and allow for and contribute to audits, including inspections, conducted by the Controller or an auditor mandated by the Controller, subject to reasonable confidentiality and frequency limits.

11. RETURN OR DELETION

11.1 Upon termination of the services, the Processor shall, at the Controller's choice, delete or return all Personal Data and delete existing copies, unless retention is required by law.

12. GENERAL

12.1 This Agreement is governed by the law of the Principal Agreement. In the event of a conflict between this Agreement and the Principal Agreement regarding Processing of Personal Data, this Agreement prevails.

IN WITNESS WHEREOF, the Parties have executed this Agreement as of the Effective Date.

{company}                                    {counterparty}

By: ______________________________          By: ______________________________
Name:                                        Name:
Title:                                       Title:
Date:                                        Date:
"""


_VENDOR = """VENDOR AGREEMENT

This Vendor Agreement (the "Agreement") is entered into as of {effective_date} (the "Effective Date") by and between {company} ("{company}") and {counterparty} ("Vendor"). {company} and Vendor are each a "Party" and together the "Parties."

1. ENGAGEMENT AND SCOPE

1.1 Vendor shall supply the goods and/or services described in one or more purchase orders or order forms agreed by the Parties (each, an "Order"). Each Order is governed by this Agreement; in the event of a conflict, this Agreement controls unless the Order expressly amends it for that Order.

2. PRICING AND PAYMENT

2.1 {company} shall pay the prices set out in the applicable Order. Undisputed invoices are payable within forty-five (45) days of receipt of a valid invoice and acceptance of the goods or services. Prices are firm for the initial term and exclusive of applicable taxes other than taxes on Vendor's income.

3. TERM

3.1 This Agreement begins on the Effective Date and continues for an initial term of one (1) year, renewing for successive one-year terms unless either Party gives sixty (60) days' notice of non-renewal. Either Party may terminate for material breach not cured within thirty (30) days of written notice.

4. COMPLIANCE

4.1 Vendor shall comply with all applicable laws in performing under this Agreement, including anti-bribery and anti-corruption laws (such as the U.S. Foreign Corrupt Practices Act and the UK Bribery Act), applicable economic sanctions and export-control laws, and applicable modern-slavery and labor laws. Vendor shall not, directly or indirectly, offer or give anything of value to obtain an improper advantage.

5. DATA PROTECTION AND SECURITY

5.1 To the extent Vendor Processes personal data on behalf of {company}, it shall do so only per {company}'s instructions and shall maintain appropriate technical and organizational security measures. Where required, the Parties shall execute a Data Processing Agreement, which is incorporated by reference.

6. CONFIDENTIALITY

6.1 Vendor shall hold {company}'s Confidential Information in confidence, use it only to perform under this Agreement, and not disclose it except to personnel with a need to know who are bound by confidentiality obligations no less protective than those here.

7. WARRANTIES AND SERVICE LEVELS

7.1 Vendor warrants that goods will be free from defects and conform to the applicable Order, and that services will be performed in a professional and workmanlike manner. Where an Order specifies service levels, Vendor shall meet them, and the associated service-level credits are {company}'s sole remedy for the corresponding failures unless the Order states otherwise.

8. LIMITATION OF LIABILITY

8.1 Except for breaches of confidentiality, indemnification obligations, breaches of the Compliance section, and a Party's gross negligence or willful misconduct, neither Party's aggregate liability will exceed the amounts paid or payable under the applicable Order in the twelve (12) months preceding the claim, and neither Party is liable for indirect, incidental, special, or consequential damages.

9. INDEMNIFICATION

9.1 Vendor shall defend, indemnify and hold {company} harmless from third-party claims arising from Vendor's breach of this Agreement, its negligence or willful misconduct, product defects, or infringement of a third party's intellectual property rights.

10. GENERAL

10.1 This Agreement is governed by the laws of the State of Delaware, without regard to its conflict-of-laws rules. It constitutes the entire agreement between the Parties regarding its subject matter, may be amended only in a signed writing, and may not be assigned by Vendor without {company}'s prior written consent.

IN WITNESS WHEREOF, the Parties have executed this Agreement as of the Effective Date.

{company}                                    {counterparty}

By: ______________________________          By: ______________________________
Name:                                        Name:
Title:                                       Title:
Date:                                        Date:
"""


# doc_type -> template spec. contract_type is the string stored on the Contract
# (drives approval routing + the NDA fast-lane, which matches "nda").
_DOC_TYPES: dict[str, dict] = {
    "nda": {"contract_type": "NDA", "label": "Mutual NDA", "template": _MUTUAL_NDA},
    "msa": {"contract_type": "MSA", "label": "Master Services Agreement", "template": _MSA},
    "dpa": {"contract_type": "DPA", "label": "Data Processing Agreement", "template": _DPA},
    "vendor": {"contract_type": "Vendor Agreement", "label": "Vendor Agreement", "template": _VENDOR},
}


def resolve_doc_type(request: IntakeRequest) -> str | None:
    """Which draftable document (if any) this request maps to. Keyword-first so
    it works regardless of the classifier; falls back to the classified category.
    Returns None for request types that are not contracts (litigation, general)."""
    from app.intake.agents import classify

    text = f"{request.type_label} {request.description or ''}".lower()
    category = (classify(request.type_label, request.description or "").get("category") or "").lower()

    if re.search(r"\b(nda|non-disclosure|non disclosure)\b", text) or category == "nda":
        return "nda"
    if re.search(r"\b(dpa|data processing|data protection|gdpr)\b", text) or category == "privacy":
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


def _nda_fill(*, company: str, counterparty: str, effective: str, fields: dict) -> dict:
    """Turn the NDA intake fields (direction / purpose / term / survival /
    governing law) into the template's placeholders. Empty fields fall back to
    the standard playbook defaults, so a bare request still yields a clean NDA."""
    f = fields or {}
    direction = str(f.get("nda_direction") or "mutual").lower()
    one_way = "one" in direction  # oneway_disclose / oneway_receive / one-way
    purpose = str(f.get("purpose") or "").strip()
    term = str(f.get("term") or "").strip()
    survival = str(f.get("survival_years") or "").strip()
    law = str(f.get("governing_law") or "").strip() or "the State of Delaware"
    if one_way:
        disc, recv = (counterparty, company) if "receive" in direction else (company, counterparty)
        direction_recital = (
            f"This is a one-way disclosure in which {disc} is the Disclosing Party "
            f"and {recv} is the Receiving Party."
        )
    else:
        direction_recital = "Each Party may act as both Disclosing Party and Receiving Party."
    return {
        "company": company, "counterparty": counterparty, "effective_date": effective,
        "nda_kind_title": "ONE-WAY " if one_way else "MUTUAL ",
        "nda_kind_word": "One-Way" if one_way else "Mutual",
        "purpose_phrase": purpose or "explore a potential business relationship",
        "direction_recital": direction_recital,
        "term_clause": f"continues for the term of {term}" if term else "continues for two (2) years",
        "survival_clause": f"continue for {survival} year(s)" if survival else "continue for three (3) years",
        "governing_law": law,
    }


def render_document(doc_type: str, *, company: str, counterparty: str, effective: str, fields: dict | None = None) -> str:
    spec = _DOC_TYPES[doc_type]
    if doc_type == "nda":
        return spec["template"].format(**_nda_fill(company=company, counterparty=counterparty, effective=effective, fields=fields or {}))
    return spec["template"].format(company=company, counterparty=counterparty, effective_date=effective)


async def draft_contract_for_request(
    db, *, actor, request: IntakeRequest, http_request_id: str | None = None
):
    """Render the right template for the request and create a real, analysed
    contract linked back to it. Idempotent: returns the existing contract if
    already drafted. Raises HTTPException(422) if the request has no template."""
    from fastapi import HTTPException, status

    from app.contract_files.service import create_contract_from_upload
    from app.contracts.models import Contract

    if request.contract_id:
        existing = db.get(Contract, request.contract_id)
        if existing is not None:
            return existing

    doc_type = resolve_doc_type(request)
    if doc_type is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "This request type has no draft template — handle it manually.",
        )
    spec = _DOC_TYPES[doc_type]

    org = db.get(Organization, actor.org_id)
    company = (org.name if org and org.name else "Company")
    fv = request.field_values or {}
    # Prefer the details captured on the intake form; fall back to screening / today.
    counterparty = str(fv.get("counterparty") or "").strip() or _primary_counterparty(request)
    effective = str(fv.get("effective_date") or "").strip() or date.today().isoformat()
    text = render_document(doc_type, company=company, counterparty=counterparty, effective=effective, fields=fv)
    # One-way NDAs get a clearer label than the generic template label.
    label = spec["label"]
    if doc_type == "nda" and "one" in str(fv.get("nda_direction") or "").lower():
        label = "One-Way NDA"
    title = f"{label} — {counterparty}"

    data = text.encode("utf-8")
    upload = UploadFile(
        file=io.BytesIO(data),
        size=len(data),
        filename=f"{spec['label']} - {counterparty}.txt",
        headers=Headers({"content-type": "text/plain"}),
    )
    result = await create_contract_from_upload(
        db,
        upload=upload,
        user=actor,
        title=title,
        counterparty_name=counterparty,
        contract_type=spec["contract_type"],
        request_id=http_request_id,
    )
    contract = result["contract"]

    # Flag for auto AI review — once clause extraction finishes (async), the job
    # runner runs risk + the matching playbook so REVIEW is ready without a click.
    meta = dict(contract.metadata_json or {})
    meta["auto_review_pending"] = True
    contract.metadata_json = meta

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
    db, *, actor, request: IntakeRequest, http_request_id: str | None = None
):
    """Create a contract FROM the request's most recent attachment (its extracted
    text) instead of a template — the 'review an existing contract' path.
    Idempotent; flags the contract for auto AI review."""
    from fastapi import HTTPException, status
    from sqlalchemy import select

    from app.contract_files.service import create_contract_from_upload
    from app.contracts.models import Contract
    from app.intake.models import IntakeDocument

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
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "No attached document with extractable text to use as the contract.",
        )

    counterparty = _primary_counterparty(request)
    doc_type = resolve_doc_type(request)
    contract_type = _DOC_TYPES[doc_type]["contract_type"] if doc_type else None
    base = doc.filename.rsplit(".", 1)[0] if "." in doc.filename else doc.filename
    title = base.strip() or f"Contract — {counterparty}"

    data = (doc.extracted_text or "").encode("utf-8")
    upload = UploadFile(
        file=io.BytesIO(data), size=len(data),
        filename=f"{base or 'contract'}.txt", headers=Headers({"content-type": "text/plain"}),
    )
    result = await create_contract_from_upload(
        db, upload=upload, user=actor, title=title,
        counterparty_name=counterparty, contract_type=contract_type, request_id=http_request_id,
    )
    contract = result["contract"]

    meta = dict(contract.metadata_json or {})
    meta["auto_review_pending"] = True
    contract.metadata_json = meta
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


if __name__ == "__main__":  # pragma: no cover - template self-check
    for dt in _DOC_TYPES:
        out = render_document(dt, company="Acme Legal", counterparty="Globex Corporation", effective="2026-07-12")
        assert "Globex Corporation" in out and "Acme Legal" in out, dt
        assert "{" not in out and "}" not in out, f"unfilled placeholder in {dt}"
        assert "GOVERN" in out.upper() or "GOVERNED" in out.upper() or dt == "dpa", dt

    class _R:
        def __init__(self, tl, desc):
            self.type_label, self.description = tl, desc

    assert resolve_doc_type(_R("NDA Request", "mutual nda with Globex")) == "nda"
    assert resolve_doc_type(_R("Vendor Due Diligence", "onboard a new supplier")) == "vendor"
    assert resolve_doc_type(_R("Privacy Question", "need a DPA for GDPR")) == "dpa"
    assert resolve_doc_type(_R("Contract Review", "draft an MSA / master services agreement")) == "msa"
    assert resolve_doc_type(_R("Litigation hold", "preserve documents")) is None
    print("drafting templates + resolver self-check passed")
