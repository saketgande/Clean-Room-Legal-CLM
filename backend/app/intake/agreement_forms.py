"""The agreement wizard's nine forms — the only request forms the app has.

``agreement_forms.json`` is the server's copy of the fields each wizard form asks
for (``AGREEMENT_FORMS`` in frontend ``_agreement-forms.tsx``). A frontend test
fails if the two drift apart, so the wizard and the server always agree on what
is required.

There is no request-type table behind these (removed 2026-09-29): a request
names its form in ``field_values.request_form``, workflows say which forms they
are "Used for", and filing validates against the fields here.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from app.contracts.blanks import first_blank

_PATH = Path(__file__).with_name("agreement_forms.json")


@lru_cache(maxsize=1)
def form_defs() -> tuple[dict, ...]:
    return tuple(json.loads(_PATH.read_text(encoding="utf-8")))


def form_def(key: str | None) -> dict | None:
    return next((f for f in form_defs() if f["key"] == key), None) if key else None


def _column(c: dict) -> SimpleNamespace:
    return SimpleNamespace(key=c["key"], label=c["label"], kind=c["kind"], max_length=c.get("max_length"),
                           unique=c.get("unique", False), no_blanks=c.get("no_blanks", False),
                           within=c.get("within"), total=c.get("total"), show=c.get("show"))


def form_fields(key: str | None) -> list[SimpleNamespace]:
    """A form's fields in the shape the filing validator reads."""
    form = form_def(key)
    return [
        SimpleNamespace(key=f["key"], label=f["label"], kind=f["kind"], required=f["required"],
                        options=[{"value": o, "label": o} for o in f.get("options") or []] or None,
                        show=f.get("show"), not_before=f.get("not_before"),
                        no_blanks=f.get("no_blanks", False), min_exclusive=f.get("min_exclusive"),
                        min_rows=f.get("min_rows"), max_rows=f.get("max_rows"),
                        legacy_key=f.get("legacy_key"), legacy_label=f.get("legacy_label"),
                        columns=[_column(c) for c in f.get("columns") or []] or None)
        for f in (form["fields"] if form else [])
    ]


def shown(field, values: dict) -> bool:
    """Whether a conditional question applies: every ``show`` rule must match.
    A rule on a multi-select answer matches when any chosen option is listed."""
    for rule in getattr(field, "show", None) or []:
        answer = values.get(rule["field"])
        chosen = answer if isinstance(answer, list) else [answer]
        if not any(a in rule["in"] for a in chosen):
            return False
    return True


# --- the "rows" question kind: a table whose rows are filed with the request -------------------------
# One definition for filing and updating; frontend lib/form-checks.ts mirrors it and both are pinned by
# tests/fixtures/deliverable_rows_cases.json, so the wizard can never accept what the server refuses.
# Everything is ASCII on purpose (digits, whitespace): Python's \d and str.strip() accept look-alikes
# that JavaScript's do not.

_SPACE = " \t\r\n\f\v"
_SPACE_RUN = re.compile(r"[ \t\r\n\f\v]+")
_ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_PERCENT = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,2})?")
_CENT = Decimal("0.01")


def not_a_table(field) -> HTTPException:
    return HTTPException(422, f'"{field.label}" must be a table of rows, not free text.')


def too_few_rows(field) -> HTTPException:
    n = field.min_rows or 1
    return HTTPException(422, f'"{field.label}" needs at least {n} row{"s" if n > 1 else ""}.')


def _tidy(raw) -> str:
    return _SPACE_RUN.sub(" ", "" if raw is None else str(raw)).strip(_SPACE)


def _day(text) -> date | None:
    if not isinstance(text, str) or not _ISO_DAY.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _long_date(d: date) -> str:
    return f"{d.day} {d:%B %Y}"


def _plain(d: Decimal) -> str:
    """Two decimals with trailing zeros dropped: 99 -> "99", 99.5 -> "99.5"."""
    text = f"{d.quantize(_CENT)}"
    return text.rstrip("0").rstrip(".")


