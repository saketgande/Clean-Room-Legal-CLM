"""Menu/screen-level security (VIEW/ADD/EDIT/DELETE) with dynamic menu
visibility.

Adds four new tables: ``action_level`` (4 seeded reference rows), ``screen``
(the platform-wide catalog of Next.js page routes, FR-1 — 39 seeded rows),
``menu_item`` (the navigable menu tree, FR-2 — 28 seeded rows mirroring
``frontend/src/components/aegis-rail.tsx``'s current ``GROUPS`` array exactly)
and ``role_screen_access`` (the only org-scoped table of the four — a role's
maximum action level on a screen, optionally scoped to an org unit, FR-5/
FR-18).

The FR-26 cutover backfill (step 5) seeds, for every existing organization's
every existing role and every seeded screen, an organization-wide
(``org_unit_id IS NULL``) ``role_screen_access`` row that preserves that
role's CURRENT effective access as of immediately before cutover: a role
whose existing permission grants are equivalent to add/edit/delete on that
screen is seeded at DELETE, a role with read-only-equivalent access is seeded
at VIEW, and a role with neither receives no grant — so no user's day-one
access changes as a direct result of this feature shipping. It also seeds the
built-in ``admin`` role's non-revocable DELETE-level grant on the
``screen_access`` screen (FR-25's bootstrap lock, enforced going forward by a
service-layer guard added in a later task).

``SEED_MAP`` is a frozen, literal snapshot of the pre-cutover permission model
— it must NOT import ``app.core.rbac`` (whose constants will keep evolving)
so this migration never drifts from what it looked like at the moment of
cutover.

Runs entirely inside this one revision — CI applies migrations once, so a
follow-up script would never run against a pre-migration snapshot. Guarded by
``context.is_offline_mode()`` (see migration 0042) so ``alembic upgrade head
--sql`` still emits DDL-only text without touching a live connection.

Revision ID: 0043_menu_screen_security
Revises: 0042_org_hierarchy_rbac
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import context, op

revision = "0043_menu_screen_security"
down_revision = "0042_org_hierarchy_rbac"
branch_labels = None
depends_on = None


# --------------------------------------------------------------------- #
# Screen catalog (39 rows) — code, name, module, route_path, read perms,
# write perms. write=None means "ALL" (no permission gate today, so every
# role can already do everything -> seed DELETE to preserve that exactly).
# --------------------------------------------------------------------- #
_ALL = None  # sentinel: no permission gate today -> seed DELETE unconditionally

_TRADEMARK_READ = frozenset({"trademark:read", "trademark:search", "trademark:integrations_manage"})
_TRADEMARK_WRITE = frozenset({"trademark:create", "trademark:update", "trademark:extract"})
_PLAYBOOK_READ = frozenset({"playbook:read"})
_PLAYBOOK_WRITE = frozenset(
    {"playbook:create", "playbook:update", "playbook:delete", "playbook:run", "playbook:publish"}
)

SCREEN_CATALOG: list[tuple[str, str, str, str, frozenset[str] | None, frozenset[str] | None]] = [
    # code, name, module, route_path, read_perms, write_perms (None == ALL)
    ("home", "Ask Aegis", "Intelligence", "/", frozenset(), _ALL),
    ("my_work", "My Work", "Workspace", "/my-work", frozenset(), _ALL),
    ("notifications", "Notifications", "Workspace", "/notifications", frozenset(), _ALL),
    ("search", "Search", "Workspace", "/search", frozenset(), _ALL),
    ("prompts", "Prompt Library", "Intelligence", "/prompts", frozenset(), _ALL),
    (
        "assistant",
        "Assistant",
        "Intelligence",
        "/assistant",
        frozenset({"assistant:use"}),
        frozenset({"assistant:use_ai_tools"}),
    ),
    ("contract_brain", "Contract Brain", "Intelligence", "/brain", frozenset({"contract:read"}), frozenset()),
    (
        "tabular_reviews",
        "Tabular Review",
        "Intelligence",
        "/tabular-reviews",
        frozenset({"contract:read"}),
        frozenset({"contract:update"}),
    ),
    (
        "tabular_review_detail",
        "Tabular Review detail",
        "Intelligence",
        "/tabular-reviews/[id]",
        frozenset({"contract:read"}),
        frozenset({"contract:update"}),
    ),
    ("playbooks", "Playbooks", "Intelligence", "/playbooks", _PLAYBOOK_READ, _PLAYBOOK_WRITE),
    ("playbook_detail", "Playbook detail", "Intelligence", "/playbooks/[id]", _PLAYBOOK_READ, _PLAYBOOK_WRITE),
    ("playbook_builder", "Playbook builder", "Intelligence", "/playbooks/build", _PLAYBOOK_READ, _PLAYBOOK_WRITE),
    (
        "contracts",
        "Contracts",
        "Workspace",
        "/contracts",
        frozenset({"contract:read"}),
        frozenset({"contract:create", "contract:update"}),
    ),
    (
        "contract_detail",
        "Contract detail",
        "Workspace",
        "/contracts/[id]",
        frozenset({"contract:read"}),
        frozenset({"contract:create", "contract:update"}),
    ),
    (
        "matters",
        "Matters",
        "Workspace",
        "/matters",
        frozenset({"project:read"}),
        frozenset({"project:create", "project:update", "project:delete", "project:share"}),
    ),
    (
        "matter_detail",
        "Matter detail",
        "Workspace",
        "/matters/[id]",
        frozenset({"project:read"}),
        frozenset({"project:create", "project:update", "project:delete", "project:share"}),
    ),
    (
        "intake",
        "Legal Intake",
        "Workspace",
        "/intake",
        frozenset({"intake:read"}),
        frozenset({"intake:create", "intake:update"}),
    ),
    (
        "delegations",
        "Delegations",
        "Workspace",
        "/delegations",
        frozenset({"delegation:manage"}),
        frozenset({"delegation:manage"}),
    ),
    (
        "approvals",
        "Approvals",
        "Lifecycle",
        "/approvals",
        frozenset({"approval:read"}),
        frozenset({"approval:decide", "approval:admin"}),
    ),
    (
        "signatures",
        "Signatures",
        "Lifecycle",
        "/signatures",
        frozenset({"contract:read"}),
        frozenset({"contract:sign"}),
    ),
    (
        "obligations",
        "Obligations",
        "Lifecycle",
        "/obligations",
        frozenset({"obligation:read"}),
        frozenset({"obligation:update"}),
    ),
    ("sla", "SLA", "Lifecycle", "/sla", frozenset({"intake:read"}), frozenset({"intake:update"})),
    (
        "notices",
        "Notices",
        "Lifecycle",
        "/notices",
        frozenset({"notice:read"}),
        frozenset({"notice:create", "notice:update", "admin_panel:access"}),
    ),
    (
        "notice_detail",
        "Notice detail",
        "Lifecycle",
        "/notices/[id]",
        frozenset({"notice:read"}),
        frozenset({"notice:create", "notice:update", "admin_panel:access"}),
    ),
    ("renewals", "Renewals", "Lifecycle", "/renewals", frozenset({"contract:read"}), frozenset({"contract:renew"})),
    ("trademarks", "Trademarks", "Workspace", "/trademarks", _TRADEMARK_READ, _TRADEMARK_WRITE),
    (
        "trademark_detail",
        "Trademark detail",
        "Workspace",
        "/trademarks/[id]",
        _TRADEMARK_READ,
        _TRADEMARK_WRITE,
    ),
    (
        "trademark_calendar",
        "Trademark calendar",
        "Workspace",
        "/trademarks/calendar",
        _TRADEMARK_READ,
        _TRADEMARK_WRITE,
    ),
    (
        "trademark_intake",
        "Trademark intake",
        "Workspace",
        "/trademarks/intake",
        _TRADEMARK_READ,
        _TRADEMARK_WRITE,
    ),
    (
        "trademark_extract",
        "Trademark extract",
        "Workspace",
        "/trademarks/extract",
        _TRADEMARK_READ,
        _TRADEMARK_WRITE,
    ),
    (
        "trademark_integrations",
        "Trademark integrations",
        "Workspace",
        "/trademarks/integrations",
        _TRADEMARK_READ,
        _TRADEMARK_WRITE,
    ),
    (
        "trademark_reports",
        "Trademark reports",
        "Workspace",
        "/trademarks/reports",
        _TRADEMARK_READ,
        _TRADEMARK_WRITE,
    ),
    (
        "workflow_builder",
        "Workflows",
        "Admin",
        "/workflow-builder",
        frozenset({"workflow:read"}),
        frozenset({"workflow:create", "workflow:update", "workflow:share"}),
    ),
    (
        "workflow_builder_detail",
        "Workflow detail",
        "Admin",
        "/workflow-builder/[id]",
        frozenset({"workflow:read"}),
        frozenset({"workflow:create", "workflow:update", "workflow:share"}),
    ),
    (
        "ai_usage",
        "AI Usage & Cost",
        "Admin",
        "/ai-usage",
        frozenset({"admin_panel:access"}),
        frozenset({"admin_panel:access"}),
    ),
    (
        "jobs",
        "Background Jobs",
        "Admin",
        "/jobs",
        frozenset({"admin_panel:access"}),
        frozenset({"admin_panel:access"}),
    ),
    (
        "admin",
        "Roles & teams",
        "Admin",
        "/admin",
        frozenset({"admin_panel:access"}),
        frozenset({"admin_panel:access"}),
    ),
    (
        "org_structure",
        "Org structure",
        "Admin",
        "/org-structure",
        frozenset({"admin_panel:access"}),
        frozenset({"admin_panel:access"}),
    ),
    (
        "screen_access",
        "Screen access",
        "Admin",
        "/screen-access",
        frozenset({"admin_panel:access"}),
        frozenset({"admin_panel:access"}),
    ),
]

# --------------------------------------------------------------------- #
# Menu tree seed (4 groups + 24 screen_link rows = 28 rows), mirroring
# frontend/src/components/aegis-rail.tsx's current GROUPS array exactly
# (label, icon SVG path data, ordering). Screens without a menu node (detail
# / sub-page / not-yet-linked screens) are intentionally absent here per
# plan.md, but still exist as full Screen rows above (FR-3).
# --------------------------------------------------------------------- #
MENU_GROUPS: list[tuple[str, int, list[tuple[str, str, str, int]]]] = [
    (
        "Workspace",
        10,
        [
            (
                "Legal Intake",
                "intake",
                '<path d="M3 7l9 6 9-6"/><rect x="3" y="5" width="18" height="14" rx="2"/>',
                10,
            ),
            (
                "My Work",
                "my_work",
                '<path d="M9 11l3 3 8-8"/><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"/>',
                20,
            ),
            (
                "Notifications",
                "notifications",
                '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
                30,
            ),
            (
                "Contracts",
                "contracts",
                '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
                40,
            ),
            (
                "Matters",
                "matters",
                '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
                50,
            ),
            ("Search", "search", '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>', 60),
            (
                "Delegations",
                "delegations",
                '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.59 13.51l6.83 3.98"/><path d="M15.41 6.51L8.59 10.49"/>',
                70,
            ),
        ],
    ),
    (
        "Intelligence",
        20,
        [
            (
                "Ask Aegis",
                "home",
                '<rect x="3" y="4" width="18" height="14" rx="2"/><path d="M8 21h8M12 18v3"/><circle cx="9" cy="11" r="1"/><circle cx="15" cy="11" r="1"/>',
                10,
            ),
            (
                "Contract Brain",
                "contract_brain",
                '<path d="M12 5a3 3 0 0 0-6 0 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 0M12 5a3 3 0 0 1 6 0 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 0M12 5v14"/>',
                20,
            ),
            (
                "Tabular Review",
                "tabular_reviews",
                '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/>',
                30,
            ),
            (
                "Playbooks",
                "playbooks",
                '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
                40,
            ),
            (
                "Prompt Library",
                "prompts",
                '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
                50,
            ),
        ],
    ),
    (
        "Lifecycle",
        30,
        [
            (
                "Approvals",
                "approvals",
                '<path d="M9 11l3 3 8-8"/><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"/>',
                10,
            ),
            (
                "Signatures",
                "signatures",
                '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
                20,
            ),
            (
                "Obligations",
                "obligations",
                '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
                30,
            ),
            ("SLA", "sla", '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', 40),
            (
                "Notices",
                "notices",
                '<path d="M10.3 3.9 2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
                50,
            ),
            (
                "Renewals",
                "renewals",
                '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v5h-5"/>',
                60,
            ),
        ],
    ),
    (
        "Admin",
        40,
        [
            (
                "AI Usage & Cost",
                "ai_usage",
                '<path d="M3 3v18h18"/><path d="M7 15l3-4 3 3 5-7"/>',
                10,
            ),
            (
                "Background Jobs",
                "jobs",
                '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
                20,
            ),
            (
                "Workflows",
                "workflow_builder",
                '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="12" r="2.5"/><path d="M8.5 6H14a2 2 0 0 1 2 2v2M8.5 18H14a2 2 0 0 0 2-2v-2"/>',
                30,
            ),
            (
                "Roles & teams",
                "admin",
                '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
                40,
            ),
            (
                "Org structure",
                "org_structure",
                '<rect x="16" y="16" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="9" y="2" width="6" height="6" rx="1"/><path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"/><path d="M12 12V8"/>',
                50,
            ),
            (
                # New item: FR-23's admin screen needs to be reachable. Icon is a
                # lucide-style shield-check path (not copied from today's rail,
                # since this menu entry does not exist there yet).
                "Screen access",
                "screen_access",
                '<path d="M20 13c0 5-3.5 7.5-7.35 8.95a1 1 0 0 1-.6.05C8.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
                60,
            ),
        ],
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)

    # ------------------------------------------------------------------ #
    # 1. action_level
    # ------------------------------------------------------------------ #
    op.create_table(
        "action_level",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("code IN ('VIEW', 'ADD', 'EDIT', 'DELETE')", name="ck_action_level_code"),
        sa.CheckConstraint("rank BETWEEN 1 AND 4", name="ck_action_level_rank"),
    )
    op.create_index("uq_action_level_code", "action_level", ["code"], unique=True)
    op.create_index("uq_action_level_rank", "action_level", ["rank"], unique=True)

    level_ids: dict[str, str] = {code: str(uuid.uuid4()) for code in ("VIEW", "ADD", "EDIT", "DELETE")}
    op.bulk_insert(
        sa.table(
            "action_level",
            sa.column("id", sa.String),
            sa.column("code", sa.String),
            sa.column("rank", sa.Integer),
            sa.column("created_at", sa.DateTime),
            sa.column("updated_at", sa.DateTime),
            sa.column("legal_hold", sa.Boolean),
        ),
        [
            {
                "id": level_ids[code],
                "code": code,
                "rank": rank,
                "created_at": now,
                "updated_at": now,
                "legal_hold": False,
            }
            for code, rank in (("VIEW", 1), ("ADD", 2), ("EDIT", 3), ("DELETE", 4))
        ],
    )

    # ------------------------------------------------------------------ #
    # 2. screen
    # ------------------------------------------------------------------ #
    op.create_table(
        "screen",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("module", sa.String(80), nullable=False),
        sa.Column("route_path", sa.String(200), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("uq_screen_code", "screen", ["code"], unique=True)
    op.create_index("uq_screen_route_path", "screen", ["route_path"], unique=True)

    screen_ids: dict[str, str] = {row[0]: str(uuid.uuid4()) for row in SCREEN_CATALOG}
    op.bulk_insert(
        sa.table(
            "screen",
            sa.column("id", sa.String),
            sa.column("code", sa.String),
            sa.column("name", sa.String),
            sa.column("module", sa.String),
            sa.column("route_path", sa.String),
            sa.column("created_at", sa.DateTime),
            sa.column("updated_at", sa.DateTime),
            sa.column("legal_hold", sa.Boolean),
        ),
        [
            {
                "id": screen_ids[code],
                "code": code,
                "name": name,
                "module": module,
                "route_path": route_path,
                "created_at": now,
                "updated_at": now,
                "legal_hold": False,
            }
            for code, name, module, route_path, _read, _write in SCREEN_CATALOG
        ],
    )

    # ------------------------------------------------------------------ #
    # 3. menu_item
    # ------------------------------------------------------------------ #
    op.create_table(
        "menu_item",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "parent_id",
            sa.String(36),
            sa.ForeignKey("menu_item.id", ondelete="RESTRICT", name="fk_menu_item_parent_id_menu_item"),
            nullable=True,
        ),
        sa.Column("label", sa.String(160), nullable=False),
        sa.Column("icon", sa.Text(), nullable=True),
        sa.Column("sequence_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("menu_type", sa.String(20), nullable=False),
        sa.Column(
            "screen_id",
            sa.String(36),
            sa.ForeignKey("screen.id", ondelete="RESTRICT", name="fk_menu_item_screen_id_screen"),
            nullable=True,
        ),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("menu_type IN ('group', 'screen_link')", name="ck_menu_item_menu_type"),
        sa.CheckConstraint(
            "(menu_type = 'screen_link' AND screen_id IS NOT NULL) "
            "OR (menu_type = 'group' AND screen_id IS NULL)",
            name="ck_menu_item_screen_link",
        ),
        sa.CheckConstraint("(parent_id IS NULL OR parent_id <> id)", name="ck_menu_item_no_self_parent"),
    )
    op.create_index("ix_menu_item_parent_id", "menu_item", ["parent_id"])
    op.create_index("ix_menu_item_screen_id", "menu_item", ["screen_id"])
    op.create_index("ix_menu_item_parent_seq", "menu_item", ["parent_id", "sequence_order"])

    menu_rows: list[dict] = []
    for group_label, group_seq, items in MENU_GROUPS:
        group_id = str(uuid.uuid4())
        menu_rows.append(
            {
                "id": group_id,
                "parent_id": None,
                "label": group_label,
                "icon": None,
                "sequence_order": group_seq,
                "menu_type": "group",
                "screen_id": None,
                "created_at": now,
                "updated_at": now,
                "legal_hold": False,
            }
        )
        for label, screen_code, icon, seq in items:
            menu_rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "parent_id": group_id,
                    "label": label,
                    "icon": icon,
                    "sequence_order": seq,
                    "menu_type": "screen_link",
                    "screen_id": screen_ids[screen_code],
                    "created_at": now,
                    "updated_at": now,
                    "legal_hold": False,
                }
            )

    op.bulk_insert(
        sa.table(
            "menu_item",
            sa.column("id", sa.String),
            sa.column("parent_id", sa.String),
            sa.column("label", sa.String),
            sa.column("icon", sa.Text),
            sa.column("sequence_order", sa.Integer),
            sa.column("menu_type", sa.String),
            sa.column("screen_id", sa.String),
            sa.column("created_at", sa.DateTime),
            sa.column("updated_at", sa.DateTime),
            sa.column("legal_hold", sa.Boolean),
        ),
        menu_rows,
    )

    # ------------------------------------------------------------------ #
    # 4. role_screen_access
    # ------------------------------------------------------------------ #
    op.create_table(
        "role_screen_access",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column(
            "role_id",
            sa.String(36),
            sa.ForeignKey("role.id", ondelete="CASCADE", name="fk_role_screen_access_role_id_role"),
            nullable=False,
        ),
        sa.Column(
            "screen_id",
            sa.String(36),
            sa.ForeignKey("screen.id", ondelete="CASCADE", name="fk_role_screen_access_screen_id_screen"),
            nullable=False,
        ),
        sa.Column(
            "org_unit_id",
            sa.String(36),
            sa.ForeignKey(
                "org_unit.id", ondelete="RESTRICT", name="fk_role_screen_access_org_unit_id_org_unit"
            ),
            nullable=True,
        ),
        sa.Column(
            "max_action_level_id",
            sa.String(36),
            sa.ForeignKey(
                "action_level.id",
                ondelete="RESTRICT",
                name="fk_role_screen_access_max_action_level_id_action_level",
            ),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.String(36), nullable=True),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_role_screen_access_org_id", "role_screen_access", ["org_id"])
    op.create_index("ix_role_screen_access_role_id", "role_screen_access", ["role_id"])
    op.create_index("ix_role_screen_access_screen_id", "role_screen_access", ["screen_id"])
    op.create_index("ix_role_screen_access_org_unit", "role_screen_access", ["org_unit_id"])
    op.create_index("ix_role_screen_access_max_action_level_id", "role_screen_access", ["max_action_level_id"])
    op.create_index(
        "ix_role_screen_access_lookup",
        "role_screen_access",
        ["org_id", "screen_id", "role_id", "deleted_at"],
    )
    op.create_index(
        "uq_role_screen_access_scope",
        "role_screen_access",
        ["role_id", "screen_id", "org_unit_id"],
        unique=True,
        postgresql_where=sa.text("org_unit_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_role_screen_access_orgwide",
        "role_screen_access",
        ["role_id", "screen_id"],
        unique=True,
        postgresql_where=sa.text("org_unit_id IS NULL AND deleted_at IS NULL"),
    )

    # ------------------------------------------------------------------ #
    # 5. FR-26 cutover backfill + FR-25 bootstrap lock.
    #
    # Skipped when generating offline SQL (``alembic upgrade head --sql``):
    # that mode never has a live connection to read from (bind.execute()
    # returns None), it only emits the DDL text above for manual review.
    # The real (online) upgrade always runs this block. See migration 0042
    # for the identical guard pattern.
    # ------------------------------------------------------------------ #
    if not context.is_offline_mode():
        role_rows = bind.execute(sa.text("SELECT id, org_id, name FROM role")).fetchall()

        grants_to_insert: list[dict] = []
        for role_id, org_id, role_name in role_rows:
            perm_rows = bind.execute(
                sa.text(
                    "SELECT p.value FROM role_permission rp "
                    "JOIN permission p ON p.id = rp.permission_id "
                    "WHERE rp.role_id = :role_id"
                ),
                {"role_id": role_id},
            ).fetchall()
            perms: set[str] = {value for (value,) in perm_rows}

            for screen_code, _name, _module, _route_path, read_perms, write_perms in SCREEN_CATALOG:
                if write_perms is _ALL or perms & write_perms:
                    level = "DELETE"
                elif read_perms and perms & read_perms:
                    level = "VIEW"
                else:
                    continue

                grants_to_insert.append(
                    {
                        "id": str(uuid.uuid4()),
                        "org_id": org_id,
                        "role_id": role_id,
                        "screen_id": screen_ids[screen_code],
                        "org_unit_id": None,
                        "max_action_level_id": level_ids[level],
                        "created_at": now,
                        "updated_at": now,
                        "legal_hold": False,
                        "_role_name": role_name,
                        "_screen_code": screen_code,
                    }
                )

        # FR-25 bootstrap: the built-in admin role's non-revocable DELETE grant
        # on the screen_access screen. If the backfill above already produced
        # that exact row (it will, since screen_access's read/write perms are
        # both admin_panel:access, which every admin role holds), leave it —
        # it is already DELETE. Otherwise (defensive — e.g. a role literally
        # named "admin" that somehow lacks admin_panel:access) add it.
        admin_screen_id = screen_ids["screen_access"]
        admin_delete_level_id = level_ids["DELETE"]
        has_admin_bootstrap_row = {
            row["role_id"]
            for row in grants_to_insert
            if row["_role_name"] == "admin" and row["_screen_code"] == "screen_access"
        }
        for role_id, org_id, role_name in role_rows:
            if role_name != "admin" or role_id in has_admin_bootstrap_row:
                continue
            grants_to_insert.append(
                {
                    "id": str(uuid.uuid4()),
                    "org_id": org_id,
                    "role_id": role_id,
                    "screen_id": admin_screen_id,
                    "org_unit_id": None,
                    "max_action_level_id": admin_delete_level_id,
                    "created_at": now,
                    "updated_at": now,
                    "legal_hold": False,
                    "_role_name": role_name,
                    "_screen_code": "screen_access",
                }
            )

        if grants_to_insert:
            for row in grants_to_insert:
                row.pop("_role_name", None)
                row.pop("_screen_code", None)
            op.bulk_insert(
                sa.table(
                    "role_screen_access",
                    sa.column("id", sa.String),
                    sa.column("org_id", sa.String),
                    sa.column("role_id", sa.String),
                    sa.column("screen_id", sa.String),
                    sa.column("org_unit_id", sa.String),
                    sa.column("max_action_level_id", sa.String),
                    sa.column("created_at", sa.DateTime),
                    sa.column("updated_at", sa.DateTime),
                    sa.column("legal_hold", sa.Boolean),
                ),
                grants_to_insert,
            )


def downgrade() -> None:
    op.drop_index("uq_role_screen_access_orgwide", table_name="role_screen_access")
    op.drop_index("uq_role_screen_access_scope", table_name="role_screen_access")
    op.drop_index("ix_role_screen_access_lookup", table_name="role_screen_access")
    op.drop_index("ix_role_screen_access_max_action_level_id", table_name="role_screen_access")
    op.drop_index("ix_role_screen_access_org_unit", table_name="role_screen_access")
    op.drop_index("ix_role_screen_access_screen_id", table_name="role_screen_access")
    op.drop_index("ix_role_screen_access_role_id", table_name="role_screen_access")
    op.drop_index("ix_role_screen_access_org_id", table_name="role_screen_access")
    op.drop_table("role_screen_access")

    op.drop_index("ix_menu_item_parent_seq", table_name="menu_item")
    op.drop_index("ix_menu_item_screen_id", table_name="menu_item")
    op.drop_index("ix_menu_item_parent_id", table_name="menu_item")
    op.drop_table("menu_item")

    op.drop_index("uq_screen_route_path", table_name="screen")
    op.drop_index("uq_screen_code", table_name="screen")
    op.drop_table("screen")

    op.drop_index("uq_action_level_rank", table_name="action_level")
    op.drop_index("uq_action_level_code", table_name="action_level")
    op.drop_table("action_level")
