"""`python eval/run_eval.py --obligations --check | --stored | --review-sheet [KEY] | --confirm KEY`: the free parts of
the obligation baseline.

--check         every answer key is valid against its frozen text, has no uncovered duty sentence, and scores
                100 percent when read back as an extraction (a scorer bug no real model would reveal); and every
                committed baseline still re-scores to the numbers it was named with, against the keys it was named from.
--stored        score the obligations stored in the dev database today against each key (read-only). Used to
                calibrate the scorer against the audit's hand counts.
--review-sheet  write the sheet a lawyer reads, ticks and signs to confirm a key (eval/obligations/review/).
--confirm KEY   record a lawyer's confirmation of KEY. Needs a person at a terminal: the AI never confirms its own key.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from . import adapters, gold, score, sheet


def _assess(path: Path) -> tuple[gold.Gold | None, list[str]]:
    """Everything that makes a key unfit to be confirmed or scored: invalid against its text, a duty sentence no atom
    covers, or a key that does not score 100 percent when read back as an extraction. (key, problems)."""
    issues = gold.problems(path)
    if issues:
        return None, issues
    key = gold.load(path)
    found = [f"a duty sentence no atom or not-duty covers: {hole}" for hole in gold.lint(key)]
    oracle = score.score(key, adapters.rows_from_gold(key))
    if not (oracle["recall"] == 1 and oracle["precision"] == 1 and oracle["side"]["value"] == 1
            and not oracle["invented_dates"]):
        found.append("the key read back as an extraction does not score 100 percent (scorer or key bug)")
    return key, found


def check_baselines(folder: Path | None = None) -> bool:
    """Each committed baseline (results/baseline-NAME/) must still be what it was named as: its results untouched since
    the day it was named, the keys it was scored against unchanged and still lawyer-confirmed, and every answered call
    re-scoring to the same numbers with today's scorer (so a scorer fix can never quietly flatter a later change).
    Prints one line per baseline; returns True when any failed."""
    failed = False
    for base in sorted((folder or gold.ROOT / "results").glob("baseline-*")):
        found: list[str] = []
        try:
            manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
            body = (base / "results.json").read_bytes()
            doc = json.loads(body)
            keys = {k.id: k for k in gold.load_all()}
        except (OSError, ValueError, gold.GoldError) as exc:
            print(f"FAIL {base.name}: cannot be read ({exc})")
            failed = True
            continue
        if hashlib.sha256(body).hexdigest() != manifest.get("results_sha256"):
            found.append("results.json was changed after the baseline was named")
        files = [call for name in ("run-a.json", "run-b.json") for call in json.loads((base / name).read_text(encoding="utf-8"))]
        if files != doc["calls"]:
            found.append("run-a.json and run-b.json do not hold the same calls as results.json")
        for entry in manifest.get("keys", []):
            key = keys.get(entry["id"])
            if key is None:
                found.append(f"key {entry['id']} no longer exists")
            elif key.fingerprint != entry["fingerprint"]:
                found.append(f"key {key.id} was edited since this baseline was named "
                             f"(fingerprint {entry['fingerprint']} is now {key.fingerprint})")
            elif not key.confirmed:
                found.append(f"key {key.id} is no longer lawyer-confirmed: {key.status_problem}")
            else:
                for call in (c for c in doc["calls"] if c["key"] == key.id and c["ok"]):
                    if json.loads(json.dumps(score.score(key, call["rows"]))) != call["score"]:
                        found.append(f"{key.id} run {call['run'] + 1} no longer re-scores to the stored numbers")
        print(f"{'FAIL' if found else 'ok  '} {base.name}: {len(manifest.get('keys', []))} key(s), "
              f"{len(doc['calls'])} calls")
        for line in found:
            print(f"  - {line}")
        failed = failed or bool(found)
    return failed


def check() -> int:
    failed = False
    keys = gold.key_files()
    if not keys:
        print("No answer keys found under eval/obligations/gold/")
        return 1
    for path in keys:
        issues = gold.problems(path)
        if issues:
            failed = True
            print(f"FAIL {path.name}")
            print("\n".join(f"  - {issue}" for issue in issues))
            continue
        key = gold.load(path)
        holes = gold.lint(key)
        oracle = score.score(key, adapters.rows_from_gold(key))
        perfect = (oracle["recall"] == 1 and oracle["precision"] == 1 and oracle["side"]["value"] == 1
                   and not oracle["invented_dates"])
        failed = failed or bool(holes) or not perfect
        print(f"{'ok  ' if not holes and perfect else 'FAIL'} {path.name}: {len(key.atoms)} atoms, "
              f"{len(key.not_duties)} not-duties, status {key.effective_status}, fingerprint {key.fingerprint}")
        if key.status.get("state") == gold.CONFIRMED and key.status_problem:
            print(f"  - NOT confirmed any more: {key.status_problem}")
        for hole in holes:
            print(f"  - a duty sentence no atom or not-duty covers: {hole}")
        if not perfect:
            print("  - the key read back as an extraction does not score 100 percent (scorer or key bug)")
    return 1 if (check_baselines() or failed) else 0


def review_sheets(key_id: str | None = None) -> int:
    """Write the lawyer's review sheet for one key (or every key)."""
    paths = [gold.ROOT / "gold" / f"{key_id}.json"] if key_id else gold.key_files()
    status = 0
    for path in paths:
        if not path.is_file():
            print(f"No answer key {path.stem!r} under eval/obligations/gold/")
            return 1
        key, issues = _assess(path)
        if key is None:
            print(f"FAIL {path.name}: " + "; ".join(issues))
            status = 1
            continue
        for issue in issues:
            print(f"warning {path.name}: {issue}")
        written = sheet.write_sheet(key)
        print(f"{written}: {len(key.atoms)} atoms, {len(key.not_duties)} not-duties, fingerprint {key.fingerprint}, "
              f"status {key.effective_status}")
    print("Send the file as it is, or open it and print to PDF. It holds the full contract text and is git-ignored "
          "(eval/obligations/review/): do not commit it unless the text's origin is traced.")
    return status


