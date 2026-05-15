# Final Backend Plan: Clean-Room Legal AI + CLM Platform

## Summary

Build a new Python FastAPI backend from scratch. AEGIS and Mike are product references only; do not copy their code, prompts, database models, or modules.

This is a **contract-first legal AI + CLM backend**. Users should not think in terms of “documents vs contracts.” In the product, everything is a **Contract**. Internally, the backend separates the business/legal contract record from the file/version layer using clearer contract-centered names.

AI execution is specified in `PLAN_AI_ARCHITECTURE.md`. `PLAN.md` owns product/backend scope; `PLAN_AI_ARCHITECTURE.md` owns Claude orchestration, prompts, skills, tool runtime, validation, citations, usage, and AI observability.

Fixed choices:

```text
Backend: FastAPI
Database: Postgres in Docker with pgvector
ORM: SQLAlchemy
Migrations: Alembic
AI: Claude only for initial release
OCR: Reducto
Email: Resend
Jobs: Redis + Celery
File storage: Docker local volume behind StorageService
API prefix: /api/v1
Frontend later: Next.js
```

## Core Product Language

User-facing language:

```text
Contract
Contract Version
Contract File
Upload Contract
New Contract
Contract Hub
Contract Review
Contract Redline
Contract Brain
```

Avoid user-facing language like:

```text
Document
Promote document
Document object
Document text snapshot
```

Backend language:

```text
Contract = legal/business/lifecycle record
ContractFile = file container for the contract
ContractVersion = each uploaded/generated/redlined/signed version
ContractTextSnapshot = extracted/OCR text for a version
ContractEdit = tracked-change edits for a version
```

Relationship:

```text
Contract
  -> ContractFile
      -> ContractVersion V1 original upload
      -> ContractVersion V2 AI redline
      -> ContractVersion V3 counterparty revision
      -> ContractVersion V4 approved clean copy
      -> ContractVersion V5 signed PDF
```

Every uploaded or generated file is a contract file. The backend automatically creates a `Contract` with `ContractFile` and `ContractVersion`; users do not need to mark anything as a contract.

## Clean-Room Rule

Allowed:

```text
Use AEGIS/Mike as product references.
Recreate useful behavior with original code.
Create new models, routes, services, prompts, and tests.
```

Not allowed:

```text
Copy AEGIS/Mike source code.
Reuse prompts verbatim.
Reuse database models line-by-line.
Run AEGIS or Mike as backend dependencies.
```

## Backend Structure

Use a modular monolith:

```text
backend/app/
  core/
  auth/
  organizations/
  projects/
  contracts/
  contract_files/
  ai/
  assistant/
  workflows/
  playbooks/
  approvals/
  signatures/
  obligations/
  renewals/
  contract_brain/
  tabular_review/
  search/
  notifications/
  admin/
  integrations/
  jobs/
  debug/
```

`core/`: config, database session, SQLAlchemy base, Alembic setup, security, RBAC helpers, audit logging, shared errors, request IDs.

`auth/`: login, registration, refresh tokens, password reset, API keys, active role switching.

`organizations/`: organization setup, allowed domains, join requests, invitations.

`projects/`: project workspaces, folders, members, shared access, project activity, project contracts.

`contracts/`: contract records, metadata, lifecycle, parties, activity, Contract Hub.

`contract_files/`: file storage, versions, extracted text, OCR, generated DOCX, tracked changes, signed PDFs, shares.

`ai/`: AIController, skill registry, prompt builder/versioning, Claude provider orchestration, Pydantic schemas, citation validation, embeddings, tool registry/runtime, confirmations, usage, eval hooks.

`assistant/`: assistant product surface, SSE routes, sessions, messages, runs, contract handles, tool-call records, confirmation endpoints.

`workflows/`: reusable user-created AI workflows.

`playbooks/`: legal rulebooks, playbook generation, versioning, rules, runs, deviations, decisions.

`approvals/`: routing rules, approval requests, decisions, approval tokens, delegation.

