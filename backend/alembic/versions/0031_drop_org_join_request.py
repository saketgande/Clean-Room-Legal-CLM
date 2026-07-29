"""Drop org_join_request.

Dead code found in a production-readiness review: this was the old
cross-org "request to join" flow. Self-registration has long since been
replaced by direct PENDING_APPROVAL users vetted on the Users & Access
queue (see register_user in auth/service.py) — nothing has constructed an
OrgJoinRequest row since, and the table is empty in every environment.
"""
from alembic import op

revision = "0031_drop_org_join_request"
down_revision = "0030_auth_hash_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS org_join_request")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS org_join_request (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) REFERENCES organization(id),
            email VARCHAR(320) NOT NULL,
            full_name VARCHAR(255) NOT NULL,
            requested_domain VARCHAR(255),
            message TEXT,
            status VARCHAR(40) NOT NULL DEFAULT 'pending',
            decided_by_user_id VARCHAR(36) REFERENCES "user"(id),
            decision_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_join_request_org_id ON org_join_request (org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_join_request_email ON org_join_request (email)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_join_request_requested_domain "
        "ON org_join_request (requested_domain)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_join_request_status ON org_join_request (status)")
