# AEGIS Legal CLM — Senior Architect's Technical Log

**Agent 4: The Builder** · **Date:** 2026-06-03
**Scope:** Implementation notes for the Sprint-1 reference slice in `aegis/`, and the architectural answers to the three questions the slice was built to settle. The slice is importable and its two unit tests pass (`10 passed`, pure — no DB/HTTP/Celery). This log reasons from the code that now exists, not from intent.

**Builds on:** `01_reality.md`, `02_classification.md`, `03_target_architecture.md` (Part 2 ports, Part 4 slice spec). **References live code:** `backend/app/ai/controller.py`, `backend/app/integrations/claude.py`, `backend/app/contracts/lifecycle.py`, `backend/app/contract_brain/retrieval.py`, `backend/app/ai/tool_registry.py`, `backend/app/ai/tool_runtime.py`.

---

## What was built (and what it proves)

The slice implements the full spine for two operations — a contract lifecycle transition and a structured-skill run — across all four layers, plus the two unit tests and an end-to-end SQLite smoke check (domain → use case → `PostgresUnitOfWork.commit()` → event dispatch → audit/timeline/stage-history rows, with a valid 64-char SHA-256 hash-chain link).

Three structural facts are now demonstrable, not asserted:

1. **The dependency rule is real.** `grep -rnE "^(from|import) (sqlalchemy|httpx|fastapi|celery|anthropic|pydantic|tenacity|app\.)" domain/ application/` returns nothing. The Domain layer is pure dataclasses + stdlib; the Application layer imports only domain + its own `ports/`. A reviewer can see the rule is enforced by *what the files can import*, not by convention.
2. **One transactional owner.** Every `.commit()` in the tree lives in `PostgresUnitOfWork.commit()` (plus the two deferred post-commit audit/timeline handler sessions). The refactored route (`adapters/api/contracts.py`) and Celery shim (`adapters/jobs/celery_tasks.py`) contain zero `db.commit()`/`db.flush()` — they call `uow.commit()` exactly once and hold no transaction.
3. **Audit is event-driven, never inline.** `Contract.transition_to()` *raises* `AuditLogRequested`; the use case collects it; `uow.commit()` publishes it; `AuditLogRequestedHandler` writes the hash-chained row. The test `test_audit_happens_only_via_event_never_direct` asserts the audit repo is empty until commit and that the use case makes **no** direct `write_audit_log` call — there is none to make.

The faithfulness anchor: the rules are copied from live code, only the structure changed. `ALLOWED_TRANSITIONS` is verbatim from `contracts/lifecycle.py:13-63`; the signed-version-for-ACTIVE guard, the citation threshold rule (`90 / 82 OCR / 92 short-quote`), the 25 000-char context cap, and the tenacity retry posture (`429`/`5xx`/transport/timeout, exponential, `reraise=True`) are all preserved.

---

## Question 1 — How the SSE streaming tool-use loop is modelled inside a clean use case without leaking `httpx`/SSE into the Application layer

**The problem in the live code.** `AIController.stream_assistant_run` (the streaming sibling of `run_structured_skill`) owns the durable loop: it calls the provider, inspects raw Claude `content`/`tool_use` blocks, runs tools, feeds `tool_result` blocks back, bounds the loop at `settings.ai_max_tool_iterations = 8`, pauses for confirmation on sensitive tools, commits per iteration for durability, and frames everything as SSE `event:`/`data:` lines. Provider JSON, `httpx`, the Messages-API message shape, SSE framing, the DB session, and the business policy are all braided into one async method. Nothing about that loop is unit-testable without a live provider and a live DB.

**The decomposition the ports enable.** The loop splits into four collaborators that meet only at provider-neutral DTOs:

- **`AbstractAIClient` (`application/ports/ai_client.py`)** — the seam. Its `stream_run(...)` and `resume_run(...)` are **`AsyncIterator[RunStreamEvent]`**, where `RunStreamEvent` is a closed union of *domain* dataclasses (`domain/ai/stream_events.py`): `MessageDelta | ToolStarted | ToolFinished | ConfirmationRequired | RunSucceeded | RunFailed`. The application side awaits an async iterator of these and **never sees an httpx response, a Claude block, or an SSE frame.** The concrete `AnthropicHTTPClient` (`infrastructure/anthropic/client.py`) is where httpx and the block shape live; `event_mapper.py` is the *only* module that reads `block.get("type") == "tool_use"`, translating raw blocks into `ToolStarted`/`MessageDelta` and the finished turn into `RunSucceeded`.

- **`AssistantRunStateMachine` (declared for Sprint 3; not implemented in this slice)** — itself an `async` generator over `RunStreamEvent`. Per iteration it: (1) `async for event in ai_client.stream_run(...)`; (2) on a `ToolStarted`-style event, `await tool_dispatcher.execute(...)` (the `AbstractToolDispatcher` port, Sprint 4) and accumulates a `ToolOutcome`; (3) re-enters via `ai_client.resume_run(provider_state, outcomes)`; (4) bounds the loop with an injected `max_iterations: int` = `settings.ai_max_tool_iterations` (the configurable 8). On a `ConfirmationRequired` event it persists `AssistantRun.provider_state` via the repository, yields `ConfirmationRequired`, and **returns**. The state machine imports the `ai_client` port, the `tool_dispatcher` port, and the UoW — none of `httpx`, `anthropic`, or `StreamingResponse`.

