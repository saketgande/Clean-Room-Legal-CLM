"""Merge heads: trademark line + notice/matters line.

The drl rebase brought together two independent head chains that both fork
off 0031_drop_org_join_request: 0032_merge_heads (which carries the trademark
module via 0029_trademarks) and 0032_notice_register -> ... ->
0040_document_elements (notices, workflow rename, projects->matters). This is
a no-op merge revision to bring the migration graph back to a single head.

Revision ID: 0041_merge_heads
Revises: 0040_document_elements, 0032_merge_heads
"""

revision = "0041_merge_heads"
down_revision = ("0040_document_elements", "0032_merge_heads")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
