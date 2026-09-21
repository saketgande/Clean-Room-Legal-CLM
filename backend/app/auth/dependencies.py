"""FastAPI-native dependency provider for the auth module.

Part of the DI migration (see backend/DI_MIGRATION.md). Mirrors the shape
used for contracts/ and contract_files/: a plain function taking `db` via
`Depends(get_db)` and returning a request-scoped service instance.
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.auth.service import AuthService
from app.core.deps import get_db


def get_auth_service(db: Session = Depends(get_db)) -> AuthService:
    return AuthService(db)
