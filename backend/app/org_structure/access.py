"""Org-scoped fetch/guard helpers for the org_structure domain.

Every helper here filters on the actor's ``org_id`` (AC-18) — these are thin,
reusable lookups consumed by ``service.py`` / the shared resolver
(``app.core.org_access``), not the resolver's ancestry/expiry/delegation logic
itself, which lives exclusively in ``app.core.org_access`` per FR-11.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.org_structure.models import OrgUnit


def get_org_unit_or_404(
    db: Session, org_id: str, org_unit_id: str, *, include_deleted: bool = False
) -> OrgUnit:
    """Fetch an ``OrgUnit`` scoped to ``org_id``. 404s when missing, in
    another org, or (unless ``include_deleted``) soft-deleted.
    """
    stmt = select(OrgUnit).where(OrgUnit.id == org_unit_id, OrgUnit.org_id == org_id)
    if not include_deleted:
        stmt = stmt.where(OrgUnit.deleted_at.is_(None))
    org_unit = db.execute(stmt).scalar_one_or_none()
    if org_unit is None:
        raise HTTPException(404, "Org unit not found")
    return org_unit


def get_org_root(db: Session, org_id: str) -> OrgUnit:
    """Fetch the single non-deleted root (``parent_id IS NULL``) org unit for
    ``org_id``. 404s if the org has no root (should not happen post-migration).
    """
    stmt = select(OrgUnit).where(
        OrgUnit.org_id == org_id,
        OrgUnit.parent_id.is_(None),
        OrgUnit.deleted_at.is_(None),
    )
    root = db.execute(stmt).scalar_one_or_none()
    if root is None:
        raise HTTPException(404, "Org root unit not found")
    return root
