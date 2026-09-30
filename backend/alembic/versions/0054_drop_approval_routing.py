"""Drop approval routing rules: a workflow's Approval step now decides who approves.

Routing rules and the workflow's Approval step both named approvers, and the
rules silently won — the team picked on the step was only used when no rule
matched, so a catch-all rule made it dead. Removed at the product owner's
request (2026-09-28). Workflow steps that pinned a rule lose the pin (they now
use their own team). Downgrade recreates empty tables; the rules are not restored.
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0054_drop_approval_routing"
down_revision = "0053_drop_intake_kb_and_routing"
branch_labels = None
depends_on = None


def _strip_pins(table: str) -> None:
    if op.get_context().as_sql:
        return  # offline SQL generation: no rows to read; the pins are then just ignored
    bind = op.get_bind()
    for row_id, steps in bind.execute(sa.text(f"SELECT id, steps FROM {table}")).all():
        steps = json.loads(steps) if isinstance(steps, str) else (steps or [])
        changed = False
        for st in steps:
            cfg = (st or {}).get("config") or {}
            if "routing_rule_id" in cfg:
                cfg.pop("routing_rule_id")
                changed = True
        if changed:
            bind.execute(sa.text(f"UPDATE {table} SET steps = CAST(:s AS JSON) WHERE id = :id"),
                         {"s": json.dumps(steps), "id": row_id})


def upgrade() -> None:
    op.execute("ALTER TABLE approval_request DROP COLUMN IF EXISTS routing_rule_id")
    op.execute("DROP TABLE IF EXISTS approval_routing_step")
    op.execute("DROP TABLE IF EXISTS approval_routing_rule")
    _strip_pins("workflow")
    _strip_pins("workflow_run")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_routing_rule (
            id VARCHAR(36) NOT NULL CONSTRAINT pk_approval_routing_rule PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            name VARCHAR(255) NOT NULL,
            priority VARCHAR(40) NOT NULL,
            criteria JSON NOT NULL,
            approver_role VARCHAR(120),
            approver_user_id VARCHAR(36) REFERENCES "user"(id),
            is_active BOOLEAN NOT NULL,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_approval_routing_rule_org_id ON approval_routing_rule (org_id);
        CREATE TABLE IF NOT EXISTS approval_routing_step (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            rule_id VARCHAR(36) NOT NULL REFERENCES approval_routing_rule(id) ON DELETE CASCADE,
            step_order INTEGER NOT NULL DEFAULT 1,
            approver_group_id VARCHAR(36) REFERENCES approver_group(id),
            approver_user_id VARCHAR(36) REFERENCES "user"(id),
            approver_role VARCHAR(120),
            mode VARCHAR(20) NOT NULL DEFAULT 'any',
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_approval_routing_step_org_id ON approval_routing_step (org_id);
        CREATE INDEX IF NOT EXISTS ix_approval_routing_step_rule_id ON approval_routing_step (rule_id);
        ALTER TABLE approval_request
            ADD COLUMN IF NOT EXISTS routing_rule_id VARCHAR(36) REFERENCES approval_routing_rule(id);
        """
    )
