"""Intake team pools + the race-safe assignment balancer.

The balancer (Part 0.15): reject overflow *cycles* at save time, and acquire
member-row locks in deterministic global order (resolve the whole overflow
chain, sort the team ids, lock all their member rows in one FOR UPDATE query)
so two concurrent creates routing to A and B can't deadlock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.intake.constants import OPEN_STATUSES
from app.intake.models import (
    IntakeRequest,
    IntakeRoutingRule,
    IntakeTeam,
    IntakeTeamMember,
)

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass
class PoolPick:
    team_id: str
    team_name: str
    user_id: str
    user_name: str
    overflow: bool


def _label(db: Session, uid: str | None) -> str | None:
    if not uid:
        return None
    u = db.get(User, uid)
    return (u.full_name or u.email) if u else uid


# --- balancer --------------------------------------------------------------

def _resolve_chain(db: Session, team_id: str) -> list[IntakeTeam]:
    """The overflow chain starting at team_id, cycle-safe."""
    chain: list[IntakeTeam] = []
    seen: set[str] = set()
    cur = team_id
    while cur and cur not in seen:
        seen.add(cur)
        t = db.get(IntakeTeam, cur)
        if t is None or not t.active:
            break
        chain.append(t)
        cur = t.overflow_team_id
    return chain


def pick_from_pool(db: Session, *, team_id: str) -> PoolPick | None:
    chain = _resolve_chain(db, team_id)
    if not chain:
        return None
    team_ids = sorted(t.id for t in chain)
    # Lock ALL member rows across the chain in one deterministic-order query.
    members = db.scalars(
        select(IntakeTeamMember)
        .where(IntakeTeamMember.team_id.in_(team_ids), IntakeTeamMember.active.is_(True))
        .order_by(IntakeTeamMember.team_id, IntakeTeamMember.id)
        .with_for_update()
    ).all()
    if not members:
        return None
    user_ids = {m.user_id for m in members}
    # Open-work counts per member, computed after the lock (same txn).
    counts = dict(
        db.execute(
            select(IntakeRequest.assigned_to_user_id, func.count())
            .where(
                IntakeRequest.assigned_to_user_id.in_(user_ids),
                IntakeRequest.status.in_(OPEN_STATUSES),
            )
            .group_by(IntakeRequest.assigned_to_user_id)
        ).all()
    )
    by_team: dict[str, list[IntakeTeamMember]] = {}
    for m in members:
        by_team.setdefault(m.team_id, []).append(m)

    for i, t in enumerate(chain):
        elig = [
            m for m in by_team.get(t.id, [])
            if m.capacity <= 0 or counts.get(m.user_id, 0) < m.capacity
        ]
        if not elig:
            continue
        # Deterministic tie-breaks. last_assigned_at NULLS FIRST → never-picked wins.
        # Bind loop vars (t, counts) as defaults so the closure can't drift if this
        # is ever refactored to defer evaluation past the current iteration.
        def key(m: IntakeTeamMember, t=t, counts=counts):
            never = m.last_assigned_at is None
            la = m.last_assigned_at or utcnow()
            if t.strategy == "round_robin":
                return (0 if never else 1, la, m.user_id)
            return (counts.get(m.user_id, 0), 0 if never else 1, la, m.user_id)

        pick = min(elig, key=key)
        pick.last_assigned_at = utcnow()  # cursor bumped under lock
        return PoolPick(
            team_id=t.id, team_name=t.name, user_id=pick.user_id,
            user_name=_label(db, pick.user_id) or pick.user_id, overflow=i > 0,
        )
    return None  # all at capacity, no overflow left → stays unassigned


# --- CRUD ------------------------------------------------------------------

def serialize_team(db: Session, t: IntakeTeam) -> dict:
    overflow = db.get(IntakeTeam, t.overflow_team_id) if t.overflow_team_id else None
    # open counts per member (best-read, no lock)
    ids = {m.user_id for m in t.members}
    counts = dict(
        db.execute(
            select(IntakeRequest.assigned_to_user_id, func.count())
            .where(IntakeRequest.assigned_to_user_id.in_(ids or {""}),
                   IntakeRequest.status.in_(OPEN_STATUSES))
            .group_by(IntakeRequest.assigned_to_user_id)
        ).all()
    ) if ids else {}
    return {
        "id": t.id, "key": t.key, "name": t.name, "description": t.description,
        "active": t.active, "strategy": t.strategy,
        "overflow_team_id": t.overflow_team_id,
        "overflow_team_name": overflow.name if overflow else None,
        "sort_order": t.sort_order,
        "members": [
            {"id": m.id, "user_id": m.user_id, "name": _label(db, m.user_id) or m.user_id,
             "capacity": m.capacity, "active": m.active, "open_count": counts.get(m.user_id, 0)}
            for m in sorted(t.members, key=lambda m: m.id)
        ],
    }


def list_teams(db: Session, *, org_id: str) -> list[dict]:
    rows = db.scalars(
        select(IntakeTeam).where(IntakeTeam.org_id == org_id)
        .order_by(IntakeTeam.sort_order, IntakeTeam.name)
    ).all()
    return [serialize_team(db, t) for t in rows]


def _get_team(db: Session, org_id: str, team_id: str) -> IntakeTeam:
    t = db.get(IntakeTeam, team_id)
    if t is None or t.org_id != org_id:
        raise HTTPException(404, "Team not found")
    return t


def _check_no_cycle(db: Session, *, org_id: str, team_id: str | None, overflow_id: str | None) -> None:
    """Walking overflow from overflow_id must never reach team_id (Part 0.15)."""
    if not overflow_id:
        return
    if overflow_id == team_id:
        raise HTTPException(422, "A pool cannot overflow to itself")
    seen: set[str] = set()
    cur = overflow_id
    while cur:
        if cur == team_id:
            raise HTTPException(422, "Overflow chain would form a cycle")
        if cur in seen:
            break
        seen.add(cur)
        t = db.get(IntakeTeam, cur)
        cur = t.overflow_team_id if t else None


def _apply_members(db: Session, t: IntakeTeam, org_id: str, members) -> None:
    seen: set[str] = set()
    rows = []
    for m in members:
        if m.user_id in seen:
            continue
        seen.add(m.user_id)
        u = db.get(User, m.user_id)
        if u is None or u.org_id != org_id:
            raise HTTPException(404, "Team member not found in org")
        rows.append(IntakeTeamMember(org_id=org_id, user_id=m.user_id,
                                     capacity=max(0, m.capacity), active=m.active))
    t.members = rows


def create_team(db: Session, *, actor: User, payload) -> dict:
    key = (payload.key or "").strip().lower()
    if not _KEY_RE.match(key):
        raise HTTPException(422, "Team key must be lowercase alphanumeric / dash / underscore")
    if db.scalar(select(IntakeTeam.id).where(IntakeTeam.org_id == actor.org_id, IntakeTeam.key == key)):
        raise HTTPException(409, f'A pool "{key}" already exists')
    if payload.overflow_team_id:
        _get_team(db, actor.org_id, payload.overflow_team_id)
        _check_no_cycle(db, org_id=actor.org_id, team_id=None, overflow_id=payload.overflow_team_id)
    t = IntakeTeam(
        org_id=actor.org_id, key=key, name=payload.name.strip(),
        description=(payload.description or None), strategy=payload.strategy,
        overflow_team_id=payload.overflow_team_id, sort_order=payload.sort_order,
        created_by_user_id=actor.id, updated_by_user_id=actor.id,
    )
    _apply_members(db, t, actor.org_id, payload.members)
    db.add(t)
    db.flush()
    write_audit_log(db, action="intake.team.created", resource_type="intake_team",
                    resource_id=t.id, org_id=actor.org_id, actor_user_id=actor.id,
                    after={"key": t.key, "name": t.name})
    db.commit()
    db.refresh(t)
    return serialize_team(db, t)


def update_team(db: Session, *, actor: User, team_id: str, payload) -> dict:
    t = _get_team(db, actor.org_id, team_id)
    if payload.overflow_team_id is not None and payload.overflow_team_id:
        _get_team(db, actor.org_id, payload.overflow_team_id)
        _check_no_cycle(db, org_id=actor.org_id, team_id=t.id, overflow_id=payload.overflow_team_id)
    for attr in ("name", "description", "active", "strategy", "sort_order", "overflow_team_id"):
        val = getattr(payload, attr, None)
        if val is not None:
            setattr(t, attr, val)
    if payload.members is not None:
        _apply_members(db, t, actor.org_id, payload.members)
    t.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(db, action="intake.team.updated", resource_type="intake_team",
                    resource_id=t.id, org_id=actor.org_id, actor_user_id=actor.id, after={"key": t.key})
    db.commit()
    db.refresh(t)
    return serialize_team(db, t)


def delete_team(db: Session, *, actor: User, team_id: str) -> None:
    t = _get_team(db, actor.org_id, team_id)
    # NULL routing-rule and other pools' pointers first (models use SET NULL, but
    # be explicit so the app state is clean immediately).
    for r in db.scalars(select(IntakeRoutingRule).where(IntakeRoutingRule.set_team_id == t.id)).all():
        r.set_team_id = None
    for other in db.scalars(select(IntakeTeam).where(IntakeTeam.overflow_team_id == t.id)).all():
        other.overflow_team_id = None
    write_audit_log(db, action="intake.team.deleted", resource_type="intake_team",
                    resource_id=t.id, org_id=actor.org_id, actor_user_id=actor.id, before={"key": t.key})
    db.delete(t)  # members cascade
    db.commit()
