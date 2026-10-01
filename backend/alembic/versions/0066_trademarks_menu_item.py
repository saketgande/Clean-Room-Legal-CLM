"""Put Trademarks in the sidebar.

0043 seeds a "trademarks" screen (with role access) but never a menu item for
it, and the sidebar is now built only from menu items — so the Trademarks
pages worked yet nothing linked to them. Adds one Workspace link in the slot
Matters left (sequence 50). Skips if a Trademarks link already exists.

Revision ID: 0066_trademarks_menu_item
Revises: 0065_drop_matters_screens
"""

import uuid

import sqlalchemy as sa

from alembic import context, op

revision = "0066_trademarks_menu_item"
down_revision = "0065_drop_matters_screens"
branch_labels = None
depends_on = None

_ICON = (
    '<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/>'
    '<circle cx="7.5" cy="7.5" r="1.5"/>'
)


def upgrade() -> None:
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    screen_id = bind.execute(sa.text("SELECT id FROM screen WHERE code = 'trademarks'")).scalar()
    parent_id = bind.execute(
        sa.text("SELECT id FROM menu_item WHERE label = 'Workspace' AND menu_type = 'group' "
                "AND deleted_at IS NULL")
    ).scalar()
    if screen_id is None or parent_id is None:
        return
    exists = bind.execute(
        sa.text("SELECT 1 FROM menu_item WHERE screen_id = :s AND deleted_at IS NULL"),
        {"s": screen_id},
    ).scalar()
    if exists:
        return
    bind.execute(
        sa.text(
            "INSERT INTO menu_item (id, parent_id, label, icon, sequence_order, menu_type, screen_id) "
            "VALUES (:id, :parent, 'Trademarks', :icon, 50, 'screen_link', :screen)"
        ),
        {"id": str(uuid.uuid4()), "parent": parent_id, "icon": _ICON, "screen": screen_id},
    )


def downgrade() -> None:
    if context.is_offline_mode():
        return
    op.execute(
        "DELETE FROM menu_item WHERE label = 'Trademarks' AND menu_type = 'screen_link' "
        "AND screen_id IN (SELECT id FROM screen WHERE code = 'trademarks')"
    )