def confirm(key_id: str, *, ask: Callable[[str], str] = input, interactive: bool | None = None,
            today: str | None = None) -> int:
    """Record a lawyer's confirmation of one key. Refuses without a person at a terminal (the AI never confirms its own
    suggestion, and nothing in CI or a script may either), for a key that is invalid or has holes, and takes the lawyer's
    name and the key's id typed by hand. The confirmation is bound to the key's fingerprint: edit the key afterwards and
    it is a draft again."""
    at_terminal = (sys.stdin.isatty() and sys.stdout.isatty()) if interactive is None else interactive
    if not at_terminal:
        print("Confirming an answer key needs a person at a terminal. A lawyer reads the whole text and every row "
              "first (--review-sheet); the AI never confirms its own suggestion.")
        return 1
    path = gold.ROOT / "gold" / f"{key_id}.json"
    if not path.is_file():
        print(f"No answer key {key_id!r} under eval/obligations/gold/")
        return 1
    key, issues = _assess(path)
    if issues:
        print(f"{key_id} cannot be confirmed yet:\n" + "\n".join(f"  - {issue}" for issue in issues))
        return 1
    if key.confirmed:
        print(f"{key_id} is already confirmed by {key.status['confirmed_by']} on {key.status['confirmed_at']}, "
              f"and unchanged since (fingerprint {key.fingerprint}).")
        return 0
    print(f"Key {key_id}: {len(key.atoms)} atoms, {len(key.not_duties)} not-duties, fingerprint {key.fingerprint}.")
    print("This records that a lawyer read the WHOLE text and agrees with every atom and every not-duty, on the sheet "
          "made by --review-sheet.")
    by = ask("Name of the lawyer who did: ").strip()
    if not by:
        print("No name given: nothing was changed.")
        return 1
    if ask(f"Type the key id ({key_id}) to confirm: ").strip() != key_id:
        print("That is not the key id: nothing was changed.")
        return 1
    # Bound to the sheet the lawyer signed: its footer carries the fingerprint of the key it was made from. A key edited
    # since (or a sheet made from another version) has a different one, and a confirmation of that sheet would not cover it.
    signed = ask("Type the fingerprint printed at the foot of the sheet the lawyer signed: ").strip().lower()
    if signed != key.fingerprint:
        print(f"That sheet was made from a different version of this key (the sheet says {signed or 'nothing'}, "
              f"the key is now {key.fingerprint}). Make a new sheet (--review-sheet {key_id}), have it confirmed, and "
              "try again: nothing was changed.")
        return 1
    when = today or datetime.now(UTC).date().isoformat()
    gold.write_confirmation(path, by=by, at=when, fingerprint=key.fingerprint)
    print(f"{key_id} is confirmed by {by} on {when}. Edit any atom, date or the text and it is a draft again.")
    return 0


def stored() -> int:
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        for key in gold.load_all():
            contract_id = key.provenance.get("source_contract_id")
            if not contract_id:
                print(f"{key.id}: no provenance.source_contract_id; skipped")
                continue
            print(score.scorecard(score.score(key, adapters.rows_from_stored(db, contract_id))))
            print()
    finally:
        db.close()
    return 0


def main(*, stored_rows: bool = False, review_sheet: str | None = None, confirm_key: str | None = None) -> int:
    if confirm_key is not None:
        return confirm(confirm_key)
    if review_sheet is not None:
        return review_sheets(review_sheet or None)
    return stored() if stored_rows else check()
