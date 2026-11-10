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
from app.intake.paper import MINIMUMS
from app.playbooks.models import Playbook, PlaybookRule, PlaybookVersion


def _floor(key: str, inr: str) -> str:
    """"INR 5 crore (or the equivalent in the currency of the fees: USD 600,000, EUR 550,000 or GBP 475,000)".
    Built from ``paper.MINIMUMS`` so the figures our paper writes and the figures this rule accepts cannot
    drift; a reviewer given only "the equivalent" cannot tell whether a stated foreign figure is one."""
    others = [MINIMUMS[c][key] for c in MINIMUMS if c != "INR"]
    return f"{inr} (or the equivalent in the currency of the fees: {', '.join(others[:-1])} or {others[-1]})"


# One sentence for every "provider indemnifies us" rule, so the template wording and the rules agree on who
# controls the defence (the indemnifying provider) and what we keep (take part at our cost, consent to settlement).
_DEFENCE = (" The provider controls the defence and settlement of an indemnified claim; we may take part at our "
            "own cost with our own counsel, and the provider may not settle in a way that admits fault for, or "
            "imposes an obligation on, us without our consent.")

_FIVE, _ONE = _floor("five_crore", "INR 5 crore"), _floor("one_crore", "INR 1 crore")


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


def _lines(*phrases: str) -> str:
    """Several phrases for one rule field, one per line (see playbooks.service._phrases)."""
    return "\n".join(phrases)


# Shared positions, reused where the same standard applies. Every preferred position covers every
# wording our own paper can produce; a fallback holds only what our paper never says, because the
# review reports a clause sitting at a fallback as a finding.
_BREACH_WINDOWS = _lines("within seventy-two (72) hours", "within 72 hours",
                         "within forty-eight (48) hours", "within 48 hours")


def _liability(position: str, fallback: str, *, required: str, prohibited: str = "unlimited liability") -> dict:
    return rule("limitation_of_liability", position, fallback=fallback, required=required, prohibited=prohibited,
                risk="high", approval=True,
                why="The cap decides the most we can lose on this contract; carve-outs keep the risks a cap must not cover.",
                guidance="Trade the multiple before the carve-outs. Never cap confidentiality or IP indemnity below the "
                         "fees; escalate any uncapped exposure for us.")


_MSE_PAYMENT = ("A supplier registered as a micro or small enterprise under the MSMED Act, 2006 is paid within 45 days "
                "at most whatever period is stated, and the contract says so.")


def _payment(position: str, fallback: str) -> dict:
    return rule("payment_terms", position, fallback=fallback,
                required=_lines("undisputed invoice", "Micro, Small and Medium Enterprises Development Act, 2006"),
                why="Protects cash flow and keeps leverage until the work is delivered, within the MSMED Act's "
                    "45-day limit for small suppliers.",
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
                f"{who} defends and indemnifies us against third-party claims arising from {covers}." + _DEFENCE,
                fallback="Indemnity limited to IP infringement and breach of law, with defence controlled by the "
                         "indemnifying party.",
                required="indemnify", risk="high",
                why="Third-party claims caused by the other side must not land on us.",
                guidance="Refuse any indemnity from us beyond our own breach of law or IP infringement.")


def _termination(convenience: str, fallback: str = "Termination for convenience on 60 days' notice.") -> dict:
    return rule("termination",
                f"We may terminate for convenience on {convenience}' notice, paying for work properly done. Either "
                "party may terminate for material breach not cured within 30 days.",
                fallback=fallback, required="terminate", risk="medium",
                why="We must be able to exit a relationship that no longer works without waiting for a breach.")


def _data_protection() -> dict:
    return rule("data_protection",
                "Where personal data is processed: the provider complies with the DPDP Act 2023 (and the GDPR where it "
                "applies), a DPA on our template applies from the start, and the provider tells us of a personal data "
                "breach within 24 hours (6 hours where a CERT-In report is required). Where no personal data is "
                "expected, the contract says so and still has our DPA signed with this Agreement, applying from the "
                "Effective Date, with no personal data processed until it is signed; a contract that says no personal data "
                "is expected and requires our DPA to be signed before any processing starts also meets this position and "
                "is not a deviation.",
                fallback="The counterparty's DPA if it meets the same standards, including the 24-hour window; "
                         "Privacy review required.",
                required=_lines("Digital Personal Data Protection Act, 2023", "twenty-four (24) hours"),
                prohibited=_BREACH_WINDOWS, risk="high", approval=True, escalate="Compliance",
                why="We have our own short statutory notice deadlines (DPDP Rules, CERT-In).")


