from sqlalchemy import Column, ForeignKey, String, Text

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    SoftDeleteMixin,
    TableNameMixin,
    TimestampMixin,
)


class TrademarkComment(
    TableNameMixin,
    IdMixin,
    OrgScopedMixin,
    ActorTrackedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    Base,
):
    """A comment or reply on a trademark's discussion thread.

    Mirrors ContractComment's shape (app/contracts/comments_models.py) minus
    the fields that are specific to contract negotiation (visibility,
    anchor, resolved_at) — trademark discussion has no counterparty-share
    concept and no resolution workflow, just a flat append-only thread.

    parent_comment_id always points at a top-level comment, never at
    another reply — enforced in TrademarkCommentService.create_comment, not
    just assumed here, so a reply-to-a-reply can never nest past one level
    regardless of what a client sends."""

    trademark_id = Column(String(36), ForeignKey("trademark.id", ondelete="CASCADE"), index=True, nullable=False)
    parent_comment_id = Column(String(36), ForeignKey("trademark_comment.id"), nullable=True)

    author_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    body = Column(Text, nullable=False)
