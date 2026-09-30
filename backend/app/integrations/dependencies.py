"""FastAPI-native dependency providers for third-party integrations.

Part of the DI migration (see backend/DI_MIGRATION.md). Providers here are
plain callables with no required arguments: usable both as `Depends(...)` in
routes and by direct call from Celery tasks / deferred imports that have no
request context. They currently return the existing module-level singletons
unchanged — only the *access path* (inject vs. import) changes, so behavior
is identical until a caller starts passing an override.
"""

from app.integrations.claude import ClaudeProvider, claude_client
from app.integrations.databricks import DatabricksDocumentClient, databricks_client
from app.integrations.docusign import SignatureProvider, docusign_client
from app.integrations.reducto import ReductoClient, reducto_client
from app.integrations.resend import EmailSender, resend_client
from app.integrations.storage import StorageBackend, storage_service


def get_storage_service() -> StorageBackend:
    return storage_service


def get_claude_client() -> ClaudeProvider:
    return claude_client


def get_reducto_client() -> ReductoClient:
    return reducto_client


def get_databricks_client() -> DatabricksDocumentClient:
    return databricks_client


def get_resend_client() -> EmailSender:
    return resend_client


def get_docusign_client() -> SignatureProvider:
    return docusign_client
