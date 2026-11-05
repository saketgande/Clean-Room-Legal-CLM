# Feature Specification: Approve and reject on the request page

Feature ID: 009-approve-on-request-page
Created: 2026-10-22
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Summary

Today an approver must leave the request, open the separate approvals page, find the contract by name and decide there. This feature lets the assigned approver approve or reject directly on the request page, after seeing the handful of facts that matter for their role, with a comment box. It is an approval, not a review: the card is short, shows only facts that are already stored, never invents a value, and tells the approver whether each value was verified or only extracted.

## User stories

- As an assigned approver, I want to approve or reject on the request page, so that I do not have to hunt for the contract on another page.
- As a Finance approver, I want to see value, term, payment terms, liability and renewal, so that I can decide on commercial exposure at a glance.
- As a Legal approver, I want to see playbook risk, indemnity, termination, governing law and non-standard clauses, so that I can decide on legal exposure at a glance.
- As an approver, I want missing facts shown as "Not available" and each shown fact labelled verified or extracted, so that I never rely on a made-up or unconfirmed value without knowing it.
- As an approver, I want to be made to acknowledge open critical/high risks before approving, so that I never approve them by accident.
- As an approver or requester, I want the chain to show who decided, their comment and when, right after a decision, so that I can see what happened without leaving the page.
- As an admin, I want to choose which view (General, Finance or Legal) an approval step shows in the workflow builder, so that each approver sees the right facts.
- As a requester or other viewer, I want a read-only view with no decision buttons, so that only the assigned approver can decide.

## Functional requirements

Approve/Reject availability
- FR-1: The system SHALL show Approve and Reject controls on the request page only to a user who is an assigned approver on the currently active approval rung and has not yet decided on it.
- FR-2: The system SHALL show every other viewer of the request (requester, other approvers, approvers who already decided, anyone else with access) the approval facts and chain read-only, with no Approve or Reject controls.
- FR-3: The system SHALL refuse a decision submitted by anyone who is not an assigned approver on the active rung, regardless of what the page showed.

Facts shown
- FR-4: The system SHALL display approval facts using only data already stored for the request or its contract, and SHALL NOT run any AI processing when the card is opened.
- FR-5: The system SHALL tag every shown value as "verified" or "extracted" according to its source (values confirmed on the contract record are verified; values read out of the document or its renewal data are extracted).
- FR-6: WHEN a fact has no stored value, the system SHALL show "Not available" for it and SHALL NOT estimate, infer or default a value.
- FR-7: The system SHALL NOT show an extracted clause value that is out of date relative to the contract's current version; such a fact SHALL show "Not available".
- FR-8: Liability cap and payment terms SHALL be shown as short quoted clause text, not converted to numbers.
- FR-9: The system SHALL show the Finance view as: value, term, payment terms, liability, renewal.
- FR-10: The system SHALL show the Legal view as: playbook risk (critical/high counts), indemnity, termination, governing law, non-standard clauses.
- FR-11: The system SHALL show the General view as: value, term, overall risk, top issues.
- FR-12: WHEN a request has no contract yet, the system SHALL show whatever facts the request itself holds and "Not available" for the rest.

View selection
- FR-13: Each workflow approval step SHALL carry a setting for its approver view with values General, Finance or Legal; WHEN unset it SHALL behave as General.
- FR-14: The system SHALL choose the view only from that setting and SHALL NOT infer it from team names or approver roles.
- FR-15: An admin SHALL be able to set the view on an approval step in the workflow builder.

Decision
- FR-16: The system SHALL provide a comment box; the comment SHALL be optional for Approve and required for Reject.
- FR-17: WHEN Reject is submitted with an empty or whitespace-only comment, the system SHALL refuse it and show a message stating a comment is required.
- FR-18: WHEN the open-risk count (critical plus high) is greater than zero, the system SHALL require an explicit acknowledgement checkbox before Approve can be submitted, and SHALL refuse an Approve submitted without it, regardless of the page.
- FR-19: The open-risk count used for FR-18, and shown on the card, SHALL equal the risk count shown elsewhere on the same request page.
- FR-20: WHEN no critical or high risks are open, the system SHALL NOT show the acknowledgement checkbox or require acknowledgement.
- FR-21: WHEN an approval is submitted with acknowledgement, the system SHALL record the acknowledgement in the audit trail.

