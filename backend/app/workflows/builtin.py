"""Curated, read-only built-in workflows.

Ships a library of prebuilt legal workflows (assistant prompt presets and
tabular-review column presets) so users have high-quality starting points
without authoring their own. Built-ins are org-agnostic and merged into the
workflow list at read time — they are never persisted and cannot be edited
or deleted.
"""

import json
from functools import lru_cache
from pathlib import Path

_DATA_FILE = Path(__file__).with_name("builtin_workflows.json")


@lru_cache(maxsize=1)
def builtin_workflows() -> list[dict]:
    with _DATA_FILE.open(encoding="utf-8") as fh:
        return json.load(fh)
