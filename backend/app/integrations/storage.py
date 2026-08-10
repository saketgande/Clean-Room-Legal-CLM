import hashlib
import re
import tempfile
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


def _build_storage_key(*, org_id: str, filename: str) -> str:
    """Org-scoped, time-partitioned, collision-proof object key.

    Shared by every backend so an object's key is identical regardless of
    where the bytes physically live.
    """
    now = datetime.now(UTC)
    return "/".join(
        [
            org_id,
            f"{now.year:04d}",
            f"{now.month:02d}",
            f"{new_uuid()}_{_safe_filename(filename)}",
        ]
    )


def _safe_filename(filename: str) -> str:
    base = Path(filename).name.strip() or "contract"
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", base)[:240]


class StorageService:
    """Local-filesystem object store (the default backend).

    Unchanged from the original single-backend implementation so existing
    behavior — including path-traversal rejection — is byte-for-byte preserved.
    """

    def __init__(self, root: Path | None = None):
        self.root = (root or settings.storage_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save_bytes(self, *, org_id: str, filename: str, mime_type: str, content: bytes) -> StoredBytes:
        safe_filename = self._safe_filename(filename)
        storage_key = _build_storage_key(org_id=org_id, filename=filename)
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

    def cleanup_read_path(self, path: Path) -> None:
        """No-op: the local backend's path IS the permanent stored file."""

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
        return _safe_filename(filename)


class S3Storage:
    """S3 (or S3-compatible) object store, selected via STORAGE_BACKEND=s3.

    Implements the same public surface as :class:`StorageService` so no caller
    changes. ``boto3`` is imported lazily inside ``_client`` so this module —
    and the default local backend — never require it to be installed.
    """

    def __init__(self, *, bucket: str | None = None):
        self.bucket = bucket or settings.s3_bucket
        if not self.bucket:
            raise RuntimeError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        self._cached_client = None

    def _client(self):
        if self._cached_client is None:
            try:
                import boto3  # lazy: only needed for the S3 backend
            except ImportError as exc:  # pragma: no cover - depends on optional dep
                raise RuntimeError(
                    "boto3 is required for STORAGE_BACKEND=s3 (pip install boto3)"
                ) from exc
            self._cached_client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url or None,
                region_name=settings.s3_region or None,
            )
        return self._cached_client

    def save_bytes(self, *, org_id: str, filename: str, mime_type: str, content: bytes) -> StoredBytes:
        safe_filename = _safe_filename(filename)
        storage_key = _build_storage_key(org_id=org_id, filename=filename)
        self._client().put_object(
            Bucket=self.bucket,
            Key=storage_key,
            Body=content,
            ContentType=mime_type,
        )
        return StoredBytes(
            storage_key=storage_key,
            filename=safe_filename,
            mime_type=mime_type,
            size_bytes=len(content),
            sha256_hash=hashlib.sha256(content).hexdigest(),
        )

    def read_bytes(self, storage_key: str) -> bytes:
        self._guard_key(storage_key)
        try:
            response = self._client().get_object(Bucket=self.bucket, Key=storage_key)
        except Exception as exc:  # botocore ClientError (NoSuchKey) -> FileNotFoundError
            if self._is_not_found(exc):
                raise FileNotFoundError(storage_key) from exc
            raise
        return response["Body"].read()

    def path_for_read(self, storage_key: str) -> Path:
        """Materialize the object to a temp file and return its path.

        Callers (e.g. ``FileResponse``) expect a local ``Path``. S3 has no
        local path, so we stream the bytes to a NamedTemporaryFile that the OS
        reclaims on process exit. Keeps the public contract identical to the
        local backend.
        """
        data = self.read_bytes(storage_key)
        suffix = Path(storage_key).suffix
        with tempfile.NamedTemporaryFile(prefix="aegis_s3_", suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp.flush()
        return Path(tmp.name)

    def cleanup_read_path(self, path: Path) -> None:
        """Unlink the NamedTemporaryFile created by path_for_read.

        Every S3 download materializes bytes to disk with delete=False (so the
        Path stays valid for FileResponse to stream); without this, each
        download leaked one temp file forever. Callers should invoke this as a
        FileResponse background task, once the response has been sent."""
        path.unlink(missing_ok=True)

    def delete_bytes_permanently(self, storage_key: str) -> None:
        self._guard_key(storage_key)
        self._client().delete_object(Bucket=self.bucket, Key=storage_key)

    @staticmethod
    def _guard_key(storage_key: str) -> None:
        # Reject traversal-style keys for parity with the local backend's
        # is_relative_to() guard; S3 keys are flat but a "../" prefix signals a
        # malformed/forged key we should never act on.
        if storage_key.startswith("/") or ".." in Path(storage_key).parts:
            raise ValueError("Storage key resolves outside the storage root")

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = response.get("Error", {}).get("Code")
            return code in {"NoSuchKey", "404", "NotFound"}
        return False


def _build_storage_service() -> StorageService | S3Storage:
    if settings.storage_backend.lower() == "s3":
        return S3Storage()
    return StorageService()


storage_service: StorageService | S3Storage = _build_storage_service()
