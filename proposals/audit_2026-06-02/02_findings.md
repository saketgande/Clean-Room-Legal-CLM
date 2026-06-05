# AEGIS Legal CLM — Audit Findings (Agent 2: The Skeptic)

**Date:** 2026-06-02
**Scope:** Ranked defects, risks, and tech debt sites. Backend-heavy. Recently-modified AI surface read in full; rest grep-driven.
**Method:** Read Agent 1's map → read 7 modified files → grep sweeps (`except Exception`, `org_id`, `hmac`, `db.get`, `accessible_contract_filter`) → targeted Reads on hits.

---

## Corrections to Agent 1

None material. One small note: Agent 1 stated `accessible_contract_filter(user)` is appended into queries that "must remember to `Contract` join into the query"; in `obligations/routes.py:50` and `renewals/routes.py` the routes do join `Contract`, but they do NOT also constrain `Contract.org_id == user.org_id` (only the domain row's `org_id`). For non-admin users `accessible_contract_filter` constrains via project membership, but for org admins it returns `true()` — see F-03.

---

## Severity counts

- Critical: 4
- High: 12
- Medium: 14
- Low: 7

---

## Critical

### F-01. SSE event_stream uses the request-scoped `db` Session after the FastAPI dependency closed it
- **Severity:** Critical
- **Category:** Correctness / Concurrency / Data Integrity
- **Location:** `backend/app/assistant/routes.py:250-379` (`stream_session`) and `:382-458` (`resume_run`); same pattern in `backend/app/ai/controller.py:93-325` (`stream_assistant_run`) and `:327-592` (`resume_assistant_run`).
- **What it is:** The route function returns `StreamingResponse(event_stream(), ...)`. By the time the generator runs, the FastAPI `get_db()` dependency that yielded `db` has already returned (and will close the session). Inside `event_stream`, the code commits/refreshes/queries on that same closed session repeatedly — for every tool call, every Claude call log, the final `AssistantMessage`/`AssistantRun` persistence, and the exception path that flips run status to FAILED.
- **Why it matters at scale or in prod:** Today this works only because `get_db()` (`backend/app/core/deps.py:17-32`) yields a session and the `finally db.close()` runs after the generator is consumed — but the generator does NOT keep the dependency context manager open across the streaming lifetime. Any change to make `get_db` an actual `Depends` resource (e.g. adding async context) will break it. More immediately: every commit inside the generator runs on a session whose lifecycle is implicit; if the client disconnects mid-stream the dependency teardown races the generator's writes. Tool calls can write a half-formed `AssistantToolCall` and never update its status. The "`assistant_run.status = FAILED` on exception" path then writes through the same already-closing session.
- **Evidence:**
```python
# routes.py:255 — db comes from Depends(get_db)
db: Session = Depends(get_db),
...
# routes.py:292 — generator function defined inside the request
async def event_stream() -> AsyncIterator[str]:
    ...
    try:
        async for event in ai_controller.stream_assistant_run(
            db, ...   # <-- same session passed all the way down
        ):
            ...
            db.commit()                          # routes.py:367
        ...
    except Exception as exc:
        assistant_run.status = AssistantRunStatus.FAILED
        assistant_run.error_message = str(exc)
        db.commit()                              # routes.py:375
```

### F-02. Token-decision endpoint is unauthenticated AND not rate-limited — single-use token is the only defense against brute force / enumeration
- **Severity:** Critical
- **Category:** Security
- **Location:** `backend/app/approvals/routes.py:141-162` (`decide_via_token`) → `backend/app/approvals/service.py:256-292` (`redeem_token_decision`).
- **What it is:** `POST /api/v1/approvals/token-decision` accepts `{token, decision, comment}` with no session auth, no `Depends(require_permission(...))`, and no `@limiter.limit(...)`. The token is 48 bytes of `secrets.token_urlsafe` (good entropy), but there is no per-IP rate limit and the error responses distinguish "not found" (404) from "already used" (409) from "expired" (409). An attacker who can reach the endpoint can probe the URL space.
- **Why it matters at scale or in prod:** The token is the only credential. The 404-vs-409 oracle leaks live-token existence cheaply. Without slowapi protection the endpoint will be abused by automation. A successful guess approves a contract.
- **Evidence:**
```python
# routes.py:141
@router.post("/token-decision")
def decide_via_token(
    payload: TokenDecisionPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    """Token-authenticated approval decision. No session auth: the single-use,
    expiring, email-bound token is the credential."""
    approval = redeem_token_decision(...)
```
```python
# service.py:264-272 — distinct error codes leak token state
if row is None:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval token not found")
if row.used_at is not None:
    raise HTTPException(status.HTTP_409_CONFLICT, "Approval token already used")
if row.expires_at < datetime.now(UTC):
    raise HTTPException(status.HTTP_409_CONFLICT, "Approval token expired")
```
No `@limiter.limit(...)` decorator anywhere on this route. No `compare_digest` either — `select(...).where(token_hash == hash_token(token))` is fine for SHA-256 hash lookups, but the existence oracle remains.

### F-02b. Approval token-decision: hash lookup spans all orgs, no per-org/per-IP rate, no replay window protection
- **Severity:** Critical
- **Category:** Security / Multi-tenancy
- **Location:** `backend/app/approvals/service.py:264-292`.
- **What it is:** `redeem_token_decision` looks up `ApprovalToken` by token_hash with NO `org_id` filter. The hash is SHA-256 (collision-resistant), so this is correct cryptographically, but combined with the lack of a "looked-up-via-token" audit signal and no rate limit (F-02), it means token traffic isn't visibly attributed to any tenant. Also, `approver = User where email == intended_approver_email` — there can be exactly one User with that email globally, fine — but the decision is logged with `actor_user_id = approver.id if approver else None`. If the original approver was deactivated, the audit attributes the action to a `None` actor without distinguishing a token from a system action.
- **Why it matters at scale or in prod:** A single token reuse oracle, combined with no rate limit, lets an attacker enumerate token state across all tenants. A compromised or deactivated approver leaves the audit log silent about who actually clicked the button.
- **Evidence:**
```python
# service.py:264
row = db.scalar(
    select(ApprovalToken).where(ApprovalToken.token_hash == hash_token(token))
)
# No .where(ApprovalToken.org_id == ...) — token hash is the lookup key globally.
...
approver = db.scalar(
    select(User).where(User.org_id == row.org_id, User.email == row.intended_approver_email)
)
return _apply_decision(
    ...
    actor_user_id=approver.id if approver else None,  # silently None if user deactivated
    actor_label=f"token:{row.intended_approver_email}",
    ...
)
```

### F-03. `accessible_contract_filter` returns `true()` for org admins — meaning a query that joins Contract WITHOUT also constraining `Contract.org_id` will leak cross-org rows
- **Severity:** Critical
- **Category:** Security / Multi-tenancy
- **Location:** `backend/app/contracts/access.py:11-13` returns `true()` if `is_org_admin(user)`. Affected callers that join `Contract` but DO NOT constrain `Contract.org_id == user.org_id`:
  - `backend/app/obligations/routes.py:50-58` (`list_obligations`).
  - `backend/app/renewals/routes.py` `list_renewals` (per Agent 1, joins RenewalEvent without explicit Contract org_id).
- **What it is:** When the caller is an org admin, the helper short-circuits to "no extra predicate". The query then relies on the OTHER table's `org_id` (e.g. `Obligation.org_id == current_user.org_id`) — but an `Obligation.contract_id` could in principle reference a `Contract` row in a different org if a data-integrity bug ever produced such a row (there's no FK on `org_id`, and `Obligation.contract_id` does not constrain the joined contract's org). The join surfaces the cross-org contract.
- **Why it matters at scale or in prod:** Multi-tenant CLM. The premise of the app is that org A never sees org B's contracts. Any inconsistency in `org_id` allocation (which, given there's no FK on `org_id` across tables, is one bug away) ships cross-tenant rows in admin views.
- **Evidence:**
```python
# contracts/access.py:11-13
def accessible_contract_filter(user: User):
    if is_org_admin(user):
        return true()
```
```python
# obligations/routes.py:50-58
query = (
    select(Obligation)
    .join(Contract, Contract.id == Obligation.contract_id)
    .where(
    Obligation.org_id == current_user.org_id,
    Obligation.deleted_at.is_(None),
        accessible_contract_filter(current_user),  # true() for admin
    )
)
# No Contract.org_id == current_user.org_id constraint.
```

