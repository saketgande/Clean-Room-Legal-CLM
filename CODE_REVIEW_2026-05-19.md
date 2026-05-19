# Code Review: AEGIS Legal CLM

**Date:** 2026-05-19
**Scope:** Deep-dive review of the full repo (FastAPI backend ~18.5k LOC Python + Next.js frontend ~16.9k LOC TS/TSX)
**Focus areas:** Security, performance, correctness, maintainability, test coverage
**Mode:** Read-only — no code changes made

---

## Summary

The codebase is well-structured for a CLM product: clear module boundaries (`auth`, `contracts`, `contract_files`, `signatures`, `playbooks`, `ai`, …), a coherent RBAC + org-scoping model, proper soft-delete + audit-log infrastructure with hash-chained tamper detection, and consistent use of the SQLAlchemy ORM (no raw SQL string concatenation found, no SQL injection vectors). The system design is sound.

That said, this review surfaced **one critical leak (a real RSA private key committed to git), several high-severity security weaknesses (email enumeration, JWT non-revocability, refresh tokens in localStorage, no rate limiting on login), and a handful of N+1 query hotspots in dashboard + chat paths**. Test coverage is notably thin given the surface area (7 backend test files, ~800 LOC; zero frontend tests).

---

## Critical issues

| # | File | Line | Issue | Severity |
|---|---|---|---|---|
| 1 | `backend/secrets/docusign_private_key.pem` | — | **RSA private key committed to git** in commit `428690ff`. `.gitignore` lists `backend/secrets/` and `**/*.pem`, but the file was already tracked when those rules were added, so it remains in HEAD and in history. Even after removal, the key is forever in git history and must be **rotated**, not just untracked. | 🔴 Critical |
| 2 | `backend/app/auth/service.py` | 277, 227 | **Email enumeration via login response.** `login_user` returns `401 "Invalid email or password"` when credentials are wrong, but `403 f"User status is {user.status}"` when the email is valid but the user is pending/rejected. An attacker can distinguish "this email is registered" from "this email is not." Same pattern in `register_user` (409 "User already exists" vs returning a join request). | 🔴 High |
| 3 | `backend/app/main.py` | — | **No rate limiting on `/auth/login`, `/auth/refresh`, `/auth/password-reset/request`, or `/auth/invitations/accept`.** Combined with email enumeration (#2) and bcrypt's relatively slow verify, this is a real password-spraying / credential-stuffing risk. No `slowapi` or equivalent middleware mounted. | 🔴 High |
| 4 | `backend/app/core/security.py` | 41–61 | **JWT access tokens are not revocable.** `create_access_token` writes a `jti`, but it's never persisted or checked in `decode_access_token`. Logout (`revoke_refresh_token`) and `confirm_password_reset` only revoke refresh tokens — already-issued access tokens remain valid until they expire (default 60 min). A stolen access token cannot be cut off. | 🔴 High |
| 5 | `frontend/src/lib/api.ts` | 10–32 | **Refresh tokens (30-day lifetime) stored in `localStorage`.** Any XSS — including from a vulnerable npm dep or a future `dangerouslySetInnerHTML` slip — exfiltrates a 30-day persistent session. Refresh tokens belong in an HttpOnly, Secure, SameSite=Strict cookie. | 🔴 High |
| 6 | `backend/app/contract_files/service.py` | 43–49 | **MIME type is trusted from the client.** `mime_type = upload.content_type or "application/octet-stream"` is checked against the allow-list, but `content_type` is the value the *client* sent. No magic-byte sniffing. A user with `contract:create` can upload arbitrary content masquerading as `application/pdf`. Downstream consumers (pypdf, python-docx, the OCR pipeline, browser preview) trust this label. | 🟠 Medium-High |
| 7 | `backend/app/contract_files/service.py` | 47–49 | **Size check happens after full read.** `content = await upload.read()` buffers the whole upload into memory **before** the size check. A 5 GB stream is fully read into RAM and only then rejected. Stream-and-cap, or rely on a request-size limit at the ASGI layer. | 🟠 Medium-High |
| 8 | `backend/app/core/audit.py` | 37–67 | **Audit hash chain has a concurrency race.** `previous_hash` is read in an autonomous session, then a new row is inserted with that prev_hash. Two concurrent audit writes can both read the same `previous_hash` and both insert children of the same parent, breaking the chain's tamper-evident guarantee. There is no `SELECT … FOR UPDATE`, advisory lock, or single-writer serialization. | 🟠 Medium-High |
| 9 | `backend/app/signatures/routes.py` | 121–129 | **HTML-injection / phishing vector in outbound signature emails.** `contract.title` is interpolated into the HTML email body unescaped: `f"<p>You have been requested to sign <b>{contract.title}</b>.</p>"`. A user with `contract:create` can craft a title containing arbitrary HTML — links, fake "click here to verify" buttons — that's then sent from your domain to external signers. | 🟠 Medium |

---

## Suggestions (lower severity, prioritized)

### Security

| # | File | Line | Suggestion | Category |
|---|---|---|---|---|
| 10 | `backend/app/auth/service.py` | 630–634 | `request_password_reset` returns the raw `reset_token` in the API response when `MOCK_RESEND` is on or `environment` is local/dev/test. Gating relies on env-string match; a typo (`prod` vs `production`) silently exposes reset tokens. Prefer an explicit `EXPOSE_RESET_TOKENS_IN_RESPONSE` flag, default false. | Security |
| 11 | `backend/app/core/security.py` | 16–22 | Silent bcrypt 72-byte truncation in `_bcrypt_secret`. Two passwords sharing the first 72 bytes hash identically. Pre-hash with SHA-256 (Django/Passlib style) before bcrypt so the entropy of the entire password is preserved. | Security |
| 12 | `backend/app/auth/service.py` | 515–528 | `accept_user_invitation` silently overwrites a `PENDING_APPROVAL` user's password if an admin issues an invitation to the same email. The flow may be intentional, but the audit log doesn't record the pre-existing user's prior state. Either reject (require an explicit re-invite path) or include the prior state in the audit `before` payload. | Security / audit |
| 13 | `pyproject.toml` | 21 | `python-jose[cryptography]` is no longer well-maintained and has historical algorithm-confusion advisories. Industry has largely moved to `PyJWT`. Track upgrade. | Security / deps |
| 14 | `backend/app/core/middleware.py` | 68 | `request_metadata={"query": str(request.url.query)}` writes the full querystring to `request_log`. Query strings sometimes carry tokens, share passcodes, search PII. Either redact known sensitive keys (`passcode`, `token`, `refresh_token`) or strip the value. | Privacy |
| 15 | `frontend/src/components/markdown.tsx` | 53–54 | Links open `target="_blank" rel="noreferrer"`. Add `noopener` (`rel="noopener noreferrer"`) — older browsers/embedded webviews leak `window.opener` without it. | Security (minor) |
| 16 | `backend/app/main.py` | 47–48 | `allow_methods=["*"], allow_headers=["*"]` is broader than needed. Pin to the methods/headers actually used. | Security (minor) |
| 17 | `backend/app/contract_files/text_extraction.py` | 30–47 | `pypdf.PdfReader` runs on user-supplied bytes with no size cap on extracted text. A "PDF bomb" (massive pages or compressed pages with huge expansion) can OOM the process. Cap per-page extracted length and total. | DoS |

### Performance

| # | File | Line | Suggestion | Category |
|---|---|---|---|---|
| 18 | `backend/app/contract_brain/retrieval.py` | 133–149 | **Classic N+1 in `_graph_facts`.** For each edge (`MAX_GRAPH_FACTS = 40`), the code does `db.get(KnowledgeNode, edge.from_node_id)` and `db.get(KnowledgeNode, edge.to_node_id)` — 80 extra round-trips per chat question on the hottest path. Fetch all referenced nodes in one `IN` query and build a `{id: node}` map. | Performance |
| 19 | `backend/app/signatures/routes.py` | 47–55 | `list_signature_requests` fetches every row, then `db.get(Contract, row.contract_id)` per row for the permission check. N+1 — should use a joined query with the `accessible_contract_filter`. | Performance |
| 20 | `backend/app/contracts/service.py` | 102–246 | `contract_hub_summary` issues ~9 distinct queries per dashboard request, several of which scan the same `accessible_ids` set. Acceptable today, but consider a single SQL with CTEs or a denormalized rollup once contract counts grow past 5–10k. | Performance |
| 21 | `backend/app/core/middleware.py` | 56–75 | The request logger opens a **fresh `SessionLocal()` and commits per request** to write a `RequestLog` row. Extra DB round-trip on every API call. Consider async write to a queue or batch-insert via a background flusher. | Performance |
| 22 | `backend/app/integrations/claude.py` | 156, 75 | Both Claude and DocuSign clients construct a new `httpx.AsyncClient` per call. Connection pooling and HTTP/2 reuse won't happen. Use a long-lived shared client per process. | Performance |
| 23 | `backend/app/integrations/docusign.py` | 62 | `_jwt_access_token` reads the PEM from disk on every call. Cache the parsed key (and the resulting bearer until its `exp - skew`). | Performance |
| 24 | `backend/app/search/routes.py` | 58, 101, 148, 70, 72, 190 | Multiple `column.ilike(f"%{q}%")` — these become full table scans on Postgres without trigram or FTS indexes. Add a `pg_trgm` GIN index on heavy columns (`contract.title`, `contract.counterparty_name`, `contract_text_snapshot.text`) or move to Postgres FTS / `tsvector`. | Performance |
| 25 | `backend/app/core/database.py` | 69–76 | Engine uses default pool (`pool_size=5, max_overflow=10`). For a multi-worker FastAPI prod deployment this is almost certainly too small. Set explicitly from env. | Performance |
| 26 | `backend/app/integrations/claude.py` | 156–167 | No retries on Anthropic 429 / 5xx. A momentary rate-limit fails the whole AI job. Wrap in a `tenacity`/exponential-backoff layer. | Performance / reliability |

### Correctness

| # | File | Line | Suggestion | Category |
|---|---|---|---|---|
| 27 | `backend/app/core/deps.py` | 17–22 | `get_db()` yields then `db.close()` in `finally`, but **no `db.rollback()` on exception**. If a handler raises mid-transaction, SQLAlchemy will roll back implicitly on close — but mixing this with the audit-log autonomous-session pattern means a request-level exception can leave half-applied mutations visible to retries. Add explicit `except: db.rollback(); raise`. | Correctness |
| 28 | `backend/app/auth/service.py` | 263–290 | `login_user` calls `write_audit_log` (autonomous commit) **before** `db.commit()` of the refresh-token insert. If the second commit fails, the audit row says "auth.login_succeeded" but no refresh token exists. Mirror the rollback or write the audit row in the same session. | Correctness |
| 29 | `backend/app/signatures/routes.py` | 84–103 | DocuSign envelope is created via API **before** the local `SignatureRequest` is committed. If the DB commit fails, the envelope is orphaned at DocuSign. Either commit a `pending` row first and update with `envelope_id`, or add a reconciliation job. | Correctness |
| 30 | `backend/app/core/audit.py` | 92–101 | `verify_audit_hash_chain` loads the entire `audit_log` table into memory. Will OOM at scale. Stream with `yield_per` and short-circuit on first mismatch. | Correctness |
| 31 | `backend/app/contract_brain/retrieval.py` | 77, 232 | `except Exception: return []` swallows all errors silently (vector store failures, embedding model failures). The user sees a hedged answer with no signal that retrieval is broken. Log at warning, surface a structured event. | Correctness / observability |
| 32 | `backend/app/contract_files/service.py` | 222–233 | After `db.commit()` the loop calls `dispatch_job` and accumulates `dispatch_errors`, but those errors only land in a timeline event — they don't 4xx/5xx the upload. The caller gets a 201 with no signal that an AI job failed to enqueue. Consider returning the errors in the response payload. | Correctness |
| 33 | `backend/app/contract_files/routes.py` | 605–621 | `_get_active_share` uses `secrets.compare_digest(_hash_secret(passcode or ""), share.passcode_hash)`. Good — but when `share.passcode_hash` is `None` (no passcode required) and the client passes any passcode anyway, it short-circuits. The current branch (`if share.passcode_hash and not …`) handles it, but the dual-meaning of "no passcode required" should have its own audit event when a passcode is supplied unnecessarily. | Correctness (minor) |

### Maintainability & tests

| # | File | Line | Suggestion | Category |
|---|---|---|---|---|
| 34 | `backend/tests/` | — | **Only 7 test files (~800 LOC) for a 35k LOC system.** Phase tests exist (auth foundation, lifecycle, playbooks, AI wiring) but the surface area is much larger. Most concerning: no test exercises the email-enumeration response shape, no test covers permission boundaries between orgs, no DocuSign-webhook negative path test, no upload-size or MIME-spoof test. | Tests |
| 35 | `frontend/` | — | **Zero frontend tests** — no `*.test.*`, `*.spec.*`, no Jest/Vitest/Playwright config. For a UI that handles contract uploads, signatures, and an AI assistant, this is a gap. | Tests |
| 36 | `backend/app/contracts/service.py` | 51–99 | `update_contract_metadata` does `setattr(contract, key, value) if value is not None`. Cannot clear a field once set — passing `null` is silently dropped. Document this or switch to Pydantic's `model_dump(exclude_unset=True)` semantics. | Maintainability |
| 37 | `backend/app/integrations/claude.py` | 195–365 | The mock-Claude branch is ~170 lines of hand-rolled keyword matching that ships in the same client. Move to `app/integrations/_claude_mock.py` so the production code surface is clean, and so the mock tool-selection logic can be unit-tested independently. | Maintainability |
| 38 | `backend/app/contract_files/service.py` | 33–259 | `create_contract_from_upload` is a 220-line function doing intake, storage, DB writes, text extraction, OCR, job queuing, audit, and dispatch. Split into composable steps (`save_upload`, `extract_text_or_ocr`, `persist_intake_records`, `queue_initial_ai`). The current shape makes the rollback-on-exception logic hard to follow. | Maintainability |
| 39 | `backend/secrets/docusign_private_key.pem` | — | Even after rotation: don't ship secrets via the repo. Use a runtime mount (`DOCUSIGN_PRIVATE_KEY` env or `_FILE` pointing at a Docker/K8s secret). | Maintainability / ops |

---

## What looks good

- **Solid RBAC + tenancy.** Every service-layer function I read enforces `org_id == user.org_id` before acting; `accessible_contract_filter(user)` is consistently applied to list/search queries.
- **No SQL injection vectors found.** All ORM queries use bound parameters; no f-string-built SQL in the entire backend.
- **Path traversal is properly defended.** `StorageService._resolve_storage_key` resolves and verifies `is_relative_to(self.root)` before any read/delete.
- **Audit hash chain design is sophisticated** — `prev_hash → row_hash` with SHA-256 over a canonical JSON payload. Only the concurrency race (issue #8) holds it back from being production-grade.
- **DocuSign Connect webhook is HMAC-verified** with `hmac.compare_digest`, and self-rejects when no key is configured. Idempotent via `signature.status` short-circuit. This is the right pattern.
- **External share endpoints use hashed tokens, hashed passcodes, and constant-time comparison.** Expiry + revocation paths are present.
- **`validate_runtime_settings`** explicitly refuses to start the app in non-local environments with default `SECRET_KEY`/`SETUP_TOKEN` or any mock integration enabled. Strong production guardrail.
- **Pydantic everywhere** at the API boundary; FastAPI is doing input validation work that would otherwise be hand-written.
- **`expire_on_commit=False` decision is explicitly documented** with a clear comment about request-context logging. Good engineering culture signal.
- **JobRun + Celery dispatch with idempotency keys** prevents duplicate AI work when versions are re-accepted.
- **Markdown rendering uses ReactMarkdown without `dangerouslySetInnerHTML`** — raw HTML is not rendered by default, so prompt-injection-induced `<script>` in AI output is inert.

---

## Verdict

**Request Changes** — primarily on the critical/high items: rotate and remove the committed RSA private key, close the email-enumeration response gap, add login/auth rate limiting, persist `jti` for JWT revocation, move refresh tokens out of `localStorage`, and add server-side MIME sniffing + streaming size enforcement on uploads. The performance N+1s in `_graph_facts` and the dashboard are worth a focused PR. Test coverage growth should be treated as a parallel workstream — particularly cross-tenant permission tests and an end-to-end signature webhook test.

Architecturally the codebase is in good shape — these are issues you fix on a healthy foundation, not symptoms of structural problems.
