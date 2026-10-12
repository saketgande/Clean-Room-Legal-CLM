"""Workflows sit under an agreement type and carry their own conditions.

Form requests now only get a workflow set up for their type (no word match, no
catch-all), so the two built-ins whose words used to catch those requests are
given the type they were catching: NDA Fast-Track → New agreement · NDA, and
Vendor / Counterparty Due Diligence → New agreement · Buying from a vendor.
Only ones with no type yet; an admin's choice is never overwritten.
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0060_workflow_types"
down_revision = "0059_new_request_forms"
branch_labels = None
depends_on = None

_TYPES = {
    "NDA Fast-Track": "NDA",
    "Vendor / Counterparty Due Diligence": "Buying from a vendor",
}


def upgrade() -> None:
    if op.get_context().as_sql:
        return  # data only
    conn = op.get_bind()
    for wid, name, criteria in conn.execute(sa.text("SELECT id, name, criteria FROM workflow")).all():
        c = criteria if isinstance(criteria, dict) else json.loads(criteria or "{}")
        if name not in _TYPES or c.get("used_for"):
            continue
        c["used_for"] = [{"form": "new_agreement", "agreement_type": _TYPES[name]}]
        c.setdefault("conditions", [])
        conn.execute(sa.text("UPDATE workflow SET criteria = CAST(:c AS json) WHERE id = :id"),
                     {"c": json.dumps(c), "id": wid})


def downgrade() -> None:
    pass  # types are ordinary workflow settings; nothing to undo structurally
