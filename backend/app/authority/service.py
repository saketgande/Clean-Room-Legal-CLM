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
    value = contract.value_amount or 0.0
    if grant.max_value is not None:
        # If both carry a currency and they differ, the limit can't be compared —
        # treat as not covering (a $-limit doesn't authorise a €-contract).
        if grant.currency and contract.currency and grant.currency != contract.currency:
            return False, (
                f"authority is in {grant.currency} but the contract is in {contract.currency}"
            )
        if value > grant.max_value:
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


class AuthorityService:
    """Delegation-of-Authority policy evaluation, enforcement, and CRUD.

    Part of the DI migration (see backend/DI_MIGRATION.md). Constructed with
    a ``db`` session; every function that took ``db`` first is now a method
    reading ``self.db``. Pure helpers (``_risk_rank``, ``_active_clause``,
    ``_principal_clause``, ``_grant_covers``, the ``AuthorityDecision``
    dataclass) stay module-level — none of them touch ``db``.
    """

    def __init__(self, db: Session):
        self.db = db

    def evaluate_authority(self, *, user: User, action: str, contract) -> AuthorityDecision:
        """Decide whether ``user`` is authorised to perform ``action`` on ``contract``."""
        db = self.db
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
        self,
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
        decision = self.evaluate_authority(user=user, action=action, contract=contract)
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

    # --- serialization --------------------------------------------------------

    def _principal_label(self, principal_type: str, principal_id: str) -> str:
        db = self.db
        if principal_type == "user":
            u = db.get(User, principal_id)
            return (u.full_name or u.email) if u else principal_id
        r = db.get(Role, principal_id)
        return r.name if r else principal_id

    def serialize_grant(self, g: AuthorityGrant) -> dict:
        now = utcnow()
        active = g.revoked_at is None and (g.valid_until is None or g.valid_until > now)
        delegated_by = (
            self._principal_label("user", g.delegated_by_user_id) if g.delegated_by_user_id else None
        )
        return {
            "id": g.id,
            "principal_type": g.principal_type,
            "principal_id": g.principal_id,
            "principal_label": self._principal_label(g.principal_type, g.principal_id),
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

    def list_grants(self, *, org_id: str, include_revoked: bool = False) -> list[dict]:
        q = select(AuthorityGrant).where(AuthorityGrant.org_id == org_id)
        if not include_revoked:
            q = q.where(AuthorityGrant.revoked_at.is_(None))
        rows = self.db.scalars(q.order_by(AuthorityGrant.action, AuthorityGrant.created_at.desc())).all()
        return [self.serialize_grant(g) for g in rows]

    def describe_self(self, *, user: User) -> dict:
        """What the current user is authorised to do, per gated action — for UI hints."""
        db = self.db
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
                "grants": [self.serialize_grant(g) for g in grants],
            }
        return out

    # --- validation + mutations ----------------------------------------------

    def _validate_principal(self, *, org_id: str, principal_type: str, principal_id: str) -> None:
        db = self.db
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

    def _validate_payload(self, *, org_id: str, payload) -> None:
        db = self.db
        if payload.action not in ACTIONS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported action")
        if payload.max_risk_band and payload.max_risk_band not in RISK_BANDS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid max_risk_band")
        if payload.max_value is not None and payload.max_value < 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "max_value cannot be negative")
        self._validate_principal(
            org_id=org_id, principal_type=payload.principal_type, principal_id=payload.principal_id
        )
        if getattr(payload, "delegated_by_user_id", None):
            db_user = db.get(User, payload.delegated_by_user_id)
            if db_user is None or db_user.org_id != org_id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Delegating user not found")

    def create_grant(self, *, actor: User, payload) -> dict:
        db = self.db
        self._validate_payload(org_id=actor.org_id, payload=payload)
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
        return self.serialize_grant(g)

    def _get(self, org_id: str, grant_id: str) -> AuthorityGrant:
        g = self.db.get(AuthorityGrant, grant_id)
        if g is None or g.org_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Authority grant not found")
        return g

    def update_grant(self, *, actor: User, grant_id: str, payload) -> dict:
        db = self.db
        g = self._get(actor.org_id, grant_id)
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
        return self.serialize_grant(g)

    def revoke_grant(self, *, actor: User, grant_id: str) -> None:
        db = self.db
        g = self._get(actor.org_id, grant_id)
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


# --- DI-MIGRATION: temporary wrappers ---------------------------------------
# enforce_authority is imported directly by approvals/routes.py,
# approvals/service.py, and signatures/routes.py (all via deferred imports).
# Kept working unchanged here; migrated in a later pass. Tracked in
# backend/DI_MIGRATION.md.

def evaluate_authority(db: Session, *, user: User, action: str, contract) -> AuthorityDecision:
    return AuthorityService(db).evaluate_authority(user=user, action=action, contract=contract)


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
    return AuthorityService(db).enforce_authority(
        user=user,
        action=action,
        contract=contract,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
    )


def serialize_grant(db: Session, g: AuthorityGrant) -> dict:
    return AuthorityService(db).serialize_grant(g)


def list_grants(db: Session, *, org_id: str, include_revoked: bool = False) -> list[dict]:
    return AuthorityService(db).list_grants(org_id=org_id, include_revoked=include_revoked)


def describe_self(db: Session, *, user: User) -> dict:
    return AuthorityService(db).describe_self(user=user)


def create_grant(db: Session, *, actor: User, payload) -> dict:
    return AuthorityService(db).create_grant(actor=actor, payload=payload)


def update_grant(db: Session, *, actor: User, grant_id: str, payload) -> dict:
    return AuthorityService(db).update_grant(actor=actor, grant_id=grant_id, payload=payload)


def revoke_grant(db: Session, *, actor: User, grant_id: str) -> None:
    return AuthorityService(db).revoke_grant(actor=actor, grant_id=grant_id)
