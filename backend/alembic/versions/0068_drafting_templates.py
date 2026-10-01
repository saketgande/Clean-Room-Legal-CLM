"""Editable drafting templates: their version table, screen and sidebar link.

The templates used to be string constants in intake/drafting.py, so changing
our standard paper meant a code change. Now each org's edits are versions in
``drafting_template_version`` (the shipped text stays the default), shown on a
"Templates" page under Intelligence next to Playbooks. The screen gets the same
role access the Playbooks screen has in every org, since a template is the
other half of the playbook it has to pass.

Revision ID: 0068_drafting_templates
Revises: 0067_remove_trademarks_menu_item
"""

import uuid

import sqlalchemy as sa

from alembic import context, op

revision = "0068_drafting_templates"
down_revision = "0067_remove_trademarks_menu_item"
branch_labels = None
depends_on = None

_ICON = (
    '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>'
    '<path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/>'
)


def upgrade() -> None:
    op.create_table(
        "drafting_template_version",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "key", "version", name="uq_drafting_template_version"),
    )
    op.create_index("ix_drafting_template_version_org_id", "drafting_template_version", ["org_id"])
    op.create_index("ix_drafting_template_version_key", "drafting_template_version", ["key"])

    if context.is_offline_mode():
        return
    bind = op.get_bind()
    screen_id = str(uuid.uuid4())
    bind.execute(
        sa.text("INSERT INTO screen (id, code, name, module, route_path) "
                "VALUES (:id, 'templates', 'Templates', 'Intelligence', '/templates')"),
        {"id": screen_id},
    )
    # Whoever may open Playbooks may open Templates, at the same level.
    bind.execute(
        sa.text(
            "INSERT INTO role_screen_access (id, org_id, role_id, screen_id, org_unit_id, max_action_level_id) "
            "SELECT md5(random()::text || a.id)::uuid::text, a.org_id, a.role_id, :screen, a.org_unit_id, "
            "a.max_action_level_id FROM role_screen_access a JOIN screen s ON s.id = a.screen_id "
            "WHERE s.code = 'playbooks' AND a.deleted_at IS NULL"
        ),
        {"screen": screen_id},
    )
    parent_id = bind.execute(
        sa.text("SELECT id FROM menu_item WHERE label = 'Intelligence' AND menu_type = 'group' "
                "AND deleted_at IS NULL")
    ).scalar()
    if parent_id is not None:
        bind.execute(
            sa.text("INSERT INTO menu_item (id, parent_id, label, icon, sequence_order, menu_type, screen_id) "
                    "VALUES (:id, :parent, 'Templates', :icon, 45, 'screen_link', :screen)"),
            {"id": str(uuid.uuid4()), "parent": parent_id, "icon": _ICON, "screen": screen_id},
        )


def downgrade() -> None:
    if not context.is_offline_mode():
        op.execute("DELETE FROM menu_item WHERE screen_id IN (SELECT id FROM screen WHERE code = 'templates')")
        op.execute("DELETE FROM screen WHERE code = 'templates'")  # role_screen_access cascades
    op.drop_index("ix_drafting_template_version_key", table_name="drafting_template_version")
    op.drop_index("ix_drafting_template_version_org_id", table_name="drafting_template_version")
    op.drop_table("drafting_template_version")
