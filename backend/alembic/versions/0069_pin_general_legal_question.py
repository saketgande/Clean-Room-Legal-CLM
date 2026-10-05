"""Pin the built-in General Legal Question workflow to its request type.

"Legal Question — General" requests (the intake form, Ask Aegis) were routed by
the AI workflow picker, which sometimes left them for a person to route. They
now always get this workflow — a "Used for" pin by request type, the same kind
of admin decision as a form pin (see workflows.service._used_for_rank).
Litigation-classified questions keep their litigation route.

Data only. Only the built-in workflow is touched, and only while it has no
"Used for" of its own — an admin's edits stay. Downgrade removes just the pin.
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0069_pin_general_legal_question"
down_revision = "0068_drafting_templates"
branch_labels = None
depends_on = None

_NAME = "General Legal Question"
_PIN = {"type_label": "Legal Question — General"}


def _rows(conn):
    sql = sa.text("SELECT id, criteria FROM workflow WHERE is_builtin AND name = :n")
    for wid, criteria in conn.execute(sql, {"n": _NAME}).all():
        yield wid, (criteria if isinstance(criteria, dict) else json.loads(criteria or "{}"))


def _save(conn, wid, c):
    conn.execute(sa.text("UPDATE workflow SET criteria = CAST(:c AS json) WHERE id = :id"),
                 {"c": json.dumps(c), "id": wid})


def upgrade() -> None:
    if op.get_context().as_sql:
        return  # data only
    conn = op.get_bind()
    for wid, c in _rows(conn):
        if c.get("used_for"):
            continue
        c["used_for"] = [dict(_PIN)]
        c.setdefault("conditions", [])
        _save(conn, wid, c)


def downgrade() -> None:
    if op.get_context().as_sql:
        return
    conn = op.get_bind()
    for wid, c in _rows(conn):
        if c.get("used_for") == [_PIN]:
            c["used_for"] = []
            _save(conn, wid, c)
