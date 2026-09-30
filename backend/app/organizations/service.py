from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.organizations.models import Organization


class OrganizationsService:
    def __init__(self, db: Session):
        self.db = db

    def get_current_organization(self, *, org_id: str) -> Organization:
        org = self.db.get(Organization, org_id)
        if org is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
        return org

    def update_organization(self, *, org_id: str, payload) -> Organization:
        db = self.db
        org = self.get_current_organization(org_id=org_id)
        if payload.name is not None:
            org.name = payload.name
        if payload.allowed_domains is not None:
            org.allowed_domains = [domain.lower() for domain in payload.allowed_domains]
        if payload.default_role_name is not None:
            org.default_role_name = payload.default_role_name
        db.commit()
        db.refresh(org)
        return org
