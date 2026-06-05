# F-16 — Boot validator misses integration-key set when mock is off (High)

**Agent 2 finding:** `validate_runtime_settings` at `core/config.py:142-182` checks default `SECRET_KEY`/`SETUP_TOKEN`, mocks-still-on, wildcard hosts — but does NOT verify that `CLAUDE_API_KEY`/`DOCUSIGN_*`/`RESEND_API_KEY`/`REDUCTO_API_KEY` are set when the matching `MOCK_X` flag is off. Boot succeeds; the first Claude/DocuSign request returns 401 in prod.

## Headline change
- Real-key requirements: when `MOCK_X=false` AND the corresponding real key/path is unset, the validator now raises with a clear message naming every missing env var.
- DocuSign extras: when `MOCK_DOCUSIGN=false`, the validator verifies that `DOCUSIGN_PRIVATE_KEY_PATH` points to an existing file and is readable as UTF-8.
- Likely-misconfiguration warnings (non-fatal): when `MOCK_X=true` AND the real key IS set, log a structured warning with `config.likely_misconfiguration` event.
- New setting `approval_token_rate_limit` (default `"30/minute;200/hour"`) added for F-02. Setting is declared here so the boot validator can sanity-check it too if needed.
- Behavior preserved for the existing production checks (`SECRET_KEY`, `SETUP_TOKEN`, mock flags, refresh cookie, allowed hosts, CORS).

## Cross-cutting dependencies
- None.

## Agent 3 done-conditions met
- `tests/test_F16_boot_validate.py::test_boot_rejects_missing_claude_key_when_real` — `environment=production, mock_claude=false, claude_api_key=None` raises `RuntimeError` with `CLAUDE_API_KEY` in the message.
- `tests/test_F16_boot_validate.py::test_boot_rejects_missing_docusign_pem_when_real` — same shape for DocuSign.
- `tests/test_F16_boot_validate.py::test_local_warnings_dont_block_boot` — local env with mock-on AND real key set logs a warning but does NOT raise.

## Files
- `core_config_validate.py` — rewritten `validate_runtime_settings` + the `approval_token_rate_limit` setting.

## Tests
- `proposals/audit_2026-06-02/tests/test_F16_boot_validate.py`
