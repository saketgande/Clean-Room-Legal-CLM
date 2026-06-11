"""drop the unused contract_activity table

The contract activity timeline is served by resource_timeline_event;
ContractActivity was declared but never written or read. Remove the
dead table. Reversible: downgrade recreates it exactly as in 0001.
"""

from alembic import op

revision = "0007_drop_dead_contract_activity"
down_revision = "0006_audit_hash_chain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS contract_activity CASCADE")


def downgrade() -> None:
    op.execute("""
    CREATE TABLE contract_activity (
        contract_id VARCHAR(36) NOT NULL,
        actor_user_id VARCHAR(36),
        activity_type VARCHAR(120) NOT NULL,
        title VARCHAR(255) NOT NULL,
        details JSON,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_contract_activity PRIMARY KEY (id),
        CONSTRAINT fk_contract_activity_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_contract_activity_actor_user_id_user FOREIGN KEY(actor_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("CREATE INDEX ix_contract_activity_activity_type ON contract_activity (activity_type)")
    op.execute("CREATE INDEX ix_contract_activity_contract_id ON contract_activity (contract_id)")
    op.execute("CREATE INDEX ix_contract_activity_org_id ON contract_activity (org_id)")
