"""Edits to the drafting templates, one row per saved version.

The default text of each template ships with the code
(``app/drafting_templates/defaults/<key>.txt``); a row here is an org's own
version of it. The newest row for a key is the one drafting uses. ``body`` NULL
means "back to the default", so a reset keeps following the shipped text as it
improves instead of freezing a copy of it. Rows are never edited or deleted —
every earlier wording stays readable.
"""

from sqlalchemy import Column, Integer, String, Text, UniqueConstraint

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)


class DraftingTemplateVersion(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    key = Column(String(40), nullable=False, index=True)  # nda | msa | consultancy | sow | vendor | saas | dpa
    version = Column(Integer, nullable=False)
    body = Column(Text, nullable=True)  # NULL = use the shipped default
    note = Column(String(300), nullable=True)

    __table_args__ = (UniqueConstraint("org_id", "key", "version", name="uq_drafting_template_version"),)
