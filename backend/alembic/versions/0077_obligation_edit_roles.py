"""Let members and legal reviewers change obligations on contracts they own.

Changing an obligation needs two keys: the role permission ``obligation:update`` and the per-contract rule
(``ContractService.get_contract_for_user(..., edit=True)``: the contract's owner, creator, an admin, or an
update-level share). Only the admin role held the first key, so the person who uploaded a contract, and who
gets its reminder emails, could not tick anything off. ``app.core.rbac.DEFAULT_ROLE_PERMISSIONS`` now grants
it to ``member`` and ``legal_reviewer``; that constant only reaches organisations created from now on
(``bootstrap_roles`` runs once, at first-admin setup), so this migration gives the same two roles the same
permission in organisations that already exist.

Additive only, like 0045: it never removes a permission, never touches any other role (``approver``, an
admin's custom roles), and does not call ``bootstrap_roles`` (which resets a role to exactly the defaults and
would erase an admin's changes). The per-contract rule still limits those roles to their own contracts.
A data migration writes no audit row: mention it in the release note.

Revision ID: 0077_obligation_edit_roles
Revises: 0076_request_summary
Create Date: 2026-10-27
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from alembic import context, op

revision = "0077_obligation_edit_roles"
down_revision = "0076_request_summary"
branch_labels = None
depends_on = None

PERMISSION = "obligation:update"
ROLES = ("member", "legal_reviewer")


def grant_obligation_update(bind) -> int:
    """Give ``obligation:update`` to every ``member`` and ``legal_reviewer`` role that lacks it. Returns how
    many grants it added. Idempotent. Takes the connection so a test can run it inside its own transaction."""
    permission_id = bind.execute(
        sa.text("SELECT id FROM permission WHERE value = :value"), {"value": PERMISSION}
    ).scalar()
    if permission_id is None:  # 0045 created every permission, but do not assume it for a hand-built database
        permission_id = str(uuid.uuid4())
        bind.execute(
            sa.text("INSERT INTO permission (id, value, created_at, updated_at) VALUES (:id, :value, now(), now())"),
            {"id": permission_id, "value": PERMISSION},
        )
    result = bind.execute(
        sa.text(
            "INSERT INTO role_permission (role_id, permission_id) "
            "SELECT r.id, :permission_id FROM role r WHERE r.name = ANY(:names) "
            "ON CONFLICT DO NOTHING"
        ),
        {"permission_id": permission_id, "names": list(ROLES)},
    )
    return result.rowcount or 0


def revoke_obligation_update(bind) -> int:
    """The reverse, for those two role names only."""
    result = bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE permission_id = (SELECT id FROM permission WHERE value = :value) "
            "AND role_id IN (SELECT id FROM role WHERE name = ANY(:names))"
        ),
        {"value": PERMISSION, "names": list(ROLES)},
    )
    return result.rowcount or 0


def upgrade() -> None:
    # Pure data migration: nothing to emit for `alembic upgrade head --sql` (same guard as 0042-0045).
    if not context.is_offline_mode():
        grant_obligation_update(op.get_bind())


def downgrade() -> None:
    # Removes the grant from every member and legal_reviewer role, including one an admin had added by hand
    # before this migration ran: there is no record of which rows this migration inserted.
    if not context.is_offline_mode():
        revoke_obligation_update(op.get_bind())
