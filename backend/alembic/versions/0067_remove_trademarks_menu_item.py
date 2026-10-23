"""Take Trademarks back out of the sidebar.

0066 added a Trademarks link, but drl's sidebar never had one and the user
wants the menu to match drl. 0066 is already pushed (other databases may have
run it), so it stays and this reverses it instead of the file being deleted.

Revision ID: 0067_remove_trademarks_menu_item
Revises: 0066_trademarks_menu_item
"""

from alembic import context, op

revision = "0067_remove_trademarks_menu_item"
down_revision = "0066_trademarks_menu_item"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        return
    op.execute(
        "DELETE FROM menu_item WHERE label = 'Trademarks' AND menu_type = 'screen_link' "
        "AND screen_id IN (SELECT id FROM screen WHERE code = 'trademarks')"
    )


def downgrade() -> None:
    # Re-adding the link is 0066's job; downgrading past it removes it again.
    pass
