# Feature Specification: Request page, "Decision first" layout

Feature ID: 010-request-page-decision-first
Created: 2026-10-23
Status: APPROVED

## Summary

The request page (the detail page of one intake request) is hard to read. The same
progress, risk, owner, requester and counterparty facts are shown several times, the
action an approver must take sits two screens below the top, internal wording and raw
data leak onto the page, and some text is fixed copy that never reflects the request's
real state. This feature rearranges the existing page into the approved "A · Decision
first" layout (mockup: `mockup.html`, screen A): a header with one slim progress strip,
a main column that leads with the decision the viewer must make, and a slim side column
for reference and housekeeping. It is a rearrangement and de-duplication only. It adds
no new capability, no new data, and changes nobody's permissions.

## User stories

- As an approver, I want my approval at the very top of the page, so that I can decide
  without scrolling or hunting.
- As a request owner, I want one clear place for status, risk and next step, so that I
  can tell at a glance what is happening and what is needed from me.
- As a requester, I want to see what I asked for and where it stands in plain words,
  without internal jargon, so that I do not have to ask legal what the page means.
- As a staff member without manage rights, I want the same readable page, showing only
  what I can see today, so that nothing new is exposed to me.
- As any user, I want sections with nothing in them to take one line, so that the page
  shows only what matters for this request.
- As any user on a narrow screen, I want the page to stay usable, so that I can act from
  a small window.

## Functional requirements

### Structure

- FR-1: The page SHALL be organised as: a header; one slim progress strip; a main column
  and a side column. Order in the main column, top to bottom: the viewer's decision
  area (when they have something to decide), "What was asked", AI risk review, comments
  and questions, activity. When the viewer has no pending approval at all, "What was
  asked" is first. Side column, top to bottom: counterparty relationship, owner
  and people, documents, steps, Ask Aegis.
- FR-2: The header SHALL carry a one-line summary: the request title, reference, who
  raised it and when, the owner, and status pills for conditions that apply (for example
  high risk, overdue). A pill SHALL be shown only when its condition is true. The header
  shows only this summary; the full details of owner, requester and counterparty live in
  the side cards (FR-17).
- FR-3: The page SHALL show progress through the seven lifecycle stages (Intake,
  Drafting, Review, Approval, Signature, Active, Closed) in exactly one slim strip,
  marking completed, current and upcoming stages, and the current step's waiting state
  (who it waits on, overdue) in words beside or under the strip.
- FR-4: When the request has a pending approval, the approval card SHALL be the first
  item in the main column for every viewer, exactly as feature 009 provides: with its
  buttons when the viewer has an open decision, and in its read-only form (the facts and
  "waiting on ...", no buttons) when the viewer cannot decide. For a person who must
  complete a human review step, the decision area SHALL also contain the existing
  decision controls for that step (approve and advance, the required reply to a
  rejection, add a comment, send back for rework) together with the risk warning that
  those controls show today.
- FR-5: WHEN the request has NO pending approval at all (and the viewer has no human
  step to complete), the decision area SHALL NOT take space (no empty card) and "What
  was asked" is the first item in the main column. A pending approval the viewer cannot
  act on is not "nothing to show"; it is shown read-only per FR-4.

### De-duplication

- FR-6: Each fact SHALL appear once on the page: progress (FR-3 strip plus the step list
  in the side column only), overall risk (one place, FR-12), and the full details of
  owner, requester and counterparty (each once, in the side cards named in FR-1 and
  FR-17). Exactly two overlaps are permitted, and each SHALL come from the same data so
  the copies can never disagree: (a) the header's one-line summary (FR-2: title,
  reference, raised-by and when, owner, status pills); (b) the approval card's repeated
  value, term and overall risk as decision inputs. Nothing else may repeat.
