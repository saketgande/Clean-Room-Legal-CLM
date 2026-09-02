"""Ethical walls — conflict-of-interest screens (the deny-override layer).

A wall is a HARD DENY. If any *active* wall covers a contract and lists the user
(directly, or by a role they hold), that user cannot see or touch the contract —
no matter what they own, what's been granted to them, or whether they're an
org-admin. Walls override every ALLOW. That inversion is the whole point of an
ethical screen: a conflicted lawyer must be sealed off from the matter even if
they created it.

Two enforcement shapes, kept in lock-step:
  * ``wall_block_filter(user)`` — a SQL predicate correlated on ``Contract`` for
    ``WHERE`` clauses (list views, retrieval scoping).
  * ``user_is_walled(db, ...)`` — the row-level equivalent for single-object
    access checks.
Both are consumed by app/contracts/access.py so the rule applies uniformly —
including on the RAG retrieval path, which funnels through the same predicates.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.auth.models import Role, User
from app.contracts.models import Contract
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.matters.models import Matter, MatterContract
from app.walls.models import EthicalWall, EthicalWallPrincipal

SCOPE_TYPES = {"contract", "project"}
PRINCIPAL_TYPES = {"user", "role"}


# --- enforcement predicates -----------------------------------------------

def _principal_match_sql(user: User):
    role_ids = [r.id for r in getattr(user, "roles", [])]
    conds = [
        and_(
            EthicalWallPrincipal.principal_type == "user",
            EthicalWallPrincipal.principal_id == user.id,
        )
    ]
    if role_ids:
        conds.append(
            and_(
                EthicalWallPrincipal.principal_type == "role",
                EthicalWallPrincipal.principal_id.in_(role_ids),
            )
        )
    return or_(*conds)


def _wall_covers_contract_sql():
    """Correlated on the outer ``Contract``: does this wall's scope cover the row?"""
    contract_scope = and_(
        EthicalWall.scope_type == "contract", EthicalWall.scope_id == Contract.id
    )
    project_scope = and_(
        EthicalWall.scope_type == "project",
        exists(
            select(MatterContract.id).where(
                MatterContract.matter_id == EthicalWall.scope_id,
                MatterContract.contract_id == Contract.id,
            )
        ),
    )
    return or_(contract_scope, project_scope)


def wall_block_filter(user: User):
    """SQL predicate (correlated on ``Contract``): TRUE when the user is NOT
    walled off from the row. ``AND`` this into any contract-visibility query."""
    barred = (
        select(EthicalWallPrincipal.id)
        .join(EthicalWall, EthicalWall.id == EthicalWallPrincipal.wall_id)
        .where(
            EthicalWall.active.is_(True),
            EthicalWall.org_id == user.org_id,
            _wall_covers_contract_sql(),
            _principal_match_sql(user),
        )
    )
    return ~barred.exists()


def user_is_walled(db: Session, *, user: User, contract: Contract) -> bool:
    """Row check: is the user sealed off from this specific contract by an active
    ethical wall (scoped to the contract, or to a project it belongs to)?"""
    project_ids = [
        pc.matter_id
        for pc in db.scalars(
            select(MatterContract).where(MatterContract.contract_id == contract.id)
        ).all()
    ]
    scope_conds = [
        and_(EthicalWall.scope_type == "contract", EthicalWall.scope_id == contract.id)
    ]
    if project_ids:
        scope_conds.append(
            and_(EthicalWall.scope_type == "project", EthicalWall.scope_id.in_(project_ids))
        )
    hit = db.scalar(
        select(EthicalWallPrincipal.id)
        .join(EthicalWall, EthicalWall.id == EthicalWallPrincipal.wall_id)
        .where(
            EthicalWall.active.is_(True),
            EthicalWall.org_id == user.org_id,
            or_(*scope_conds),
            _principal_match_sql(user),
        )
        .limit(1)
    )
    return hit is not None


# --- scope / principal resolution + serialization -------------------------

def _scope_label(db: Session, scope_type: str, scope_id: str) -> str | None:
    if scope_type == "contract":
        c = db.get(Contract, scope_id)
        return c.title if c else None
    if scope_type == "project":
        p = db.get(Matter, scope_id)
        return p.name if p else None
    return None


def _principal_label(db: Session, principal_type: str, principal_id: str) -> str:
    if principal_type == "user":
        u = db.get(User, principal_id)
        return (u.full_name or u.email) if u else principal_id
    if principal_type == "role":
        r = db.get(Role, principal_id)
        return r.name if r else principal_id
    return principal_id


