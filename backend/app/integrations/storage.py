import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings
from app.core.database import new_uuid


@dataclass(frozen=True)
class StoredBytes:
    storage_key: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256_hash: str


class StorageService:
    def __init__(self, root: Path | None = None):
        self.root = (root or settings.storage_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save_bytes(self, *, org_id: str, filename: str, mime_type: str, content: bytes) -> StoredBytes:
        safe_filename = self._safe_filename(filename)
        now = datetime.now(UTC)
        storage_key = "/".join(
            [
                org_id,
                f"{now.year:04d}",
                f"{now.month:02d}",
                f"{new_uuid()}_{safe_filename}",
            ]
        )
        absolute_path = self._resolve_storage_key(storage_key, must_exist=False)
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_bytes(content)
        return StoredBytes(
            storage_key=storage_key,
            filename=safe_filename,
            mime_type=mime_type,
            size_bytes=len(content),
            sha256_hash=hashlib.sha256(content).hexdigest(),
        )

    def path_for_read(self, storage_key: str) -> Path:
        return self._resolve_storage_key(storage_key, must_exist=True)

    def read_bytes(self, storage_key: str) -> bytes:
        return self.path_for_read(storage_key).read_bytes()

    def delete_bytes_permanently(self, storage_key: str) -> None:
        path = self._resolve_storage_key(storage_key, must_exist=False)
        if path.exists():
            path.unlink()

    def _resolve_storage_key(self, storage_key: str, *, must_exist: bool) -> Path:
        candidate = (self.root / storage_key).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("Storage key resolves outside the storage root")
        if must_exist and not candidate.exists():
            raise FileNotFoundError(storage_key)
        return candidate

    @staticmethod
    def _safe_filename(filename: str) -> str:
        base = Path(filename).name.strip() or "contract"
        return re.sub(r"[^A-Za-z0-9._ -]+", "_", base)[:240]


storage_service = StorageService()
