from sqlalchemy import Boolean, Column, JSON, String

from app.core.database import Base, IdMixin, TableNameMixin, TimestampMixin


class Organization(TableNameMixin, IdMixin, TimestampMixin, Base):
    name = Column(String(255), nullable=False)
    slug = Column(String(120), unique=True, index=True, nullable=False)
    allowed_domains = Column(JSON, nullable=False, default=list)
    default_role_name = Column(String(120), nullable=False, default="member")
    setup_complete = Column(Boolean, nullable=False, default=False)
    settings = Column(JSON, nullable=False, default=dict)
