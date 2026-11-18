"""Value types several output models share. Pure Pydantic, like the rest of this package.

Each one is *lenient on the way in, exact on the way out*: Claude is told the precise type (a 0..1 number,
a real date, one of a fixed list) through the JSON Schema, while the Python side forgives a bad value
the way the hand-written ``_clean_*`` helpers did, so one odd field costs that field, not the whole answer.

  Score            0..1 number; an out-of-range number is clamped. Not a number at all is refused.
  LenientDate      a calendar date; anything that is not a real YYYY-MM-DD becomes None.
  NonNegInt        a whole number >= 0; anything else becomes None.
  Text             a string or None; a number is written out as text, anything else becomes None.
  lenient_choice   one of a fixed list or None; an unlisted value becomes ``fallback`` (None unless given).
                   With a ``fallback`` the field is never None.
  lenient_items    a list where an item that does not fit its model is dropped, not fatal.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, ValidationError


def _clamp_unit(value):
    # A bool is an int in Python (True would read as 1.0), so it is left to fall through to the refusal.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        try:
            return max(0.0, min(1.0, float(value.strip())))
        except ValueError:
            pass
    raise ValueError("a score is a number between 0 and 1")


Score = Annotated[
    float,
    BeforeValidator(_clamp_unit),
    Field(json_schema_extra={"minimum": 0, "maximum": 1}),
]


def _date_or_none(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        # Only the calendar day survives; naive is fine because the time is thrown away.
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()  # noqa: DTZ007
    except ValueError:
        return None


LenientDate = Annotated[date | None, BeforeValidator(_date_or_none)]


def _nonneg_int_or_none(value):
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


NonNegInt = Annotated[
    int | None,
    BeforeValidator(_nonneg_int_or_none),
    Field(json_schema_extra={"minimum": 0}),
]


def _text_or_none(value):
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


Text = Annotated[str | None, BeforeValidator(_text_or_none)]


def lenient_choice(values: tuple[str, ...], *, fallback: str | None = None, fold_case: bool = False):
    """``Literal[values]`` that turns an unlisted value into ``fallback`` instead of failing.

    Nullable only when there is no fallback."""

    def _pick(value):
        if value is None:
            return fallback
        candidate = value.strip().lower() if (fold_case and isinstance(value, str)) else value
        return candidate if candidate in values else fallback

    literal = Literal[values]  # type: ignore[valid-type]
    return Annotated[literal if fallback is not None else literal | None, BeforeValidator(_pick)]


def lenient_items(item: type[BaseModel]):
    """``list[item]`` that drops entries which do not validate (the old code dropped malformed rows)."""

    def _keep(value):
        if not isinstance(value, list):
            return value  # left for the list check to refuse
        kept = []
        for entry in value:
            try:
                kept.append(item.model_validate(entry))
            except ValidationError:
                continue
        return kept

    return Annotated[list[item], BeforeValidator(_keep)]