`signatures/`: DocuSign envelopes, recipients, status sync, signed contract versions.

`obligations/`: obligation extraction, owners, due dates, reminders.

`renewals/`: renewal events, notice dates, reminders, decisions.

`contract_brain/`: clause extraction, embeddings, knowledge graph, precedent retrieval, contract-specific Q&A, portfolio Q&A.

`tabular_review/`: contract comparison tables, cell extraction, table chat, XLSX export.

`search/`: permission-aware full-text, metadata, vector, project, contract, clause search.

`admin/`: settings, domains, upload limits, provider status, retention, usage limits.

`integrations/`: Claude, Reducto, Resend, DocuSign, local file storage.

`jobs/`: Celery tasks, retries, progress, cancellation.

`debug/`: health/readiness, request traces, job logs, AI call logs, resource timelines, debug bundles.

## Infrastructure

Docker Compose services:

```text
api: FastAPI
postgres: Postgres + pgvector
redis: Celery broker/result backend
worker: Celery worker
file_storage: mounted Docker local volume
```

Postgres stores:

```text
users/org/RBAC
projects
contract metadata
contract text snapshots
embeddings
playbooks
approvals
obligations
knowledge graph
audit logs
job records
usage records
```

Local volume stores:

```text
original PDFs/DOCX/images
generated DOCX
redline DOCX
counterparty versions
signed PDFs
preview files
```

All file access goes through `StorageService`.

## User Onboarding

V1 default: single-org private deployment.

User statuses:

```text
pending_approval
active
rejected
suspended
deactivated
```

Flows:

```text
first admin created through setup token
admin invite -> user accepts -> active
self-register -> pending_approval -> admin approves/rejects
unknown domain -> join request only
```

Pending users do not receive normal access tokens.

Entities:

```text
UserInvitation
OrgJoinRequest
UserApprovalDecision
```

## RBAC, Access, Audit

Every major table includes:

```text
org_id
created_by_user_id
updated_by_user_id
created_at
updated_at
```

Specific fields where needed:

```text
owner_user_id
assigned_to_user_id
reviewer_user_id
approver_user_id
```

Core permissions:

```text
contract:read/create/update/redline/approve/sign/renew/archive
contract_file:read/create/update/delete/share
project:read/create/update/share
assistant:use/use_ai_tools
workflow:read/create/update/share
playbook:read/create/update/delete/run/publish
approval:read/decide/admin
obligation:read/update
admin_panel:access
user:read/update_role/approve
```

Contract access order:

```text
1. org admin permission
2. contract owner
3. project membership
4. explicit contract share
5. approval/signature participant limited access
6. external share token
```

Audit logs are immutable and required for:

```text
login failures
user approval/rejection
contract upload/create
contract file version creation
contract sharing
assistant mutating tools
playbook publish/run
contract lifecycle changes
approval decisions
DocuSign actions
signed version saved
obligation updates
external views/downloads
admin setting changes
```

## Storage And Contract Creation Pipeline

Storage object:

```text
StorageObject
  org_id
  storage_key
  filename
  mime_type
  size_bytes
  sha256_hash
  storage_backend
  created_by_user_id
  deleted_at
```

Upload rules:

```text
allowed MIME types only
max upload size
SHA-256 hash
soft-delete metadata first
background cleanup deletes bytes later
```

New upload flow:

```text
1. User uploads contract file
2. Store original bytes in local volume
3. Create StorageObject
4. Create Contract
5. Create ContractFile
6. Create ContractVersion V1
7. Try normal text extraction
8. Score extraction quality
9. If poor, call Reducto OCR
10. Store ContractTextSnapshot
11. Extract contract metadata
12. Extract clauses
13. Create embeddings
14. Queue Contract Brain ingestion
15. Show contract in Contract Hub
16. Write audit log
```

Generated contract flow:

```text
1. Assistant/template generates DOCX
2. Store generated DOCX
3. Create Contract + ContractFile + ContractVersion V1
4. Extract text, metadata, clauses
5. Add to Contract Hub and Contract Brain
```