def _governing_law() -> dict:
    return rule("governing_law",
                "Indian law. Disputes go to arbitration under the Arbitration and Conciliation Act, 1996 by a sole "
                "arbitrator seated in a named Indian city (an Indian institution such as MCIA where the provider is "
                "Indian), or, for a provider outside India, under the SIAC Rules seated in Singapore; either party may "
                "seek urgent interim relief from any competent court. A seat stated as the city of our registered "
                "office is a named seat. Where the contract itself states that the other party is incorporated or "
                "established outside India, a Singapore seat under the SIAC Rules meets this position. A clause saying the courts at the "
                "arbitration seat have supervisory jurisdiction over the arbitration is not the prohibited phrase: "
                "that phrase is about submitting the dispute itself to a foreign court instead of arbitration.",
                fallback="English law with SIAC arbitration seated in Singapore for a provider outside India, with "
                         "Legal Counsel approval.",
                required="laws of India",
                prohibited=_lines("exclusive jurisdiction of the courts of", "laws of the State of Delaware",
                                  "laws of the State of New York"),
                risk="high", approval=True, escalate="Legal Counsel",
                why="An award is enforceable abroad under the New York Convention; a court judgment against a foreign "
                    "provider often is not.")


def _tax_compliance() -> dict:
    return rule("tax_compliance",
                "Fees exclude GST; the supplier issues compliant tax invoices with its GSTIN, reports them and pays the "
                "tax so we keep our input tax credit, and reimburses credit lost through its default; GST on imported "
                "services is paid by us under reverse charge without increasing the fees; tax is withheld as the law "
                "requires with certificates given; a reduced treaty rate applies only on a tax residency certificate "
                "and the forms the law requires.",
                required=_lines("input tax credit", "reverse charge"), risk="medium",
                why="A supplier's GST default otherwise costs us the credit.")


_INSURANCE_WHY = "Insurance stands behind the provider's indemnities only if the amount is stated."
_NO_VAGUE_COVER = _lines("limits of cover appropriate to")


