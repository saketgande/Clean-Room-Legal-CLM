"""Docstudio: structure evidence, provenance, page regions, OCR cache.

The structure layer was rebuilt around evidence strength: the OCR provider's
own block labels, then the numbering, then layout, then the AI for what is left.
These columns carry what that needs to persist — the number read structurally,
who decided each parent, every page region a clause occupies — plus a cache so a
scan is OCR'd once per file, not once per parse.

Revision ID: 0046_docstudio_structure
Revises: 0045_docstudio_tables
"""

import sqlalchemy as sa

from alembic import op

revision = "0046_docstudio_structure"
down_revision = "0045_docstudio_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ds_clause", sa.Column("number_scheme", sa.String(24), nullable=True))
    op.add_column("ds_clause", sa.Column("number_path", sa.JSON(), nullable=True))
    op.add_column("ds_clause", sa.Column("structure_source", sa.String(24), nullable=True))
    op.add_column("ds_clause", sa.Column("source_regions", sa.JSON(), nullable=True))
    op.add_column("ds_version", sa.Column("artifacts", sa.JSON(), nullable=True))
    op.create_table(
        "ds_ocr_result",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("blocks", sa.JSON(), nullable=True),
        sa.Column("quality", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ds_ocr_result_sha", "ds_ocr_result", ["sha256", "provider"])


def downgrade() -> None:
    op.drop_index("ix_ds_ocr_result_sha", table_name="ds_ocr_result")
    op.drop_table("ds_ocr_result")
    op.drop_column("ds_version", "artifacts")
    op.drop_column("ds_clause", "source_regions")
    op.drop_column("ds_clause", "structure_source")
    op.drop_column("ds_clause", "number_path")
    op.drop_column("ds_clause", "number_scheme")
