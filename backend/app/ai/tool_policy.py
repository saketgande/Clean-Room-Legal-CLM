"""Runtime enablement policy for assistant tools (finding F-04).

The ToolSpec.feature_flag was declared but never consulted at runtime, so admin
toggles in the AdminSetting table had no effect — a disabled AI tool still ran.
This module is the single source of truth, consulted in three places:

  * controller._assistant_tool_schemas — so disabled tools are never offered to Claude
  * tool_runtime.execute               — a hard gate (defense-in-depth) on the live tool loop
  * tool_runtime.execute_confirmed     — re-gate on the confirmation/resume path, so a tool
                                         disabled after a confirmation was minted cannot still run

It mirrors the existing skill gate (controller._ensure_skill_enabled): an explicit
AdminSetting row overrides the static default; absent a row, the static default wins.

`AdminSetting.value` is a free-form JSON column, so a "disabled" toggle may arrive as a
JSON boolean `false`, an int `0`, or the *string* `"false"`. A naive `bool(value)` is
fail-OPEN — `bool("false")` is True — meaning a disable would silently do nothing. The
coercion below treats falsey strings/numbers as disabled so the control fails safe.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tool_registry import ToolSpec
from app.core.models import AdminSetting

# Stored JSON values that mean "off" when a flag is persisted as a string/number.
_FALSEY_FLAG_TOKENS: frozenset[str] = frozenset(
    {"false", "0", "no", "off", "null", "none", "disabled", ""}
)


def flag_value_is_enabled(value: object, *, default: bool) -> bool:
    """Coerce a JSON-stored feature-flag value to a bool without the bool("false") fail-open; falls back to `default` when the value carries no decision (None)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() not in _FALSEY_FLAG_TOKENS
    return bool(value)


def is_tool_enabled(db: Session, *, org_id: str, spec: ToolSpec) -> bool:
    """Return whether `spec` is enabled for `org_id`, honoring the DB-backed feature-flag override; falls back to the static default when no AdminSetting row exists."""
    if spec.feature_flag is None:
        return spec.enabled_by_default
    setting = db.scalar(
        select(AdminSetting).where(
            AdminSetting.org_id == org_id,
            AdminSetting.key == spec.feature_flag,
        )
    )
    if setting is None:
        return spec.enabled_by_default
    return flag_value_is_enabled(setting.value, default=spec.enabled_by_default)
