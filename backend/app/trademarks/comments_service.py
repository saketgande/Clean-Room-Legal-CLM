from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.audit import write_audit_log
from app.trademarks.comments_models import TrademarkComment
from app.trademarks.models import Trademark


class TrademarkCommentService:
    """A flat, append-only discussion thread per trademark.

    Mirrors ContractCommentService's shape (app/contracts/comments_service.py)
    minus the negotiation-specific bits (visibility, anchor, resolve). No
    delete method exists here, deliberately — "entire timeline preserved"
    was read literally, unlike contract comments which allow a soft-delete.
    """

    def __init__(self, db: Session):
        self.db = db

    def _name_map(self, org_id: str) -> dict[str, str]:
        return {
            u.id: (u.full_name or u.email)
            for u in self.db.scalars(select(User).where(User.org_id == org_id)).all()
        }

    @staticmethod
    def _serialize(comment: TrademarkComment, *, names: dict[str, str]) -> dict:
        return {
            "id": comment.id,
            "trademark_id": comment.trademark_id,
            "parent_comment_id": comment.parent_comment_id,
            "author_user_id": comment.author_user_id,
            "author_name": names.get(comment.author_user_id or "") or "Unknown",
            "body": comment.body,
            "created_at": comment.created_at,
        }

    def _get_trademark_for_user(self, *, trademark_id: str, user: User) -> Trademark:
        trademark = self.db.get(Trademark, trademark_id)
        if trademark is None or trademark.org_id != user.org_id or trademark.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Trademark not found")
        return trademark

    def list_comments(self, *, trademark_id: str, user: User) -> list[dict]:
        trademark = self._get_trademark_for_user(trademark_id=trademark_id, user=user)
        rows = self.db.scalars(
            select(TrademarkComment)
            .where(
                TrademarkComment.org_id == trademark.org_id,
                TrademarkComment.trademark_id == trademark.id,
                TrademarkComment.deleted_at.is_(None),
            )
            .order_by(TrademarkComment.created_at.asc())
        ).all()
        names = self._name_map(trademark.org_id)
        return [self._serialize(r, names=names) for r in rows]

    def create_comment(
        self,
        *,
        trademark_id: str,
        user: User,
        body: str,
        parent_comment_id: str | None = None,
        request_id: str | None = None,
    ) -> dict:
        db = self.db
        trademark = self._get_trademark_for_user(trademark_id=trademark_id, user=user)
        if not body or not body.strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Comment body is required")

        # Flatten to one level server-side: a reply to a reply re-parents to
        # that reply's own parent, regardless of what the client sends.
        if parent_comment_id:
            parent = db.get(TrademarkComment, parent_comment_id)
            if (
                parent is None
                or parent.org_id != trademark.org_id
                or parent.trademark_id != trademark.id
                or parent.deleted_at is not None
            ):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent comment not found")
            if parent.parent_comment_id is not None:
                parent_comment_id = parent.parent_comment_id

        comment = TrademarkComment(
            org_id=trademark.org_id,
            trademark_id=trademark.id,
            parent_comment_id=parent_comment_id,
            author_user_id=user.id,
            body=body.strip(),
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(comment)
        db.flush()

        write_audit_log(
            db,
            action="trademark.comment.created",
            resource_type="trademark_comment",
            resource_id=comment.id,
            org_id=trademark.org_id,
            actor_user_id=user.id,
            request_id=request_id,
            after={"trademark_id": trademark.id},
        )
        db.commit()
        db.refresh(comment)
        return self._serialize(comment, names=self._name_map(trademark.org_id))

    def get_comment_counts(self, *, org_id: str) -> dict[str, int]:
        """One bulk {trademark_id: count} query — drives the chat-icon badge
        on every row in My Trademarks without an N+1 request per row."""
        rows = self.db.execute(
            select(TrademarkComment.trademark_id, func.count(TrademarkComment.id))
            .where(TrademarkComment.org_id == org_id, TrademarkComment.deleted_at.is_(None))
            .group_by(TrademarkComment.trademark_id)
        ).all()
        return {trademark_id: count for trademark_id, count in rows}
