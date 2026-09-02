"""Legal-notice register.

A Notice is a tracked legal communication with a counterparty, in either
direction. Kept separate from IntakeRequest on purpose: a *received* notice has
no internal requester, and `response_due_date` is an externally-imposed
statutory deadline with legal consequence — not an internal SLA in hours that
can be paused. Deadline posture is derived on read (see service.deadline_posture)
rather than stored, so it can never go stale against the clock.

Enum-ish fields are validated VARCHARs, never DB enums — same convention as the
intake models, so adding a notice type is a code change, not a migration.
"""
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)

# Direction of travel. 'received' is the common case and the default.
DIRECTIONS = ("received", "sent")

NOTICE_TYPES = (
    "demand",
    "cease_and_desist",
    "breach",
    "termination",
    "statutory",
    "recovery",
    "infringement",
    "other",
)

# draft   — outbound notice being prepared, not yet sent
# open    — live; awaiting our response (received) or theirs (sent)
# responded — we've replied; kept distinct from closed so "replied but still
#             running" is visible on the board
# escalated — handed to litigation/dispute
# closed  — terminal
STATUSES = ("draft", "open", "responded", "escalated", "closed")
TERMINAL_STATUSES = ("closed",)

PRIORITIES = ("Critical", "High", "Medium", "Low")

EVENT_KINDS = (
    "filed",
    "assigned",
    "status_changed",
    "responded",
    "escalated",
    "closed",
    "note",
    "reminded",
)

# Reminder milestones, in escalating order. The daily sweep only notifies when
# a notice's current milestone is MORE urgent than the last one recorded on it,
# which is what makes the job idempotent without a per-reminder table.
REMINDER_STAGES = ("t7", "t3", "due", "overdue")
REMINDER_STAGE_RANK = {None: 0, "": 0, "t7": 1, "t3": 2, "due": 3, "overdue": 4}


class Notice(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """One row per legal notice. `closed_at` stamps once on entering a terminal
    status so response-time evidence can't be rewritten by `updated_at`."""

    ref = Column(String(20), nullable=False)  # 'NOT-1001' from notice_ref_seq
    direction = Column(String(10), nullable=False, default="received")
    notice_type = Column(String(40), nullable=False, default="other")
    subject = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default="")

    counterparty_name = Column(String(200), nullable=False)
    counterparty_ref = Column(String(120), nullable=True)  # their reference number
    contract_id = Column(
        String(36), ForeignKey("contract.id", ondelete="SET NULL"), nullable=True
    )

    notice_date = Column(Date, nullable=True)  # date printed on the notice
    received_at = Column(Date, nullable=True)  # date it actually reached us
    # The statutory clock. Nullable because some notices are informational.
    response_due_date = Column(Date, nullable=True)

    status = Column(String(20), nullable=False, default="open")
    priority = Column(String(20), nullable=False, default="Medium")
    owner_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)

    responded_at = Column(DateTime(timezone=True), nullable=True)
    response_summary = Column(Text, nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    # Most urgent reminder milestone already sent for this notice (see
    # REMINDER_STAGES). Never reset on its own — answering or closing the notice
    # takes it out of the sweep's query entirely.
    last_reminder_stage = Column(String(10), nullable=True)
    last_reminder_at = Column(DateTime(timezone=True), nullable=True)

    # AI-drafted reply, held here until a human edits and sends it. Kept apart
    # from response_summary: this is a proposal, that is the record of what was
    # actually sent.
    draft_response = Column(Text, nullable=True)
    draft_response_at = Column(DateTime(timezone=True), nullable=True)

    # Escalation opens a linked intake ticket rather than duplicating routing,
    # SLA and the approval ladder here. See migration 0035.
    escalated_intake_request_id = Column(
        String(36), ForeignKey("intake_request.id", ondelete="SET NULL"), nullable=True
    )

    events = relationship(
        "NoticeEvent",
        back_populates="notice",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="NoticeEvent.created_at",
    )
    documents = relationship(
        "NoticeDocument",
        back_populates="notice",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="NoticeDocument.created_at",
    )


class NoticeEvent(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """Append-only timeline entry. Never updated in place — a correction is a
    new event, so the trail stays honest."""

    notice_id = Column(
        String(36), ForeignKey("notice.id", ondelete="CASCADE"), nullable=False
    )
    kind = Column(String(30), nullable=False)
    body = Column(Text, nullable=True)
    actor_user_id = Column(String(36), nullable=True)

    notice = relationship("Notice", back_populates="events")


class NoticeDocument(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base
):
    """An attachment on a notice. Like IntakeDocument, we persist the extracted
    text rather than the bytes — the text is what the extraction agent reads and
    what a future search would index, and a scanned two-page notice doesn't
    justify a second blob-storage lifecycle."""

    notice_id = Column(
        String(36), ForeignKey("notice.id", ondelete="CASCADE"), nullable=False
    )
    filename = Column(String(300), nullable=False)
    mime_type = Column(String(120), nullable=False)
    size_bytes = Column(Integer, nullable=False, default=0)
    extracted_text = Column(Text, nullable=True)
    extraction_quality = Column(Float, nullable=True)

    notice = relationship("Notice", back_populates="documents")