def serialize_wall(db: Session, wall: EthicalWall) -> dict:
    return {
        "id": wall.id,
        "name": wall.name,
        "reason": wall.reason,
        "scope_type": wall.scope_type,
        "scope_id": wall.scope_id,
        "scope_label": _scope_label(db, wall.scope_type, wall.scope_id),
        "active": wall.active,
        "principals": [
            {
                "id": p.id,
                "principal_type": p.principal_type,
                "principal_id": p.principal_id,
                "principal_label": _principal_label(db, p.principal_type, p.principal_id),
            }
            for p in wall.principals
        ],
        "created_at": wall.created_at.isoformat() if wall.created_at else None,
    }


def list_walls(db: Session, *, org_id: str, include_inactive: bool = True) -> list[dict]:
    q = select(EthicalWall).where(EthicalWall.org_id == org_id)
    if not include_inactive:
        q = q.where(EthicalWall.active.is_(True))
    rows = db.scalars(q.order_by(EthicalWall.created_at.desc())).all()
    return [serialize_wall(db, w) for w in rows]


# --- validation -----------------------------------------------------------

def _validate_scope(db: Session, *, org_id: str, scope_type: str, scope_id: str) -> None:
    if scope_type not in SCOPE_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid scope_type")
    if scope_type == "contract":
        c = db.get(Contract, scope_id)
        ok = c is not None and c.org_id == org_id
    else:
        p = db.get(Matter, scope_id)
        ok = p is not None and p.org_id == org_id
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Walled resource not found")


def _validate_principal(db: Session, *, org_id: str, principal_type: str, principal_id: str) -> None:
    if principal_type not in PRINCIPAL_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid principal_type")
    if principal_type == "user":
        u = db.get(User, principal_id)
        ok = u is not None and u.org_id == org_id
    else:
        r = db.get(Role, principal_id)
        ok = r is not None and r.org_id == org_id
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Barred principal not found")


def _apply_principals(db: Session, wall: EthicalWall, org_id: str, principals: list) -> None:
    seen: set[tuple[str, str]] = set()
    rows: list[EthicalWallPrincipal] = []
    for p in principals:
        key = (p.principal_type, p.principal_id)
        if key in seen:
            continue
        seen.add(key)
        _validate_principal(
            db, org_id=org_id, principal_type=p.principal_type, principal_id=p.principal_id
        )
        rows.append(
            EthicalWallPrincipal(
                org_id=org_id, principal_type=p.principal_type, principal_id=p.principal_id
            )
        )
    if not rows:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "A wall must bar at least one principal"
        )
    wall.principals = rows


# --- mutations ------------------------------------------------------------

def create_wall(db: Session, *, actor: User, payload) -> dict:
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Wall name is required")
    _validate_scope(
        db, org_id=actor.org_id, scope_type=payload.scope_type, scope_id=payload.scope_id
    )
    wall = EthicalWall(
        org_id=actor.org_id,
        name=name,
        reason=(payload.reason or "").strip() or None,
        scope_type=payload.scope_type,
        scope_id=payload.scope_id,
        active=True,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    _apply_principals(db, wall, actor.org_id, payload.principals)
    db.add(wall)
    db.flush()
    write_audit_log(
        db,
        action="ethical_wall.created",
        resource_type="ethical_wall",
        resource_id=wall.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        after={
            "name": wall.name,
            "scope_type": wall.scope_type,
            "scope_id": wall.scope_id,
            "principals": [(p.principal_type, p.principal_id) for p in wall.principals],
        },
    )
    db.commit()
    db.refresh(wall)
    return serialize_wall(db, wall)


def _get_wall(db: Session, org_id: str, wall_id: str) -> EthicalWall:
    wall = db.get(EthicalWall, wall_id)
    if wall is None or wall.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Wall not found")
    return wall


def update_wall(db: Session, *, actor: User, wall_id: str, payload) -> dict:
    wall = _get_wall(db, actor.org_id, wall_id)
    before = {"name": wall.name, "active": wall.active}
    if payload.name is not None:
        new_name = payload.name.strip()
        if not new_name:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Wall name is required")
        wall.name = new_name
    if payload.reason is not None:
        wall.reason = payload.reason.strip() or None
    if payload.active is not None and payload.active != wall.active:
        wall.active = payload.active
        wall.deactivated_at = None if payload.active else utcnow()
    if payload.principals is not None:
        _apply_principals(db, wall, actor.org_id, payload.principals)
    wall.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(
        db,
        action="ethical_wall.updated",
        resource_type="ethical_wall",
        resource_id=wall.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        before=before,
        after={"name": wall.name, "active": wall.active},
    )
    db.commit()
    db.refresh(wall)
    return serialize_wall(db, wall)


def delete_wall(db: Session, *, actor: User, wall_id: str) -> None:
    wall = _get_wall(db, actor.org_id, wall_id)
    write_audit_log(
        db,
        action="ethical_wall.deleted",
        resource_type="ethical_wall",
        resource_id=wall.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        before={"name": wall.name, "scope_type": wall.scope_type, "scope_id": wall.scope_id},
    )
    db.delete(wall)
    db.commit()