- FR-7: The page SHALL NOT show raw structured data (for example braces/JSON such as
  review "drivers"), internal step identifiers or internal step-type names, or filler
  text added by the system. Steps SHALL be named in plain words a requester understands.
  Text the requester typed (title, description, form answers) is the requester's own
  wording and SHALL be shown as written, never removed or replaced.
- FR-8: Text that is fixed copy and not about this request's real state SHALL be
  replaced with real data or hidden when there is no data. Text the system adds, such as
  descriptive filler (for example a standing sentence explaining how the workflow engine
  works) or an automatically appended suffix on a title (for example "— form by Local
  Admin"), SHALL NOT be shown. A title the requester typed stays as the page title.
- FR-9: A section with nothing to show SHALL collapse to a single line (for example
  "No comments yet", "No documents attached", "No open issues") or be omitted where
  an absence is not worth saying. No empty bordered card with only a placeholder.

### Content of each area

- FR-10: "What was asked" SHALL show the request description and the request's facts
  (counterparty, type, value, term, priority, and for form requests the answers given
  on the form including the deliverables/answers editor that exists today), labelled as
  from the form or as understood by the AI as today, plus the AI read of the request
  (what does not match the form, what goes beyond the template, what is likely to be
  negotiated) and the AI extraction confidence where shown today.
- FR-11: The notices that exist today for "needs information before drafting" (listing
  the missing items) and "AI triage unavailable" (with its reason) SHALL remain, shown
  inside "What was asked" and only when they apply.
- FR-12: AI risk review SHALL be one section showing the overall risk band, the count of
  findings by severity and how many remain to decide, and a readable list of issues,
  each with severity, clause, the issue in plain language and its suggested fix when
  one exists today. Issues accepted as-is that still count SHALL remain listed. Issues
  SHALL be grouped by severity and each group SHALL be collapsible as today. The
  Recompute action SHALL stay in this section for people who may use it today.
- FR-13: Where a workflow review step produced a risk summary, confidence, and drivers,
  that information SHALL be presented in readable form without duplicating FR-12's
  content. The review step's own result (risk, confidence, summary, drivers) SHALL be shown as expandable detail on the "AI review" step in the side column, hidden until the viewer opens it (decided by the user).
- FR-14: "Comments and questions" SHALL list the existing workflow comments (including
  "sent back" and "replied to the rejection" entries and the step they refer to) and
  keep the box to post a comment or question for people who may today.
- FR-15: Activity SHALL list the existing activity trail (request raised, hand-offs,
  comments) in time order, once.
- FR-16: The side column steps list SHALL show each workflow step compactly and
  vertically with its state (done, here now, waiting on whom, skipped, failed), the
  assignee or team, and the stage grouping that exists today, and SHALL hold the
  existing "Send back for rework" control (choose an earlier step, optional reason).
  Before a workflow has started, it SHALL show the steps of the suggested workflow.
- FR-17: The side column SHALL be the one place for the full details of owner,
  requester and counterparty (the header only summarises, FR-2). It SHALL show the counterparty relationship note (counterparty,
  other parties, what is already on file) only when it exists today; owner and people
  (owner, requester, department) with the Reassign owner control for people who may
  use it today; documents with their parsed/attached state and "Open in CLM editor"
  when the request has a contract; and Ask Aegis with its suggested questions, scoped
  as today (this contract, or portfolio when no contract exists).

### Existing functions that SHALL remain reachable

- FR-18: Every action available on the page today SHALL remain reachable, from the page
  in at most one place each, for exactly the people who have it today:
  start the suggested workflow; pick a workflow when none is set up; check approval or
  signature status; check AI review status; retry an interrupted AI review; complete a
  human step (approve and advance, with the open-risk confirmation); reply to a
  rejection; send back for rework; add a comment or question; reassign the owner; apply
  the AI's suggested priority; approve and draft the contract; use the attached file as
  the contract; approve and draft fresh; continue as a trademark; open the draft /
  review and redline / open to negotiate for the current step; the guided negotiation
  panel on a negotiate step; recompute risk; edit the form answers or deliverables
  (while editable today); open in CLM editor; Ask Aegis; back to Legal Intake.
- FR-19: The following read-only information that exists today SHALL remain reachable:
  who the next step waits on, by when, and what follows; the approval chain with each
  approver's status and recorded decisions and comments; the agent hand-off trace; a
  workflow error message; the "workflow just filed, being set up" state; the template
  notice shown after drafting; SLA state; department; priority.
  The approval chain (with each approver's recorded decisions and comments) SHALL be expandable detail on the Approval step in the side column, and the agent hand-off trace SHALL be expandable detail under the steps list; both hidden until opened (decided by the user).
- FR-20: The progress strip SHALL show the current stage and what each stage contains
  in the same words as today. Each of the seven stages SHALL be clickable to reveal that stage's steps, as the lifecycle bar allows today (decided by the user).

### Quality constraints

- FR-21: The page SHALL use the application's existing visual language (its existing
  colours, spacing, type and components) and SHALL display correctly in both light and
  dark mode. No new theme, font or colour set.
- FR-22: Layout SHALL adapt to narrow widths: the two columns SHALL stack into one, with
  the decision area first, and no content or action SHALL be cut off or become
  unreachable; the progress strip SHALL remain legible (scroll or compact form).
- FR-23: Loading, load-failure (with retry and back) and "just filed, workflow being
  set up" states SHALL still be handled as today.
- FR-24: The page SHALL be checked side by side against screen A of the mockup
  (structure, order, grouping, density) for the REQ-7705 case and for at least one
  request in each of: no workflow yet, waiting on a human step, waiting on approval,
  rejected with a reply owed, complete, and a request with no contract.

## Permissions, scoping & audit

Frontend-only rearrangement. All of the following stay exactly as they are today:

- Who may perform each action: unchanged. Actions shown only to people with manage
  rights (reassign, start workflow, pick a workflow, draft/ingest, continue as
  trademark, send back, post a comment, recompute risk, apply priority, retry review,
  check status, complete step) stay limited to them while the request is open. The
  approval card is visible to everyone who sees the request, as feature 009 provides,
  but its buttons are usable only by the person (or team member) whose decision it is;
  for others it is read-only. Requesters and staff without manage rights see the same read-only
  information they see today.
- What data is visible: unchanged. The page SHALL NOT show any datum, field, person,
  count or comment to a viewer that the current page does not already show that
  viewer. Request access, ethical walls and organisation scoping are enforced by the
  existing data sources and are not altered. Moving information must not widen who
  sees it.
- Audit: no new audit events. Every action that records an audit or timeline entry
  today records exactly the same entry after the change; moving a control must not
  skip or duplicate it.

## Acceptance criteria

- AC-1 (FR-1): Given an approver with an open approval on a request, when they open the
  page, then the first content under the header and progress strip is their approval
  card, followed by What was asked, AI risk review, comments and questions, activity;
  and the side column lists counterparty, owner and people, documents, steps, Ask
  Aegis in that order.
- AC-2 (FR-2): Given a request that is high risk and overdue, when the page opens, then
  the header shows the title, reference, raised-by with date, owner, a high risk pill
  and an overdue pill; and given a request that is neither, then neither pill appears.
- AC-3 (FR-3, FR-6): Given any request, when the page opens, then progress through the
  seven stages appears in exactly one strip, and no other "You are here", lifecycle
  bar, "Workflow & stages" or "At a glance" progress block is present.
- AC-4 (FR-4): Given a person who must complete a human review step with a rejection
  reply owed, when the page opens, then the decision area is first and contains the
  rejection reason, a required reply box, approve and advance (disabled until a reply
  is written), send back for rework and add a comment.
- AC-5 (FR-4, FR-5): Given a request with a pending approval and a viewer who cannot
  decide it, when the page opens, then the approval card is first in the main column in
  its read-only form (facts and "waiting on ...", no buttons); and given a request with
  no pending approval at all, then no approval card or empty placeholder is shown and
  "What was asked" is first.
- AC-6 (FR-6): Given REQ-7705, when the page is read in full, then the full details of
  owner, requester and counterparty, and overall risk, are each stated once in their
  own place; the only repeats are the header's one-line summary (title, reference,
  raised-by and when, owner, status pills) and the approval card's value, term and
  overall risk; and those repeated values equal the page's.
