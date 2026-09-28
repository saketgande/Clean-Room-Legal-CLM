"""Docstudio: documents, versions, clauses, annotations, events.

A rebuild of document representation, alongside the existing pipeline rather
than inside it. The tables it replaces anchor every clause, citation and comment
to a character offset into one mutable string, so each new version silently
invalidates all of them. These carry three anchors per annotation instead (see
app/docstudio/anchoring.py) and treat the flat text as derived.

Nothing here touches or reads the contract_* tables. The old pipeline keeps
running until docstudio is ahead of it.

Revision ID: 0045_docstudio_tables
Revises: 0044_intake_type_sla_hours
"""

import sqlalchemy as sa

from alembic import op

revision = "0045_docstudio_tables"
down_revision = "0044_intake_type_sla_hours"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ds_document",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False, index=True),
        # Deliberately not a foreign key: docstudio reads files through
        # FileSource and knows nothing else about the caller's domain.
        sa.Column("external_ref", sa.String(128), nullable=True, index=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("current_version_id", sa.String(36), nullable=True),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "ds_version",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False, index=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("ds_document.id"), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False, index=True),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(500), nullable=True),
        sa.Column("byte_size", sa.Integer, nullable=False),
        # Which parser produced the structure. Re-parsing with a different
        # version yields different offsets, so a version that does not record
        # how it was parsed cannot be reproduced or audited.
        sa.Column("parser_name", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("flat_text", sa.Text, nullable=False, server_default=""),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column("parse_warnings", sa.JSON, nullable=True),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "version_number", name="uq_ds_version_document_number"),
    )
    op.create_index("ix_ds_version_document", "ds_version", ["document_id", "version_number"])

    op.create_table(
        "ds_clause",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False, index=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("ds_version.id"), nullable=False),
        # Stable identity carried across versions. NOT a content hash: a hash
        # changes the moment a clause is reworded, which is exactly when its
        # comments most need to follow it.
        sa.Column("clause_id", sa.String(64), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("parent_clause_id", sa.String(64), nullable=True),
        sa.Column("number_label", sa.String(64), nullable=True),
        sa.Column("level", sa.Integer, nullable=False, server_default="1"),
        sa.Column("clause_type", sa.String(32), nullable=False, server_default="clause"),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("char_start", sa.Integer, nullable=False),
        sa.Column("char_end", sa.Integer, nullable=False),
        # Populated at parse time for PDFs. The parser returns these for free,
        # and not storing them is why a citation could never be highlighted on
        # the page it came from.
        sa.Column("page_number", sa.Integer, nullable=True),
        sa.Column("bbox", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ds_clause_version_seq", "ds_clause", ["version_id", "seq"])
    op.create_index("ix_ds_clause_identity", "ds_clause", ["clause_id"])

    op.create_table(
        "ds_annotation",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False, index=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("ds_document.id"), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(24), nullable=False),
        # The three W3C Web Annotation selectors: structural, quote-with-context,
        # and position. One anchor alone is what breaks on every version.
        sa.Column("anchor_clause_id", sa.String(64), nullable=True),
        sa.Column("anchor_quote_exact", sa.Text, nullable=True),
        sa.Column("anchor_quote_prefix", sa.Text, nullable=True),
        sa.Column("anchor_quote_suffix", sa.Text, nullable=True),
        sa.Column("anchor_start", sa.Integer, nullable=True),
        sa.Column("anchor_end", sa.Integer, nullable=True),
        sa.Column("anchor_state", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("anchor_rung", sa.Integer, nullable=True),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("proposed_text", sa.Text, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("parent_annotation_id", sa.String(36), nullable=True),
        sa.Column("author_kind", sa.String(16), nullable=False, server_default="user"),
        sa.Column("author_user_id", sa.String(36), nullable=True),
        sa.Column("author_name", sa.String(255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ds_annotation_document_state", "ds_annotation", ["document_id", "anchor_state"]
    )
    op.create_index("ix_ds_annotation_clause", "ds_annotation", ["anchor_clause_id"])

    op.create_table(
        "ds_event",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False, index=True),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("details", sa.JSON, nullable=True),
        sa.Column("actor_user_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ds_event_document", "ds_event", ["document_id", "created_at"])


def downgrade() -> None:
    op.drop_table("ds_event")
    op.drop_table("ds_annotation")
    op.drop_table("ds_clause")
    op.drop_table("ds_version")
    op.drop_table("ds_document")
