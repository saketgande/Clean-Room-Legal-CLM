"""Remove Matters (formerly "projects") entirely.

Matters grouped contracts and could grant access to them (members, shares),
carry project-scoped ethical walls and scope Ask Aegis. The feature is gone:
its six tables, the matter_id link on six other tables, the project:*
permissions, and any project grants / walls / Brain answers.

Dropping it can only narrow access, never widen it: membership and shares were
ALLOW paths, and a project-scoped wall (a DENY) is refused rather than silently
dropped — see the check below.
"""

import sqlalchemy as sa

from alembic import op

revision = "0062_remove_matters"
down_revision = "0061_contract_workflow_library"
branch_labels = None
depends_on = None

_LINKED = ("contract", "assistant_session", "brain_query", "prompt_run", "tabular_review", "intake_request")
_MATTER_TABLES = ("matter_contract", "matter_activity", "matter_member", "matter_share", "matter_folder", "matter")


def upgrade() -> None:
    if not op.get_context().as_sql:  # offline --sql can't read rows; the check needs a live DB
        # A wall on a matter is a deny rule; dropping it would unseal people. Make
        # someone re-scope it to the contracts first instead of losing it quietly.
        walls = op.get_bind().execute(
            sa.text("SELECT count(*) FROM ethical_wall WHERE scope_type = 'project'")
        ).scalar()
        if walls:
            raise RuntimeError(
                f"{walls} ethical wall(s) are scoped to a matter. Re-create them on the "
                "matter's contracts, then run this migration again."
            )
    op.execute("DELETE FROM resource_grant WHERE resource_type = 'project'")
    # Saved answers asked within a matter: nothing left can check who may see them.
    op.execute("DELETE FROM brain_query WHERE matter_id IS NOT NULL OR query_scope = 'project'")
    op.execute(
        "DELETE FROM role_permission WHERE permission_id IN "
        "(SELECT id FROM permission WHERE value LIKE 'project:%')"
    )
    op.execute("DELETE FROM permission WHERE value LIKE 'project:%'")
    op.execute("UPDATE assistant_session SET session_type = 'general' WHERE session_type = 'project'")

    for table in _LINKED:
        # DROP COLUMN takes the column's foreign key and indexes with it.
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS matter_id")
    for table in _MATTER_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


def downgrade() -> None:
    raise NotImplementedError("Matters were removed; restore from a backup to go back.")
