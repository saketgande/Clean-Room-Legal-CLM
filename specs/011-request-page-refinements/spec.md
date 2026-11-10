# Feature Specification: Request page refinements (workflow card, real activity, counterparty files)

Feature ID: 011-request-page-refinements
Created: 2026-10-23
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Summary

The shipped "Decision first" request page (feature 010) has three weaknesses. The Steps
card in the side column is about 900px tall, repeats the progress strip and makes the page
unattractive. The Activity section is sometimes missing and, when present, does not say
what actually happened. The Counterparty card says similar files exist but does not name
the counterparty or let anyone open those files. This feature fixes all three, following
the approved mockup (`mockup.html`, second screen, "Option 2 · Workflow moves under the
main content"): the workflow becomes one wide card in the main column; Activity shows the
real, readable history of the request and its contract; Counterparty shows the name and
clickable prior contracts and earlier requests, limited strictly to what the viewer is
allowed to see. It adds no write actions and no audit events.

## Supersedes (feature 010)

This feature supersedes the following parts of `010-request-page-decision-first`. Everything
else in 010 (layout A, decision area and approval card first, one progress strip,
de-duplication, no raw data, collapse-empty-sections, light and dark mode) remains in force.

- 010 FR-1 (the side column order includes "steps"; main column ends with "comments and
  questions, activity") and 010 FR-16 (steps list in the side column): superseded. Steps
  no longer appear in the side column. The new order is in FR-1 below.
- 010 FR-15 (Activity lists the existing trail: request raised, hand-offs, comments):
  superseded by FR-8 to FR-15 below.
- 010 FR-17, counterparty part only (note shown only when it exists today): superseded by
  FR-16 to FR-27 below. Owner and people, documents and Ask Aegis in FR-17 are unchanged.
- 010 FR-13, FR-19 (review result, approval chain, hand-off trace as expandable detail "in
  the side column"): the content and the "hidden until opened" behaviour stay; only the
  place changes to the Workflow card (FR-6).
- 010 FR-18 (every action stays reachable) and the 010 permissions section still apply to
  every step action moved by this feature.

## User stories

- As a request owner, I want the whole workflow visible as one row under the risk review,
  so that I can see where the request is without scrolling a tall list.
- As an approver, I want the current step to stand out with who it waits on, whether it is
  overdue, and a check-status action, so that I know what to chase.
- As a requester, I want Activity to read as a plain history of what happened and who did
  it, so that I do not need to ask legal for an update.
- As an owner, I want AI and system housekeeping hidden unless I ask for it, so that the
  history is not drowned in noise.
- As a reviewer, I want the counterparty's name and its earlier contracts and requests as
  clickable items, so that I can check precedent in one click.
- As any viewer, I want to see only contracts and requests I am allowed to see, in lists
  and in counts, so that the page never reveals something I could not open.
- As a staff member without manage rights, I want the same readable page with only what I
  may see, so that nothing new is exposed to me.

## Functional requirements

### Layout

- FR-1: The page SHALL keep the feature 010 header, progress strip and two-column layout,
  with these orders. Main column, top to bottom: the viewer's decision area (when they
  have something to decide), "What was asked", AI risk review, Workflow, Activity,
  Comments and questions. Side column, top to bottom: Counterparty, Owner and people,
  Documents, Ask Aegis. The Steps card SHALL NOT appear in the side column.
- FR-2: Sections with nothing to show SHALL collapse to one line (or be omitted), as in 010.

### Workflow card

- FR-3: The page SHALL show the workflow as ONE wide card ("Workflow", with the workflow's
  name and "N of M steps done") containing one tile per workflow step in a single row,
  in step order. Before a workflow has started, the tiles SHALL show the suggested
  workflow's steps, as today.
- FR-4: Each tile SHALL show the step's plain-language name and its state. A done step is
  visually tinted and shows a check, and the assignee or outcome where known. The current
  step is highlighted and shows who it waits on, its overdue state in words when overdue,
  and its status action (for example "Check approval status") when the viewer has it
  today. An upcoming step is plain and may show its number of sub-steps or "next". A
  skipped, failed or cancelled step is shown as such and is not counted as done. When
  several steps run side by side, every open one is highlighted.
- FR-5: The Workflow card SHALL carry small links, each shown only when it applies and only
  to the people who have the function today: Review result, Approval chain, Agent
  hand-off trace, Send back for rework. Review result, Approval chain and Agent hand-off
  trace SHALL open as expandable detail inside the card, hidden until opened, with no raw
  data shown (010 FR-7 applies). Send back for rework SHALL keep today's behaviour: an
  earlier step must be chosen, a reason is optional.
- FR-6: Moving the steps SHALL NOT remove or change any step function: every action,
  read-only datum and gate described in 010 FR-18 and FR-19 that belongs to the steps
  remains reachable at most once on the page, for exactly the same people as today.
- FR-7: Narrow widths: the row of tiles SHALL wrap onto further lines or scroll within the
  card; the page itself SHALL NOT scroll horizontally, and no tile or action SHALL be cut
  off or unreachable.

### Activity

- FR-8: Activity SHALL show the real history of the request AND of its contract (when the
  request has one) merged into one list in time order, grouped by day. Newest first: the most recent day is at the top, and within a day the latest event is first (decided by the user; the mockup shows the opposite order and is overridden on this point).. The existing hand-offs and comments SHALL remain in the list.
- FR-9: Each event SHALL be one plain-language sentence with who did it (a person's name, or
  "Aegis"/"AI" for automated work) and the time. Raw event-type codes, identifiers and
  structured data SHALL NOT be shown.
- FR-10: The list SHALL cover at least these kinds of event when they have occurred: request
  filed; AI triage complete; workflow started; contract drafted; stage changed (naming
  the new stage); AI review complete (with the count of issues found, when known); AI
  drafted a suggested change; approval requested, approved or rejected; steps completed
  or sent back; comments and hand-offs.
- FR-11: System-level events (for example an AI skill starting or succeeding, document
  ingestion, background job dispatch, and similar housekeeping) SHALL be hidden by
  default. A "Show system details" control SHALL reveal them in the same list, in time
  order, and hide them again; the control SHALL be shown only when there is at least one
  hidden event. System events shown on request SHALL also be plain-language, never raw
  codes.
- FR-12: WHEN there are genuinely no events to show, Activity SHALL be a single line, not an
  empty card.
- FR-13: Activity SHALL include an event only if the viewer may see the request or contract
  it belongs to. A contract's events SHALL NOT appear for a viewer who may not see that
  contract (ethical wall, clearance, no grant), even while they can see the request.
  Request events SHALL appear only for viewers who may see the request.
- FR-14: Activity SHALL be shown every time the viewer opens the page when events exist: it
  SHALL NOT be intermittently absent. WHEN the events cannot be loaded, the card SHALL say
  so in one line and offer a retry, rather than disappearing.
- FR-15: The Activity list SHALL show a first batch of recent events with a control to show
  earlier ones, so that a long history does not make the page long. The default batch is
  20 events (a default, adjustable in planning).

### Counterparty

- FR-16: The Counterparty card SHALL show the counterparty's name (the primary counterparty
  of the request, matched exactly as today), not only a count.
- FR-17: The card SHALL show one short summary line made only of parts that apply, for
  example "5 other contracts · 2 active · 7 earlier requests". Whether a total value is
  shown is governed by FR-24.
- FR-18: The card SHALL list the counterparty's other contracts as clickable items, each
  showing title, type, stage, value (when it has one) and date, newest first. Clicking an
  item SHALL open that contract.
- FR-19: The card SHALL list the counterparty's earlier requests as clickable items, each
  showing reference, type and status, newest first. Clicking an item SHALL open that
  request.
- FR-20: The contract this request is about SHALL be excluded from the contracts list and
  from every count; this request SHALL be excluded from the requests list and counts.
- FR-21: Counterparty matching SHALL stay as it is today (same name-matching rules). The
  existing text note ("Known counterparty: ..." / "New counterparty ...") SHALL remain,
  worded from the same visible set as the lists.
- FR-22: Each list SHALL show a first batch with a "show more" control that reveals further
  items. The default is 5 contracts and 5 requests (a default, adjustable in planning).
  The summary line and counts SHALL cover the whole visible set, not just the batch.
- FR-23: A section (contracts or requests) with nothing visible SHALL collapse to one line.
  When the whole visible set is empty the card SHALL show the name and "No other contracts
  or requests on file" (never a claim about contracts the viewer cannot see).
- FR-24: The following SHALL be computed ONLY from contracts and requests the viewer may
  see: the lists, every count, the stage mix ("active"), any total value, and the text
  note. A viewer SHALL NOT be able to infer the existence of a contract or request they
  may not see from any count, total, value, stage figure, name or wording. "New
  counterparty" SHALL be stated whenever the visible set is empty, without implying that
  nothing exists.
- FR-25: Visibility of contracts SHALL follow the rules used for contracts elsewhere in the
  app: ethical walls (which bind everyone, including admins), clearance and
  confidentiality level, and access by ownership, creation, grant or admin role. Visibility
  of earlier requests SHALL follow the rules used for requests elsewhere: staff see
  requests they are allowed to see; a non-staff requester sees only their own requests,
  and a request tied to a contract the viewer is walled off from SHALL NOT be listed or
  counted.
- FR-26: Opening a listed item SHALL use the destination page's normal access check. Items
  are listed only if the viewer passes that same check, so a listed item never opens to a
  refusal.
- FR-27: The card SHALL be visible to the same viewers who see the request, and for a
  request with no counterparty named it SHALL be omitted or one line, as today.

### Quality constraints

- FR-28: The page SHALL keep the app's own look in light and dark mode: existing colours,
  spacing, type and components; no hardcoded colours or white blocks.
- FR-29: Narrow widths: columns stack as in 010 with the decision area first; all new lists
  and tiles remain reachable.
- FR-30: The page SHALL be checked side by side against the second mockup screen for REQ-7705
  and for at least one request in each of: no workflow yet, waiting on a human step,
  waiting on approval, rejected with a reply owed, complete, and a request with no contract.
  Mockup sample data (names, figures) is illustrative.

## Permissions, scoping & audit

- Who may do what: this feature adds no new action. Every step action moved into the
  Workflow card (check status, send back for rework, expand results) stays limited to
  exactly the people who have it today. Viewing the page, Activity and Counterparty is
  open to everyone who may see the request, subject to the visibility rules below.
- Data scoping: everything is within the viewer's organisation. Activity events are
  restricted to those of requests and contracts the viewer may see (FR-13). Counterparty
  contracts, requests, counts, totals and the text note are restricted to what the viewer
  may see (FR-24, FR-25), including for staff without manage rights, requesters, owners and
  approvers, each seeing only their own visible set. Two viewers of the same request may
  therefore see different counts; that is expected.
- Audit: no new audit or timeline events. Reading the page records nothing new. Existing
  actions record exactly what they record today. This feature does not change how
  timeline events are written.
- New read access SHALL be permission-gated and organisation-scoped like the data it sits
  beside; it SHALL NOT be reachable by users who cannot open the request.

## Acceptance criteria

- AC-1 (FR-1, FR-3): Given REQ-7705, when the page opens, then the main column reads
  decision area, What was asked, AI risk review, Workflow, Activity, Comments and
  questions; the side column reads Counterparty, Owner and people, Documents, Ask Aegis;
  and no Steps card is in the side column.
- AC-2 (FR-3, FR-4): Given a six-step workflow at step 4, when the page opens, then one
  Workflow card shows "3 of 6 steps done" and six tiles in a row: three tinted done, the
  fourth highlighted with who it waits on, overdue wording if overdue and "Check approval
  status", and two plain upcoming tiles.
- AC-3 (FR-4): Given a skipped step and a failed step in a workflow, when the page opens,
  then each is labelled as skipped or failed and neither counts toward "N of M done".
- AC-4 (FR-5, FR-6): Given a person with manage rights on an open request, when they use
  Check approval status, Review result, Approval chain, Agent hand-off trace and Send back
  for rework from the card, then each behaves as before: results expand with no raw data,
  send back requires a chosen earlier step, and the same entries are recorded as before.
- AC-5 (FR-5, FR-6): Given a requester without manage rights, when they open their request,
  then the Workflow card shows tiles and states but none of the controls they do not have
  today.
- AC-6 (FR-7, FR-29): Given a phone-width window, when the page opens, then the tiles wrap
  or scroll inside the card, the page has no horizontal scroll, and every tile and action
  is reachable.
- AC-7 (FR-8, FR-10): Given REQ-7705 with a contract, when the page opens, then Activity
  lists request and contract events merged by day in time order, including request filed,
  AI triage complete, workflow started, drafted the contract, stage changed, AI review
  complete, AI drafted a suggested change, and the approval and review decisions, each
  with who and when.
- AC-8 (FR-8): Given events on two different days, when Activity is read, then they are
  grouped under day headings and ordered in time within the chosen direction.
- AC-9 (FR-9): Given any Activity list, when it is read in full, then no event-type code,
  identifier or raw data appears.
- AC-10 (FR-8): Given a request with existing hand-offs and comments, when Activity is
  read, then those still appear in the list.
- AC-11 (FR-11): Given a request whose history includes AI job starts and ingestion events,
  when the page opens, then they are hidden; when "Show system details" is used, then they
  appear in time order in plain language; and given no hidden events, then the control
  is absent.
- AC-12 (FR-12, FR-14): Given a request with no events, when the page opens, then Activity
  is one line; and given the events fail to load, then one line states that with a retry,
  and the card does not vanish.
- AC-13 (FR-13): Given a viewer who may see a request but is walled off from its contract,
  when Activity is read, then no event of that contract appears; and given a viewer who
  may not see the request, then no request event reaches them.
- AC-14 (FR-15): Given more than 20 events, when the page opens, then the latest 20 show
  with a control that reveals earlier ones.
- AC-15 (FR-16, FR-17): Given a counterparty "Initech Software Ltd" with visible history,
  when the page opens, then the card shows the name and a summary line such as "5 other
  contracts · 2 active · 7 earlier requests" computed from the visible set.
- AC-16 (FR-18, FR-26): Given visible prior contracts, when the card is read, then each
  shows title, type, stage, value if any and date, newest first; and clicking one opens
  that contract.
- AC-17 (FR-19, FR-26): Given visible earlier requests, when the card is read, then each
  shows reference, type and status, newest first; and clicking one opens that request.
- AC-18 (FR-20): Given a request about contract C, when the card is read, then C is in no
  list and no count, and this request is in no list and no count.
- AC-19 (FR-21): Given the same counterparty spelled "Umbrella Corp" and "Umbrella
  Corporation", when the card is read, then both are matched as they are today.
- AC-20 (FR-22): Given 8 visible contracts and 7 visible requests, when the page opens, then
  5 contracts and 5 requests show with "show more" for each; the summary says 8 and 7;
  and show more reveals the rest.
- AC-21 (FR-23): Given a counterparty with visible contracts but no visible earlier
  requests, then the requests part collapses to one line; and given no visible items at
  all, then the card shows the name and "No other contracts or requests on file".
- AC-22 (FR-24, FR-25): Given a counterparty with 3 contracts, 1 of them behind an ethical
  wall for the viewer, when the card is read, then the lists, count ("2 other
  contracts"), stage figures, any total value and the text note all reflect only the 2
  visible contracts; and the wall's contract title, name, value and existence are not
  inferable from the page.
- AC-23 (FR-24): Given a viewer for whom every matching contract is hidden, when the card is
  read, then it says the counterparty is new / nothing on file and shows no count,
  total or value.
- AC-24 (FR-25): Given a non-staff requester, when the card is read, then earlier requests
  by other people are not listed or counted; given a staff viewer, then they see the
  earlier requests they may see; given an earlier request tied to a contract the viewer
  is walled off from, then it is not listed or counted for them.
- AC-25 (FR-26): Given a listed item, when the viewer opens it, then the destination's normal
  access check runs and passes; no listed item opens to a refusal.
- AC-26 (permissions): Given a requester, an owner, an approver and a staff member without
  manage rights viewing the same request, when each opens it, then each sees only the
  Activity events and counterparty items they may see, and no action control they lack
  today.
- AC-27 (FR-28): Given light and dark mode, when the page opens in each, then the Workflow
  card, tiles, Activity and Counterparty are legible with no white or hardcoded colour
  block.
- AC-28 (Audit): Given the page is opened, or any listed item is clicked, then no audit or
  timeline entry is written; and given a step action is taken from the new card, then
  exactly the same entry as before is written, once.
- AC-29 (FR-30): Given the finished page, when opened next to the second mockup screen for
  REQ-7705, then order, grouping and density match and any difference is recorded and
  accepted by the user.

## Out of scope

- Other pages, the sidebar and navigation, the Legal Intake list and hub.
- The workflow engine, approval rules and routing, risk scoring, AI behaviour, and the
  content of the approval card.
- Editing, merging or correcting counterparties; changing how counterparties are matched.
- Changing how timeline or audit events are written, or adding new event kinds.
- Any new write action, button or audit event.
- The mockup's Option 1 (compact steps in the side column).
- Changing who can see or do anything beyond the visibility rules stated here.
- Re-theming, new fonts or colour tokens.

## Edge cases & error behavior

- Workflow not started, being set up, failed, cancelled or complete: the Workflow card
  shows a clear one-line state with the tiles that apply.
- Very many steps: tiles wrap or scroll inside the card; very long names, titles and
  counterparty names wrap or truncate without breaking layout.
- A request with no contract: Activity holds only request events; Counterparty has nothing
  to exclude.
- A request with no named counterparty: Counterparty omitted or one line.
- A contract in a closed or expired stage appears per FR-18 and is counted as "earlier",
  not as "active" Closed and expired contracts are listed after the active ones (decided by the user)..
- Two contracts or requests with identical titles: items remain distinguishable by type,
  stage and date.
- Visibility changes while the page is open (a wall is added): the next refresh drops
  the affected items and counts.
- Activity or Counterparty load failing must not blank the rest of the page.
- A typed comment, reply or Ask Aegis draft is not lost by refresh or by expanding
  Activity or the lists.

## Open questions

- [x] FR-11 resolved: "Show system details" is available to everyone who can see the Activity.
- [x] FR-18 resolved: closed and expired contracts are listed after the active ones.
- [x] FR-8 resolved: Activity is newest first.

## Context for planning

Non-binding notes for the architect; not requirements.

- Today the Steps card is in `_request-steps.tsx` and is placed in the side column by
  `_request-overview.tsx`; Activity and Counterparty cards are in `_request-cards.tsx`.
  Activity is currently built on the client from the request's creation time, hand-offs
  and workflow comments only, which is why it is thin.
- The real history is in the shared timeline-event store (resource types for intake
  requests and for contracts); the contract's activity reader exists in the contracts
  service. System events are distinguishable by event type; a plain-language mapping per
  event type is needed (and a fallback that never prints the raw code).
- The counterparty relationship note is computed in the intake screening module by
  counting every matching contract and request in the organisation with no per-viewer
  filtering, and it discards the list. It is also stored on the request at screening time,
  so a stored count could leak what a different viewer may not see: the visible set needs
  computing per viewer at read time. Contract visibility has an existing shared filter
  (`accessible_contract_filter` in the contracts access module: org, ethical walls,
  clearance, ownership, grant, admin). Request visibility rules live in the intake module.
  Note the filter also binds to contract-linked requests.
- The mockup shows a total value ("₹90 lakh in force") and the sentence "You see only the
  contracts you have access to"; both are illustrative. Totals must come from the visible
  set only (FR-24).
- Per the project rules the spec stays free of endpoint and table names; those belong in
  plan.md.
