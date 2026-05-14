from sqlalchemy import Boolean, Column, ForeignKey, Integer, JSON, String, Text

from app.core.database import ActorTrackedMixin, Base, IdMixin, OrgScopedMixin, TableNameMixin, TimestampMixin


class KnowledgeNode(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    node_type = Column(String(120), index=True, nullable=False)
    label = Column(String(500), index=True, nullable=False)
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=True)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), index=True, nullable=True)
    text_snapshot_id = Column(String(36), ForeignKey("contract_text_snapshot.id"), nullable=True)
    properties = Column(JSON, nullable=False, default=dict)
    is_stale = Column(Boolean, nullable=False, default=False)


class KnowledgeEdge(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    edge_type = Column(String(120), index=True, nullable=False)
    from_node_id = Column(String(36), ForeignKey("knowledge_node.id"), index=True, nullable=False)
    to_node_id = Column(String(36), ForeignKey("knowledge_node.id"), index=True, nullable=False)
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=True)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), index=True, nullable=True)
    text_snapshot_id = Column(String(36), ForeignKey("contract_text_snapshot.id"), nullable=True)
    properties = Column(JSON, nullable=False, default=dict)
    is_stale = Column(Boolean, nullable=False, default=False)


class ClauseExtraction(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    contract_id = Column(String(36), ForeignKey("contract.id"), index=True, nullable=False)
    contract_version_id = Column(String(36), ForeignKey("contract_version.id"), index=True, nullable=False)
    text_snapshot_id = Column(String(36), ForeignKey("contract_text_snapshot.id"), index=True, nullable=False)
    clause_type = Column(String(160), index=True, nullable=False)
    heading = Column(String(500), nullable=True)
    text = Column(Text, nullable=False)
    start_char = Column(Integer, nullable=True)
    end_char = Column(Integer, nullable=True)
    confidence = Column(String(40), nullable=True)
    is_stale = Column(Boolean, nullable=False, default=False)


class BrainQuery(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base):
    query_scope = Column(String(80), index=True, nullable=False)
    question = Column(Text, nullable=False)
    contract_id = Column(String(36), ForeignKey("contract.id"), nullable=True)
    project_id = Column(String(36), ForeignKey("project.id"), nullable=True)
    answer = Column(Text, nullable=True)
    citations = Column(JSON, nullable=True)
    retrieval_metadata = Column(JSON, nullable=True)
