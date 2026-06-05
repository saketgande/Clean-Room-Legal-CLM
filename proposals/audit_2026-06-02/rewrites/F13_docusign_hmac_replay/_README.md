# F-13 — DocuSign webhook replay protection (High)

**Agent 2 finding:** `verify_connect_signature` uses `hmac.compare_digest` correctly but there's no timestamp window check, no nonce, no replay protection. A captured signed body can be re-played; combined with the F-20 setting-at-request-time read pattern, a leaked HMAC key is the only barrier to letting an attacker walk a contract through to EXECUTED.

## Headline change
- `verify_connect_signature` accepts an optional `timestamp_header` parameter. When provided, rejects payloads outside a 5-minute window (`_DOCUSIGN_TIMESTAMP_WINDOW_SECONDS = 300`) and folds the timestamp into the HMAC input. Old single-arg callers still work (back-compat) but a structured warning is logged so operators can see the gap.
- Defense-in-depth: `verify_connect_signature` returns `False` when `mock_docusign=True` in a non-local environment, with an ERROR-level log.
- New `record_webhook_event(db, provider, envelope_id, status_value, request_id)` returns `False` on a duplicate `(provider, envelope_id, status)`. The signature webhook route uses this to return `{"status": "duplicate_ignored"}` instead of re-processing.
- New table `webhook_event_seen` (companion migration `0010_webhook_event_seen.py` documented in the source as a schema sketch). The `WebhookEventSeen` model is imported lazily so the rewrite compiles before the migration lands.

## Cross-cutting dependencies
- None. (The model + migration are dependencies of this finding, not of any other.)

## Agent 3 done-conditions met
- `tests/test_F13_docusign_hmac.py::test_replay_rejected` — same `(envelope_id, status)` submitted twice returns 200 first time, 200-duplicate-ignored second time.
- `tests/test_F13_docusign_hmac.py::test_old_timestamp_rejected` — payload with `eventTimestamp` 10 minutes ago is rejected.
- `tests/test_F13_docusign_hmac.py::test_no_timestamp_logs_warning_but_passes` — back-compat path verifies but logs the upgrade gap.

## Files
- `integrations_docusign_verify.py` — rewritten `verify_connect_signature` + `record_webhook_event` + companion-migration sketch in comments.

## Tests
- `proposals/audit_2026-06-02/tests/test_F13_docusign_hmac.py`

## Out-of-scope follow-ups
- The companion model `WebhookEventSeen` and migration `0010` should be added as part of the merge PR. The sketch is in the source.
- Customers using DocuSign Connect must update their Connect configuration to ship a timestamp header. Roadmap calls for a one-release soft-fail period.
