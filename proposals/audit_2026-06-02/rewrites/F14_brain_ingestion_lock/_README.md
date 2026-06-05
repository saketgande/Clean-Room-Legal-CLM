# F-14 — `contract_brain_ingestion` 3x race (High)

**Agent 2 finding:** Clause/obligation/renewal jobs each enqueue a `contract_brain_ingestion` JobRun keyed by `...:{reason}`. Three reasons → three concurrent ingests per contract upload. `ingest_contract_brain` marks prior nodes/edges stale then inserts new ones without a lock. Two concurrent ingests corrupt the graph.

## Headline change
- New `build_auto_brain_ingestion_key(version_id, snapshot_id)` from CC-5 — single canonical idempotency key per `(version_id, snapshot_id)` regardless of triggering reason. `JobRun.idempotency_key UNIQUE` collapses the three siblings to one row.
- New `acquire_brain_ingest_lock(db, contract_id)` takes `pg_advisory_xact_lock(hash("brain_ingest:" + contract_id))`. SQLite (test) skips the lock harmlessly.
- New wrapper `ingest_contract_brain_locked(...)` acquires the lock then delegates to the existing ingestion function. Callers in `jobs/tasks.py` are intended to switch to this wrapper.
- Trigger reason still recorded in `metadata_json.trigger_reason` so the ingestion log is still attributable to whichever upstream extraction completed first.

## Cross-cutting dependencies
- **CC-5** `app/jobs/idempotency.py` — `build_auto_brain_ingestion_key`.

## Agent 3 done-conditions met
- `tests/test_F14_brain_ingestion.py::test_three_extractions_yield_one_ingestion_job` — running clause+obligation+renewal extractions in rapid succession yields exactly one `contract_brain_ingestion` JobRun.
- `tests/test_F14_brain_ingestion.py::test_concurrent_ingest_serializes` (Postgres-only) — two simultaneous ingests for the same contract serialize via the advisory lock.

## Files
- `jobs_tasks_brain_ingestion.py` — rewritten `_queue_contract_brain_ingestion` + new lock helpers + wrapped ingestor.

## Tests
- `proposals/audit_2026-06-02/tests/test_F14_brain_ingestion.py`
