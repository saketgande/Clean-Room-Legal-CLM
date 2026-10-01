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
from decimal import Decimal, InvalidOperation

from starlette.datastructures import Headers, UploadFile

from app.contract_files.service import ContractFilesService
from app.core.audit import write_audit_log, write_timeline_event
from app.core.config import settings
from app.core.database import utcnow
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

2.1 {company} shall pay the fees set out in the applicable SOW. Undisputed invoices are payable within {payment_days} days of receipt. {company} may withhold payment of amounts it disputes in good faith pending resolution. Fees are exclusive of applicable taxes, other than taxes on Provider's income.{value_sentence}

3. TERM AND TERMINATION

3.1 This Agreement begins on the Effective Date and {msa_term}. Either Party may terminate this Agreement or any SOW for material breach not cured within thirty (30) days of written notice, or immediately if the other Party becomes insolvent. {company} may terminate any SOW for convenience on thirty (30) days' notice, paying for Services performed through the termination date.

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

2.1 {company} shall pay the prices set out in the applicable Order. Undisputed invoices are payable within {payment_days} days of receipt of a valid invoice and acceptance of the goods or services. Prices are firm for the initial term and exclusive of applicable taxes other than taxes on Vendor's income.{value_sentence}

3. TERM

3.1 This Agreement begins on the Effective Date and {vendor_term}. Either Party may terminate for material breach not cured within thirty (30) days of written notice.

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


# Which template each form (or kind of new agreement) drafts. Selling to a
# customer and "something else" have no template: Legal drafts them.
_FORM_DOC = {"sow": "msa", "dpa": "dpa"}
_KIND_DOC = {"NDA": "nda", "Services (MSA)": "msa", "Consultancy": "msa",
             "Buying from a vendor": "vendor", "Software or SaaS": "vendor"}


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


def _nda_fill(*, company: str, counterparty: str, effective: str, fields: dict) -> dict:
    """Turn the NDA intake fields (direction / purpose / term / survival /
    governing law) into the template's placeholders. Empty fields fall back to
    the standard playbook defaults, so a bare request still yields a clean NDA."""
    f = fields or {}
    one_way = f.get("nda_kind") == "One-way"
    purpose = str(f.get("purpose") or "").strip()
    term = str(f.get("nda_term") or "").strip()
    survival = str(f.get("survival_years") or "").strip()
    law = str(f.get("governing_law") or "").strip() or "the State of Delaware"
    if one_way:
        disc, recv = (counterparty, company) if f.get("nda_direction") == "They share" else (company, counterparty)
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
    fv = fields or {}
    facts = request_facts(fv)
    end = facts.get("expiration_date")
    value = facts.get("value_amount")
    term = f"continues until {end.isoformat()}" if end else "continues until terminated"
    if end and fv.get("term") == "Renews automatically":
        term += (f", and then renews automatically for successive periods of {fv.get('renewal_term') or '1 year'}"
                 f" unless either Party gives {fv.get('notice_days') or '60 days'}' written notice of non-renewal")
    days = str(fv.get("payment_terms") or "45 days").split()[0]
    return spec["template"].format(
        company=company, counterparty=counterparty, effective_date=effective,
        msa_term=term, vendor_term=term, payment_days=days,
        value_sentence=f" The total value of this Agreement shall not exceed {facts['currency']} {value:,.2f}." if value else "",
    )


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
                status.HTTP_422_UNPROCESSABLE_ENTITY,
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
            text = render_document(doc_type, company=company, counterparty=counterparty, effective=effective, fields=fv)
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
                status.HTTP_422_UNPROCESSABLE_ENTITY,
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
