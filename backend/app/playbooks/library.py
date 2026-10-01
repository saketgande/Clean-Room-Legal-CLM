"""Starter playbooks, one per agreement type the request forms draft or review.

A playbook is picked for a contract by matching its name to the contract's
type (pick_playbook_for_contract), so each name here carries the words of the
contract type it serves: "Vendor Agreement Playbook" ↔ "Vendor Agreement".
NDAs are left to the org's own NDA playbook.

Each rule is written for both ways it is checked:

* The AI review reads the preferred / fallback positions, rationale and
  guidance and judges the clause as a lawyer would.
* When the AI can't run, the rules are matched literally: ``required_language``
  must appear and ``prohibited_language`` must not. So those are short phrases
  that really appear (or really shouldn't) — the required ones all appear in our
  own templates, so a contract drafted from them is not flagged for missing
  what it plainly has (test_playbook_library checks this).

Positions are starting points in INR terms for an Indian company; Legal edits
them in the Playbooks page like any other playbook.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit_log
from app.core.enums import PlaybookStatus
from app.playbooks.models import Playbook, PlaybookRule, PlaybookVersion


def rule(clause_type, preferred, *, fallback=None, required=None, prohibited=None, risk="medium",
         why="", guidance=None, approval=False, escalate="Legal Counsel"):
    return {
        "clause_type": clause_type, "rule_type": "standard_position",
        "preferred_position": preferred, "fallback_position": fallback,
        "required_language": required, "prohibited_language": prohibited,
        "risk_level": risk, "rationale": why, "negotiation_guidance": guidance,
        "approval_required": approval, "escalation_role": escalate if (approval or risk in ("high", "critical")) else None,
        "sample_clause": None,
    }


# Shared positions, reused where the same standard applies.
def _liability(cap: str, fallback: str, *, required=True) -> dict:
    return rule("limitation_of_liability",
                f"Each party's total liability is capped at {cap}. Indirect and consequential losses are excluded. "
                "The cap does not apply to breach of confidentiality, indemnities, gross negligence, wilful misconduct "
                "or fraud.",
                fallback=fallback, required="limitation of liability" if required else None,
                prohibited="unlimited liability", risk="high", approval=True,
                why="The cap decides the most we can lose on this contract; carve-outs keep the risks a cap must not cover.",
                guidance="Trade the multiple before the carve-outs. Never cap confidentiality or IP indemnity below the "
                         "fees; escalate any uncapped exposure for us.")


def _payment(days: str, fallback: str) -> dict:
    return rule("payment_terms",
                f"Payment within {days} of receipt of a valid, undisputed invoice. Disputed amounts may be withheld in "
                "good faith. No advance payment above 20% of the fees.",
                fallback=fallback, required="undisputed invoices", prohibited="payable in advance",
                why="Protects cash flow and keeps leverage until the work is delivered.",
                guidance="Accept milestone-based payments tied to acceptance before accepting any advance.")


def _confidentiality() -> dict:
    return rule("confidentiality",
                "Mutual confidentiality covering all non-public information, with the standard exclusions, surviving "
                "at least 3 years after the agreement ends (trade secrets for as long as they remain secret).",
                fallback="2 years' survival for general information if trade secrets stay protected indefinitely.",
                required="confidential", risk="medium",
                why="Our business and product information must stay protected after the relationship ends.")


def _indemnity(who: str, covers: str) -> dict:
    return rule("indemnification",
                f"{who} defends and indemnifies us against third-party claims arising from {covers}.",
                fallback="Indemnity limited to IP infringement and breach of law, with defence controlled by the "
                         "indemnifying party.",
                required="indemnify", risk="high",
                why="Third-party claims caused by the other side must not land on us.",
                guidance="Refuse any indemnity from us beyond our own breach of law or IP infringement.")


def _termination(convenience: str) -> dict:
    return rule("termination",
                f"We may terminate for convenience on {convenience}' notice, paying for work properly done. Either "
                "party may terminate for material breach not cured within 30 days.",
                fallback="Termination for convenience on 60 days' notice.",
                required="terminate", risk="medium",
                why="We must be able to exit a relationship that no longer works without waiting for a breach.")


def _data_protection(*, required: bool = True) -> dict:
    return rule("data_protection",
                "Where personal data is processed: comply with the DPDP Act 2023 (and GDPR where it applies), a DPA "
                "on our template, breach notice within 72 hours, and no transfer outside India without safeguards.",
                fallback="The counterparty's DPA if it meets the same standards; Privacy review required.",
                required="data protection" if required else None, risk="high", approval=True, escalate="Compliance",
                why="Personal data breaches carry statutory penalties and notice deadlines.")


PLAYBOOK_LIBRARY: list[dict] = [
    {"name": "Master Services Agreement Playbook",
     "description": "Services we buy under a master agreement (MSA). Used for Services (MSA) requests, our template or theirs.",
     "rules": [
         _liability("the fees paid or payable in the 12 months before the claim",
                    "Up to 2x the annual fees; anything higher needs Legal Counsel approval."),
         _indemnity("The provider", "its breach, negligence, wilful misconduct or infringement of third-party IP"),
         rule("intellectual_property",
              "Deliverables created for us belong to us on payment. The provider keeps its pre-existing IP and grants us "
              "a perpetual, royalty-free licence to use it within the deliverables.",
              fallback="Provider owns generic tools; we own everything specific to our business and data.",
              required="intellectual property", risk="high",
              why="We pay for the work, so we must be able to use and change it without the provider."),
         _payment("45 days", "60 days if the provider accepts milestone payments tied to acceptance."),
         _termination("30 days"),
         _confidentiality(),
         rule("insurance",
              "The provider holds commercial general liability and professional indemnity insurance appropriate to the "
              "services for the term and 1 year after.",
              fallback="Certificates on request instead of named-insured status.", required="insurance", risk="low",
              why="Insurance stands behind the provider's indemnities."),
         rule("service_levels",
              "Services meet agreed service levels with service credits for misses and a right to terminate for "
              "repeated failure.", fallback="Service credits only, capped at 10% of monthly fees.", risk="medium",
              why="Without measurable service levels there is no remedy short of termination."),
         _data_protection(required=False),
     ]},
    {"name": "Consultancy Agreement Playbook",
     "description": "An individual or firm advising us. Used for Consultancy requests.",
     "rules": [
         rule("intellectual_property",
              "Everything the consultant creates for us is assigned to us on creation, with moral rights waived where "
              "the law allows.", fallback="Assignment on payment of the related invoice.",
              required="intellectual property", risk="high",
              why="Advice and material we pay for must be ours to use."),
         rule("independent_contractor",
              "The consultant is an independent contractor: no employment relationship, the consultant pays its own "
              "taxes and benefits, and uses its own tools.", risk="high", approval=True, escalate="Legal Counsel",
              why="Misclassification creates employment and tax liability for us."),
         _confidentiality(),
         rule("conflicts_of_interest",
              "The consultant discloses conflicts of interest and does not advise our competitors on the same matter "
              "during the engagement.", fallback="Disclosure of conflicts only.", risk="medium",
              why="Advice must be independent of competing interests."),
         rule("non_solicitation",
              "Neither party solicits the other's staff for 12 months after the engagement.", risk="low",
              fallback="6 months.", why="Protects our team."),
         _payment("30 days", "45 days; expenses only if pre-approved in writing."),
         _termination("15 days"),
         _liability("the fees paid under the agreement", "Up to 2x the fees."),
     ]},
    {"name": "Vendor Agreement Playbook",
     "description": "Goods or services we buy from a supplier. Used for Buying from a vendor requests.",
     "rules": [
         _liability("12 months' fees for the vendor, with no cap on its indemnities",
                    "Vendor cap at 1x annual fees if indemnities stay uncapped."),
         _indemnity("The vendor", "defective goods, its breach of law, negligence or infringement of third-party IP"),
         rule("warranties",
              "The vendor warrants that goods and services conform to the specification, are free from defects for "
              "at least 12 months, and are supplied with reasonable skill and care.",
              fallback="6-month warranty with repair or replacement at the vendor's cost.",
              required="service levels", risk="medium", why="Warranties give us a remedy when what we buy fails."),
         rule("compliance",
              "The vendor complies with anti-bribery, sanctions, labour and environmental law and our supplier code of "
              "conduct, and we may terminate for breach.", required="compliance", risk="high", approval=True,
              escalate="Compliance", why="A supplier's breach of law becomes our reputational and legal problem."),
         _payment("45 days", "60 days for strategic suppliers."),
         rule("subcontracting",
              "The vendor may not subcontract or assign without our written consent and stays responsible for its "
              "subcontractors.", required="consent", risk="medium",
              why="We chose this vendor; others must not do the work without our say."),
         rule("audit_rights",
              "We may audit the vendor's records relating to the agreement on reasonable notice.",
              fallback="Audit by an independent auditor once a year.", risk="low",
              why="Lets us verify charges and compliance."),
         _data_protection(),
     ]},
    {"name": "Software / SaaS Agreement Playbook",
     "description": "Software licences and subscriptions we buy. Used for Software or SaaS requests.",
     "rules": [
         rule("service_levels",
              "At least 99.5% monthly availability, with service credits for misses and a right to terminate after "
              "repeated failure.", fallback="99% availability with service credits.", required="service levels",
              risk="high", why="Downtime of a system we depend on stops our work."),
         rule("data_security",
              "The provider holds ISO 27001 or SOC 2 Type II, encrypts our data in transit and at rest, and notifies "
              "us of a security incident within 72 hours.", fallback="Equivalent controls evidenced by a security "
              "questionnaire reviewed by Compliance.", required="data protection", risk="high", approval=True,
              escalate="Compliance", why="Our data in someone else's system is only as safe as their controls."),
         rule("data_location",
              "Our data is hosted in India (or the EU). Any transfer outside needs Standard Contractual Clauses or an "
              "equivalent safeguard and Privacy approval.", risk="high", approval=True, escalate="Compliance",
              why="Cross-border transfers carry regulatory conditions."),
         rule("exit_and_data_return",
              "On termination we can export all our data in a standard format for 30 days, after which the provider "
              "deletes it and certifies deletion.", fallback="Export on request for 15 days.", risk="medium",
              why="We must never be locked in by our own data."),
         rule("price_increases",
              "Price increases on renewal are capped at 5% a year and notified at least 90 days in advance.",
              fallback="Capped at 8% or CPI, whichever is lower.", risk="medium",
              why="Avoids unbudgeted jumps once we depend on the system."),
         rule("renewal",
              "Renewal only with at least 60 days' notice to cancel, and a reminder from the provider before it "
              "renews.", fallback="30 days' notice to cancel.", risk="medium",
              why="Silent auto-renewal commits budget we have not approved."),
         _liability("12 months' fees, with a higher cap (at least 3x fees) for data breaches",
                    "A single cap of 2x annual fees covering data breaches."),
         _payment("45 days", "Annual in advance only for subscriptions under INR 10 lakh."),
     ]},
    {"name": "Customer Agreement Playbook",
     "description": "Agreements where we sell to a customer, usually on the customer's paper. Used for Selling to a customer requests.",
     "rules": [
         rule("limitation_of_liability",
              "Our total liability is capped at the fees paid by the customer in the 12 months before the claim, with "
              "indirect and consequential losses excluded.",
              fallback="Up to 2x annual fees; uncapped only for our breach of confidentiality.",
              prohibited="unlimited liability", risk="critical", approval=True, escalate="Legal Counsel",
              why="Uncapped exposure to a customer can exceed the whole value of the deal.",
              guidance="Offer a higher cap before accepting any uncapped category; escalate to Executive above 2x."),
         rule("indemnification",
              "We indemnify only for third-party IP infringement by our products, with the usual exclusions "
              "(customer modifications, combinations, misuse).",
              fallback="Add indemnity for our breach of law.", risk="high", approval=True,
              why="Broad indemnities make us insurer of the customer's business."),
         rule("intellectual_property",
              "We keep all IP in our products and know-how; the customer gets a licence to use what it buys.",
              prohibited="work made for hire", risk="high", approval=True,
              why="Assigning our IP to one customer stops us selling it to others."),
         rule("payment_terms",
              "Payment within 30–45 days of invoice, interest on late payment, and the right to suspend after 30 days "
              "overdue.", fallback="60 days for strategic customers with Finance approval.",
              prohibited="pay when paid", risk="medium", approval=False,
              why="Our cash flow must not depend on the customer's own customers paying."),
         rule("service_credits",
              "Service credits are the sole remedy for missed service levels and capped at 10% of monthly fees.",
              fallback="15% cap.", prohibited="liquidated damages", risk="high", approval=True, escalate="Finance",
              why="Uncapped penalties can wipe out the margin on the deal."),
         rule("warranties",
              "We warrant the products work materially as documented for 90 days; all other warranties are "
              "disclaimed.", fallback="12-month warranty with repair, replace or refund as the only remedy.",
              risk="medium", why="Open-ended warranties are unpriced risk."),
         rule("exclusivity_and_mfn",
              "No exclusivity and no most-favoured-customer pricing.", risk="high", approval=True, escalate="Executive",
              why="Exclusivity or MFN pricing restricts the rest of our business."),
         rule("governing_law",
              "Indian law with courts or arbitration seated in India.",
              fallback="English law with arbitration in Singapore for foreign customers.", risk="medium",
              why="Enforcement abroad is slow and costly."),
     ]},
    {"name": "Statement of Work Playbook",
     "description": "Statements of Work under a signed master agreement. Used for Statement of Work requests.",
     "rules": [
         rule("scope_and_deliverables",
              "Every deliverable is named with a due date and an owner; anything not listed is out of scope.",
              risk="high", why="Vague scope is the most common cause of disputes and overruns."),
         rule("acceptance",
              "We accept deliverables in writing after testing against agreed acceptance criteria within 10 working "
              "days.", fallback="Deemed acceptance only after 15 working days and a reminder.",
              prohibited="deemed accepted", risk="medium",
              why="Deemed acceptance lets the provider invoice for work we have not checked."),
         rule("fees_and_pricing",
              "Fixed fee or time-and-materials with a not-to-exceed cap; rates as in the master agreement.",
              fallback="Rates up to 5% above the master rate card with Finance approval.", risk="medium",
              approval=True, escalate="Finance", why="Uncapped time-and-materials has no budget ceiling."),
         rule("milestone_payments",
              "Payments are tied to accepted milestones, not to dates.", risk="medium",
              why="We pay for delivered value, not the passage of time."),
         rule("change_control",
              "Changes to scope, fees or dates only through a written change order signed by both parties.",
              risk="medium", why="Stops scope creep through emails and calls."),
         rule("order_of_precedence",
              "The master agreement prevails over the SoW, except for scope and fees stated in the SoW.",
              risk="high", approval=True,
              why="A SoW must not quietly override the negotiated master terms (liability, IP, data)."),
         rule("key_personnel",
              "Named key personnel are not replaced without our consent.", risk="low",
              why="We engaged these people for this work."),
     ]},
    {"name": "Data Processing Agreement Playbook",
     "description": "DPAs where a partner processes personal data for us (or with us). Used for Data Processing Agreement requests.",
     "rules": [
         rule("processing_instructions",
              "The processor processes personal data only on our documented instructions and for the stated purpose.",
              required="instructions", risk="high", escalate="Compliance",
              why="Required by the DPDP Act and GDPR; processing beyond instructions is a breach."),
         rule("sub_processors",
              "Sub-processors only with our prior written authorisation, bound by the same obligations, with the "
              "processor fully liable for them.", fallback="General authorisation with 30 days' notice and a right "
              "to object.", required="sub-processor", risk="high", escalate="Compliance",
              why="We stay responsible for everyone who touches the data."),
         rule("breach_notification",
              "Personal data breaches are notified to us without undue delay and within 48 hours, with the details we "
              "need to notify regulators and individuals.", fallback="Within 72 hours.",
              required="personal data breach", risk="critical", approval=True, escalate="Compliance",
              why="We have our own statutory notice deadlines."),
         rule("security_measures",
              "Appropriate technical and organisational measures, described in an annex, including encryption and "
              "access control.", required="security measures", risk="high", escalate="Compliance",
              why="Security obligations must be concrete enough to check."),
         rule("international_transfers",
              "No transfer outside India (or the EEA for EU data) without Standard Contractual Clauses or another "
              "lawful safeguard.", required="international transfers", risk="high", approval=True, escalate="Compliance",
              why="Unlawful transfers carry regulatory penalties."),
         rule("audit",
              "We may audit, or receive independent audit reports, to verify compliance.",
              fallback="Annual independent report (ISO 27001 / SOC 2) instead of on-site audits.", required="audit",
              risk="medium", why="Lets us demonstrate our own compliance."),
         rule("data_subject_rights",
              "The processor helps us answer data subject requests promptly.", required="data subject",
              risk="medium", why="We must meet statutory response times."),
         rule("return_or_deletion",
              "At the end of the services, personal data is returned or deleted at our choice, and deletion is "
              "certified.", required="deletion", risk="medium", why="Data must not outlive its purpose."),
     ]},
]

# The contract type each New-agreement kind (or form) is stored as, so the
# matching playbook above is picked for it.
CONTRACT_TYPE_OF_KIND = {
    "NDA": "NDA", "Services (MSA)": "MSA", "Consultancy": "Consultancy Agreement",
    "Buying from a vendor": "Vendor Agreement", "Software or SaaS": "Software / SaaS Agreement",
    "Selling to a customer": "Customer Agreement",
}
CONTRACT_TYPE_OF_FORM = {"sow": "Statement of Work", "dpa": "DPA"}


def seed_playbook_library(db: Session, *, org_id: str, actor_id: str | None) -> list[str]:
    """Create any library playbook this org doesn't have (by name), published,
    so the automatic review after drafting has one to run. Idempotent; never
    touches a playbook that exists. Returns the names added; caller commits."""
    have = {n.lower() for n in db.scalars(
        select(Playbook.name).where(Playbook.org_id == org_id, Playbook.deleted_at.is_(None))).all()}
    added = []
    for spec in PLAYBOOK_LIBRARY:
        if spec["name"].lower() in have:
            continue
        pb = Playbook(org_id=org_id, name=spec["name"], description=spec["description"],
                      status=PlaybookStatus.PUBLISHED, created_by_user_id=actor_id, updated_by_user_id=actor_id)
        db.add(pb)
        db.flush()
        version = PlaybookVersion(org_id=org_id, playbook_id=pb.id, version_number=1, status=PlaybookStatus.PUBLISHED,
                                  summary="Starter playbook", source_metadata={"library": True},
                                  created_by_user_id=actor_id, updated_by_user_id=actor_id)
        db.add(version)
        db.flush()
        pb.current_version_id = version.id
        for r in spec["rules"]:
            db.add(PlaybookRule(org_id=org_id, playbook_version_id=version.id,
                                created_by_user_id=actor_id, updated_by_user_id=actor_id, **r))
        write_audit_log(db, action="playbook.published", resource_type="playbook", resource_id=pb.id, org_id=org_id,
                        actor_user_id=actor_id,
                        after={"name": pb.name, "source": "library", "rules": len(spec["rules"]), "version_number": 1})
        added.append(pb.name)
    return added
