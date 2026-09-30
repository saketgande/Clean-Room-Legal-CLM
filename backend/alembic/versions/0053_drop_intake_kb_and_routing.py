"""Drop the intake knowledge base (Self-Service) and intake routing rules.

Self-Service's articles had no editing screen, so it only ever showed seed
content. Intake routing rules never ran on a real request: only the demo seed
called the evaluator, while AI triage + teams did the actual assignment.
``intake_request.fired_rules`` was written only by that evaluator. All removed
at the product owner's request (2026-09-28). Downgrade recreates empty tables.
"""

from alembic import op

revision = "0053_drop_intake_kb_and_routing"
down_revision = "0052_drop_sanctions_list"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS intake_kb_article")
    op.execute("DROP TABLE IF EXISTS intake_routing_rule")
    op.execute("ALTER TABLE intake_request DROP COLUMN IF EXISTS fired_rules")


def downgrade() -> None:
    op.execute("ALTER TABLE intake_request ADD COLUMN IF NOT EXISTS fired_rules JSONB")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_routing_rule (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            name VARCHAR(120) NOT NULL,
            description TEXT,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            eval_order INTEGER NOT NULL DEFAULT 100,
            match_type VARCHAR(120),
            match_priority VARCHAR(20),
            match_department VARCHAR(60),
            match_keyword VARCHAR(200),
            match_complexity VARCHAR(20),
            set_assignee_user_id VARCHAR(36) REFERENCES "user"(id),
            set_priority VARCHAR(20),
            set_sla_hours INTEGER,
            set_team_id VARCHAR(36) REFERENCES intake_team(id) ON DELETE SET NULL,
            escalate_to_user_id VARCHAR(36) REFERENCES "user"(id),
            require_approval_from_user_id VARCHAR(36) REFERENCES "user"(id),
            times_fired INTEGER NOT NULL DEFAULT 0,
            last_fired_at TIMESTAMPTZ,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_intake_routing_rule_org_eval
            ON intake_routing_rule(org_id, enabled, eval_order);
        CREATE TABLE IF NOT EXISTS intake_kb_article (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            source_ref VARCHAR(120) NOT NULL,
            title VARCHAR(200) NOT NULL,
            body TEXT NOT NULL,
            tags JSONB,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_intake_kb_article_org_ref
            ON intake_kb_article(org_id, source_ref);
        """
    )
