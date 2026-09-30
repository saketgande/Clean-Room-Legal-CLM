"""The contract workflow library: give the existing typed workflows their conditions.

Each agreement type now has workflows told apart by conditions (see
app/workflows/contract_library.py; the new ones are added by "Seed defaults").
The two existing typed built-ins become one tier of their type:

* NDA Fast-Track → our template + mutual (one-way and their-paper NDAs get
  their own workflows).
* Master Services Agreement → value under INR 50 lakh (MSA — high value takes
  50 lakh and up).

Vendor / Counterparty Due Diligence is due diligence on a supplier, not the
purchase agreement; the vendor purchase workflows replace it for the form, so
it goes back to serving email and chat requests (no type).

Only workflows still without conditions are touched — an admin's edits stay.
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0061_contract_workflow_library"
down_revision = "0060_workflow_types"
branch_labels = None
depends_on = None

_CONDITIONS = {
    "NDA Fast-Track": [{"field": "paper", "op": "is", "value": "Our template"},
                       {"field": "nda_kind", "op": "is", "value": "Mutual"}],
    "Master Services Agreement": [{"field": "value", "op": "under", "value": 5000000.0, "currency": "INR"}],
}


def upgrade() -> None:
    if op.get_context().as_sql:
        return  # data only
    conn = op.get_bind()
    for wid, name, criteria in conn.execute(sa.text("SELECT id, name, criteria FROM workflow WHERE is_builtin")).all():
        c = criteria if isinstance(criteria, dict) else json.loads(criteria or "{}")
        if c.get("conditions"):
            continue
        if name in _CONDITIONS and c.get("used_for"):
            c["conditions"] = _CONDITIONS[name]
        elif name == "Vendor / Counterparty Due Diligence":
            c["used_for"], c["conditions"] = [], []
        else:
            continue
        conn.execute(sa.text("UPDATE workflow SET criteria = CAST(:c AS json) WHERE id = :id"),
                     {"c": json.dumps(c), "id": wid})


def downgrade() -> None:
    pass  # ordinary workflow settings; edit them in the builder
