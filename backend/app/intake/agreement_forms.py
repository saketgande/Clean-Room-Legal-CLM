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
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

_PATH = Path(__file__).with_name("agreement_forms.json")


@lru_cache(maxsize=1)
def form_defs() -> tuple[dict, ...]:
    return tuple(json.loads(_PATH.read_text(encoding="utf-8")))


def form_def(key: str | None) -> dict | None:
    return next((f for f in form_defs() if f["key"] == key), None) if key else None


def form_fields(key: str | None) -> list[SimpleNamespace]:
    """A form's fields in the shape the filing validator reads."""
    form = form_def(key)
    return [
        SimpleNamespace(key=f["key"], label=f["label"], kind=f["kind"], required=f["required"],
                        options=[{"value": o, "label": o} for o in f.get("options") or []] or None,
                        show=f.get("show"))
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
