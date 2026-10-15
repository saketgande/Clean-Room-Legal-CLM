"""Edits to the drafting templates, one row per saved version.

The default text of each template ships with the code
(``app/drafting_templates/defaults/<key>.txt``); a row here is an org's own
version of it. The newest row for a key is the one drafting uses. ``body`` NULL
means "back to the default", so a reset keeps following the shipped text as it
improves instead of freezing a copy of it. Rows are never edited or deleted —
every earlier wording stays readable.

``DraftingTemplateCheck`` is one run of the template-against-playbook check
(who asked, which wording and playbook version it read, what it found).
``DraftingTemplateType`` holds a type's facts that can change per org: for the
shipped types a row appears only once the template is pinned to a playbook.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

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
    # [{check_id, finding_index, clause_type, severity}]: the check findings this
    # version was saved to fix (FR-25).
    applied_findings = Column(JSON, nullable=True)
    restored_from_version = Column(Integer, nullable=True)  # set when this row is a restore (FR-58)

    __table_args__ = (UniqueConstraint("org_id", "key", "version", name="uq_drafting_template_version"),)


class DraftingTemplateCheck(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    template_key = Column(String(40), nullable=False)
    template_version = Column(Integer, nullable=False)  # 0 = shipped wording, never saved
    wording_sha256 = Column(String(64), nullable=False)  # hash of the wording that was checked
    unsaved = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    unsaved_body = Column(Text, nullable=True)  # only when `unsaved`
    # saved | uploaded | restored | reset | type_created | type_reactivated |
    # playbook_published | playbook_changed | manual | check_all | retry
    trigger = Column(String(30), nullable=False)
    trigger_detail = Column(JSON, nullable=True)
    # queued | running | completed | failed | superseded
    status = Column(String(20), nullable=False, default="queued", server_default="queued")
    playbook_id = Column(String(36), ForeignKey("playbook.id"), index=True, nullable=True)
    playbook_version_id = Column(String(36), ForeignKey("playbook_version.id"), index=True, nullable=True)
    # Display snapshots: they survive a rename or delete of the playbook.
    playbook_name = Column(String(255), nullable=True)
    playbook_version_number = Column(Integer, nullable=True)
    rule_count = Column(Integer, nullable=True)
    findings = Column(JSON, nullable=True)
    summary = Column(Text, nullable=True)
    text_was_truncated = Column(Boolean, nullable=True)
    text_length = Column(Integer, nullable=True)
    read_length = Column(Integer, nullable=True)
    # ai_unavailable | cost_cap | no_rules | no_playbook | unfillable | wording_changed | interrupted
    error_code = Column(String(40), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_drafting_template_check_org_key_created", "org_id", "template_key", "created_at"),
        Index("ix_drafting_template_check_status", "status"),
        # One running and one queued check per template: a second request joins the queued one (FR-15).
        Index(
            "uq_drafting_template_check_one_running",
            "org_id",
            "template_key",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        Index(
            "uq_drafting_template_check_one_queued",
            "org_id",
            "template_key",
            unique=True,
            postgresql_where=text("status = 'queued'"),
        ),
    )


class DraftingTemplateType(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    key = Column(String(40), nullable=False)
    name = Column(String(120), nullable=False)
    label = Column(String(120), nullable=False)  # draft title prefix
    form_key = Column(String(40), nullable=False)  # new_agreement | sow | dpa
    agreement_kind = Column(String(80), nullable=True)  # NULL for sow / dpa
    shipped = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    status = Column(String(20), nullable=False, default="active", server_default="active")  # active | retired
    # The playbook that reviews this type's contracts. NULL = not pinned yet.
    playbook_id = Column(String(36), ForeignKey("playbook.id"), index=True, nullable=True)

    __table_args__ = (
        UniqueConstraint("org_id", "key", name="uq_drafting_template_type_key"),
        # One active type per form + kind (FR-49) and one active type per name, any case (FR-50).
        Index(
            "uq_drafting_template_type_binding",
            "org_id",
            "form_key",
            text("coalesce(agreement_kind, '')"),
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_drafting_template_type_name",
            "org_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )
