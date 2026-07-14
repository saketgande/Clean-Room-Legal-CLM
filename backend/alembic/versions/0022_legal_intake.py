"""Legal intake module (native intake — the legal front door).

Adds the intake domain: a first-class request entity plus no-code request
types/fields, routing rules, claimable team pools, an append-only custody
ledger, the AI review-recommendation artifact, sub-tasks, and a citable KB.
Reuses the existing audit chain, AI skill pipeline, confirmations gate,
delegation-of-authority, projects and celery infrastructure — this migration is
just the new tables. Status ∈ awaiting_triage|in_review|escalated|approved|closed
(no 'rejected' status); closed_at stamps once on entering a terminal status.

Revision ID: 0022_legal_intake
Revises: 0021_authority_grants
Create Date: 2026-07-11
"""

from alembic import op

revision = "0022_legal_intake"
down_revision = "0021_authority_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS intake_ref_seq START WITH 4001")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_request_type (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            key VARCHAR(60) NOT NULL,
            name VARCHAR(120) NOT NULL,
            workstream VARCHAR(120),
            description TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            stages JSONB,
            sort_order INTEGER NOT NULL DEFAULT 100,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_request_field (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            request_type_id VARCHAR(36) NOT NULL REFERENCES intake_request_type(id) ON DELETE CASCADE,
            key VARCHAR(60) NOT NULL,
            label VARCHAR(120) NOT NULL,
            kind VARCHAR(20) NOT NULL DEFAULT 'text',
            required BOOLEAN NOT NULL DEFAULT FALSE,
            sort_order INTEGER NOT NULL DEFAULT 100,
            options JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_team (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            key VARCHAR(60) NOT NULL,
            name VARCHAR(120) NOT NULL,
            description TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            strategy VARCHAR(20) NOT NULL DEFAULT 'least_loaded',
            overflow_team_id VARCHAR(36) REFERENCES intake_team(id) ON DELETE SET NULL,
            sort_order INTEGER NOT NULL DEFAULT 100,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_team_member (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            team_id VARCHAR(36) NOT NULL REFERENCES intake_team(id) ON DELETE CASCADE,
            user_id VARCHAR(36) NOT NULL REFERENCES "user"(id),
            capacity INTEGER NOT NULL DEFAULT 0,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            last_assigned_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
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
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_request (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            ref VARCHAR(20) NOT NULL,
            source VARCHAR(20) NOT NULL DEFAULT 'form',
            requester_user_id VARCHAR(36) NOT NULL REFERENCES "user"(id),
            requester_name VARCHAR(200),
            department VARCHAR(60),
            request_type_id VARCHAR(36) REFERENCES intake_request_type(id) ON DELETE SET NULL,
            type_label VARCHAR(120) NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            field_values JSONB,
            priority VARCHAR(20) NOT NULL DEFAULT 'Medium',
            status VARCHAR(40) NOT NULL DEFAULT 'awaiting_triage',
            stage VARCHAR(60) NOT NULL DEFAULT 'new',
            work_status VARCHAR(40),
            assigned_to_user_id VARCHAR(36) REFERENCES "user"(id),
            approval_gate_user_id VARCHAR(36) REFERENCES "user"(id),
            sla_hours INTEGER NOT NULL DEFAULT 24,
            sla_status VARCHAR(20) NOT NULL DEFAULT 'on_track',
            paused_at TIMESTAMPTZ,
            paused_ms_total BIGINT NOT NULL DEFAULT 0,
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            closed_at TIMESTAMPTZ,
            triaged_by_user_id VARCHAR(36) REFERENCES "user"(id),
            triaged_at TIMESTAMPTZ,
            triage_action VARCHAR(30),
            snoozed_until TIMESTAMPTZ,
            agent_processed_at TIMESTAMPTZ,
            agent_outcome VARCHAR(20),
            ai_triage JSONB,
            fired_rules JSONB,
            stage_timestamps JSONB,
            conversation JSONB,
            handoff_holder VARCHAR(10),
            handoff_user_id VARCHAR(36),
            handoff_updated_at TIMESTAMPTZ,
            external_message_id VARCHAR(200),
            project_id VARCHAR(36),
            contract_id VARCHAR(36) REFERENCES contract(id) ON DELETE SET NULL,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_agent_recommendation (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            request_id VARCHAR(36) NOT NULL REFERENCES intake_request(id) ON DELETE CASCADE,
            agent_id VARCHAR(60) NOT NULL,
            confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
            suggested_action VARCHAR(40) NOT NULL DEFAULT 'flag_for_review',
            drafted_response TEXT NOT NULL DEFAULT '',
            reasoning TEXT NOT NULL DEFAULT '',
            concerns JSONB,
            citations JSONB,
            short_form_reply TEXT,
            degraded BOOLEAN NOT NULL DEFAULT FALSE,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            reviewed_by_user_id VARCHAR(36) REFERENCES "user"(id),
            reviewed_at TIMESTAMPTZ,
            override_reason VARCHAR(300),
            edited_at TIMESTAMPTZ,
            skill_run_id VARCHAR(36),
            confirmation_id VARCHAR(36),
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_handoff (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            request_id VARCHAR(36) NOT NULL REFERENCES intake_request(id) ON DELETE CASCADE,
            from_holder VARCHAR(10),
            to_holder VARCHAR(10) NOT NULL,
            to_user_id VARCHAR(36),
            reason VARCHAR(300),
            actor_type VARCHAR(10) NOT NULL DEFAULT 'user',
            recommendation_id VARCHAR(36) REFERENCES intake_agent_recommendation(id) ON DELETE SET NULL,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_task (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            request_id VARCHAR(36) NOT NULL REFERENCES intake_request(id) ON DELETE CASCADE,
            title VARCHAR(200) NOT NULL,
            description TEXT,
            assignee_user_id VARCHAR(36) REFERENCES "user"(id),
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            sort_order INTEGER NOT NULL DEFAULT 100,
            effort_minutes INTEGER NOT NULL DEFAULT 0,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
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
        )
        """
    )

    # indexes
    for stmt in [
        "CREATE INDEX IF NOT EXISTS ix_intake_request_type_org ON intake_request_type(org_id)",
        "CREATE INDEX IF NOT EXISTS ix_intake_request_field_type ON intake_request_field(request_type_id)",
        "CREATE INDEX IF NOT EXISTS ix_intake_team_org ON intake_team(org_id)",
        "CREATE INDEX IF NOT EXISTS ix_intake_team_member_team ON intake_team_member(team_id)",
        "CREATE INDEX IF NOT EXISTS ix_intake_routing_rule_org_eval ON intake_routing_rule(org_id, enabled, eval_order)",
        "CREATE INDEX IF NOT EXISTS ix_intake_request_org_status ON intake_request(org_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_intake_request_org_stage ON intake_request(org_id, stage)",
        "CREATE INDEX IF NOT EXISTS ix_intake_request_org_assignee ON intake_request(org_id, assigned_to_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_intake_request_org_requester ON intake_request(org_id, requester_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_intake_request_org_submitted ON intake_request(org_id, submitted_at)",
        "CREATE INDEX IF NOT EXISTS ix_intake_handoff_request_created ON intake_handoff(request_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_intake_agent_recommendation_request ON intake_agent_recommendation(request_id)",
        "CREATE INDEX IF NOT EXISTS ix_intake_agent_recommendation_org_status ON intake_agent_recommendation(org_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_intake_task_request ON intake_task(request_id)",
        # uniques
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_intake_request_org_ref ON intake_request(org_id, ref)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_intake_request_type_org_key ON intake_request_type(org_id, key)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_intake_request_field_type_key ON intake_request_field(request_type_id, key)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_intake_team_org_key ON intake_team(org_id, key)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_intake_team_member_team_user ON intake_team_member(team_id, user_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_intake_kb_article_org_ref ON intake_kb_article(org_id, source_ref)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_intake_request_org_extmsg ON intake_request(org_id, external_message_id) WHERE external_message_id IS NOT NULL",
    ]:
        op.execute(stmt)


def downgrade() -> None:
    for tbl in [
        "intake_kb_article",
        "intake_task",
        "intake_handoff",
        "intake_agent_recommendation",
        "intake_request",
        "intake_routing_rule",
        "intake_team_member",
        "intake_team",
        "intake_request_field",
        "intake_request_type",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
    op.execute("DROP SEQUENCE IF EXISTS intake_ref_seq")
