# Ask Aegis audit — 2026-10-30

Read-only audit of the in-app assistant. Everything below comes from reading code (nothing was run live), except where marked "confirmed" by the orchestrator. Items marked "inferred" were not confirmed at runtime. Line numbers are approximate. No fixes have been made for anything in Parts B–D.

Paths relative to the repo root. RT = `backend/app/ai/tool_runtime.py`, REG = `backend/app/ai/tool_registry.py`.

---

## Part A — Found by the orchestrator (1–20)

Already partly fixed in code this session: request-filing questions, label list, counterparty register lookup, template-vs-free drafting choice, `draft_from_template`, needed-by date check.

1. Chat-drafted contracts get no workflow/request (`start_flow_for_contract` is only called by the workflow route/service).
2. Drafts take the counterparty as typed text only (no register lookup, no `counterparty_id`), use the org name as "our company", never ask which legal entity signs.
3. `create_intake_request` captures ~4 fields; the wizard asks 15+ (entity, their signer, term, governing law, payment terms, start date).
4. Chat labels "Amendment/Renewal/Termination/Novation Request" are unsafe: those need a parent contract the chat cannot attach, so `apply_change_request` never writes onto the original.
5. `archive_contract` hard-codes `override=True`, from any stage; no legal-hold, open approval/signature, live obligation checks.
6. `external_share` and counterparty negotiation: link with no recipient, no final/approved check, passcode optional.
7. `reassign_request`: exact case-insensitive match only ("Saket" finds nobody); two people with the same name raises an error; no active/team check.
8. `create_notice`: counterparty typed text, no register check, no contract prompt, free-text `notice_type`.
9. No list-tasks tool, but `complete_task` needs a raw task id.
10. Cannot edit/cancel a request; cannot make renewal decisions.
11. No counterparty/entity register tool (search, look up, add).
12. Inconsistent answers: #24 listed 7 Globex contracts with no tool call vs 4 in #6; #21 vs #28 name different pending approvals. The model answers from earlier context.
13. No today's date in the system prompt (a needed-by of 2024-11-16 was filed in 2026).
14. `find_contracts`: whole-phrase substring on title/counterparty only, `limit(8)` with no ORDER BY, so a different subset can come back.
15. `list_my_approvals` returns only ids and filters on `approver_user_id`, so team approvals (`approver_team_id`) never appear.
16. `list_my_requests`: only requests you raised, includes closed, silent cap of 25 rows.
17. `get_intake_request` summary omits requester, counterparty, value, due date, SLA, approval state, attachments, activity.
18. `decide_approval` approves blind (no step, approver, value or authority shown first).
19. No duplicate-request check beyond the same request twice within ~2 minutes.
20. Some confirmation cards arrive with an empty chat message (sheet #8).

---

## Part B — Intake, workflow, notice, approval tools and missing abilities (subagent report, verbatim)

All findings come from reading code only. "RT" = tool_runtime.py, "REG" = tool_registry.py. The audit read the intake, workflows, notices, approvals and signatures services and routes, but did not open most frontend pages.

### PART 2: DEPTH DEFECTS IN EXISTING TOOLS

1. **list_workflows lists the wrong thing** (defect, high)
   - Scenario: "Which workflow should I start on REQ-123?" or "what workflows do we have?"
   - Evidence: RT `_list_workflows` (~l.1417) queries the `Prompt` table (prompt-library items). `start_intake_workflow.workflow_id` and `create_workflow` use the `Workflow` engine table. No tool lists engine workflows or their steps.
   - Result: the model can only guess a workflow_id or leave it blank.
   - `run_workflow` (REG l.414) also takes a prompt id and is gated on `workflow:read` while being a mutating tool.

2. **decide_approval picks the oldest pending approval on the request, not the user's** (defect, high)
   - Scenario: a request has several rungs or approvals and the user is the approver of a later one. "Approve REQ-55" returns "You are not assigned to decide this approval" even though the user has a pending approval there.
   - Evidence: RT `_decide_approval` (l.646) selects `ApprovalRequest` by `intake_request_id` and PENDING, ordered by `created_at` ascending. It does not filter on `approver_user_id` or team.
   - The tool also cannot target contract-level approvals (no `contract_id` or `approval_id` input), although `list_my_approvals` returns those.

3. **decide_approval skips the open-risk acknowledgement the Approvals page enforces** (defect, high)
   - Scenario: "approve it" when the contract has critical or high open risks. The UI route refuses until `acknowledged_open_risks` is set and writes an `approval.risk_acknowledged` audit row. The chat tool approves silently with no audit row.
   - Evidence: backend/app/approvals/routes.py l.326-390 holds the gate. The comment there says it is deliberately kept out of `decide_in_app`, so the tool bypasses it. `DecideApprovalInput` (REG l.258) has no acknowledgement field.
   - This is separate from the known "decide_approval blind" finding, which is about what the tool shows. This one is about a missing governance gate.

4. **advance_intake_workflow reports success when nothing advanced** (defect, high)
   - Scenario: "advance REQ-9" when the current step is an AI step, a counterparty step, or already running or finished. The tool says `advanced: true` and the user believes it moved.
   - Evidence: RT l.614-632. Any step type other than approval or signature goes to `complete_human_step`. In backend/app/workflows/service.py l.1441-1474 that function silently does nothing unless the step is `waiting_human`. The tool then returns `advanced: True` unconditionally.
   - It also never checks whether the run is already completed or cancelled.

5. **advance_intake_workflow drops the UI's step options** (gap, med)
   - Scenarios: "complete the review step even though redlines are open" (override), "complete step 2 of the parallel group", "reply to the rework request".
   - Evidence: the route `/runs/{id}/complete-step` (workflows/routes.py l.212) accepts `step_idx`, `override_reason` and `rework_reply`. `AdvanceIntakeWorkflowInput` (REG l.249) only has `note`.
   - Parallel steps are auto-picked. An open-redline block comes back as a raw 409 with no way to override.
   - The workflow-run actions "return to earlier step", "retry review", "comment on run" and "counterparty send" (routes l.236-293) have no tools.

6. **start_intake_workflow says "started" when a run already exists** (defect, med)
   - Scenario: "start the NDA workflow on REQ-7" when another flow is already running. The tool returns `started: true` with the old run and ignores the requested workflow_id.
   - Evidence: `start_flow` (workflows/service.py l.529-531) returns the existing open run. RT l.611-612 labels it "started" regardless.
   - It also does not check for a closed request.
   - The "No workflow matches this request" 422 gives the model no list of workflows to pick from (see 1).

7. **create_notice cannot set a deadline, dates, owner or priority** (gap, high)
   - Scenario: "Log a notice received today from Acme, termination notice, we must respond within 30 days."
   - Evidence: `CreateNoticeInput` (REG l.305) has only subject, counterparty_name, direction, notice_type, description and contract_id. `NoticeCreate` (notices/schemas.py l.31-52) supports `notice_date`, `received_at`, `response_due_date`, `deadline_basis`, `deadline_confirmed`, `priority`, `owner_user_id`, `counterparty_ref` and `status`.
   - A chat-filed notice has no due date, so it does not get overdue or at-risk handling or reminders. It only appears as "undated" in the notices summary, and the tool does not warn about that.
   - The `/notices/extract` AI extraction that reads a pasted notice has no tool.
   - `notice_type` is not checked against the allowed list in the tool, so a free-text type fails Pydantic validation with a raw error.
   - The return value does not tell the user the notice has no due date.

8. **Notice register actions beyond create and draft have no tools** (gap, high)
   - Scenarios: "mark notice N-12 as responded or closed", "change the due date", "add a note to the notice", "attach the PDF the counterparty sent", "escalate this notice to legal", "delete the duplicate notice".
   - Evidence: notices/routes.py has `PATCH /{id}`, `POST /{id}/status`, `/escalate`, `/notes`, `/documents` (POST and DELETE), `DELETE /{id}` and `/summary`. The only notice tools are list_notices, read_notice, create_notice and draft_notice_response (REG l.300-323).
   - Escalation, which turns a notice into an intake ticket (notices/service.py l.790), is the most obvious missing one.
   - There is also no notice summary tool for "how many notices are overdue".

9. **list_notices has thin filters and a silent cap** (defect, med)
   - Scenario: "show received termination notices from Acme owned by me", or "list all open notices" with more than 25.
   - Evidence: RT l.756-760 passes only `status` and `overdue_only`. The service supports direction, type, owner, contract and `q` search. Rows are sliced `[:25]` with `count` and `total` both set to `len(rows)`, which is the full count. The model cannot tell it was truncated.
   - Rows are the full serialized notice (ordering unknown), not a trimmed summary.

10. **draft_notice_response overwrites the existing draft and gives no feedback on generation** (defect, med)
    - Scenario: the user already wrote or edited a draft on the notice, then asks "draft a response".
    - Evidence: `NoticesService.draft_response` (notices/service.py l.759-790) says it overwrites any previous draft.
    - RT l.775-781 reports `drafted: true` and returns only the draft text.
    - The service also returns `draft_generated`, which is false when the AI was unavailable and a template was used. The tool drops it, so the model presents a boilerplate template as an AI draft.
    - There is no confirmation step before it overwrites. The tool is registered MUTATING, so the default is "required", but the prompt shows no warning about an existing draft.
    - There is no tool to send or record the response either (`POST /status` to responded).

11. **complete_task has no read side and no useful result** (gap and defect, med)
    - Scenario: "mark my 'collect W-9' task done." The user has no way to give a task id.
    - Evidence: RT l.833-839 returns just `task_id`. `TaskUpdateReq` and `update_task` also support title, description, assignee and status. Tasks can also be created and deleted, and effort can be logged (intake/routes.py l.315-368). None of this is exposed.
    - `complete_task` has no way to reopen a task and does not say which request or task title it closed.
    - `my-work` (intake/routes.py l.370) is also not exposed.

12. **reassign_request does not check the target user's state** (defect, med)
    - Scenario: "Reassign to Priya" when Priya is inactive, or the requester's own account.
    - Evidence: RT l.662-679. The query matches `UserModel` by org and name or email only, with no active filter or role or permission check.
    - Two people with the same name make `db.scalar` take an arbitrary one (silent wrong assignee). The exact-name matching itself is already known.
    - The result returns only `owner` (the full name), not the previous owner.
    - The UI has an "assignees" list endpoint (`GET /intake/assignees`) that the tool does not use. There is also a claim or pool flow (`/requests/pool`, `/assign-to-me`) with no tool: "take this request myself", "what is in the unassigned queue".

13. **get_signature_link returns a page path, not a signing link** (defect, med)
    - Scenario: "Give me the link to sign the NDA." The tool returns `/contracts/{id}` with the note "Open this link to review and sign".
    - Evidence: RT l.697-704. It does not check that a signature request exists, that the user is a signer, or that the contract is in a signable stage.
    - Compare `get_signature_status`, which returns "No signature yet".
    - The link is a relative path that the model may present as a signing URL.

14. **get_signature_status is thin** (defect, low-med)
    - Scenario: "Who still has to sign, and since when?"
    - Evidence: RT l.841-860 returns the status, plus name and status per recipient. It omits email, role and order, sent and signed timestamps, and the provider envelope status.
    - It takes `order_by(created_at.desc())` and reads only the newest request; older voided or declined requests are not shown.
    - The Signatures page (`GET /signatures`, with sync, resend and void) has no tool. "Which contracts are waiting for signature", "refresh from DocuSign" (`/requests/{id}/sync`) and "remind the signer" all fail.

15. **send_for_negotiation does not do what the UI does** (defect, med)
    - Evidence: RT l.714-753.
    - Counterparty branch: it only mints a view-only `ContractShare`. It sets no expiry, writes no audit or timeline event, and does not move the stage or open a negotiation round. Yet the note claims "their returned redline is recorded as the next round".
    - Internal branch: it inserts `Notification` rows directly for every active team member. This bypasses the notification service and writes no audit or timeline event. It does not exclude the sender, and a team with zero members returns `notified: 0` as success.
    - Neither branch checks that the contract is in a negotiable stage.
    - The negotiation panel in the UI (`frontend/src/app/(app)/intake/_negotiation-panel.tsx`) has more actions than the tool. The external-share recipient problem is already known.
    - `team_id` is a raw id and there is no team-list tool, so the model cannot supply it.

16. **create_workflow input is not checked in the tool** (defect, med)
    - Scenario: "Create a workflow: draft, legal approval, sign."
    - Evidence: `steps: list[dict]` is free-form (REG l.269). `_clean_steps` may drop or alter things silently, and the result echoes only step names.
    - Criteria are limited to `match_type` (RT l.637), while the UI supports richer criteria and `eval_order`.
    - The flow is created enabled, and `_refuse_clash` is the only guard.
    - `create_flow` writes no audit row, unlike `update_flow`.
    - The tool needs `admin_panel:access`, while the route needs `workflow:create`. These differ.
    - There is no update, enable or disable, or delete tool for flows, and no "show me workflow X's steps" tool.

### PART 1: CAPABILITY GAPS (page and action with no tool)

17. **Intake request actions beyond create, get and reassign** (gap, high)
    - Page: `(app)/intake`, routes at intake/routes.py l.157-290 and l.388-610.
    - Failing requests:
      - "Add a comment or answer to REQ-x" (the request-answers panel).
      - "Attach this file to REQ-x" (`/documents`, `/ingest-attachment`).
      - "Show the activity or timeline on REQ-x" (`/activity`).
      - "Show the approval chain for REQ-x" (`/approval-chain`, `/approval-summary`).
      - "Who is the approver for REQ-x" (the `intake/_approval-card.tsx` and `_governance-ladder.tsx` UI).
      - "What's the SLA or when is it due" (`/sla`) and "pause the clock" (`/pause`).
      - "Hand it back to the requester" (`/handoff`, `/handoffs`).
      - "Promote to a contract" or "draft the contract for this request" (`/promote`, `/draft-contract`).
      - "Show all requests" or "requests in the queue" (`/requests`, `/pool`; the tool only lists requests I raised).
    - Evidence: the registered intake tools are only create, get, start, advance, list_my_requests and reassign.

18. **No approver or authority lookups** (gap, high)
    - Scenarios: "Who needs to approve this contract?", "what is my approval limit?", "can I approve a $2M deal?", "who can I escalate this approval to?"
    - Evidence: backend/app/approvals/routes.py has `/contracts/{id}/chain`, `/eligible-approvers` and `/requests/{id}/reassign-candidates`. The authority module (backend/app/authority/) has the policies that `_enforce_approve_authority` applies. The `(app)/delegations` page exists.
    - No tool reads the approval chain or the user's delegated authority.
    - The model learns about an authority limit only after a failed `decide_approval`.

19. **Delegate, escalate and reassign an approval, and delegations** (gap, med)
    - Scenarios: "Delegate this approval to Sam", "escalate to the GC", "set me out-of-office and delegate my approvals".
    - Evidence: `POST /approvals/requests/{id}/reassign` (approvals/routes.py l.415), reassign-candidates, and the `(app)/delegations` page. The registered tool `reassign_request` only changes the intake owner, not the approver.

20. **Notifications have no tool** (gap, med)
    - Scenarios: "What's new for me?", "mark all my notifications read", "how many unread do I have".
    - Evidence: notifications/routes.py has GET, `/unread-count`, `/read-all` and `/{id}/read`. REG has no notification tool. `my_attention_items` is the closest.
    - Related pages with no tools: `(app)/my-work` (the combined work queue, `/intake/my-work`), `(app)/sla` (SLA operations), and `(app)/search`, which is global search.

21. **Activity, history and audit on contracts** (gap, med)
    - Scenarios: "Show the activity or timeline on this contract", "who changed this and when", "what was the last comment".
    - Evidence: `add_contract_comment` exists (RT l.706) but there is no comment-list tool, and `ContractCommentService` also supports replies, mentions and anchors. There is no contract-timeline or audit-read tool. The `(app)/contracts/[id]` page was not opened.
    - `add_contract_comment` takes no `parent_comment_id` or `mentioned_user_ids`, so "reply to Sam's comment" and "tag Legal" fail.

22. **Obligation actions beyond complete** (gap, med)
    - Scenarios: "Change the due date on this obligation", "reassign it to Ana", "snooze or waive it", "mark it not applicable", "give me the obligation summary or overdue count".
    - Evidence: obligations/routes.py has `PATCH /{id}`, `/summary` and `/run-reminders`. REG has only `list_obligations`, `extract_obligations` and `complete_obligation`.
    - `complete_obligation` creates no follow-up and does not report the next scheduled instance, although the code path mentions "complete_and_schedule_next".

23. **Renewals: list is thin and the other actions are missing** (gap, med)
    - Scenarios: "Which renewals are due this quarter with auto-renew and value?", "what does Aegis recommend for contract X?", "confirm the renewal dates".
    - Evidence: RT `_list_renewals` (l.799) returns only id, contract_id (no title or counterparty), decision and `window_starts`. There is no end date, notice deadline, value or status, and no filter by window or decision.
    - Routes `/renewals/{id}/recommendation` and `/confirm-dates` have no tool. Making renewal decisions is already known.

24. **Teams, org structure and admin lookups** (gap, low-med)
    - Scenarios: "Which team handles IP questions?", "who is in the Procurement team?", "who is on the legal team?", "list the users".
    - Evidence: intake teams (intake/routes.py l.485-500), `(app)/org-structure`, `(app)/screen-access`, `(app)/ai-usage`, and `(app)/admin`. The REG comment says these are deliberately admin-only (l.~270).
    - Read-only team and user lookups are still needed for `reassign_request`, `send_for_negotiation.team_id` and `create_intake_request`. The model has no way to resolve a team or a person.

25. **No contract-level tool to start a workflow on an uploaded or standalone contract** (gap, low-med)
    - Scenario: "Run governance on this contract I just uploaded."
    - Evidence: `POST /workflows/start-for-contract` (workflows/routes.py l.180) files a request and starts the flow. The tools require an intake `request_id`. The known "chat-drafted contracts get no workflow" finding covers drafts but not uploads.
    - Also the `GET /workflows/lifecycle` view and `GET /workflows/runs/by-contract/{id}` ("where is this contract in its workflow?") have no tool. `get_intake_request` shows a run only when you start from a request ref.

**NOT COVERED:** tabular review, playbooks, prompts, templates, brain, trademarks, grants and walls, and analytics pages were not compared line-by-line. The only gap noticed in passing is that `list_playbooks`/`run_playbook_review` exist while playbook create or edit does not.

---

## Part C — Contract tools (subagent report, verbatim)

1. **add_contract_comment** (tool_runtime.py:706, schema tool_registry.py:287). Takes only a raw `contract_id`, with no `contract_handle`. The model only ever sees handles like contract-0, because controller.py:1109 swaps every `contract_id` in results for a handle. Scenario: the user says "comment on this contract: ..." and the model passes "contract-0" as `contract_id`, which returns 404 "Contract not found". The same gap affects send_for_negotiation (:714, via `_resolve_contract_id` :680), get_signature_status (:841) and get_signature_link (:697). Their inputs are `request_id`/`contract_id` only. Why: those inputs bypass `_resolve_contract`, the only place that maps handles to ids. Severity: high. Handle input is not accepted on these paths (confirmed); the model passing a handle there is inferred.

2. **send_for_signature** (tool_runtime.py:2396). The /signatures/requests route (signatures/routes.py:~85) calls `enforce_authority(... "contract:sign" ...)`, the delegated-signing-authority ABAC gate. The tool never calls it (no `enforce_authority` anywhere in tool_runtime.py). Scenario: a user with no signing authority for a high-value contract sends it to DocuSign through chat. Why: the tool copies the route's checks but omits the authority gate. Severity: high.

3. **send_for_signature** (tool_runtime.py:2396-2500). The route wraps persistence in try/except and voids the live DocuSign envelope if the local commit fails (routes.py:~163). The tool does neither. It also sends the recipient emails (:2466-2480) inside the loop before the DB commit. Scenario: a later flush or commit failure leaves a live envelope and sent emails with no SignatureRequest row, and the user is told it failed. Why: the tool omits the route's compensation logic. Severity: med.

4. **send_for_signature result** (tool_runtime.py:2490-2497). It returns only `status`, `signature_request_id`, `recipient_count` and `provider_status`. It does not say which version was sent, nor that emails went out. It also swallows email failures (logged only), so the model reports "sent to all signers" when notification emails failed. Why: the result has no per-recipient notification outcome. Severity: low-med.

5. **get_signature_link** (tool_runtime.py:697-704). It never looks up a SignatureRequest. It returns `/contracts/{id}` plus the note "review and sign the contract in the app" for any readable contract. It does not check that a request exists, that the status is still pending, or that the caller is a signer. Scenario: the user asks "give me the signing link" on a draft or already-executed contract and is told to click and sign. Signing actually happens in DocuSign by email. Why: the tool is a static deep link with a misleading claim. Severity: med.

6. **get_signature_status** (tool_runtime.py:841-853). It returns only the latest request's `status` and each recipient's `name` and `status`. Missing: sent_at, signed or declined timestamps, signer email, routing order, which version was sent, and any prior requests. There is no sync with the provider, unlike the route's /sync. Scenario: "who still needs to sign, and since when?" cannot be answered. Recipients are also unordered. Why: the result is too thin. Severity: low-med.

7. **send_for_negotiation** (tool_runtime.py:714-754).
   (a) The counterparty branch creates a ContractShare with no `expires_at`, so the link never expires. The UI route (contract_files/routes.py:1468-1513) accepts expiry, passcode and download flags and writes a `contract.share_created` audit log. The tool writes no audit row and no timeline event.
   (b) The internal branch notifies every active team member with the contract title without checking they can open the contract. An ethical-wall user gets the title via the notification (inferred). It also returns `sent: internal, notified: 0` for an empty team, with no warning.
   Why: the tool is a thin copy of the share route, missing its guardrails and audit. Severity: high for a perpetual, unaudited external link; med for the notification leak.

8. **edit_contract** (tool_runtime.py:1778-1796). It wraps the whole AI skill call in `except Exception` and only logs. A provider error, rate limit or timeout then falls through to the 422 "model could not locate specific language / document may be a placeholder / too broad". Scenario: a transient AI outage makes the model tell the user their instruction was too broad and to re-draft the contract. Why: the real failure cause is hidden and replaced with a misleading one. Severity: med. The same applies to generate_contract_docx (:1568-1590), whose error says "request too broad" when the skill failed.

9. **edit_contract** (tool_runtime.py:~1856-1880 and the return at ~1925). It creates a ContractEdit row with status "proposed" for every anchored suggestion, including ones with `applied=False` (unlocatable or overlapping). It then reports `edits: len(edit_rows)`, which counts those too. Scenario: the model says "I proposed 6 edits" when only 3 are in the redline. The other 3 show up in list_contract_suggestions as open items that are not in the proposed version. Why: the unplaced and overlapping edits are not separated from the applied ones in the count or the rows. Severity: med.

10. **edit_contract (:1732) and redraft_contract (:1944).** Neither checks `lifecycle_stage` or in-flight approval or signature.
    - A tracked-change version can be proposed on a contract that is in SIGNATURE or executed.
    - redraft_contract promotes the new version to authoritative (`promote_version`), replacing the text that was approved or sent. Its summary does not say so.
    - Redraft also leaves existing proposed ContractEdits dangling against the old version without telling the user.
    - Neither tool is idempotent against stacking: repeating edit_contract adds duplicate proposals for the same clause.
    Scenario: "tighten the liability cap" on a contract already out for signature silently makes a new authoritative version, and send-for-signature's approved-version check (signatures/service.py:68) then blocks it unexpectedly. Why: no stage or approval guard on mutating the authoritative text. Severity: high for redraft, med for edit. UI parity is inferred; the missing guard is confirmed.

11. **redraft_contract** (tool_runtime.py:2012-2034). It inlines the entire existing contract text into the brief with no size cap, and returns only `sections` and `assumptions`. It does not return the new text or a diff, and never mentions that the old version was superseded or that approvals reset. Scenario: the user cannot tell what was kept or dropped (the model must call compare_contract_versions itself). Why: the result is too thin and the input is unbounded (an oversized contract fails inside the skill). Severity: low-med.

12. **list_renewals** (tool_runtime.py:799-818). The route (renewals/routes.py:60-80) hides dateless "empty shell" events and returns the contract title, `notice_date` and `expiration_date`. The tool returns only `id`, `contract_id`, `decision` and `window_starts`. It has no title, no `notice_date`, no `expiration_date`, no auto-renew flag, and no dateless filter, and it is ordered by `renewal_window_starts_at`. Scenario: "what renewals are coming up and when does notice close?" gets rows with a decision and a null window start, which the model cannot interpret. Why: the result lacks the fields the UI shows and includes the junk rows the UI filters out. Severity: high.

13. **my_attention_items** (tool_runtime.py:1121-1128). The renewal rule `today <= target <= window_end` drops a missed notice date that is still undecided. Obligations show as "overdue", but an undecided renewal whose notice date has passed disappears. Scenario: the user misses a notice deadline and the worklist says nothing needs them. Why: there is an overdue branch for obligations but not for renewals. Severity: high.

14. **my_attention_items** (tool_runtime.py:~1190). It returns `"count": len(items), "total": len(items)` and then `items[:40]`. The silent cut at 40 is reported as complete. The "Awaiting signature" rows are every in-flight signature in the org's accessible contracts, not just the user's. The query also loads all accessible contracts and every renewal and approval row in Python. Scenario: with 70 items the model says "you have 70 things" and lists 40, or says "all of these". Why: `count` and `total` are computed after truncation is ignored. Severity: med.

15. **list_obligations** (tool_runtime.py:1308-1356).
    - `due_within_days` is `due_date >= today`, so overdue obligations are excluded from "due in the next 30 days". The note tells the model the list is windowed, but it never lists the overdue ones.
    - The row list is unbounded (no cap or total), so a big book floods the model context.
    - Descriptions are cut at 160 characters with no marker.
    Why: the window ignores overdue items and the list has no limit. Severity: med.

16. **complete_obligation** (tool_runtime.py:783-797). The service (obligations/service.py:814-826) can schedule a successor for a recurring obligation (`complete_and_schedule_next`). The tool returns only `completed`, `obligation_id` and `status`. It omits the next obligation's id and date, and the description and contract title. The tool also has no check for an already-completed obligation, and `list_obligations` truncates descriptions, so the model completes by id with a weak confirmation. Scenario: the user says "mark the quarterly report done" and is never told that next quarter's was created. Why: the result is stripped of the service's side effect. Severity: low-med.

17. **find_in_contract** (tool_runtime.py:1003-1024). It does a plain case-insensitive substring search and stops silently at 10 matches. There is no `total` and no `has_more`, and no whitespace or line-break tolerance (extracted text often has hard breaks). It also returns no clause or page reference. Scenario: "does it mention indemnification?" misses a hit split across a line break and reports no matches, or says "10 occurrences" when there are 40. Why: no match count, no truncation flag and no normalization. Severity: med.

18. **read_contract** (tool_runtime.py:971-1000). The description promises "full text and metadata", but the result has no counterparty, value, dates, type or version number. A contract with no text snapshot (scanned or unextracted) returns `text: ""` with `total_chars: 0` and no explanation. Scenario: the model summarises "an empty contract". Why: the empty-text case is indistinguishable from a blank document, and the metadata is not returned. Severity: med.

19. **list_contract_suggestions** (tool_runtime.py:909-948). It is capped at `.limit(25)` with no offset, and `original` and `replacement` are cut to 300 characters. `shown` and `open_suggestions` are returned, but there is no hint that the list is cut and no way to page. A long clause cannot be quoted in full. Scenario: 40 open playbook suggestions, and the model can see and accept or reject only the first 25 and only the first 300 characters of each. Why: the cap and truncation have no continuation. Severity: low-med.

20. **create_tabular_review** (tool_runtime.py:2544-2613) versus the service (tabular_review/service.py:255-310). The tool skips `_enforce_review_size`, the caps of 100 contracts, 50 columns and 1000 cells. It is registered with `confirmation_policy="none"`, so 100+ contracts times many columns can be queued with no confirmation and no cap, a cost and queue-flood risk. It dispatches before commit (the service commits first), so the same worker-race hazard noted in `_extract_obligations` applies (inferred). It returns no review URL. Why: the service's guards were not applied to the tool path. Severity: med-high.

21. **read_table_cells** (tool_runtime.py:2615-2653). It returns `citation_count` only, with no citation text, and no review status or pending/done counts. Cells are not joined to a contract title. Cell `status` values are raw, and the model gets back only a handle. Scenario: "what does it say for Acme's governing law?" with no citation to quote, and the model cannot tell whether the table is still filling. Why: the result is too thin to answer or to flag incompleteness. Severity: low-med.

22. **get_contract_status** (tool_runtime.py:1359-1415). It returns `counterparty_name`, `risk_band` and the workflow step, but not owner, value, expiry or version number. It shows no pending approvals and no approver (only the workflow step's assignee). Scenario: "who is holding this up?" cannot be answered. Why: the status result is thin. Severity: low.

23. **replicate_contract_version** (tool_runtime.py:2146-2224). It creates a new version on every call, so repeats add duplicates. It is `DRAFT_OR_PROPOSE` with no confirmation, uses `ASSISTANT_GENERATED` as the source, and the summary never says it is not authoritative. It gives no version number in the result (`contract_version_id` is stripped as an internal key, so the model gets only `source_version_id` and `contract_version_id`, which are also internal keys). The model cannot tell the user "created version 5". Why: INTERNAL_RESULT_KEYS strips these ids and no `version_number` is returned. Severity: low.

Write tools (edit_contract, redraft_contract, replicate_contract_version, add_contract_comment, send_for_negotiation, send_for_signature, run_playbook_review with redline) use `_resolve_contract`, which is `get_contract_for_user` without `edit=True`. extract_obligations and complete_obligation do use `edit=True`. This is a read-only-grant bypass of the same gate those two tools apply. The routes also omit it, so UI parity is not confirmed; flagged as inferred, severity med.

---

## Part D — Chat runtime and UI (subagent report, verbatim)

Read-only audit of Ask Aegis (cross-cutting runtime and UI). Nothing was run. Everything is read from code, so anything marked "inferred" is not confirmed at runtime. Line numbers are approximate.

1. **Parallel tool calls break the confirmation resume (high)**
   - Scenario: the model emits two or more tool_use blocks in one turn and one of them needs confirmation, e.g. `find_contracts` then `add_contract_comment`, or a mutating call followed by a second call.
   - What goes wrong: the loop appends the assistant message with ALL tool_use blocks, runs the blocks in order, and `return`s at the first confirmation. Results already computed for earlier blocks are dropped, and later blocks are never executed or answered.
   - On resume, `provider_state["messages"]` is replayed with a tool_result for only the pending block. The provider then sees tool_use ids with no tool_result and rejects the request, or the model never learns about the other calls. The user sees "couldn't finish" after approving.
   - Evidence: controller.py:472-529 (stream), 787-835 (resume) and 709-730 (single tool_result appended on resume).

2. **Approve is two separate calls, so a dropped connection strands an approved action (high)**
   - Scenario: the user clicks Approve, `POST /confirmations/{id}/confirm` succeeds, then the tab closes or the network drops before `POST /runs/{id}/resume`.
   - What goes wrong: the confirmation is CONFIRMED but the run stays `waiting_confirmation`. `pending_confirmation_for_session` only looks at PENDING confirmations, so no card comes back. `sweep_confirmations` only closes runs whose confirmation was rejected or expired. The user sees a question with no answer, and the approved action never runs.
   - The same applies to an org admin approving someone else's card: `_get_pending_confirmation` allows it, but `resume_run` requires session ownership.
   - Evidence: routes.py:421-440 (confirm), 296-312 (resume), confirmations.py:247-266 and 234-242; assistant-workspace.tsx resolveConfirmation (~960-985).

3. **Hitting the tool-iteration limit is a generic failure that loses the work (high)**
   - Scenario: the prompt says "Read an entire contract… continue from next_offset until the end" and "resolve with find_contracts first". Each `read_contract` page costs one iteration, and the limit is 8.
   - What goes wrong: a long contract or a multi-step task hits `RuntimeError("Assistant tool loop exceeded maximum iterations")`. The user gets "Aegis couldn't finish this answer. Please try again (reference …)". Retrying hits the same wall.
   - Any partial text is saved with `interrupted=True`, and the user is not told it was a step limit. The model is never told it is on its last iteration, so it cannot wrap up.
   - Evidence: config.py:134, controller.py:421 and 607, runner.py:47-58, prompt_versions.py:2177.

4. **Rejecting never reaches the model, and the reason is discarded (high)**
   - Scenario: the user rejects an edit because "the date is wrong, use 1 March".
   - What goes wrong: the UI hardcodes the reason as "Rejected by user", with no field to type one. The server closes the run with the canned text "Cancelled — you rejected "<tool name>", so nothing was changed." No model turn happens, so the model gets no tool_result and no reason.
   - The user must retype everything. The tool name shows as a snake_case string with underscores swapped for spaces.
   - Evidence: assistant-workspace.tsx:982, contract-chat-controller.ts (`reject(..., "Rejected by user")`), confirmations.py:175-203, routes.py:380-400.

5. **Confirmation card shows the model's raw arguments, with no edit and no expiry (high)**
   - Scenario: the card for any tool without a custom `card()` (everything except stage moves).
   - What goes wrong:
     - `confirmation_details` keeps only scalar top-level args. Nested or list args (edit lists, steps, recipients, playbook payloads) are silently dropped.
     - It keeps at most 8 keys, and each value is cut at 300 characters.
     - Keys are raw field names run through `titleCase`/`prettyKey`.
     - Values are the model's own inputs: handles such as "contract-0", request ids, approval ids, ISO dates. The user is shown "Contract Handle: contract-0" or a UUID, not a title or counterparty.
     - There is no way to edit a value before approving.
     - The card does not show the 30-minute expiry. An expired approval produces a toast plus a canned "expired" message.
   - Evidence: confirmations.py:153-172, controller.py:1811-1816, assistant-workspace.tsx:1336-1345, _chat-tab.tsx card block, tool_runtime.py:221-244 (a custom card exists only for stage moves).

6. **Prompt tells the model to act on guesses, and the card is the only check (high)**
   - Scenario: "Move it to approval", "email the other side", "add a comment on the cap".
   - What goes wrong: the prompt says "Prefer acting over asking" and "call the tool straight away… that card is the only question". The AMBIGUITY section says "Proceed on the most reasonable interpretation when… reversible". GROUNDING says "Never guess". These conflict.
   - The model fills in entity, contract and date args from its own inference. The card then shows raw args (finding 5) and is unreadable for sign-off.
   - There is no rule to confirm the entity, counterparty or dates before calling a mutating tool. There is no rule to state which contract a mutation applies to.
   - Evidence: prompt_versions.py:2173-2186, 2201-2203, 2214.

7. **Identical mutating calls within 10 minutes replay the redacted first result (high)**
   - Scenario: the user re-asks "add the same comment" or "send for signature again", or the model repeats a call.
   - What goes wrong: any non-read-only, non-confirmation call with the same args in the same session within 10 minutes returns `prior.result`.
     - That stored result was passed through `redact_ai_payload`: emails become `<redacted-email>`, 16+ digit runs are masked, strings over 500 chars are truncated, and `text`/`content` become `<redacted text length=N>`.
     - The model then states success, or quotes redacted placeholders, for an action that did not run this time. No "this was a repeat" signal reaches the model or the user.
   - Evidence: tool_runtime.py:197-205, 259, 274-322, redaction.py:6, 53-64.

8. **Sources are the retrieval dump, not the model's quotes, and validity is never shown (high)**
   - Scenario: the model blockquotes a clause with "verbatim" wording.
   - What goes wrong:
     - Citations are assembled from tool-result excerpts, so every excerpt from every tool call attaches to the final message, whether or not it was cited.
     - The text-snapshot citation is the first 1200 characters of whatever text the tool returned, with `start_char` hardcoded to 0.
     - The model's own blockquotes are never checked against the contract text. A tidied quote passes.
     - Citation `validation_status` and `similarity_score` are computed and stored, but the frontend never reads them (no hits in assistant-workspace.tsx, _chat-tab.tsx or ask-aegis-popup.tsx). "invalid" and "unverified" chips look identical to valid ones.
     - Chips are un-numbered against the answer text, and repeated `read_contract` pages produce duplicate chips.
   - Validation is against the current authoritative version's text. Older-version reads (compare) will always score "invalid" (inferred).
   - Evidence: runner.py:61-98, 331-332; service.py:101-160; assistant-workspace.tsx:2253-2300.

9. **The contract-page chat tab shows no sources and no artifact links (med-high)**
   - Scenario: asking about a clause in the Assistant tab on the contract page.
   - What goes wrong: `Reply` renders only blocks and markdown. There is no CitationList, no "show in document", and no request or open link, although the prompt promises quotations "with the contract and clause named". The user cannot check any claim there.
   - Evidence: _chat-tab.tsx:52-65.

10. **The stream/stored text loses the break between pre-tool and post-tool text (med)**
    - Scenario: "Let me check." then a tool call, then "Here is…".
    - What goes wrong: `answer_parts` from all loop iterations are joined with "". The stored `content` reads "Let me check.Here is…". The garbled text is replayed to the model as conversation history on the next turn.
    - The workspace renders the interleaved blocks, so only the stored content and the history are garbled. The popup and `m.content` paths show it directly.
    - Evidence: controller.py:446-448, service.py:196 (`"".join(answer_parts)`), controller.py:298-304 (history uses `content`).

11. **A turn with tools but no final text saves no message (med)**
    - Scenario: the model ends after a tool with no text, or the run is cancelled or fails before any text.
    - What goes wrong: `persist_assistant_answer` does `if not answer: return`. No assistant message exists, so the tool calls are not linked (`message_id` stays NULL) and the persisted tool trace is lost. After reload the question has no answer and no steps. `has_answer` is false.
    - A "succeeded" run therefore looks like it produced nothing.
    - Evidence: service.py:195-197, runner.py:357-362, assistant-workspace.tsx:~280-300.

12. **Pre-confirmation text and tool steps vanish from the saved turn (med)**
    - Scenario: the model explains, then calls a confirmation-gated tool.
    - What goes wrong: on `waiting_for_confirmation` the runner returns without persisting. The resume task starts a fresh `answer_parts` and `blocks`, so the saved message holds only post-approval text and steps. The preface and earlier steps are gone after reload.
    - This is related to, but distinct from, the known empty-message issue.
    - Evidence: runner.py:351-356 and 290-296, controller.py:731-786.

13. **The message list returns the OLDEST 100 messages, so long chats stop updating (high for long chats)**
    - Scenario: a long-running chat, including the single permanent per-contract chat.
    - What goes wrong: `list_session_messages` is `ORDER BY created_at ASC LIMIT min(limit,200)`, and the UI asks for 100. After about 50 turns the view shows the first 100 messages and never the newest ones.
    - `syncFromServer` and the poll watchdog both rely on this list and look for a fresh assistant message, so the "Thinking…" state and the new answer can never appear. The new answer is stored but invisible.
    - The contract tab has no "New chat" button, so it hits this and the history window for good.
    - Evidence: service.py:338-346, endpoints.ts:843, contract-chat-controller.ts load/sync, assistant-workspace.tsx:~325-360.

14. **Conversation memory is short and silently truncated (med)**
    - Scenario: a long conversation, or one with long answers.
    - What goes wrong: history is the last 20 merged messages and 24,000 characters, and tool results are not replayed at all.
    - A single large assistant answer can push everything earlier out, with no notice to the model or the user that history was cut. The model is not told that earlier turns were dropped.
    - The over-fetch is 4x20 rows, and `max_chars` drops older turns whole.
    - Evidence: controller.py:256-326.

15. **No size cap on tool results, and no pruning across iterations (med)**
    - Scenario: `read_contract` pages, `ask_contract_brain`, or `query_contracts` results.
    - What goes wrong: `_json_tool_result` is an unbounded `json.dumps`, and `messages` accumulates every result for up to 8 iterations. There is no context-length guard and no truncation notice.
    - Large `tool_result`s are also copied into `run.provider_state` and `confirmation.provider_state` in the DB.
    - Evidence: controller.py:1131-1134, 472-605, 502-508, tool_runtime.py:226-232.

16. **Tool errors give the model little to correct itself with (med)**
    - Scenario: a model-supplied arg fails validation, or a tool throws.
    - What goes wrong: a pydantic `ValidationError` becomes "Tool input validation failed" with no field names. Any non-HTTPException becomes "Tool execution failed", and a non-string `detail` also becomes "Tool execution failed".
    - The model cannot fix the call, so it retries blindly (burning iterations) or invents an explanation. The user sees the same generic text on the red step.
    - Evidence: controller.py:97-102, 575-604.

17. **A failure in the confirmed action on resume is unhandled (med)**
    - Scenario: after approval, `execute_confirmed` throws.
    - What goes wrong: the call at controller.py:685 is outside the try. The run ends FAILED with a generic reference message. The tool row never gets a `tool_finished` and stays "running" in the live UI. The confirmation is already consumed, so the user cannot retry that card. The model is never told.
    - Evidence: controller.py:681-707, runner.py:364-377.

18. **Switching or adding contracts mid-session is inconsistent (med, partly inferred)**
    - Scenario: the user switches the document in the workspace, or continues a session that was created for contract A.
    - What goes wrong:
      - `runner.py` uses `session.contract_id or contract_ids[0]` as the primary contract. A session created on A keeps A as the "primary_contract_handle" even when the UI sends B (inferred).
      - Every handle ever added to the session (including tool-discovered contracts) gets its metadata and a 6000-character excerpt injected into every later turn, so prompt size and DB queries grow with each contract touched.
      - The workspace sends a single `contract_id` even when its copy says "attached document(s)" or "each document".
    - Evidence: runner.py:266-279, controller.py:352, 1029-1091, assistant-workspace.tsx:945, 2618-2627.

19. **Fixed "grounded" badge (med)**
    - Scenario: any answer in the workspace.
    - What goes wrong: a permanently green "grounded" indicator sits in the composer, not tied to whether any tool or source backed the answer. The text is always shown regardless of status.
    - Evidence: assistant-workspace.tsx:~1454-1458.

20. **The popup is a different engine with no memory (med)**
    - Scenario: a follow-up such as "and the second one?" in the Ask Aegis popup.
    - What goes wrong:
      - It calls `brainApi.ask({question…})` with no history, so every question is stateless and the model never sees earlier popup messages.
      - It shows only "N sources" as a count, with no way to open them, yet the empty state says "Answers cite their sources".
      - Scope is read from the URL, so the contract pin can change while old messages stay.
    - Accessibility: the dialog has no `aria-modal`, no focus trap and no focus return, and the entire log is `aria-live` (long answers re-announce).
    - Evidence: ask-aegis-popup.tsx:23-40, 64-103, 127.

21. **Watchdog and poll can re-enable input while the run is still going (med)**
    - Scenario: a long run, which can legitimately last up to the 900s Celery limit.
    - What goes wrong: both the workspace poll and the contract-chat watchdog stop after 300s and set `streaming=false`. The composer re-enables while the run is still RUNNING. A send returns 409 "still answering".
    - The workspace poll also replaces items with server messages whenever any assistant message newer than `startedAt-2s` appears.
    - Evidence: assistant-workspace.tsx:~326-340, contract-chat-controller.ts:44-45 and startWatchdog.

22. **The card disappears if the confirm call fails transiently (med)**
    - Scenario: a network error or 5xx on `/confirm`.
    - What goes wrong: `setPending(null)` runs before the call. On failure the catch only syncs messages, not the session state, so the card is not restored while the confirmation is still PENDING on the server. The user must reload to approve.
    - The contract tab's `resolve` has the same ordering.
    - Evidence: assistant-workspace.tsx:962-995, contract-chat-controller.ts resolve.

23. **Full tool results are streamed to the browser (low-med)**
    - Scenario: `read_contract`, `ask_contract_brain`, `query_contracts`.
    - What goes wrong: `tool_finished` publishes the entire unredacted result, including contract text, to Redis and to the browser, and it is stored in React state. Event replay holds up to 20k entries for an hour.
    - The payload is heavy on mobile and on slow connections. It is also a data-exposure surface wider than what the UI needs (inferred).
    - Evidence: runner.py:331-337, controller.py:556-559.

24. **Missing prompt rules: dates, locale, currency, IDs, assumptions (med)**
    - Scenario: any answer involving dates, amounts or locales.
    - What goes wrong:
      - The prompt has no rules for date format, relative-date resolution ("next quarter"), timezone, or currency and number formatting (lakh/crore versus millions). Contract metadata is injected as raw ISO dates and raw numeric `value_amount` with no formatting hint.
      - It has no instruction to confirm entity or dates before a mutating call (see 6), and it does not tell the model to show the request ref or record it acted on.
      - REGISTER says "Do not expose internal IDs" while REFERENCES says to cite "REQ-4196". It never says whether handles like "contract-0" can be mentioned to the user.
      - The only locale rule is in the legal-question session context ("answer under Indian law"). For all other sessions there is no default jurisdiction or currency.
    - Evidence: prompt_versions.py:2241-2243, 2255-2256, controller.py:1789-1801 and 1060-1080.

25. **Usage and cost are invisible to the user (low-med)**
    - Scenario: a user running long agentic turns.
    - What goes wrong: token usage is recorded server-side (`_record_usage`), but nothing in the UI shows tokens, cost or remaining budget. A budget refusal appears only as a generic error string or toast.
    - Evidence: controller.py:451-460, 1694; no usage references in assistant-workspace.tsx or _chat-tab.tsx.

26. **No handling of `max_tokens` truncation (low-med, inferred)**
    - Scenario: a long drafting answer with `max_tokens` 8000.
    - What goes wrong: no `stop_reason` check exists anywhere in controller.py. A mid-sentence cut-off is saved as a normal "succeeded" answer with no "truncated" notice or continue option. The registry comment even refers to a cut-off answer reading as "incomplete".
    - Evidence: registry.py:197-198, absence of stop_reason in controller.py.

27. **The Stop path and approval wording have gaps (low)**
    - The pending composer is disabled with "Confirm or cancel the action above first", so the user cannot clarify or modify the request without rejecting. This is only a card-and-reject path (the card has no edit and no ask-a-question control).
    - The card has no `role="alertdialog"` or `aria-live` in the workspace, and focus is not moved to it. The contract tab uses `role="group"` but no live region. A keyboard or screen-reader user may not notice an action is waiting.
    - The popup and the workspace composers lack a visible character limit for the 8000-character cap (`max_length=8000`), so long pastes fail with a server error.
    - Evidence: _chat-tab.tsx:150-156 and 197, assistant-workspace.tsx:1300-1350, routes.py:70.

**Not found:**
- No file or attachment upload in the chat. Only a contract picker and a separate import modal, so findings about uploads do not apply.
- No PII redaction is applied to model answers. Redaction only affects stored logs (and replayed results, see 7).
