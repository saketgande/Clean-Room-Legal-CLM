"""Standard paper: publish the reviewed playbook versions and mark each template's wording.

Data only, no schema change. The logic lives in app.drafting_templates.standard so the
migration and its tests share one body. With no organisation (CI, the test DB) it does nothing.

Chained after 0077_obligation_edit_roles, the head when this was written (same number, one chain).
"""
from sqlalchemy.orm import Session

from alembic import context, op

revision = "0077_std_paper"
down_revision = "0077_obligation_edit_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():  # `upgrade head --sql` has no connection to read from
        return
    from app.drafting_templates.standard import apply_standard_update

    db = Session(bind=op.get_bind())
    apply_standard_update(db)


def downgrade() -> None:
    if context.is_offline_mode():
        return
    from app.drafting_templates.standard import revert_standard_update

    db = Session(bind=op.get_bind())
    revert_standard_update(db)
