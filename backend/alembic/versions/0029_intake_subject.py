"""Add a short 'subject' title to intake requests.

Requests only had type_label + description; the ticket header faked a title from
the first line of the description. This adds a first-class `subject` column and
backfills existing rows from the description's first line (or type_label).
"""
from alembic import op

revision = "0029_intake_subject"
down_revision = "0028_remove_triage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE intake_request ADD COLUMN IF NOT EXISTS subject VARCHAR(200);")
    # Backfill: first non-empty line of the description, capped at 200 chars.
    op.execute(
        "UPDATE intake_request "
        "SET subject = left(split_part(description, E'\\n', 1), 200) "
        "WHERE subject IS NULL AND description IS NOT NULL AND description <> '';"
    )
    # Anything still blank falls back to the type label.
    op.execute(
        "UPDATE intake_request SET subject = type_label "
        "WHERE subject IS NULL OR subject = '';"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE intake_request DROP COLUMN IF EXISTS subject;")
