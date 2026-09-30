"""Remove request types: the nine agreement forms are the only request forms.

The request-type table held the forms' fields (a copy of agreement_forms.json),
four hand-made demo types, and ids that "Used for" pointed at. Workflows now
say which FORM they are used for (``{"form": "new_agreement", ...}``), filing
validates against agreement_forms.json, and a request names its form in
``field_values.request_form`` — so the tables and the request's column go.

"Used for" entries are rewritten from the type id to its form key first; an
entry pointing at a hand-made type (no form) is dropped, since that type no
longer exists to be filed.
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0058_drop_request_types"
down_revision = "0057_workflow_step_stages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Offline (--sql) there is no data to read; the schema change still prints.
    if not op.get_context().as_sql:
        _used_for_to_forms()
    op.drop_constraint("intake_request_request_type_id_fkey", "intake_request", type_="foreignkey")
    op.drop_column("intake_request", "request_type_id")
    op.drop_table("intake_request_field")
    op.drop_table("intake_request_type")


def _used_for_to_forms() -> None:
    conn = op.get_bind()
    form_of = dict(conn.execute(sa.text(
        "SELECT id, form_key FROM intake_request_type WHERE form_key IS NOT NULL")).all())
    for wid, criteria in conn.execute(sa.text("SELECT id, criteria FROM workflow")).all():
        c = criteria if isinstance(criteria, dict) else json.loads(criteria or "{}")
        entries = c.get("used_for")
        if not entries:
            continue
        c["used_for"] = [
            {"form": form_of[e["request_type_id"]], "agreement_type": e.get("agreement_type")}
            for e in entries
            if isinstance(e, dict) and form_of.get(e.get("request_type_id"))
        ]
        conn.execute(sa.text("UPDATE workflow SET criteria = CAST(:c AS json) WHERE id = :id"),
                     {"c": json.dumps(c), "id": wid})


def downgrade() -> None:
    raise NotImplementedError("Request types were removed; restore from a backup to go back.")
