"""Master records for the parties to an agreement.

``LegalEntity`` is one of *our* companies (the contracting entity and its
authorised signatory); ``Counterparty`` is the other side. Both used to be free
text typed into each request, so nothing linked a request to a counterparty's
other contracts and the entity list was three hard-coded names.

``source`` / ``external_ref`` let an ERP or vendor master fill these same rows
later instead of replacing them.
"""

from sqlalchemy import Boolean, Column, Index, String, Text, func

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    TableNameMixin,
    TimestampMixin,
)


class LegalEntity(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    name = Column(String(200), nullable=False)  # registered legal name
    jurisdiction = Column(String(120), nullable=True)
    registered_address = Column(Text, nullable=True)
    authorised_signatory = Column(String(200), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    source = Column(String(40), nullable=False, default="aegis")  # aegis | an external system's name
    external_ref = Column(String(120), nullable=True)

    __table_args__ = (
        Index("uq_legal_entity_org_name", "org_id", func.lower(name), unique=True),
    )


class Counterparty(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    name = Column(String(200), nullable=False)  # legal name
    jurisdiction = Column(String(120), nullable=True)
    address = Column(Text, nullable=True)
    contact_email = Column(String(254), nullable=True)  # signature routing uses this
    active = Column(Boolean, nullable=False, default=True)
    source = Column(String(40), nullable=False, default="aegis")
    external_ref = Column(String(120), nullable=True)

    __table_args__ = (
        Index("uq_counterparty_org_name", "org_id", func.lower(name), unique=True),
    )
