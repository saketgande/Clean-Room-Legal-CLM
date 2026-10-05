"""Org-unit hierarchy + scoped, hierarchical RBAC resolver.

Adds ``org_unit`` (a self-referencing tree per organization, single root
enforced by a partial unique index) and ``delegation`` (bounded, revocable
delegation of a user's own eligibility). Reshapes the old flat ``user_role``
association table into a fully mapped grant (``UserRoleGrant`` in
app/auth/models.py): adds its own ``id`` PK (a user may now hold the same
role at several org units), ``org_id``/``org_unit_id`` scoping, a
``valid_from``/``valid_to`` window, and actor-tracked soft-delete columns.
Adds ``role.allows_hierarchy_rollup`` (default true, FR-7).

The data migration (step 4) backfills every pre-existing organization with a
"Global" root org unit and re-scopes every pre-existing ``user_role`` row to
that root with no expiry, so no user loses access at cutover (FR-12, AC-9).
Runs inside this same revision — CI applies migrations once, so a follow-up
script would never run against a pre-migration snapshot.

Revision ID: 0042_org_hierarchy_rbac
Revises: 0041_merge_heads
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import context, op

revision = "0042_org_hierarchy_rbac"
down_revision = "0041_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------ #
    # 1. org_unit
    # ------------------------------------------------------------------ #
    op.create_table(
        "org_unit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "parent_id",
            sa.String(36),
            sa.ForeignKey("org_unit.id", ondelete="RESTRICT", name="fk_org_unit_parent_id_org_unit"),
            nullable=True,
        ),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("(parent_id IS NULL OR parent_id <> id)", name="ck_org_unit_no_self_parent"),
    )
    op.create_index("ix_org_unit_org_id", "org_unit", ["org_id"])
    op.create_index("ix_org_unit_parent_id", "org_unit", ["parent_id"])
    op.create_index("ix_org_unit_org_parent", "org_unit", ["org_id", "parent_id"])
    op.create_index(
        "uq_org_unit_single_root",
        "org_unit",
        ["org_id"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL AND deleted_at IS NULL"),
    )

    # ------------------------------------------------------------------ #
    # 2. role.allows_hierarchy_rollup (FR-7, default rolls up)
    # ------------------------------------------------------------------ #
    op.add_column(
        "role",
        sa.Column("allows_hierarchy_rollup", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    # ------------------------------------------------------------------ #
    # 3. user_role widening — all new columns nullable first
    # ------------------------------------------------------------------ #
    op.add_column("user_role", sa.Column("id", sa.String(36), nullable=True))
    op.add_column("user_role", sa.Column("org_id", sa.String(36), nullable=True))
    op.add_column("user_role", sa.Column("org_unit_id", sa.String(36), nullable=True))
    op.add_column("user_role", sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_role", sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_role", sa.Column("created_by_user_id", sa.String(36), nullable=True))
    op.add_column("user_role", sa.Column("updated_by_user_id", sa.String(36), nullable=True))
    op.add_column("user_role", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_role", sa.Column("deleted_by_user_id", sa.String(36), nullable=True))
    op.add_column(
        "user_role", sa.Column("legal_hold", sa.Boolean(), nullable=True, server_default=sa.false())
    )
    op.add_column(
        "user_role",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
    )
    op.add_column(
        "user_role",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
    )

    # ------------------------------------------------------------------ #
    # 4. Data migration (FR-12): one root org_unit per organization, then
    #    backfill every existing user_role row to that org's root, no expiry.
    #
    #    Skipped when generating offline SQL (``alembic upgrade head --sql``):
    #    that mode never has a live connection to read from (bind.execute()
    #    returns None), it only emits the DDL text above for manual review —
    #    there is no data to read or write. The real (online) upgrade always
    #    runs this block. See app/../tests/test_migration_deployability.py.
    # ------------------------------------------------------------------ #
    if not context.is_offline_mode():
        now = datetime.now(UTC)

        org_rows = bind.execute(sa.text("SELECT id FROM organization")).fetchall()
        root_id_by_org: dict[str, str] = {}
        for (org_id,) in org_rows:
            existing_root = bind.execute(
                sa.text(
                    "SELECT id FROM org_unit WHERE org_id = :org_id AND parent_id IS NULL "
                    "AND deleted_at IS NULL"
                ),
                {"org_id": org_id},
            ).fetchone()
            if existing_root is not None:
                root_id_by_org[org_id] = existing_root[0]
                continue
            new_root_id = str(uuid.uuid4())
            bind.execute(
                sa.text(
                    "INSERT INTO org_unit "
                    "(id, org_id, name, parent_id, created_at, updated_at, legal_hold) "
                    "VALUES (:id, :org_id, 'Global', NULL, :now, :now, false)"
                ),
                {"id": new_root_id, "org_id": org_id, "now": now},
            )
            root_id_by_org[org_id] = new_root_id

        # Defensive: drop grants for users that no longer exist (matches the
        # documented migration plan; avoids a NULL org_id/org_unit_id backfill
        # target below).
        bind.execute(sa.text('DELETE FROM user_role WHERE user_id NOT IN (SELECT id FROM "user")'))

        user_role_rows = bind.execute(
            sa.text(
                'SELECT user_id, role_id, "user".org_id AS org_id FROM user_role '
                'JOIN "user" ON "user".id = user_role.user_id'
            )
        ).fetchall()
        for user_id, role_id, org_id in user_role_rows:
            root_id = root_id_by_org.get(org_id)
            if root_id is None:
                # organization row missing / orphaned — should not happen, but
                # skip rather than crash the migration.
                continue
            bind.execute(
                sa.text(
                    "UPDATE user_role SET id = :id, org_id = :org_id, org_unit_id = :org_unit_id, "
                    "valid_from = NULL, valid_to = NULL, created_at = :now, updated_at = :now, "
                    "legal_hold = false "
                    "WHERE user_id = :user_id AND role_id = :role_id"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "org_id": org_id,
                    "org_unit_id": root_id,
                    "now": now,
                    "user_id": user_id,
                    "role_id": role_id,
                },
            )

    # ------------------------------------------------------------------ #
    # 5. Tighten NOT NULLs now that every row is backfilled
    # ------------------------------------------------------------------ #
    op.alter_column("user_role", "id", nullable=False)
    op.alter_column("user_role", "org_id", nullable=False)
    op.alter_column("user_role", "org_unit_id", nullable=False)
    op.alter_column("user_role", "created_at", nullable=False)
    op.alter_column("user_role", "updated_at", nullable=False)
    op.alter_column("user_role", "legal_hold", nullable=False)

    # ------------------------------------------------------------------ #
    # 6. Replace composite PK with the new id PK
    # ------------------------------------------------------------------ #
    op.drop_constraint("pk_user_role", "user_role", type_="primary")
    op.create_primary_key("pk_user_role", "user_role", ["id"])

    # ------------------------------------------------------------------ #
    # 7. FK, indexes, partial-unique scope guard
    # ------------------------------------------------------------------ #
    op.create_foreign_key(
        "fk_user_role_org_unit_id_org_unit",
        "user_role",
        "org_unit",
        ["org_unit_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_user_role_lookup", "user_role", ["user_id", "org_id", "deleted_at"])
    op.create_index(
        "uq_user_role_scope",
        "user_role",
        ["user_id", "role_id", "org_unit_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ------------------------------------------------------------------ #
    # 8. delegation
    # ------------------------------------------------------------------ #
    op.create_table(
        "delegation",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column(
            "delegator_user_id",
            sa.String(36),
            sa.ForeignKey("user.id", name="fk_delegation_delegator_user_id_user"),
            nullable=False,
        ),
        sa.Column(
            "delegate_user_id",
            sa.String(36),
            sa.ForeignKey("user.id", name="fk_delegation_delegate_user_id_user"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            sa.String(36),
            sa.ForeignKey("role.id", name="fk_delegation_role_id_role"),
            nullable=True,
        ),
        sa.Column(
            "org_unit_id",
            sa.String(36),
            sa.ForeignKey("org_unit.id", name="fk_delegation_org_unit_id_org_unit"),
            nullable=True,
        ),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("delegator_user_id <> delegate_user_id", name="ck_delegation_distinct_parties"),
        sa.CheckConstraint("end_date >= start_date", name="ck_delegation_date_order"),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_delegation_status"),
    )
    op.create_index("ix_delegation_org_id", "delegation", ["org_id"])
    op.create_index("ix_delegation_delegator_user_id", "delegation", ["delegator_user_id"])
    op.create_index("ix_delegation_delegate_user_id", "delegation", ["delegate_user_id"])
    op.create_index("ix_delegation_role_id", "delegation", ["role_id"])
    op.create_index("ix_delegation_org_unit_id", "delegation", ["org_unit_id"])
    op.create_index("ix_delegation_status", "delegation", ["status"])
    op.create_index(
        "ix_delegation_delegate_lookup", "delegation", ["delegate_user_id", "status", "end_date"]
    )
    op.create_index("ix_delegation_delegator", "delegation", ["delegator_user_id", "status"])


def downgrade() -> None:
    """Reverse the upgrade.

    NOTE (documented, accepted limitation — dev/CI-only path, not production):
    restoring the old composite ``(user_id, role_id)`` primary key on
    ``user_role`` cannot survive a user holding the same role at multiple org
    units, since that composite key allows only one row per pair. Before
    restoring it we de-duplicate, keeping the most recently created
    (``created_at`` desc, tie-broken by ``id``) live grant per
    ``(user_id, role_id)`` pair and dropping the rest — any grant history at a
    second org unit for the same (user, role) pair is lost on downgrade.
    """
    bind = op.get_bind()

    op.drop_table("delegation")

    op.drop_index("uq_user_role_scope", table_name="user_role")
    op.drop_index("ix_user_role_lookup", table_name="user_role")
    op.drop_constraint("fk_user_role_org_unit_id_org_unit", "user_role", type_="foreignkey")

    op.drop_constraint("pk_user_role", "user_role", type_="primary")

    # De-duplicate: keep one row per (user_id, role_id), the most recent.
    bind.execute(
        sa.text(
            """
            DELETE FROM user_role
            WHERE id NOT IN (
                SELECT DISTINCT ON (user_id, role_id) id
                FROM user_role
                ORDER BY user_id, role_id, created_at DESC, id DESC
            )
            """
        )
    )

    op.create_primary_key("pk_user_role", "user_role", ["user_id", "role_id"])

    op.drop_column("user_role", "legal_hold")
    op.drop_column("user_role", "deleted_by_user_id")
    op.drop_column("user_role", "deleted_at")
    op.drop_column("user_role", "updated_by_user_id")
    op.drop_column("user_role", "created_by_user_id")
    op.drop_column("user_role", "updated_at")
    op.drop_column("user_role", "created_at")
    op.drop_column("user_role", "valid_to")
    op.drop_column("user_role", "valid_from")
    op.drop_column("user_role", "org_unit_id")
    op.drop_column("user_role", "org_id")
    op.drop_column("user_role", "id")

    op.drop_column("role", "allows_hierarchy_rollup")

    op.drop_index("uq_org_unit_single_root", table_name="org_unit")
    op.drop_index("ix_org_unit_org_parent", table_name="org_unit")
    op.drop_index("ix_org_unit_parent_id", table_name="org_unit")
    op.drop_index("ix_org_unit_org_id", table_name="org_unit")
    op.drop_table("org_unit")
