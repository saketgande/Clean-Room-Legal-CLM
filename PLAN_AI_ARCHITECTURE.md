# Complete AI Architecture Plan: Clean-Room Legal CLM AI Layer

## 1. Final Architecture Decision

Build a custom backend-owned AI architecture instead of using a full external agent framework as the core.

The architecture will be inspired by:

- Mike: shared assistant prompt, document/contract handles, tool-based chat, SSE, generated DOCX, tracked edits.
- AEGIS: skill-style AI modules, structured outputs, playbooks, Contract Brain, graph ingestion.
- LangGraph: stateful execution, human approval interrupts, durable job-style flows.
- Pydantic AI: Pydantic schemas for structured model output.
- LlamaIndex/Haystack: retrieval pipelines, RAG, document ingestion, traceable search.
- AutoGen/CrewAI: agents/tools/flows as concepts, but not as core dependencies.

Reference links:
- [LangGraph](https://langchain-ai.github.io/langgraphjs/reference/modules/langgraph.html)
- [Pydantic AI structured output](https://pydantic.dev/docs/ai/core-concepts/output/)
- [LlamaIndex structured output](https://docs.llamaindex.ai/en/stable/understanding/agent/structured_output/)
- [Haystack](https://docs.haystack.deepset.ai/docs)
- [AutoGen AgentChat](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/quickstart.html)
- [CrewAI](https://docs.crewai.com/en/introduction)

Core rule:

```text
No route, job, assistant tool, or domain service calls Claude directly.
Every Claude call goes through AIController.
```

Final flow:

```text
Route / Job / Assistant
  -> Domain service
  -> AIController
  -> SkillRegistry
  -> PromptBuilder
  -> ContextBuilder
  -> Tool/Permission/Confirmation Guard
  -> ClaudeClient
  -> Pydantic Validator
  -> Result Persister
  -> Audit + Job + Debug Logs
```

## 2. New Backend Module Structure

Add:

```text
backend/app/ai/
  controller.py
  registry.py
  prompts/
  skills/
  schemas/
  context.py
  tool_registry.py
  tool_runtime.py
  confirmations.py
  citations.py
  output_parser.py
  prompt_versions.py
  errors.py
  service.py
```

Responsibilities:

```text
controller.py
Central entrypoint for all AI calls.

registry.py
Registers all AI skills and validates skill metadata at startup.

prompts/
Stores original clean-room prompt builders.

skills/
One file per AI skill. Each skill defines input schema, output schema, prompt builder, permissions, logging, and execution mode.

schemas/
Pydantic input/output models for AI tasks.

context.py
Builds contract/project/playbook/table context safely.

tool_registry.py
Defines assistant tools, permissions, categories, confirmation rules, and service bindings.

tool_runtime.py
Executes tools after permission and confirmation checks.

confirmations.py
Creates and resolves confirmation-required events.

citations.py
Normalizes citations from Claude outputs and validates source text.

output_parser.py
Validates structured Claude output and classifies failures.

prompt_versions.py
Stores prompt name, version, hash, and prompt assembly metadata.

errors.py
Defines AI-specific errors.

service.py
Convenience service methods used by contracts, playbooks, obligations, renewals, Contract Brain, tabular review, and assistant.
```

## 3. Provider Layer

Keep:

```text
backend/app/integrations/claude.py
```

But reduce it to a low-level provider adapter.

It should only know:

```text
Claude API key
Claude model
request payload
streaming call
non-streaming structured call
tool-use call
timeouts
rate-limit errors
token usage
raw response
```

It should not know:

```text
contracts
playbooks
obligations
Contract Brain
assistant business rules
RBAC
database update rules
```

Claude remains the only generation/reasoning provider.

Embedding decision:

```text
Use local open-source embeddings through an EmbeddingService for pgvector.
Claude is still the only LLM provider.
```

Recommended local embedding default:

```text
fastembed with BAAI/bge-small-en-v1.5
```

Reason:

```text
keeps semantic search local
avoids adding OpenAI/Gemini as another AI provider
works with pgvector
good enough for v1 contract retrieval
```

## 4. Shared Legal System Prompt

Create one shared clean-room legal system prompt.

It must not copy Mike or AEGIS text.

It should be assembled as sections:

```text
1. Product identity
2. Contract-first language
3. Legal assistant boundaries
4. Confidentiality and privacy
5. Contract handle rules
6. Citation rules
7. Tool-use rules
8. Confirmation rules
9. Structured output rules
10. Failure behavior
```

Required behavior:

```text
Use Contract, Contract Version, Contract File language.
Never expose internal UUIDs to Claude unless absolutely required by a backend-only tool.
Use handles like contract-0, contract-1.
Never invent contract facts.
Use citations for contract-specific claims.
Do not perform mutating or external actions without backend confirmation.
Return strict JSON when the skill requires structured output.
If evidence is missing, say evidence is missing.
```

Prompt composition order:

```text
Shared legal system prompt
+ org legal profile
+ selected playbook/legal policy if relevant
+ skill-specific instruction
+ resource context
+ output schema instruction
```

Org legal profile source:

```text
AdminSetting or published Playbook content, not a local legal.local.md file.
```

Reason:

```text
multi-org future safe
admin-editable
auditable
not tied to local filesystem
```

## 5. Prompt Versioning

Each prompt must have:

```text
prompt_key
prompt_version
prompt_hash
owner module
created_at
status: draft | active | archived
```

V1 storage decision:

```text
Prompt text lives in repo files.
Database stores prompt_key, version, hash, and use history.
```

Every AI call stores:

```text
prompt_key
prompt_version
prompt_hash
skill_name
input_payload
output_schema_name
raw_ai_output
validated_output
validation_status
validation_error
```

When prompt content changes:

```text
increment prompt_version
old AI logs remain linked to old prompt_hash
old playbook runs remain reproducible
```

## 6. AI Skill Definition Contract

Every AI skill must declare:

```text
name
description
input_schema
output_schema
prompt_key
prompt_version
required_permissions
resource_type
execution_mode
requires_citations
allows_mutation
requires_confirmation
job_type
audit_action
timeline_event_type
max_tokens
temperature
timeout_seconds
retry_policy
```

Execution modes:

```text
sync_structured
async_structured_job
streaming_assistant
tool_routing
retrieval_answer
docx_generation
redline_generation
```

Retry policy defaults:

```text
Validation error: no automatic retry in v1, mark needs_review
Claude timeout: retry 2 times with backoff
Rate limit: retry with backoff
Max token truncation: retry once with smaller context
Permission error: no retry
Missing evidence: no retry, return needs_review
```

## 7. Required AI Skills

### 7.1 `contract_metadata_extraction`

Trigger:

```text
after contract upload
after generated contract creation
after signed version ingestion if metadata is missing
manual rerun
```

Input:

```text
contract_id
contract_version_id
text_snapshot_id
contract text or selected chunks
filename
known project metadata
known counterparty if user supplied
```

Output:

```text
title
contract_type
counterparty_name
parties
effective_date
expiration_date
renewal_notice_date
jurisdiction
governing_law
contract_value
currency
risk_level
summary
confidence
citations
needs_review
```

Writes:

```text
Contract metadata
ContractParty rows
RenewalEvent draft if date exists
AICallLog
JobRun
AuditLog
ResourceTimelineEvent
```

Failure behavior:

```text
validation failure -> JobRun failed or needs_review
no Contract update unless output validates
low confidence -> save suggestion, not authoritative metadata overwrite
```

### 7.2 `clause_extraction`

Trigger:

```text
after text snapshot creation
after new authoritative version
manual rerun
```

Input:

```text
contract_id
contract_version_id
text_snapshot_id
contract text chunks
known clause taxonomy
```

Output:

```text
clauses[]
  clause_type
  heading
  text
  start_char
  end_char
  page
  risk_signal
  confidence
  citations
```

Writes:

```text
ClauseExtraction rows
KnowledgeNode clause nodes
ContractEmbedding chunks
stale old clause rows for prior version
```

Failure behavior:

```text
partial valid clauses can be saved only if each clause validates independently
invalid full output marks job needs_review
```

### 7.3 `obligation_extraction`

Trigger:

```text
contract becomes active
signed version saved
manual extraction
assistant tool request with confirmation if it updates records
```

Input:

```text
authoritative contract text
clauses likely to contain obligations
contract metadata
parties
```

Output:

```text
obligations[]
  obligation_type
  description
  responsible_party
  owner_suggestion
  due_date
  recurrence
  source_clause_type
  citation
  confidence
  needs_review
```

Writes:

```text
Obligation rows
ObligationReminder rows
Contract Hub obligation widgets
timeline event
```

Rules:

```text
every obligation must have citation
missing due date allowed
missing citation not allowed
low confidence obligations become needs_review
```

### 7.4 `renewal_extraction`

Trigger:

```text
contract metadata extraction
contract becomes active
manual rerun
```

Input:

```text
contract text
term/renewal/termination clauses
metadata dates
```

Output:

```text
expiration_date
auto_renewal
renewal_term
notice_date
notice_period_days
termination_rights_summary
recommended_owner
citations
confidence
needs_review
```

Writes:

```text
RenewalEvent
Contract metadata if high confidence
reminder job
timeline event
```

Rules:

```text
expiration and notice dates require citations
if date math is inferred, store calculation explanation
```

### 7.5 `playbook_generation`

Trigger:

```text
admin/legal user creates playbook from prompt
admin/legal user creates playbook from uploaded reference
assistant tool creates draft playbook
```

Input:

```text
playbook purpose
contract type
jurisdiction
company position
optional reference contracts
optional desired risk posture
```

Output:

```text
playbook_name
description
rules[]
  clause_type
  rule_type
  preferred_position
  fallback_position
  prohibited_language
  required_language
  risk_level
  rationale
  approval_required
  escalation_role
  sample_clause
  negotiation_guidance
```

Writes:

```text
Playbook draft
PlaybookVersion draft
PlaybookRule rows
```

Rules:

```text
generated playbook is always draft
never auto-publish
publishing requires user action
```

### 7.6 `playbook_review`

Trigger:

```text
user runs playbook on contract
assistant run_playbook_review tool
scheduled review after new version
```

Input:

```text
contract_id
contract_version_id
text_snapshot_id
published playbook_version_id
rules
contract clauses
```

Output:

```text
overall_risk
summary
deviations[]
  rule_id
  clause_type
  severity
  issue
  current_language
  preferred_language
  fallback_language
  suggested_fix
  approval_required
  citation
  confidence
```

Writes:

```text
PlaybookRun
PlaybookDeviation
Contract risk suggestion
timeline event
```

Rules:

```text
official run requires published playbook version
draft playbook can only create test run
every deviation must cite contract text
```

### 7.7 `playbook_redline_suggestions`

Trigger:

```text
after playbook review
assistant redline_against_playbook tool
manual redline generation
```

Input:

```text
contract version
playbook deviations
current DOCX availability
clause text
```

Output:

```text
edits[]
  edit_type
  target_text
  replacement_text
  rationale
  deviation_id
  citation
  confidence
```

Writes:

```text
ContractEdit rows
new ContractVersion if DOCX redline generated
timeline event
```

Rules:

```text
AI proposes edits
backend deterministic DOCX editor applies tracked changes
accept/reject all requires confirmation
```

### 7.8 `assistant_tool_routing`

Trigger:

```text
every assistant user turn
```

Input:

```text
assistant session
user message
available contract handles
project context
available tools
user permissions
prior messages summary
```

Output:

```text
intent
tool_calls[]
  tool_name
  arguments
  reason
  requires_confirmation
answer_without_tool_allowed
```

Writes:

```text
AssistantToolCall planned rows
debug trace
```

Rules:

```text
Claude only sees tools user is allowed to use
backend re-checks permissions anyway
mutating tools do not execute without confirmation
```

### 7.9 `assistant_final_answer`

Trigger:

```text
after read-only tool calls
after confirmed tool calls
after no tool needed
```

Input:

```text
user message
tool results
citations
contract handles
assistant session history
```

Output:

```text
streamed prose answer
citations[]
created artifact references
follow-up suggestions if useful
```

Writes:

```text
AssistantMessage
citations
timeline event if artifact created
```

Rules:

```text
stream visible text over SSE
hide internal IDs
use filenames/titles in prose
citation payload can contain handles
```

### 7.10 `contract_docx_generation`

Trigger:

```text
assistant generate_contract_docx tool
template generation
workflow drafting
```

Input:

```text
contract type
party details
business terms
source contract context
template/playbook guidance
```

Output:

```text
structured_docx_plan
  title
  sections[]
  clauses[]
  signature_blocks
  formatting_intent
citations_to_sources
```

Writes:

```text
generated DOCX bytes through document renderer
StorageObject
Contract
ContractFile
ContractVersion V1
ContractTextSnapshot
```

Rules:

```text
Claude outputs structured document plan
backend renderer creates DOCX
Claude does not directly produce raw DOCX bytes
generated file is automatically a Contract
```

### 7.11 `contract_edit_suggestions`

Trigger:

```text
assistant edit_contract tool
workflow edit
manual contract redline
```

Input:

```text
target contract version
user instruction
full or relevant contract text
existing edits
```

Output:

```text
edits[]
  target_text
  replacement_text
  edit_type
  rationale
  citation
  confidence
```

Writes:

```text
ContractEdit rows
new tracked-change ContractVersion if applied
```

Rules:

```text
edits are traceable
backend applies edits deterministically
high-impact rewrite requires confirmation
```

### 7.12 `contract_brain_query_parse`

Trigger:

```text
Contract Brain Q&A request
assistant ask_contract_brain tool
```

Input:

```text
natural language question
user/org/project permissions
optional contract_id
optional project_id
```

Output:

```text
query_scope
filters
target_clause_types
party_filters
date_filters
risk_filters
needs_vector_search
needs_graph_search
needs_full_text_search
```

Writes:

```text
BrainQuery retrieval metadata
debug trace
```

### 7.13 `contract_brain_answer`

Trigger:

```text
after retrieval context assembled
```

Input:

```text
question
retrieved clauses
graph nodes/edges
precedent contracts
full-text matches
permission-filtered context
```

Output:

```text
answer
citations[]
related_contracts[]
precedents[]
confidence
limitations
```

Writes:

```text
BrainQuery answer
timeline event
AICallLog
```

Rules:

```text
answer must cite sources
prefer authoritative active/signed versions
allow explicit older-version query
```

### 7.14 `tabular_cell_extraction`

Trigger:

```text
new tabular review
new column
cell rerun
```

Input:

```text
contract_id
contract_version_id
column prompt
contract text/retrieved snippets
```

Output:

```text
answer
reasoning
citations
confidence
needs_review
```

Writes:

```text
TabularReviewCell
JobRun
AICallLog
```

Rules:

```text
one cell per job
failure isolated to that cell
rerun replaces only that cell output
```

### 7.15 `workflow_execution`

Trigger:

```text
user runs saved workflow
assistant run_workflow tool
tabular review from workflow
```

Input:

```text
workflow definition
selected contracts
user prompt
project context
```

Output:

```text
workflow_steps
tool_calls
created_artifacts
summary
```

Writes:

```text
WorkflowRun
AssistantToolCall rows if assistant-driven
created artifacts
```

Rules:

```text
workflow prompts can guide AI skills
workflow cannot bypass permissions
workflow cannot publish playbooks or send signatures without confirmation
```

## 8. Tool Registry Design

All assistant tools are registered in one place.

Each tool definition contains:

```text
name
description
category
required_permission
input_schema
output_schema
handler_service
confirmation_policy
audit_action
timeline_event_type
visible_to_assistant
```

Tool categories:

```text
read_only
draft_or_propose
mutating
external_action
destructive
```

Tool execution order:

```text
1. Assistant proposes tool
2. ToolRuntime validates input schema
3. ToolRuntime checks permission
4. ToolRuntime checks resource access
5. ToolRuntime checks confirmation policy
6. If confirmation needed, create confirmation_required event
7. If confirmed or not required, execute domain service
8. Store AssistantToolCall
9. Write AuditLog if needed
10. Return tool result to assistant
```

Tool table:

```text
read_contract
category: read_only
permission: contract:read
confirmation: no

find_in_contract
category: read_only
permission: contract:read
confirmation: no

list_project_contracts
category: read_only
permission: project:read
confirmation: no

generate_contract_docx
category: draft_or_propose
permission: contract:create
confirmation: no, unless generated contract is shared externally

edit_contract
category: mutating
permission: contract:redline
confirmation: yes for high-impact rewrite, no for draft suggestions

replicate_contract_version
category: draft_or_propose
permission: contract_file:create
confirmation: no

list_workflows
category: read_only
permission: workflow:read
confirmation: no

run_workflow
category: mutating
permission: workflow:read
confirmation: depends on workflow actions

list_playbooks
category: read_only
permission: playbook:read
confirmation: no

run_playbook_review
category: mutating
permission: playbook:run
confirmation: no for review, yes if applying redlines

redline_against_playbook
category: mutating
permission: contract:redline
confirmation: yes before applying tracked-change version

ask_contract_brain
category: read_only
permission: assistant:use
confirmation: no

get_contract_status
category: read_only
permission: contract:read
confirmation: no

update_contract_metadata
category: mutating
permission: contract:update
confirmation: yes for high-impact overwrite

submit_for_approval
category: external_action
permission: contract:approve
confirmation: yes

send_for_signature
category: external_action
permission: contract:sign
confirmation: yes

extract_obligations
category: mutating
permission: obligation:update
confirmation: no if system lifecycle-triggered, yes if assistant-triggered bulk update

create_tabular_review
category: mutating
permission: assistant:use_ai_tools
confirmation: no

read_table_cells
category: read_only
permission: assistant:use
confirmation: no

external_share
category: external_action
permission: contract_file:share
confirmation: yes

archive_contract
category: destructive
permission: contract:archive
confirmation: yes
```

## 9. Confirmation System

Add a backend confirmation object.

Fields:

```text
id
org_id
session_id
tool_call_id
resource_type
resource_id
action_name
risk_level
proposed_arguments
human_readable_summary
expires_at
status: pending | confirmed | rejected | expired
created_by_user_id
decided_by_user_id
decided_at
```

Assistant SSE event:

```text
confirmation_required
```

Payload:

```text
confirmation_id
tool_name
summary
risk_level
expires_at
preview
```

Confirmation flow:

```text
assistant proposes risky action
backend creates confirmation
frontend shows confirm/reject UI
user confirms
backend revalidates permissions and resource state
tool executes
assistant receives result
assistant final answer streams
```

Rules:

```text
confirmation cannot be reused
confirmation expires
confirmation arguments cannot be changed after creation
permission is checked again at execution time
resource version is checked to prevent stale approval
```

## 10. Structured Output Validation

Every AI output has two layers:

```text
schema validation
business invariant validation
```

Schema validation:

```text
Pydantic checks field types, enums, required fields
```

Business invariant validation examples:

```text
deviation count equals number of deviations
citations exist when citations_required=true
cited quote appears in source text
contract_version_id matches current resource
date outputs are parseable
risk_level is allowed enum
tool name exists in registry
tool arguments match tool schema
```

Validation statuses:

```text
not_validated
valid
invalid_schema
invalid_invariant
needs_review
provider_error
truncated
permission_denied
```

Update rule:

```text
Only valid outputs can update final business records.
needs_review outputs can be stored as suggestions.
invalid outputs never update final records.
```

## 11. Citation Architecture

Citation object:

```text
citation_id
contract_id
contract_version_id
text_snapshot_id
clause_id nullable
page nullable
start_char nullable
end_char nullable
quote
source_label
confidence
```

Rules:

```text
Every contract-specific claim needs citation.
Every playbook deviation needs citation.
Every obligation needs citation.
Every renewal date needs citation.
Every tabular cell needs citation unless answer is "not found".
Every Contract Brain answer needs citations.
```

Validation:

```text
quote must appear in ContractTextSnapshot text or page map
if quote does not match, citation is invalid
invalid required citation -> output needs_review or failed
```

Assistant prose:

```text
Frontend may show citations as chips.
Claude may use contract handles internally.
User-facing prose should use contract title or filename, not internal UUIDs.
```

## 12. Context Building

Add context builders:

```text
ContractContextBuilder
ProjectContextBuilder
PlaybookContextBuilder
AssistantSessionContextBuilder
TabularReviewContextBuilder
ContractBrainContextBuilder
```

Contract context includes:

```text
contract metadata
current authoritative version
text snapshot
clauses
parties
lifecycle stage
risk level
playbook runs
obligations
renewals
recent versions
```

Token budgeting:

```text
small contract: include full text
large contract: include table of contents + relevant chunks
Contract Brain: include retrieved snippets only
tabular cell: include relevant chunks for that column
playbook review: include clauses mapped to playbook rule types
```

Hard rule:

```text
Do not dump every contract in a project into Claude.
Use retrieval/chunking.
```

## 13. Assistant Runtime

Assistant has three layers:

```text
session layer
tool-routing layer
answer layer
```

Request flow:

```text
POST /assistant/sessions/{id}/stream
  -> load session
  -> save user message
  -> build handles
  -> build available tools from permission-filtered ToolRegistry
  -> AIController runs assistant_tool_routing
  -> ToolRuntime executes allowed tools or returns confirmation_required
  -> AIController runs assistant_final_answer
  -> stream SSE events
  -> save assistant message and annotations
```

SSE events:

```text
message_delta
reasoning_delta optional dev-only
tool_started
tool_finished
confirmation_required
citation
contract_generated
tracked_change_created
approval_created
signature_sent
job_created
error
done
```

Persistence:

```text
AssistantSession
AssistantMessage
AssistantToolCall
AssistantContractHandle
AICallLog
AuditLog
ResourceTimelineEvent
```

Rules:

```text
assistant session can be general, project, contract, or tabular_review
contract handles are stable within a session
new generated contracts get new handles
assistant cannot call tools not exposed by permissions
backend never trusts Claude tool arguments without validation
```

## 14. Background Job AI Runtime

AI jobs use the same AIController.

Job flow:

```text
JobRun queued
worker loads job
worker builds context
AIController runs skill
output validates
domain persister applies valid output
JobRun succeeds
debug timeline updated
```

Common AI jobs:

```text
metadata_extraction
clause_extraction
contract_brain_ingestion
playbook_review
tabular_cell_extraction
obligation_extraction
renewal_extraction
embedding_generation
```

Idempotency keys:

```text
metadata_extraction:{contract_version_id}:{text_snapshot_id}
clause_extraction:{contract_version_id}:{text_snapshot_id}
obligation_extraction:{contract_version_id}
renewal_extraction:{contract_version_id}
tabular_cell:{cell_id}:{column_version}
playbook_review:{contract_version_id}:{playbook_version_id}
```

Retry behavior:

```text
provider timeout -> retry
rate limit -> retry
validation failure -> no retry by default
missing resource -> fail
permission failure -> fail
stale version -> cancel
```

## 15. Contract Brain AI Flow

Ingestion flow:

```text
new text snapshot
-> clause_extraction
-> local embeddings
-> entity extraction
-> relationship extraction
-> stale old graph entries
-> create new graph entries tied to version
```

Q&A flow:

```text
question
-> contract_brain_query_parse
-> permission-aware metadata filter
-> graph retrieval
-> vector retrieval
-> full-text fallback
-> assemble cited context
-> contract_brain_answer
-> store BrainQuery
```

Retrieval priority:

```text
1. explicit contract/version requested by user
2. authoritative signed/active version
3. current authoritative version
4. older versions only if asked
```

Graph node types:

```text
contract
party
clause
person
jurisdiction
obligation
approval
signature
playbook_rule
project
```

Edge types:

```text
contains_clause
negotiated_with
approved_by
signed_by
amends
supersedes
related_to
has_obligation
deviates_from_rule
accepted_fallback
rejected_position
```

## 16. Playbook AI Flow

Playbook generation:

```text
input prompt/reference
-> playbook_generation skill
-> validate rules
-> create draft playbook version
-> audit
```

Playbook review:

```text
selected contract version
+ selected published playbook version
-> context maps rules to relevant clauses
-> playbook_review skill
-> validate deviations
-> save PlaybookRun + PlaybookDeviation
```

Redline:

```text
PlaybookDeviation rows
-> playbook_redline_suggestions skill
-> validate edits
-> create ContractEdit suggestions
-> DOCX redline renderer creates new ContractVersion
```

Decision tracking:

```text
user accepts/rejects deviation
-> PlaybookDecision
-> Contract Brain learns final negotiated outcome
```

## 17. Tabular Review AI Flow

Create review:

```text
selected contracts
columns
-> create TabularReview
-> create TabularReviewColumn rows
-> create pending TabularReviewCell rows
-> queue one job per cell
```

Cell job:

```text
cell
-> retrieve relevant contract snippets
-> tabular_cell_extraction skill
-> validate answer/citations
-> update cell status
```

Statuses:

```text
pending
running
complete
failed
needs_review
```

Rules:

```text
one bad cell does not fail the table
rerun only affects selected cell
table chat uses completed cell data + citations
XLSX export includes answer, confidence, citations
```

## 18. DOCX Generation And Redline Strategy

Claude role:

```text
produce structured document plan or structured edit suggestions
```

Backend role:

```text
render DOCX
apply tracked changes
store bytes
create ContractVersion
extract text again
queue metadata/clause/brain jobs
```

Rule:

```text
Claude never directly writes final binary files.
```

Generated contract flow:

```text
AI structured plan
-> DOCX renderer
-> StorageService
-> Contract + ContractFile + ContractVersion
-> TextSnapshot
-> metadata/clause/brain jobs
```

Edit flow:

```text
AI edit suggestions
-> ContractEdit rows
-> tracked-change renderer
-> new ContractVersion
-> extraction jobs
```

## 19. Data Model Changes Needed

Add or extend these records.

New:

```text
AISkillRun
AIConfirmation
AIPromptVersion
```

Extend `AICallLog`:

```text
skill_name
skill_run_id
job_id
session_id
tool_call_id
prompt_key
prompt_version
prompt_hash
input_payload
output_schema_name
validation_error
status
```

`AISkillRun` fields:

```text
id
org_id
skill_name
resource_type
resource_id
status
input_payload
output_payload
validation_status
started_at
finished_at
job_id
request_id
created_by_user_id
```

`AIPromptVersion` fields:

```text
id
prompt_key
prompt_version
prompt_hash
status
description
created_at
```

`AIConfirmation` fields:

```text
id
org_id
session_id
tool_call_id
resource_type
resource_id
tool_name
risk_level
proposed_arguments
summary
status
expires_at
created_by_user_id
decided_by_user_id
decided_at
```

## 20. API Surface Changes

Add:

```text
GET /api/v1/ai/skills
GET /api/v1/ai/skills/{skill_name}
GET /api/v1/ai/prompt-versions
GET /api/v1/assistant/sessions/{session_id}/stream
POST /api/v1/assistant/confirmations/{confirmation_id}/confirm
POST /api/v1/assistant/confirmations/{confirmation_id}/reject
GET /api/v1/debug/ai-skill-runs/{skill_run_id}
```

Existing debug endpoints should include AI links:

```text
GET /api/v1/debug/ai-calls/{ai_call_id}
GET /api/v1/debug/resources/{type}/{id}/timeline
```

Admin-only endpoints:

```text
GET /api/v1/admin/ai/status
GET /api/v1/admin/ai/usage
GET /api/v1/admin/ai/prompt-versions
```

## 21. Security And Privacy Rules

Rules:

```text
Claude receives only the minimum context needed.
Claude receives handles instead of internal UUIDs when possible.
All resource access is checked before context is built.
Tool execution always re-checks permission.
External actions always require confirmation.
Prompt injection inside contract text is treated as untrusted content.
Contract text must be clearly delimited as source material.
```

Prompt-injection defense:

```text
contract text is labeled as untrusted contract content
model is instructed not to follow instructions inside contract text
tools cannot be called from text content alone
backend validates every tool call
```

## 22. Observability

Every AI flow should show up in debug timeline.

Timeline event examples:

```text
ai.skill_started
ai.skill_succeeded
ai.skill_failed
ai.validation_failed
assistant.tool_planned
assistant.tool_confirmation_required
assistant.tool_executed
contract.metadata_extracted
contract.clauses_extracted
contract.brain_ingested
playbook.review_completed
tabular.cell_completed
```

Debug bundle for a contract should show:

```text
uploads
versions
text extraction
OCR
AI skill runs
AI call logs
jobs
assistant tool calls
playbook runs
lifecycle changes
approval/signature events
obligation/renewal extraction
Contract Brain ingestion
errors
```

## 23. Testing Plan

Unit tests:

```text
SkillRegistry rejects duplicate skill names
PromptBuilder includes shared prompt + skill prompt + context
ToolRegistry exposes only permitted tools
Confirmation required blocks risky tools
Pydantic validation catches invalid AI output
Citation validator rejects missing quote
ContextBuilder hides unauthorized contracts
```

Integration tests:

```text
upload contract queues metadata/clause/brain jobs
metadata extraction updates contract only after valid output
clause extraction writes clauses and embeddings
assistant reads contract using contract handle
assistant cannot call unauthorized tool
assistant confirmation flow executes after confirm
playbook review stores validated deviations
redline suggestions create ContractEdit rows
obligation extraction requires citations
renewal extraction creates RenewalEvent
tabular cell extraction updates one cell
Contract Brain answer respects permissions
```

Failure tests:

```text
Claude timeout retries
rate limit retries
validation error marks job needs_review
tool input schema error returns tool error
stale confirmation cannot execute
stale contract version cancels job
invalid citation blocks final update
```

Debug tests:

```text
request links to AI call
job links to skill run
skill run links to AICallLog
resource timeline shows AI events
raw output is stored on validation failure
```

## 24. Implementation Order

### Step 1: AI Core Foundation

```text
create app/ai module
add SkillRegistry
add PromptBuilder
add AIController
extend AICallLog
add AISkillRun
add AIPromptVersion
add AIConfirmation
```

### Step 2: Structured Output Runtime

```text
add base AI schemas
add output parser
add validation status handling
add citation validator
add prompt version hashing
```

### Step 3: First AI Skills

Implement first:

```text
contract_metadata_extraction
clause_extraction
assistant_tool_routing
assistant_final_answer
```

Reason:

```text
these unlock upload intelligence and assistant foundation
```

### Step 4: Tool Runtime

```text
replace static assistant tool dict with ToolRegistry
add ToolRuntime
add confirmation flow
add AssistantToolCall persistence
add SSE confirmation events
```

### Step 5: Phase 2/3 Integration

```text
wire upload jobs to AIController
wire assistant sessions to AIController
wire read/find/generate/edit tools
```

### Step 6: CLM AI Skills

```text
playbook_generation
playbook_review
playbook_redline_suggestions
obligation_extraction
renewal_extraction
```

### Step 7: Contract Brain

```text
local embeddings
query parser
retrieval pipeline
answer skill
graph staleness
precedent retrieval
```

### Step 8: Tabular Review

```text
cell extraction skill
cell jobs
rerun
table chat
XLSX export
```

## 25. Non-Negotiable Rules

```text
Do not copy Mike or AEGIS prompts.
Do not copy Mike or AEGIS source code.
Use them only as references for architecture ideas.
Do not let domain modules call Claude directly.
Do not let Claude mutate records directly.
Do not update business records from invalid AI output.
Do not expose internal UUIDs in user-facing assistant text.
Do not execute external actions without confirmation.
Do not answer contract-specific questions without citations.
```

## 26. Final Target Shape

When complete, every AI feature follows the same pattern:

```text
User/job/system event
-> AI skill selected
-> permission checked
-> context built
-> prompt assembled
-> Claude called
-> output validated
-> citations checked
-> domain records updated only if valid
-> audit/job/debug logs written
-> assistant or API returns traceable result
```

This gives the application:

```text
Mike-like assistant quality
AEGIS-like skill/playbook structure
LangGraph-like state/approval discipline
Pydantic-style validation safety
Haystack/LlamaIndex-style retrieval discipline
CLM-specific audit, RBAC, lifecycle, and Contract Brain control
```