Existing contract revision flow:

```text
counterparty revision upload
assistant redline
approved clean copy
DocuSign signed PDF
```

These create new `ContractVersion` records under the same `ContractFile`, not new contracts.

OCR trigger rules:

```text
empty extracted text
text length below threshold
too many unreadable characters
low alphabetic ratio
mostly image-based PDF
manual OCR request
```

## Contract File Model

Entities:

```text
ContractFile
ContractVersion
ContractTextSnapshot
ContractEdit
ContractShare
ContractEmbedding
```

`ContractFile` fields:

```text
contract_id
current_version_id
file_label
created_by_user_id
```

`ContractVersion` fields:

```text
contract_file_id
version_number
storage_object_id
text_snapshot_id
source
change_summary
is_authoritative
created_by_user_id
```

Version sources:

```text
upload
manual_upload
assistant_generated
assistant_edit
playbook_redline
counterparty_revision
approved_clean
signed
restored
template_generated
```

`ContractTextSnapshot` exists because file bytes are not suitable for fast search, citations, embeddings, playbook review, tabular review, or Contract Brain.

## Projects

Project is a workspace for contracts.

Project contains:

```text
folders
contracts
assistant sessions
tabular reviews
workflows
members
activity timeline
```

Project types for v1:

```text
general
contract_review
due_diligence
regulatory
```

Project type is metadata only. It suggests workflows/playbooks but does not restrict actions.

Contracts may exist inside or outside projects.

## Assistant

Assistant session types:

```text
general
project
contract
tabular_review
```

Tool categories:

```text
read_only
draft_or_propose
mutating
external_action
destructive
```

Tools:

```text
read_contract
find_in_contract
list_project_contracts
generate_contract_docx
edit_contract
replicate_contract_version
list_workflows
run_workflow
list_playbooks
run_playbook_review
redline_against_playbook
ask_contract_brain
get_contract_status
update_contract_metadata
submit_for_approval
send_for_signature
extract_obligations
create_tabular_review
read_table_cells
```

Confirmation required for:

```text
submit_for_approval
send_for_signature
external_share
accept_all_redlines
reject_all_redlines
archive/delete
high-impact metadata overwrite
```

Assistant rules:

```text
Claude sees contract handles like contract-0 or doc-0, not internal UUIDs
tools call service layer only
services enforce RBAC, validation, lifecycle, audit
mutating tools create confirmation_required event if confirmation is missing
```

Streaming events:

```text
message_delta
tool_started
tool_finished
confirmation_required
citation
contract_generated
tracked_change_created
approval_created
signature_sent
error
done
```

## AI Validation And Usage

All structured Claude outputs use Pydantic validation.

Applies to:

```text
contract metadata extraction
clause extraction
playbook generation
playbook review
deviation extraction
obligation extraction
tabular cells
Contract Brain query parsing
```

Store:

```text
raw_ai_output
validated_output
validation_status
model_name
token_usage
latency_ms
```

Usage tracking:

```text
AI calls
tokens
OCR pages
storage used
contracts uploaded
tabular cells generated
per-user limits
per-org limits
```

Invalid AI output should mark the job `failed` or `needs_review`, not silently update records.

## Workflows

Workflows are reusable AI processes, not legal rulebooks.

Types:

```text
assistant
tabular_review
contract_review
drafting
intake
```

Visibility:

```text
private
shared_with_users
org_wide
system_builtin
```

WorkflowRun stores:

```text
input contracts
input prompt
output
tool calls
created artifacts
model used
status
created_at
```

## Playbooks

Playbooks are governed legal rulebooks.

Entities:

```text
Playbook
PlaybookVersion
PlaybookRule
PlaybookRun
PlaybookDeviation
PlaybookDecision
```

Statuses:

```text
draft
published
archived
```

Rules:

```text
only published versions can be used for official redlines
draft versions can be tested
every run stores exact playbook_version_id
old runs stay tied to old versions
publishing/rule changes are audited
```