- AC-7 (FR-7): Given a request whose review step result contains structured drivers,
  when the page opens, then no braces, quoted key names or internal ids appear, and no
  step is labelled with an internal name such as "Clm Draft".
- AC-8 (FR-7, FR-8): Given a request whose requester typed the title "Today_14_test",
  when the page opens, then that title is shown as the page title, as written; and given
  system-added text such as the suffix "— form by Local Admin", a standing explanatory
  sentence, an internal step name or raw data, then none of it is shown. The description
  the requester wrote appears as written, in "What was asked".
- AC-9 (FR-8): Given a request with no review result, no comments and no documents,
  when the page opens, then no fixed explanatory copy about how the workflow engine
  works is shown and no section is filled with placeholder prose.
- AC-10 (FR-9): Given a request with no comments, no documents and no open issues,
  when the page opens, then each of those is a single line (or omitted), not a full
  card.
- AC-11 (FR-10, FR-11): Given a Statement of Work request with deliverables and an AI
  read with mismatches, when the page opens, then What was asked shows the facts, the
  deliverables (editable where editable today) and the AI read; and given missing
  information or degraded triage, then the corresponding notice appears there and
  nowhere else.
- AC-12 (FR-12): Given a contract with one high and one accepted-as-is issue, when the
  page opens, then one risk section shows the band, "1 high" and how many remain to
  decide, lists the high issue with clause, plain-language issue and suggested fix, and
  lists the accepted issue as still counting; and a person with manage rights sees
  Recompute there, others do not.
