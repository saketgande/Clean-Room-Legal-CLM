"""Remove the Matters screens that 0043 seeded.

0043_menu_screen_security (drl) seeds a "matters" and "matter_detail" screen
plus a Matters menu item, but Matters no longer exist (0062_remove_matters,
drl-saket). Left in place they'd put a menu entry in front of every role that
leads to a page that isn't there. Rows only — the screen table stays.
Guarded like 0043 so `alembic upgrade head --sql` stays DDL-only.

Revision ID: 0065_drop_matters_screens
Revises: 0064_merge_drl_saket
"""

from alembic import context, op

revision = "0065_drop_matters_screens"
down_revision = "0064_merge_drl_saket"
branch_labels = None
depends_on = None

_CODES = "('matters', 'matter_detail')"


def upgrade() -> None:
    if context.is_offline_mode():
        return
    # menu_item → screen is ON DELETE RESTRICT, so the menu rows go first;
    # role_screen_access cascades with the screen.
    op.execute(
        f"DELETE FROM menu_item WHERE screen_id IN (SELECT id FROM screen WHERE code IN {_CODES})"
    )
    op.execute(f"DELETE FROM screen WHERE code IN {_CODES}")


def downgrade() -> None:
    # Matters are gone; there is nothing for these screens to point at.
    pass
