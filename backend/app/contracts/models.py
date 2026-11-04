from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    event,
    select,
)
from sqlalchemy.orm import attributes

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    SoftDeleteMixin,
    TableNameMixin,
    TimestampMixin,
)
from app.core.enums import ContractLifecycleStage


class Contract(
    TableNameMixin,
    IdMixin,
    OrgScopedMixin,
    ActorTrackedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    Base,
):
    title = Column(String(500), nullable=False)
    contract_type = Column(String(160), index=True, nullable=True)
    lifecycle_stage = Column(
        String(80), index=True, nullable=False, default=ContractLifecycleStage.INTAKE
    )
    # Derived sub-states of ACTIVE / CLOSED, folded out of the stage enum:
    #   renewal_due — ACTIVE contract whose renewal/notice window has opened
    #                 (set by the renewal-window job; cleared on a renewal decision)
    #   archived    — CLOSED contract kept for retention but hidden from default views
    renewal_due = Column(Boolean, index=True, nullable=False, default=False)
    archived = Column(Boolean, index=True, nullable=False, default=False)
    owner_user_id = Column(String(36), ForeignKey("user.id"), index=True, nullable=False)
    counterparty_name = Column(String(255), index=True, nullable=True)
    # Register records this contract was drafted for (set from its intake
    # request). The name above stays for display and for contracts that
    # predate the register or arrive by plain upload.
    counterparty_id = Column(String(36), ForeignKey("counterparty.id", ondelete="SET NULL"), index=True, nullable=True)
    legal_entity_id = Column(String(36), ForeignKey("legal_entity.id", ondelete="SET NULL"), index=True, nullable=True)
    jurisdiction = Column(String(160), index=True, nullable=True)
    # Phase 3 (MAC): confidentiality classification. Ordered ladder
    #   public < internal < confidential < restricted
    # A user may only read a contract whose classification is <= their clearance
    # (see app/contracts/access.py). Defaults to 'internal' so nothing is locked
    # down harder than before until it's deliberately reclassified.
    confidentiality = Column(String(40), index=True, nullable=False, default="internal")
    risk_level = Column(String(80), index=True, nullable=True)
    # Weighted, explainable risk. risk_summary holds the drivers behind the score.
    risk_score = Column(Integer, nullable=True)
    risk_band = Column(String(40), nullable=True)
    risk_summary = Column(JSON, nullable=True)
    value_amount = Column(Numeric(18, 2), nullable=True)  # exact money, not binary float
    currency = Column(String(3), nullable=True)
    effective_date = Column(Date, nullable=True)
    expiration_date = Column(Date, nullable=True)
    current_contract_file_id = Column(
        String(36),
        ForeignKey("contract_file.id", name="fk_contract_current_contract_file_id", use_alter=True),
        nullable=True,
    )
    current_authoritative_version_id = Column(
        String(36),
        ForeignKey(
            "contract_version.id",
            name="fk_contract_current_authoritative_version_id",
            use_alter=True,
        ),
        nullable=True,
    )
    metadata_json = Column(JSON, nullable=False, default=dict)
    # The playbook pinned to the drafting template this contract was drafted from.
    # NULL (uploads, custom drafts, drafts that predate pinning) keeps the by-name
    # match. Set once at drafting, never changed afterwards.
    review_playbook_id = Column(String(36), ForeignKey("playbook.id"), index=True, nullable=True)


_GONE = object()


def merge_json(base, mine, theirs):
    """Three-way merge of JSON objects: what this session changed since it read `base`, laid onto `theirs`,
    the row as it is now. Objects merge key by key, all the way down; any other value this session changed wins."""
    if not (isinstance(base, dict) and isinstance(mine, dict) and isinstance(theirs, dict)):
        return mine if mine != base else theirs
    merged = dict(theirs)
    for key in base.keys() | mine.keys():
        before, after = base.get(key, _GONE), mine.get(key, _GONE)
        if before == after:
            continue
        if after is _GONE:
            merged.pop(key, None)
        elif key in theirs and before is not _GONE:
            merged[key] = merge_json(before, after, theirs[key])
        else:
            merged[key] = after
    return merged


@event.listens_for(Contract, "before_update")
def _save_only_the_metadata_keys_this_session_changed(mapper, connection, contract):
    """Every writer reads metadata_json, edits it and assigns the whole thing back, and sessions keep their
    instances after a commit. So a job that read it before a 4-minute review and then dropped one flag wrote
    back its old copy of everything else: the provenance in field_sources and the AI's suggestions were
    wiped, and an AI guess at the expiry became a trusted anchor. Here, under a row lock, the stored value
    is re-read and only this session's own changes are applied to it."""
    history = attributes.get_history(contract, "metadata_json", passive=attributes.PASSIVE_NO_INITIALIZE)
    if not history.deleted or not history.added:
        return
    column = Contract.__table__.c.metadata_json
    stored = connection.execute(
        select(column).where(Contract.__table__.c.id == contract.id).with_for_update()
    ).scalar_one_or_none()
    if stored is not None and stored != history.deleted[0]:
        contract.metadata_json = merge_json(history.deleted[0], history.added[0], stored)


class ContractParty(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    name = Column(String(255), index=True, nullable=False)
    party_type = Column(String(120), nullable=True)
    contact_email = Column(String(320), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class ContractStageHistory(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    from_stage = Column(String(80), nullable=True)
    to_stage = Column(String(80), index=True, nullable=False)
    reason = Column(Text, nullable=True)
    changed_by_user_id = Column(String(36), ForeignKey("user.id"), nullable=True)
    changed_at = Column(DateTime(timezone=True), nullable=False)
    override_used = Column(Boolean, nullable=False, default=False)
