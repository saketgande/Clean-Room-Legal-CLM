"""initial contract-first platform schema

Revision ID: 0001_initial_contract_platform
Revises:
Create Date: 2026-05-14
"""

from alembic import op

revision = "0001_initial_contract_platform"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("""
    CREATE TABLE a_i_call_log (
        request_id VARCHAR(80),
        skill_run_id VARCHAR(36),
        job_id VARCHAR(36),
        session_id VARCHAR(36),
        assistant_run_id VARCHAR(36),
        tool_call_id VARCHAR(36),
        resource_type VARCHAR(120),
        resource_id VARCHAR(36),
        provider VARCHAR(80) NOT NULL,
        model VARCHAR(160) NOT NULL,
        model_config_hash VARCHAR(64),
        prompt_key VARCHAR(180),
        prompt_version VARCHAR(80),
        prompt_hash VARCHAR(64),
        input_payload JSON,
        output_schema_name VARCHAR(180),
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        total_tokens INTEGER,
        latency_ms FLOAT,
        status VARCHAR(80),
        validation_status VARCHAR(80),
        validation_error TEXT,
        error_class VARCHAR(160),
        provider_request_id VARCHAR(255),
        stop_reason VARCHAR(120),
        redaction_status VARCHAR(80),
        raw_ai_output JSON,
        validated_output JSON,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_a_i_call_log PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE a_i_prompt_version (
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
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_a_i_prompt_version PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE admin_setting (
        key VARCHAR(180) NOT NULL,
        value JSON,
        is_secret BOOLEAN NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_admin_setting PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE audit_log (
        org_id VARCHAR(36),
        actor_user_id VARCHAR(36),
        action VARCHAR(120) NOT NULL,
        resource_type VARCHAR(120) NOT NULL,
        resource_id VARCHAR(36),
        request_id VARCHAR(80),
        ip_address VARCHAR(80),
        user_agent TEXT,
        before JSON,
        after JSON,
        metadata_json JSON,
        prev_hash VARCHAR(64),
        row_hash VARCHAR(64) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        id VARCHAR(36) NOT NULL,
        CONSTRAINT pk_audit_log PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE job_run (
        job_type VARCHAR(160) NOT NULL,
        resource_type VARCHAR(120) NOT NULL,
        resource_id VARCHAR(36) NOT NULL,
        idempotency_key VARCHAR(255),
        status VARCHAR(40) NOT NULL,
        progress INTEGER NOT NULL,
        started_at TIMESTAMP WITH TIME ZONE,
        finished_at TIMESTAMP WITH TIME ZONE,
        error_message TEXT,
        error_stack TEXT,
        attempt_count INTEGER NOT NULL,
        celery_task_id VARCHAR(255),
        metadata_json JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_job_run PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE organization (
        name VARCHAR(255) NOT NULL,
        slug VARCHAR(120) NOT NULL,
        allowed_domains JSON NOT NULL,
        default_role_name VARCHAR(120) NOT NULL,
        setup_complete BOOLEAN NOT NULL,
        settings JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_organization PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE permission (
        value VARCHAR(160) NOT NULL,
        description TEXT,
        id VARCHAR(36) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_permission PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE playbook (
        name VARCHAR(255) NOT NULL,
        description TEXT,
        status VARCHAR(80) NOT NULL,
        current_version_id VARCHAR(36),
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        deleted_at TIMESTAMP WITH TIME ZONE,
        deleted_by_user_id VARCHAR(36),
        legal_hold BOOLEAN NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_playbook PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE request_log (
        request_id VARCHAR(80) NOT NULL,
        user_id VARCHAR(36),
        org_id VARCHAR(36),
        method VARCHAR(16) NOT NULL,
        route VARCHAR(500) NOT NULL,
        status_code INTEGER NOT NULL,
        latency_ms FLOAT NOT NULL,
        error_class VARCHAR(160),
        request_metadata JSON,
        id VARCHAR(36) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_request_log PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE resource_timeline_event (
        resource_type VARCHAR(120) NOT NULL,
        resource_id VARCHAR(36) NOT NULL,
        event_type VARCHAR(120) NOT NULL,
        title VARCHAR(255) NOT NULL,
        details JSON,
        request_id VARCHAR(80),
        job_id VARCHAR(36),
        skill_run_id VARCHAR(36),
        assistant_run_id VARCHAR(36),
        ai_call_id VARCHAR(36),
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_resource_timeline_event PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE role (
        name VARCHAR(120) NOT NULL,
        description TEXT,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_role PRIMARY KEY (id),
        CONSTRAINT uq_role_org_name UNIQUE (org_id, name)
    )
    """)
    op.execute("""
    CREATE TABLE storage_object (
        storage_key VARCHAR(1000) NOT NULL,
        filename VARCHAR(500) NOT NULL,
        mime_type VARCHAR(255) NOT NULL,
        size_bytes INTEGER NOT NULL,
        sha256_hash VARCHAR(64) NOT NULL,
        storage_backend VARCHAR(80) NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        deleted_at TIMESTAMP WITH TIME ZONE,
        deleted_by_user_id VARCHAR(36),
        legal_hold BOOLEAN NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_storage_object PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE usage_record (
        user_id VARCHAR(36),
        resource_type VARCHAR(120) NOT NULL,
        resource_id VARCHAR(36),
        metric VARCHAR(120) NOT NULL,
        quantity FLOAT NOT NULL,
        metadata_json JSON,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_usage_record PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE user_invitation (
        email VARCHAR(320) NOT NULL,
        role_name VARCHAR(120) NOT NULL,
        token_hash VARCHAR(255) NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        accepted_at TIMESTAMP WITH TIME ZONE,
        revoked_at TIMESTAMP WITH TIME ZONE,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_user_invitation PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE workflow (
        name VARCHAR(255) NOT NULL,
        workflow_type VARCHAR(80) NOT NULL,
        visibility VARCHAR(80) NOT NULL,
        description TEXT,
        definition JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        deleted_at TIMESTAMP WITH TIME ZONE,
        deleted_by_user_id VARCHAR(36),
        legal_hold BOOLEAN NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_workflow PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE playbook_version (
        playbook_id VARCHAR(36) NOT NULL,
        version_number INTEGER NOT NULL,
        status VARCHAR(80) NOT NULL,
        summary TEXT,
        source_metadata JSON,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_playbook_version PRIMARY KEY (id),
        CONSTRAINT fk_playbook_version_playbook_id_playbook FOREIGN KEY(playbook_id) REFERENCES playbook (id)
    )
    """)
    op.execute("""
    CREATE TABLE role_permission (
        role_id VARCHAR(36) NOT NULL,
        permission_id VARCHAR(36) NOT NULL,
        CONSTRAINT pk_role_permission PRIMARY KEY (role_id, permission_id),
        CONSTRAINT fk_role_permission_role_id_role FOREIGN KEY(role_id) REFERENCES role (id) ON DELETE CASCADE,
        CONSTRAINT fk_role_permission_permission_id_permission FOREIGN KEY(permission_id) REFERENCES permission (id) ON DELETE CASCADE
    )
    """)
    op.execute("""
    CREATE TABLE "user" (
        email VARCHAR(320) NOT NULL,
        full_name VARCHAR(255) NOT NULL,
        hashed_password VARCHAR(500) NOT NULL,
        status VARCHAR(40) NOT NULL,
        active_role_id VARCHAR(36),
        last_login_at TIMESTAMP WITH TIME ZONE,
        preferences JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_user PRIMARY KEY (id),
        CONSTRAINT fk_user_active_role_id_role FOREIGN KEY(active_role_id) REFERENCES role (id)
    )
    """)
    op.execute("""
    CREATE TABLE api_key (
        user_id VARCHAR(36) NOT NULL,
        name VARCHAR(255) NOT NULL,
        key_hash VARCHAR(255) NOT NULL,
        last_used_at TIMESTAMP WITH TIME ZONE,
        revoked_at TIMESTAMP WITH TIME ZONE,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_api_key PRIMARY KEY (id),
        CONSTRAINT fk_api_key_user_id_user FOREIGN KEY(user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE approval_routing_rule (
        name VARCHAR(255) NOT NULL,
        priority VARCHAR(40) NOT NULL,
        criteria JSON NOT NULL,
        approver_role VARCHAR(120),
        approver_user_id VARCHAR(36),
        is_active BOOLEAN NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_approval_routing_rule PRIMARY KEY (id),
        CONSTRAINT fk_approval_routing_rule_approver_user_id_user FOREIGN KEY(approver_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE contract (
        title VARCHAR(500) NOT NULL,
        contract_type VARCHAR(160),
        lifecycle_stage VARCHAR(80) NOT NULL,
        owner_user_id VARCHAR(36) NOT NULL,
        counterparty_name VARCHAR(255),
        jurisdiction VARCHAR(160),
        risk_level VARCHAR(80),
        value_amount FLOAT,
        currency VARCHAR(3),
        effective_date DATE,
        expiration_date DATE,
        current_contract_file_id VARCHAR(36),
        current_authoritative_version_id VARCHAR(36),
        metadata_json JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        deleted_at TIMESTAMP WITH TIME ZONE,
        deleted_by_user_id VARCHAR(36),
        legal_hold BOOLEAN NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_contract PRIMARY KEY (id),
        CONSTRAINT fk_contract_owner_user_id_user FOREIGN KEY(owner_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE notification (
        user_id VARCHAR(36),
        channel VARCHAR(80) NOT NULL,
        event_type VARCHAR(160) NOT NULL,
        subject VARCHAR(500),
        body TEXT,
        status VARCHAR(80) NOT NULL,
        provider_message_id VARCHAR(255),
        sent_at TIMESTAMP WITH TIME ZONE,
        error_message TEXT,
        metadata_json JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_notification PRIMARY KEY (id),
        CONSTRAINT fk_notification_user_id_user FOREIGN KEY(user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE org_join_request (
        org_id VARCHAR(36),
        email VARCHAR(320) NOT NULL,
        full_name VARCHAR(255) NOT NULL,
        requested_domain VARCHAR(255),
        message TEXT,
        status VARCHAR(40) NOT NULL,
        decided_by_user_id VARCHAR(36),
        decision_reason TEXT,
        id VARCHAR(36) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_org_join_request PRIMARY KEY (id),
        CONSTRAINT fk_org_join_request_org_id_organization FOREIGN KEY(org_id) REFERENCES organization (id),
        CONSTRAINT fk_org_join_request_decided_by_user_id_user FOREIGN KEY(decided_by_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE password_reset_token (
        user_id VARCHAR(36) NOT NULL,
        token_hash VARCHAR(255) NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        used_at TIMESTAMP WITH TIME ZONE,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_password_reset_token PRIMARY KEY (id),
        CONSTRAINT fk_password_reset_token_user_id_user FOREIGN KEY(user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE playbook_rule (
        playbook_version_id VARCHAR(36) NOT NULL,
        clause_type VARCHAR(160) NOT NULL,
        rule_type VARCHAR(160) NOT NULL,
        preferred_position TEXT,
        fallback_position TEXT,
        prohibited_language TEXT,
        required_language TEXT,
        risk_level VARCHAR(80),
        rationale TEXT,
        approval_required BOOLEAN NOT NULL,
        escalation_role VARCHAR(120),
        sample_clause TEXT,
        negotiation_guidance TEXT,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_playbook_rule PRIMARY KEY (id),
        CONSTRAINT fk_playbook_rule_playbook_version_id_playbook_version FOREIGN KEY(playbook_version_id) REFERENCES playbook_version (id)
    )
    """)
    op.execute("""
    CREATE TABLE project (
        name VARCHAR(255) NOT NULL,
        description TEXT,
        project_type VARCHAR(80) NOT NULL,
        owner_user_id VARCHAR(36) NOT NULL,
        metadata_json JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        deleted_at TIMESTAMP WITH TIME ZONE,
        deleted_by_user_id VARCHAR(36),
        legal_hold BOOLEAN NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_project PRIMARY KEY (id),
        CONSTRAINT fk_project_owner_user_id_user FOREIGN KEY(owner_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE refresh_token (
        user_id VARCHAR(36) NOT NULL,
        token_hash VARCHAR(255) NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        revoked_at TIMESTAMP WITH TIME ZONE,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_refresh_token PRIMARY KEY (id),
        CONSTRAINT fk_refresh_token_user_id_user FOREIGN KEY(user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE user_approval_decision (
        target_user_id VARCHAR(36) NOT NULL,
        decision VARCHAR(40) NOT NULL,
        reason TEXT,
        metadata_json JSON,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_user_approval_decision PRIMARY KEY (id),
        CONSTRAINT fk_user_approval_decision_target_user_id_user FOREIGN KEY(target_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE user_role (
        user_id VARCHAR(36) NOT NULL,
        role_id VARCHAR(36) NOT NULL,
        CONSTRAINT pk_user_role PRIMARY KEY (user_id, role_id),
        CONSTRAINT fk_user_role_user_id_user FOREIGN KEY(user_id) REFERENCES "user" (id) ON DELETE CASCADE,
        CONSTRAINT fk_user_role_role_id_role FOREIGN KEY(role_id) REFERENCES role (id) ON DELETE CASCADE
    )
    """)
    op.execute("""
    CREATE TABLE assistant_session (
        session_type VARCHAR(80) NOT NULL,
        title VARCHAR(255),
        project_id VARCHAR(36),
        contract_id VARCHAR(36),
        tabular_review_id VARCHAR(36),
        status VARCHAR(80) NOT NULL,
        metadata_json JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_assistant_session PRIMARY KEY (id),
        CONSTRAINT fk_assistant_session_project_id_project FOREIGN KEY(project_id) REFERENCES project (id),
        CONSTRAINT fk_assistant_session_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id)
    )
    """)
    op.execute("""
    CREATE TABLE brain_query (
        query_scope VARCHAR(80) NOT NULL,
        question TEXT NOT NULL,
        contract_id VARCHAR(36),
        project_id VARCHAR(36),
        answer TEXT,
        citations JSON,
        retrieval_metadata JSON,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_brain_query PRIMARY KEY (id),
        CONSTRAINT fk_brain_query_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_brain_query_project_id_project FOREIGN KEY(project_id) REFERENCES project (id)
    )
    """)
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
    op.execute("""
    CREATE TABLE contract_file (
        contract_id VARCHAR(36) NOT NULL,
        current_version_id VARCHAR(36),
        file_label VARCHAR(255) NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        deleted_at TIMESTAMP WITH TIME ZONE,
        deleted_by_user_id VARCHAR(36),
        legal_hold BOOLEAN NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_contract_file PRIMARY KEY (id),
        CONSTRAINT fk_contract_file_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id)
    )
    """)
    op.execute("""
    CREATE TABLE contract_party (
        contract_id VARCHAR(36) NOT NULL,
        name VARCHAR(255) NOT NULL,
        party_type VARCHAR(120),
        contact_email VARCHAR(320),
        metadata_json JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_contract_party PRIMARY KEY (id),
        CONSTRAINT fk_contract_party_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id)
    )
    """)
    op.execute("""
    CREATE TABLE contract_stage_history (
        contract_id VARCHAR(36) NOT NULL,
        from_stage VARCHAR(80),
        to_stage VARCHAR(80) NOT NULL,
        reason TEXT,
        changed_by_user_id VARCHAR(36),
        changed_at TIMESTAMP WITH TIME ZONE NOT NULL,
        override_used BOOLEAN NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_contract_stage_history PRIMARY KEY (id),
        CONSTRAINT fk_contract_stage_history_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_contract_stage_history_changed_by_user_id_user FOREIGN KEY(changed_by_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE project_activity (
        project_id VARCHAR(36) NOT NULL,
        actor_user_id VARCHAR(36),
        activity_type VARCHAR(120) NOT NULL,
        title VARCHAR(255) NOT NULL,
        details JSON,
        occurred_at TIMESTAMP WITH TIME ZONE,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_project_activity PRIMARY KEY (id),
        CONSTRAINT fk_project_activity_project_id_project FOREIGN KEY(project_id) REFERENCES project (id),
        CONSTRAINT fk_project_activity_actor_user_id_user FOREIGN KEY(actor_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE project_folder (
        project_id VARCHAR(36) NOT NULL,
        parent_folder_id VARCHAR(36),
        name VARCHAR(255) NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        deleted_at TIMESTAMP WITH TIME ZONE,
        deleted_by_user_id VARCHAR(36),
        legal_hold BOOLEAN NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_project_folder PRIMARY KEY (id),
        CONSTRAINT fk_project_folder_project_id_project FOREIGN KEY(project_id) REFERENCES project (id),
        CONSTRAINT fk_project_folder_parent_folder_id_project_folder FOREIGN KEY(parent_folder_id) REFERENCES project_folder (id)
    )
    """)
    op.execute("""
    CREATE TABLE project_member (
        project_id VARCHAR(36) NOT NULL,
        user_id VARCHAR(36) NOT NULL,
        role VARCHAR(120) NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_project_member PRIMARY KEY (id),
        CONSTRAINT uq_project_member UNIQUE (project_id, user_id),
        CONSTRAINT fk_project_member_project_id_project FOREIGN KEY(project_id) REFERENCES project (id),
        CONSTRAINT fk_project_member_user_id_user FOREIGN KEY(user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE project_share (
        project_id VARCHAR(36) NOT NULL,
        shared_with_user_id VARCHAR(36) NOT NULL,
        access_level VARCHAR(40) NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE,
        revoked_at TIMESTAMP WITH TIME ZONE,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_project_share PRIMARY KEY (id),
        CONSTRAINT fk_project_share_project_id_project FOREIGN KEY(project_id) REFERENCES project (id),
        CONSTRAINT fk_project_share_shared_with_user_id_user FOREIGN KEY(shared_with_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE tabular_review (
        name VARCHAR(255) NOT NULL,
        project_id VARCHAR(36),
        source_contract_ids JSON NOT NULL,
        status VARCHAR(80) NOT NULL,
        metadata_json JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        deleted_at TIMESTAMP WITH TIME ZONE,
        deleted_by_user_id VARCHAR(36),
        legal_hold BOOLEAN NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_tabular_review PRIMARY KEY (id),
        CONSTRAINT fk_tabular_review_project_id_project FOREIGN KEY(project_id) REFERENCES project (id)
    )
    """)
    op.execute("""
    CREATE TABLE workflow_run (
        workflow_id VARCHAR(36) NOT NULL,
        project_id VARCHAR(36),
        status VARCHAR(80) NOT NULL,
        input_contract_ids JSON NOT NULL,
        input_prompt TEXT,
        output JSON,
        tool_calls JSON,
        created_artifacts JSON,
        model_used VARCHAR(160),
        error_message TEXT,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_workflow_run PRIMARY KEY (id),
        CONSTRAINT fk_workflow_run_workflow_id_workflow FOREIGN KEY(workflow_id) REFERENCES workflow (id),
        CONSTRAINT fk_workflow_run_project_id_project FOREIGN KEY(project_id) REFERENCES project (id)
    )
    """)
    op.execute("""
    CREATE TABLE assistant_contract_handle (
        session_id VARCHAR(36) NOT NULL,
        contract_id VARCHAR(36) NOT NULL,
        handle VARCHAR(80) NOT NULL,
        metadata_json JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_assistant_contract_handle PRIMARY KEY (id),
        CONSTRAINT fk_assistant_contract_handle_session_id_assistant_session FOREIGN KEY(session_id) REFERENCES assistant_session (id),
        CONSTRAINT fk_assistant_contract_handle_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id)
    )
    """)
    op.execute("""
    CREATE TABLE assistant_message (
        session_id VARCHAR(36) NOT NULL,
        role VARCHAR(40) NOT NULL,
        content TEXT NOT NULL,
        citations JSON,
        metadata_json JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_assistant_message PRIMARY KEY (id),
        CONSTRAINT fk_assistant_message_session_id_assistant_session FOREIGN KEY(session_id) REFERENCES assistant_session (id)
    )
    """)
    op.execute("""
    CREATE TABLE contract_version (
        contract_id VARCHAR(36) NOT NULL,
        contract_file_id VARCHAR(36) NOT NULL,
        version_number INTEGER NOT NULL,
        storage_object_id VARCHAR(36) NOT NULL,
        text_snapshot_id VARCHAR(36),
        source VARCHAR(80) NOT NULL,
        change_summary TEXT,
        is_authoritative BOOLEAN NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        deleted_at TIMESTAMP WITH TIME ZONE,
        deleted_by_user_id VARCHAR(36),
        legal_hold BOOLEAN NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_contract_version PRIMARY KEY (id),
        CONSTRAINT fk_contract_version_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_contract_version_contract_file_id_contract_file FOREIGN KEY(contract_file_id) REFERENCES contract_file (id),
        CONSTRAINT fk_contract_version_storage_object_id_storage_object FOREIGN KEY(storage_object_id) REFERENCES storage_object (id)
    )
    """)
    op.execute("""
    CREATE TABLE project_contract (
        project_id VARCHAR(36) NOT NULL,
        contract_id VARCHAR(36) NOT NULL,
        folder_id VARCHAR(36),
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_project_contract PRIMARY KEY (id),
        CONSTRAINT uq_project_contract UNIQUE (project_id, contract_id),
        CONSTRAINT fk_project_contract_project_id_project FOREIGN KEY(project_id) REFERENCES project (id),
        CONSTRAINT fk_project_contract_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_project_contract_folder_id_project_folder FOREIGN KEY(folder_id) REFERENCES project_folder (id)
    )
    """)
    op.execute("""
    CREATE TABLE tabular_review_chat (
        tabular_review_id VARCHAR(36) NOT NULL,
        role VARCHAR(40) NOT NULL,
        content TEXT NOT NULL,
        citations JSON,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_tabular_review_chat PRIMARY KEY (id),
        CONSTRAINT fk_tabular_review_chat_tabular_review_id_tabular_review FOREIGN KEY(tabular_review_id) REFERENCES tabular_review (id)
    )
    """)
    op.execute("""
    CREATE TABLE tabular_review_column (
        tabular_review_id VARCHAR(36) NOT NULL,
        name VARCHAR(255) NOT NULL,
        prompt TEXT NOT NULL,
        position INTEGER NOT NULL,
        metadata_json JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_tabular_review_column PRIMARY KEY (id),
        CONSTRAINT fk_tabular_review_column_tabular_review_id_tabular_review FOREIGN KEY(tabular_review_id) REFERENCES tabular_review (id)
    )
    """)
    op.execute("""
    CREATE TABLE approval_request (
        contract_id VARCHAR(36) NOT NULL,
        contract_version_id VARCHAR(36),
        status VARCHAR(80) NOT NULL,
        requested_by_user_id VARCHAR(36) NOT NULL,
        approver_user_id VARCHAR(36),
        approver_role VARCHAR(120),
        due_at TIMESTAMP WITH TIME ZONE,
        metadata_json JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_approval_request PRIMARY KEY (id),
        CONSTRAINT fk_approval_request_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_approval_request_contract_version_id_contract_version FOREIGN KEY(contract_version_id) REFERENCES contract_version (id),
        CONSTRAINT fk_approval_request_requested_by_user_id_user FOREIGN KEY(requested_by_user_id) REFERENCES "user" (id),
        CONSTRAINT fk_approval_request_approver_user_id_user FOREIGN KEY(approver_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE assistant_run (
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
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_assistant_run PRIMARY KEY (id),
        CONSTRAINT fk_assistant_run_session_id_assistant_session FOREIGN KEY(session_id) REFERENCES assistant_session (id),
        CONSTRAINT fk_assistant_run_user_message_id_assistant_message FOREIGN KEY(user_message_id) REFERENCES assistant_message (id),
        CONSTRAINT fk_assistant_run_assistant_message_id_assistant_message FOREIGN KEY(assistant_message_id) REFERENCES assistant_message (id)
    )
    """)
    op.execute("""
    CREATE TABLE contract_edit (
        contract_id VARCHAR(36) NOT NULL,
        contract_version_id VARCHAR(36) NOT NULL,
        edit_type VARCHAR(120) NOT NULL,
        status VARCHAR(80) NOT NULL,
        original_text TEXT,
        replacement_text TEXT,
        rationale TEXT,
        citation JSON,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_contract_edit PRIMARY KEY (id),
        CONSTRAINT fk_contract_edit_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_contract_edit_contract_version_id_contract_version FOREIGN KEY(contract_version_id) REFERENCES contract_version (id)
    )
    """)
    op.execute("""
    CREATE TABLE contract_share (
        contract_id VARCHAR(36) NOT NULL,
        contract_version_id VARCHAR(36),
        token_hash VARCHAR(255) NOT NULL,
        passcode_hash VARCHAR(255),
        access_mode VARCHAR(80) NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE,
        revoked_at TIMESTAMP WITH TIME ZONE,
        download_allowed BOOLEAN NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        deleted_at TIMESTAMP WITH TIME ZONE,
        deleted_by_user_id VARCHAR(36),
        legal_hold BOOLEAN NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_contract_share PRIMARY KEY (id),
        CONSTRAINT fk_contract_share_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_contract_share_contract_version_id_contract_version FOREIGN KEY(contract_version_id) REFERENCES contract_version (id)
    )
    """)
    op.execute("""
    CREATE TABLE contract_text_snapshot (
        contract_id VARCHAR(36) NOT NULL,
        contract_version_id VARCHAR(36) NOT NULL,
        extraction_method VARCHAR(120) NOT NULL,
        extraction_quality_score FLOAT NOT NULL,
        text TEXT NOT NULL,
        page_map JSON,
        ocr_provider VARCHAR(120),
        validation_status VARCHAR(80),
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        deleted_at TIMESTAMP WITH TIME ZONE,
        deleted_by_user_id VARCHAR(36),
        legal_hold BOOLEAN NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_contract_text_snapshot PRIMARY KEY (id),
        CONSTRAINT fk_contract_text_snapshot_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_contract_text_snapshot_contract_version_id_contract_version FOREIGN KEY(contract_version_id) REFERENCES contract_version (id)
    )
    """)
    op.execute("""
    CREATE TABLE obligation (
        contract_id VARCHAR(36) NOT NULL,
        contract_version_id VARCHAR(36),
        owner_user_id VARCHAR(36),
        responsible_party VARCHAR(255),
        obligation_type VARCHAR(160),
        description TEXT NOT NULL,
        due_date DATE,
        recurrence VARCHAR(120),
        status VARCHAR(80) NOT NULL,
        source_citation JSON,
        metadata_json JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        deleted_at TIMESTAMP WITH TIME ZONE,
        deleted_by_user_id VARCHAR(36),
        legal_hold BOOLEAN NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_obligation PRIMARY KEY (id),
        CONSTRAINT fk_obligation_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_obligation_contract_version_id_contract_version FOREIGN KEY(contract_version_id) REFERENCES contract_version (id),
        CONSTRAINT fk_obligation_owner_user_id_user FOREIGN KEY(owner_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE playbook_run (
        playbook_id VARCHAR(36) NOT NULL,
        playbook_version_id VARCHAR(36) NOT NULL,
        contract_id VARCHAR(36) NOT NULL,
        contract_version_id VARCHAR(36) NOT NULL,
        status VARCHAR(80) NOT NULL,
        raw_ai_output JSON,
        validated_output JSON,
        validation_status VARCHAR(80),
        model_name VARCHAR(160),
        token_usage JSON,
        latency_ms INTEGER,
        error_message TEXT,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_playbook_run PRIMARY KEY (id),
        CONSTRAINT fk_playbook_run_playbook_id_playbook FOREIGN KEY(playbook_id) REFERENCES playbook (id),
        CONSTRAINT fk_playbook_run_playbook_version_id_playbook_version FOREIGN KEY(playbook_version_id) REFERENCES playbook_version (id),
        CONSTRAINT fk_playbook_run_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_playbook_run_contract_version_id_contract_version FOREIGN KEY(contract_version_id) REFERENCES contract_version (id)
    )
    """)
    op.execute("""
    CREATE TABLE renewal_event (
        contract_id VARCHAR(36) NOT NULL,
        contract_version_id VARCHAR(36),
        expiration_date DATE,
        notice_date DATE,
        renewal_window_starts_at DATE,
        owner_user_id VARCHAR(36),
        decision VARCHAR(80) NOT NULL,
        decision_note TEXT,
        metadata_json JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_renewal_event PRIMARY KEY (id),
        CONSTRAINT fk_renewal_event_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_renewal_event_contract_version_id_contract_version FOREIGN KEY(contract_version_id) REFERENCES contract_version (id),
        CONSTRAINT fk_renewal_event_owner_user_id_user FOREIGN KEY(owner_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE signature_request (
        contract_id VARCHAR(36) NOT NULL,
        contract_version_id VARCHAR(36) NOT NULL,
        provider VARCHAR(80) NOT NULL,
        provider_envelope_id VARCHAR(255),
        status VARCHAR(80) NOT NULL,
        sent_by_user_id VARCHAR(36),
        sent_at TIMESTAMP WITH TIME ZONE,
        completed_at TIMESTAMP WITH TIME ZONE,
        signed_contract_version_id VARCHAR(36),
        metadata_json JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_signature_request PRIMARY KEY (id),
        CONSTRAINT fk_signature_request_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_signature_request_contract_version_id_contract_version FOREIGN KEY(contract_version_id) REFERENCES contract_version (id),
        CONSTRAINT fk_signature_request_sent_by_user_id_user FOREIGN KEY(sent_by_user_id) REFERENCES "user" (id),
        CONSTRAINT fk_signature_request_signed_contract_version_id_contrac_3865 FOREIGN KEY(signed_contract_version_id) REFERENCES contract_version (id)
    )
    """)
    op.execute("""
    CREATE TABLE tabular_review_cell (
        tabular_review_id VARCHAR(36) NOT NULL,
        column_id VARCHAR(36) NOT NULL,
        contract_id VARCHAR(36) NOT NULL,
        status VARCHAR(80) NOT NULL,
        answer TEXT,
        reasoning TEXT,
        citations JSON,
        confidence VARCHAR(40),
        raw_ai_output JSON,
        error_message TEXT,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_tabular_review_cell PRIMARY KEY (id),
        CONSTRAINT fk_tabular_review_cell_tabular_review_id_tabular_review FOREIGN KEY(tabular_review_id) REFERENCES tabular_review (id),
        CONSTRAINT fk_tabular_review_cell_column_id_tabular_review_column FOREIGN KEY(column_id) REFERENCES tabular_review_column (id),
        CONSTRAINT fk_tabular_review_cell_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id)
    )
    """)
    op.execute("""
    CREATE TABLE approval_decision (
        approval_request_id VARCHAR(36) NOT NULL,
        approver_user_id VARCHAR(36),
        decision VARCHAR(80) NOT NULL,
        comment TEXT,
        decided_at TIMESTAMP WITH TIME ZONE NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_approval_decision PRIMARY KEY (id),
        CONSTRAINT fk_approval_decision_approval_request_id_approval_request FOREIGN KEY(approval_request_id) REFERENCES approval_request (id),
        CONSTRAINT fk_approval_decision_approver_user_id_user FOREIGN KEY(approver_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE approval_token (
        approval_request_id VARCHAR(36) NOT NULL,
        intended_approver_email VARCHAR(320) NOT NULL,
        token_hash VARCHAR(255) NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        used_at TIMESTAMP WITH TIME ZONE,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_approval_token PRIMARY KEY (id),
        CONSTRAINT fk_approval_token_approval_request_id_approval_request FOREIGN KEY(approval_request_id) REFERENCES approval_request (id)
    )
    """)
    op.execute("""
    CREATE TABLE assistant_tool_call (
        session_id VARCHAR(36) NOT NULL,
        assistant_run_id VARCHAR(36),
        message_id VARCHAR(36),
        provider_tool_use_id VARCHAR(255),
        confirmation_id VARCHAR(36),
        tool_name VARCHAR(160) NOT NULL,
        category VARCHAR(80) NOT NULL,
        arguments JSON NOT NULL,
        result JSON,
        status VARCHAR(80) NOT NULL,
        confirmation_required BOOLEAN NOT NULL,
        confirmed_by_user_id VARCHAR(36),
        resource_type VARCHAR(120),
        resource_id VARCHAR(36),
        resource_version VARCHAR(80),
        idempotency_key VARCHAR(255),
        output_schema_name VARCHAR(180),
        started_at TIMESTAMP WITH TIME ZONE,
        finished_at TIMESTAMP WITH TIME ZONE,
        error_message TEXT,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_assistant_tool_call PRIMARY KEY (id),
        CONSTRAINT fk_assistant_tool_call_session_id_assistant_session FOREIGN KEY(session_id) REFERENCES assistant_session (id),
        CONSTRAINT fk_assistant_tool_call_assistant_run_id_assistant_run FOREIGN KEY(assistant_run_id) REFERENCES assistant_run (id),
        CONSTRAINT fk_assistant_tool_call_message_id_assistant_message FOREIGN KEY(message_id) REFERENCES assistant_message (id),
        CONSTRAINT fk_assistant_tool_call_confirmed_by_user_id_user FOREIGN KEY(confirmed_by_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE clause_extraction (
        contract_id VARCHAR(36) NOT NULL,
        contract_version_id VARCHAR(36) NOT NULL,
        text_snapshot_id VARCHAR(36) NOT NULL,
        clause_type VARCHAR(160) NOT NULL,
        heading VARCHAR(500),
        text TEXT NOT NULL,
        start_char INTEGER,
        end_char INTEGER,
        confidence VARCHAR(40),
        is_stale BOOLEAN NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_clause_extraction PRIMARY KEY (id),
        CONSTRAINT fk_clause_extraction_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_clause_extraction_contract_version_id_contract_version FOREIGN KEY(contract_version_id) REFERENCES contract_version (id),
        CONSTRAINT fk_clause_extraction_text_snapshot_id_contract_text_snapshot FOREIGN KEY(text_snapshot_id) REFERENCES contract_text_snapshot (id)
    )
    """)
    op.execute("""
    CREATE TABLE contract_embedding (
        contract_id VARCHAR(36) NOT NULL,
        contract_version_id VARCHAR(36) NOT NULL,
        text_snapshot_id VARCHAR(36) NOT NULL,
        chunk_index INTEGER NOT NULL,
        chunk_text TEXT NOT NULL,
        embedding VECTOR(384),
        metadata_json JSON NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_contract_embedding PRIMARY KEY (id),
        CONSTRAINT fk_contract_embedding_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_contract_embedding_contract_version_id_contract_version FOREIGN KEY(contract_version_id) REFERENCES contract_version (id),
        CONSTRAINT fk_contract_embedding_text_snapshot_id_contract_text_snapshot FOREIGN KEY(text_snapshot_id) REFERENCES contract_text_snapshot (id)
    )
    """)
    op.execute("""
    CREATE TABLE knowledge_node (
        node_type VARCHAR(120) NOT NULL,
        label VARCHAR(500) NOT NULL,
        contract_id VARCHAR(36),
        contract_version_id VARCHAR(36),
        text_snapshot_id VARCHAR(36),
        properties JSON NOT NULL,
        is_stale BOOLEAN NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_knowledge_node PRIMARY KEY (id),
        CONSTRAINT fk_knowledge_node_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_knowledge_node_contract_version_id_contract_version FOREIGN KEY(contract_version_id) REFERENCES contract_version (id),
        CONSTRAINT fk_knowledge_node_text_snapshot_id_contract_text_snapshot FOREIGN KEY(text_snapshot_id) REFERENCES contract_text_snapshot (id)
    )
    """)
    op.execute("""
    CREATE TABLE obligation_reminder (
        obligation_id VARCHAR(36) NOT NULL,
        remind_at DATE NOT NULL,
        sent_at DATE,
        channel VARCHAR(80) NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_obligation_reminder PRIMARY KEY (id),
        CONSTRAINT fk_obligation_reminder_obligation_id_obligation FOREIGN KEY(obligation_id) REFERENCES obligation (id)
    )
    """)
    op.execute("""
    CREATE TABLE playbook_deviation (
        playbook_run_id VARCHAR(36) NOT NULL,
        playbook_rule_id VARCHAR(36),
        contract_id VARCHAR(36) NOT NULL,
        severity VARCHAR(80) NOT NULL,
        clause_type VARCHAR(160),
        issue TEXT NOT NULL,
        suggested_fix TEXT,
        citation JSON,
        status VARCHAR(80) NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_playbook_deviation PRIMARY KEY (id),
        CONSTRAINT fk_playbook_deviation_playbook_run_id_playbook_run FOREIGN KEY(playbook_run_id) REFERENCES playbook_run (id),
        CONSTRAINT fk_playbook_deviation_playbook_rule_id_playbook_rule FOREIGN KEY(playbook_rule_id) REFERENCES playbook_rule (id),
        CONSTRAINT fk_playbook_deviation_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id)
    )
    """)
    op.execute("""
    CREATE TABLE signature_event (
        signature_request_id VARCHAR(36) NOT NULL,
        event_type VARCHAR(120) NOT NULL,
        payload JSON,
        error_message TEXT,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_signature_event PRIMARY KEY (id),
        CONSTRAINT fk_signature_event_signature_request_id_signature_request FOREIGN KEY(signature_request_id) REFERENCES signature_request (id)
    )
    """)
    op.execute("""
    CREATE TABLE signature_recipient (
        signature_request_id VARCHAR(36) NOT NULL,
        name VARCHAR(255) NOT NULL,
        email VARCHAR(320) NOT NULL,
        role VARCHAR(120),
        routing_order VARCHAR(40),
        status VARCHAR(80),
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_signature_recipient PRIMARY KEY (id),
        CONSTRAINT fk_signature_recipient_signature_request_id_signature_request FOREIGN KEY(signature_request_id) REFERENCES signature_request (id)
    )
    """)
    op.execute("""
    CREATE TABLE a_i_confirmation (
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
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_a_i_confirmation PRIMARY KEY (id),
        CONSTRAINT fk_a_i_confirmation_session_id_assistant_session FOREIGN KEY(session_id) REFERENCES assistant_session (id),
        CONSTRAINT fk_a_i_confirmation_assistant_run_id_assistant_run FOREIGN KEY(assistant_run_id) REFERENCES assistant_run (id),
        CONSTRAINT fk_a_i_confirmation_tool_call_id_assistant_tool_call FOREIGN KEY(tool_call_id) REFERENCES assistant_tool_call (id),
        CONSTRAINT fk_a_i_confirmation_decided_by_user_id_user FOREIGN KEY(decided_by_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE a_i_skill_run (
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
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_a_i_skill_run PRIMARY KEY (id),
        CONSTRAINT fk_a_i_skill_run_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_a_i_skill_run_contract_version_id_contract_version FOREIGN KEY(contract_version_id) REFERENCES contract_version (id),
        CONSTRAINT fk_a_i_skill_run_text_snapshot_id_contract_text_snapshot FOREIGN KEY(text_snapshot_id) REFERENCES contract_text_snapshot (id),
        CONSTRAINT fk_a_i_skill_run_job_id_job_run FOREIGN KEY(job_id) REFERENCES job_run (id),
        CONSTRAINT fk_a_i_skill_run_session_id_assistant_session FOREIGN KEY(session_id) REFERENCES assistant_session (id),
        CONSTRAINT fk_a_i_skill_run_assistant_run_id_assistant_run FOREIGN KEY(assistant_run_id) REFERENCES assistant_run (id),
        CONSTRAINT fk_a_i_skill_run_tool_call_id_assistant_tool_call FOREIGN KEY(tool_call_id) REFERENCES assistant_tool_call (id)
    )
    """)
    op.execute("""
    CREATE TABLE knowledge_edge (
        edge_type VARCHAR(120) NOT NULL,
        from_node_id VARCHAR(36) NOT NULL,
        to_node_id VARCHAR(36) NOT NULL,
        contract_id VARCHAR(36),
        contract_version_id VARCHAR(36),
        text_snapshot_id VARCHAR(36),
        properties JSON NOT NULL,
        is_stale BOOLEAN NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_knowledge_edge PRIMARY KEY (id),
        CONSTRAINT fk_knowledge_edge_from_node_id_knowledge_node FOREIGN KEY(from_node_id) REFERENCES knowledge_node (id),
        CONSTRAINT fk_knowledge_edge_to_node_id_knowledge_node FOREIGN KEY(to_node_id) REFERENCES knowledge_node (id),
        CONSTRAINT fk_knowledge_edge_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_knowledge_edge_contract_version_id_contract_version FOREIGN KEY(contract_version_id) REFERENCES contract_version (id),
        CONSTRAINT fk_knowledge_edge_text_snapshot_id_contract_text_snapshot FOREIGN KEY(text_snapshot_id) REFERENCES contract_text_snapshot (id)
    )
    """)
    op.execute("""
    CREATE TABLE playbook_decision (
        playbook_deviation_id VARCHAR(36) NOT NULL,
        decision VARCHAR(80) NOT NULL,
        rationale TEXT,
        decided_by_user_id VARCHAR(36) NOT NULL,
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_playbook_decision PRIMARY KEY (id),
        CONSTRAINT fk_playbook_decision_playbook_deviation_id_playbook_deviation FOREIGN KEY(playbook_deviation_id) REFERENCES playbook_deviation (id),
        CONSTRAINT fk_playbook_decision_decided_by_user_id_user FOREIGN KEY(decided_by_user_id) REFERENCES "user" (id)
    )
    """)
    op.execute("""
    CREATE TABLE a_i_citation (
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
        id VARCHAR(36) NOT NULL,
        org_id VARCHAR(36) NOT NULL,
        created_by_user_id VARCHAR(36),
        updated_by_user_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_a_i_citation PRIMARY KEY (id),
        CONSTRAINT fk_a_i_citation_skill_run_id_a_i_skill_run FOREIGN KEY(skill_run_id) REFERENCES a_i_skill_run (id),
        CONSTRAINT fk_a_i_citation_ai_call_id_a_i_call_log FOREIGN KEY(ai_call_id) REFERENCES a_i_call_log (id),
        CONSTRAINT fk_a_i_citation_assistant_run_id_assistant_run FOREIGN KEY(assistant_run_id) REFERENCES assistant_run (id),
        CONSTRAINT fk_a_i_citation_tool_call_id_assistant_tool_call FOREIGN KEY(tool_call_id) REFERENCES assistant_tool_call (id),
        CONSTRAINT fk_a_i_citation_contract_id_contract FOREIGN KEY(contract_id) REFERENCES contract (id),
        CONSTRAINT fk_a_i_citation_contract_version_id_contract_version FOREIGN KEY(contract_version_id) REFERENCES contract_version (id),
        CONSTRAINT fk_a_i_citation_text_snapshot_id_contract_text_snapshot FOREIGN KEY(text_snapshot_id) REFERENCES contract_text_snapshot (id)
    )
    """)
    op.execute("""
    ALTER TABLE contract ADD CONSTRAINT fk_contract_current_authoritative_version_id FOREIGN KEY(current_authoritative_version_id) REFERENCES contract_version (id)
    """)
    op.execute("""
    ALTER TABLE contract ADD CONSTRAINT fk_contract_current_contract_file_id FOREIGN KEY(current_contract_file_id) REFERENCES contract_file (id)
    """)
    op.execute("""
    ALTER TABLE contract_file ADD CONSTRAINT fk_contract_file_current_version_id FOREIGN KEY(current_version_id) REFERENCES contract_version (id)
    """)
    op.execute("""
    ALTER TABLE contract_version ADD CONSTRAINT fk_contract_version_text_snapshot_id FOREIGN KEY(text_snapshot_id) REFERENCES contract_text_snapshot (id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_call_log_assistant_run_id ON a_i_call_log (assistant_run_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_call_log_job_id ON a_i_call_log (job_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_call_log_org_id ON a_i_call_log (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_call_log_prompt_key ON a_i_call_log (prompt_key)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_call_log_request_id ON a_i_call_log (request_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_call_log_resource_id ON a_i_call_log (resource_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_call_log_resource_type ON a_i_call_log (resource_type)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_call_log_session_id ON a_i_call_log (session_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_call_log_skill_run_id ON a_i_call_log (skill_run_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_call_log_status ON a_i_call_log (status)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_call_log_tool_call_id ON a_i_call_log (tool_call_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_call_log_validation_status ON a_i_call_log (validation_status)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_prompt_version_org_id ON a_i_prompt_version (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_prompt_version_prompt_hash ON a_i_prompt_version (prompt_hash)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_prompt_version_prompt_key ON a_i_prompt_version (prompt_key)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_prompt_version_status ON a_i_prompt_version (status)
    """)
    op.execute("""
    CREATE INDEX ix_admin_setting_key ON admin_setting (key)
    """)
    op.execute("""
    CREATE INDEX ix_admin_setting_org_id ON admin_setting (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_audit_log_action ON audit_log (action)
    """)
    op.execute("""
    CREATE INDEX ix_audit_log_actor_user_id ON audit_log (actor_user_id)
    """)
    op.execute("""
    CREATE INDEX ix_audit_log_org_id ON audit_log (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_audit_log_request_id ON audit_log (request_id)
    """)
    op.execute("""
    CREATE INDEX ix_audit_log_resource_id ON audit_log (resource_id)
    """)
    op.execute("""
    CREATE INDEX ix_audit_log_resource_type ON audit_log (resource_type)
    """)
    op.execute("""
    CREATE UNIQUE INDEX ix_audit_log_row_hash ON audit_log (row_hash)
    """)
    op.execute("""
    CREATE UNIQUE INDEX ix_job_run_idempotency_key ON job_run (idempotency_key)
    """)
    op.execute("""
    CREATE INDEX ix_job_run_job_type ON job_run (job_type)
    """)
    op.execute("""
    CREATE INDEX ix_job_run_org_id ON job_run (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_job_run_resource_id ON job_run (resource_id)
    """)
    op.execute("""
    CREATE INDEX ix_job_run_resource_type ON job_run (resource_type)
    """)
    op.execute("""
    CREATE INDEX ix_job_run_status ON job_run (status)
    """)
    op.execute("""
    CREATE UNIQUE INDEX ix_organization_slug ON organization (slug)
    """)
    op.execute("""
    CREATE UNIQUE INDEX ix_permission_value ON permission (value)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_org_id ON playbook (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_status ON playbook (status)
    """)
    op.execute("""
    CREATE INDEX ix_request_log_org_id ON request_log (org_id)
    """)
    op.execute("""
    CREATE UNIQUE INDEX ix_request_log_request_id ON request_log (request_id)
    """)
    op.execute("""
    CREATE INDEX ix_request_log_route ON request_log (route)
    """)
    op.execute("""
    CREATE INDEX ix_request_log_user_id ON request_log (user_id)
    """)
    op.execute("""
    CREATE INDEX ix_resource_timeline_event_ai_call_id ON resource_timeline_event (ai_call_id)
    """)
    op.execute("""
    CREATE INDEX ix_resource_timeline_event_assistant_run_id ON resource_timeline_event (assistant_run_id)
    """)
    op.execute("""
    CREATE INDEX ix_resource_timeline_event_event_type ON resource_timeline_event (event_type)
    """)
    op.execute("""
    CREATE INDEX ix_resource_timeline_event_job_id ON resource_timeline_event (job_id)
    """)
    op.execute("""
    CREATE INDEX ix_resource_timeline_event_org_id ON resource_timeline_event (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_resource_timeline_event_request_id ON resource_timeline_event (request_id)
    """)
    op.execute("""
    CREATE INDEX ix_resource_timeline_event_resource_id ON resource_timeline_event (resource_id)
    """)
    op.execute("""
    CREATE INDEX ix_resource_timeline_event_resource_type ON resource_timeline_event (resource_type)
    """)
    op.execute("""
    CREATE INDEX ix_resource_timeline_event_skill_run_id ON resource_timeline_event (skill_run_id)
    """)
    op.execute("""
    CREATE INDEX ix_role_name ON role (name)
    """)
    op.execute("""
    CREATE INDEX ix_role_org_id ON role (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_storage_object_org_id ON storage_object (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_storage_object_sha256_hash ON storage_object (sha256_hash)
    """)
    op.execute("""
    CREATE UNIQUE INDEX ix_storage_object_storage_key ON storage_object (storage_key)
    """)
    op.execute("""
    CREATE INDEX ix_usage_record_metric ON usage_record (metric)
    """)
    op.execute("""
    CREATE INDEX ix_usage_record_org_id ON usage_record (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_usage_record_resource_id ON usage_record (resource_id)
    """)
    op.execute("""
    CREATE INDEX ix_usage_record_resource_type ON usage_record (resource_type)
    """)
    op.execute("""
    CREATE INDEX ix_usage_record_user_id ON usage_record (user_id)
    """)
    op.execute("""
    CREATE INDEX ix_user_invitation_email ON user_invitation (email)
    """)
    op.execute("""
    CREATE INDEX ix_user_invitation_org_id ON user_invitation (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_workflow_org_id ON workflow (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_workflow_visibility ON workflow (visibility)
    """)
    op.execute("""
    CREATE INDEX ix_workflow_workflow_type ON workflow (workflow_type)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_version_org_id ON playbook_version (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_version_playbook_id ON playbook_version (playbook_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_version_status ON playbook_version (status)
    """)
    op.execute("""
    CREATE UNIQUE INDEX ix_user_email ON "user" (email)
    """)
    op.execute("""
    CREATE INDEX ix_user_org_id ON "user" (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_user_status ON "user" (status)
    """)
    op.execute("""
    CREATE INDEX ix_api_key_org_id ON api_key (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_api_key_user_id ON api_key (user_id)
    """)
    op.execute("""
    CREATE INDEX ix_approval_routing_rule_org_id ON approval_routing_rule (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_contract_type ON contract (contract_type)
    """)
    op.execute("""
    CREATE INDEX ix_contract_counterparty_name ON contract (counterparty_name)
    """)
    op.execute("""
    CREATE INDEX ix_contract_jurisdiction ON contract (jurisdiction)
    """)
    op.execute("""
    CREATE INDEX ix_contract_lifecycle_stage ON contract (lifecycle_stage)
    """)
    op.execute("""
    CREATE INDEX ix_contract_org_id ON contract (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_owner_user_id ON contract (owner_user_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_risk_level ON contract (risk_level)
    """)
    op.execute("""
    CREATE INDEX ix_notification_channel ON notification (channel)
    """)
    op.execute("""
    CREATE INDEX ix_notification_event_type ON notification (event_type)
    """)
    op.execute("""
    CREATE INDEX ix_notification_org_id ON notification (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_notification_status ON notification (status)
    """)
    op.execute("""
    CREATE INDEX ix_notification_user_id ON notification (user_id)
    """)
    op.execute("""
    CREATE INDEX ix_org_join_request_email ON org_join_request (email)
    """)
    op.execute("""
    CREATE INDEX ix_org_join_request_org_id ON org_join_request (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_org_join_request_requested_domain ON org_join_request (requested_domain)
    """)
    op.execute("""
    CREATE INDEX ix_org_join_request_status ON org_join_request (status)
    """)
    op.execute("""
    CREATE INDEX ix_password_reset_token_org_id ON password_reset_token (org_id)
    """)
    op.execute("""
    CREATE UNIQUE INDEX ix_password_reset_token_token_hash ON password_reset_token (token_hash)
    """)
    op.execute("""
    CREATE INDEX ix_password_reset_token_user_id ON password_reset_token (user_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_rule_clause_type ON playbook_rule (clause_type)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_rule_org_id ON playbook_rule (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_rule_playbook_version_id ON playbook_rule (playbook_version_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_rule_risk_level ON playbook_rule (risk_level)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_rule_rule_type ON playbook_rule (rule_type)
    """)
    op.execute("""
    CREATE INDEX ix_project_org_id ON project (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_project_owner_user_id ON project (owner_user_id)
    """)
    op.execute("""
    CREATE INDEX ix_refresh_token_org_id ON refresh_token (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_refresh_token_user_id ON refresh_token (user_id)
    """)
    op.execute("""
    CREATE INDEX ix_user_approval_decision_decision ON user_approval_decision (decision)
    """)
    op.execute("""
    CREATE INDEX ix_user_approval_decision_org_id ON user_approval_decision (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_user_approval_decision_target_user_id ON user_approval_decision (target_user_id)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_session_org_id ON assistant_session (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_session_session_type ON assistant_session (session_type)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_session_status ON assistant_session (status)
    """)
    op.execute("""
    CREATE INDEX ix_brain_query_org_id ON brain_query (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_brain_query_query_scope ON brain_query (query_scope)
    """)
    op.execute("""
    CREATE INDEX ix_contract_activity_activity_type ON contract_activity (activity_type)
    """)
    op.execute("""
    CREATE INDEX ix_contract_activity_contract_id ON contract_activity (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_activity_org_id ON contract_activity (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_file_contract_id ON contract_file (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_file_org_id ON contract_file (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_party_contract_id ON contract_party (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_party_name ON contract_party (name)
    """)
    op.execute("""
    CREATE INDEX ix_contract_party_org_id ON contract_party (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_stage_history_contract_id ON contract_stage_history (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_stage_history_org_id ON contract_stage_history (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_stage_history_to_stage ON contract_stage_history (to_stage)
    """)
    op.execute("""
    CREATE INDEX ix_project_activity_activity_type ON project_activity (activity_type)
    """)
    op.execute("""
    CREATE INDEX ix_project_activity_org_id ON project_activity (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_project_activity_project_id ON project_activity (project_id)
    """)
    op.execute("""
    CREATE INDEX ix_project_folder_org_id ON project_folder (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_project_folder_project_id ON project_folder (project_id)
    """)
    op.execute("""
    CREATE INDEX ix_project_member_org_id ON project_member (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_project_member_project_id ON project_member (project_id)
    """)
    op.execute("""
    CREATE INDEX ix_project_member_user_id ON project_member (user_id)
    """)
    op.execute("""
    CREATE INDEX ix_project_share_org_id ON project_share (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_project_share_project_id ON project_share (project_id)
    """)
    op.execute("""
    CREATE INDEX ix_project_share_shared_with_user_id ON project_share (shared_with_user_id)
    """)
    op.execute("""
    CREATE INDEX ix_tabular_review_org_id ON tabular_review (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_tabular_review_status ON tabular_review (status)
    """)
    op.execute("""
    CREATE INDEX ix_workflow_run_org_id ON workflow_run (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_workflow_run_status ON workflow_run (status)
    """)
    op.execute("""
    CREATE INDEX ix_workflow_run_workflow_id ON workflow_run (workflow_id)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_contract_handle_contract_id ON assistant_contract_handle (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_contract_handle_org_id ON assistant_contract_handle (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_contract_handle_session_id ON assistant_contract_handle (session_id)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_message_org_id ON assistant_message (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_message_session_id ON assistant_message (session_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_version_contract_file_id ON contract_version (contract_file_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_version_contract_id ON contract_version (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_version_org_id ON contract_version (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_version_source ON contract_version (source)
    """)
    op.execute("""
    CREATE INDEX ix_project_contract_contract_id ON project_contract (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_project_contract_org_id ON project_contract (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_project_contract_project_id ON project_contract (project_id)
    """)
    op.execute("""
    CREATE INDEX ix_tabular_review_chat_org_id ON tabular_review_chat (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_tabular_review_chat_tabular_review_id ON tabular_review_chat (tabular_review_id)
    """)
    op.execute("""
    CREATE INDEX ix_tabular_review_column_org_id ON tabular_review_column (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_tabular_review_column_tabular_review_id ON tabular_review_column (tabular_review_id)
    """)
    op.execute("""
    CREATE INDEX ix_approval_request_approver_user_id ON approval_request (approver_user_id)
    """)
    op.execute("""
    CREATE INDEX ix_approval_request_contract_id ON approval_request (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_approval_request_org_id ON approval_request (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_approval_request_requested_by_user_id ON approval_request (requested_by_user_id)
    """)
    op.execute("""
    CREATE INDEX ix_approval_request_status ON approval_request (status)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_run_org_id ON assistant_run (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_run_session_id ON assistant_run (session_id)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_run_status ON assistant_run (status)
    """)
    op.execute("""
    CREATE INDEX ix_contract_edit_contract_id ON contract_edit (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_edit_contract_version_id ON contract_edit (contract_version_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_edit_org_id ON contract_edit (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_share_contract_id ON contract_share (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_share_org_id ON contract_share (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_text_snapshot_contract_id ON contract_text_snapshot (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_text_snapshot_contract_version_id ON contract_text_snapshot (contract_version_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_text_snapshot_org_id ON contract_text_snapshot (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_obligation_contract_id ON obligation (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_obligation_contract_version_id ON obligation (contract_version_id)
    """)
    op.execute("""
    CREATE INDEX ix_obligation_due_date ON obligation (due_date)
    """)
    op.execute("""
    CREATE INDEX ix_obligation_obligation_type ON obligation (obligation_type)
    """)
    op.execute("""
    CREATE INDEX ix_obligation_org_id ON obligation (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_obligation_owner_user_id ON obligation (owner_user_id)
    """)
    op.execute("""
    CREATE INDEX ix_obligation_status ON obligation (status)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_run_contract_id ON playbook_run (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_run_contract_version_id ON playbook_run (contract_version_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_run_org_id ON playbook_run (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_run_playbook_id ON playbook_run (playbook_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_run_playbook_version_id ON playbook_run (playbook_version_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_run_status ON playbook_run (status)
    """)
    op.execute("""
    CREATE INDEX ix_renewal_event_contract_id ON renewal_event (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_renewal_event_decision ON renewal_event (decision)
    """)
    op.execute("""
    CREATE INDEX ix_renewal_event_expiration_date ON renewal_event (expiration_date)
    """)
    op.execute("""
    CREATE INDEX ix_renewal_event_notice_date ON renewal_event (notice_date)
    """)
    op.execute("""
    CREATE INDEX ix_renewal_event_org_id ON renewal_event (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_renewal_event_renewal_window_starts_at ON renewal_event (renewal_window_starts_at)
    """)
    op.execute("""
    CREATE INDEX ix_signature_request_contract_id ON signature_request (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_signature_request_contract_version_id ON signature_request (contract_version_id)
    """)
    op.execute("""
    CREATE INDEX ix_signature_request_org_id ON signature_request (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_signature_request_provider_envelope_id ON signature_request (provider_envelope_id)
    """)
    op.execute("""
    CREATE INDEX ix_signature_request_status ON signature_request (status)
    """)
    op.execute("""
    CREATE INDEX ix_tabular_review_cell_column_id ON tabular_review_cell (column_id)
    """)
    op.execute("""
    CREATE INDEX ix_tabular_review_cell_contract_id ON tabular_review_cell (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_tabular_review_cell_org_id ON tabular_review_cell (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_tabular_review_cell_status ON tabular_review_cell (status)
    """)
    op.execute("""
    CREATE INDEX ix_tabular_review_cell_tabular_review_id ON tabular_review_cell (tabular_review_id)
    """)
    op.execute("""
    CREATE INDEX ix_approval_decision_approval_request_id ON approval_decision (approval_request_id)
    """)
    op.execute("""
    CREATE INDEX ix_approval_decision_approver_user_id ON approval_decision (approver_user_id)
    """)
    op.execute("""
    CREATE INDEX ix_approval_decision_decision ON approval_decision (decision)
    """)
    op.execute("""
    CREATE INDEX ix_approval_decision_org_id ON approval_decision (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_approval_token_approval_request_id ON approval_token (approval_request_id)
    """)
    op.execute("""
    CREATE INDEX ix_approval_token_intended_approver_email ON approval_token (intended_approver_email)
    """)
    op.execute("""
    CREATE INDEX ix_approval_token_org_id ON approval_token (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_tool_call_assistant_run_id ON assistant_tool_call (assistant_run_id)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_tool_call_idempotency_key ON assistant_tool_call (idempotency_key)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_tool_call_org_id ON assistant_tool_call (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_tool_call_resource_id ON assistant_tool_call (resource_id)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_tool_call_resource_type ON assistant_tool_call (resource_type)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_tool_call_session_id ON assistant_tool_call (session_id)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_tool_call_status ON assistant_tool_call (status)
    """)
    op.execute("""
    CREATE INDEX ix_assistant_tool_call_tool_name ON assistant_tool_call (tool_name)
    """)
    op.execute("""
    CREATE INDEX ix_clause_extraction_clause_type ON clause_extraction (clause_type)
    """)
    op.execute("""
    CREATE INDEX ix_clause_extraction_contract_id ON clause_extraction (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_clause_extraction_contract_version_id ON clause_extraction (contract_version_id)
    """)
    op.execute("""
    CREATE INDEX ix_clause_extraction_org_id ON clause_extraction (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_clause_extraction_text_snapshot_id ON clause_extraction (text_snapshot_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_embedding_contract_id ON contract_embedding (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_embedding_contract_version_id ON contract_embedding (contract_version_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_embedding_org_id ON contract_embedding (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_contract_embedding_text_snapshot_id ON contract_embedding (text_snapshot_id)
    """)
    op.execute("""
    CREATE INDEX ix_knowledge_node_contract_id ON knowledge_node (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_knowledge_node_contract_version_id ON knowledge_node (contract_version_id)
    """)
    op.execute("""
    CREATE INDEX ix_knowledge_node_label ON knowledge_node (label)
    """)
    op.execute("""
    CREATE INDEX ix_knowledge_node_node_type ON knowledge_node (node_type)
    """)
    op.execute("""
    CREATE INDEX ix_knowledge_node_org_id ON knowledge_node (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_obligation_reminder_obligation_id ON obligation_reminder (obligation_id)
    """)
    op.execute("""
    CREATE INDEX ix_obligation_reminder_org_id ON obligation_reminder (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_obligation_reminder_remind_at ON obligation_reminder (remind_at)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_deviation_clause_type ON playbook_deviation (clause_type)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_deviation_contract_id ON playbook_deviation (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_deviation_org_id ON playbook_deviation (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_deviation_playbook_rule_id ON playbook_deviation (playbook_rule_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_deviation_playbook_run_id ON playbook_deviation (playbook_run_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_deviation_severity ON playbook_deviation (severity)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_deviation_status ON playbook_deviation (status)
    """)
    op.execute("""
    CREATE INDEX ix_signature_event_event_type ON signature_event (event_type)
    """)
    op.execute("""
    CREATE INDEX ix_signature_event_org_id ON signature_event (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_signature_event_signature_request_id ON signature_event (signature_request_id)
    """)
    op.execute("""
    CREATE INDEX ix_signature_recipient_email ON signature_recipient (email)
    """)
    op.execute("""
    CREATE INDEX ix_signature_recipient_org_id ON signature_recipient (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_signature_recipient_signature_request_id ON signature_recipient (signature_request_id)
    """)
    op.execute("""
    CREATE INDEX ix_signature_recipient_status ON signature_recipient (status)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_confirmation_assistant_run_id ON a_i_confirmation (assistant_run_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_confirmation_idempotency_key ON a_i_confirmation (idempotency_key)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_confirmation_org_id ON a_i_confirmation (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_confirmation_resource_id ON a_i_confirmation (resource_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_confirmation_resource_type ON a_i_confirmation (resource_type)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_confirmation_session_id ON a_i_confirmation (session_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_confirmation_status ON a_i_confirmation (status)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_confirmation_tool_call_id ON a_i_confirmation (tool_call_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_confirmation_tool_name ON a_i_confirmation (tool_name)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_skill_run_assistant_run_id ON a_i_skill_run (assistant_run_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_skill_run_contract_id ON a_i_skill_run (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_skill_run_contract_version_id ON a_i_skill_run (contract_version_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_skill_run_execution_mode ON a_i_skill_run (execution_mode)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_skill_run_job_id ON a_i_skill_run (job_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_skill_run_org_id ON a_i_skill_run (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_skill_run_prompt_key ON a_i_skill_run (prompt_key)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_skill_run_resource_id ON a_i_skill_run (resource_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_skill_run_resource_type ON a_i_skill_run (resource_type)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_skill_run_session_id ON a_i_skill_run (session_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_skill_run_skill_name ON a_i_skill_run (skill_name)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_skill_run_status ON a_i_skill_run (status)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_skill_run_text_snapshot_id ON a_i_skill_run (text_snapshot_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_skill_run_tool_call_id ON a_i_skill_run (tool_call_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_skill_run_validation_status ON a_i_skill_run (validation_status)
    """)
    op.execute("""
    CREATE INDEX ix_knowledge_edge_contract_id ON knowledge_edge (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_knowledge_edge_contract_version_id ON knowledge_edge (contract_version_id)
    """)
    op.execute("""
    CREATE INDEX ix_knowledge_edge_edge_type ON knowledge_edge (edge_type)
    """)
    op.execute("""
    CREATE INDEX ix_knowledge_edge_from_node_id ON knowledge_edge (from_node_id)
    """)
    op.execute("""
    CREATE INDEX ix_knowledge_edge_org_id ON knowledge_edge (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_knowledge_edge_to_node_id ON knowledge_edge (to_node_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_decision_decision ON playbook_decision (decision)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_decision_org_id ON playbook_decision (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_playbook_decision_playbook_deviation_id ON playbook_decision (playbook_deviation_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_citation_ai_call_id ON a_i_citation (ai_call_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_citation_assistant_run_id ON a_i_citation (assistant_run_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_citation_contract_id ON a_i_citation (contract_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_citation_contract_version_id ON a_i_citation (contract_version_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_citation_org_id ON a_i_citation (org_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_citation_resource_id ON a_i_citation (resource_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_citation_resource_type ON a_i_citation (resource_type)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_citation_skill_run_id ON a_i_citation (skill_run_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_citation_text_snapshot_id ON a_i_citation (text_snapshot_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_citation_tool_call_id ON a_i_citation (tool_call_id)
    """)
    op.execute("""
    CREATE INDEX ix_a_i_citation_validation_status ON a_i_citation (validation_status)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS a_i_citation CASCADE")
    op.execute("DROP TABLE IF EXISTS playbook_decision CASCADE")
    op.execute("DROP TABLE IF EXISTS knowledge_edge CASCADE")
    op.execute("DROP TABLE IF EXISTS a_i_skill_run CASCADE")
    op.execute("DROP TABLE IF EXISTS a_i_confirmation CASCADE")
    op.execute("DROP TABLE IF EXISTS signature_recipient CASCADE")
    op.execute("DROP TABLE IF EXISTS signature_event CASCADE")
    op.execute("DROP TABLE IF EXISTS playbook_deviation CASCADE")
    op.execute("DROP TABLE IF EXISTS obligation_reminder CASCADE")
    op.execute("DROP TABLE IF EXISTS knowledge_node CASCADE")
    op.execute("DROP TABLE IF EXISTS contract_embedding CASCADE")
    op.execute("DROP TABLE IF EXISTS clause_extraction CASCADE")
    op.execute("DROP TABLE IF EXISTS assistant_tool_call CASCADE")
    op.execute("DROP TABLE IF EXISTS approval_token CASCADE")
    op.execute("DROP TABLE IF EXISTS approval_decision CASCADE")
    op.execute("DROP TABLE IF EXISTS tabular_review_cell CASCADE")
    op.execute("DROP TABLE IF EXISTS signature_request CASCADE")
    op.execute("DROP TABLE IF EXISTS renewal_event CASCADE")
    op.execute("DROP TABLE IF EXISTS playbook_run CASCADE")
    op.execute("DROP TABLE IF EXISTS obligation CASCADE")
    op.execute("DROP TABLE IF EXISTS contract_text_snapshot CASCADE")
    op.execute("DROP TABLE IF EXISTS contract_share CASCADE")
    op.execute("DROP TABLE IF EXISTS contract_edit CASCADE")
    op.execute("DROP TABLE IF EXISTS assistant_run CASCADE")
    op.execute("DROP TABLE IF EXISTS approval_request CASCADE")
    op.execute("DROP TABLE IF EXISTS tabular_review_column CASCADE")
    op.execute("DROP TABLE IF EXISTS tabular_review_chat CASCADE")
    op.execute("DROP TABLE IF EXISTS project_contract CASCADE")
    op.execute("DROP TABLE IF EXISTS contract_version CASCADE")
    op.execute("DROP TABLE IF EXISTS assistant_message CASCADE")
    op.execute("DROP TABLE IF EXISTS assistant_contract_handle CASCADE")
    op.execute("DROP TABLE IF EXISTS workflow_run CASCADE")
    op.execute("DROP TABLE IF EXISTS tabular_review CASCADE")
    op.execute("DROP TABLE IF EXISTS project_share CASCADE")
    op.execute("DROP TABLE IF EXISTS project_member CASCADE")
    op.execute("DROP TABLE IF EXISTS project_folder CASCADE")
    op.execute("DROP TABLE IF EXISTS project_activity CASCADE")
    op.execute("DROP TABLE IF EXISTS contract_stage_history CASCADE")
    op.execute("DROP TABLE IF EXISTS contract_party CASCADE")
    op.execute("DROP TABLE IF EXISTS contract_file CASCADE")
    op.execute("DROP TABLE IF EXISTS contract_activity CASCADE")
    op.execute("DROP TABLE IF EXISTS brain_query CASCADE")
    op.execute("DROP TABLE IF EXISTS assistant_session CASCADE")
    op.execute("DROP TABLE IF EXISTS user_role CASCADE")
    op.execute("DROP TABLE IF EXISTS user_approval_decision CASCADE")
    op.execute("DROP TABLE IF EXISTS refresh_token CASCADE")
    op.execute("DROP TABLE IF EXISTS project CASCADE")
    op.execute("DROP TABLE IF EXISTS playbook_rule CASCADE")
    op.execute("DROP TABLE IF EXISTS password_reset_token CASCADE")
    op.execute("DROP TABLE IF EXISTS org_join_request CASCADE")
    op.execute("DROP TABLE IF EXISTS notification CASCADE")
    op.execute("DROP TABLE IF EXISTS contract CASCADE")
    op.execute("DROP TABLE IF EXISTS approval_routing_rule CASCADE")
    op.execute("DROP TABLE IF EXISTS api_key CASCADE")
    op.execute('DROP TABLE IF EXISTS "user" CASCADE')
    op.execute("DROP TABLE IF EXISTS role_permission CASCADE")
    op.execute("DROP TABLE IF EXISTS playbook_version CASCADE")
    op.execute("DROP TABLE IF EXISTS workflow CASCADE")
    op.execute("DROP TABLE IF EXISTS user_invitation CASCADE")
    op.execute("DROP TABLE IF EXISTS usage_record CASCADE")
    op.execute("DROP TABLE IF EXISTS storage_object CASCADE")
    op.execute("DROP TABLE IF EXISTS role CASCADE")
    op.execute("DROP TABLE IF EXISTS resource_timeline_event CASCADE")
    op.execute("DROP TABLE IF EXISTS request_log CASCADE")
    op.execute("DROP TABLE IF EXISTS playbook CASCADE")
    op.execute("DROP TABLE IF EXISTS permission CASCADE")
    op.execute("DROP TABLE IF EXISTS organization CASCADE")
    op.execute("DROP TABLE IF EXISTS job_run CASCADE")
    op.execute("DROP TABLE IF EXISTS audit_log CASCADE")
    op.execute("DROP TABLE IF EXISTS admin_setting CASCADE")
    op.execute("DROP TABLE IF EXISTS a_i_prompt_version CASCADE")
    op.execute("DROP TABLE IF EXISTS a_i_call_log CASCADE")
    op.execute("DROP EXTENSION IF EXISTS vector")
