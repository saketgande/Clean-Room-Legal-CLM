"""Looking up and comparing a contract's versions for the chat assistant.

The web page already has a line diff between two versions (ContractService.compute_version_diff). What
the assistant needed on top of it is what people actually say: "compare v7 and v6". That means

- finding a version by its number, and saying so plainly when the number matches nothing or two
  files of the same contract each have one (numbers are unique per file, not per contract), instead
  of guessing which was meant;
- naming the version before the current one when the person did not say;
- handing a long diff over in windows, the way read_contract hands over a long contract.

Everything here is read-only. Who may call it (contract wall, permission) is decided by the caller.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.models import Contract

# How many characters of diff one call returns. A call always returns at least one line.
WINDOW_CHARS = 20000


def versions_of(db: Session, contract: Contract) -> list[ContractVersion]:
    """The contract's versions that have not been deleted, oldest first (the order the version list page uses)."""
    return list(
        db.scalars(
            select(ContractVersion)
            .where(
                ContractVersion.org_id == contract.org_id,
                ContractVersion.contract_id == contract.id,
                ContractVersion.deleted_at.is_(None),
            )
            .order_by(ContractVersion.version_number.asc(), ContractVersion.created_at.asc())
        )
    )


def _state(version: ContractVersion, current: ContractVersion | None) -> str:
    """The same words the version dropdown uses: current, earlier, or not in use (a proposal never applied)."""
    if current is None:
        return "unknown"
    if version.id == current.id:
        return "current"
    return "not in use" if version.version_number > current.version_number else "earlier"


def versions_with_text(db: Session, versions: list[ContractVersion]) -> set[str]:
    """Ids of the versions whose text was read and is not blank. One query for all of them."""
    snapshot_ids = [v.text_snapshot_id for v in versions if v.text_snapshot_id]
    if not snapshot_ids:
        return set()
    readable = set(
        db.scalars(
            select(ContractTextSnapshot.id).where(
                ContractTextSnapshot.id.in_(snapshot_ids),
                func.length(func.trim(ContractTextSnapshot.text)) > 0,
            )
        )
    )
    return {v.id for v in versions if v.text_snapshot_id in readable}


def describe(version: ContractVersion, current: ContractVersion | None, with_text: set[str]) -> dict[str, Any]:
    return {
        "version_id": version.id,
        "version_number": version.version_number,
        "source": version.source,
        "state": _state(version, current),
        "is_current": current is not None and version.id == current.id,
        "has_text": version.id in with_text,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "change_summary": version.change_summary,
    }


def current_version(contract: Contract, versions: list[ContractVersion]) -> ContractVersion | None:
    return next((v for v in versions if v.id == contract.current_authoritative_version_id), None)


def list_versions(db: Session, contract: Contract) -> dict[str, Any]:
    versions = versions_of(db, contract)
    current = current_version(contract, versions)
    with_text = versions_with_text(db, versions)
    return {
        "contract_id": contract.id,
        "title": contract.title,
        "current_version_number": current.version_number if current else None,
        "versions": [describe(v, current, with_text) for v in versions],
        "note": (
            "'current' is the version in use. 'earlier' versions were replaced by later ones. 'not in use' versions "
            "are newer proposals that were never applied. A version with has_text false cannot be compared."
        ),
    }


class _Lookup:
    """What the pickers need to describe a version: the versions, which one is current, which have text."""

    def __init__(self, db: Session, contract: Contract):
        self.versions = versions_of(db, contract)
        self.current = current_version(contract, self.versions)
        self.with_text = versions_with_text(db, self.versions)

    def describe(self, version: ContractVersion) -> dict[str, Any]:
        return describe(version, self.current, self.with_text)

    def not_found(self, reason: str, message: str, **extra) -> dict[str, Any]:
        return {
            "found": False,
            "reason": reason,
            "message": message,
            "versions": [self.describe(v) for v in self.versions],
            **extra,
        }


def _pick(lookup: _Lookup, *, number: int | None, version_id: str | None, label: str):
    """(version, None) or (None, the not-found answer)."""
    if version_id:
        match = next((v for v in lookup.versions if v.id == version_id), None)
        if match is None:
            return None, lookup.not_found(
                "no_such_version", f"The {label} version id does not belong to this contract."
            )
        return match, None
    matches = [v for v in lookup.versions if v.version_number == number]
    if not matches:
        return None, lookup.not_found(
            "no_such_version", f"This contract has no version {number}. The versions it has are listed."
        )
    if len(matches) > 1:
        return None, lookup.not_found(
            "ambiguous_version",
            f"More than one version is numbered {number} (they belong to different files of this contract). "
            "Say which one by version_id.",
            candidates=[lookup.describe(v) for v in matches],
        )
    return matches[0], None


def resolve_pair(
    db: Session,
    contract: Contract,
    *,
    base_number: int | None,
    target_number: int | None,
    base_id: str | None,
    target_id: str | None,
) -> tuple[ContractVersion | None, ContractVersion | None, _Lookup, dict[str, Any] | None]:
    """The (older, newer) pair to compare, the lookup used, and None; or, when it cannot be done, a
    not-found answer saying why in place of the last item."""
    lookup = _Lookup(db, contract)
    if target_number is None and not target_id:
        if lookup.current is None:
            return None, None, lookup, lookup.not_found("no_current_version", "This contract has no current version.")
        target = lookup.current
    else:
        target, problem = _pick(lookup, number=target_number, version_id=target_id, label="newer")
        if problem:
            return None, None, lookup, problem
    if base_number is None and not base_id:
        earlier = [v for v in lookup.versions if v.version_number < target.version_number]
        if not earlier:
            return None, None, lookup, lookup.not_found(
                "no_earlier_version",
                f"Version {target.version_number} is the first version, so there is nothing before it to compare with.",
            )
        base = earlier[-1]
    else:
        base, problem = _pick(lookup, number=base_number, version_id=base_id, label="older")
        if problem:
            return None, None, lookup, problem
    if base.id == target.id:
        return None, None, lookup, lookup.not_found(
            "same_version", f"Both sides are version {base.version_number}; pick two different versions."
        )
    for version in (base, target):
        if version.id not in lookup.with_text:
            return None, None, lookup, lookup.not_found(
                "version_has_no_text",
                f"Version {version.version_number}'s text has not been read, so it cannot be compared.",
            )
    return base, target, lookup, None


def format_line(line: dict[str, str]) -> str:
    kind, text = line.get("type"), line.get("text", "")
    if kind == "add":
        return f"+ {text}"
    if kind == "remove":
        return f"- {text}"
    if kind == "gap":
        return f"… {text} …"
    return f"  {text}"


def window(lines: list[str], offset: int) -> tuple[list[str], int | None]:
    """The lines from `offset` on, up to WINDOW_CHARS (at least one line), and the offset to continue from."""
    start = max(0, min(offset, len(lines)))
    out: list[str] = []
    used = 0
    index = start
    while index < len(lines):
        size = len(lines[index]) + 1
        if out and used + size > WINDOW_CHARS:
            break
        out.append(lines[index])
        used += size
        index += 1
    return out, (index if index < len(lines) else None)
