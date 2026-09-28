"""Search, create and edit legal entities and counterparties."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.audit import write_audit_log, write_timeline_event
from app.parties.models import Counterparty, LegalEntity

_MODELS = {"legal_entity": LegalEntity, "counterparty": Counterparty}
_FIELDS = {
    "legal_entity": ("name", "jurisdiction", "registered_address", "authorised_signatory", "active"),
    "counterparty": ("name", "jurisdiction", "address", "contact_email", "active"),
}


def serialize(row) -> dict:
    kind = "legal_entity" if isinstance(row, LegalEntity) else "counterparty"
    out = {"id": row.id, "source": row.source, "external_ref": row.external_ref,
           "created_at": row.created_at, "updated_at": row.updated_at}
    out.update({f: getattr(row, f) for f in _FIELDS[kind]})
    return out


def search(db: Session, *, actor: User, kind: str, q: str = "", include_inactive: bool = False,
           limit: int = 20) -> list[dict]:
    model = _MODELS[kind]
    stmt = select(model).where(model.org_id == actor.org_id)
    if not include_inactive:
        stmt = stmt.where(model.active.is_(True))
    q = (q or "").strip()
    if q:
        # Contains-match, so "acme" finds "The Acme Corporation".
        stmt = stmt.where(func.lower(model.name).contains(q.lower()))
    rows = db.scalars(stmt.order_by(func.lower(model.name)).limit(min(max(limit, 1), 200))).all()
    return [serialize(r) for r in rows]


def get(db: Session, *, org_id: str, kind: str, record_id: str | None):
    """The record, or None if it doesn't exist in this organisation."""
    if not record_id:
        return None
    row = db.get(_MODELS[kind], record_id)
    return row if row is not None and row.org_id == org_id else None


def _clean_name(name: str | None) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise HTTPException(422, "Name is required")
    return cleaned[:200]


def create(db: Session, *, actor: User, kind: str, payload) -> dict:
    model = _MODELS[kind]
    name = _clean_name(payload.name)
    existing = db.scalar(select(model).where(model.org_id == actor.org_id,
                                             func.lower(model.name) == name.lower()))
    if existing is not None:
        label = "An entity" if kind == "legal_entity" else "A counterparty"
        raise HTTPException(409, f'{label} called "{existing.name}" already exists — pick it from the list')
    row = model(org_id=actor.org_id, name=name, created_by_user_id=actor.id, updated_by_user_id=actor.id)
    for f in _FIELDS[kind]:
        if f not in ("name", "active") and getattr(payload, f, None) is not None:
            setattr(row, f, (getattr(payload, f) or "").strip() or None)
    db.add(row)
    db.flush()
    write_audit_log(db, action=f"{kind}.created", resource_type=kind, resource_id=row.id,
                    org_id=actor.org_id, actor_user_id=actor.id, after={"name": row.name})
    write_timeline_event(db, org_id=actor.org_id, resource_type=kind, resource_id=row.id,
                         event_type=f"{kind}.created", title=f"Created {row.name}", actor_user_id=actor.id)
    db.commit()
    db.refresh(row)
    return serialize(row)


def update(db: Session, *, actor: User, kind: str, record_id: str, payload) -> dict:
    row = get(db, org_id=actor.org_id, kind=kind, record_id=record_id)
    if row is None:
        raise HTTPException(404, "Not found")
    before = serialize(row)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        name = _clean_name(data["name"])
        model = _MODELS[kind]
        clash = db.scalar(select(model.id).where(model.org_id == actor.org_id, model.id != row.id,
                                                 func.lower(model.name) == name.lower()))
        if clash:
            raise HTTPException(409, f'"{name}" already exists')
        row.name = name
    for f in _FIELDS[kind]:
        if f in data and f != "name":
            val = data[f]
            setattr(row, f, (val.strip() or None) if isinstance(val, str) else val)
    row.updated_by_user_id = actor.id
    db.flush()
    write_audit_log(db, action=f"{kind}.updated", resource_type=kind, resource_id=row.id,
                    org_id=actor.org_id, actor_user_id=actor.id,
                    before={k: before[k] for k in _FIELDS[kind]},
                    after={k: getattr(row, k) for k in _FIELDS[kind]})
    db.commit()
    db.refresh(row)
    return serialize(row)
