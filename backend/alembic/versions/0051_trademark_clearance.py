"""Trademark clearance: saved search runs, decision and filing record.

Additive only. ``trademark_search_run`` keeps every similarity search so the
preliminary and detailed searches of a clearance case can be shown, risk-rated
and exported. The new ``trademark`` columns hold the clearance decision and the
filing record (application number, form, portal reference, docket number); the
filing date reuses the existing ``filed_on``.

Revision ID: 0051_trademark_clearance
Revises: 0050_extract_only_ingest
"""

import sqlalchemy as sa

from alembic import op

revision = "0051_trademark_clearance"
down_revision = "0050_extract_only_ingest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trademark_search_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("trademark_id", sa.String(36), sa.ForeignKey("trademark.id", ondelete="CASCADE"), nullable=True),
        sa.Column("query_id", sa.String(40), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=True),
        sa.Column("query", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trademark_search_run_org_id", "trademark_search_run", ["org_id"])
    op.create_index("ix_trademark_search_run_trademark_id", "trademark_search_run", ["trademark_id"])
    op.create_index("ix_trademark_search_run_query_id", "trademark_search_run", ["query_id"])

    op.add_column("trademark", sa.Column("decision", sa.String(20), nullable=True))
    op.add_column("trademark", sa.Column("decision_conditions", sa.Text(), nullable=True))
    op.add_column("trademark", sa.Column("decided_by_user_id", sa.String(36), nullable=True))
    op.add_column("trademark", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("trademark", sa.Column("application_number", sa.String(120), nullable=True))
    op.add_column("trademark", sa.Column("filing_form", sa.String(60), nullable=True))
    op.add_column("trademark", sa.Column("portal_reference", sa.String(120), nullable=True))
    op.add_column("trademark", sa.Column("docket_number", sa.String(120), nullable=True))


def downgrade() -> None:
    for column in (
        "docket_number",
        "portal_reference",
        "filing_form",
        "application_number",
        "decided_at",
        "decided_by_user_id",
        "decision_conditions",
        "decision",
    ):
        op.drop_column("trademark", column)
    op.drop_table("trademark_search_run")