def clean_rows(field, raw, values: dict, labels: dict) -> list[dict]:
    """The rows a table question holds, tidied, or a 422 naming the first problem.

    ``values`` are the form's other answers (pricing shows the share column; the services dates are the
    due-date window), ``labels`` the form's question labels. Row numbers are positions in ``raw`` as sent,
    counting rows that are ignored, so the wizard and the server number rows alike. A null or "" answer
    never gets here: the filing validator treats it as missing first."""
    if not isinstance(raw, list):
        raise not_a_table(field)
    for n, row in enumerate(raw, 1):
        if not isinstance(row, dict):
            raise HTTPException(422, f'"{field.label}", row {n} is not a row of the table.')
    cols = [c for c in field.columns if shown(c, values)]
    rows = []
    for n, row in enumerate(raw, 1):
        cells = {c.key: _tidy(row.get(c.key)) for c in cols}
        if any(cells.values()):  # a row left entirely empty is ignored
            rows.append((n, cells))
    if len(rows) < (field.min_rows or 1):
        raise too_few_rows(field)
    if field.max_rows and len(rows) > field.max_rows:
        raise HTTPException(
            422, f'"{field.label}" can have at most {field.max_rows} rows (this has {len(rows)}).')

    for n, cells in rows:
        for c in cols:
            cell, where = cells[c.key], f'"{field.label}", row {n}: "{c.label}"'
            if not cell:
                raise HTTPException(422, f"{where} is required.")
            if c.kind == "text":
                if c.max_length and len(cell) > c.max_length:
                    raise HTTPException(422, f"{where} is longer than {c.max_length} characters.")
                blank = first_blank(cell) if c.no_blanks else None
                if blank:
                    raise HTTPException(422, f"{where} contains {blank}, which reads as a blank to fill in "
                                             "later. Reword it without square brackets.")
            elif c.kind == "date":
                if _day(cell) is None:
                    raise HTTPException(422, f"{where} must be a date in YYYY-MM-DD form.")
                start, end = ((values.get(k) for k in c.within) if c.within else (None, None))
                if _day(start) and _day(end) and not start <= cell <= end:
                    raise HTTPException(
                        422, f"{where} must be between {_long_date(_day(start))} and {_long_date(_day(end))} "
                             f"({labels.get(c.within[0], c.within[0])} to {labels.get(c.within[1], c.within[1])}).")
            elif c.kind == "percent":
                share = Decimal(cell) if _PERCENT.fullmatch(cell) else None
                if share is None or not 0 < share <= 100:
                    raise HTTPException(422, f"{where} must be a number above 0 and at most 100, with no "
                                             "more than two decimal places.")
                cells[c.key] = str(share.quantize(_CENT))

    for c in (c for c in cols if c.unique):
        first: dict[str, tuple[int, str]] = {}
        for n, cells in rows:
            earlier = first.setdefault(cells[c.key].lower(), (n, cells[c.key]))
            if earlier[0] != n:
                raise HTTPException(
                    422, f'"{field.label}", rows {earlier[0]} and {n} have the same "{c.label}" '
                         f'("{earlier[1]}"). Give each row its own.')
    for c in (c for c in cols if c.total is not None):
        total = sum((Decimal(cells[c.key]) for _, cells in rows), Decimal(0))
        if total != c.total:
            gap = abs(total - c.total)
            raise HTTPException(
                422, f'"{field.label}": the "{c.label}" column adds up to {_plain(total)}%, {_plain(gap)} '
                     f'{"short of" if total < c.total else "over"} {c.total}%.')
    return [cells for _, cells in rows]


def answer_text(form_key: str | None, key: str, value) -> str:
    """An answer as the AI prompts show it: a rows answer one numbered line per row, anything else as is."""
    if not isinstance(value, list):
        return str(value)
    field = next((f for f in form_fields(form_key) if f.key == key and f.kind == "rows"), None)
    if field is None:
        return str(value)
    names = {c.key: c.label for c in field.columns}
    return "".join(
        f"\n    {n}. " + "; ".join(f"{names.get(k, k)}: {v}" for k, v in row.items())
        for n, row in enumerate(value, 1) if isinstance(row, dict))
