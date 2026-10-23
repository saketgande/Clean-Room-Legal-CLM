"""Post-ingest integrity audit for the upload pipeline.

Answers "what silently broke across the corpus?" — the failures that leave rows
behind rather than raising. Read-only; run it after any batch of uploads:

    docker compose exec backend python eval/audit_pipeline.py

Each check prints a rate, because the rate is what projects to the next 100
documents. Non-zero exit if any check is over its threshold.
"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import text

from app.core.database import SessionLocal
from app.integrations.storage import storage_service

# Deviations whose suggested_fix is process advice, not contract language. Applying
# one as a redline edits the document with commentary — see check_redlines.
META_CLAUSE_TYPES = {"contract_type_mismatch"}
ADVICE = re.compile(
    r"apply the correct playbook|does not apply to this contract type"
    r"|cannot be meaningfully evaluated|playbook rules for",
    re.IGNORECASE,
)

_NDA = re.compile(r"\bnda\b|non[- ]?disclosure|confidential", re.IGNORECASE)

failures: list[str] = []


def report(name: str, bad: int, total: int, *, limit_pct: float, note: str = "") -> None:
    pct = 100.0 * bad / total if total else 0.0
    flag = "FAIL" if pct > limit_pct else "ok  "
    if flag == "FAIL":
        failures.append(f"{name}: {pct:.1f}% > {limit_pct}%")
    print(f"  [{flag}] {name:38} {bad:5}/{total:<5} {pct:5.1f}%  {note}")


def check_storage(db) -> None:
    """Bytes on disk must match what the DB says, and every object must be reachable."""
    print("\nSTORAGE")
    rows = db.execute(
        text(
            """
            select so.id, so.storage_key, so.size_bytes, so.sha256_hash,
                   (select count(*) from contract_version cv
                     where cv.storage_object_id = so.id) refs
            from storage_object so
            """
        )
    ).all()
    missing = corrupt = orphan = 0
    wasted = 0
    for r in rows:
        if r.refs == 0:
            orphan += 1
            wasted += r.size_bytes or 0
        try:
            blob = storage_service.read_bytes(r.storage_key)
        except Exception:
            missing += 1
            continue
        if len(blob) != r.size_bytes or hashlib.sha256(blob).hexdigest() != r.sha256_hash:
            corrupt += 1
    n = len(rows)
    report("bytes missing from disk", missing, n, limit_pct=0)
    report("bytes corrupt (size/hash)", corrupt, n, limit_pct=0)
    report("orphaned (no version refs)", orphan, n, limit_pct=5, note=f"{wasted/1024:.0f} KB leaked")

    dupes = db.execute(
        text(
            "select count(*) - count(distinct sha256_hash) from storage_object"
        )
    ).scalar()
    report("redundant identical copies", dupes or 0, n, limit_pct=10, note="no content dedupe")


def check_offsets(db) -> None:
    """char offsets must actually locate their own text in their own snapshot.

    The deterministic parser (ContractDocumentElement) computes them; the LLM
    (ClauseExtraction) reports them, and a model cannot count characters — so
    this check is what separates a real citation from a plausible-looking one.
    """
    print("\nOFFSET INTEGRITY  (citations resolve to the right span)")
    for label, sql in (
        (
            "parser elements",
            """select de.char_start c_start, de.char_end c_end, de.text c_text, sn.text snap, length(sn.text) snap_len
               from contract_document_element de
               join contract_text_snapshot sn on sn.id = de.text_snapshot_id""",
        ),
        (
            "LLM clauses",
            """select ce.start_char c_start, ce.end_char c_end, ce.text c_text, sn.text snap, length(sn.text) snap_len
               from clause_extraction ce
               join contract_text_snapshot sn on sn.id = ce.text_snapshot_id""",
        ),
    ):
        rows = db.execute(text(sql)).all()
        nulls = oob = mism = exact = 0
        for r in rows:
            if r.c_start is None or r.c_end is None:
                nulls += 1
            elif r.c_start < 0 or r.c_end > r.snap_len or r.c_start >= r.c_end:
                oob += 1
            elif (r.c_text or "").strip()[:60] != r.snap[r.c_start : r.c_end].strip()[:60]:
                mism += 1
            else:
                exact += 1
        total = len(rows)
        report(f"{label}: no offsets", nulls, total, limit_pct=5)
        report(f"{label}: out of bounds", oob, total, limit_pct=0)
        report(f"{label}: point at wrong text", mism, total, limit_pct=2, note=f"{exact} exact")


def check_playbooks(db) -> None:
    """A playbook must match the contract type it was run against."""
    print("\nPLAYBOOK SELECTION")
    rows = db.execute(
        text(
            """
            select c.contract_type ct, p.name pb, count(*) n
            from playbook_run pr
            join contract c on c.id = pr.contract_id
            join playbook p on p.id = pr.playbook_id
            group by 1, 2
            """
        )
    ).all()
    total = sum(r.n for r in rows)
    wrong = 0
    for r in rows:
        # \b matters: "Standard MSA Playbook" contains the substring "nda".
        if bool(_NDA.search(r.pb or "")) != bool(_NDA.search(r.ct or "")):
            wrong += r.n
    report("playbook/contract-type mismatch", wrong, total, limit_pct=0)


def check_redlines(db) -> None:
    """An auto-redline must edit clauses, not delete the parties block or insert advice."""
    print("\nAUTO-REDLINE SAFETY")
    rows = db.execute(
        text(
            """
            -- Compare against the authoritative version, which is what a redline
            -- is actually proposed against. Using version_number - 1 instead makes
            -- an earlier *rejected* redline the baseline, and every correct
            -- proposal after a bad one then looks like it deleted the document.
            select sn.text rtext, base_sn.text btext
            from contract_version cv
            join contract_text_snapshot sn on sn.contract_version_id = cv.id
            join contract c on c.id = cv.contract_id
            join contract_version base_cv
              on base_cv.id = c.current_authoritative_version_id
            join contract_text_snapshot base_sn
              on base_sn.contract_version_id = base_cv.id
            where cv.source = 'playbook_redline' and cv.id <> base_cv.id
            """
        )
    ).all()
    pairs = [r for r in rows if r.btext]
    advice = headless = shrank = 0
    for r in pairs:
        base_lines, new_lines = r.btext.splitlines(), r.rtext.splitlines()
        added = [ln for ln in new_lines if ln not in base_lines]
        removed = [ln for ln in base_lines if ln not in new_lines]
        if any(ADVICE.search(ln) for ln in added):
            advice += 1
        if removed and base_lines and (base_lines[0] in removed or base_lines[1:2] and base_lines[1] in removed):
            headless += 1
        if len(r.rtext) < len(r.btext) * 0.98:
            shrank += 1
    n = len(pairs)
    report("inserted playbook advice prose", advice, n, limit_pct=0)
    report("deleted title/parties block", headless, n, limit_pct=0)
    report("net content loss >2%", shrank, n, limit_pct=5)

    meta = db.execute(
        text("select count(*) from playbook_deviation where clause_type = any(:t)"),
        {"t": list(META_CLAUSE_TYPES)},
    ).scalar()
    dev_total = db.execute(text("select count(*) from playbook_deviation")).scalar()
    report("non-actionable meta deviations", meta or 0, dev_total or 0, limit_pct=0)


def check_enrichment(db) -> None:
    """Every live contract should come out of the pipeline fully enriched."""
    print("\nDOWNSTREAM ENRICHMENT  (of live contracts)")
    r = db.execute(
        text(
            """
            select
              (select count(*) from contract where deleted_at is null) total,
              (select count(*) from contract c where c.deleted_at is null
                and not exists (select 1 from contract_document_element d
                                 where d.contract_id = c.id)) no_elements,
              (select count(*) from contract c where c.deleted_at is null
                and not exists (select 1 from clause_extraction ce
                                 where ce.contract_id = c.id)) no_clauses,
              (select count(*) from contract c where c.deleted_at is null
                and c.risk_summary is null) no_risk,
              (select count(*) from contract c where c.deleted_at is null
                and not exists (select 1 from contract_embedding e
                                 where e.contract_version_id
                                       = c.current_authoritative_version_id)) no_emb,
              (select count(*) from contract where deleted_at is null
                and metadata_json::text like '%%"error"%%') extract_err
            """
        )
    ).first()
    report("no parser elements", r.no_elements, r.total, limit_pct=0)
    report("no LLM clauses", r.no_clauses, r.total, limit_pct=5)
    report("no risk summary", r.no_risk, r.total, limit_pct=10)
    report("not vector-searchable", r.no_emb, r.total, limit_pct=0)
    report("extraction provider error", r.extract_err, r.total, limit_pct=2)


def check_jobs(db) -> None:
    print("\nJOB OUTCOMES")
    rows = db.execute(
        text(
            """
            select job_type,
                   count(*) n,
                   count(*) filter (where status = 'failed') failed,
                   max(created_at) filter (where status = 'failed') last_fail
            from job_run group by 1 order by 3 desc
            """
        )
    ).all()
    for r in rows:
        note = f"last failure {r.last_fail:%Y-%m-%d}" if r.failed else ""
        report(r.job_type, r.failed, r.n, limit_pct=2, note=note)


def main() -> int:
    db = SessionLocal()
    try:
        n = db.execute(text("select count(*) from contract where deleted_at is null")).scalar()
        print(f"AEGIS pipeline audit — {n} live contracts")
        check_storage(db)
        check_offsets(db)
        check_playbooks(db)
        check_redlines(db)
        check_enrichment(db)
        check_jobs(db)
    finally:
        db.close()

    print()
    if failures:
        print(f"{len(failures)} check(s) over threshold:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all checks within thresholds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
