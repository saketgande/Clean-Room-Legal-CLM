import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, String, create_engine
from sqlalchemy.orm import DeclarativeBase, declared_attr, sessionmaker

from app.core.config import settings


def new_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class IdMixin:
    id = Column(String(36), primary_key=True, default=new_uuid)


class TimestampMixin:
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class OrgScopedMixin:
    org_id = Column(String(36), index=True, nullable=False)


class ActorTrackedMixin:
    created_by_user_id = Column(String(36), nullable=True)
    updated_by_user_id = Column(String(36), nullable=True)


class SoftDeleteMixin:
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id = Column(String(36), nullable=True)
    legal_hold = Column(Boolean, nullable=False, default=False)


class TableNameMixin:
    @declared_attr.directive
    def __tablename__(cls) -> str:
        name = cls.__name__
        chars: list[str] = []
        for index, char in enumerate(name):
            if char.isupper() and index > 0:
                chars.append("_")
            chars.append(char.lower())
        return "".join(chars)


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