- **`AbstractToolDispatcher`** — so the loop's "run a tool" step is `await dispatcher.execute(tool_name, input, …) -> ToolResult` (another neutral DTO), not a direct call into a service. Each tool ends as one Application use case (see Question 3).

- **`AssistantSSEPresenter` (`adapters/api/presenters/`, Sprint 3)** — the **only** thing that turns `RunStreamEvent`s into `event:`/`data:` frames. SSE is an HTTP-delivery concern; it lives in the adapter, downstream of the use case, exactly where Content-Type belongs.

**What the slice already demonstrates of this model.** `AnthropicHTTPClient.stream_run` is implemented now: it drives one turn through the transport, maps blocks via `event_mapper`, yields neutral `MessageDelta`/`ToolStarted` events, bounds itself by `max_tool_iterations`, and — critically — surfaces the **confirmation pause as a yielded `ConfirmationRequired` event carrying the opaque `provider_state`** (`{"messages": …, "pending_tool_blocks": …}`), then returns. `resume_run` re-hydrates that `provider_state`, appends the confirmed `tool_result` content, and continues. So the provider-neutral event/result DTOs the loop trades in are real and live in the domain/application DTO modules; no httpx type escapes `infrastructure/anthropic/`. The state machine that consumes them is the thin Sprint-3 layer on top — the ports it needs (`AbstractAIClient` streaming + resume, `AbstractToolDispatcher`) are already defined and shaped to make it transport-free.

