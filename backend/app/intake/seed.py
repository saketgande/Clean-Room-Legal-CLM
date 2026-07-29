"""Idempotent intake demo seed — request types, KB, and a handful of sample
requests across statuses so the front door has something to show on first run.
Match-by-key/ref so re-running is a no-op. Local/dev only."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.auth.models import User
from app.core.database import utcnow
from app.intake.models import (
    IntakeKbArticle,
    IntakeRequest,
    IntakeRequestField,
    IntakeRequestType,
)

_TYPES = [
    {
        "key": "nda", "name": "NDA", "workstream": "Commercial", "sort_order": 10,
        "stages": ["draft", "review"],
        "fields": [
            {"key": "draft_path", "label": "How should we produce the NDA?", "kind": "select", "required": True, "sort_order": 5,
             "options": [
                 {"value": "fast_lane", "label": "Fast-lane — use our standard NDA template (recommended)"},
                 {"value": "custom", "label": "Draft custom — an attorney drafts it fresh, no template"},
                 {"value": "attach", "label": "Attach existing — I'll upload our NDA to review"},
             ]},
            {"key": "nda_direction", "label": "Direction", "kind": "select", "required": True, "sort_order": 10,
             "options": [
                 {"value": "mutual", "label": "Mutual — both sides disclose"},
                 {"value": "oneway_disclose", "label": "One-way — we disclose"},
                 {"value": "oneway_receive", "label": "One-way — we receive"},
             ]},
            {"key": "counterparty", "label": "Counterparty (legal name)", "kind": "text", "required": True, "sort_order": 20},
            {"key": "counterparty_jurisdiction", "label": "Counterparty jurisdiction / state", "kind": "text", "required": False, "sort_order": 30},
            {"key": "purpose", "label": "Purpose of disclosure", "kind": "textarea", "required": False, "sort_order": 40},
            {"key": "term", "label": "Term / duration (e.g. 2 weeks, 1 year)", "kind": "text", "required": False, "sort_order": 50},
            {"key": "survival_years", "label": "Confidentiality survives (years)", "kind": "number", "required": False, "sort_order": 60},
            {"key": "governing_law", "label": "Governing law (e.g. Delaware)", "kind": "text", "required": False, "sort_order": 70},
            {"key": "effective_date", "label": "Effective date", "kind": "date", "required": False, "sort_order": 80},
        ],
    },
    {
        "key": "contract_review", "name": "Contract Review", "workstream": "Commercial", "sort_order": 20,
        "stages": ["review", "redline", "negotiation"],
        "fields": [
            {"key": "counterparty", "label": "Counterparty", "kind": "text", "required": True, "sort_order": 10},
            {"key": "value", "label": "Est. value (USD)", "kind": "number", "required": False, "sort_order": 20},
        ],
    },
    {
        "key": "privacy", "name": "Data Privacy (DPIA)", "workstream": "Privacy", "sort_order": 30,
        "stages": ["assessment", "sign_off"],
        "fields": [
            {"key": "system", "label": "System / process", "kind": "text", "required": True, "sort_order": 10},
        ],
    },
    {
        "key": "trademark", "name": "Trademark", "workstream": "IP", "sort_order": 40,
        "stages": ["search", "opinion"],
        "fields": [
            {"key": "mark", "label": "Proposed mark", "kind": "text", "required": True, "sort_order": 10},
        ],
    },
]

_KB = [
    {"source_ref": "NDA-SELF-SERVE", "title": "Generate a standard NDA yourself",
     "body": "For standard mutual NDAs on our playbook terms (2-year, mutual, DE law), use the self-service generator — no legal review needed.",
     "tags": ["nda", "self-service", "standard"]},
    {"source_ref": "POLICY-GIFTS", "title": "Gifts & entertainment limits",
     "body": "Gifts under $100 need no approval; $100-$500 require manager sign-off; over $500 require legal review.",
     "tags": ["policy", "gifts", "compliance"]},
    {"source_ref": "CONTRACT-REVIEW-WHEN", "title": "When do I need legal review?",
     "body": "Non-standard terms, value over $50k, or any uncapped liability trigger legal review. Standard paper under $50k uses the fast lane.",
     "tags": ["contracts", "review", "threshold"]},
]

# One request per scenario the agents + flow library cover — so the inbox
# demonstrates the full range (NDA, contract review, vendor DD, privacy/breach,
# patent litigation, legal notice, regulatory, investigation, employment,
# trademark, self-serve) with a spread of priorities and SLA postures.
_REQUESTS = [
    # ── Commercial / contracts ──────────────────────────────────────────
    {"ref_key": "seed-nda", "type": "nda", "type_label": "NDA Request", "priority": "Medium",
     "description": "Need a mutual NDA with Northwind Traders GmbH before a product evaluation — 2-year term, Delaware law.",
     "fields": {"draft_path": "fast_lane", "nda_direction": "mutual", "counterparty": "Northwind Traders GmbH",
                "governing_law": "Delaware", "term": "2 years"},
     "requester": "user1@example.com"},
    {"ref_key": "seed-msa", "type": "contract_review", "type_label": "Contract Review", "priority": "High",
     "description": ("Review the Globex MSA — several non-standard clauses flagged by procurement, including "
                     "uncapped liability, unilateral termination and broad IP assignment. Complex non-standard "
                     "paper that needs a careful redline."),
     "fields": {"counterparty": "Globex Corporation", "value": 250000},
     "requester": "user1@example.com"},
    {"ref_key": "seed-vendor", "type": None, "type_label": "Vendor Due Diligence", "priority": "Medium",
     "description": ("Onboarding a new supplier, Fabrikam Ltd, for cloud infrastructure. Run sanctions and "
                     "debarment screening and clear any exceptions before we sign the vendor MSA."),
     "fields": {"counterparty": "Fabrikam Ltd"}, "requester": "user2@example.com"},

    # ── Privacy ─────────────────────────────────────────────────────────
    {"ref_key": "seed-breach", "type": None, "type_label": "Data Privacy Incident (DPA)", "priority": "Critical",
     "description": ("Suspected personal-data breach — our marketing analytics vendor exposed roughly 40,000 EU "
                     "customer records. The 72-hour GDPR breach-notification clock is running; we need containment "
                     "and a regulator-notification decision now."),
     "fields": {"counterparty": "Contoso Analytics"}, "requester": "user2@example.com", "overdue": True},
    {"ref_key": "seed-dpia", "type": "privacy", "type_label": "Data Privacy Assessment", "priority": "Medium",
     "description": "DPIA for a new marketing-analytics vendor that will process EU customer personal data.",
     "fields": {"system": "Marketing analytics — Fabrikam"}, "requester": "user2@example.com"},

    # ── Litigation & disputes ───────────────────────────────────────────
    {"ref_key": "seed-patent", "type": None, "type_label": "Litigation — Patent (Para IV)", "priority": "Critical",
     "description": ("Received a Paragraph IV notice on our generic-drug ANDA filing. There is a 45-day statutory "
                     "window to sue the patent holder. Need matter docketing, deadline extraction and IP-counsel "
                     "assessment; a legal hold is likely required."),
     "fields": {"counterparty": "Initech Pharma"}, "requester": "user1@example.com"},
    {"ref_key": "seed-litig", "type": None, "type_label": "Litigation hold", "priority": "Critical",
     "description": "Litigation hold — Acme dispute. Preserve all documents; deadline approaching.",
     "fields": {"counterparty": "Acme Corp"}, "requester": "user2@example.com", "overdue": True},
    {"ref_key": "seed-notice", "type": None, "type_label": "Legal Notice", "priority": "High",
     "description": ("Cease-and-desist letter from Initech alleging trademark infringement of our new logo. It sets "
                     "a statutory reply deadline 10 days out — extract the deadline and draft a response."),
     "fields": {"counterparty": "Initech"}, "requester": "user1@example.com"},

    # ── Regulatory / compliance / employment ────────────────────────────
    {"ref_key": "seed-reg", "type": None, "type_label": "Regulatory Action", "priority": "High",
     "description": ("USFDA issued Form 483 observations after inspecting our Baddi manufacturing facility. We need "
                     "a coordinated, cross-functional response with Quality/Regulatory within 15 business days."),
     "fields": {}, "requester": "user2@example.com"},
    {"ref_key": "seed-invest", "type": None, "type_label": "Compliance Investigation", "priority": "High",
     "description": ("Whistleblower complaint alleging kickbacks in procurement. Needs a confidential investigation "
                     "with an investigation plan, fact-finding and a mandatory closure report."),
     "fields": {}, "requester": "user2@example.com"},
    {"ref_key": "seed-empl", "type": None, "type_label": "Employment / POSH Matter", "priority": "High",
     "description": ("POSH committee matter — a harassment complaint has been raised against a senior manager. "
                     "Statutory timelines apply and the matter is confidential."),
     "fields": {}, "requester": "user1@example.com"},

    # ── IP + self-serve ─────────────────────────────────────────────────
    {"ref_key": "seed-tm", "type": "trademark", "type_label": "Trademark", "priority": "Medium",
     "description": "Trademark clearance for the new brand name 'Aegis Shield' ahead of the Q3 product launch.",
     "fields": {"mark": "Aegis Shield"}, "requester": "user1@example.com"},
    {"ref_key": "seed-faq", "type": None, "type_label": "Legal Question — General", "priority": "Low",
     "description": "Quick question — can we share our standard MSA with a prospect under our existing mutual NDA?",
     "fields": {}, "requester": "user1@example.com"},
]

_TEAMS = [
    {"key": "tier1", "name": "Tier 1 · Paralegals", "strategy": "least_loaded",
     "members": [("user1@example.com", 8), ("user2@example.com", 8)]},
    {"key": "tier2", "name": "Tier 2 · Counsel", "strategy": "round_robin", "overflow": "tier1",
     "members": [("legal1@example.com", 6), ("legal2@example.com", 6)]},
]

_RULES = [
    {"name": "Standard NDA fast-lane", "eval_order": 10,
     "match_type": "NDA Request", "match_complexity": "simple", "set_team": "tier1", "set_sla_hours": 2},
    {"name": "Complex review → counsel", "eval_order": 20,
     "match_complexity": "complex", "set_team": "tier2"},
    {"name": "Critical → escalate to GC", "eval_order": 30,
     "match_priority": "Critical", "escalate_to": "legal1@example.com"},
]


def seed_intake(db, org, admin) -> bool:
    """Returns True if anything new was created."""
    created = False

    # request types + fields
    type_by_key: dict[str, IntakeRequestType] = {
        t.key: t for t in db.scalars(
            select(IntakeRequestType).where(IntakeRequestType.org_id == org.id)
        ).all()
    }
    for spec in _TYPES:
        if spec["key"] in type_by_key:
            continue
        t = IntakeRequestType(
            org_id=org.id, key=spec["key"], name=spec["name"], workstream=spec["workstream"],
            stages=spec["stages"], sort_order=spec["sort_order"], active=True,
            created_by_user_id=admin.id, updated_by_user_id=admin.id,
        )
        t.fields = [
            IntakeRequestField(org_id=org.id, key=f["key"], label=f["label"], kind=f["kind"],
                               required=f["required"], sort_order=f["sort_order"])
            for f in spec["fields"]
        ]
        db.add(t)
        db.flush()
        type_by_key[spec["key"]] = t
        created = True

    # KB
    kb_refs = {
        a.source_ref for a in db.scalars(
            select(IntakeKbArticle).where(IntakeKbArticle.org_id == org.id)
        ).all()
    }
    for a in _KB:
        if a["source_ref"] in kb_refs:
            continue
        db.add(IntakeKbArticle(
            org_id=org.id, source_ref=a["source_ref"], title=a["title"], body=a["body"],
            tags=a["tags"], active=True, created_by_user_id=admin.id, updated_by_user_id=admin.id,
        ))
        created = True

    users = {u.email: u for u in db.scalars(select(User).where(User.org_id == org.id)).all()}

    # teams / pools
    from app.intake.models import IntakeRoutingRule, IntakeTeam, IntakeTeamMember
    team_by_key = {t.key: t for t in db.scalars(select(IntakeTeam).where(IntakeTeam.org_id == org.id)).all()}
    for spec in _TEAMS:
        if spec["key"] in team_by_key:
            continue
        t = IntakeTeam(org_id=org.id, key=spec["key"], name=spec["name"], strategy=spec["strategy"],
                       created_by_user_id=admin.id, updated_by_user_id=admin.id)
        t.members = [IntakeTeamMember(org_id=org.id, user_id=users[e].id, capacity=cap)
                     for e, cap in spec["members"] if e in users]
        db.add(t)
        db.flush()
        team_by_key[spec["key"]] = t
        created = True
    # wire overflow now that both tiers exist
    for spec in _TEAMS:
        if spec.get("overflow") and spec["key"] in team_by_key:
            t = team_by_key[spec["key"]]
            if t.overflow_team_id is None:
                t.overflow_team_id = team_by_key[spec["overflow"]].id

    # routing rules
    rule_names = {r.name for r in db.scalars(select(IntakeRoutingRule).where(IntakeRoutingRule.org_id == org.id)).all()}
    for spec in _RULES:
        if spec["name"] in rule_names:
            continue
        r = IntakeRoutingRule(
            org_id=org.id, name=spec["name"], eval_order=spec["eval_order"],
            match_type=spec.get("match_type"), match_priority=spec.get("match_priority"),
            match_complexity=spec.get("match_complexity"),
            set_team_id=team_by_key[spec["set_team"]].id if spec.get("set_team") else None,
            set_sla_hours=spec.get("set_sla_hours"),
            escalate_to_user_id=users[spec["escalate_to"]].id if spec.get("escalate_to") in users else None,
            created_by_user_id=admin.id, updated_by_user_id=admin.id,
        )
        db.add(r)
        created = True
    db.flush()

    # sample requests — run the real pipeline so they get classification, routing + a recommendation
    from datetime import timedelta

    from app.intake import agents, routing as routing_mod, service as svc
    from sqlalchemy import func as _func

    existing_markers = {
        (r.field_values or {}).get("_seed") for r in db.scalars(
            select(IntakeRequest).where(IntakeRequest.org_id == org.id)
        ).all()
    }
    now = utcnow()
    for spec in _REQUESTS:
        if spec["ref_key"] in existing_markers:
            continue
        requester = users.get(spec["requester"]) or admin
        rtype = type_by_key.get(spec["type"]) if spec.get("type") else None
        submitted = now - timedelta(hours=3) if spec.get("overdue") else now
        n = db.execute(select(_func.nextval("intake_ref_seq"))).scalar_one()
        r = IntakeRequest(
            org_id=org.id, ref=f"REQ-{n}", source="seed",
            requester_user_id=requester.id, department="Sales",
            request_type_id=(rtype.id if rtype else None), type_label=spec["type_label"],
            description=spec["description"],
            field_values={**spec["fields"], "_seed": spec["ref_key"]},
            priority=spec["priority"], status="open", stage="new",
            sla_hours=2 if spec.get("overdue") else 24, submitted_at=submitted,
            handoff_holder="queue",
            stage_timestamps=[{"stage": "new", "at": submitted.isoformat()}],
            created_by_user_id=requester.id, updated_by_user_id=requester.id,
        )
        r.ai_triage = agents.classify(r.type_label, r.description)
        db.add(r)
        db.flush()
        routing_mod.apply_routing(db, r)
        # Let the Flow Router / Litigation agent suggest a workflow so the seeded
        # inbox demonstrates them. Best-effort — a model hiccup never fails seed.
        try:
            svc._compute_intake_analysis(db, r)
        except Exception:
            logging.getLogger(__name__).debug(
                "seed: intake analysis failed for %s", r.id, exc_info=True
            )
        if spec.get("overdue"):
            r.sla_status = "overdue"
        created = True

    return created
