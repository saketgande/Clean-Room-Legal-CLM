"""Prebuilt workflow library, ported from github.com/Letscode82/aegis
(``packages/workflow/src/library.ts``) — 10 pharma-GC governance ladders —
and mapped onto our flow-step schema:

  their HUMAN step            -> our "human_task"  (approver_role, sla_hours)
  their HUMAN @ gc/board rung  -> our "approval"    (delegates to the DoA ladder)
  their HUMAN @ signature_screen -> our "signature"
  their AGENT step             -> our "ai_task"     (agent, escalate_role,
                                   escalate_below_confidence, sla_hours)
  their metadataJson.skip_if   -> preserved on config.skip_if (field/op/value)

Selection ``criteria`` were added per ladder so the engine picks the right one
by request type/keyword. Seeding is idempotent (by name)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.workflows.models import Workflow


# (kind, name, screenKey, approverRole, slaHours, agentKey, minConfidence, skip_if)
def _h(name, screen, role, sla=None, skip=None):
    return ("HUMAN", name, screen, role, sla, None, None, skip)


def _a(name, role, agent, minconf, sla=None):
    return ("AGENT", name, "agent_review", role, sla, agent, minconf, None)


# Ported verbatim from library.ts (key, name, description, criteria, eval_order, steps)
_LIBRARY = [
    ("nda_fasttrack", "NDA Fast-Track",
     "Mutual/one-way NDAs. The NDA agent reviews against the standard template; only deviations need lawyer attention.",
     {"match_type": "nda"}, 10, [
         _h("Request & Upload", "nda_intake", "requester"),
         _a("AI Template Review", "attorney", "nda-agent", 0.75, 4),
         _h("Legal Sign-off", "legal_review", "attorney", 24),
         _h("E-Signature", "signature_screen", "gc", 48),
     ]),
    ("data_breach", "Data Privacy Incident (DPDP)",
     "Personal-data breach response. The 72-hour notification clock makes the first stages the tightest SLAs in the library.",
     {"match_type": "dpa"}, 15, [
         _h("Incident Logging", "breach_intake", "legal_ops", 4),
         _a("AI Severity & Notification Assessment", "legal_ops", "privacy-assessment-agent", 0.85, 4),
         _h("Containment & Legal Position", "legal_review", "attorney", 24),
         _h("Regulator / Data-Principal Notification", "notification_dispatch", "gc", 36),
     ]),
    ("vendor_onboarding", "Vendor / Counterparty Due Diligence",
     "Third-party onboarding: the Vendor agent runs sanctions/debarment screening; compliance clears exceptions.",
     {"match_keyword": "vendor"}, 20, [
         _h("Vendor Details & Documents", "vendor_intake", "requester"),
         _a("AI Sanctions & Debarment Screening", "legal_ops", "vendor-intake-agent", 0.85, 8),
         _h("Compliance Clearance", "compliance_review", "legal_ops", 72),
         _h("Contract Terms Approval", "legal_review", "attorney", 72),
     ]),
    ("patent_litigation", "Patent / ANDA Litigation (Para IV)",
     "Hatch-Waxman: the 45-day statutory window to sue after a Para IV notice makes early stages hard-SLA'd. Settlements require antitrust review before GC sign-off.",
     {"match_keyword": "litigation"}, 25, [
         _h("Matter Intake & Docketing", "litigation_intake", "paralegal", 24),
         _a("AI Case Summary & Deadline Extraction", "attorney", "litigation-agent", 0.7, 8),
         _h("IP Counsel Assessment", "ip_assessment", "attorney", 120),
         _h("Outside Counsel Engagement", "counsel_engagement", "attorney", 168,
            {"field": "handled_inhouse", "op": "eq", "value": True}),
         _h("Strategy & Budget Approval", "gc_approval", "gc", 120),
         _h("Settlement Antitrust Review", "antitrust_review", "attorney", 168,
            {"field": "settlement_proposed", "op": "eq", "value": False}),
         _h("GC / Board Sign-off", "board_signoff", "gc", 120),
     ]),
    ("legal_notice", "Legal Notice Response",
     "Statutory / demand notices with hard reply deadlines. The Notice agent extracts every deadline with its source cited; counsel finalizes.",
     {"match_keyword": "notice"}, 30, [
         _h("Notice Logging", "notice_intake", "legal_ops", 8),
         _a("AI Deadline & Claim Extraction", "attorney", "notice-mgmt-agent", 0.75, 4),
         _h("Response Drafting", "response_draft", "attorney", 72),
         _h("GC Approval & Dispatch", "gc_approval", "gc", 48),
     ]),
    ("regulatory_response", "Regulatory Action Response",
     "USFDA 483 / warning letters, NPPA-DPCO pricing notices, state drug-controller actions. Cross-functional with Quality/Regulatory Affairs.",
     {"match_keyword": "regulatory"}, 35, [
         _h("Action Logging & Classification", "regulatory_intake", "legal_ops", 8),
         _h("Cross-functional Assessment", "cfa_review", "legal_ops", 72),
         _h("Legal Position & Draft Response", "legal_review", "attorney", 120),
         _h("GC Approval", "gc_approval", "gc", 48),
         _h("Board / Disclosure Review", "board_signoff", "legal_ops", 48,
            {"field": "material", "op": "eq", "value": False}),
     ]),
    ("compliance_investigation", "Compliance Investigation",
     "Whistleblower / UCPMP / anti-bribery matters. Confidential track with mandatory closure report.",
     {"match_keyword": "investigation"}, 40, [
         _h("Complaint Triage", "investigation_intake", "legal_ops", 48),
         _h("Investigation Plan Approval", "investigation_plan", "gc", 72),
         _h("Fact-finding & Interviews", "investigation_work", "attorney", 336),
         _h("Findings & Recommendation", "findings_review", "legal_ops", 120),
         _h("GC / Audit Committee Closure", "board_signoff", "gc", 120),
     ]),
    ("employment_matter", "Employment / POSH Matter",
     "Disciplinary, separation and POSH-committee matters with statutory timelines.",
     {"match_keyword": "employment"}, 45, [
         _h("Matter Intake", "hr_intake", "requester", 48),
         _h("Legal Assessment", "legal_review", "attorney", 96),
         _h("Committee / HR Head Decision", "committee_review", "legal_ops", 168),
         _h("GC Sign-off", "gc_approval", "gc", 72),
     ]),
    ("board_approval", "Board / Secretarial Approval",
     "POAs, authorised-signatory changes, disclosures and resolutions.",
     {"match_keyword": "board"}, 50, [
         _h("Request & Draft Resolution", "secretarial_intake", "legal_ops", 72),
         _h("Legal Vetting", "legal_review", "attorney", 72),
         _h("CS / Board Approval", "board_signoff", "gc", 168),
     ]),
    ("clm_contract_approval", "Contract Approval Ladder",
     "Commercial contracts: supply, distribution, licensing, services. Default for any contract.",
     {}, 100, [
         _h("Draft & Submit", "contract_draft", "requester"),
         _a("AI Risk Review", "attorney", "contract-review-agent", 0.8, 8),
         _h("Legal Review", "legal_review", "attorney", 48),
         _h("Finance Review", "finance_review", "legal_ops", 48,
            {"field": "contract_value", "op": "lt", "value": 10000}),
         _h("GC Approval", "gc_approval", "gc", 72),
         _h("Counter-signature", "signature_screen", "gc", 72),
     ]),
]

# gc/board human rungs become real "approval" steps (delegate to the DoA engine).
_APPROVAL_SCREENS = {"gc_approval", "board_signoff"}
# The contract-producing intake steps become a real clm_draft so the agent
# actually drafts the document and the rest of the ladder acts on that contract
# (rather than a human "upload" that leaves the approval/signature steps no-ops).
_DRAFT_SCREENS = {"contract_draft", "nda_intake", "vendor_intake"}


def _to_steps(raw):
    out = []
    for (kind, name, screen, role, sla, agent, minconf, skip) in raw:
        if screen == "signature_screen":
            t, cfg = "signature", {"approver_role": role, "sla_hours": sla}
        elif screen in _DRAFT_SCREENS:
            t, cfg = "clm_draft", {"mode": "template"}
        elif kind == "AGENT":
            t, cfg = "ai_task", {"agent": agent, "sla_hours": sla,
                                 "escalate_role": role, "escalate_below_confidence": minconf}
        elif screen in _APPROVAL_SCREENS:
            t, cfg = "approval", {"approver_role": role, "sla_hours": sla}
        else:
            t, cfg = "human_task", {"approver_role": role, "sla_hours": sla}
        if skip:
            cfg["skip_if"] = skip
        cfg = {k: v for k, v in cfg.items() if v is not None}
        out.append({"id": str(uuid.uuid4()), "type": t, "name": name, "config": cfg})
    return out


BUILTIN_FLOWS = [
    {"name": name, "description": desc, "eval_order": order, "criteria": crit, "steps": _to_steps(raw)}
    for (_key, name, desc, crit, order, raw) in _LIBRARY
]


# Master Services Agreement — the full lifecycle from the workflow-designer
# reference (screen-06): a draft, sequential legal review, a conditional parallel
# Quality + Privacy review, a conditional finance approval, counterparty
# negotiation, signature, and automated activation. Uses the richer step schema
# (outcomes / instructions / parallel / cond) directly rather than the _h/_a
# tuple helpers, which predate those fields.
def _msa(type_, name, dept, assign_by, sla, outcomes, instructions, parallel=False, cond=None):
    cfg = {"sla_hours": sla, "outcomes": outcomes, "instructions": instructions}
    if dept:
        cfg["dept"] = dept
        cfg["approver_role"] = dept  # engine resolves the dept name to a team pool
    if assign_by:
        cfg["assign_by"] = assign_by
    step = {"id": str(uuid.uuid4()), "type": type_, "name": name,
            "config": {k: v for k, v in cfg.items() if v is not None}}
    if parallel:
        step["parallel"] = True
    if cond:
        step["cond"] = cond
    return step


_LOADED = "Auto — least-loaded in team"
_HEAD = "Auto — team head"
_SPECIFIC = "Specific person"
_MSA_STEPS = [
    _msa("clm_draft", "Prepare draft", "Legal & IP", _LOADED, 24, ["complete"], "Draft from the MSA template."),
    _msa("human_task", "Legal review", "Legal & IP", _LOADED, 48, ["approve", "request_changes", "need_info", "escalate"], "Review scope, liability and IP against the playbook."),
    _msa("human_task", "Quality review", "Quality & Compliance", _HEAD, 48, ["approve", "request_changes"], "Confirm GxP obligations.", cond={"field": "gxp", "op": "eq", "value": "true"}),
    _msa("human_task", "Privacy review", "Privacy / DPO", _HEAD, 48, ["approve", "request_changes"], "Check data-processing terms.", parallel=True, cond={"field": "personal_data", "op": "eq", "value": "true"}),
    _msa("approval", "Finance approval", "Finance & Tax", _HEAD, 24, ["approve", "reject", "escalate"], "Approve the contract value.", cond={"field": "high_value", "op": "eq", "value": "true"}),
    _msa("counterparty", "Counterparty negotiation", "Counterparty", _SPECIFIC, 120, ["approve", "request_changes"], "Exchange redlines with the counterparty until both sides agree."),
    _msa("signature", "Internal signature", "Signatory", _SPECIFIC, 24, ["sign", "decline"], "Sign the executed agreement."),
    _msa("ai_task", "Activate & track", "System (automated)", _LOADED, 0, [], "File the contract and start obligation tracking."),
]

BUILTIN_FLOWS.append({
    "name": "Master Services Agreement",
    "description": "Full MSA lifecycle — draft, legal review, a conditional parallel Quality + Privacy review, Finance approval, counterparty negotiation, signature, and automated activation.",
    "eval_order": 12,
    "criteria": {"match_type": "msa"},
    "steps": _MSA_STEPS,
})


def seed_builtin_flows(db: Session, *, org_id: str, actor_id: str | None = None) -> int:
    """Idempotent: create any builtin flow (by name) that this org is missing."""
    have = set(db.scalars(select(Workflow.name).where(Workflow.org_id == org_id, Workflow.is_builtin.is_(True))).all())
    added = 0
    for spec in BUILTIN_FLOWS:
        if spec["name"] in have:
            continue
        db.add(Workflow(
            id=str(uuid.uuid4()), org_id=org_id, created_by_user_id=actor_id,
            name=spec["name"], description=spec["description"], enabled=True, is_builtin=True,
            eval_order=spec["eval_order"], version=1, criteria=spec["criteria"], steps=spec["steps"],
        ))
        added += 1
    if added:
        db.commit()
    return added
