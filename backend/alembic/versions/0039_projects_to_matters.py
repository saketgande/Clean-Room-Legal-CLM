"""Evolve Projects into Matters (full physical rename + enrich + one-to-many).

Class names drive __tablename__ (snake_case), so the physical tables follow the
code rename:  project -> matter, project_folder -> matter_folder, etc. Every
`project_id` column becomes `matter_id`, and `matter.project_type` becomes
`matter_type`. `rename_table` / `alter_column ... new_column_name` preserve
columns, data and inbound foreign keys — only names change.

New matter fields: matter_number, client_name, status, opened_at, closed_at.
One matter -> many: `contract` gains a nullable `matter_id`; existing many-to-many
`matter_contract` rows collapse to one matter per contract — the
most-recently-updated wins. `intake_request` already had a project link, now
renamed to matter_id.
"""
from alembic import op
import sqlalchemy as sa

revision = "0039_projects_to_matters"
down_revision = "0038_team_departments"
branch_labels = None
depends_on = None

# Tables renamed project* -> matter* (satellite tables carry a project_id too).
_TABLE_RENAMES = [
    ("project", "matter"),
    ("project_folder", "matter_folder"),
    ("project_member", "matter_member"),
    ("project_share", "matter_share"),
    ("project_contract", "matter_contract"),
    ("project_activity", "matter_activity"),
]

# Every table with a project_id column (post table-rename names).
_PROJECT_ID_TABLES = [
    "matter_folder",
    "matter_member",
    "matter_share",
    "matter_contract",
    "matter_activity",
    "assistant_session",
    "brain_query",
    "intake_request",
    "prompt_run",
    "tabular_review",
]


def upgrade() -> None:
    # 1. Rename the tables.
    for old, new in _TABLE_RENAMES:
        op.rename_table(old, new)

    # 2. Rename matter.project_type -> matter_type.
    op.alter_column("matter", "project_type", new_column_name="matter_type")

    # 3. Rename every project_id column -> matter_id.
    for table in _PROJECT_ID_TABLES:
        op.alter_column(table, "project_id", new_column_name="matter_id")

    # 4. New matter identity + lifecycle fields.
    op.add_column("matter", sa.Column("matter_number", sa.String(length=40), nullable=True))
    op.add_column("matter", sa.Column("client_name", sa.String(length=255), nullable=True))
    op.add_column(
        "matter",
        sa.Column("status", sa.String(length=40), nullable=False, server_default="active"),
    )
    op.add_column("matter", sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("matter", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))

    # 5. One matter -> many contracts: contract.matter_id.
    op.add_column("contract", sa.Column("matter_id", sa.String(length=36), nullable=True))
    op.create_foreign_key("fk_contract_matter", "contract", "matter", ["matter_id"], ["id"])
    op.create_index("ix_contract_matter_id", "contract", ["matter_id"])

    # 6. Backfill.
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE matter SET opened_at = created_at WHERE opened_at IS NULL"))
    # Per-org sequential matter numbers: M-1000, M-1001, ...
    conn.execute(
        sa.text(
            """
            WITH numbered AS (
                SELECT id,
                       'M-' || LPAD(
                           (999 + ROW_NUMBER() OVER (PARTITION BY org_id ORDER BY created_at))::text,
                           4, '0'
                       ) AS num
                FROM matter
            )
            UPDATE matter m SET matter_number = n.num
            FROM numbered n WHERE m.id = n.id AND m.matter_number IS NULL
            """
        )
    )
    # Collapse many-to-many -> one matter per contract: most-recently-updated wins.
    conn.execute(
        sa.text(
            """
            WITH pick AS (
                SELECT DISTINCT ON (contract_id) contract_id, matter_id
                FROM matter_contract
                ORDER BY contract_id, updated_at DESC NULLS LAST
            )
            UPDATE contract c SET matter_id = p.matter_id
            FROM pick p WHERE c.id = p.contract_id AND c.matter_id IS NULL
            """
        )
    )

    # 7. Indexes + uniqueness (after backfill so the unique holds).
    op.create_index("ix_matter_matter_number", "matter", ["matter_number"])
    op.create_index("ix_matter_client_name", "matter", ["client_name"])
    op.create_index("ix_matter_status", "matter", ["status"])
    op.create_unique_constraint(
        "uq_matter_number_per_org", "matter", ["org_id", "matter_number"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_matter_number_per_org", "matter", type_="unique")
    op.drop_index("ix_matter_status", table_name="matter")
    op.drop_index("ix_matter_client_name", table_name="matter")
    op.drop_index("ix_matter_matter_number", table_name="matter")

    op.drop_index("ix_contract_matter_id", table_name="contract")
    op.drop_constraint("fk_contract_matter", "contract", type_="foreignkey")
    op.drop_column("contract", "matter_id")

    op.drop_column("matter", "closed_at")
    op.drop_column("matter", "opened_at")
    op.drop_column("matter", "status")
    op.drop_column("matter", "client_name")
    op.drop_column("matter", "matter_number")

    for table in _PROJECT_ID_TABLES:
        op.alter_column(table, "matter_id", new_column_name="project_id")

    op.alter_column("matter", "matter_type", new_column_name="project_type")

    for old, new in reversed(_TABLE_RENAMES):
        op.rename_table(new, old)
