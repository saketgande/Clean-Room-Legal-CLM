from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    SoftDeleteMixin,
    TableNameMixin,
    TimestampMixin,
)


class ContractComment(
    TableNameMixin,
    IdMixin,
    OrgScopedMixin,
    ActorTrackedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    Base,
):
    """A comment on a contract during review/negotiation.

    visibility="internal" → team-only (negotiation strategy, hidden from the
    counterparty); visibility="shared" → may be surfaced to the counterparty in
    the external-share view. author_kind distinguishes an internal user from a
    counterparty who comments via a share token (no account)."""

    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), nullable=True)
    parent_comment_id = Column(String(36), ForeignKey("contract_comment.id"), nullable=True)

    visibility = Column(String(20), index=True, nullable=False, default="internal")
    author_kind = Column(String(20), nullable=False, default="user")  # user | counterparty
    author_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    author_label = Column(String(255), nullable=True)  # for counterparty authors

    body = Column(Text, nullable=False)
    anchor = Column(JSON, nullable=True)  # optional clause/text anchor
    mentioned_user_ids = Column(JSON, nullable=False, default=list)

    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
