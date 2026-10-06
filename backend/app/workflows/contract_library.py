"""The starter workflow for every agreement type on the request forms.

Each entry sits under one agreement type (``used_for``) with the conditions
that choose it among that type's workflows. Values are in INR; the thresholds
are starting points an admin edits in the builder, not policy.

Shape follows the way these agreements are really handled:

* Our paper is drafted from the template; on their paper the draft step skips
  itself (the contract already came from their document) and the AI review
  runs on their draft — so paper only splits a type when the PATH differs.
* Reviews that only sometimes apply (Privacy when personal data is shared,
  Quality for GxP work, Finance above a value) are conditional steps inside one
  workflow, not extra workflows.
* Value tiers split workflows where the approvers genuinely differ.

Team keys are resolved to this org's teams at seed time (seed_builtin_flows).
"""

from __future__ import annotations

import uuid

from app.workflows import stages

_LOADED = "Auto — least-loaded in team"
_HEAD = "Auto — team head"
_SPECIFIC = "Specific person"
_ANY = "Any one member"

REVIEW = ["approve", "request_changes", "need_info", "escalate"]
DECIDE = ["approve", "reject", "escalate"]
SIGN = ["sign", "decline"]

# Step conditions (run this step only when …) on the request's answers.
PERSONAL_DATA = {"field": "personal_data", "op": "eq", "value": "true"}
GXP = {"field": "gxp", "op": "eq", "value": "true"}


def value_at_least(amount: int) -> dict:
    return {"field": "contract_value", "op": "gte", "value": amount}


def _step(type_, name, team, assign_by, sla, outcomes, instructions, *, parallel=False, cond=None, **extra):
    cfg = {"sla_hours": sla, "outcomes": outcomes, "instructions": instructions, **extra}
    if team:
        cfg["team"] = team
    if assign_by:
        cfg["assign_by"] = assign_by
    step = {"id": str(uuid.uuid4()), "type": type_, "name": name, "config": cfg}
    if parallel:
        step["parallel"] = True
    if cond:
        step["cond"] = cond
    return step


def draft(team="legal_counsel", sla=24, what="Draft from the template."):
    return _step("clm_draft", "Prepare draft", team, _LOADED, sla, ["complete"], what)


def ai_review(agent="contract_review_agent", what="AI review against the playbook; low confidence goes to a person."):
    return _step("ai_task", "AI review", "legal_counsel", _LOADED, 4, [], what, agent=agent, escalate_below_confidence=0.6)


def review(name, team, sla, what, *, cond=None, parallel=False, assign_by=_LOADED):
    return _step("human_task", name, team, assign_by, sla, REVIEW, what, cond=cond, parallel=parallel)


def privacy(parallel=True):
    return review("Privacy review", "compliance", 48, "Check the data-protection terms.", cond=PERSONAL_DATA,
                  parallel=parallel, assign_by=_HEAD)


def quality():
    return review("Quality review", "compliance", 48, "Confirm the GxP obligations.", cond=GXP, assign_by=_HEAD)


def negotiate(sla=120):
    return _step("counterparty", "Counterparty negotiation", None, _SPECIFIC, sla, ["approve", "request_changes"],
                 "Exchange redlines with the counterparty until both sides agree.")


def approve(name, team, sla=24, what="Approve the agreed terms.", cond=None):
    return _step("approval", name, team, _ANY, sla, DECIDE, what, cond=cond)


def sign(sla=48):
    return _step("signature", "Signature", "legal_counsel", _SPECIFIC, sla, SIGN, "Send for signature.")


def activate():
    return _step("ai_task", "Activate & track", None, _LOADED, 0, [], "File the contract and start obligation tracking.")


def notify(name, message):
    return _step("notify", name, None, None, 0, [], message, message=message)


def new(kind: str) -> list[dict]:
    return [{"form": "new_agreement", "agreement_type": kind}]


def form(key: str) -> list[dict]:
    return [{"form": key, "agreement_type": None}]


def is_(field, value):
    return {"field": field, "op": "is", "value": value}


def under(amount):
    return {"field": "value", "op": "under", "value": float(amount), "currency": "INR"}


def at_least(amount):
    return {"field": "value", "op": "at_least", "value": float(amount), "currency": "INR"}


LAKH = 100_000
CRORE = 100 * LAKH


def at(stage: str, step: dict) -> dict:
    """Pin a step to a lifecycle stage the inference wouldn't pick on its own."""
    return {**step, "stage": stage}


def _flow(name, description, used_for, conditions, steps, order):
    return {"name": name, "description": description, "eval_order": order, "criteria": {},
            "used_for": used_for, "conditions": conditions, "steps": stages.infer(steps)}


