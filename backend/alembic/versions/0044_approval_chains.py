"""Dynamic, condition-driven approval chains (feature 004).

Adds six new tables in the new ``approval_chains`` domain:
``approval_chain_definition``, ``approval_chain_step``,
``approval_chain_step_rule``, ``approval_chain_instance``,
``approval_chain_requirement`` and ``approval_chain_history``. None of these
alter or read the legacy ``app/approvals/`` tables
(``ApprovalRequest``/``ApprovalRoutingRule``/``ApprovalRoutingStep``/
``ApprovalDecision``/``ApprovalToken``) by even one column — FR-21's cutover
boundary is structural, not conditional: no legacy row can ever enter this
feature's code because no legacy row is of this type.

``approval_chain_history`` is truly append-only (FR-10/FR-11/AC-10),
enforced twice: an ORM ``before_update``/``before_delete`` listener pair in
``app/approval_chains/models.py``, and — added here — a Postgres
``BEFORE UPDATE OR DELETE`` trigger, so a raw SQL statement issued outside
the ORM is rejected too. The trigger is Postgres-only (guarded by a dialect
check) so a SQLite-backed dev run still upgrades cleanly; CI and this
repo's test suite run on Postgres, so the trigger is exercised for real.

Step 8 (the FR-22 cutover seed) creates, for every existing organization and
each of the two subject-type modules (``contract``, ``intake_request``), one
``is_default_seeded=true`` chain definition with one sequential step and one
base requirement targeting that org's ``approver`` role (falling back to
``admin`` when no ``approver`` role exists for that org) — see plan.md
"Risks & decisions > ROUTING-RULE DISPOSITION" for why this is a seeded
conservative default rather than an automatic translation of
``ApprovalRoutingRule`` configuration. This is what makes FR-22 safe on day
one: without a definition, the dispatch falls back to the legacy engine
(never auto-approves, but also never satisfies FR-22); with a definition
that has zero steps or zero base requirements, materialization would produce
zero required approvers — a silent auto-approval. Guarded by
``context.is_offline_mode()`` (see migrations 0042/0043 for the identical
pattern) so ``alembic upgrade head --sql`` still emits DDL-only text without
touching a live connection.

No ``ApprovalRequest``/``ApprovalDecision`` row is read, written, migrated or
backfilled by this migration — FR-21.

Revision ID: 0044_approval_chains
Revises: 0043_menu_screen_security
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import context, op

revision = "0044_approval_chains"
down_revision = "0043_menu_screen_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)

    # ------------------------------------------------------------------ #
    # 1. approval_chain_definition
    # ------------------------------------------------------------------ #
    op.create_table(
        "approval_chain_definition",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("module", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default_seeded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "module IN ('contract', 'intake_request')", name="ck_approval_chain_definition_module"
        ),
    )
    op.create_index("ix_approval_chain_definition_org_id", "approval_chain_definition", ["org_id"])
    op.create_index(
        "uq_approval_chain_definition_scope",
        "approval_chain_definition",
        ["org_id", "name", "version"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_approval_chain_definition_active_module",
        "approval_chain_definition",
        ["org_id", "module"],
        unique=True,
        postgresql_where=sa.text("is_active AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_approval_chain_definition_lookup",
        "approval_chain_definition",
        ["org_id", "module", "is_active"],
    )

    # ------------------------------------------------------------------ #
    # 2. approval_chain_step
    # ------------------------------------------------------------------ #
    op.create_table(
        "approval_chain_step",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column(
            "definition_id",
            sa.String(36),
            sa.ForeignKey(
                "approval_chain_definition.id",
                ondelete="CASCADE",
                name="fk_approval_chain_step_definition_id_approval_chain_definition",
            ),
            nullable=False,
        ),
        sa.Column("step_key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("sequence_order", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(20), nullable=False, server_default="approval"),
        sa.Column("approval_mode", sa.String(20), nullable=False, server_default="sequential"),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("step_type IN ('action', 'approval')", name="ck_approval_chain_step_step_type"),
        sa.CheckConstraint(
            "approval_mode IN ('sequential', 'parallel')", name="ck_approval_chain_step_approval_mode"
        ),
    )
    op.create_index("ix_approval_chain_step_org_id", "approval_chain_step", ["org_id"])
    op.create_index("ix_approval_chain_step_definition_id", "approval_chain_step", ["definition_id"])
    op.create_index(
        "uq_approval_chain_step_key",
        "approval_chain_step",
        ["definition_id", "step_key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_approval_chain_step_order",
        "approval_chain_step",
        ["definition_id", "sequence_order"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ------------------------------------------------------------------ #
    # 3. approval_chain_step_rule
    # ------------------------------------------------------------------ #
    op.create_table(
        "approval_chain_step_rule",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column(
            "step_id",
            sa.String(36),
            sa.ForeignKey(
                "approval_chain_step.id",
                ondelete="CASCADE",
                name="fk_approval_chain_step_rule_step_id_approval_chain_step",
            ),
            nullable=False,
        ),
        sa.Column("is_base_requirement", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("condition_expression", sa.JSON(none_as_null=True), nullable=True),
        sa.Column(
            "required_role_id",
            sa.String(36),
            sa.ForeignKey("role.id", ondelete="RESTRICT", name="fk_approval_chain_step_rule_required_role_id_role"),
            nullable=False,
        ),
        sa.Column("sequence_order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("description", sa.String(300), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "(is_base_requirement AND condition_expression IS NULL) "
            "OR (NOT is_base_requirement AND condition_expression IS NOT NULL)",
            name="ck_approval_chain_step_rule_base_xor_condition",
        ),
    )
    op.create_index("ix_approval_chain_step_rule_org_id", "approval_chain_step_rule", ["org_id"])
    op.create_index("ix_approval_chain_step_rule_step_id", "approval_chain_step_rule", ["step_id"])
    op.create_index(
        "ix_approval_chain_step_rule_required_role_id", "approval_chain_step_rule", ["required_role_id"]
    )
    op.create_index(
        "uq_approval_chain_step_rule_base",
        "approval_chain_step_rule",
        ["step_id", "required_role_id"],
        unique=True,
        postgresql_where=sa.text("is_base_requirement AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_approval_chain_step_rule_step",
        "approval_chain_step_rule",
        ["step_id", "is_active", "deleted_at"],
    )

    # ------------------------------------------------------------------ #
    # 4. approval_chain_instance
    # ------------------------------------------------------------------ #
    op.create_table(
        "approval_chain_instance",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column(
            "definition_id",
            sa.String(36),
            sa.ForeignKey(
                "approval_chain_definition.id",
                ondelete="RESTRICT",
                name="fk_approval_chain_instance_definition_id_ac_definition",
            ),
            nullable=False,
        ),
        sa.Column("module", sa.String(40), nullable=False),
        sa.Column("module_record_id", sa.String(36), nullable=False),
        sa.Column(
            "org_unit_id",
            sa.String(36),
            sa.ForeignKey("org_unit.id", ondelete="RESTRICT", name="fk_approval_chain_instance_org_unit_id_org_unit"),
            nullable=False,
        ),
        sa.Column(
            "current_step_id",
            sa.String(36),
            sa.ForeignKey(
                "approval_chain_step.id",
                ondelete="RESTRICT",
                name="fk_approval_chain_instance_current_step_id_approval_chain_step",
            ),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "started_by_user_id",
            sa.String(36),
            sa.ForeignKey("user.id", name="fk_approval_chain_instance_started_by_user_id_user"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("module IN ('contract', 'intake_request')", name="ck_approval_chain_instance_module"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_approval_chain_instance_status",
        ),
    )
    op.create_index("ix_approval_chain_instance_org_id", "approval_chain_instance", ["org_id"])
    op.create_index("ix_approval_chain_instance_definition_id", "approval_chain_instance", ["definition_id"])
    op.create_index("ix_approval_chain_instance_org_unit_id", "approval_chain_instance", ["org_unit_id"])
    op.create_index("ix_approval_chain_instance_current_step_id", "approval_chain_instance", ["current_step_id"])
    op.create_index("ix_approval_chain_instance_status", "approval_chain_instance", ["status"])
    op.create_index(
        "ix_approval_chain_instance_started_by_user_id", "approval_chain_instance", ["started_by_user_id"]
    )
    op.create_index(
        "ix_approval_chain_instance_record",
        "approval_chain_instance",
        ["org_id", "module", "module_record_id"],
    )
    op.create_index(
        "uq_approval_chain_instance_live",
        "approval_chain_instance",
        ["definition_id", "module", "module_record_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND deleted_at IS NULL"),
    )

    # ------------------------------------------------------------------ #
    # 5. approval_chain_requirement
    # ------------------------------------------------------------------ #
    op.create_table(
        "approval_chain_requirement",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column(
            "instance_id",
            sa.String(36),
            sa.ForeignKey(
                "approval_chain_instance.id",
                ondelete="CASCADE",
                name="fk_approval_chain_requirement_instance_id_ac_instance",
            ),
            nullable=False,
        ),
        sa.Column(
            "step_id",
            sa.String(36),
            sa.ForeignKey(
                "approval_chain_step.id",
                ondelete="RESTRICT",
                name="fk_approval_chain_requirement_step_id_approval_chain_step",
            ),
            nullable=False,
        ),
        sa.Column(
            "required_role_id",
            sa.String(36),
            sa.ForeignKey(
                "role.id", ondelete="RESTRICT", name="fk_approval_chain_requirement_required_role_id_role"
            ),
            nullable=False,
        ),
        sa.Column("sequence_order", sa.Integer(), nullable=False),
        sa.Column("is_base_requirement", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("triggered_by_rule_ids", sa.JSON(), nullable=False),
        sa.Column("condition_explanations", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("counts_toward_completion", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "acted_by_user_id",
            sa.String(36),
            sa.ForeignKey("user.id", name="fk_approval_chain_requirement_acted_by_user_id_user"),
            nullable=True,
        ),
        sa.Column(
            "acted_as_role_id",
            sa.String(36),
            sa.ForeignKey("role.id", name="fk_approval_chain_requirement_acted_as_role_id_role"),
            nullable=True,
        ),
        sa.Column(
            "delegated_from_user_id",
            sa.String(36),
            sa.ForeignKey("user.id", name="fk_approval_chain_requirement_delegated_from_user_id_user"),
            nullable=True,
        ),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("is_unfulfillable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("eligible_user_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_approval_chain_requirement_status",
        ),
    )
    op.create_index("ix_approval_chain_requirement_org_id", "approval_chain_requirement", ["org_id"])
    op.create_index("ix_approval_chain_requirement_instance_id", "approval_chain_requirement", ["instance_id"])
    op.create_index("ix_approval_chain_requirement_step_id", "approval_chain_requirement", ["step_id"])
    op.create_index(
        "ix_approval_chain_requirement_required_role_id", "approval_chain_requirement", ["required_role_id"]
    )
    op.create_index(
        "uq_approval_chain_requirement_role",
        "approval_chain_requirement",
        ["instance_id", "step_id", "required_role_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND superseded_at IS NULL"),
    )
    op.create_index(
        "ix_approval_chain_requirement_step",
        "approval_chain_requirement",
        ["instance_id", "step_id", "status"],
    )

    # ------------------------------------------------------------------ #
    # 6. approval_chain_history — truly append-only, reduced column set
    # (no updated_at, no deleted_at, no updated_by_user_id, no legal_hold).
    # ------------------------------------------------------------------ #
    op.create_table(
        "approval_chain_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column(
            "instance_id",
            sa.String(36),
            sa.ForeignKey(
                "approval_chain_instance.id",
                ondelete="RESTRICT",
                name="fk_approval_chain_history_instance_id_approval_chain_instance",
            ),
            nullable=False,
        ),
        sa.Column(
            "step_id",
            sa.String(36),
            sa.ForeignKey(
                "approval_chain_step.id",
                ondelete="RESTRICT",
                name="fk_approval_chain_history_step_id_approval_chain_step",
            ),
            nullable=True,
        ),
        sa.Column(
            "requirement_id",
            sa.String(36),
            sa.ForeignKey(
                "approval_chain_requirement.id",
                ondelete="RESTRICT",
                name="fk_approval_chain_history_requirement_id_ac_requirement",
            ),
            nullable=True,
        ),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column(
            "acted_by_user_id",
            sa.String(36),
            sa.ForeignKey("user.id", name="fk_approval_chain_history_acted_by_user_id_user"),
            nullable=True,
        ),
        sa.Column(
            "acted_as_role_id",
            sa.String(36),
            sa.ForeignKey("role.id", name="fk_approval_chain_history_acted_as_role_id_role"),
            nullable=True,
        ),
        sa.Column(
            "delegated_from_user_id",
            sa.String(36),
            sa.ForeignKey("user.id", name="fk_approval_chain_history_delegated_from_user_id_user"),
            nullable=True,
        ),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "action IN ("
            "'instance_created', 'materialized', 'approved', 'rejected', "
            "'recalculated', 'blocked_no_eligible_approver', "
            "'instance_completed', 'instance_rejected'"
            ")",
            name="ck_approval_chain_history_action",
        ),
    )
    op.create_index("ix_approval_chain_history_org_id", "approval_chain_history", ["org_id"])
    op.create_index("ix_approval_chain_history_instance_id", "approval_chain_history", ["instance_id"])
    op.create_index("ix_approval_chain_history_step_id", "approval_chain_history", ["step_id"])
    op.create_index("ix_approval_chain_history_requirement_id", "approval_chain_history", ["requirement_id"])
    op.create_index("ix_approval_chain_history_action", "approval_chain_history", ["action"])
    op.create_index("ix_approval_chain_history_acted_at", "approval_chain_history", ["acted_at"])

    # ------------------------------------------------------------------ #
    # 7. Append-only DB-layer guard (Postgres only) — the second,
    # independent enforcement layer beyond the ORM event listeners in
    # app/approval_chains/models.py. See that module's ApprovalChainHistory
    # docstring for the full reasoning (FR-10/FR-11/AC-10).
    # ------------------------------------------------------------------ #
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION approval_chain_history_append_only()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'approval_chain_history is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_approval_chain_history_append_only
            BEFORE UPDATE OR DELETE ON approval_chain_history
            FOR EACH ROW EXECUTE FUNCTION approval_chain_history_append_only();
            """
        )

    # ------------------------------------------------------------------ #
    # 8. FR-22 cutover seed: one default chain definition per org per
    # subject type, so the dispatch always has something to route new
    # submissions to on day one. Skipped when generating offline SQL
    # (``alembic upgrade head --sql``): that mode never has a live
    # connection to read from. See migrations 0042/0043 for the identical
    # guard pattern.
    # ------------------------------------------------------------------ #
    if not context.is_offline_mode():
        org_rows = bind.execute(sa.text("SELECT id FROM organization")).fetchall()

        definition_rows: list[dict] = []
        step_rows: list[dict] = []
        rule_rows: list[dict] = []

        for (org_id,) in org_rows:
            approver_role_id = bind.execute(
                sa.text("SELECT id FROM role WHERE org_id = :org_id AND name = 'approver'"),
                {"org_id": org_id},
            ).scalar()
            if approver_role_id is None:
                approver_role_id = bind.execute(
                    sa.text("SELECT id FROM role WHERE org_id = :org_id AND name = 'admin'"),
                    {"org_id": org_id},
                ).scalar()
            if approver_role_id is None:
                # No approver and no admin role for this org — nothing safe
                # to seed a base requirement against. Skip; the dispatch
                # simply finds no active definition for this org and falls
                # through to the legacy engine (audited as
                # approval_chain.reroute_skipped) until an admin configures
                # a chain definition explicitly.
                continue

            for module in ("contract", "intake_request"):
                definition_id = str(uuid.uuid4())
                step_id = str(uuid.uuid4())
                rule_id = str(uuid.uuid4())

                definition_rows.append(
                    {
                        "id": definition_id,
                        "org_id": org_id,
                        "name": f"Default approval chain ({module})",
                        "module": module,
                        "version": 1,
                        "is_active": True,
                        "is_default_seeded": True,
                        "created_at": now,
                        "updated_at": now,
                        "legal_hold": False,
                    }
                )
                step_rows.append(
                    {
                        "id": step_id,
                        "org_id": org_id,
                        "definition_id": definition_id,
                        "step_key": "approval",
                        "name": "Approval",
                        "sequence_order": 1,
                        "step_type": "approval",
                        "approval_mode": "sequential",
                        "created_at": now,
                        "updated_at": now,
                        "legal_hold": False,
                    }
                )
                rule_rows.append(
                    {
                        "id": rule_id,
                        "org_id": org_id,
                        "step_id": step_id,
                        "is_base_requirement": True,
                        "condition_expression": None,
                        "required_role_id": approver_role_id,
                        "sequence_order": 1,
                        "description": None,
                        "is_active": True,
                        "created_at": now,
                        "updated_at": now,
                        "legal_hold": False,
                    }
                )

        if definition_rows:
            op.bulk_insert(
                sa.table(
                    "approval_chain_definition",
                    sa.column("id", sa.String),
                    sa.column("org_id", sa.String),
                    sa.column("name", sa.String),
                    sa.column("module", sa.String),
                    sa.column("version", sa.Integer),
                    sa.column("is_active", sa.Boolean),
                    sa.column("is_default_seeded", sa.Boolean),
                    sa.column("created_at", sa.DateTime),
                    sa.column("updated_at", sa.DateTime),
                    sa.column("legal_hold", sa.Boolean),
                ),
                definition_rows,
            )
            op.bulk_insert(
                sa.table(
                    "approval_chain_step",
                    sa.column("id", sa.String),
                    sa.column("org_id", sa.String),
                    sa.column("definition_id", sa.String),
                    sa.column("step_key", sa.String),
                    sa.column("name", sa.String),
                    sa.column("sequence_order", sa.Integer),
                    sa.column("step_type", sa.String),
                    sa.column("approval_mode", sa.String),
                    sa.column("created_at", sa.DateTime),
                    sa.column("updated_at", sa.DateTime),
                    sa.column("legal_hold", sa.Boolean),
                ),
                step_rows,
            )
            op.bulk_insert(
                sa.table(
                    "approval_chain_step_rule",
                    sa.column("id", sa.String),
                    sa.column("org_id", sa.String),
                    sa.column("step_id", sa.String),
                    sa.column("is_base_requirement", sa.Boolean),
                    sa.column("condition_expression", sa.JSON(none_as_null=True)),
                    sa.column("required_role_id", sa.String),
                    sa.column("sequence_order", sa.Integer),
                    sa.column("description", sa.String),
                    sa.column("is_active", sa.Boolean),
                    sa.column("created_at", sa.DateTime),
                    sa.column("updated_at", sa.DateTime),
                    sa.column("legal_hold", sa.Boolean),
                ),
                rule_rows,
            )


def downgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_approval_chain_history_append_only ON approval_chain_history;")
        op.execute("DROP FUNCTION IF EXISTS approval_chain_history_append_only();")

    op.drop_index("ix_approval_chain_history_acted_at", table_name="approval_chain_history")
    op.drop_index("ix_approval_chain_history_action", table_name="approval_chain_history")
    op.drop_index("ix_approval_chain_history_requirement_id", table_name="approval_chain_history")
    op.drop_index("ix_approval_chain_history_step_id", table_name="approval_chain_history")
    op.drop_index("ix_approval_chain_history_instance_id", table_name="approval_chain_history")
    op.drop_index("ix_approval_chain_history_org_id", table_name="approval_chain_history")
    op.drop_table("approval_chain_history")

    op.drop_index("ix_approval_chain_requirement_step", table_name="approval_chain_requirement")
    op.drop_index("uq_approval_chain_requirement_role", table_name="approval_chain_requirement")
    op.drop_index("ix_approval_chain_requirement_required_role_id", table_name="approval_chain_requirement")
    op.drop_index("ix_approval_chain_requirement_step_id", table_name="approval_chain_requirement")
    op.drop_index("ix_approval_chain_requirement_instance_id", table_name="approval_chain_requirement")
    op.drop_index("ix_approval_chain_requirement_org_id", table_name="approval_chain_requirement")
    op.drop_table("approval_chain_requirement")

    op.drop_index("uq_approval_chain_instance_live", table_name="approval_chain_instance")
    op.drop_index("ix_approval_chain_instance_record", table_name="approval_chain_instance")
    op.drop_index("ix_approval_chain_instance_started_by_user_id", table_name="approval_chain_instance")
    op.drop_index("ix_approval_chain_instance_status", table_name="approval_chain_instance")
    op.drop_index("ix_approval_chain_instance_current_step_id", table_name="approval_chain_instance")
    op.drop_index("ix_approval_chain_instance_org_unit_id", table_name="approval_chain_instance")
    op.drop_index("ix_approval_chain_instance_definition_id", table_name="approval_chain_instance")
    op.drop_index("ix_approval_chain_instance_org_id", table_name="approval_chain_instance")
    op.drop_table("approval_chain_instance")

    op.drop_index("ix_approval_chain_step_rule_step", table_name="approval_chain_step_rule")
    op.drop_index("uq_approval_chain_step_rule_base", table_name="approval_chain_step_rule")
    op.drop_index("ix_approval_chain_step_rule_required_role_id", table_name="approval_chain_step_rule")
    op.drop_index("ix_approval_chain_step_rule_step_id", table_name="approval_chain_step_rule")
    op.drop_index("ix_approval_chain_step_rule_org_id", table_name="approval_chain_step_rule")
    op.drop_table("approval_chain_step_rule")

    op.drop_index("uq_approval_chain_step_order", table_name="approval_chain_step")
    op.drop_index("uq_approval_chain_step_key", table_name="approval_chain_step")
    op.drop_index("ix_approval_chain_step_definition_id", table_name="approval_chain_step")
    op.drop_index("ix_approval_chain_step_org_id", table_name="approval_chain_step")
    op.drop_table("approval_chain_step")

    op.drop_index("ix_approval_chain_definition_lookup", table_name="approval_chain_definition")
    op.drop_index("uq_approval_chain_definition_active_module", table_name="approval_chain_definition")
    op.drop_index("uq_approval_chain_definition_scope", table_name="approval_chain_definition")
    op.drop_index("ix_approval_chain_definition_org_id", table_name="approval_chain_definition")
    op.drop_table("approval_chain_definition")
