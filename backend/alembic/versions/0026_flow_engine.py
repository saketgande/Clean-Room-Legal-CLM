"""Workflow engine: flow / flow_run / flow_step_run.

A Flow is a reusable, versioned workflow definition (selection criteria + an
ordered JSON list of typed steps). A FlowRun is one ticket walking one flow; a
FlowStepRun is the per-step audit row the executor advances. This is the single
engine that will drive tickets end-to-end (draft -> AI review -> approval ladder
-> signature -> active).
"""

from alembic import op

revision = "0026_flow_engine"
down_revision = "0025_approval_subject"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS flow (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            name VARCHAR(160) NOT NULL,
            description TEXT,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
            eval_order INTEGER NOT NULL DEFAULT 100,
            version INTEGER NOT NULL DEFAULT 1,
            criteria JSON NOT NULL DEFAULT '{}',
            steps JSON NOT NULL DEFAULT '[]',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS flow_run (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            request_id VARCHAR(36) NOT NULL REFERENCES intake_request(id) ON DELETE CASCADE,
            flow_id VARCHAR(36) REFERENCES flow(id) ON DELETE SET NULL,
            flow_name VARCHAR(160) NOT NULL,
            flow_version INTEGER NOT NULL DEFAULT 1,
            steps JSON NOT NULL DEFAULT '[]',
            status VARCHAR(24) NOT NULL DEFAULT 'running',
            current_index INTEGER NOT NULL DEFAULT 0,
            contract_id VARCHAR(36) REFERENCES contract(id) ON DELETE SET NULL,
            context JSON NOT NULL DEFAULT '{}',
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_flow_run_request_id ON flow_run (request_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_flow_run_contract_id ON flow_run (contract_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_flow_run_status ON flow_run (status);")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS flow_step_run (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            flow_run_id VARCHAR(36) NOT NULL REFERENCES flow_run(id) ON DELETE CASCADE,
            idx INTEGER NOT NULL,
            step_type VARCHAR(40) NOT NULL,
            step_name VARCHAR(160) NOT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'pending',
            assignee_user_id VARCHAR(36) REFERENCES "user"(id) ON DELETE SET NULL,
            team_id VARCHAR(36),
            waiting_job_id VARCHAR(36),
            result JSON,
            note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_flow_step_run_flow_run_id ON flow_step_run (flow_run_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_flow_step_run_waiting_job_id ON flow_step_run (waiting_job_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS flow_step_run;")
    op.execute("DROP TABLE IF EXISTS flow_run;")
    op.execute("DROP TABLE IF EXISTS flow;")