PLAYBOOK_LIBRARY: list[dict] = [
    {"name": "Master Services Agreement Playbook",
     "description": "Services we buy under a master agreement (MSA). Used for Services (MSA) requests, our template or theirs.",
     "rules": [
         _liability("Each party's total liability is capped at the greater of the fees paid or payable in the 12 months "
                    "before the claim and the fees payable for the first 12 months (\"fees paid or payable under this "
                    "Agreement in the twelve (12) months immediately preceding the event giving rise to the claim\" "
                    "meets this position, and a total contract value stated elsewhere in the Agreement does not "
                    "narrow it: that figure is a spending figure only and never reduces the cap). Indirect and consequential losses are excluded. The "
                    "cap does not apply to breach of confidentiality or data protection, the provider's indemnities "
                    "(including for IP infringement), gross negligence, wilful misconduct, fraud, or death or personal "
                    "injury.",
                    "Up to 2x the annual fees; anything higher needs Legal Counsel approval.",
                    required=_lines("limitation of liability", "paid or payable"),
                    prohibited=_lines("Customer shall have unlimited liability",
                                      "Customer's liability shall be unlimited")),
         rule("indemnification",
              "The provider defends and indemnifies us against third-party claims arising from its breach of "
              "confidentiality, data protection or law, its negligence, wilful misconduct or fraud, or infringement of "
              "third-party IP." + _DEFENCE,
              fallback="Indemnity limited to IP infringement and breach of law, with defence controlled by the "
                       "indemnifying party.",
              required="indemnify", risk="high",
              why="Third-party claims caused by the other side must not land on us.",
              guidance="Refuse any indemnity from us beyond our own breach of law or IP infringement."),
         rule("intellectual_property",
              "Deliverables created for us belong to us on payment, assigned worldwide for the full term of the rights "
              "with no lapse for non-use (sections 19(4) and 19A of the Copyright Act do not apply). The provider keeps "
              "its pre-existing IP and grants us a perpetual, royalty-free licence to use it within the deliverables.",
              fallback="Provider owns generic tools; we own everything specific to our business and data.",
              required=_lines("intellectual property", "full term"), risk="high",
              why="We pay for the work, so we must be able to use and change it without the provider."),
         _payment("Payment within 45 or 60 days of receipt of a valid, undisputed invoice, with disputed amounts "
                  f"withheld in good faith. {_MSE_PAYMENT} Any advance or mobilisation payment is no more than 20% of "
                  "the fees and is credited against later invoices.",
                  "90 days for a supplier that is not a micro or small enterprise, with Finance approval."),
         _termination("30 days"),
         _confidentiality(),
         rule("insurance",
              f"The provider holds professional indemnity insurance of at least {_FIVE} per claim, "
              "cyber liability insurance of at least the same amount where it "
              "handles personal data or our systems, and general liability cover, for the term and 1 year after, with "
              "certificates on request. Cover of the stated amount per claim, with or without the words \"and in the aggregate\", meets this position.",
              fallback="Certificates on request instead of named-insured status.", required="per claim",
              prohibited=_NO_VAGUE_COVER, risk="medium", why=_INSURANCE_WHY),
         rule("service_levels",
              "Service levels and credits are set in each SOW. Where an SOW sets service levels without credits, the "
              "MSA's default applies: 5% of the month's fees for the affected services per failure, capped at 15% of "
              "the month's fees under that SOW, with termination for repeated or critical failure. An SOW for project "
              "deliverables with no service levels needs none, and the MSA is not flagged for it.",
              fallback="Credits capped at 10% of monthly fees with a termination right for repeated failure.",
              required="fifteen percent (15%)", prohibited="sole and exclusive remedy", risk="medium",
              why="Without measurable service levels there is no remedy short of termination."),
         _data_protection(),
         _governing_law(),
         _tax_compliance(),
     ]},
    {"name": "Consultancy Agreement Playbook",
     "description": "An individual or firm advising us. Used for Consultancy requests.",
     "rules": [
         rule("intellectual_property",
              "Everything the consultant creates for us is assigned to us on creation, the fees being the full "
              "consideration, worldwide and for the full term, with no lapse for non-use (section 19(4) of the "
              "Copyright Act does not apply); the consultant waives and does not assert moral rights where the law "
              "allows.",
              fallback="Assignment on payment of the related invoice, with a licence to use the work until then.",
              required=_lines("intellectual property", "section 19(4)"), risk="high",
              why="Advice and material we pay for must be ours to use."),
         rule("independent_contractor",
              "An independent contractor: no employment relationship; the consultant pays its own taxes and statutory "
              "contributions and uses its own tools, is free to serve others, works no fixed hours, and is the "
              "employer of its own personnel, complying with labour law on our premises.",
              required="independent contractor", risk="high", approval=True, escalate="Legal Counsel",
              why="Misclassification creates employment and tax liability for us."),
         _confidentiality(),
         rule("conflicts_of_interest",
              "The consultant discloses conflicts of interest and does not advise our competitors on the same matter "
              "during the engagement.", fallback="Disclosure of conflicts only.", risk="medium",
              why="Advice must be independent of competing interests."),
         rule("non_solicitation",
              "Neither party solicits the other's staff for 12 months after the engagement.", risk="low",
              fallback="6 months.", why="Protects our team."),
         _payment("Payment within 45 or 60 days of receipt of a valid, undisputed invoice, with disputed amounts "
                  f"withheld in good faith. {_MSE_PAYMENT} Any advance or mobilisation payment is no more than 20% of "
                  "the fees and is credited against later invoices.",
                  "90 days for a consultant that is not a micro or small enterprise, with Finance approval."),
         _termination("15 days", "Termination for convenience on 30 days' notice."),
         _liability("Each party's total liability is capped at the fees paid or payable under the agreement. Indirect "
                    "and consequential losses are excluded. The cap does not apply to breach of confidentiality "
                    "(including a personal data breach), the consultant's indemnities and its tax and statutory "
                    "obligations, gross negligence, wilful misconduct, fraud, or death or personal injury.",
                    "Up to 2x the fees.", required=_lines("limitation of liability", "paid or payable"),
                    prohibited="Company shall have unlimited liability"),
         rule("governing_law",
              "Indian law, with the exclusive jurisdiction of the courts at a named Indian city (our registered city, "
              "or the city of our registered office), for every consultant, individual or firm, Indian or foreign; "
              "either party may seek urgent interim relief from any competent court. Arbitration (MCIA, or SIAC seated "
              "in Singapore for a consultant outside India) also meets this position when a consultant's paper offers "
              "it.",
              fallback="English or Singapore law with SIAC arbitration for a consultant outside India, with Legal "
                       "Counsel approval.",
              required=_lines("laws of India", "courts at"),
              prohibited=_lines("exclusive jurisdiction of the courts of", "laws of the State of Delaware"),
              risk="high", approval=True, escalate="Legal Counsel",
              why="An agreed, named forum avoids a fight over where a dispute is heard."),
         rule("insurance",
              f"Professional indemnity insurance of at least {_FIVE} per claim "
              f"and public liability insurance of at least {_ONE} per claim during the engagement and 2 years after; cyber liability of the "
              "same amount where the consultant handles personal data or our systems. Cover of the stated amount per claim, with or without the words \"and in the aggregate\", meets this position.",
              required="per claim", prohibited=_NO_VAGUE_COVER, risk="medium", why=_INSURANCE_WHY),
         _data_protection(),
         _tax_compliance(),
         rule("non_compete",
              "No non-compete binding either party beyond the conflicts-of-interest restriction during the "
              "engagement; nothing restricts the Company from engaging others or doing similar work.",
              prohibited=_lines("shall not compete", "non-compete", "not to compete"), risk="high", approval=True,
              escalate="Legal Counsel",
              why="A restraint on our business belongs in a negotiated commercial agreement, not a consultancy."),
     ]},
    {"name": "Vendor Agreement Playbook",
     "description": "Goods or services we buy from a supplier. Used for Buying from a vendor requests.",
     "rules": [
         _liability("Each party's total liability is capped at the greater of the fees paid and payable in the 12 "
                    "months before the claim and the fees payable under all orders in the first 12 months. Indirect "
                    "losses are excluded. Uncapped: breach of confidentiality, the vendor's indemnities (defective "
                    "goods, breach of law including data protection, negligence, wilful misconduct or fraud, IP "
                    "infringement, claims by its personnel), gross negligence, wilful misconduct, fraud, and death or "
                    "personal injury.",
                    "A super-cap of at least 3x annual fees for the vendor's indemnities for defective goods and "
                    "negligence, with IP and data protection indemnities uncapped, with Legal Counsel approval.",
                    required=_lines("limitation of liability", "first twelve (12) months")),
         rule("indemnification",
              "The vendor defends and indemnifies us against third-party claims arising from defective goods, its "
              "breach of law, negligence or infringement of third-party IP, and claims by its personnel for wages or "
              "statutory dues." + _DEFENCE,
              fallback="Indemnity limited to IP infringement and breach of law, with defence controlled by the "
                       "indemnifying party.",
              required="indemnify", risk="high",
              why="Third-party claims caused by the other side must not land on us.",
              guidance="Refuse any indemnity from us beyond our own breach of law or IP infringement."),
         rule("warranties",
              "The vendor warrants that what it supplies conforms to the specification and is free from defects for at "
              "least 12 months; services are performed with reasonable skill and care and meet any service levels an "
              "order sets.",
              fallback="6-month warranty with repair or replacement at the vendor's cost.",
              required="warrants", risk="medium", why="Warranties give us a remedy when what we buy fails."),
         rule("compliance",
              "The vendor complies with anti-bribery, sanctions, labour and environmental law and our supplier code of "
              "conduct, and we may terminate for breach. It is solely responsible for the wages and statutory dues of "
              "its personnel.", required="anti-bribery", risk="high", approval=True,
              escalate="Compliance", why="A supplier's breach of law becomes our reputational and legal problem."),
         _payment("Payment within 45, 60 or 90 days of receipt of a valid, undisputed invoice, with disputed amounts "
                  f"withheld in good faith. {_MSE_PAYMENT} For a registered micro or small supplier, payment within the agreed "
                  "period or 45 days from acceptance, whichever is shorter, meets this position. Any advance is no more "
                  "than 20% of the order's fees.",
                  "30 days for a strategic supplier that is not a micro or small enterprise, with Finance approval."),
         rule("subcontracting",
              "The vendor may not subcontract or assign without our written consent and stays responsible for its "
              "subcontractors.", required="consent", risk="medium",
              why="We chose this vendor; others must not do the work without our say."),
         rule("audit_rights",
              "We may audit the vendor's records relating to the agreement on reasonable notice.",
              fallback="Audit by an independent auditor once a year.", risk="low",
              why="Lets us verify charges and compliance."),
         _data_protection(),
         rule("delivery_and_acceptance",
              "Goods are delivered DAP (Incoterms 2020) to the named place, with title and risk passing on our written "
              "acceptance; short or excess quantities may be rejected; the vendor runs recalls at its cost. Services "
              "are accepted only on our written confirmation. Late delivery costs 0.5% of the late item's fees per "
              "week, capped at 10%, and we may end the order after 30 days' delay. On termination for convenience we "
              "pay only for accepted deliverables.",
              required="confirms acceptance in writing", prohibited="deemed accepted", risk="medium",
              why="We must check what we buy before it counts as delivered and paid for."),
         rule("insurance",
              "A goods supplier holds product and public liability insurance of at least the greater of the contract "
              f"value and {_ONE} per claim; a supplier of services under an Order holds professional indemnity insurance of "
              f"at least {_FIVE} per claim (installing, repairing, replacing or re-performing delivered goods under "
              "the warranty is part of the goods supply and does not make a goods supplier a services supplier); cyber liability of at least the same amount as the professional indemnity cover "
              "where it handles personal data or our "
              "systems; for the term and 1 year after, with evidence "
              "on request. Cover of the stated amount per claim, with or without the words \"and in the aggregate\", meets this position.",
              required="per claim",
              prohibited=_lines("limits of cover appropriate to", "insurance adequate to cover its liabilities"),
              risk="medium", why=_INSURANCE_WHY),
         _governing_law(),
         _tax_compliance(),
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
              "us of a Security Incident within 24 hours (6 hours where a CERT-In report is required).",
              fallback="Equivalent controls evidenced by a security "
              "questionnaire reviewed by Compliance.",
              required=_lines("Security Incident", "twenty-four (24) hours"), prohibited=_BREACH_WINDOWS,
              risk="high", approval=True,
              escalate="Compliance", why="Our data in someone else's system is only as safe as their controls."),
         rule("data_location",
              "Customer data stays in a named location: India is preferred; the EU, the US or other regions named in "
              "the order form only with our privacy function's written approval, and never in a country restricted "
              "under section 16 of the DPDP Act; data subject to the GDPR leaves the EEA or UK only under SCCs or an "
              "equivalent safeguard. A location (including the EU or the US) stated in the Order Form and subject to the "
              "prior written approval of the Customer's privacy function, given before any Customer Data is stored "
              "there, IS the preferred position, not a fallback or a deviation; India need not also be named. Regions listed "
              "in an Order Form signed together with the Agreement, each with that approval, and no other region, "
              "also meet the position: the location is fixed at signature, so it is not left open.",
              required="privacy function",
              prohibited=_lines("any location chosen by the Provider",
                                "any country in which the Provider or its sub-processors operate"),
              risk="high", approval=True, escalate="Compliance",
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
              "Renewal only with at least 60 days' notice to cancel, the same period wherever notice is stated, and a "
              "written reminder from the provider before the cancellation deadline. A written reminder at least 30 days "
              "before the cancellation deadline meets this position.",
              fallback="30 days' notice to cancel.", required="renewal reminder", risk="medium",
              why="Silent auto-renewal commits budget we have not approved."),
         _liability("General cap: the greater of the fees paid and payable in the 12 months before the claim and the "
                    "fees payable for the first 12 months. Data-breach liability (a security incident, or breach of "
                    "the data security, data location or data protection terms, including where it is also a breach of "
                    "confidentiality or falls under the data indemnity) is under one higher cap: the greater of 3x the "
                    f"general cap and {_FIVE}. Uncapped: the IP "
                    "indemnity (stated to be unlimited and outside every cap), other breach of confidentiality, gross negligence, wilful misconduct, fraud, and death "
                    "or personal injury. Indirect losses excluded, with data restoration, breach notification costs and "
                    "regulatory fines treated as direct.",
                    "A single cap of 2x annual fees covering data breaches, with Legal Counsel approval.",
                    required=_lines("limitation of liability", "Data Breach Cap")),
         _payment("Payment within 45, 60 or 90 days of receipt of a valid, undisputed invoice; billed quarterly in "
                  "arrears unless the order form says otherwise (annual in advance only with a pro-rata refund on early "
                  "termination, and with Finance approval above INR 10 lakh); a micro or small enterprise is paid "
                  "within 45 days at most and the contract says so.",
                  "30 days for a strategic provider that is not a micro or small enterprise, with Finance approval."),
         rule("suspension",
              "The provider may suspend only briefly for an imminent security threat with notice, or for undisputed "
              "fees more than 30 days overdue after two written notices; never while an amount is disputed in good "
              "faith.", required="suspend", prohibited="suspend the Service at any time", risk="medium",
              why="A supplier switching off a system we depend on is a business-continuity risk."),
         rule("business_continuity",
              "A tested (yearly) business continuity and disaster recovery plan, daily backups in-region, RPO 24 hours "
              "and RTO 8 hours unless the order form says otherwise.",
              required="disaster recovery", risk="medium",
              why="We must be able to recover our service and data after an outage."),
         rule("subprocessors",
              "A current sub-processor list, 30 days' notice of a new one with a right to object, and if the objection "
              "is not resolved in 30 days, a right to end the affected order with a refund of prepaid fees.",
              required="object", risk="high", approval=True, escalate="Compliance",
              why="We stay responsible for everyone who touches our data."),
         rule("data_use",
              "Customer data is used only to provide the service; never to train or improve AI models, benchmark, or "
              "build data sets for others without our written consent.",
              required="train", prohibited="may use Customer Data to improve", risk="high", approval=True,
              escalate="Compliance", why="Our data must not become the provider's product."),
         _governing_law(),
         _tax_compliance(),
         rule("insurance",
              f"The provider holds professional indemnity and cyber liability insurance of at least {_FIVE} each per "
              "claim for the subscription term and 1 year after, with "
              "certificates on request. Cover of the stated amount per claim, with or without the words \"and in the aggregate\", meets this position.",
              fallback="Certificates on request instead of named-insured status.", required="per claim",
              prohibited=_NO_VAGUE_COVER, risk="medium", why=_INSURANCE_WHY),
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
              "Every deliverable is named with a due date and an owner; anything not listed is out of scope. The scope "
              "describes the real work in words, not placeholder text.",
              risk="high", why="Vague scope is the most common cause of disputes and overruns."),
         rule("acceptance",
              "We accept deliverables in writing after testing against agreed acceptance criteria within 10 working "
              "days.", fallback="Deemed acceptance only after 15 working days and a reminder.",
              prohibited="deemed accepted", risk="medium",
              why="Deemed acceptance lets the provider invoice for work we have not checked."),
         rule("fees_and_pricing",
              "One pricing model, stated with its amount: a fixed fee (lump sum or instalments tied to accepted "
              "milestones) with no rates and changes priced by change order; or time and materials at rates in the SOW "
              "or the master agreement (or, where neither sets rates, agreed in a signed change order before any "
              "charge) under a not-to-exceed cap the provider may not pass without a signed change order.",
              fallback="Rates up to 5% above the master rate card with Finance approval.",
              prohibited="rates may be increased", risk="medium",
              approval=True, escalate="Finance", why="Uncapped time-and-materials has no budget ceiling."),
         rule("milestone_payments",
              "For a fixed-fee or milestone SOW, payments are tied to accepted deliverables, not to dates. A "
              "time-and-materials SOW is invoiced monthly in arrears for time worked, with a hold-back until "
              "acceptance, and needs no milestones.",
              prohibited="payable on signature of this SOW", risk="medium",
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
         rule("service_levels",
              "An SOW for ongoing services sets measurable service levels with credits (or the master agreement's "
              "default credits apply); an SOW for project deliverables states that no service levels apply, any being "
              "added only by signed change order.",
              required="Service Level", risk="medium",
              why="Without measurable service levels there is no remedy short of termination."),
         rule("payment_terms",
              "Payment follows the master agreement's payment period; the SOW does not state its own.",
              required="period set out in the Master Agreement", risk="medium",
              why="Two payment periods in one deal invite a dispute about which applies."),
         rule("governing_law",
              "The SOW follows the law and dispute resolution of the master agreement and does not state its own.",
              required="set out in the Master Agreement", prohibited="This SOW is governed by the laws of",
              risk="medium", why="One law and one forum for the whole relationship."),
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
              "to object, and a right to end the affected services if the objection is not resolved in 30 days.",
              required="processor", risk="high", escalate="Compliance",
              why="We stay responsible for everyone who touches the data."),
         rule("breach_notification",
              "Personal data breaches are notified to us without undue delay and within 24 hours (6 hours where a "
              "CERT-In report is required), with their nature, the people and records affected, likely consequences, "
              "measures taken, the date, time and place, the cause and the person responsible, so we can notify the "
              "Data Protection Board and individuals.",
              required="personal data breach", prohibited=_BREACH_WINDOWS, risk="critical", approval=True,
              escalate="Compliance", why="We have our own statutory notice deadlines."),
         rule("security_measures",
              "Appropriate technical and organisational measures, described in an annex, including encryption and "
              "access control.", required="security measures", risk="high", escalate="Compliance",
              why="Security obligations must be concrete enough to check."),
         rule("international_transfers",
              "No transfer outside India without our written authorisation, and never to a country restricted under "
              "section 16 of the DPDP Act; for EU or UK data, only under SCCs (with the UK Addendum) or another "
              "Chapter V safeguard. Wording that forbids any transfer without our prior written authorisation meets this "
              "position.",
              required="transfer", prohibited="may transfer Personal Data to any country", risk="high",
              approval=True, escalate="Compliance", why="Unlawful transfers carry regulatory penalties."),
         rule("audit",
              "We may audit, or receive independent audit reports, to verify compliance.",
              fallback="An annual independent report (ISO 27001 / SOC 2) instead of routine on-site audits, keeping "
                       "on-site audits after a breach or at a regulator's demand.",
              required="audit", risk="medium", why="Lets us demonstrate our own compliance."),
         rule("data_subject_rights",
              "The processor forwards a Data Principal's (data subject's) request to us within 2 business days and "
              "helps us answer it within 5 business days.", required="request",
              risk="medium", why="We must meet statutory response times."),
         rule("return_or_deletion",
              "At the end of the services, personal data is returned or deleted (erased) at our choice, with deletion "
              "certified; specific data is erased within 10 business days of our instruction.",
              prohibited="retain Personal Data for its own", risk="medium", why="Data must not outlive its purpose."),
         rule("liability",
              "The processor's liability for data protection breaches is under one higher limit: the greater of 3x the "
              "fees paid or payable under the principal agreement in the 12 months before the claim and INR 5 crore, "
              "never below the principal agreement's general limit; fines caused by the processor or its staff are "
              "recoverable under the indemnity within that limit.",
              required="three (3) times", risk="high", approval=True, escalate="Legal Counsel",
              why="Data protection fines and claims can exceed the contract's ordinary cap."),
         rule("term",
              "The DPA runs from its effective date until the later of the end of the principal agreement and the "
              "return or deletion of all personal data; no separate fixed term.",
              required="return or deletion of all Personal Data", risk="medium",
              why="Personal data must stay protected for as long as the processor holds it."),
         rule("processing_description",
              "An annex describes the subject matter, duration, nature and purpose, data types and categories of "
              "people, and processing is limited to it.",
              required="Annex 1", risk="medium", why="The processor may only do what the annex describes."),
         rule("no_own_use",
              "No processing for the processor's own purposes, no sale, advertising or combination with other data, and "
              "no AI training.", required="own purposes", risk="high",
              why="Our data must not become the processor's product."),
         rule("confidentiality_of_personnel",
              "Only personnel who need access, each bound by confidentiality and trained.",
              required="confidentiality", risk="medium", why="People with access are the most common leak."),
         rule("assistance",
              "The processor assists with data protection impact assessments, prior consultations, records of "
              "processing and authority requests.", required="impact assessment", risk="medium",
              why="We carry these duties as controller and need the processor's help to meet them."),
         rule("children_data",
              "No tracking, behavioural monitoring or targeted advertising directed at children; children's data only "
              "on our instructions under section 9 of the DPDP Act.",
              required="children", risk="high", approval=True, escalate="Compliance",
              why="The DPDP Act restricts processing of children's data."),
         rule("governing_law",
              "The DPA follows the principal agreement's law and dispute route; the SCCs follow their own.",
              required="governs the Principal Agreement",
              prohibited="This Agreement shall be governed by and construed in accordance with the laws of",
              risk="medium", why="One law and one forum for the whole relationship."),
     ]},
]

