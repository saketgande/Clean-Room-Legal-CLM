"""Save as Draft for the agreement wizard: create, update, list, discard.

Drafts are private to the user who saved them and are never validated — a
draft is by definition incomplete. Validation happens when it is submitted.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.intake.agreement_forms import form_defs
from app.intake.models import IntakeDraft

MAX_DRAFTS_PER_USER = 50


def serialize(d: IntakeDraft) -> dict:
    return {
        "id": d.id, "form_key": d.form_key, "title": d.title, "values": d.values or {},
        "parent_contract_id": d.parent_contract_id, "page_index": d.page_index,
        "visited": d.visited, "created_at": d.created_at, "updated_at": d.updated_at,
    }


def _own(db: Session, actor: User, draft_id: str) -> IntakeDraft:
    d = db.get(IntakeDraft, draft_id)
    if d is None or d.org_id != actor.org_id or d.user_id != actor.id:
        raise HTTPException(404, "Draft not found")
    return d


def list_mine(db: Session, *, actor: User) -> list[dict]:
    rows = db.scalars(
        select(IntakeDraft)
        .where(IntakeDraft.org_id == actor.org_id, IntakeDraft.user_id == actor.id)
        .order_by(IntakeDraft.updated_at.desc())
    ).all()
    return [serialize(d) for d in rows]


def save(db: Session, *, actor: User, payload, draft_id: str | None = None) -> dict:
    if payload.form_key not in {f["key"] for f in form_defs()}:
        raise HTTPException(422, "Unknown agreement form")
    if draft_id:
        d = _own(db, actor, draft_id)
    else:
        count = len(list_mine(db, actor=actor))
        if count >= MAX_DRAFTS_PER_USER:
            raise HTTPException(409, f"You already have {count} drafts — submit or delete some first")
        d = IntakeDraft(org_id=actor.org_id, user_id=actor.id)
        db.add(d)
    d.form_key = payload.form_key
    d.title = (payload.title or "").strip()[:200] or None
    d.values = payload.values or {}
    d.parent_contract_id = payload.parent_contract_id or None
    d.page_index = payload.page_index
    d.visited = payload.visited
    db.commit()
    db.refresh(d)
    return serialize(d)


def delete(db: Session, *, actor: User, draft_id: str) -> None:
    db.delete(_own(db, actor, draft_id))
    db.commit()
