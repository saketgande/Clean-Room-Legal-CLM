"""Teams — the one list of groups of people who do the work — plus the
race-safe assignment balancer.

A team owns new requests (the default intake team, or the AI's expertise
match), does workflow review steps, and approves at workflow approval steps.
There are no separate pools or approver groups (merged 2026-09-28).

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


DEFAULT_TEAMS: tuple[tuple[str, str, str], ...] = (
    ("legal_counsel", "Legal Counsel", "Contract review, negotiation and legal sign-off."),
    ("paralegals", "Paralegals", "Simple NDAs, first drafts and filing."),
    ("finance", "Finance", "Pricing, payment terms, budget and revenue impact."),
    ("procurement", "Procurement", "Supplier terms and sourcing policy."),
    ("compliance", "Compliance", "Regulatory, privacy and security requirements."),
    ("executive", "Executive", "Final sign-off for high-value or strategic agreements."),
)


def ensure_default_teams(db: Session, *, org_id: str, actor_user_id: str | None = None) -> list[IntakeTeam]:
    """Create any default team the org lacks (by key or name) — empty, for an
    admin to fill — and make Legal Counsel the default intake team when none
    is set. Idempotent; the caller commits."""
    have = db.scalars(select(IntakeTeam).where(IntakeTeam.org_id == org_id)).all()
    keys = {t.key for t in have}
    names = {t.name.lower() for t in have}
    created = []
    for key, name, description in DEFAULT_TEAMS:
        if key in keys or name.lower() in names:
            continue
        t = IntakeTeam(org_id=org_id, key=key, name=name, description=description,
                       created_by_user_id=actor_user_id, updated_by_user_id=actor_user_id)
        db.add(t)
        created.append(t)
    db.flush()
    if not any(t.is_default_intake for t in have):
        counsel = db.scalar(select(IntakeTeam).where(IntakeTeam.org_id == org_id,
                                                     IntakeTeam.key == "legal_counsel"))
        if counsel is not None:
            counsel.is_default_intake = True
    return created


def default_intake_team(db: Session, *, org_id: str) -> IntakeTeam | None:
    return db.scalar(select(IntakeTeam).where(IntakeTeam.org_id == org_id, IntakeTeam.active.is_(True),
                                              IntakeTeam.is_default_intake.is_(True)))


def member_users(db: Session, *, team_id: str | None, org_id: str) -> list[User]:
    """The team's active members as users — who is emailed for, and may
    decide, an approval addressed to the team."""
    if not team_id:
        return []
    return list(db.scalars(
        select(User).join(IntakeTeamMember, IntakeTeamMember.user_id == User.id)
        .where(IntakeTeamMember.team_id == team_id, IntakeTeamMember.active.is_(True),
               User.org_id == org_id)
        .order_by(User.id)
    ).all())


def _uses(db: Session, t: IntakeTeam) -> list[dict]:
    """Where the team is used: default intake, and each workflow step naming it."""
    from app.workflows.models import Workflow

    out = [{"where": "Owner of new requests", "kind": "intake"}] if t.is_default_intake else []
    for wf in db.scalars(select(Workflow).where(Workflow.org_id == t.org_id)).all():
        for st in wf.steps or []:
            if ((st or {}).get("config") or {}).get("team_id") == t.id:
                out.append({"where": f"{wf.name} · {st.get('name') or 'step'}", "kind": st.get("type"),
                            "stage": st.get("stage")})
    return out


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


def pick_from_pool(db: Session, *, team_id: str, exclude_user_id: str | None = None) -> PoolPick | None:
    """The member who takes the next item. ``exclude_user_id`` keeps a person
    from being handed their own request to own."""
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
            if (m.capacity <= 0 or counts.get(m.user_id, 0) < m.capacity)
            and m.user_id != exclude_user_id
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
    from app.authority.service import authority_limits

    limits = authority_limits(db, org_id=t.org_id, user_ids=ids) or {}
    return {
        "id": t.id, "key": t.key, "name": t.name, "description": t.description,
        "active": t.active, "strategy": t.strategy,
        "overflow_team_id": t.overflow_team_id,
        "overflow_team_name": overflow.name if overflow else None,
        "sort_order": t.sort_order,
        "is_default_intake": bool(t.is_default_intake),
        "used_in": _uses(db, t),
        "expertise": t.expertise or [],
        "departments": t.departments or [],
        "members": [
            {"id": m.id, "user_id": m.user_id, "name": _label(db, m.user_id) or m.user_id,
             "capacity": m.capacity, "active": m.active, "open_count": counts.get(m.user_id, 0),
             "approve_limit": limits.get(m.user_id)}
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
        raise HTTPException(422, "A team cannot overflow to itself")
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
        raise HTTPException(409, f'A team "{key}" already exists')
    if payload.overflow_team_id:
        _get_team(db, actor.org_id, payload.overflow_team_id)
        _check_no_cycle(db, org_id=actor.org_id, team_id=None, overflow_id=payload.overflow_team_id)
    t = IntakeTeam(
        org_id=actor.org_id, key=key, name=payload.name.strip(),
        description=(payload.description or None), strategy=payload.strategy,
        overflow_team_id=payload.overflow_team_id, sort_order=payload.sort_order,
        expertise=(payload.expertise or None), departments=(payload.departments or None),
        created_by_user_id=actor.id, updated_by_user_id=actor.id,
    )
    _apply_members(db, t, actor.org_id, payload.members)
    db.add(t)
    db.flush()
    if payload.is_default_intake:
        _make_default_intake(db, t)
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
    for attr in ("name", "description", "active", "strategy", "sort_order", "overflow_team_id",
                 "expertise", "departments"):
        val = getattr(payload, attr, None)
        if val is not None:
            setattr(t, attr, val)
    if payload.members is not None:
        _apply_members(db, t, actor.org_id, payload.members)
    if payload.is_default_intake:
        _make_default_intake(db, t)
    t.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(db, action="intake.team.updated", resource_type="intake_team",
                    resource_id=t.id, org_id=actor.org_id, actor_user_id=actor.id, after={"key": t.key})
    db.commit()
    db.refresh(t)
    return serialize_team(db, t)


def _make_default_intake(db: Session, t: IntakeTeam) -> None:
    """Exactly one default intake team per org."""
    for other in db.scalars(select(IntakeTeam).where(IntakeTeam.org_id == t.org_id,
                                                     IntakeTeam.is_default_intake.is_(True))).all():
        other.is_default_intake = False
    t.is_default_intake = True


def delete_team(db: Session, *, actor: User, team_id: str) -> None:
    t = _get_team(db, actor.org_id, team_id)
    uses = _uses(db, t)
    if uses:
        # A workflow step naming a deleted team would ask nobody.
        raise HTTPException(409, "This team is still used — " + "; ".join(u["where"] for u in uses[:5])
                            + ". Move those steps to another team first.")
    # NULL other pools' overflow pointers first (the model uses SET NULL, but
    # be explicit so the app state is clean immediately).
    for other in db.scalars(select(IntakeTeam).where(IntakeTeam.overflow_team_id == t.id)).all():
        other.overflow_team_id = None
    write_audit_log(db, action="intake.team.deleted", resource_type="intake_team",
                    resource_id=t.id, org_id=actor.org_id, actor_user_id=actor.id, before={"key": t.key})
    db.delete(t)  # members cascade
    db.commit()
