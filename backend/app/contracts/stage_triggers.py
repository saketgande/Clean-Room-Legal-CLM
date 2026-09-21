"""DI-MIGRATION: this module's logic moved into
``ContractLifecycleService._fire_stage_entry_triggers`` in lifecycle.py (see
backend/DI_MIGRATION.md — the class merges the state machine and its
stage-entry side effects, since the triggers were only ever called from
inside a transition). This wrapper exists only in case something still
imports ``fire_stage_entry_triggers`` directly; grep found no such caller at
the time of the merge, so this file is a safety net, not a live seam.
"""

from sqlalchemy.orm import Session

from app.contracts.lifecycle import ContractLifecycleService
from app.contracts.models import Contract


def fire_stage_entry_triggers(
    db: Session,
    *,
    contract: Contract,
    from_stage: str,
    to_stage: str,
    actor_user_id: str | None,
) -> None:
    ContractLifecycleService(db)._fire_stage_entry_triggers(
        contract=contract,
        from_stage=from_stage,
        to_stage=to_stage,
        actor_user_id=actor_user_id,
    )
