"""initial contract-first platform schema

Revision ID: 0001_initial_contract_platform
Revises:
Create Date: 2026-05-14
"""

from alembic import op

from app.core.database import Base
import app.models  # noqa: F401

revision = "0001_initial_contract_platform"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
    op.execute("DROP EXTENSION IF EXISTS vector")