**Why "resume as continuation" matters.** Modelling resume as `resume_run(provider_state, tool_outcomes)` rather than "rebuild the whole conversation in the use case" keeps the Messages-API message-list shape entirely inside the infra client. The Application layer persists and hands back an opaque `dict[str, object]`; it never knows that `provider_state` is a Claude message history. That is what lets a future provider swap (or Anthropic's native streaming endpoint) happen with **zero** Application/Domain edits.

**Durability without leakage.** The live loop commits per iteration so a long assistant run survives a crash mid-stream. In the target this is an explicit Application policy executed by the UoW: the state machine calls `uow.commit()` at each iteration boundary, and the UoW dispatches that iteration's events. Same durability behaviour; the SSE framing and httpx stay in adapter/infra.

---

## Question 2 — How `pgvector` is abstracted behind `AbstractVectorStore` so no SQLAlchemy leaks into Domain/Application

**The leak in the live code.** `contract_brain/retrieval.py:63` issues a raw pgvector similarity read — `ContractEmbedding.embedding.cosine_distance(query_vec)` — and `ai/embeddings.py:53` owns the `BAAI/bge-small-en-v1.5`, 384-dim model. The `Vector(384)` column type, the cosine operator, and the embedding model are SQLAlchemy/pgvector specifics that, today, any caller of retrieval transitively depends on. A use case that wants "the most relevant clauses" cannot express that without importing the ORM.

**The abstraction (`application/ports/vector_store.py`).** Two ports, both pgvector-free in their signatures:

- **`EmbeddingModel.embed(texts) -> list[list[float]]`** — the model name and dimensionality (384) live *only* in the impl (`infrastructure/vector/fastembed_model.py`, Sprint 4). The Application layer asks for vectors; it does not know they are 384-wide or which model produced them.
- **`AbstractVectorStore`** — `upsert_chunks(...)` and `search(..., top_k, …) -> list[ScoredChunk]`. The return type `ScoredChunk` (`domain/contract_brain/retrieval_types.py`) is a pure value object: `(contract_id, text, score)`. **No `Vector`, no `cosine_distance`, no `Session` appears in any signature.** The cosine query and the `Vector(384)` column are confined to `infrastructure/vector/pgvector_store.py` and `infrastructure/postgres/models/contract_files.py` — and *only* there.

**Why this holds the line.** `AskContractBrainUseCase` (Sprint 4) composes vector + fulltext + graph results by calling `vector_store.search(...)`, `clause_repo.candidate_clauses(...)`, and `knowledge_graph_repo.graph_facts(...)`, then ranks them with the *pure* term-overlap rule moved into `domain/contract_brain/ranking.py`. Every input to the use case is a plain value object; the SQL — the `cosine_distance` operator, the `WHERE org_id` filter, the authoritative-version join — is assembled by the impl behind `search`. The org boundary is enforced twice (the use case passes `org_id`; the store applies `WHERE org_id`), matching the defense-in-depth posture of the rest of the design. The result: swapping pgvector for a dedicated vector DB later is an infra-only change; the Domain/Application layers never learn that the store was ever SQL-backed.

This slice does not implement the vector store (it is Sprint 4, explicitly out of scope), but the port contract that makes the isolation possible is the same family as the `AbstractAIClient`/repository ports the slice *does* implement: **return domain value objects, accept primitives + `org_id`, keep the driver type in the impl.** The slice's `SQLAlchemyContractRepository` is the proof-of-pattern — it returns pure `Contract` aggregates and never lets a SQLAlchemy row escape (`_mapping.to_domain_contract` copies scalars only); `AbstractVectorStore` is the identical discipline applied to the one read that today rides on pgvector.

---

## Question 3 — How the 24-tool `ToolRegistry` was decomposed so individual tools are unit-testable with zero infrastructure

**The god-object in the live code.** `ToolRuntime` (`ai/tool_runtime.py`) is the widest fan-out in the codebase (Agent 1 §4.2): it imports the `approvals`, `contract_files`, `playbooks`, `signatures`, and `tabular_review` services, plus `jobs.service`, `contract_brain.retrieval`, `contracts.lifecycle`, and three integration singletons — then dispatches 24 tools with ~40 inline flushes and 2 commits, mixing provider block translation, confirmation policy, persistence, and cross-domain business calls in one class. A single tool cannot be tested without standing up half the application.

**The three-way split (schema / dispatch / work).** The decomposition assigns each concern to exactly one layer:

- **Schema = Domain.** `ToolSpec` moves from `tool_registry.py:118` to `domain/ai/tool_spec.py`, and the 24 `_register(...)` calls become pure catalog **data** in `domain/ai/tool_catalog.py`. A tool's identity — its name, input schema, `AssistantToolCategory`, and its `requires_confirmation` rule — is now a framework-free value object. The confirmation policy (`send_for_signature`, `external_share`, `archive_contract` require confirmation) is a pure predicate on `ToolSpec`, unit-testable by constructing a spec and asserting the boolean. (This slice already proves the pattern with `SkillDefinition` in `domain/ai/skill_definition.py` + `skill_catalog.py`: the skill is pure data carrying pure `validate`/`extract_citations` callables, no Pydantic.)

- **Dispatch = Adapter.** `ToolDispatcher` (impl of `AbstractToolDispatcher`, `adapters/ai_tools/`, Sprint 4) is a thin router: validate the input against the `ToolSpec`, branch on `confirmation_policy` (the `ConfirmationGate` persists an `AIConfirmation` via a repository and returns `requires_confirmation=True` *without performing the side effect*), and otherwise call **exactly one** Application use case. The provider-block↔internal-dict translation that bloated `ToolRuntime` (`_model_safe_result`, `_json_tool_result`) moves to `tool_use_translator.py` in the same adapter package. The dispatcher depends only on use cases and ports — `grep` for `from app.(approvals|signatures|playbooks|…).service` in `adapters/ai_tools/` returns nothing.

- **Work = Application use case behind ports.** Each tool's actual behaviour is one use case: `read_contract` → `GetContractForUserUseCase`, `ask_contract_brain` → `AskContractBrainUseCase`, `send_for_signature` → `SendForSignatureUseCase` (behind `ESignaturePort`), `submit_for_approval` → `SubmitForApprovalUseCase`, `archive_contract` → `ArchiveContractUseCase`, and so on. The cross-domain service imports that made `ToolRuntime` a hub collapse into use cases that each name only their repositories/ports.

**Why each tool is now unit-testable with zero infrastructure.** Because the work is a use case that depends on *ports*, a tool's test is the same shape as the two tests in this slice: inject a fake UoW + fake ports, drive the handler, assert behaviour — no live `Session`, no httpx, no Celery, no provider. `read_contract` is tested against a `FakeContractRepository`; `send_for_signature` against a `FakeESignaturePort` (asserting `requires_confirmation=True` and that the envelope is **not** created until resume); `ask_contract_brain` against a `FakeVectorStore` returning canned `ScoredChunk`s. The 24 tools become 24 small, fast, deterministic tests — exactly the property this slice already exhibits: `RunStructuredSkillUseCase` runs green against `FakeStructuredAIClient` + a fake UoW, asserting the run reaches `SUCCEEDED`/`NEEDS_REVIEW`, citations persist, and an AI failure rolls the UoW back with no partial write.

**The load-bearing insight.** Testability followed from the dependency rule, not from a separate testing effort. Once "what a tool does" is a use case that imports interfaces instead of concretes, the infrastructure is *substitutable by construction* — the fakes in `tests/_fakes.py` are not mocks bolted onto production code, they are first-class alternative implementations of the same ports the container wires to SQLAlchemy/httpx in production.

---

## Closing note

The slice is deliberately narrow and deep: two operations, every layer, real adapters, passing pure tests, and a live-SQLAlchemy smoke check. It is not the migration — it is the *template* for it. Sprints 2–5 widen it (the remaining 12 skills, the state machine, the 24-tool dispatcher, the vector store, the full job dispatch) by **copying the pattern this slice fixes in code**, behind the `use_aegis_*` strangler-fig flags so each area cuts over and rolls back independently with behaviour preserved.