- AC-13 (FR-14): Given a request with a "sent back" comment on a step, when the page
  opens, then it is listed in comments and questions marked as sent back with its
  step; and a person who may comment can post a new one, which then appears.
- AC-14 (FR-16): Given a running workflow at step 4, when the page opens, then the side
  column lists all steps vertically with done / here now / upcoming states and
  assignees, and Send back for rework offers only earlier steps, requires a chosen
  step, and sends the workflow back as today.
- AC-15 (FR-16): Given a request with no workflow started and a suggested workflow,
  when the page opens, then the steps list shows the suggested workflow's steps and
  "start it" is available to people who may start it today.
- AC-16 (FR-17): Given a request without a contract, when the page opens, then
  documents shows attached files (or a one-line none) and no "Open in CLM editor";
  given a request with a contract, then "Open in CLM editor" is present.
- AC-17 (FR-17): Given a person with manage rights, when they use Reassign on the owner,
  then the owner changes, the same toast and audit/timeline entries occur as before.
- AC-18 (FR-18): Given each of the request states listed in FR-24, when the page is
  opened as a person with manage rights, then every action in FR-18 that applies to
  that state is present exactly once and works as before.
- AC-19 (FR-13, FR-19): Given the clarified placement (see Open questions), when a
  request has a review step result, an approval chain with decisions, and an agent
  hand-off trace, then each is reachable in the agreed place with no raw data shown.
- AC-20 (permissions, FR-18): Given a requester with no manage rights, when they open
  their own request, then they see no action controls that they do not see today and
  no data they do not see today; and given an approver who is not the owner, then they
  see the approval card and read-only information only; and given a viewer who is not
  the decider of a pending approval, then the approval card is read-only with no buttons.
- AC-21 (FR-21): Given light mode and dark mode, when the page is opened in each, then
  all text, pills, cards and the progress strip are legible, and no white or hardcoded
  colour block appears in dark mode.
