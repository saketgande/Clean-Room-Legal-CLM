"""Delegation of Authority — the ABAC action gate (legal RBAC Phase 4).

``evaluate_authority`` is the policy engine: given a subject (user), an action,
and a resource (contract), it decides whether any active authority policy covers
the attempt. ``enforce_authority`` is the choke-point the approval-decision and
signature-initiation endpoints call — it raises 403 (and Method-8 logs the deny)
when authority is lacking.

Progressive enforcement: if NO policy exists for an action org-wide, the gate is
dormant and RBAC alone governs — so enabling Phase 4 never retroactively blocks a
working approval flow. The moment an admin defines any policy for that action,
the action becomes authority-gated and every actor must be explicitly covered.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.auth.models import Role, User
from app.authority.models import AuthorityGrant
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.core.money import to_money

# Business actions the gate governs (decoupled from the RBAC permission strings).
ACTIONS = {"contract:approve", "contract:sign"}
RISK_BANDS = ["low", "medium", "high", "critical"]
PRINCIPAL_TYPES = {"user", "role"}


def _risk_rank(band: str | None) -> int:
    if not band:
        return 0
    try:
        return RISK_BANDS.index(band.lower())
    except ValueError:
        return 0


def _active_clause():
    now = utcnow()
    return and_(
        AuthorityGrant.revoked_at.is_(None),
        or_(AuthorityGrant.valid_from.is_(None), AuthorityGrant.valid_from <= now),
        or_(AuthorityGrant.valid_until.is_(None), AuthorityGrant.valid_until > now),
    )


def _principal_clause(user: User):
    conds = [
        and_(AuthorityGrant.principal_type == "user", AuthorityGrant.principal_id == user.id)
    ]
    role_ids = [r.id for r in getattr(user, "roles", [])]
    if role_ids:
        conds.append(
            and_(AuthorityGrant.principal_type == "role", AuthorityGrant.principal_id.in_(role_ids))
        )
    return or_(*conds)


# --- policy evaluation ----------------------------------------------------

@dataclass
class AuthorityDecision:
    allowed: bool
    gated: bool  # True when at least one policy exists for the action (gate is live)
    reason: str | None = None
    matched_grant_id: str | None = None


def _grant_covers(grant: AuthorityGrant, contract) -> tuple[bool, str | None]:
    """Does one policy's limits cover this contract? Returns (ok, miss_reason)."""
    value = to_money(contract.value_amount)
    if grant.max_value is not None:
        # Unknown value fails closed: counting it as 0 let any limit cover it.
        if contract.value_amount is None:
            return False, "the value is unknown, so it can't be confirmed within this authority's limit"
        # If both carry a currency and they differ, the limit can't be compared —
        # treat as not covering (a $-limit doesn't authorise a €-contract).
        if grant.currency and contract.currency and grant.currency != contract.currency:
            return False, (
                f"authority is in {grant.currency} but the contract is in {contract.currency}"
            )
        if value > to_money(grant.max_value):
            cur = contract.currency or grant.currency or ""
            return False, (
                f"contract value {cur}{value:,.0f} exceeds the authority limit "
                f"{cur}{grant.max_value:,.0f}"
            )
    types = grant.allowed_contract_types or []
    if types:
        ct = (contract.contract_type or "").lower()
        if ct not in {t.lower() for t in types}:
            return False, (
                f"contract type “{contract.contract_type or 'unspecified'}” is outside this authority"
            )
    juris = grant.allowed_jurisdictions or []
    if juris:
        cj = (contract.jurisdiction or "").lower()
        if cj not in {j.lower() for j in juris}:
            return False, (
                f"jurisdiction “{contract.jurisdiction or 'unspecified'}” is outside this authority"
            )
    if grant.max_risk_band:
        band = contract.risk_band or contract.risk_level
        if _risk_rank(band) > _risk_rank(grant.max_risk_band):
            return False, (
                f"risk band “{band}” exceeds the authorised ceiling “{grant.max_risk_band}”"
            )
    return True, None


def evaluate_authority(db: Session, *, user: User, action: str, contract) -> AuthorityDecision:
    """Decide whether ``user`` is authorised to perform ``action`` on ``contract``."""
    # Any policy for this action in the org? If none, the gate is dormant.
    any_policy = db.scalar(
        select(AuthorityGrant.id)
        .where(
            AuthorityGrant.org_id == user.org_id,
            AuthorityGrant.action == action,
            _active_clause(),
        )
        .limit(1)
    )
    if any_policy is None:
        return AuthorityDecision(allowed=True, gated=False)

    grants = db.scalars(
        select(AuthorityGrant).where(
            AuthorityGrant.org_id == user.org_id,
            AuthorityGrant.action == action,
            _principal_clause(user),
            _active_clause(),
        )
    ).all()
    if not grants:
        return AuthorityDecision(
            allowed=False,
            gated=True,
            reason="you hold no delegated authority for this action",
        )
    closest_miss: str | None = None
    for g in grants:
        ok, miss = _grant_covers(g, contract)
        if ok:
            return AuthorityDecision(allowed=True, gated=True, matched_grant_id=g.id)
        closest_miss = closest_miss or miss
    return AuthorityDecision(
        allowed=False, gated=True, reason=closest_miss or "outside your delegated authority"
    )


