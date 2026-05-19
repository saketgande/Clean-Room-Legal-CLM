from datetime import UTC, datetime, timedelta
import hashlib
import secrets
from typing import Any
import uuid

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings


ALGORITHM = "HS256"


def _bcrypt_secret(password: str) -> bytes:
    # bcrypt only uses the first 72 bytes; longer inputs raise in bcrypt 4+.
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_bcrypt_secret(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    pw_hash = password_hash.encode("utf-8")
    try:
        if bcrypt.checkpw(_bcrypt_secret(password), pw_hash):
            return True
        # Legacy fallback: pre-hardening hashes were raw-password bcrypt
        # (Passlib CryptContext schemes=["bcrypt"]). Without this, the
        # SHA-256 pre-hash change silently invalidates every account created
        # before it. Accept the legacy hash; login_user re-hashes it to the
        # new scheme on the next successful login (see password_needs_rehash).
        return bcrypt.checkpw(password.encode("utf-8"), pw_hash)
    except ValueError:
        return False


def password_needs_rehash(password: str, password_hash: str) -> bool:
    """True when the stored hash only verified via the legacy raw-bcrypt
    fallback — the caller should re-store ``hash_password(password)`` so the
    account is migrated to the current scheme."""
    pw_hash = password_hash.encode("utf-8")
    try:
        if bcrypt.checkpw(_bcrypt_secret(password), pw_hash):
            return False
        return bcrypt.checkpw(password.encode("utf-8"), pw_hash)
    except ValueError:
        return False


def create_token_secret(prefix: str | None = None) -> str:
    token = secrets.token_urlsafe(48)
    return f"{prefix}{token}" if prefix else token


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(subject: str, claims: dict[str, Any] | None = None) -> str:
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "exp": expires_at,
        "typ": "access",
        "jti": str(uuid.uuid4()),
    }
    if claims:
        payload.update(claims)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise ValueError("Invalid access token") from exc
    if payload.get("typ") != "access" or not payload.get("jti"):
        raise ValueError("Invalid access token")
    return payload