### F-04. AI tool `feature_flag` is never enforced server-side — disabled tools are still executable if Claude calls them
- **Severity:** Critical
- **Category:** Security / Authz
- **Location:** `backend/app/ai/tool_runtime.py:86-157` (`ToolRuntime.execute`). Tool registry declares `feature_flag` (e.g. `feature.ai.edit_suggestions`, `feature.ai.docx_generation`) at `backend/app/ai/tool_registry.py:127`, `:211`, `:229`. The controller's `_assistant_tool_schemas` (`controller.py:594-608`) filters by `enabled_by_default` and permission only — not by feature flag. `ToolRuntime.execute` checks permission and confirmation policy but never queries `AdminSetting` for the tool's `feature_flag`. The skill-side guard `_ensure_skill_enabled` (`controller.py:1098-1106`) only fires for `run_structured_skill`, not for tool invocations.
- **What it is:** If an admin turns off `feature.ai.edit_suggestions`, Claude will still pick `edit_contract` from the schema (because `enabled_by_default=True` and the schema generation doesn't consult `AdminSetting`), and `tool_runtime.execute` will run it.
- **Why it matters at scale or in prod:** Customers will toggle feature flags to disable AI editing for legal-review or compliance reasons. The toggle silently does nothing. This is a contract violation.
- **Evidence:**
```python
# controller.py:594
def _assistant_tool_schemas(self, *, user: User) -> list[dict[str, Any]]:
    tools = []
    for tool in tool_registry.all():
        if not tool.enabled_by_default:
            continue
        if not has_permission(user.permission_values, tool.required_permission):
            continue
        # No feature_flag check.
        tools.append({...})
```
```python
# tool_runtime.py:97-119 — no AdminSetting lookup, no spec.feature_flag check
spec = tool_registry.get(tool_name)
if not has_permission(user.permission_values, spec.required_permission):
    raise HTTPException(status.HTTP_403_FORBIDDEN, ...)
validated_input = spec.input_model.model_validate(tool_input)
# straight into create AssistantToolCall + execute
```

---

## High

### F-05. `_anchor_suggestions` accepts an empty `original_text` and inserts at offset 0 — silent prepend on every "no original_text" edit
- **Severity:** High
- **Category:** Correctness / Data Integrity
- **Location:** `backend/app/ai/tool_runtime.py:1589-1622`.
- **What it is:** When the model returns an edit with `original_text == ""` (e.g. "insert a confidentiality clause"), the code marks `rec["start"] = rec["end"] = 0; rec["matched"] = True`. The first such edit wins (`applied=True`) because `cursor=0 ≤ start=0`. Multiple "no-original" edits all anchor at 0 but only the first applies — the others fail the `start >= cursor` guard silently. There's no audit trail that the model proposed N inserts and only 1 was applied.
- **Why it matters at scale or in prod:** A legal user asking "add an NDA clause and a confidentiality clause" gets only the first applied. The DOCX file says "1 tracked change" — they sign it not knowing the second clause was silently dropped. There is no `applied=False` warning surfaced to the result.
- **Evidence:**
```python
# tool_runtime.py:1614-1631
if original == "":
    rec["start"] = rec["end"] = 0
    rec["matched"] = True
else:
    span = _find_span(source_text, original)
    if span is not None:
        rec["start"], rec["end"] = span
        rec["matched"] = True
anchored.append(rec)

cursor = 0
for rec in sorted([a for a in anchored if a["matched"]], key=lambda a: (a["start"], a["end"])):
    if rec["start"] >= cursor:
        rec["applied"] = True
        cursor = max(cursor, rec["end"])
```

### F-06. AssistantSession ownership is hard-bound to `created_by_user_id` — sessions cannot be reassigned, and any audit/admin scrutiny needs raw DB access
- **Severity:** High
- **Category:** Security / Data Integrity / UX
- **Location:** `backend/app/assistant/routes.py:512-520` (`_get_session_for_user`).
- **What it is:** The session-fetch helper enforces `session.created_by_user_id != current_user.id` as a 404. There is NO admin override. An org admin investigating a runaway tool loop, a billing audit, or a sensitive AI conversation cannot read any session they didn't create.
- **Why it matters at scale or in prod:** Compliance teams need to investigate AI activity. Customer support needs to debug user reports. The current shape forces those into raw SQL, which defeats audit logging.
- **Evidence:**
```python
# routes.py:512-520
def _get_session_for_user(db, *, session_id, current_user):
    session = db.get(AssistantSession, session_id)
    if (
        session is None
        or session.org_id != current_user.org_id
        or session.created_by_user_id != current_user.id   # no admin bypass
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assistant session not found")
    return session
```

### F-07. The tool-use loop has no per-run cost budget and no cancellation point — a runaway 8-iteration loop spends real Claude $ even after the client disconnects
- **Severity:** High
- **Category:** Performance / Cost / Scaling
- **Location:** `backend/app/ai/controller.py:168-319` (`stream_assistant_run`) and `:445-585` (`resume_assistant_run`).
- **What it is:** The loop runs up to `settings.ai_max_tool_iterations = 8` Claude calls regardless of upstream client state. There is no `request.is_disconnected()` check, no token-budget check, no max-tokens-per-run accumulator, no early-abort on a repeated tool. Each iteration is one Claude call costing real money.
- **Why it matters at scale or in prod:** A page refresh during streaming continues to pay 8 Claude calls. Anthropic outage that returns 200 with empty `tool_use_blocks` quickly enough that retries don't fire causes an infinite-loop-shaped (8 iter) burn. Iteration 9 raises `RuntimeError("Assistant tool loop exceeded maximum iterations")` but iterations 1-8 still cost real $.
- **Evidence:**
```python
# controller.py:169
for iteration in range(settings.ai_max_tool_iterations):
    provider_response = await claude_client.complete_with_tools(...)
    # No disconnect check. No cost accumulator. No cancellation.
    ...
raise RuntimeError("Assistant tool loop exceeded maximum iterations")
```

### F-08. SSE generator swallows tool exceptions, surfaces only `str(exc)` to the model, never logs the stack — silent failure on every tool error
- **Severity:** High
- **Category:** Observability / Error Boundaries
- **Location:** `backend/app/ai/controller.py:289-316` and `:557-583`.
- **What it is:** Each tool exception inside the loop is caught with `except Exception as exc:` and turned into `{"error": str(exc), "tool_name": tool_name}` re-fed to Claude. The exception is not logged. No traceback is captured. The model gets a stringified error and decides what to do next.
- **Why it matters at scale or in prod:** Bug hunting: an error like `KeyError: 'contract_id'` in a tool method becomes `KeyError: 'contract_id'` in `tool_finished` event payload sent to the model, then disappears. The model may retry, may answer apologetically, may loop. No alerting, no Sentry capture, no audit entry.
- **Evidence:**
```python
# controller.py:289-316
except Exception as exc:
    db.commit()
    error_result = {"error": str(exc), "tool_name": tool_name}
    tool_results.append(error_result)
    yield {
        "event": "tool_finished",
        "payload": {"tool_name": tool_name, "tool_use_id": tool_use.get("id"), "error": str(exc)},
    }
    # No logging.exception(), no Sentry, no audit, no timeline.
```

### F-09. Contract-handle generation has a TOCTOU race — two concurrent threads/workers serving the same session both compute `count = len(...)` and both insert `contract-N`
- **Severity:** High
- **Category:** Concurrency / Data Integrity
- **Location:** `backend/app/ai/controller.py:736-774` (`_handle_for_contract`); duplicated at `backend/app/ai/tool_runtime.py:399-431` (`_find_contracts`); third copy at `backend/app/assistant/routes.py:567-587` (`_ensure_contract_handle`).
- **What it is:** Each call does (read existing handle) → (count existing handles) → (insert new). Under concurrency with two workers serving the same session, both read the same count and insert the same handle. No unique constraint on `(session_id, handle)` is checked atomically.
- **Why it matters at scale or in prod:** With horizontal scaling, a single user with two browser tabs streaming on the same session produces duplicate handles. The model sees `contract-0` mapping to two contracts. Tool calls become non-deterministic. Per Agent 1, three writers exist for the same logic — pattern divergence makes this worse.
- **Evidence:**
```python
# controller.py:754-774
count = len(
    db.scalars(
        select(AssistantContractHandle).where(
            AssistantContractHandle.org_id == org_id,
            AssistantContractHandle.session_id == session_id,
        )
    ).all()
)
handle_value = f"contract-{count}"
db.add(AssistantContractHandle(... handle=handle_value ...))
```
Same logic copy-pasted in `tool_runtime.py:415-425` and `routes.py:568-576`.

### F-10. Idempotency key for `obligation_extraction` manual extract includes `utcnow().timestamp()` — defeating the purpose of `job_run.idempotency_key UNIQUE`
- **Severity:** High
- **Category:** Correctness / Cost
- **Location:** `backend/app/obligations/routes.py:161` and `backend/app/ai/tool_runtime.py:1242`.
- **What it is:** The key is `f"obligation_extraction:{version.id}:{snapshot.id}:manual:{utcnow().timestamp()}"`. Every click produces a different key — by design (per the comment, "so manual reruns aren't deduped against the upload-time job"). But this also means rapid double-clicks both succeed and both spawn full Claude calls. The unique constraint on `JobRun.idempotency_key` only prevents accidental upload-time dedup — not user-facing dedup.
- **Why it matters at scale or in prod:** Click-twice-and-pay-twice on every "re-extract obligations" button. With Claude at production token rates, this is real money per double-click.
- **Evidence:**
```python
# obligations/routes.py:161
idempotency_key=f"obligation_extraction:{version.id}:{snapshot.id}:manual:{utcnow().timestamp()}",
```
```python
# tool_runtime.py:1242
idempotency_key=f"obligation_extraction:{version.id}:{snapshot.id}:assistant:{utcnow().timestamp()}",
```

### F-11. `_persist_obligations` soft-deletes ALL prior AI-extracted open obligations on every Claude run, even on a flaky retry — duplicate work + reminder churn
- **Severity:** High
- **Category:** Data Integrity / Correctness
- **Location:** `backend/app/ai/controller.py:1446-1542` (`_persist_obligations`).
- **What it is:** On every successful obligation_extraction run, the code soft-deletes every AI-extracted open obligation for the contract, then re-creates them and their reminders. There is no "did anything change" check. If Claude returns a near-identical list, every row is replaced. `ObligationReminder.remind_at` is recomputed; an admin who hand-edited the reminder date loses it.
- **Why it matters at scale or in prod:** Obligation reminders that humans tuned get clobbered by AI re-extraction. The audit log gets a "obligations_extracted: N rows" event but says nothing about deletes. The reminder cron sends "Reminder: X due 2026-07-01" for the new obligation while the user remembers being assigned the old one with a different ID — confusion at minimum, missed obligations at worst.
- **Evidence:**
```python
# controller.py:1464-1479
existing = db.scalars(select(Obligation).where(...)).all()
for ob in existing:
    if (ob.metadata_json or {}).get("source") == "ai_extraction" and ob.status not in {"completed", "cancelled"}:
        ob.deleted_at = utcnow()
        ob.deleted_by_user_id = created_by_user_id
        ob.updated_by_user_id = created_by_user_id
# Then unconditional re-insert; no diff, no merge.
```

### F-12. Audit-log autonomous session swallows Postgres errors as a generic `rollback()`, hiding chain-corruption events
- **Severity:** High
- **Category:** Security / Observability / Compliance
- **Location:** `backend/app/core/audit.py:54-60` and `:85-87`.
- **What it is:** `write_audit_log` opens a fresh `SessionLocal()`, tries `pg_advisory_xact_lock`, and on ANY exception does `durable_db.rollback()` and falls through — including a `pg_advisory_xact_lock` failure on Postgres (not just SQLite). The outer try/except at `:85-87` does `rollback(); raise` but the operation in between is not bracketed by any tamper-evident signal. A Postgres connection error mid-write that drops the row leaves the chain technically intact but with a gap silently.
- **Why it matters at scale or in prod:** This is the tamper-evidence chain. If an attacker can DoS Postgres briefly during a security-critical action (e.g. external_share creation), the audit row may not be written. The hash chain "stays valid" because the missing row was never appended — but the action ran.
- **Evidence:**
```python
# audit.py:54-60
try:
    durable_db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _AUDIT_CHAIN_LOCK_KEY})
except Exception:
    # Backends without pg_advisory_xact_lock (e.g. SQLite in tests)
    # fall through; concurrency guarantees come from the DB engine
    # only when running on Postgres.
    durable_db.rollback()
# But a real Postgres error here also falls through.
```

### F-13. Webhook signature verification: `verify_connect_signature` returns False on missing key — but `mock_docusign=True` lets the entire webhook bypass the check via env toggle
- **Severity:** High
- **Category:** Security
- **Location:** `backend/app/integrations/docusign.py:189-201` plus `backend/app/signatures/routes.py:214-291` (`docusign_connect_webhook`).
- **What it is:** The HMAC check uses `hmac.compare_digest` correctly. But: when `settings.docusign_connect_hmac_key` is unset, the function returns `False` and the route returns 401. `validate_runtime_settings` explicitly comments "HMAC key intentionally optional: Connect is plan-gated; the webhook self-rejects when unset." That is fine when the key is unset. But the webhook route does NOT check `settings.mock_docusign` — and Agent 1's notes show `mock_docusign` flips the entire integration. If someone ships a prod build with `MOCK_DOCUSIGN=true` (which `validate_runtime_settings` blocks at boot), the boot check is the only barrier; but the webhook accepts any signed payload regardless of mock state.
- **Why it matters at scale or in prod:** The webhook activates contracts (transitions to APPROVED/EXECUTED). The boot check is the single line between "DocuSign webhook drives lifecycle" and "anyone with the HMAC key can". If the HMAC key ever leaks (it's an env var; logs/devtools/dump are all leak vectors), an attacker can mint a webhook payload and walk a contract through to EXECUTED. The current code has no anomaly detection on webhook calls (no per-envelope-id rate limit, no timestamp window check, no nonce — the body is the only signed payload).
- **Evidence:**
```python
# docusign.py:189-201
def verify_connect_signature(*, body: bytes, signature_header: str | None) -> bool:
    key = settings.docusign_connect_hmac_key
    if not key or not signature_header:
        return False
    expected = base64.b64encode(
        hmac.new(key.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected, signature_header.strip())
```
No timestamp/nonce window. A captured webhook body can be replayed.

### F-14. Auto-enqueued `contract_brain_ingestion` race: clause/obligation/renewal jobs each enqueue ingestion; concurrent contract uploads can interleave
- **Severity:** High
- **Category:** Concurrency / Data Integrity
- **Location:** `backend/app/jobs/tasks.py:216-251` (`_queue_contract_brain_ingestion`) is called from three places (`tasks.py:63, 76, 89`).
- **What it is:** A successful clause/obligation/renewal extraction enqueues a brain ingestion job keyed `f"contract_brain_ingestion:{version_id}:{snapshot_id}:{reason}"`. Three reasons → three potential ingestion jobs per contract. They run concurrently. `ingest_contract_brain` marks prior nodes/edges stale then inserts new ones — without a lock. Two concurrent ingestion runs both mark stale, both insert; the graph ends up with duplicate edges or partial stale-flagging.
- **Why it matters at scale or in prod:** The knowledge graph is used by the Contract Brain answer skill; corrupted graphs produce wrong cited answers. There is no per-contract ingestion mutex.
- **Evidence:**
```python
# tasks.py:216-251 — idempotency_key includes ":{reason}", so three different reasons -> three jobs
idempotency_key = f"contract_brain_ingestion:{version_id}:{snapshot_id}:{reason}"
existing = db.scalar(select(JobRun).where(JobRun.idempotency_key == idempotency_key))
if existing is not None:
    return
```
Plus `ingest_contract_brain` (per Agent 1) "marks prior graph stale rather than deleting" — no lock.

### F-15. `tool_runtime.execute` registers an `AssistantToolCall` row, then re-raises on permission/validation error — the row is left RUNNING with no `error_message`
- **Severity:** High
- **Category:** Data Integrity / Observability
- **Location:** `backend/app/ai/tool_runtime.py:86-157`.
- **What it is:** The flow is: (1) permission check via `has_permission`, (2) validate input pydantic, (3) `db.add(AssistantToolCall(...status=RUNNING))`, (4) flush, (5) confirmation branch OR `_execute_validated`. Steps 1-2 raise BEFORE the row is added — fine. But step 4 already inserted the row. The pydantic `model_validate` at step 2 is BEFORE the add, so that's clean — but `_execute_validated` can raise NotImplementedError-style "feature_not_enabled" branch silently returns; and the except path only triggers if step 5 raises. If `db.flush()` fails (DB error mid-tool-call), the catch path isn't taken.
- **Why it matters at scale or in prod:** Stuck `AssistantToolCall` rows in `RUNNING` state, no error_message. The 20-min reaper exists for `JobRun` only (`backend/app/jobs/routes.py:_reap_stuck_jobs`); there is no equivalent for tool calls.
- **Evidence:**
```python
# tool_runtime.py:98-157
spec = tool_registry.get(tool_name)
if not has_permission(...):
    raise HTTPException(...)
validated_input = spec.input_model.model_validate(tool_input)
call = AssistantToolCall(...status=AssistantToolCallStatus.RUNNING...)
db.add(call)
db.flush()
if spec.requires_confirmation:
    confirmation = create_confirmation(...)  # if this raises, call is left RUNNING
    db.flush()
    return {...}
try:
    result = await self._execute_validated(...)
    ...
except Exception as exc:
    call.status = AssistantToolCallStatus.FAILED
    ...
```

### F-16. Anthropic API key env-name reading is identical across all "mock vs real" toggles — flipping `MOCK_CLAUDE=false` without `CLAUDE_API_KEY` set crashes at request time, not at boot
- **Severity:** High
- **Category:** Configuration / Operations
- **Location:** `backend/app/core/config.py:93-105`, `backend/app/integrations/claude.py` (long-lived `httpx.AsyncClient` constructed on first call).
- **What it is:** `validate_runtime_settings` checks for default `secret_key`, default `setup_token`, mock toggles still on — but does NOT validate that `claude_api_key` is set when `mock_claude=False` (and the same for docusign, resend, reducto keys). Boot succeeds. First Claude request returns a 401 from Anthropic.
- **Why it matters at scale or in prod:** Deploy → smoke test passes (mock off boots fine) → first user query hits production and returns 500. No early signal at deploy time.
- **Evidence:**
```python
# config.py:142-182 — checks mock flags are off, but not that real keys are populated
enabled_mocks = [name for name, on in (...) if on]
if enabled_mocks:
    problems.append(f"disable mock integrations {', '.join(enabled_mocks)}")
# No corresponding "claude_api_key must be set when mock_claude is False" check.
```

---

## Medium

### F-17. `_model_safe_result` INTERNAL_RESULT_KEYS is a hand-curated denylist — every new tool that returns a new internal ID risks leaking it
- **Severity:** Medium
- **Category:** Security / Maintainability
- **Location:** `backend/app/ai/controller.py:43-70`.
- **What it is:** The set lists 27 keys explicitly. New IDs that aren't on the list pass through. The architecture explicitly handles `contract_id` (swapped for handle) but every new ID type is one more entry to remember. A drift between `_INTERNAL_RESULT_KEYS` and the actual return shapes is silent.
- **Why it matters at scale or in prod:** With 24 tools growing, redaction drift is inevitable. An internal UUID surfaced to the model leaks into the conversation log and could be exfiltrated through cited responses.
- **Evidence:**
```python
# controller.py:43-70 — hand-maintained denylist
INTERNAL_RESULT_KEYS = {
    "text_snapshot_id", "contract_version_id", "contract_file_id",
    "storage_object_id", "source_version_id", "base_version_id",
    ... 27 entries ...
}
```

### F-18. Three near-duplicate copies of "next contract handle" logic that must stay in sync
- **Severity:** Medium
- **Category:** Duplication / Coupling
- **Location:** `backend/app/ai/controller.py:736-774`, `backend/app/ai/tool_runtime.py:399-431`, `backend/app/assistant/routes.py:539-587`.
- **What it is:** All three compute `count = len(existing handles)`, build `f"contract-{count}"`, insert. Slightly different validation in each.
- **Why it matters at scale or in prod:** When the race in F-09 is fixed in one site, the other two stay broken. Schema changes to `AssistantContractHandle` need updating in three places.
- **Evidence:** see F-09 quotes.

### F-19. `find_contracts` and `my_attention_items` issue two unbounded org-wide queries (`Contract.org_id == org`) without limit — full-table scan at 10K+ contracts
- **Severity:** Medium
- **Category:** Performance / Scaling
- **Location:** `backend/app/ai/tool_runtime.py:336-397` (`_my_attention_items`, `_list_obligations` similar).
- **What it is:** `_my_attention_items` loads `contracts = {c.id: c for c in db.scalars(select(Contract).where(Contract.org_id == org)).all()}` — every contract in the org into memory, then `select(RenewalEvent).where(...)`, then `select(ApprovalRequest).where(...)`, then `select(Obligation).where(...)`. Every page render is O(N_contracts + N_renewals + N_approvals + N_obligations).
- **Why it matters at scale or in prod:** At 10× scale (10K contracts / org) every "what needs attention" query loads 10K rows + many joins. The endpoint is meant to be called per-page-load. This won't survive even moderate scale.
- **Evidence:**
```python
# tool_runtime.py:341
contracts = {c.id: c for c in db.scalars(select(Contract).where(Contract.org_id == org)).all()}
```

### F-20. Signature webhook does NOT guard against env-var-injected key rotation: the long-lived `claude_client` / `docusign_client` read `settings.*` at request time, not at boot
- **Severity:** Medium
- **Category:** Configuration
- **Location:** `backend/app/integrations/docusign.py`, `backend/app/integrations/claude.py`.
- **What it is:** The integration clients keep an `httpx.AsyncClient` but read `settings.docusign_connect_hmac_key` on every call. If the env is mutated post-boot (rare but possible in some deployments), the boot check (`validate_runtime_settings`) does not re-fire. Not exploitable on its own, but pairs with F-13.
- **Evidence:**
```python
# docusign.py:195 — reads key at every webhook call
key = settings.docusign_connect_hmac_key
```

### F-21. Obligation `status_filter` is a free-form string passed straight into a WHERE — no enum validation, accepts anything
- **Severity:** Medium
- **Category:** Correctness
- **Location:** `backend/app/obligations/routes.py:45,62-63`.
- **What it is:** `status_filter: str | None = None`, then `query.where(Obligation.status == status_filter)`. Pydantic does not constrain the value. Any string yields a query — most just return zero rows. No 422 for invalid status.
- **Evidence:**
```python
# obligations/routes.py:62-63
if status_filter:
    query = query.where(Obligation.status == status_filter)
```

### F-22. SSE event errors are JSON-stringified with `default=str` — UUID/datetime defaults silently mask serialization bugs and may leak `repr()` of unexpected types
- **Severity:** Medium
- **Category:** Observability / Security
- **Location:** `backend/app/assistant/routes.py:508-509` and `backend/app/ai/controller.py:877-880`.
- **What it is:** `_sse` and `_json_tool_result` use `json.dumps(payload, default=str)`. When `payload` carries a SQLAlchemy ORM object, `str(orm_obj)` returns `<Contract object at 0x...>` — leaks memory address. When it carries a `bytes`, returns Python repr.
- **Evidence:**
```python
# routes.py:508
def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"
```

### F-23. The `_apply_anchored` ordering depends on `sorted(... key=lambda a: a["start"])` — ties at `start=0` collapse to first-only
- **Severity:** Medium
- **Category:** Correctness
- **Location:** `backend/app/ai/tool_runtime.py:1635-1649`.
- **What it is:** Stable sort on `start`, so two inserts at offset 0 both have `applied=True` only for the first (because cursor advances past end). Compounds with F-05.
- **Evidence:** see F-05.

### F-24. `_extract_obligations` and `_edit_contract` raise `HTTPException(422)` from inside a tool execution — propagates the literal status code to the caller path but inside a tool loop is just a string
- **Severity:** Medium
- **Category:** Coupling / Error Boundaries
- **Location:** `backend/app/ai/tool_runtime.py:576-581, 779-785, 1233-1234`.
- **What it is:** Inside a tool execution, raising `HTTPException` is structurally wrong: the tool runs inside the streaming generator, so the 422 doesn't surface to the HTTP client — it gets caught by the controller's `except Exception` at `controller.py:289` and turned into a tool_result error. The 422 information is lost (it becomes `str(exc) = "422: ..."`).
- **Evidence:**
```python
# tool_runtime.py:576
raise HTTPException(
    status.HTTP_422_UNPROCESSABLE_ENTITY,
    "The drafting model returned no contract sections. ...",
)
```

### F-25. `decide_via_token` allows `comment` of any length — no max, could be abused as DB-row-stuffing
- **Severity:** Medium
- **Category:** Security
- **Location:** `backend/app/approvals/routes.py:33-36`.
- **What it is:** `TokenDecisionPayload.comment: str | None = None` — no Field(max_length=...). Combined with F-02 (no rate limit), an attacker who learns a token can pummel the DB with multi-MB comments.
- **Evidence:**
```python
# routes.py:33-36
class TokenDecisionPayload(BaseModel):
    token: str = Field(min_length=8)
    decision: str = Field(pattern="^(approve|reject)$")
    comment: str | None = None  # no max_length
```

### F-26. Mock module hard-codes `"mock-playbook-id"` and `"mock-project-id"` — would crash a real tool call if mock is on but the dispatch happens against a real DB
- **Severity:** Medium
- **Category:** Correctness
- **Location:** `backend/app/integrations/_claude_mock.py:115-142`.
- **What it is:** `select_mock_tool` returns `{"playbook_id": "mock-playbook-id"}` etc. The tool then runs against the real DB and 404s on the playbook lookup. In mock-Claude mode the route is supposed to wrap up gracefully, but the tool_runtime raises 404. The streaming generator catches it and feeds Claude the error.
- **Evidence:**
```python
# _claude_mock.py:115-119
if "playbook" in text and "redline" in text and "redline_against_playbook" in tool_names:
    return "redline_against_playbook", {
        "contract_handle": "contract-0",
        "playbook_id": "mock-playbook-id",
    }
```

### F-27. `_persist_metadata` mutates `Contract.contract_type` / `value_amount` etc. when `confidence == "high" and citations` — no human-in-the-loop, no diff threshold
- **Severity:** Medium
- **Category:** Data Integrity
- **Location:** `backend/app/ai/controller.py:1295-1308`.
- **What it is:** A high-confidence cited metadata extraction overwrites the contract row directly. The user never sees a "review and accept" step. Citations are validated by fuzzy match (`rapidfuzz`) which has a non-zero false-positive rate — high-confidence + fuzzy-validated does not equal "definitely right". One bad extraction permanently overwrites `value_amount` on a contract.
- **Evidence:**
```python
# controller.py:1295-1308
if metadata.confidence == "high" and metadata.citations:
    for field in [...]:
        value = getattr(metadata, field)
        if value is not None:
            setattr(contract, field, value)
```

### F-28. `_validate_and_store_assistant_citations` swallows `get_contract_for_user` errors silently in source cache lookup
- **Severity:** Medium
- **Category:** Security / Observability
- **Location:** `backend/app/assistant/routes.py:640-647`.
- **What it is:** When the per-citation `_source_for(contract_id)` fails (e.g. permission denied because the model cited a contract the user can't see), it caches `None` and the citation goes "unvalidated" — but the citation excerpt is still returned to the user. A model that hallucinated a citation against a contract the user has no access to would show the excerpt back to the user.
- **Evidence:**
```python
# routes.py:640-647
def _source_for(contract_id: str):
    if contract_id in source_cache:
        return source_cache[contract_id]
    try:
        contract = get_contract_for_user(db, contract_id=contract_id, user=current_user)
    except Exception:
        source_cache[contract_id] = None
        return None
```

### F-29. Migration 0007 `DROP TABLE IF EXISTS contract_activity CASCADE` is destructive and not gated by an empty-table check
- **Severity:** Medium
- **Category:** Operations / Data Integrity
- **Location:** `backend/alembic/versions/0007_drop_dead_contract_activity.py:16-17`.
- **What it is:** Drops a table named `contract_activity` with CASCADE — any FK that points to it is also dropped. The migration assumes "no one writes to this table" because the SQLAlchemy model was removed. But raw SQL writers (rare but possible) or accidental data in a customer environment is silently destroyed.
- **Evidence:**
```python
# 0007:16-17
def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS contract_activity CASCADE")
```

### F-30. `_handle_for_contract` in controller has different validation than `_ensure_contract_handle` in route (route checks for duplicate `requested_handle`; controller doesn't)
- **Severity:** Medium
- **Category:** Duplication / Coupling
- **Location:** `backend/app/ai/controller.py:736-774` vs `backend/app/assistant/routes.py:539-587`.
- **What it is:** Route's `_ensure_contract_handle` rejects 409 if a user-supplied handle name collides. Controller's `_handle_for_contract` doesn't (auto-generates only). When the controller is invoked from a tool result and tries to insert `f"contract-{count}"` that already exists (after a race), the integrity-error path is silent.
- **Evidence:** see F-09 / F-18 quotes.

---

## Low

### F-31. `_redacted_input` redaction substring check uses `"text" in key.lower()` — broad match catches `subtext`, `pretext`, etc.
- **Severity:** Low
- **Category:** Correctness
- **Location:** `backend/app/ai/controller.py:1669-1676`.
- **Evidence:**
```python
# controller.py:1669-1676
def _redacted_input(payload):
    redacted = {}
    for key, value in payload.items():
        if isinstance(value, str) and "text" in key.lower() and len(value) > 500:
            redacted[key] = f"<redacted text length={len(value)}>"
```

### F-32. `_json_safe` uses `json.loads(json.dumps(value, default=str))` — double-encode/decode for every audit row, hot in audit-heavy operations
- **Severity:** Low
- **Category:** Performance
- **Location:** `backend/app/core/audit.py:22-31`.

### F-33. `Settings.allowed_hosts: str = "*"` is the DEFAULT — anyone deploying without explicit override has a wildcard host
- **Severity:** Low (because `validate_runtime_settings` catches it in production environments)
- **Category:** Security / Configuration
- **Location:** `backend/app/core/config.py:23`.
- **Evidence:**
```python
allowed_hosts: str = "*"
```
The boot validator catches it in non-local environments, but anyone who sets `environment=local` in a real deployment gets a wildcard.

### F-34. The `else: return {"status": "feature_not_enabled", "tool": tool_name}` branch in `_execute_validated` is unreachable but suggests stale design intent
- **Severity:** Low
- **Category:** Maintainability
- **Location:** `backend/app/ai/tool_runtime.py:266`.
- **What it is:** Every registered tool has a branch above; the `else` clause never fires because `tool_registry.get(tool_name)` already raises KeyError for unknowns. Stale fallback.

### F-35. `_render_structured_contract_docx` re-imports `from docx import Document` per call — fine for correctness, adds ~10ms first call after Python module caching
- **Severity:** Low
- **Category:** Performance
- **Location:** `backend/app/ai/tool_runtime.py:1514`.

### F-36. Three different concurrency patterns for "find an existing handle" — some use `scalar()`, others use `scalars().all()` and `len()`
- **Severity:** Low
- **Category:** Maintainability
- **Location:** see F-09.

### F-37. `assistant/routes.py:295` `db.refresh(assistant_run)` after the run completes; this fires a SELECT that might be unnecessary if the in-memory state is current
- **Severity:** Low
- **Category:** Performance

---

## What is genuinely well-built

- **`backend/app/core/audit.py:54-83`** — Postgres advisory lock + hash chain for tamper-evidence is real engineering. (Bonus: the SQLite test fallback is gated correctly.)
- **`backend/app/core/security.py:18-26`** — SHA-256 pre-hash + base64 trick to dodge bcrypt's 72-byte ceiling, with the legacy-fallback `_legacy_bcrypt_secret` and `password_needs_rehash` so existing users aren't broken.
- **`backend/app/contracts/access.py:11-53`** — `accessible_contract_filter` pushes access into the SQL plan rather than fetching-then-filtering (modulo the admin shortcut critiqued in F-03). Right shape for scale.
- **`backend/app/contract_brain/retrieval.py:132-172`** — Bulk-loading nodes by `IN (...)` after walking edges instead of 80 round-trips per chat question. Good thinking.
- **`backend/app/ai/citations.py`** — Fuzzy citation validation with two thresholds (OCR vs native, raised for short citations) is the right shape; the design correctly distinguishes "claimed quote" from "verified excerpt".
- **`backend/app/core/config.py:142-182`** `validate_runtime_settings` — Refusing to boot non-local with mocks on / wildcards / dev defaults catches an entire class of footguns at startup. Strong baseline.
- **`backend/app/integrations/docusign.py:189-201`** `verify_connect_signature` — Uses `hmac.compare_digest` and rejects when no key is configured. Crypto-clean (issues in F-13 are not about the verify primitive itself).
- **`backend/app/jobs/routes.py:20-38`** `_reap_stuck_jobs` — Self-healing 20-min TTL on stuck queued/running jobs.
- **`backend/app/ai/tool_runtime.py:1773-1827`** — Native Word `w:ins`/`w:del` OOXML redlining is rare quality. Worth not refactoring casually.
- **`backend/app/core/request_log_queue.py`** — Batched-writer pattern for `RequestLog` (one INSERT per N requests) instead of per-request DB hit; well-scoped scaling work.
- **`backend/app/contract_files/service.py` upload pipeline** — Streaming chunked upload + magic-byte sniff + sha256 + storage-path-traversal guard. Multiple defenses stacked.
- **`backend/app/ai/redaction.py`** plus the `_redacted_input` shape — Long-text redaction in stored AI inputs so prompt content doesn't sit at rest in `AISkillRun.input_payload`. Right instinct (modulo the substring check in F-31).