def enforce_authority(
    db: Session,
    *,
    user: User,
    action: str,
    contract,
    resource_type: str,
    resource_id: str,
    request_id: str | None = None,
) -> AuthorityDecision:
    """Raise 403 (+ Method-8 deny log) when authority is lacking; else return the
    decision. A no-op when the gate is dormant."""
    decision = evaluate_authority(db, user=user, action=action, contract=contract)
    if not decision.allowed:
        from app.core.authz import record_decision

        record_decision(
            user=user,
            action=action,
            outcome="denied",
            resource_type=resource_type,
            resource_id=resource_id,
            reason="authority_limit_exceeded",
            request_id=request_id,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Outside your delegated authority — {decision.reason}.",
        )
    return decision


# --- plain-words summaries (Teams page, workflow steps) -----------------------

def _gate_live(db: Session, *, org_id: str, action: str) -> bool:
    return db.scalar(
        select(AuthorityGrant.id).where(
            AuthorityGrant.org_id == org_id, AuthorityGrant.action == action, _active_clause()
        ).limit(1)
    ) is not None


def _money_label(value, currency: str | None) -> str:
    return f"{currency + ' ' if currency else ''}{to_money(value):,.0f}"


def authority_limits(
    db: Session, *, org_id: str, user_ids, action: str = "contract:approve"
) -> dict[str, str] | None:
    """Each user's limit for ``action`` in plain words ("up to INR 5,00,00,000",
    "no value limit", "no authority"). None while no policy exists for the action:
    the gate is dormant, so nobody is limited and there is nothing to show."""
    ids = [u for u in set(user_ids) if u]
    if not ids or not _gate_live(db, org_id=org_id, action=action):
        return None
    out: dict[str, str] = {}
    for user in db.scalars(select(User).where(User.org_id == org_id, User.id.in_(ids))).all():
        grants = db.scalars(select(AuthorityGrant).where(
            AuthorityGrant.org_id == org_id, AuthorityGrant.action == action,
            _principal_clause(user), _active_clause(),
        )).all()
        if not grants:
            out[user.id] = "no authority"
            continue
        if any(g.max_value is None for g in grants):
            label = "no value limit"
        else:
            top = max(grants, key=lambda g: to_money(g.max_value))
            label = f"up to {_money_label(top.max_value, top.currency)}"
        if any(g.allowed_contract_types or g.allowed_jurisdictions or g.max_risk_band for g in grants):
            label += " · some types/regions only"
        out[user.id] = label
    return out


def approval_authority_note(
    db: Session, *, org_id: str, user_ids, contract, action: str = "contract:approve"
) -> str | None:
    """What an approval step needs, and how many of its approvers have it — the
    same check the decision itself runs (``evaluate_authority``). None while the
    gate is dormant."""
    ids = [u for u in dict.fromkeys(user_ids) if u]
    if not ids or not _gate_live(db, org_id=org_id, action=action):
        return None
    users = db.scalars(select(User).where(User.org_id == org_id, User.id.in_(ids))).all()
    if contract is None:
        limits = authority_limits(db, org_id=org_id, user_ids=ids, action=action) or {}
        have = sum(1 for u in users if limits.get(u.id) != "no authority")
        return f"Needs approval authority · {have} of {len(users)} can approve"
    have = sum(1 for u in users if evaluate_authority(db, user=u, action=action, contract=contract).allowed)
    need = ("for " + _money_label(contract.value_amount, contract.currency)
            if contract.value_amount is not None else "(value unknown — only no-limit authority covers it)")
    return f"Needs authority {need} · {have} of {len(users)} can approve"


# --- serialization --------------------------------------------------------

def _principal_label(db: Session, principal_type: str, principal_id: str) -> str:
    if principal_type == "user":
        u = db.get(User, principal_id)
        return (u.full_name or u.email) if u else principal_id
    r = db.get(Role, principal_id)
    return r.name if r else principal_id


def serialize_grant(db: Session, g: AuthorityGrant) -> dict:
    now = utcnow()
    active = g.revoked_at is None and (g.valid_until is None or g.valid_until > now)
    delegated_by = (
        _principal_label(db, "user", g.delegated_by_user_id) if g.delegated_by_user_id else None
    )
    return {
        "id": g.id,
        "principal_type": g.principal_type,
        "principal_id": g.principal_id,
        "principal_label": _principal_label(db, g.principal_type, g.principal_id),
        "action": g.action,
        "max_value": g.max_value,
        "currency": g.currency,
        "allowed_contract_types": g.allowed_contract_types or [],
        "allowed_jurisdictions": g.allowed_jurisdictions or [],
        "max_risk_band": g.max_risk_band,
        "delegated_by_user_id": g.delegated_by_user_id,
        "delegated_by_label": delegated_by,
        "note": g.note,
        "valid_until": g.valid_until.isoformat() if g.valid_until else None,
        "revoked_at": g.revoked_at.isoformat() if g.revoked_at else None,
        "active": active,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }


