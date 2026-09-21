"""FastAPI-native dependency providers for the contract_files module.

Part of the DI migration (see backend/DI_MIGRATION.md). `get_contract_files_service`
is request-scoped (built fresh per request from the request-scoped `db`
session) and wires in the singleton integration clients via
`app.integrations.dependencies` — usable both as `Depends(...)` in routes and
by direct call from non-request contexts.
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.contract_files.service import ContractFilesService
from app.core.deps import get_db
from app.integrations.databricks import DatabricksDocumentClient
from app.integrations.dependencies import (
    get_databricks_client,
    get_reducto_client,
    get_storage_service,
)
from app.integrations.ocr import OCRProvider
from app.integrations.storage import StorageBackend


def get_contract_files_service(
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage_service),
    reducto: OCRProvider = Depends(get_reducto_client),
    databricks: DatabricksDocumentClient = Depends(get_databricks_client),
) -> ContractFilesService:
    return ContractFilesService(db, storage=storage, reducto=reducto, databricks=databricks)
