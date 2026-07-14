"""Idempotent intake demo seed — request types, KB, and a handful of sample
requests across statuses so the front door has something to show on first run.
Match-by-key/ref so re-running is a no-op. Local/dev only."""

from __future__ import annotations

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
            {"key": "counterparty", "label": "Counterparty", "kind": "text", "required": True, "sort_order": 10},
            {"key": "mutual", "label": "Mutual?", "kind": "boolean", "required": False, "sort_order": 20},
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

_REQUESTS = [
    {"ref_key": "seed-nda", "type": "nda", "type_label": "NDA Request", "priority": "Medium",
     "description": "Need a mutual NDA with Northwind Traders GmbH before a product eval.",
     "fields": {"counterparty": "Northwind Traders GmbH", "mutual": True},
     "requester": "user1@example.com"},
    {"ref_key": "seed-msa", "type": "contract_review", "type_label": "Contract Review", "priority": "High",
     "description": ("Review the Globex MSA — several non-standard clauses flagged by procurement, "
                     "including uncapped liability, unilateral termination and broad IP assignment. "
                     "This is complex non-standard paper and needs a careful redline."),
     "fields": {"counterparty": "Globex Corporation", "value": 250000},
     "requester": "user1@example.com"},
    {"ref_key": "seed-dpa", "type": "privacy", "type_label": "Data Privacy Assessment", "priority": "High",
     "description": "DPIA for a new marketing analytics vendor processing EU customer data.",
     "fields": {"system": "Marketing analytics — Fabrikam"},
     "requester": "user2@example.com"},
    {"ref_key": "seed-litig", "type": None, "type_label": "Litigation hold", "priority": "Critical",
     "description": "Litigation hold — Acme dispute. Preserve all documents; deadline approaching.",
     "fields": {}, "requester": "user2@example.com", "overdue": True},
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
            priority=spec["priority"], status="awaiting_triage", stage="new",
            sla_hours=2 if spec.get("overdue") else 24, submitted_at=submitted,
            handoff_holder="queue",
            stage_timestamps=[{"stage": "new", "at": submitted.isoformat()}],
            created_by_user_id=requester.id, updated_by_user_id=requester.id,
        )
        r.ai_triage = agents.classify(r.type_label, r.description)
        db.add(r)
        db.flush()
        routing_mod.apply_routing(db, r)
        svc.run_triage(db, r, counterparty=spec["fields"].get("counterparty"))
        if spec.get("overdue"):
            r.sla_status = "overdue"
        created = True

    return created
