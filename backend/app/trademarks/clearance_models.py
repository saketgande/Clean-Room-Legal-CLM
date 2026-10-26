from sqlalchemy import JSON, Column, ForeignKey, String, Text

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)


class TrademarkSearchRun(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    """One saved similarity search.

    ``POST /trademarks/search-similar`` stores every run under the ``query_id`` it
    returns. A run starts unattached (the intake wizard searches before the mark
    exists); submitting the wizard with that ``search_query_id`` - or searching
    from a case with a ``trademark_id`` - attaches it to the mark. The LTE then
    records an overall ``risk_level`` and notes on a detailed run, which is the
    "risk mapping" the clearance process asks for.
    """

    trademark_id = Column(
        String(36), ForeignKey("trademark.id", ondelete="CASCADE"), index=True, nullable=True
    )
    query_id = Column(String(40), index=True, nullable=False)
    purpose = Column(String(20), nullable=True)
    query = Column(JSON, nullable=False)
    sources = Column(JSON, nullable=False)
    summary = Column(JSON, nullable=False)
    risk_level = Column(String(20), nullable=True)
    notes = Column(Text, nullable=True)
