"""Attachment pre-flight (FR-26, FR-27, FR-29, FR-30). Runs before ANY upstream call and
never fetches anything: a URL or filesystem path in `content_base64` is refused outright.

Order is fixed by the plan and stops at the first failure: count, reference-looking value,
strict base64, empty, per-file size, total size. File type and content checks stay with the
platform so the accepted-type list cannot drift (FR-28)."""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .errors import ToolError

MIB = 1024 * 1024
MAX_FILES = 5
MAX_FILE_BYTES = 25 * MIB
MAX_TOTAL_BYTES = 40 * MIB

_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_WHITESPACE = re.compile(r"\s+")
_B64_CHARS = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")


class AttachmentLike(Protocol):
    filename: str
    content_base64: str


@dataclass(frozen=True)
class CheckedAttachment:
    filename: str
    size_bytes: int
    content_base64: str  # whitespace removed; safe to forward as `content_b64`


def _mb(n: float) -> str:
    return f"{n / MIB:.1f}"


def _over(excess: int) -> str:
    # One decimal as in the plan, but a 1-byte overage must not read "over by 0.0 MB".
    return "less than 0.1 MB" if excess / MIB < 0.05 else f"{_mb(excess)} MB"


def _strict_b64(value: str) -> bool:
    if not _B64_CHARS.match(value) or len(value) % 4:
        return False
    try:
        base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return False
    return True


def _looks_like_reference(value: str) -> bool:
    if _URL.match(value) or _DRIVE.match(value):
        return True
    if value.startswith(("~", "\\")):
        return True
    # "/" is also a legitimate first base64 character (a JPEG starts "/9j/"), so a leading
    # "/" is only a path when the whole value is not valid base64.
    return value.startswith("/") and not _strict_b64(value)


def preflight_attachments(items: Sequence[AttachmentLike]) -> list[CheckedAttachment]:
    if len(items) > MAX_FILES:
        extra = len(items) - MAX_FILES
        raise ToolError(
            "too_large",
            f"{len(items)} attachments were sent; at most {MAX_FILES} are allowed "
            f"({extra} too many). Nothing was sent to Aegis.",
            field="attachments",
        )

    cleaned: list[tuple[str, str]] = []
    for item in items:
        value = _WHITESPACE.sub("", item.content_base64)
        if _looks_like_reference(value):
            raise ToolError(
                "invalid_input",
                f"Attachment '{item.filename}' must be inline base64 content. "
                "URLs and file paths are not accepted, and nothing was fetched.",
                field="attachments",
            )
        cleaned.append((item.filename, value))

    checked: list[CheckedAttachment] = []
    for name, value in cleaned:
        if not _strict_b64(value):
            raise ToolError(
                "invalid_input",
                f"Attachment '{name}' is not valid base64.",
                field="attachments",
            )
        size = len(base64.b64decode(value, validate=True))
        if size == 0:
            raise ToolError("invalid_input", f"Attachment '{name}' is empty.", field="attachments")
        checked.append(CheckedAttachment(name, size, value))

    for att in checked:
        if att.size_bytes > MAX_FILE_BYTES:
            raise ToolError(
                "too_large",
                f"'{att.filename}' is {_mb(att.size_bytes)} MB; the limit is 25 MB per file "
                f"(over by {_over(att.size_bytes - MAX_FILE_BYTES)}). "
                "Nothing was sent to Aegis.",
                field="attachments",
            )
    total = sum(a.size_bytes for a in checked)
    if total > MAX_TOTAL_BYTES:
        raise ToolError(
            "too_large",
            f"Attachments total {_mb(total)} MB; the limit is 40 MB per filing "
            f"(over by {_over(total - MAX_TOTAL_BYTES)}). Nothing was sent to Aegis.",
            field="attachments",
        )
    return checked
