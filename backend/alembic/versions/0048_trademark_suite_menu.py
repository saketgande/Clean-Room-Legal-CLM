"""Trademark Suite in the DB-driven sidebar, plus the Trademark dashboard screen.

drl moved the sidebar from a hard-coded array to a server-driven menu tree
(``menu_item`` / ``screen`` / ``role_screen_access``, migration
0043_menu_screen_security). That seed mirrored the sidebar as it stood then,
which had no trademark entry, so the Trademark Suite was unreachable from the
menu even though every trademark screen except the dashboard was already in the
``screen`` catalog.

This revision, data only (no DDL):

1. adds the ``trademark_dashboard`` screen (``/trademarks/dashboard``);
2. adds a "Trademark Suite" group under the "Workspace" group, directly after
   Search, with seven screen links: Dashboard, New intake, Document
   extraction, My trademarks, Renewal calendar, Reports, Integration status;
3. gives every role that already has an organization-wide grant on the
   ``trademarks`` screen the same level on ``trademark_dashboard``, so nobody
   who can open My trademarks today loses the dashboard, and nobody gains
   access they did not already have. Grants scoped to a single org unit are
   deliberate admin decisions and are not copied.

Idempotent: every insert is skipped if its row already exists. Skipped in
offline (``--sql``) mode, which never has a live connection to read from (same
guard as 0043_menu_screen_security).

Revision ID: 0048_trademark_suite_menu
Revises: 0047_merge_heads
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import context, op

revision = "0048_trademark_suite_menu"
down_revision = "0047_merge_heads"
branch_labels = None
depends_on = None

_PARENT_GROUP = "Workspace"
_GROUP_LABEL = "Trademark Suite"
_GROUP_ICON = '<path d="M12 15a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"/><path d="M8.5 13.5 6 21l6-2 6 2-2.5-7.5"/>'
# Search is 60 and Delegations is 70 in the 0043 seed; 65 sits right under Search.
_GROUP_SEQ = 65

_DASHBOARD_SCREEN = (
    "trademark_dashboard",
    "Trademark dashboard",
    "Workspace",
    "/trademarks/dashboard",
)

# label, screen code, icon, sequence_order
_CHILDREN: list[tuple[str, str, str, int]] = [
    (
        "Dashboard",
        "trademark_dashboard",
        (
            '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/>'
            '<rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>'
        ),
        10,
    ),
    (
        "New intake",
        "trademark_intake",
        (
            '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>'
            '<path d="M14 3v5h5M12 11v6M9 14h6"/>'
        ),
        20,
    ),
    (
        "Document extraction",
        "trademark_extract",
        (
            '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/>'
            '<path d="M7 12h10"/>'
        ),
        30,
    ),
    (
        "My trademarks",
        "trademarks",
        (
            '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/>'
            '<path d="M7 7h.01"/>'
        ),
        40,
    ),
    (
        "Renewal calendar",
        "trademark_calendar",
        '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
        50,
    ),
    ("Reports", "trademark_reports", '<path d="M18 20V10M12 20V4M6 20v-6"/>', 60),
    (
        "Integration status",
        "trademark_integrations",
        '<path d="M12 22v-5M9 8V2M15 8V2M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z"/>',
        70,
    ),
]


def _screen_id(bind, code: str) -> str | None:
    return bind.execute(
        sa.text("SELECT id FROM screen WHERE code = :code AND deleted_at IS NULL"), {"code": code}
    ).scalar()


def upgrade() -> None:
    if context.is_offline_mode():
        return

    bind = op.get_bind()
    now = datetime.now(UTC)

    # 1. The dashboard screen.
    code, name, module, route_path = _DASHBOARD_SCREEN
    dashboard_id = _screen_id(bind, code)
    if dashboard_id is None:
        dashboard_id = str(uuid.uuid4())
        bind.execute(
            sa.text(
                "INSERT INTO screen (id, code, name, module, route_path, created_at, updated_at, legal_hold) "
                "VALUES (:id, :code, :name, :module, :route_path, :now, :now, FALSE)"
            ),
            {
                "id": dashboard_id,
                "code": code,
                "name": name,
                "module": module,
                "route_path": route_path,
                "now": now,
            },
        )

    # 2. The "Trademark Suite" menu group under Workspace, and its seven links.
    workspace_id = bind.execute(
        sa.text(
            "SELECT id FROM menu_item WHERE menu_type = 'group' AND parent_id IS NULL "
            "AND label = :label AND deleted_at IS NULL"
        ),
        {"label": _PARENT_GROUP},
    ).scalar()
    if workspace_id is None:
        raise RuntimeError(
            f"menu group {_PARENT_GROUP!r} not found - cannot attach the {_GROUP_LABEL!r} group "
            "(was the menu tree seeded by 0043_menu_screen_security?)"
        )

    group_id = bind.execute(
        sa.text(
            "SELECT id FROM menu_item WHERE menu_type = 'group' AND parent_id = :parent "
            "AND label = :label AND deleted_at IS NULL"
        ),
        {"parent": workspace_id, "label": _GROUP_LABEL},
    ).scalar()
    if group_id is None:
        group_id = str(uuid.uuid4())
        bind.execute(
            sa.text(
                "INSERT INTO menu_item (id, parent_id, label, icon, sequence_order, menu_type, "
                "screen_id, created_at, updated_at, legal_hold) "
                "VALUES (:id, :parent, :label, :icon, :seq, 'group', NULL, :now, :now, FALSE)"
            ),
            {
                "id": group_id,
                "parent": workspace_id,
                "label": _GROUP_LABEL,
                "icon": _GROUP_ICON,
                "seq": _GROUP_SEQ,
                "now": now,
            },
        )

    for label, screen_code, icon, seq in _CHILDREN:
        screen_id = _screen_id(bind, screen_code)
        if screen_id is None:
            raise RuntimeError(f"screen {screen_code!r} not found - cannot add menu link {label!r}")
        exists = bind.execute(
            sa.text(
                "SELECT 1 FROM menu_item WHERE parent_id = :parent AND screen_id = :screen "
                "AND deleted_at IS NULL"
            ),
            {"parent": group_id, "screen": screen_id},
        ).scalar()
        if exists:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO menu_item (id, parent_id, label, icon, sequence_order, menu_type, "
                "screen_id, created_at, updated_at, legal_hold) "
                "VALUES (:id, :parent, :label, :icon, :seq, 'screen_link', :screen, :now, :now, FALSE)"
            ),
            {
                "id": str(uuid.uuid4()),
                "parent": group_id,
                "label": label,
                "icon": icon,
                "seq": seq,
                "screen": screen_id,
                "now": now,
            },
        )

    # 3. Dashboard access follows My trademarks access.
    source_rows = bind.execute(
        sa.text(
            "SELECT src.org_id, src.role_id, src.max_action_level_id "
            "FROM role_screen_access src JOIN screen s ON s.id = src.screen_id "
            "WHERE s.code = 'trademarks' AND src.org_unit_id IS NULL AND src.deleted_at IS NULL"
        )
    ).fetchall()
    for org_id, role_id, level_id in source_rows:
        already = bind.execute(
            sa.text(
                "SELECT 1 FROM role_screen_access WHERE role_id = :role AND screen_id = :screen "
                "AND org_unit_id IS NULL AND deleted_at IS NULL"
            ),
            {"role": role_id, "screen": dashboard_id},
        ).scalar()
        if already:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO role_screen_access (id, org_id, role_id, screen_id, org_unit_id, "
                "max_action_level_id, created_at, updated_at, legal_hold) "
                "VALUES (:id, :org, :role, :screen, NULL, :level, :now, :now, FALSE)"
            ),
            {
                "id": str(uuid.uuid4()),
                "org": org_id,
                "role": role_id,
                "screen": dashboard_id,
                "level": level_id,
                "now": now,
            },
        )


def downgrade() -> None:
    if context.is_offline_mode():
        return

    bind = op.get_bind()

    group_ids = [
        row[0]
        for row in bind.execute(
            sa.text(
                "SELECT g.id FROM menu_item g JOIN menu_item p ON p.id = g.parent_id "
                "WHERE g.menu_type = 'group' AND g.label = :group AND p.label = :parent "
                "AND p.parent_id IS NULL"
            ),
            {"group": _GROUP_LABEL, "parent": _PARENT_GROUP},
        ).fetchall()
    ]
    for group_id in group_ids:
        bind.execute(sa.text("DELETE FROM menu_item WHERE parent_id = :id"), {"id": group_id})
        bind.execute(sa.text("DELETE FROM menu_item WHERE id = :id"), {"id": group_id})

    dashboard_id = bind.execute(
        sa.text("SELECT id FROM screen WHERE code = :code"), {"code": _DASHBOARD_SCREEN[0]}
    ).scalar()
    if dashboard_id is not None:
        bind.execute(
            sa.text("DELETE FROM role_screen_access WHERE screen_id = :id"), {"id": dashboard_id}
        )
        bind.execute(sa.text("DELETE FROM screen WHERE id = :id"), {"id": dashboard_id})