def list_grants(db: Session, *, org_id: str, include_revoked: bool = False) -> list[dict]:
    q = select(AuthorityGrant).where(AuthorityGrant.org_id == org_id)
    if not include_revoked:
        q = q.where(AuthorityGrant.revoked_at.is_(None))
    rows = db.scalars(q.order_by(AuthorityGrant.action, AuthorityGrant.created_at.desc())).all()
    return [serialize_grant(db, g) for g in rows]


def describe_self(db: Session, *, user: User) -> dict:
    """What the current user is authorised to do, per gated action — for UI hints."""
    out: dict[str, dict] = {}
    for action in sorted(ACTIONS):
        any_policy = db.scalar(
            select(AuthorityGrant.id)
            .where(
                AuthorityGrant.org_id == user.org_id,
                AuthorityGrant.action == action,
                _active_clause(),
            )
            .limit(1)
        )
        if any_policy is None:
            out[action] = {"gated": False, "grants": []}
            continue
        grants = db.scalars(
            select(AuthorityGrant).where(
                AuthorityGrant.org_id == user.org_id,
                AuthorityGrant.action == action,
                _principal_clause(user),
                _active_clause(),
            )
        ).all()
        out[action] = {
            "gated": True,
            "grants": [serialize_grant(db, g) for g in grants],
        }
    return out


# --- validation + mutations ----------------------------------------------

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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Principal not found in this organization")


def _validate_payload(db: Session, *, org_id: str, payload) -> None:
    if payload.action not in ACTIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported action")
    if payload.max_risk_band and payload.max_risk_band not in RISK_BANDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid max_risk_band")
    if payload.max_value is not None and payload.max_value < 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "max_value cannot be negative")
    _validate_principal(
        db, org_id=org_id, principal_type=payload.principal_type, principal_id=payload.principal_id
    )
    if getattr(payload, "delegated_by_user_id", None):
        db_user = db.get(User, payload.delegated_by_user_id)
        if db_user is None or db_user.org_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Delegating user not found")


def create_grant(db: Session, *, actor: User, payload) -> dict:
    _validate_payload(db, org_id=actor.org_id, payload=payload)
    g = AuthorityGrant(
        org_id=actor.org_id,
        principal_type=payload.principal_type,
        principal_id=payload.principal_id,
        action=payload.action,
        max_value=payload.max_value,
        currency=(payload.currency or None),
        allowed_contract_types=payload.allowed_contract_types or None,
        allowed_jurisdictions=payload.allowed_jurisdictions or None,
        max_risk_band=payload.max_risk_band or None,
        delegated_by_user_id=getattr(payload, "delegated_by_user_id", None),
        note=(payload.note or "").strip() or None,
        valid_from=utcnow(),
        valid_until=payload.valid_until,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(g)
    db.flush()
    write_audit_log(
        db,
        action="authority.created",
        resource_type="authority_grant",
        resource_id=g.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        after={
            "principal_type": g.principal_type,
            "principal_id": g.principal_id,
            "action": g.action,
            "max_value": g.max_value,
        },
    )
    db.commit()
    db.refresh(g)
    return serialize_grant(db, g)


def _get(db: Session, org_id: str, grant_id: str) -> AuthorityGrant:
    g = db.get(AuthorityGrant, grant_id)
    if g is None or g.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Authority grant not found")
    return g


def update_grant(db: Session, *, actor: User, grant_id: str, payload) -> dict:
    g = _get(db, actor.org_id, grant_id)
    if payload.max_value is not None and payload.max_value < 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "max_value cannot be negative")
    if payload.max_risk_band and payload.max_risk_band not in RISK_BANDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid max_risk_band")
    for field in (
        "max_value",
        "currency",
        "allowed_contract_types",
        "allowed_jurisdictions",
        "max_risk_band",
        "note",
        "valid_until",
    ):
        val = getattr(payload, field, None)
        if val is not None:
            setattr(g, field, val)
    g.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(
        db,
        action="authority.updated",
        resource_type="authority_grant",
        resource_id=g.id,
        org_id=actor.org_id,
        actor_user_id=actor.id,
        after={"max_value": g.max_value, "action": g.action},
    )
    db.commit()
    db.refresh(g)
    return serialize_grant(db, g)


def revoke_grant(db: Session, *, actor: User, grant_id: str) -> None:
    g = _get(db, actor.org_id, grant_id)
    if g.revoked_at is None:
        g.revoked_at = utcnow()
        g.updated_by_user_id = actor.id
        write_audit_log(
            db,
            action="authority.revoked",
            resource_type="authority_grant",
            resource_id=g.id,
            org_id=actor.org_id,
            actor_user_id=actor.id,
        )
        db.commit()
