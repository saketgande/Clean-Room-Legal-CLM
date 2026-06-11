"""access-token jti revocation table

Adds a per-jti revocation row so logout / password reset / forced re-auth
can cut off in-flight access tokens immediately, instead of waiting for
the token's natural ``exp``.

Revision ID: 0008_revoked_access_token
Revises: 0007_drop_dead_contract_activity
Create Date: 2026-05-19
"""

import sqlalchemy as sa
from alembic import op


revision = "0008_revoked_access_token"
down_revision = "0007_drop_dead_contract_activity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revoked_access_token",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("user.id", name="fk_revoked_access_token_user_id_user"),
            nullable=True,
        ),
        sa.Column("jti", sa.String(length=80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_revoked_access_token_jti",
        "revoked_access_token",
        ["jti"],
        unique=True,
    )
    op.create_index(
        "ix_revoked_access_token_user_id",
        "revoked_access_token",
        ["user_id"],
    )
    op.create_index(
        "ix_revoked_access_token_org_id",
        "revoked_access_token",
        ["org_id"],
    )
    op.create_index(
        "ix_revoked_access_token_expires_at",
        "revoked_access_token",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_revoked_access_token_expires_at", table_name="revoked_access_token")
    op.drop_index("ix_revoked_access_token_org_id", table_name="revoked_access_token")
    op.drop_index("ix_revoked_access_token_user_id", table_name="revoked_access_token")
    op.drop_index("ix_revoked_access_token_jti", table_name="revoked_access_token")
    op.drop_table("revoked_access_token")
