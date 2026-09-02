"""Structured document model: contract_document_element.

The extraction step already recovers a structured document — headings, clauses,
paragraphs, tables — each with a type, per-element confidence, and page. Today
that structure is flattened into one lossy `contract_text_snapshot.text` string
and everything downstream reads the blob. This migration adds the table that
lets us keep the structure (one row per element) so readers can move to
clause-scoped access instead of the whole document.

Phase 0 of the migration path: purely additive. Nothing reads the new table
yet, and existing snapshots default to structure_status='flat_only', so this
deploys with no behaviour change and rolls back cleanly.
"""
import sqlalchemy as sa
from alembic import op

revision = "0040_document_elements"
down_revision = "0039_projects_to_matters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contract_document_element",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("contract_id", sa.String(36), sa.ForeignKey("contract.id"), nullable=False),
        sa.Column("contract_version_id", sa.String(36), sa.ForeignKey("contract_version.id"), nullable=False),
        sa.Column("text_snapshot_id", sa.String(36), sa.ForeignKey("contract_text_snapshot.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("parent_id", sa.String(36), sa.ForeignKey("contract_document_element.id"), nullable=True),
        sa.Column("element_type", sa.String(40), nullable=False, server_default="paragraph"),
        sa.Column("level", sa.Integer, nullable=False, server_default="0"),
        sa.Column("number_label", sa.String(40), nullable=True),
        sa.Column("block_id", sa.String(40), nullable=False),
        sa.Column("text", sa.Text, nullable=False, server_default=""),
        sa.Column("html", sa.Text, nullable=True),
        sa.Column("page_number", sa.Integer, nullable=True),
        sa.Column("char_start", sa.Integer, nullable=True),
        sa.Column("char_end", sa.Integer, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("bbox", sa.JSON, nullable=True),
        sa.Column("source", sa.String(30), nullable=False, server_default="ocr"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_contract_document_element_org_id", "contract_document_element", ["org_id"])
    op.create_index("ix_contract_document_element_contract_id", "contract_document_element", ["contract_id"])
    op.create_index("ix_contract_document_element_text_snapshot_id", "contract_document_element", ["text_snapshot_id"])
    op.create_index("ix_element_block", "contract_document_element", ["block_id"])
    op.create_index("ix_element_version_seq", "contract_document_element", ["contract_version_id", "seq"])
    op.create_unique_constraint(
        "uq_element_snapshot_seq", "contract_document_element", ["text_snapshot_id", "seq"]
    )

    # Existing snapshots are text-only until backfilled (Phase 2).
    op.add_column(
        "contract_text_snapshot",
        sa.Column("structure_status", sa.String(20), nullable=False, server_default="flat_only"),
    )
    op.add_column(
        "contract_text_snapshot",
        sa.Column("element_count", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("contract_text_snapshot", "element_count")
    op.drop_column("contract_text_snapshot", "structure_status")
    op.drop_table("contract_document_element")
