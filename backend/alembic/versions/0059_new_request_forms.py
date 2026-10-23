"""The new request forms: "Agreement type" answers changed.

The New agreement form now asks "What kind of agreement?" with plain choices
("Services (MSA)", "Buying from a vendor", …) instead of the old CLM's list
("Master Services Agreement", "Confidentiality agreement — India", …).
Workflows set up for an old answer are pointed at its new one.
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0059_new_request_forms"
down_revision = "0058_drop_request_types"
branch_labels = None
depends_on = None

_NEW = {
    "master services agreement": "Services (MSA)",
    "confidentiality agreement — india": "NDA",
    "consultancy agreement — india": "Consultancy",
    "vendor / supplier agreement": "Buying from a vendor",
    "customer / sales agreement": "Selling to a customer",
    "saas or software licence": "Software or SaaS",
}


def upgrade() -> None:
    if op.get_context().as_sql:
        return  # data only
    conn = op.get_bind()
    for wid, criteria in conn.execute(sa.text("SELECT id, criteria FROM workflow")).all():
        c = criteria if isinstance(criteria, dict) else json.loads(criteria or "{}")
        entries = c.get("used_for") or []
        changed = False
        for e in entries:
            new = _NEW.get(str(e.get("agreement_type") or "").strip().lower())
            if new:
                e["agreement_type"], changed = new, True
        if changed:
            conn.execute(sa.text("UPDATE workflow SET criteria = CAST(:c AS json) WHERE id = :id"),
                         {"c": json.dumps(c), "id": wid})


def downgrade() -> None:
    pass  # the old answers no longer exist on any form