PlaybookRule fields:

```text
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

Run flow:

```text
1. Load current authoritative contract text
2. Load selected published playbook version
3. Claude compares contract against rules
4. Validate structured output
5. Store PlaybookRun and PlaybookDeviation rows
6. Create suggested edits
7. If DOCX is available, create tracked-change ContractVersion
8. Store deviation decisions
9. Feed final outcomes into Contract Brain
```

## Contract Lifecycle

Stages:

```text
intake
drafting
ai_review
internal_review
counterparty_review
approval_pending
approved
signature_pending
active
renewal_due
closed
archived
```

Allowed transitions:

```text
intake -> drafting | ai_review | active
drafting -> ai_review | internal_review
ai_review -> internal_review | counterparty_review
internal_review -> counterparty_review | approval_pending
counterparty_review -> ai_review | internal_review | approval_pending
approval_pending -> approved | internal_review | counterparty_review
approved -> signature_pending | active
signature_pending -> active | approved
active -> renewal_due | closed | archived
renewal_due -> active | closed
closed -> archived
```

Rules:

```text
DocuSign requires approved unless override permission
active requires signed PDF or uploaded signed-contract confirmation
every stage change creates ContractStageHistory
assistant cannot bypass lifecycle service
```

Contract Hub aggregates:

```text
contracts by stage
contracts by risk
pending approvals
pending signatures
upcoming renewals
overdue obligations
top deviated clauses
average cycle time
counterparty friction
recent activity
```

## Approvals, Signing, Obligations, Renewals

Approval routing can use:

```text
contract type
risk level
contract value
jurisdiction
counterparty
playbook deviation severity
department/owner
```

Approval token rules:

```text
single-use
expires
bound to intended approver email
decision is atomic
rejection requires comment
audited
```

Approval flow:

```text
submit contract
evaluate routing
create ApprovalRequest
Resend notifies approvers
approver decides in app or token
record ApprovalDecision
advance to approved or return to review stage
```

DocuSign flow:

```text
check permission and lifecycle
send latest approved ContractVersion
store SignatureRequest and recipients
move to signature_pending
worker/webhook syncs status
download signed PDF
create signed ContractVersion
mark signed version authoritative
move to active
queue obligations and Contract Brain ingestion
```

Obligation flow:

```text
active contract triggers extraction
Claude extracts obligations with source clauses
store owner, due date, recurrence, responsible party
create reminders
Resend sends reminders
Contract Hub displays upcoming/overdue
```

Renewal flow:

```text
extract expiration and notice dates
create RenewalEvent
notify owner before notice date
move active -> renewal_due when renewal window starts
record renew/terminate/renegotiate decision
```

## Contract Brain

Supports:

```text
contract-specific Q&A
portfolio-wide Q&A
precedent retrieval
negotiation memory
```

Every graph extraction references:

```text
contract_id
contract_version_id
text_snapshot_id
```

Staleness rules:

```text
new version marks old extraction stale
signed/active version becomes authoritative
answers prefer authoritative version
older versions can be queried explicitly
```

Knowledge node types:

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

Retrieval:

```text
parse question
apply org/project/contract access filters
retrieve graph nodes/edges
run vector search
run full-text fallback
assemble cited context
Claude answers with citations
```

## Search

Search must be permission-aware and support:

```text
contract metadata search
contract text search
clause search
project search
counterparty filters
risk/stage/date filters
semantic/vector search
```

## Tabular Review

Rows are contracts. Columns are legal questions/fields. Cells are Claude-generated answers with citations.

Entities:

```text
TabularReview
TabularReviewColumn
TabularReviewCell
TabularReviewChat
```

Cell statuses:

```text
pending
running
complete
failed
needs_review
```

Flow:

```text
create review from project contracts or selected contracts
define columns manually or from workflow/playbook
create pending cells
Celery fills cells with Claude
store answer, reasoning, citation, confidence, status
allow rerun
chat over table
export XLSX
```

## Background Jobs

Use `JobRun`.

Fields:

```text
job_type
resource_type
resource_id
idempotency_key
status
progress
started_at
finished_at
error_message
attempt_count
```

Statuses:

```text
queued
running
succeeded
failed
cancelled
```

Jobs:

```text
OCR
text extraction
embeddings
metadata extraction
clause extraction
Contract Brain ingestion
playbook review
tabular cell extraction
obligation extraction
DocuSign sync
Resend email sending
file cleanup
```

Required behavior:

```text
idempotency keys
safe retries with backoff
transactional DB writes
visible partial failures
progress via /api/v1/jobs
```

## External Sharing

External sharing supports:

```text
expiring links
optional passcode
view-only mode
download allowed/disabled
instant revoke
audit every view/download
```

External users never receive org-wide visibility.

## Admin Settings

Admin APIs manage:

```text
allowed email domains
default roles
upload size limits
allowed MIME types
OCR enabled/disabled
retention policy
DocuSign status
Resend status
Claude status
playbook defaults
approval routing rules
usage limits
debug mode
```

## Retention And Deletion

Rules:

```text
soft delete first
hard delete admin-only
file cleanup by background job
audit logs immutable
legal hold blocks deletion
retention policy per org/project
```

Use:

```text
deleted_at
deleted_by_user_id
legal_hold
```

## Debugging And Observability

Required from phase 1.

Every request logs:

```text
X-Request-ID
user_id
org_id
route
status_code
latency_ms
```

Every AI call logs:

```text
ai_call_id
request_id
resource_type/resource_id
model
token usage
latency
validation status
error class
```

Every job logs:

```text
job_id
job_type
resource_id
attempt
progress
error stack
duration
```

Debug APIs:

```text
GET /api/v1/debug/health
GET /api/v1/debug/readiness
GET /api/v1/debug/config-status
GET /api/v1/debug/requests/{request_id}
GET /api/v1/debug/jobs/{job_id}
GET /api/v1/debug/ai-calls/{ai_call_id}
GET /api/v1/debug/resources/{type}/{id}/timeline
```

Resource timeline shows:

```text
contract uploads
versions
OCR jobs
AI calls
assistant tool calls
playbook runs
lifecycle changes
approval events
signature events
obligation extraction
Contract Brain ingestion
errors
```

Dev conveniences:

```text
seed data command
dev-only reset database command
sample project/contracts
mock Claude mode
mock Reducto mode
mock Resend mode
mock DocuSign mode
verbose debug logging flag
```

## API Surface

Use `/api/v1`.

```text
/api/v1/auth/*
/api/v1/organizations/*
/api/v1/users/*
/api/v1/rbac/*
/api/v1/projects/*
/api/v1/contracts/*
/api/v1/contracts/{contract_id}/versions/*
/api/v1/contracts/{contract_id}/files/*
/api/v1/assistant/*
/api/v1/workflows/*
/api/v1/playbooks/*
/api/v1/contract-hub
/api/v1/contract-brain/*
/api/v1/approvals/*
/api/v1/signatures/*
/api/v1/obligations/*
/api/v1/renewals/*
/api/v1/tabular-reviews/*
/api/v1/search/*
/api/v1/notifications/*
/api/v1/jobs/*
/api/v1/admin/*
/api/v1/debug/*
```

## Build Phases

Phase 1: Foundation

```text
FastAPI app
Docker Postgres + pgvector
SQLAlchemy + Alembic
Redis + Celery
local StorageService
request IDs
structured logging
auth
org setup
user approval
RBAC
audit logs
debug health/readiness
```

Phase 1.5: AI Architecture Spine

```text
app/ai module
AIController as only Claude caller
provider-only ClaudeClient
SkillRegistry and ToolRegistry
shared legal system prompt
task-specific prompt versions
Pydantic output schemas
fuzzy citation validation
AISkillRun
AIPromptVersion
AIConfirmation
AICitation
AssistantRun
extended AICallLog
384-dimension local embeddings
assistant SSE run persistence
confirmation pause/resume data model
AI debug traces
golden eval harness foundation
```

Phase 2: Contracts, Files, Projects

```text
project CRUD/folders/members
contract auto-creation on upload/generation
StorageObject
ContractFile/ContractVersion/ContractTextSnapshot
text extraction
Reducto fallback
download/version restore
sharing
search basics
debug resource timeline
```

Phase 3: Assistant

```text
sessions
SSE
contract handles
read/find
citations
generate contract DOCX
edit contract DOCX
tracked changes
tool confirmations
assistant tool logs
```

Phase 4: Lifecycle + Contract Hub

```text
metadata extraction
lifecycle state machine
activity timeline
contract hub aggregation
contract-specific assistant context
```

Phase 5: Playbooks

```text
create/generate
draft/published versions
rules
runs
validated deviations
redline suggestions
tracked-change output
decision tracking
```

Phase 6: Approvals + Signing

```text
approval routing
approval requests
Resend notifications
secure approval tokens
DocuSign envelope
signature sync
signed contract version
```

Phase 7: Obligations + Renewals

```text
obligation extraction
renewal extraction
reminders
overdue detection
hub widgets
```

Phase 8: Contract Brain

```text
clause extraction
embeddings
knowledge graph
staleness handling
contract-specific Q&A
portfolio-wide Q&A
precedent retrieval
```

Phase 9: Tabular Review

```text
review creation from contracts
async cell extraction
citations
cell rerun
table chat
XLSX export
```

## Test Plan

Tests must cover:

```text
user invite/self-register/approve/reject
org isolation
RBAC and assistant tool permissions
contract auto-creation on upload
generated contract auto-creation
new version on redline/revision/signature
contract access precedence
external share expiry/revoke/audit
normal extraction and Reducto fallback
file hash/size/MIME validation
version restore
tracked-change accept/reject
assistant citations and confirmation-required tools
AIController is the only Claude caller
prompt version/hash stored on AI calls
fuzzy citation validation
AI confirmation server-side policy
AI skill run and AI call debug traces
AI validation failure handling
workflow run persistence
playbook draft/publish/run/deviation decisions
lifecycle valid/invalid transitions
contract hub aggregation
approval token atomic single-use
Resend mocked emails
DocuSign mocked completion
signed version creation
obligation and renewal extraction
Contract Brain stale-version behavior
contract-specific and portfolio Q&A
search permission filtering
tabular review generation/rerun/export
Celery retry/idempotency/cancellation
debug endpoints and resource timelines
audit logs
```

## Assumptions

```text
Every uploaded/generated file is a contract.
No manual “promote document to contract” feature is needed.
Users see Contracts and Contract Versions, not Documents.
Backend uses ContractFile/ContractVersion for the file layer.
V1 is single-org private deployment by default.
Claude is the only AI provider for initial release.
AI execution follows PLAN_AI_ARCHITECTURE.md.
Assistant product routes live in app/assistant; AI execution internals live in app/ai.
Embeddings use a 384-dimension local model for v1.
Reducto is OCR.
Resend is email.
DocuSign is signing.
Projects is the product term.
No AEGIS or Mike code is reused.
```

## Target Backend Journey

```text
Admin bootstraps org and invites users.
User creates a project.
User uploads a scanned vendor contract.
Backend creates Contract + ContractFile + ContractVersion automatically.
Normal extraction is poor, so Reducto OCR runs.
Text snapshot, embeddings, and clause extraction are created.
Contract Brain ingestion stores graph data.
Assistant summarizes with citations.
User runs a published playbook.
Backend stores deviations and creates tracked-change redline version.
Lifecycle moves through review and approval.
Resend notifies approvers.
Approval token is used once.
DocuSign sends approved version for signature.
Signed PDF returns as new contract version.
Contract becomes active.
Obligations and renewals are extracted.
Contract Brain stores final negotiated outcome.
Future projects retrieve this contract as precedent.
Debug timelines show every request, job, AI call, and lifecycle event.
```
