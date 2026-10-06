"""Rename the Trademark Suite menu group to "Trademarks" and "New intake" to "Trademarks intake".

Data only (no DDL). 0048_trademark_suite_menu is already pushed, so its labels
are changed here rather than by editing it. Only the two rows 0048 created are
touched: the group under the "Workspace" group, and the link to the
``trademark_intake`` screen inside that group. Idempotent, and skipped in
offline (``--sql``) mode like 0048.

Revision ID: 0049_trademark_menu_labels
Revises: 0048_trademark_suite_menu
"""

import sqlalchemy as sa

from alembic import context, op

revision = "0049_trademark_menu_labels"
down_revision = "0048_trademark_suite_menu"
branch_labels = None
depends_on = None

_PARENT_GROUP = "Workspace"
_INTAKE_SCREEN = "trademark_intake"

_OLD_GROUP, _NEW_GROUP = "Trademark Suite", "Trademarks"
_OLD_INTAKE, _NEW_INTAKE = "New intake", "Trademarks intake"


def _rename(old_group: str, new_group: str, old_intake: str, new_intake: str) -> None:
    if context.is_offline_mode():
        return

    bind = op.get_bind()
    group_ids = [
        row[0]
        for row in bind.execute(
            sa.text(
                "SELECT g.id FROM menu_item g JOIN menu_item p ON p.id = g.parent_id "
                "WHERE g.menu_type = 'group' AND g.label = :label AND p.label = :parent "
                "AND p.parent_id IS NULL AND g.deleted_at IS NULL"
            ),
            {"label": old_group, "parent": _PARENT_GROUP},
        ).fetchall()
    ]
    for group_id in group_ids:
        bind.execute(
            sa.text("UPDATE menu_item SET label = :label WHERE id = :id"),
            {"label": new_group, "id": group_id},
        )
        bind.execute(
            sa.text(
                "UPDATE menu_item SET label = :new FROM screen s "
                "WHERE menu_item.screen_id = s.id AND s.code = :code "
                "AND menu_item.parent_id = :group AND menu_item.label = :old"
            ),
            {"new": new_intake, "code": _INTAKE_SCREEN, "group": group_id, "old": old_intake},
        )


def upgrade() -> None:
    _rename(_OLD_GROUP, _NEW_GROUP, _OLD_INTAKE, _NEW_INTAKE)


def downgrade() -> None:
    _rename(_NEW_GROUP, _OLD_GROUP, _NEW_INTAKE, _OLD_INTAKE)
