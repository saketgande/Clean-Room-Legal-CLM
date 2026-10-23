"""Register the agreement-wizard forms as real request types.

The nine agreement forms (new agreement, SoW, DPA, …) lived only in the
frontend, so wizard requests were stored with no request type and the server
validated nothing. ``form_key`` marks the request-type row that backs each
form; the rows themselves are created on demand by
``app.intake.agreement_forms.ensure_agreement_types`` so every organisation gets
them without a data migration.
"""

import sqlalchemy as sa

from alembic import op

revision = "0048_intake_agreement_form_types"
down_revision = "0047_docstudio_original_file"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("intake_request_type", sa.Column("form_key", sa.String(60), nullable=True))
    op.create_index(
        "uq_intake_request_type_org_form_key", "intake_request_type", ["org_id", "form_key"],
        unique=True, postgresql_where=sa.text("form_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_intake_request_type_org_form_key", table_name="intake_request_type")
    op.drop_column("intake_request_type", "form_key")