CONTRACT_FLOWS = [
    # ---- NDA (NDA Fast-Track in the library covers our template, needed within 7 days) ----
    _flow("NDA — standard", "Our template, mutual, not urgent: drafted from the template, AI review, Legal review, sign.",
          new("NDA"), [is_("paper", "Our template"), is_("nda_kind", "Mutual")],
          [draft("paralegals", 24, "Draft from the mutual NDA template."), ai_review("nda"),
           review("Legal review", "legal_counsel", 48, "Check the term, governing law and anything bespoke."), sign(), activate()], 20),
    _flow("NDA — one-way", "Our template, one-way. A one-way NDA protects only one side, so Legal checks the direction and term.",
          new("NDA"), [is_("paper", "Our template"), is_("nda_kind", "One-way")],
          [draft("paralegals", 8, "Draft from the one-way NDA template."), ai_review("nda"),
           review("Legal review", "legal_counsel", 24, "Check who discloses, the term and governing law."), privacy(), sign(), activate()], 21),
    _flow("NDA — their paper", "The counterparty's NDA: AI review against our NDA playbook, Legal review, negotiate, sign.",
          new("NDA"), [is_("paper", "Their paper")],
          [ai_review("nda", "AI review of their NDA against our NDA playbook."),
           review("Legal review", "legal_counsel", 24, "Review their NDA's deviations from our playbook."), privacy(),
           negotiate(72), sign(), activate()], 22),

    # ---- Services (MSA): Master Services Agreement (library) is under 50 lakh ----
    _flow("MSA — high value", "Services of INR 50 lakh and more: the standard MSA path plus Executive approval.",
          new("Services (MSA)"), [at_least(50 * LAKH)],
          [draft(), review("Legal review", "legal_counsel", 48, "Review scope, liability and IP against the playbook."),
           quality(), privacy(), negotiate(), approve("Finance approval", "finance", 24, "Approve the contract value and payment terms."),
           approve("Executive approval", "executive", 48, "Approve a high-value commitment."), sign(), activate()], 23),

    # ---- Buying from a vendor ----
    _flow("Vendor — small purchase", "Purchases under INR 5 lakh: Procurement checks the vendor and terms; no Finance approval.",
          new("Buying from a vendor"), [under(5 * LAKH)],
          [draft("paralegals", 24, "Draft from the vendor template."),
           review("Procurement review", "procurement", 24, "Check the vendor, pricing and sourcing policy."), privacy(),
           sign(), activate()], 24),
    _flow("Vendor — standard purchase", "Purchases of INR 5 lakh and more: Procurement, Legal and Finance, plus Executive approval from INR 1 crore.",
          new("Buying from a vendor"), [at_least(5 * LAKH)],
          [draft(), review("Procurement review", "procurement", 24, "Check the vendor, pricing and sourcing policy."),
           review("Legal review", "legal_counsel", 48, "Review liability, warranties and termination."), quality(), privacy(),
           negotiate(), approve("Finance approval", "finance", 24, "Approve the spend and payment terms."),
           approve("Executive approval", "executive", 48, "Approve a purchase of INR 1 crore or more.", cond=value_at_least(CRORE)),
           sign(), activate()], 25),

    # ---- Software or SaaS ----
    _flow("Software / SaaS purchase", "Software we buy: security review of the vendor and hosting, Legal review, Finance approval.",
          new("Software or SaaS"), [],
          [draft(), review("Security review", "compliance", 48, "Check hosting location, access to our systems and security terms.",
                           assign_by=_HEAD),
           privacy(), review("Legal review", "legal_counsel", 48, "Review licence scope, SLAs, liability and exit terms."),
           negotiate(), approve("Finance approval", "finance", 24, "Approve the subscription cost."), sign(), activate()], 26),

    # ---- Consultancy ----
    _flow("Consultancy", "An individual or firm advising us: Legal review, Finance approval from INR 10 lakh.",
          new("Consultancy"), [],
          [draft(what="Draft from the services template."),
           review("Legal review", "legal_counsel", 48, "Check scope, IP ownership, confidentiality and independence."), privacy(),
           negotiate(72), approve("Finance approval", "finance", 24, "Approve the fees.", cond=value_at_least(10 * LAKH)),
           sign(), activate()], 27),

    # ---- Selling to a customer (no customer template: Legal drafts or reviews theirs) ----
    _flow("Customer — standard", "Selling under INR 1 crore: Legal prepares or reviews the paper, Finance approves pricing.",
          new("Selling to a customer"), [under(CRORE)],
          [ai_review(what="AI review of the customer paper, if they sent one."),
           review("Legal review", "legal_counsel", 48, "Prepare our terms or review the customer's; check the non-standard asks."),
           privacy(), negotiate(), approve("Finance approval", "finance", 24, "Approve pricing, discounts and payment terms."),
           sign(), activate()], 28),
    _flow("Customer — strategic deal", "Selling for INR 1 crore or more: adds Executive approval of the commitment.",
          new("Selling to a customer"), [at_least(CRORE)],
          [ai_review(what="AI review of the customer paper, if they sent one."),
           review("Legal review", "legal_counsel", 48, "Prepare our terms or review the customer's; check the non-standard asks."),
           privacy(), negotiate(), approve("Finance approval", "finance", 24, "Approve pricing, discounts and payment terms."),
           approve("Executive approval", "executive", 48, "Approve a strategic customer commitment."), sign(), activate()], 29),

    # ---- Something else ----
    _flow("Other agreement", "Anything the other kinds don't cover: Legal decides the path.",
          new("Something else"), [],
          [review("Legal review", "legal_counsel", 48, "Work out what this agreement needs and prepare or review it."),
           privacy(), negotiate(), approve("Finance approval", "finance", 24, "Approve the value.", cond=value_at_least(10 * LAKH)),
           sign(), activate()], 30),

    # ---- Under an existing agreement ----
    _flow("Statement of Work", "Scope and fees under a signed master: Legal checks it fits the master, Finance approves the fees.",
          form("sow"), [],
          [at("drafting", review("Prepare SoW", "legal_counsel", 24, "Prepare the SoW against its master agreement's terms.")),
           quality(), privacy(), negotiate(72), approve("Finance approval", "finance", 24, "Approve the SoW fees."), sign(), activate()], 31),
    _flow("Data Processing Agreement", "A DPA from our template or theirs: Privacy leads, Legal signs off.",
          form("dpa"), [],
          [draft("compliance", 24, "Draft from the DPA template (skipped on their paper)."),
           review("Privacy review", "compliance", 48, "Check roles, data, transfers, sub-processors and breach terms.", assign_by=_HEAD),
           review("Legal sign-off", "legal_counsel", 24, "Confirm the DPA fits the main agreement."), negotiate(72), sign(), activate()], 32),

    # ---- Changing an agreement ----
    _flow("Amendment", "Legal prepares the amendment, the counterparty agrees, Finance approves value changes of INR 10 lakh or more.",
          form("amendment"), [],
          [at("drafting", review("Prepare amendment", "legal_counsel", 48, "Draft the amendment for the changes requested.")),
           negotiate(72), approve("Finance approval", "finance", 24, "Approve the new value.", cond=value_at_least(10 * LAKH)),
           sign()], 33),
    _flow("Renewal — same terms", "A straight extension: a paralegal checks the new dates and it goes for signature.",
          form("renewal"), [is_("renew_terms", "Same terms")],
          [at("drafting", review("Prepare renewal", "paralegals", 24, "Prepare the extension letter with the new end date.")), sign()], 34),
    _flow("Renewal — changed terms", "Renewing with changes: Legal review, negotiation and Finance approval.",
          form("renewal"), [is_("renew_terms", "Changed terms")],
          [review("Legal review", "legal_counsel", 48, "Review the changed terms."), negotiate(72),
           approve("Finance approval", "finance", 24, "Approve the new value."), sign()], 35),
    _flow("Termination — for breach", "Ending for breach: Legal checks the breach and cure period, Executive approves, the notice goes out.",
          form("termination"), [is_("grounds", "Breach by the other side")],
          [review("Legal review", "legal_counsel", 24, "Confirm the breach, the cure period and the notice required."),
           approve("Executive approval", "executive", 24, "Approve terminating for breach."),
           at("closed", review("Send termination notice", "legal_counsel", 24, "Serve the notice as the agreement requires."))], 36),
    _flow("Termination — other grounds", "Convenience, mutual agreement or non-renewal: Legal checks the notice period and serves notice.",
          form("termination"), [{"field": "grounds", "op": "is_not", "value": "Breach by the other side"}],
          [review("Legal review", "legal_counsel", 24, "Check the notice period and any termination fees."),
           approve("Business approval", "finance", 24, "Approve ending the agreement."),
           at("closed", review("Send termination notice", "legal_counsel", 24, "Serve the notice as the agreement requires."))], 37),
    _flow("Novation", "Moving an agreement to another company: consent from all sides, then all three sign.",
          form("novation"), [],
          [review("Legal review", "legal_counsel", 48, "Check consent, liabilities and the incoming party."),
           negotiate(120), approve("Finance approval", "finance", 24, "Approve the transfer of liabilities."), sign()], 38),

    # ---- Records ----
    _flow("Signed outside the system", "Brings an executed agreement onto the register and records the policy exception.",
          form("regularize"), [],
          [review("Legal review", "legal_counsel", 48, "Check the signed document, who signed and whether they had authority."),
           approve("Retrospective approval", "finance", 48, "Approve an agreement signed without the usual approvals.",
                   cond={"field": "prior_approval", "op": "ne", "value": "Yes, in full"}),
           at("active", activate())], 39),
]
