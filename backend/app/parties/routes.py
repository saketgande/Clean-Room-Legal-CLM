from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_permission
from app.parties import service

router = APIRouter(tags=["parties"])

# Anyone who can file a request needs to look parties up and add a missing
# counterparty; only admins maintain the organisation's own legal entities.
_FILER = require_permission("intake:create")
_ADMIN = require_permission("admin_panel:access")


class EntityIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    jurisdiction: str | None = Field(default=None, max_length=120)
    registered_address: str | None = Field(default=None, max_length=2000)
    authorised_signatory: str | None = Field(default=None, max_length=200)


class EntityPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    jurisdiction: str | None = Field(default=None, max_length=120)
    registered_address: str | None = Field(default=None, max_length=2000)
    authorised_signatory: str | None = Field(default=None, max_length=200)
    active: bool | None = None


class EntityOut(BaseModel):
    id: str
    name: str
    jurisdiction: str | None = None
    registered_address: str | None = None
    authorised_signatory: str | None = None
    active: bool
    source: str
    external_ref: str | None = None
    created_at: datetime
    updated_at: datetime


class CounterpartyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    jurisdiction: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, max_length=2000)
    contact_email: EmailStr | None = None


class CounterpartyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    jurisdiction: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, max_length=2000)
    contact_email: EmailStr | None = None
    active: bool | None = None


class CounterpartyOut(BaseModel):
    id: str
    name: str
    jurisdiction: str | None = None
    address: str | None = None
    contact_email: str | None = None
    active: bool
    source: str
    external_ref: str | None = None
    created_at: datetime
    updated_at: datetime


@router.get("/legal-entities", response_model=list[EntityOut])
def list_entities(q: str = "", include_inactive: bool = False, db: Session = Depends(get_db),
                  current_user=Depends(_FILER)):
    return service.search(db, actor=current_user, kind="legal_entity", q=q,
                          include_inactive=include_inactive, limit=200)


@router.post("/legal-entities", response_model=EntityOut, status_code=201)
def create_entity(payload: EntityIn, db: Session = Depends(get_db), current_user=Depends(_ADMIN)):
    return service.create(db, actor=current_user, kind="legal_entity", payload=payload)


@router.patch("/legal-entities/{entity_id}", response_model=EntityOut)
def update_entity(entity_id: str, payload: EntityPatch, db: Session = Depends(get_db),
                  current_user=Depends(_ADMIN)):
    return service.update(db, actor=current_user, kind="legal_entity", record_id=entity_id, payload=payload)


@router.get("/counterparties", response_model=list[CounterpartyOut])
def list_counterparties(q: str = "", include_inactive: bool = False, limit: int = 20,
                        db: Session = Depends(get_db), current_user=Depends(_FILER)):
    return service.search(db, actor=current_user, kind="counterparty", q=q,
                          include_inactive=include_inactive, limit=limit)


@router.post("/counterparties", response_model=CounterpartyOut, status_code=201)
def create_counterparty(payload: CounterpartyIn, db: Session = Depends(get_db),
                        current_user=Depends(_FILER)):
    return service.create(db, actor=current_user, kind="counterparty", payload=payload)


@router.patch("/counterparties/{counterparty_id}", response_model=CounterpartyOut)
def update_counterparty(counterparty_id: str, payload: CounterpartyPatch, db: Session = Depends(get_db),
                        current_user=Depends(_ADMIN)):
    return service.update(db, actor=current_user, kind="counterparty", record_id=counterparty_id,
                          payload=payload)
