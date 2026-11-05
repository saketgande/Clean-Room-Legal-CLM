"""Template and playbook sync: check records, template types, version and contract columns.

Adds ``drafting_template_check`` (one run of the template-against-playbook check,
with a partial unique pair so a template has at most one running and one queued
check), ``drafting_template_type`` (per-org facts of a template type, holding the
pinned reviewing playbook), two columns on ``drafting_template_version`` and
``contract.review_playbook_id`` (the playbook pinned to the template a contract
was drafted from; NULL keeps today's by-name match).

Revision ID: 0074_template_sync
Revises: 0073_merge_trademark_suite
"""

import sqlalchemy as sa

from alembic import op

revision = "0074_template_sync"
down_revision = "0073_merge_trademark_suite"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drafting_template_check",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("template_key", sa.String(40), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("wording_sha256", sa.String(64), nullable=False),
        sa.Column("unsaved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("unsaved_body", sa.Text(), nullable=True),
        sa.Column("trigger", sa.String(30), nullable=False),
        sa.Column("trigger_detail", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("playbook_id", sa.String(36), sa.ForeignKey("playbook.id"), nullable=True),
        sa.Column("playbook_version_id", sa.String(36), sa.ForeignKey("playbook_version.id"), nullable=True),
        sa.Column("playbook_name", sa.String(255), nullable=True),
        sa.Column("playbook_version_number", sa.Integer(), nullable=True),
        sa.Column("rule_count", sa.Integer(), nullable=True),
        sa.Column("findings", sa.JSON(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("text_was_truncated", sa.Boolean(), nullable=True),
        sa.Column("text_length", sa.Integer(), nullable=True),
        sa.Column("read_length", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_drafting_template_check_org_id", "drafting_template_check", ["org_id"])
    op.create_index("ix_drafting_template_check_playbook_id", "drafting_template_check", ["playbook_id"])
    op.create_index(
        "ix_drafting_template_check_playbook_version_id", "drafting_template_check", ["playbook_version_id"]
    )
    op.create_index(
        "ix_drafting_template_check_org_key_created",
        "drafting_template_check",
        ["org_id", "template_key", "created_at"],
    )
    op.create_index("ix_drafting_template_check_status", "drafting_template_check", ["status"])
    op.create_index(
        "uq_drafting_template_check_one_running",
        "drafting_template_check",
        ["org_id", "template_key"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "uq_drafting_template_check_one_queued",
        "drafting_template_check",
        ["org_id", "template_key"],
        unique=True,
        postgresql_where=sa.text("status = 'queued'"),
    )

    op.create_table(
        "drafting_template_type",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("form_key", sa.String(40), nullable=False),
        sa.Column("agreement_kind", sa.String(80), nullable=True),
        sa.Column("shipped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("playbook_id", sa.String(36), sa.ForeignKey("playbook.id"), nullable=True),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "key", name="uq_drafting_template_type_key"),
    )
    op.create_index("ix_drafting_template_type_org_id", "drafting_template_type", ["org_id"])
    op.create_index("ix_drafting_template_type_playbook_id", "drafting_template_type", ["playbook_id"])
    # One active type per form + kind (FR-49), and one active type per name in any case (FR-50).
    op.create_index(
        "uq_drafting_template_type_binding",
        "drafting_template_type",
        ["org_id", "form_key", sa.text("coalesce(agreement_kind, '')")],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_drafting_template_type_name",
        "drafting_template_type",
        ["org_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.add_column("drafting_template_version", sa.Column("applied_findings", sa.JSON(), nullable=True))
    op.add_column("drafting_template_version", sa.Column("restored_from_version", sa.Integer(), nullable=True))

    op.add_column("contract", sa.Column("review_playbook_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_contract_review_playbook_id_playbook", "contract", "playbook", ["review_playbook_id"], ["id"]
    )
    op.create_index("ix_contract_review_playbook_id", "contract", ["review_playbook_id"])


def downgrade() -> None:
    op.drop_index("ix_contract_review_playbook_id", table_name="contract")
    op.drop_constraint("fk_contract_review_playbook_id_playbook", "contract", type_="foreignkey")
    op.drop_column("contract", "review_playbook_id")

    op.drop_column("drafting_template_version", "restored_from_version")
    op.drop_column("drafting_template_version", "applied_findings")

    op.drop_index("uq_drafting_template_type_name", table_name="drafting_template_type")
    op.drop_index("uq_drafting_template_type_binding", table_name="drafting_template_type")
    op.drop_index("ix_drafting_template_type_playbook_id", table_name="drafting_template_type")
    op.drop_index("ix_drafting_template_type_org_id", table_name="drafting_template_type")
    op.drop_table("drafting_template_type")

    op.drop_index("uq_drafting_template_check_one_queued", table_name="drafting_template_check")
    op.drop_index("uq_drafting_template_check_one_running", table_name="drafting_template_check")
    op.drop_index("ix_drafting_template_check_status", table_name="drafting_template_check")
    op.drop_index("ix_drafting_template_check_org_key_created", table_name="drafting_template_check")
    op.drop_index("ix_drafting_template_check_playbook_version_id", table_name="drafting_template_check")
    op.drop_index("ix_drafting_template_check_playbook_id", table_name="drafting_template_check")
    op.drop_index("ix_drafting_template_check_org_id", table_name="drafting_template_check")
    op.drop_table("drafting_template_check")