# Our standard NDA positions, in step with defaults/nda.txt. Kept in code but not seeded into new
# organisations: NDAs use the org's own NDA playbook (test_ndas_are_left_to_the_orgs_own_nda_playbook),
# and the standard-update flow offers these to an org that already has "Corporate NDA Negotiation
# Playbook v1.0".
NDA_STANDARD: dict = {
    "name": "Corporate NDA Negotiation Playbook v1.0",
    "rules": [
        rule("definition_of_confidential_information",
             "Broad definition capturing all non-public information disclosed in any form (written, oral, electronic, "
             "visual), whether or not marked 'Confidential,' including technical data, trade secrets, business plans, "
             "financial data, customer lists, and AI/ML training data, datasets, model weights, and outputs derived "
             "from our information.",
             fallback="Accept a narrower definition limited to information marked 'Confidential' at time of "
                      "disclosure, provided oral disclosures are confirmed in writing within 10 business days, and "
                      "AI/ML data and training prohibitions are preserved as a standalone clause.",
             required="Confidential Information expressly includes: (a) all data, datasets, and information used to "
                      "train, fine-tune, or evaluate any artificial intelligence or machine learning model; (b) "
                      "outputs, embeddings, or derivative works generated using Disclosing Party's Confidential "
                      "Information; and (c) the fact that discussions between the parties are occurring.",
             prohibited="Any language that excludes AI-generated outputs, model training data, or derivative works from "
                        "the definition of Confidential Information. Any blanket exclusion of orally disclosed "
                        "information without a written confirmation mechanism.",
             risk="high",
             why="An overly narrow definition creates gaps that counterparties can exploit to use sensitive data "
                 "— especially AI/ML data — without restriction. Broad coverage is our first line of defense. "
                 "AI-data prohibitions are non-negotiable given the irreversible nature of model training."),
        rule("exclusions_from_confidential_information",
             "Standard four exclusions only: public through no fault of the receiving party, already known, received "
             "from a third party without restriction, or independently developed without use of the information; each "
             "shown by the receiving party's written records, with the burden on the receiving party; a combination is "
             "not excluded merely because its elements are.",
             required="written records", risk="medium",
             why="Exclusions must be provable but not set at a standard that binds us unreasonably when we receive "
                 "under a mutual NDA."),
        rule("permitted_use_and_standard_of_care",
             "Use only for the Purpose; at least reasonable care and no less than the care used for the receiving "
             "party's own similar information; disclosure only to Representatives (directors, officers, employees, "
             "Affiliates, professional advisers, contractors and service providers) who need to know and are bound by "
             "written or professional confidentiality, the receiving party being liable for them; no use to train or "
             "develop AI or machine learning models, except ordinary business tools whose provider does not train on "
             "the information. Wording that limits use to 'solely for the Purpose' meets this position.",
             required="train, fine-tune, test, or otherwise develop any artificial intelligence or machine learning "
                      "model",
             prohibited="for any purpose whatsoever", risk="high",
             why="Information must be used only for the deal, protected to a market standard, and never fed into "
                 "model training."),
        rule("compelled_disclosure_and_legal_process",
             "Receiving Party must provide prompt written notice to Disclosing Party as soon as legally permissible "
             "(and in any event before disclosure) upon receipt of any legal process, subpoena, court order, or "
             "regulatory demand requiring disclosure of Confidential Information. Receiving Party must: (a) cooperate "
             "with Disclosing Party's efforts to seek a protective order or other relief; (b) disclose only the minimum "
             "amount legally required; and (c) use reasonable efforts to obtain confidential treatment for any "
             "disclosed information. No notice is needed for a routine examination by a regulator or a requirement of "
             "a stock exchange that is not targeted at the disclosing party, provided confidential treatment is "
             "requested.",
             fallback="If notice before disclosure is legally prohibited, notice as soon as legally permitted after "
                      "disclosure, keeping the cooperation and minimum-disclosure obligations.",
             required="The Receiving Party shall: (a) provide prompt written notice to the Disclosing Party prior to any "
                      "such disclosure to the extent permitted by law; (b) cooperate fully with the Disclosing Party in "
                      "seeking a protective order, confidential treatment, or other appropriate relief; and (c) "
                      "disclose only that portion of the Confidential Information that is legally required to be "
                      "disclosed.",
             prohibited="deemed a waiver of all confidentiality obligations", risk="medium",
             why="We must be able to protect our information when a court or authority demands it, without breaching "
                 "on routine regulator visits."),
        rule("return_or_destruction_of_information",
             "Within 10 business days of the end of the agreement or a request, return or destroy all confidential "
             "information with written certification by an authorised representative; copies required by law, or held "
             "in automated backups not accessible in the ordinary course and overwritten in the normal cycle, may be "
             "kept and stay confidential.",
             fallback="A 30-day return or destruction window, with certification.",
             required="ten (10) business days", prohibited="internal business purposes", risk="medium",
             why="Our information must not outlive the purpose, but backup deletion on demand is not achievable."),
        rule("term_and_duration_of_confidentiality",
             "An agreement term of 2 to 5 years, with confidentiality surviving at least 3 years after the term ends "
             "(5 years is best), and trade secrets protected for as long as they remain trade secrets. The term and "
             "the survival period are separate: a 2-year term with 5 years' survival meets this position.",
             fallback="A 1-year term with at least 3 years' survival and trade secrets protected while secret.",
             required="for so long as it remains a trade secret",
             prohibited=_lines("expire upon termination of this Agreement",
                               "expire upon expiration or termination of this Agreement",
                               "terminate upon termination of this Agreement"),
             risk="high",
             why="Protection must last long enough after the deal ends; trade secrets must never lapse."),
        rule("governing_law_and_jurisdiction",
             "Indian law by default, or the law of England and Wales, Singapore, New York or Delaware where chosen for "
             "the transaction; the exclusive jurisdiction of the courts at a named city (our registered city, or the "
             "city of our registered office), with either party free to seek urgent interim relief from any competent "
             "court. Wording naming 'the exclusive jurisdiction of the courts at <city>' meets this position. "
             "Wording that makes the agreement 'governed by the laws of the State of Delaware' or 'the laws of the "
             "State of New York' meets this position where that law was chosen for the transaction: the prohibited "
             "phrase concerns submitting to the courts of a US state, not choosing a US state's law.",
             fallback="Arbitration (MCIA, or SIAC seated in Singapore for a foreign party) with a carve-out for urgent "
                      "court relief, with Legal approval.",
             required="courts at", prohibited="exclusive jurisdiction of the courts of the State of", risk="medium",
             why="A dispute forum must be named precisely and allow urgent relief."),
        rule("remedies_and_injunctive_relief",
             "Mutual: a breach may cause irreparable harm; either party may seek injunctive or equitable relief, to the "
             "extent permitted by law without posting a bond, in addition to other remedies; no fee-shifting to the "
             "winning party.",
             fallback="Nominal security as the court may require, keeping mutual injunctive relief.",
             required="injunctive", prohibited="limited to monetary damages", risk="high",
             why="Only an injunction stops a leak in time."),
        rule("non_compete_and_exclusivity",
             "No non-compete, exclusivity or standstill in an NDA.",
             prohibited=_lines("shall not compete", "non-compete", "exclusivity"), risk="high", approval=True,
             escalate="Legal Counsel",
             why="An NDA must not restrict our business; such terms belong in a negotiated commercial agreement."),
        rule("indemnity",
             "No indemnity in an NDA; remedies are damages and injunctive relief.",
             prohibited=_lines("indemnify", "hold harmless"), risk="high", approval=True, escalate="Legal Counsel",
             why="An indemnity turns a confidentiality promise into uncapped insurance."),
        rule("residuals",
             "No residuals clause: information kept in memory is still confidential.",
             prohibited="residuals", risk="high",
             why="A residuals clause lets the other side use our information freely once remembered."),
        rule("personal_data",
             "Personal data in confidential information is handled under the DPDP Act 2023 with reasonable security "
             "safeguards, and any personal data breach is notified without undue delay.",
             required=_lines("Digital Personal Data Protection Act, 2023", "without undue delay"), risk="medium",
             why="Personal data has statutory protection that an NDA must respect."),
        rule("assignment",
             "No assignment without consent, except to an affiliate or a successor to the business on notice.",
             required="successor", risk="low",
             why="We must be able to reorganise without the counterparty's consent."),
    ],
}

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
