"""Drop the sanctions list: sanctions screening was removed from intake.

It matched names only, against one US list, and a hit blocked nothing, so a
"Clear" looked like a compliance check that was not really happening.
Sanctions checks belong with a dedicated screening provider. Downgrade
recreates the empty table; the list itself was refetched daily, so nothing
unrecoverable is lost.
"""

from alembic import op

revision = "0052_drop_sanctions_list"
down_revision = "0051_contract_party_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sanctions_list_entry")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sanctions_list_entry (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            source VARCHAR(40) NOT NULL,
            source_ref VARCHAR(60) NOT NULL,
            name VARCHAR(400) NOT NULL,
            name_normalized VARCHAR(400) NOT NULL,
            programs VARCHAR(400),
            refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_sanctions_source_ref
            ON sanctions_list_entry (source, source_ref);
        CREATE INDEX IF NOT EXISTS ix_sanctions_name_norm
            ON sanctions_list_entry (name_normalized);
        """
    )