After a decision
- FR-22: WHEN a decision succeeds, the system SHALL update the request page without navigation to show the new chain state and, for every rung, each decision's approver, comment and time.
- FR-23: WHEN a decision succeeds, the Approve/Reject controls SHALL disappear for that user on that rung.
- FR-24: WHEN the final required approval is given on a request that has no contract yet, the system SHALL advance the workflow promptly after the decision, without waiting for a later periodic check.
- FR-25: WHEN a non-final rung is approved, the system SHALL show the next rung as active and the request as not yet approved.
- FR-26: WHEN a rung is rejected, the system SHALL send the workflow back as it does today and the page SHALL show the rejection and its comment.

Existing behaviour preserved
- FR-27: The system SHALL continue to refuse self-approval, enforce approval authority limits, and treat a repeated or concurrent submission as one decision (the second is refused, not applied twice).
- FR-28: The existing approvals page SHALL continue to work unchanged.
- FR-29: Before a contract exists, values taken from the requester's form answers are tagged "requester-stated" (a third label, distinct from "verified" and "extracted"), so an unchecked number never looks confirmed.

## Permissions, scoping & audit

- Approve/Reject: only an assigned approver on the active rung who has not decided; the permission to decide on approvals is required. Not the requester on their own request (no self-approval). Admin role confers no decision right unless assigned.
- View card (facts, chain, decisions): any user who can already view that request, subject to the same visibility rules (organisation scope, ethical walls, access grants). A user who cannot see the request cannot see the card's facts, risks or comments.
- Everything is scoped to the user's organisation; a user from another organisation gets no data and cannot decide.
- Set approver view: admins who may edit workflows, same as other step settings. The change is audited like other workflow edits.
- Audit trail (immutable): the decision (who, approve/reject, rung, comment, time) as today; a separate entry when open risks were acknowledged, recording who, the request, and the critical/high counts they acknowledged; the workflow-view setting change. Refused attempts (non-approver, self-approval, missing acknowledgement) must not change state.
- User-visible history: the decision appears in the request's timeline as today.

## Acceptance criteria

