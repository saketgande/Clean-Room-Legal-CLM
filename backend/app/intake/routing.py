"""No-code when->then routing. Rules evaluate in eval_order inside the save
chokepoint; conditions AND, actions are cumulative (later rules see earlier
effects); a rule never re-fires once an attorney has acted. Every newly-fired
rule is chain-sealed and its counter bumped (no-op matches don't count)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.intake import teams as teams_mod
from app.intake.models import IntakeRequest, IntakeRoutingRule, IntakeTeam

_CONDITIONS = ["match_type", "match_priority", "match_department", "match_keyword", "match_complexity"]
_ACTIONS = ["set_assignee_user_id", "set_priority", "set_sla_hours", "set_team_id",
            "escalate_to_user_id", "require_approval_from_user_id"]


@dataclass
class _WS:
    type_label: str
    priority: str
    department: str | None
    description: str
    sla_hours: int
    assignee: str | None
    complexity: str
    gate: str | None
    escalated: bool = False


def _matches(r: IntakeRoutingRule, w: _WS) -> bool:
    if r.match_type and r.match_type.lower() != (w.type_label or "").lower():
        return False
    if r.match_priority and r.match_priority != w.priority:
        return False
    if r.match_department and r.match_department.lower() != (w.department or "").lower():
        return False
    if r.match_keyword and r.match_keyword.lower() not in (w.description or "").lower():
        return False
    if r.match_complexity and r.match_complexity != w.complexity:
        return False
    return True


def apply_routing(db: Session, request: IntakeRequest) -> None:
    # Never override a human decision.
    if request.triaged_by_user_id or request.triage_action:
        return
    rules = db.scalars(
        select(IntakeRoutingRule)
        .where(IntakeRoutingRule.org_id == request.org_id, IntakeRoutingRule.enabled.is_(True))
        .order_by(IntakeRoutingRule.eval_order, IntakeRoutingRule.id)
    ).all()
    if not rules:
        return
    complexity = (request.ai_triage or {}).get("complexity") or "standard"
    w = _WS(
        type_label=request.type_label, priority=request.priority, department=request.department,
        description=request.description or "", sla_hours=request.sla_hours,
        assignee=request.assigned_to_user_id, complexity=complexity,
        gate=request.approval_gate_user_id,
    )
    fired: list[tuple[IntakeRoutingRule, list[str]]] = []
    for r in rules:
        if not _matches(r, w):
            continue
        actions: list[str] = []
        if r.set_priority and r.set_priority != w.priority:
            w.priority = r.set_priority; actions.append(f"priority → {r.set_priority}")
        if r.set_sla_hours and r.set_sla_hours != w.sla_hours:
            w.sla_hours = r.set_sla_hours; actions.append(f"SLA → {r.set_sla_hours}h")
        if r.set_assignee_user_id and r.set_assignee_user_id != w.assignee:
            w.assignee = r.set_assignee_user_id
            actions.append(f"assignee → {teams_mod._label(db, r.set_assignee_user_id)}")
        elif r.set_team_id:
            pick = teams_mod.pick_from_pool(db, team_id=r.set_team_id)
            if pick and pick.user_id != w.assignee:
                w.assignee = pick.user_id
                actions.append(f"pool {pick.team_name} → {pick.user_name}" + (" (overflow)" if pick.overflow else ""))
        if r.escalate_to_user_id:
            changed = False
            if not (r.set_assignee_user_id or r.set_team_id) and w.assignee != r.escalate_to_user_id:
                w.assignee = r.escalate_to_user_id; changed = True
            if not r.set_priority and w.priority != "Critical":
                w.priority = "Critical"; changed = True
            if changed:
                w.escalated = True
                actions.append(f"escalate → {teams_mod._label(db, r.escalate_to_user_id)}")
        if r.require_approval_from_user_id and r.require_approval_from_user_id != w.gate:
            w.gate = r.require_approval_from_user_id
            actions.append(f"approval gate → {teams_mod._label(db, r.require_approval_from_user_id)}")
        if actions:
            fired.append((r, actions))

    # apply working state back
    request.priority = w.priority
    request.sla_hours = w.sla_hours
    request.approval_gate_user_id = w.gate
    if w.assignee and w.assignee != request.assigned_to_user_id:
        request.assigned_to_user_id = w.assignee
        request.handoff_holder = "human"
        request.handoff_user_id = w.assignee
    if w.escalated:
        request.status = "escalated"

    prior = set((request.fired_rules or {}).get("rule_ids", []))
    request.fired_rules = {
        "rule_ids": [r.id for r, _ in fired],
        "fired_at": utcnow().isoformat(),
        "summaries": [{"id": r.id, "name": r.name, "actions": a} for r, a in fired],
    }
    for r, actions in fired:
        if r.id not in prior:
            write_audit_log(db, action="intake.routing_rule.fired", resource_type="intake_request",
                            resource_id=request.id, org_id=request.org_id, actor_user_id=None,
                            after={"rule": r.name, "actions": actions})
            r.times_fired += 1
            r.last_fired_at = utcnow()


# --- rule CRUD -------------------------------------------------------------

def serialize_rule(db: Session, r: IntakeRoutingRule) -> dict:
    team = db.get(IntakeTeam, r.set_team_id) if r.set_team_id else None
    return {
        "id": r.id, "name": r.name, "description": r.description, "enabled": r.enabled,
        "eval_order": r.eval_order,
        "match_type": r.match_type, "match_priority": r.match_priority,
        "match_department": r.match_department, "match_keyword": r.match_keyword,
        "match_complexity": r.match_complexity,
        "set_assignee_user_id": r.set_assignee_user_id,
        "set_assignee_name": teams_mod._label(db, r.set_assignee_user_id),
        "set_priority": r.set_priority, "set_sla_hours": r.set_sla_hours,
        "set_team_id": r.set_team_id, "set_team_name": team.name if team else None,
        "escalate_to_user_id": r.escalate_to_user_id,
        "escalate_to_name": teams_mod._label(db, r.escalate_to_user_id),
        "require_approval_from_user_id": r.require_approval_from_user_id,
        "require_approval_from_name": teams_mod._label(db, r.require_approval_from_user_id),
        "times_fired": r.times_fired,
        "last_fired_at": r.last_fired_at.isoformat() if r.last_fired_at else None,
    }


def list_rules(db: Session, *, org_id: str) -> list[dict]:
    rows = db.scalars(
        select(IntakeRoutingRule).where(IntakeRoutingRule.org_id == org_id)
        .order_by(IntakeRoutingRule.eval_order, IntakeRoutingRule.id)
    ).all()
    return [serialize_rule(db, r) for r in rows]


def _assert_semantics(payload) -> None:
    has_cond = any(getattr(payload, c, None) for c in _CONDITIONS)
    has_action = any(getattr(payload, a, None) for a in _ACTIONS)
    if not has_cond:
        raise HTTPException(422, "A rule needs at least one condition")
    if not has_action:
        raise HTTPException(422, "A rule needs at least one action")


def create_rule(db: Session, *, actor: User, payload) -> dict:
    if not (payload.name or "").strip():
        raise HTTPException(422, "Rule name is required")
    _assert_semantics(payload)
    r = IntakeRoutingRule(
        org_id=actor.org_id, name=payload.name.strip(), description=(payload.description or None),
        enabled=payload.enabled, eval_order=payload.eval_order,
        created_by_user_id=actor.id, updated_by_user_id=actor.id,
    )
    for f in _CONDITIONS + _ACTIONS:
        setattr(r, f, getattr(payload, f, None))
    db.add(r)
    db.flush()
    write_audit_log(db, action="intake.routing_rule.created", resource_type="intake_routing_rule",
                    resource_id=r.id, org_id=actor.org_id, actor_user_id=actor.id, after={"name": r.name})
    db.commit()
    db.refresh(r)
    return serialize_rule(db, r)


def _get_rule(db: Session, org_id: str, rule_id: str) -> IntakeRoutingRule:
    r = db.get(IntakeRoutingRule, rule_id)
    if r is None or r.org_id != org_id:
        raise HTTPException(404, "Rule not found")
    return r


def update_rule(db: Session, *, actor: User, rule_id: str, payload) -> dict:
    r = _get_rule(db, actor.org_id, rule_id)
    for attr in ("name", "description", "enabled", "eval_order", *(_CONDITIONS + _ACTIONS)):
        val = getattr(payload, attr, None)
        if val is not None:
            setattr(r, attr, val)
    r.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(db, action="intake.routing_rule.updated", resource_type="intake_routing_rule",
                    resource_id=r.id, org_id=actor.org_id, actor_user_id=actor.id, after={"name": r.name})
    db.commit()
    db.refresh(r)
    return serialize_rule(db, r)


def delete_rule(db: Session, *, actor: User, rule_id: str) -> None:
    r = _get_rule(db, actor.org_id, rule_id)
    write_audit_log(db, action="intake.routing_rule.deleted", resource_type="intake_routing_rule",
                    resource_id=r.id, org_id=actor.org_id, actor_user_id=actor.id, before={"name": r.name})
    db.delete(r)
    db.commit()
