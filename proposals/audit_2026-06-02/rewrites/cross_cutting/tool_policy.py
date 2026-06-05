"""Cross-cutting decision CC-3 — runtime assistant tool policy gate.

Resolves Agent 2 finding F-04: ``ToolSpec.feature_flag`` is declared at
``backend/app/ai/tool_registry.py:127,211,229`` and elsewhere but never
consulted by either ``AIController._assistant_tool_schemas``
(``backend/app/ai/controller.py:594-608``) or ``ToolRuntime.execute``
(``backend/app/ai/tool_runtime.py:86-157``). This module wires the flag
through.

Intended location: ``backend/app/ai/tool_policy.py``. Both schema-build
and execution paths must call ``AssistantToolPolicy.is_enabled`` before
offering or running a tool.
"""

from __future__ import annotations

import logging
import time
from threading import RLock
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tool_registry import tool_registry
from app.core.models import AdminSetting

_logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS: float = 10.0


class _CacheEntry(TypedDict):
    value: bool
    expires_at: float


class AssistantToolPolicy:
    """Runtime gate for assistant tool feature flags backed by AdminSetting."""

    _cache: dict[tuple[str, str], _CacheEntry] = {}
    _cache_lock: RLock = RLock()

    @classmethod
    def is_enabled(
        cls,
        tool_name: str,
        db: Session,
        org_id: str,
    ) -> bool:
        """Return True iff the tool's feature_flag (if any) resolves truthy for org."""
        try:
            spec = tool_registry.get(tool_name)
        except KeyError:
            return False
        flag = spec.feature_flag
        if not flag:
            # No flag declared on the spec — tool participation is governed by
            # ``enabled_by_default`` and the permission check, which remain
            # the responsibility of the caller.
            return True
        cache_key = (org_id, flag)
        now = time.monotonic()
        with cls._cache_lock:
            entry = cls._cache.get(cache_key)
            if entry is not None and entry["expires_at"] > now:
                return entry["value"]
        value = cls._lookup_setting(db, org_id=org_id, flag=flag)
        with cls._cache_lock:
            cls._cache[cache_key] = {
                "value": value,
                "expires_at": now + _CACHE_TTL_SECONDS,
            }
        return value

    @classmethod
    def clear_cache(cls) -> None:
        """Invalidate the full policy cache (test/admin-toggle hook)."""
        with cls._cache_lock:
            cls._cache.clear()

    @staticmethod
    def _lookup_setting(db: Session, *, org_id: str, flag: str) -> bool:
        """Look up the AdminSetting row keyed by the tool's feature_flag string."""
        row = db.scalar(
            select(AdminSetting).where(
                AdminSetting.org_id == org_id,
                AdminSetting.key == flag,
            )
        )
        if row is None:
            # No explicit row: default-enabled. Customers who never write a
            # setting still get the tool. They opt OUT by writing
            # ``{"enabled": false}``.
            return True
        value = row.value
        if isinstance(value, dict):
            enabled = value.get("enabled")
            if enabled is None:
                enabled = value.get("value")
            return _coerce_bool(enabled, default=True)
        return _coerce_bool(value, default=True)


def _coerce_bool(value: object, *, default: bool) -> bool:
    """Coerce an AdminSetting value to bool with a default for absent/unknown."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on", "enabled"}:
            return True
        if normalized in {"false", "0", "no", "off", "disabled"}:
            return False
    return default
