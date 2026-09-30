"""Idempotent intake demo seed — teams and a handful of sample
requests across statuses so the front door has something to show on first run.
Match-by-key/ref so re-running is a no-op. Local/dev only."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.auth.models import User
from app.core.database import utcnow
from app.intake.models import IntakeRequest

# One request per scenario the agents + flow library cover — so the inbox
# demonstrates the full range (NDA, contract review, vendor DD, privacy/breach,
# patent litigation, legal notice, regulatory, investigation, employment,
# trademark) with a spread of priorities and SLA postures.
_REQUESTS = [
    # ── Commercial / contracts ──────────────────────────────────────────
    {"ref_key": "seed-nda", "type": "nda", "type_label": "NDA Request", "priority": "Medium",
     "description": "Need a mutual NDA with Northwind Traders GmbH before a product evaluation — 2-year term, Delaware law.",
     "fields": {"draft_path": "fast_lane", "nda_kind": "Mutual", "counterparty": "Northwind Traders GmbH",
                "governing_law": "Delaware", "nda_term": "2 years"},
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

# Demo members for the default teams (intake/teams.DEFAULT_TEAMS).
_TEAMS = [
    {"key": "paralegals", "strategy": "least_loaded",
     "expertise": ["NDA", "Vendor", "Policy/FAQ"],
     "members": [("user1@example.com", 8), ("user2@example.com", 8)]},
    {"key": "legal_counsel", "strategy": "round_robin", "overflow": "paralegals",
     "expertise": ["Litigation", "Privacy", "Trademark", "Contract Review", "General"],
     "members": [("legal1@example.com", 6), ("legal2@example.com", 6)]},
    {"key": "executive", "members": [("admin1@example.com", 0), ("admin2@example.com", 0)]},
    {"key": "finance", "members": [("approver1@example.com", 0)]},
    {"key": "compliance", "members": [("approver2@example.com", 0)]},
]

def seed_intake(db, org, admin) -> bool:
    """Returns True if anything new was created."""
    created = False

    users = {u.email: u for u in db.scalars(select(User).where(User.org_id == org.id)).all()}

    # teams: fill the default teams with demo members (only a team still empty)
    from app.intake.models import IntakeTeam, IntakeTeamMember
    from app.intake.teams import ensure_default_teams
    ensure_default_teams(db, org_id=org.id, actor_user_id=admin.id)
    team_by_key = {t.key: t for t in db.scalars(select(IntakeTeam).where(IntakeTeam.org_id == org.id)).all()}
    for spec in _TEAMS:
        t = team_by_key.get(spec["key"])
        if t is None or t.members:
            continue
        t.strategy = spec.get("strategy", t.strategy)
        t.expertise = spec.get("expertise") or t.expertise
        t.members = [IntakeTeamMember(org_id=org.id, user_id=users[e].id, capacity=cap)
                     for e, cap in spec["members"] if e in users]
        created = True
    # wire overflow now that the teams exist
    for spec in _TEAMS:
        if spec.get("overflow") and spec["key"] in team_by_key:
            t = team_by_key[spec["key"]]
            if t.overflow_team_id is None:
                t.overflow_team_id = team_by_key[spec["overflow"]].id

    db.flush()

    # sample requests — run the real pipeline so they get classification + a recommendation
    from datetime import timedelta

    from sqlalchemy import func as _func

    from app.intake import agents
    from app.intake import service as svc

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
        submitted = now - timedelta(hours=3) if spec.get("overdue") else now
        n = db.execute(select(_func.nextval("intake_ref_seq"))).scalar_one()
        r = IntakeRequest(
            org_id=org.id, ref=f"REQ-{n}", source="seed",
            requester_user_id=requester.id, department="Sales",
            type_label=spec["type_label"],
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