- AC-1 (FR-1): Given I am the assigned approver on the active rung and have not decided, when I open the request page, then I see Approve and Reject.
- AC-2 (FR-2): Given I am the requester, a different approver or a user with view-only access, when I open the request page, then I see the facts and chain but no Approve or Reject.
- AC-3 (FR-2, FR-23): Given I already decided on the rung, when I reopen the page, then I see no decision controls.
- AC-4 (FR-3): Given I am not an assigned approver, when I submit a decision by any means, then it is refused and nothing changes.
- AC-5 (FR-3): Given I belong to another organisation, when I request the card or submit a decision, then I get no data and the decision is refused.
- AC-6 (FR-4): Given a request with stored facts, when I open the card, then no AI job is started.
- AC-7 (FR-5): Given the contract value is confirmed on the record and the liability clause was extracted from the document, when I open the card, then value is tagged verified and liability extracted.
- AC-8 (FR-6): Given the contract has no payment-terms clause stored, when I open the Finance view, then payment terms shows "Not available".
- AC-9 (FR-7): Given a clause extraction belongs to an older version than the current one, when I open the card, then that fact shows "Not available".
- AC-10 (FR-8): Given a liability clause is stored, when I open the card, then I see its short text and no computed number.
- AC-11 (FR-9): Given the step view is Finance, when I open the card, then I see exactly value, term, payment terms, liability and renewal.
- AC-12 (FR-10): Given the step view is Legal, when I open the card, then I see playbook risk, indemnity, termination, governing law and non-standard clauses.
- AC-13 (FR-11, FR-13): Given the step has no view set, when I open the card, then I see the General view.
- AC-14 (FR-14): Given an approver's team is named "Finance" but the step view is Legal, when they open the card, then they see the Legal view.
- AC-15 (FR-15): Given I am an admin in the workflow builder, when I set an approval step to Finance and save, then requests on that step show the Finance view.
- AC-16 (FR-12): Given a request with no contract, when the approver opens the card, then request-held facts are shown and the rest show "Not available".
- AC-17 (FR-16): Given I leave the comment empty, when I approve, then the approval is recorded with no comment.
- AC-18 (FR-17): Given the comment is empty or spaces, when I reject, then it is refused with a "comment required" message and nothing changes.
- AC-19 (FR-18): Given critical or high risks are open and I have not ticked the acknowledgement, when I approve, then it is refused and the rung is unchanged.
- AC-20 (FR-18, FR-21): Given critical or high risks are open and I tick the acknowledgement, when I approve, then the approval is recorded and the audit trail has an acknowledgement entry with the counts.
- AC-21 (FR-19): Given the request page header shows 2 critical and 1 high open risks, when I open the card, then the card shows the same 2 and 1.
- AC-22 (FR-20): Given no critical or high risks are open, when I open the card, then there is no checkbox and approving needs no acknowledgement.
- AC-23 (FR-22): Given I approve, when it succeeds, then without leaving the page the chain shows my decision with my name, comment and time.
- AC-24 (FR-24): Given a request with no contract and I give the last required approval, when it succeeds, then the workflow moves to its next step shortly after, without waiting for a periodic check.
- AC-25 (FR-25): Given a two-rung chain, when the first rung is approved, then the second shows as active and the request is not marked approved.
- AC-26 (FR-26): Given I reject with a comment, when it succeeds, then other rungs are cancelled, the workflow is sent back and the page shows my rejection and comment.
- AC-27 (FR-27): Given I am the requester and also listed as approver, when I try to approve, then it is refused.
- AC-28 (FR-27): Given the request value exceeds my approval authority, when I approve, then it is refused with the authority message.
- AC-29 (FR-27): Given I double-click Approve, when both submissions arrive, then one decision is recorded and the other is refused.
- AC-30 (FR-28): Given a pending approval, when I decide from the approvals page, then it works as before and the request page reflects it.
- AC-31 (FR-29): Given a request with no contract, when the approver opens the card, then value and dates from the form show the "requester-stated" tag, and any missing one shows "Not available".

## Out of scope

- Per-role mandatory checklists.
- Save Draft of a decision.
- Request Changes as a third outcome.
- A budget field.
- An approval card on the contract page.
- Parsing liability caps or payment terms into numbers.
- New approval records or a new approval data structure.
- Running or refreshing any AI extraction from the card.
- Requiring the risk acknowledgement on decisions made through the assistant (not gated by this feature).
- Changing authority-limit policy, approver assignment or team setup.

## Edge cases & error behavior

- Approver is on more than one rung or in a parallel group: controls apply only to the active rung(s) they are assigned to.
- Approval chain already decided or request moved on when the user submits: the decision is refused with a clear message and the page refreshes to current state.
- Risks change between opening the card and submitting: the requirement is evaluated at submission time against the current count.
- No contract, no extractions, no renewal data: card still renders, all affected facts show "Not available".
- Network or server error on submit: the page shows the error, keeps the typed comment and checkbox state, and does not show the decision as done.
- A very long clause snippet is shortened on the card, not shown in full.
- Dark and light themes both render the card legibly.

## Open questions

- [x] FR-29 resolved: request-form values are tagged "requester-stated".

## Context for planning

Non-binding notes for the architect; not requirements. The approved brief is at `/Users/saketgande/.claude/plans/no-i-just-adaptive-graham.md`. It proposes reusing the existing decision flow (which already enforces self-approval, authority, locking, reject-comment and double-submit rules), adding decision data to the intake chain, a read-only summary built from stored contract fields, renewal data and non-stale clause extractions, taking the risk count from the same source as the page's risk header, and the per-step view setting on the approval step config. The brief notes that the no-contract approval path advances only on a workflow refresh, which FR-24 requires be triggered right after a decision.
