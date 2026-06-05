# F-12 — Audit-log Postgres error swallowed silently (High)

**Agent 2 finding:** `write_audit_log` opens a fresh `SessionLocal()`, calls `pg_advisory_xact_lock`, and catches **any** exception as `durable_db.rollback()` then falls through — including a real Postgres error on Postgres. If an attacker can DoS Postgres briefly during a security-critical action (e.g. `external_share` creation), the audit row may not be written. The hash chain "stays valid" because the missing row was never appended.

## Headline change (behavior change — this finding IS a bug)
- New exception type `AuditChainCorrupted(action, message, retryable)` in this module.
- Backend-aware lock acquisition: SQLite (test path) skips the advisory lock; everything else MUST acquire it or raise `AuditChainCorrupted("lock_acquire", ..., retryable=True)`.
- Any `SQLAlchemyError` during the lock, the prev-hash lookup, or the commit raises `AuditChainCorrupted` with the underlying error preserved as `__cause__` plus structured logging.
- Per Agent 3's operational-risk note, the retryable flag lets the caller (or a future retry decorator) distinguish a transient blip from a fatal chain-integrity violation.
- Behavior preserved: when no error happens, the function returns the same `AuditLog` row as before. SQLite test path is identical.
- `write_timeline_event` is unchanged.

## Migration plan for downstream callers (out of this PR's scope, see roadmap)
Sprint 2 task: audit all ~30 call sites of `write_audit_log`. Default action: let `AuditChainCorrupted` propagate to the request handler so the business write is rolled back too. No call site should bare-`except` it.

## Cross-cutting dependencies
- None. (`AuditChainCorrupted` should eventually move to `app/core/exceptions.py`; for the proposal patch we keep it co-located.)

## Agent 3 done-conditions met
- `tests/test_F12_audit_failure.py::test_audit_failure_raises_chain_corrupted` — simulated commit failure raises `AuditChainCorrupted`, NOT a silent return.
- `tests/test_F12_audit_failure.py::test_sqlite_skips_advisory_lock` — on SQLite, the lock branch is bypassed without raising.
- Logger captures the failure with structured fields (`action`, `resource_type`, `resource_id`, `error_class`).

## Files
- `core_audit_record.py` — rewritten `write_audit_log` + `AuditChainCorrupted` + helpers. Drop-in replacement for `backend/app/core/audit.py`.

## Tests
- `proposals/audit_2026-06-02/tests/test_F12_audit_failure.py`
