"""ai architecture spine

Revision ID: 0002_ai_architecture_spine
Revises: 0001_initial_contract_platform
Create Date: 2026-05-15
"""

from alembic import op

revision = "0002_ai_architecture_spine"
down_revision = "0001_initial_contract_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_run (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            session_id VARCHAR(36) NOT NULL,
            status VARCHAR(80) NOT NULL,
            user_message_id VARCHAR(36),
            assistant_message_id VARCHAR(36),
            provider_state JSON NOT NULL,
            context_manifest JSON NOT NULL,
            model VARCHAR(160),
            model_config_hash VARCHAR(64),
            prompt_hash VARCHAR(64),
            current_tool_iteration INTEGER NOT NULL,
            error_message TEXT,
            completed_at TIMESTAMP WITH TIME ZONE,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_assistant_run_org_id ON assistant_run (org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_assistant_run_session_id ON assistant_run (session_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_assistant_run_status ON assistant_run (status)")

    for table, columns in {
        "assistant_tool_call": [
            "ADD COLUMN IF NOT EXISTS assistant_run_id VARCHAR(36)",
            "ADD COLUMN IF NOT EXISTS provider_tool_use_id VARCHAR(255)",
            "ADD COLUMN IF NOT EXISTS confirmation_id VARCHAR(36)",
            "ADD COLUMN IF NOT EXISTS resource_type VARCHAR(120)",
            "ADD COLUMN IF NOT EXISTS resource_id VARCHAR(36)",
            "ADD COLUMN IF NOT EXISTS resource_version VARCHAR(80)",
            "ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255)",
            "ADD COLUMN IF NOT EXISTS output_schema_name VARCHAR(180)",
            "ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITH TIME ZONE",
            "ADD COLUMN IF NOT EXISTS finished_at TIMESTAMP WITH TIME ZONE",
        ],
        "resource_timeline_event": [
            "ADD COLUMN IF NOT EXISTS skill_run_id VARCHAR(36)",
            "ADD COLUMN IF NOT EXISTS assistant_run_id VARCHAR(36)",
        ],
        "a_i_call_log": [
            "ADD COLUMN IF NOT EXISTS skill_run_id VARCHAR(36)",
            "ADD COLUMN IF NOT EXISTS job_id VARCHAR(36)",
            "ADD COLUMN IF NOT EXISTS session_id VARCHAR(36)",
            "ADD COLUMN IF NOT EXISTS assistant_run_id VARCHAR(36)",
            "ADD COLUMN IF NOT EXISTS tool_call_id VARCHAR(36)",
            "ADD COLUMN IF NOT EXISTS model_config_hash VARCHAR(64)",
            "ADD COLUMN IF NOT EXISTS prompt_key VARCHAR(180)",
            "ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(80)",
            "ADD COLUMN IF NOT EXISTS prompt_hash VARCHAR(64)",
            "ADD COLUMN IF NOT EXISTS input_payload JSON",
            "ADD COLUMN IF NOT EXISTS output_schema_name VARCHAR(180)",
            "ADD COLUMN IF NOT EXISTS status VARCHAR(80)",
            "ADD COLUMN IF NOT EXISTS validation_error TEXT",
            "ADD COLUMN IF NOT EXISTS provider_request_id VARCHAR(255)",
            "ADD COLUMN IF NOT EXISTS stop_reason VARCHAR(120)",
            "ADD COLUMN IF NOT EXISTS redaction_status VARCHAR(80)",
        ],
    }.items():
        for column_sql in columns:
            op.execute(f"ALTER TABLE {table} {column_sql}")

    op.execute("CREATE INDEX IF NOT EXISTS ix_assistant_tool_call_assistant_run_id ON assistant_tool_call (assistant_run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_assistant_tool_call_resource_type ON assistant_tool_call (resource_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_assistant_tool_call_resource_id ON assistant_tool_call (resource_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_assistant_tool_call_idempotency_key ON assistant_tool_call (idempotency_key)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_resource_timeline_event_skill_run_id ON resource_timeline_event (skill_run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_resource_timeline_event_assistant_run_id ON resource_timeline_event (assistant_run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_a_i_call_log_skill_run_id ON a_i_call_log (skill_run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_a_i_call_log_job_id ON a_i_call_log (job_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_a_i_call_log_session_id ON a_i_call_log (session_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_a_i_call_log_assistant_run_id ON a_i_call_log (assistant_run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_a_i_call_log_tool_call_id ON a_i_call_log (tool_call_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_a_i_call_log_prompt_key ON a_i_call_log (prompt_key)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_a_i_call_log_status ON a_i_call_log (status)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS a_i_skill_run (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            skill_name VARCHAR(180) NOT NULL,
            skill_version VARCHAR(80) NOT NULL,
            execution_mode VARCHAR(80) NOT NULL,
            status VARCHAR(80) NOT NULL,
            resource_type VARCHAR(120),
            resource_id VARCHAR(36),
            contract_id VARCHAR(36),
            contract_version_id VARCHAR(36),
            text_snapshot_id VARCHAR(36),
            job_id VARCHAR(36),
            session_id VARCHAR(36),
            assistant_run_id VARCHAR(36),
            tool_call_id VARCHAR(36),
            prompt_key VARCHAR(180) NOT NULL,
            prompt_version VARCHAR(80) NOT NULL,
            prompt_hash VARCHAR(64) NOT NULL,
            model VARCHAR(160) NOT NULL,
            model_config_hash VARCHAR(64),
            input_payload JSON NOT NULL,
            output_payload JSON,
            validation_status VARCHAR(80) NOT NULL,
            validation_error TEXT,
            error_message TEXT,
            retry_count INTEGER NOT NULL,
            started_at TIMESTAMP WITH TIME ZONE,
            finished_at TIMESTAMP WITH TIME ZONE,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    for index_sql in [
        "CREATE INDEX IF NOT EXISTS ix_a_i_skill_run_org_id ON a_i_skill_run (org_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_skill_run_skill_name ON a_i_skill_run (skill_name)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_skill_run_execution_mode ON a_i_skill_run (execution_mode)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_skill_run_status ON a_i_skill_run (status)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_skill_run_resource_type ON a_i_skill_run (resource_type)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_skill_run_resource_id ON a_i_skill_run (resource_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_skill_run_contract_id ON a_i_skill_run (contract_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_skill_run_contract_version_id ON a_i_skill_run (contract_version_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_skill_run_text_snapshot_id ON a_i_skill_run (text_snapshot_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_skill_run_job_id ON a_i_skill_run (job_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_skill_run_session_id ON a_i_skill_run (session_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_skill_run_assistant_run_id ON a_i_skill_run (assistant_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_skill_run_tool_call_id ON a_i_skill_run (tool_call_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_skill_run_prompt_key ON a_i_skill_run (prompt_key)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_skill_run_validation_status ON a_i_skill_run (validation_status)",
    ]:
        op.execute(index_sql)

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS a_i_prompt_version (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            prompt_key VARCHAR(180) NOT NULL,
            version VARCHAR(80) NOT NULL,
            status VARCHAR(80) NOT NULL,
            prompt_text TEXT NOT NULL,
            prompt_hash VARCHAR(64) NOT NULL,
            description TEXT,
            model_name VARCHAR(160),
            model_config_hash VARCHAR(64),
            live_eval_required BOOLEAN NOT NULL,
            activated_at TIMESTAMP WITH TIME ZONE,
            archived_at TIMESTAMP WITH TIME ZONE,
            metadata_json JSON NOT NULL,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_a_i_prompt_version_org_id ON a_i_prompt_version (org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_a_i_prompt_version_prompt_key ON a_i_prompt_version (prompt_key)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_a_i_prompt_version_status ON a_i_prompt_version (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_a_i_prompt_version_prompt_hash ON a_i_prompt_version (prompt_hash)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS a_i_confirmation (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            session_id VARCHAR(36),
            assistant_run_id VARCHAR(36),
            tool_call_id VARCHAR(36),
            tool_name VARCHAR(180) NOT NULL,
            status VARCHAR(80) NOT NULL,
            requested_payload JSON NOT NULL,
            tool_input JSON NOT NULL,
            policy JSON NOT NULL,
            resource_type VARCHAR(120),
            resource_id VARCHAR(36),
            resource_version VARCHAR(80),
            idempotency_key VARCHAR(255),
            expires_at TIMESTAMP WITH TIME ZONE,
            decided_at TIMESTAMP WITH TIME ZONE,
            decided_by_user_id VARCHAR(36),
            rejection_reason TEXT,
            provider_state JSON NOT NULL,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    for index_sql in [
        "CREATE INDEX IF NOT EXISTS ix_a_i_confirmation_org_id ON a_i_confirmation (org_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_confirmation_session_id ON a_i_confirmation (session_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_confirmation_assistant_run_id ON a_i_confirmation (assistant_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_confirmation_tool_call_id ON a_i_confirmation (tool_call_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_confirmation_tool_name ON a_i_confirmation (tool_name)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_confirmation_status ON a_i_confirmation (status)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_confirmation_resource_type ON a_i_confirmation (resource_type)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_confirmation_resource_id ON a_i_confirmation (resource_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_confirmation_idempotency_key ON a_i_confirmation (idempotency_key)",
    ]:
        op.execute(index_sql)

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS a_i_citation (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            skill_run_id VARCHAR(36),
            ai_call_id VARCHAR(36),
            assistant_run_id VARCHAR(36),
            tool_call_id VARCHAR(36),
            resource_type VARCHAR(120),
            resource_id VARCHAR(36),
            contract_id VARCHAR(36),
            contract_version_id VARCHAR(36),
            text_snapshot_id VARCHAR(36),
            label VARCHAR(255),
            quote TEXT NOT NULL,
            normalized_quote TEXT,
            page_number INTEGER,
            start_char INTEGER,
            end_char INTEGER,
            validation_status VARCHAR(80) NOT NULL,
            similarity_score FLOAT,
            metadata_json JSON NOT NULL,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    for index_sql in [
        "CREATE INDEX IF NOT EXISTS ix_a_i_citation_org_id ON a_i_citation (org_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_citation_skill_run_id ON a_i_citation (skill_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_citation_ai_call_id ON a_i_citation (ai_call_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_citation_assistant_run_id ON a_i_citation (assistant_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_citation_tool_call_id ON a_i_citation (tool_call_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_citation_resource_type ON a_i_citation (resource_type)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_citation_resource_id ON a_i_citation (resource_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_citation_contract_id ON a_i_citation (contract_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_citation_contract_version_id ON a_i_citation (contract_version_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_citation_text_snapshot_id ON a_i_citation (text_snapshot_id)",
        "CREATE INDEX IF NOT EXISTS ix_a_i_citation_validation_status ON a_i_citation (validation_status)",
    ]:
        op.execute(index_sql)

    op.execute("ALTER TABLE contract_embedding ALTER COLUMN embedding TYPE vector(384) USING NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE contract_embedding ALTER COLUMN embedding TYPE vector(1536) USING NULL")
    op.execute("DROP TABLE IF EXISTS a_i_citation")
    op.execute("DROP TABLE IF EXISTS a_i_confirmation")
    op.execute("DROP TABLE IF EXISTS a_i_prompt_version")
    op.execute("DROP TABLE IF EXISTS a_i_skill_run")
    op.execute("DROP TABLE IF EXISTS assistant_run")