- AC-22 (FR-22): Given a viewport of phone width, when the page opens, then there is no
  horizontal page scroll (the progress strip may scroll within itself), columns are
  stacked with the decision area first, and every action remains reachable.
- AC-23 (FR-23): Given a request that cannot be opened, when the page loads, then the
  existing error with retry and back is shown; given a just-filed request, then the
  "being set up" state shows until the workflow appears.
- AC-24 (FR-24, FR-1): Given the finished page, when opened next to screen A of the
  mockup for REQ-7705, then the order, grouping and density match and any difference is
  recorded and accepted by the user.
- AC-25 (Audit): Given any action performed from the new layout, when the audit trail
  is read, then it holds the same entry as when the same action was performed on the
  old layout, once.
- AC-26 (FR-20): Given the finished page, when a viewer clicks a stage in the strip, then that stage's steps are revealed as the lifecycle bar does today.

## Out of scope

- Any new feature, button or backend behaviour. In particular the mockup's "Apply
  suggested wording" and "Nudge the approvers" are NOT included; the suggested wording
  is shown as text only, as today.
- Changing who can see or do anything; new data; new audit events.
- Other pages, the sidebar and navigation, the Legal Intake list and hub.
- The workflow engine, approval rules and routing, risk scoring, AI behaviour, and the
  content of the approval card (feature 009).
- The mockup's layouts B and C.
- Re-theming, new fonts or new colour tokens.
- Changing which requests, comments or documents a viewer can see.

## Edge cases & error behavior

- No workflow yet, workflow being set up, workflow failed or cancelled, workflow
  complete: each has a clear one-line state in the strip and steps list.
- Parallel steps (several running side by side): the strip and steps list show all open
  ones; the viewer's own step is the decision.
- Step skipped by condition: shown as skipped, not counted as done.
- Request closed or approved: no action controls, read-only view as today.
- No contract: no risk section, no CLM editor link, Ask Aegis in portfolio scope.
- Risk not yet reviewed: "not reviewed" stated once, never presented as low or clear.
- Very long titles, counterparty names, comments or file names wrap or truncate
  without breaking layout.
- Data still loading in parts: each area loads independently without layout jumping to
  empty cards.
- A viewer who is both owner and approver sees one decision area, not two.
- Browser focus-driven refresh and live polling continue; a typed comment, reply or
  Ask Aegis draft SHALL NOT be lost by a refresh.

## Decisions (2026-10-23)

- Header vs side cards: the header is a one-line summary (title, ref, raised-by and
  when, owner, status pills); full owner, requester and counterparty details live once
  in the side cards (mockup screen A). The header summary and the approval card's
  value/term/risk are the only allowed repeats.
- Request titles: a title the requester typed stays as the page title (for example
  "Today_14_test"). Only text the system adds (filler, suffixes, standing sentences,
  internal step names, raw data) is removed.
- Read-only approval card: a person who cannot decide still sees the approval card at
  the top of the main column, read-only, as spec 009 provides. The "no space" rule
  applies only when there is no pending approval at all.

## Open questions

- [x] FR-13 resolved: the review step's result is expandable detail on the AI review step.
- [x] FR-19 resolved: approval chain expands on the Approval step; hand-off trace expands under the steps list.
- [x] FR-20 resolved: the stage strip is clickable, as today.

## Context for planning

Non-binding notes for the architect; not requirements. The page is the request overview
in the intake area of the frontend (`_request-overview.tsx`), with the approval card
from spec 009 (`_approval-card.tsx`), the request answers editor, the negotiation
panel and the shared lifecycle view. Today it shows progress five times (header badge,
"You are here", lifecycle bar, "Workflow & stages", "At a glance"), risk four times,
and prints raw review-step result values, internal step names and fixed explanatory
copy. A measured baseline (REQ-7705) is the before state for the side-by-side check.
