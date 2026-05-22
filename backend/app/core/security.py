import base64
from datetime import UTC, datetime, timedelta
import hashlib
import secrets
from typing import Any
import uuid

import bcrypt
import jwt
from jwt import PyJWTError

from app.core.config import settings


ALGORITHM = "HS256"


def _bcrypt_secret(password: str) -> bytes:
    # SHA-256 pre-hash + base64. bcrypt only consumes the first 72 bytes of
    # its input, so without pre-hashing, two long passwords sharing the first
    # 72 bytes hash identically. SHA-256 collapses arbitrary-length input to a
    # fixed 32-byte digest; base64 keeps the bytes printable and well under
    # bcrypt's 72-byte ceiling (44 bytes encoded), and avoids a NUL byte
    # truncation issue some bcrypt implementations had with raw digests.
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def _legacy_bcrypt_secret(password: str) -> bytes:
    """The pre-SHA-256-prehash input: truncated raw UTF-8.

    Used only to verify hashes created before the KDF change so existing
    accounts keep working; ``password_needs_rehash`` flags these for upgrade.
    """
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_bcrypt_secret(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    pw_hash = password_hash.encode("utf-8")
    try:
        if bcrypt.checkpw(_bcrypt_secret(password), pw_hash):
            return True
        # Legacy fallback: hashes created before the SHA-256 pre-hash KDF
        # change. Without this, that one-line change in ``_bcrypt_secret``
        # would silently invalidate every account created earlier. Accept
        # the legacy hash; ``login_user`` re-hashes it to the new scheme on
        # the next successful login (see ``password_needs_rehash``).
        return bcrypt.checkpw(_legacy_bcrypt_secret(password), pw_hash)
    except ValueError:
        return False


def password_needs_rehash(password: str, password_hash: str) -> bool:
    """True when the stored hash only verified via the legacy bcrypt path —
    the caller should re-store ``hash_password(password)`` so the account is
    migrated to the current SHA-256-pre-hash scheme."""
    pw_hash = password_hash.encode("utf-8")
    try:
        if bcrypt.checkpw(_bcrypt_secret(password), pw_hash):
            return False
        return bcrypt.checkpw(_legacy_bcrypt_secret(password), pw_hash)
    except ValueError:
        return False


def create_token_secret(prefix: str | None = None) -> str:
    token = secrets.token_urlsafe(48)
    return f"{prefix}{token}" if prefix else token


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(
    subject: str, claims: dict[str, Any] | None = None
) -> tuple[str, dict[str, Any]]:
    """Return ``(token, payload)`` so callers can persist the ``jti`` (for
    revocation) and ``exp`` (for revocation-row TTL) without re-decoding the
    token they just signed."""
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": issued_at,
        "exp": expires_at,
        "typ": "access",
        "jti": str(uuid.uuid4()),
    }
    if claims:
        payload.update(claims)
    token = jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
    # PyJWT 1.x returned bytes, 2.x returns str. Normalize defensively so any
    # downstream code that still bytes-checks doesn't fight the version.
    token_str = token if isinstance(token, str) else token.decode("utf-8")
    return token_str, payload


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except PyJWTError as exc:
        raise ValueError("Invalid access token") from exc
    if payload.get("typ") != "access" or not payload.get("jti"):
        raise ValueError("Invalid access token")
    return payload
