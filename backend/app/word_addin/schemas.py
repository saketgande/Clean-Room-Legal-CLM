from pydantic import BaseModel


class LinkResponse(BaseModel):
    """Result of auto-linking the open Word document to a contract."""

    contract_id: str
    title: str | None = None
    lifecycle_stage: str | None = None
    # True when a new contract was created; False when the document matched an
    # existing one (same file content).
    created: bool = False
