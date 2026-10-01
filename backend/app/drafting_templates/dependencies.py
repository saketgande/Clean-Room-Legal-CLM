from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.drafting_templates.service import DraftingTemplateService


def get_drafting_template_service(db: Session = Depends(get_db)) -> DraftingTemplateService:
    return DraftingTemplateService(db)
