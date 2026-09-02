"""Notice management: the legal-notice register.

A notice is a tracked legal communication with a counterparty — a demand,
cease-and-desist, breach or statutory notice — in either direction. It is
deliberately NOT an intake_request row: a received notice has no internal
requester (intake_request.requester_user_id is NOT NULL), and its deadline is
an externally-imposed statutory date rather than an internal SLA in hours.

notice_event is the append-only timeline (filed / assigned / responded /
escalated / closed / note) that gives each notice its audit trail.
"""
from alembic import op

revision = "0032_notice_register"
down_revision = "0031_drop_org_join_request"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Human-facing reference, mirroring intake_ref_seq's 'REQ-4123' scheme.
    op.execute("CREATE SEQUENCE IF NOT EXISTS notice_ref_seq START WITH 1001")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS notice (
            id                  VARCHAR(36)  PRIMARY KEY,
            org_id              VARCHAR(36)  NOT NULL,
            ref                 VARCHAR(20)  NOT NULL,
            direction           VARCHAR(10)  NOT NULL DEFAULT 'received',
            notice_type         VARCHAR(40)  NOT NULL DEFAULT 'other',
            subject             VARCHAR(200) NOT NULL,
            description         TEXT         NOT NULL DEFAULT '',
            counterparty_name   VARCHAR(200) NOT NULL,
            counterparty_ref    VARCHAR(120),
            contract_id         VARCHAR(36) REFERENCES contract(id) ON DELETE SET NULL,
            notice_date         DATE,
            received_at         DATE,
            response_due_date   DATE,
            status              VARCHAR(20)  NOT NULL DEFAULT 'open',
            priority            VARCHAR(20)  NOT NULL DEFAULT 'Medium',
            owner_user_id       VARCHAR(36) REFERENCES "user"(id),
            responded_at        TIMESTAMPTZ,
            response_summary    TEXT,
            closed_at           TIMESTAMPTZ,
            created_by_user_id  VARCHAR(36),
            updated_by_user_id  VARCHAR(36),
            created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_notice_org_id ON notice (org_id);")
    # The register's default view is "open notices by deadline", and the
    # reminder sweep scans due dates — both are covered by this composite.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notice_org_status_due "
        "ON notice (org_id, status, response_due_date);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_notice_contract_id ON notice (contract_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notice_owner_user_id ON notice (owner_user_id);")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_notice_org_ref ON notice (org_id, ref);"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS notice_event (
            id                  VARCHAR(36)  PRIMARY KEY,
            org_id              VARCHAR(36)  NOT NULL,
            notice_id           VARCHAR(36)  NOT NULL REFERENCES notice(id) ON DELETE CASCADE,
            kind                VARCHAR(30)  NOT NULL,
            body                TEXT,
            actor_user_id       VARCHAR(36),
            created_by_user_id  VARCHAR(36),
            updated_by_user_id  VARCHAR(36),
            created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_notice_event_org_id ON notice_event (org_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notice_event_notice_id_created "
        "ON notice_event (notice_id, created_at);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notice_event;")
    op.execute("DROP TABLE IF EXISTS notice;")
    op.execute("DROP SEQUENCE IF EXISTS notice_ref_seq;")
