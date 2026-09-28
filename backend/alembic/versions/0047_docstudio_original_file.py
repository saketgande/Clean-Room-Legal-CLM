"""Keep the bytes a version was read from, so the file itself can be shown.

Phase 1 stored what it read — text, clauses, geometry — but not the file. A
document view has to draw the real page: a scan's signatures and stamps are not
in the text, and a reader must be able to check the text against the original.
Nullable, because versions ingested before this have no stored bytes; the view
says so rather than pretending.
"""

import sqlalchemy as sa

from alembic import op

revision = "0047_docstudio_original_file"
down_revision = "0046_docstudio_structure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ds_version", sa.Column("storage_key", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("ds_version", "storage_key")
