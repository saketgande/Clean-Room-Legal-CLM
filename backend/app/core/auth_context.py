"""How the current request authenticated.

Kept free of app imports so the auth service, deps and any domain service can
all use it without an import cycle.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AuthContext:
    method: Literal["api_key", "access_token", "internal"]
    api_key_id: str | None = None


# "internal" = called without an HTTP auth context (ingest, chat tools, jobs).
INTERNAL = AuthContext(method="internal")
