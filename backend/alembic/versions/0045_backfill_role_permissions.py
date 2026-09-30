"""Backfill role-permission grants for pre-existing organizations.

Features 002/003/004 each added new permission strings to
``app.core.rbac.ALL_PERMISSIONS``/``DEFAULT_ROLE_PERMISSIONS``
(``org_unit:read``/``delegation:manage``; ``menu:read``/``screen_access:read``/
``screen_access:manage``; ``approval_chain:read``/``manage``/``decide``/
``recalculate``). Those are Python constants only — the actual ``permission``
table rows and their ``role_permission`` links are created by
``app.auth.service.bootstrap_roles``, which runs exactly once, at org setup
(``create_first_admin``). None of the three features' own migrations re-ran
that sync for organizations that already existed before each feature shipped,
so every pre-existing org's built-in roles were silently missing the new
permission strings — discovered via manual end-to-end testing (an existing
admin user got 403s on ``GET /menu-tree`` and ``GET /screen-access/me``
despite holding the ``admin`` role, because ``menu:read``/``screen_access:read``
had never been granted to that role's actual DB rows).

This migration is deliberately **additive only**: for every existing
organization, for every one of its roles whose name matches a
``DEFAULT_ROLE_PERMISSIONS`` key (``admin``/``member``/``legal_reviewer``/
``approver`` — the same ``BUILTIN_ROLE_NAMES`` set ``app.roles.service`` uses),
grant any permission value from that role's default set that isn't already
granted. It never removes a permission from a role. ``bootstrap_roles`` itself
is not reused here because it unconditionally *resets* a role's permission
list to exactly the default set — safe for a freshly-created org, but
destructive against a real production org where an admin may have manually
added or removed permissions on a built-in role beyond the defaults.

Guarded by ``context.is_offline_mode()`` (see migrations 0042/0043/0044 for
the identical pattern) so ``alembic upgrade head --sql`` still emits DDL-only
text (there is none here — this is a pure data migration) without touching a
live connection.

Revision ID: 0045_backfill_role_permissions
Revises: 0044_approval_chains
Create Date: 2026-09-16
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from alembic import context, op

# --- revision identifiers --------------------------------------------------
revision = "0045_backfill_role_permissions"
down_revision = "0044_approval_chains"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not context.is_offline_mode():
        _backfill_role_permissions()


def _backfill_role_permissions() -> None:
    # Imported lazily, inside the guarded branch, exactly like migrations
    # 0042-0044's own data-migration steps — keeps `alembic upgrade head --sql`
    # free of any app-layer import at collection time.
    from app.core.rbac import ALL_PERMISSIONS, DEFAULT_ROLE_PERMISSIONS

    bind = op.get_bind()

    # 1. Ensure every current permission STRING exists as a `permission` row,
    #    org-independent (the table isn't org-scoped).
    existing_values = {
        row[0] for row in bind.execute(sa.text("SELECT value FROM permission")).fetchall()
    }
    for value in sorted(ALL_PERMISSIONS - existing_values):
        bind.execute(
            sa.text(
                "INSERT INTO permission (id, value, created_at, updated_at) "
                "VALUES (:id, :value, now(), now())"
            ),
            {"id": str(uuid.uuid4()), "value": value},
        )

    # Re-read so newly-inserted rows have ids available for step 3's lookup.
    value_to_id = dict(
        bind.execute(sa.text("SELECT value, id FROM permission")).fetchall()
    )

    # 2. For every org and every one of its BUILT-IN-named roles, find which
    #    default permissions that role is currently missing.
    # `role` has no soft-delete column (Role isn't SoftDeleteMixin) — every
    # row is live by construction.
    roles = bind.execute(
        sa.text("SELECT id, org_id, name FROM role WHERE name = ANY(:names)"),
        {"names": list(DEFAULT_ROLE_PERMISSIONS)},
    ).fetchall()

    for role_id, _org_id, role_name in roles:
        wanted = DEFAULT_ROLE_PERMISSIONS[role_name]
        already_granted = {
            row[0]
            for row in bind.execute(
                sa.text(
                    "SELECT permission_id FROM role_permission WHERE role_id = :role_id"
                ),
                {"role_id": role_id},
            ).fetchall()
        }
        missing = wanted - {
            v for v, pid in value_to_id.items() if pid in already_granted
        }
        for value in sorted(missing):
            permission_id = value_to_id[value]
            if permission_id in already_granted:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO role_permission (role_id, permission_id) "
                    "VALUES (:role_id, :permission_id) ON CONFLICT DO NOTHING"
                ),
                {"role_id": role_id, "permission_id": permission_id},
            )


def downgrade() -> None:
    # Deliberately a no-op. This migration only ever ADDS role_permission
    # rows and permission rows that should exist per the current codebase's
    # own permission catalog — there is no "pre-migration state" to restore
    # to that wouldn't itself just be "some built-in roles are missing
    # permissions the code assumes they have," which is the bug this
    # migration exists to fix. Removing the added grants on downgrade would
    # actively reintroduce that bug. Accepted as a one-directional data fix,
    # matching this repo's convention of an intentionally lossy/no-op
    # downgrade for pure data-backfill steps in prior migrations.
    pass
