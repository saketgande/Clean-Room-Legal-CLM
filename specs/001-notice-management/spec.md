# Feature Specification: Notice Management

Feature ID: 001-notice-management
Created: 2026-08-11
Status: DRAFT   <!-- DRAFT | APPROVED — only the user approves -->

## Summary

Legal teams routinely send and receive formal notices tied to contracts — termination
notices, breach notices, renewal notices, cure notices, and similar instruments that
carry contractual deadlines and legal consequences. Today there is no structured way
to track these within the platform, which creates risk of missed deadlines and no
defensible record of what was sent, to whom, when, and how it was delivered. This
feature introduces a Notice Management module so users can log, track, and manage the
lifecycle of legal notices associated with contracts, including their delivery details
and current status, within their organization's data.

## User stories

- As a contract owner, I want to record a notice I sent or received related to one of
  my contracts, so that there is a clear record of the notice and its delivery details.
- As a legal team member, I want to see the current status of a notice (e.g. drafted,
  sent, delivered, acknowledged, disputed, expired), so that I know whether follow-up
  action is required.
- As a legal team member, I want to view all notices associated with a given contract,
  so that I understand the full notice history for that agreement.
- As a legal team member, I want to view all notices across my organization (not just
  a single contract), so that I can track outstanding notices in one place.
- As an admin/compliance user, I want every notice creation and status change to be
  recorded in an audit trail, so that the organization can demonstrate what happened
  and when if a dispute arises.
- As a user, I want to update a notice's status as it progresses (e.g. from "sent" to
  "delivered" to "acknowledged"), so that the record stays current.
- As a user, I want to be prevented from viewing or managing notices belonging to
  another organization, so that tenant data stays isolated.

## Functional requirements

Numbered, testable statements. Each must be verifiable by /verify.

- FR-1: The system SHALL allow an authorized user to create a notice record
  associated with exactly one contract within their organization.
- FR-2: Each notice SHALL have a notice type. [NEEDS CLARIFICATION: what is the
  authoritative, closed list of notice types the system must support — e.g.
  termination, breach, renewal, cure, default, indemnification, other? Is the list
  fixed, or can org admins extend it?]
- FR-3: Each notice SHALL have a direction indicating whether it was sent by the
  organization or received by the organization.
- FR-4: Each notice SHALL have a status drawn from a defined lifecycle (e.g. drafted,
  sent, delivered, acknowledged, disputed, withdrawn, expired). [NEEDS
  CLARIFICATION: confirm the exact set of statuses and the allowed transitions between
  them — e.g. can a notice move from "delivered" back to "sent," or is the lifecycle
  strictly forward-only?]
- FR-5: The system SHALL capture delivery details for each notice, including at
  minimum: delivery method, date sent, and date delivered/received (when known).
  [NEEDS CLARIFICATION: which delivery methods must be supported — e.g. certified
  mail, email, courier, hand delivery, platform-generated e-signature/e-notice? Is
  proof-of-delivery (e.g. tracking number, signed receipt, attached document) a
  required or optional field?]
- FR-6: The system SHALL allow an authorized user to attach or reference supporting
  documentation to a notice (e.g. the notice letter itself, proof of delivery).
  [NEEDS CLARIFICATION: is document attachment in scope for this feature, or is it
  deferred to a later phase? If in scope, are there file type/size constraints beyond
  what the platform already enforces elsewhere?]
- FR-7: The system SHALL allow an authorized user to record the sender and recipient
  of a notice (organization, counterparty, or named individual/role as applicable).
- FR-8: The system SHALL allow an authorized user to record a response/cure deadline
  on a notice where applicable (e.g. a cure period end date), and SHALL make overdue
  deadlines visibly distinguishable from met/pending ones.
- FR-9: WHEN a notice's response/cure deadline has passed and the notice has not
  reached a terminal status, the system SHALL flag the notice as overdue. [NEEDS
  CLARIFICATION: is a proactive reminder/escalation mechanism (e.g. notification to
  the contract owner ahead of the deadline) required in this feature, or is passive
  flagging in the notice list sufficient for v1?]
- FR-10: The system SHALL allow an authorized user to update the status of an existing
  notice, and SHALL retain a history of status changes (who changed it, from what
  status to what status, and when).
- FR-11: The system SHALL allow an authorized user to view the list of notices
  associated with a specific contract.
- FR-12: The system SHALL allow an authorized user to view a list of notices across
  their organization, filterable by at least notice type, status, direction, and
  contract.
- FR-13: The system SHALL restrict visibility and management of a notice to users
  within the organization that owns the associated contract; a notice tied to a
  contract the requesting user cannot access (due to organization scope or an
  ethical wall / access grant restriction on that contract) SHALL NOT be visible or
  editable by that user.
- FR-14: The system SHALL record an immutable audit trail entry for every notice
  creation, field edit, status change, and deletion (if deletion is permitted),
  capturing the acting user, organization, timestamp, and the nature of the change.
- FR-15: The system SHALL allow an authorized user to edit the non-status details of
  a notice (e.g. correct a delivery date or notice type) prior to [NEEDS
  CLARIFICATION: is there a point after which a notice record becomes locked from
  editing — e.g. once acknowledged/expired/disputed — to preserve its evidentiary
  value, or should full edit history via the audit trail be considered sufficient
  and edits remain always allowed to authorized roles?].
- FR-16: [NEEDS CLARIFICATION: can a notice be deleted at all, or only ever
  superseded/withdrawn via a status change, given its role as a legal record? If
  deletion is allowed, who may perform it and is it a soft delete retained for audit
  purposes?]
- FR-17: [NEEDS CLARIFICATION: which roles/permission levels exist for this feature?
  At minimum the spec assumes a "can view notices for contracts I can access" level
  and a "can create/manage notices for contracts I can access" level — confirm
  whether these two are sufficient, whether a broader org-wide "manage all notices"
  admin capability is also required, and how these map to this platform's existing
  roles (e.g. Attorney, Paralegal, Contract Owner, Org Admin).]
- FR-18: [NEEDS CLARIFICATION: what is the required retention period for notice
  records and their audit history? Does this feature inherit the platform's existing
  general document/record retention policy, or does it need a distinct one given
  notices' legal significance?]

## Permissions, scoping & audit

This platform is multi-tenant with role-based access, ethical walls, and an
immutable audit trail. Pin down, per action in this feature:

- **View notices** (list/detail, for a contract or across the org): requires a
  `notice:read`-equivalent permission. A user may only see notices for contracts
  within their own organization, and further only for contracts they are otherwise
  permitted to see under existing contract access rules (organization scope +
  ethical walls / per-contract access grants) — a notice never grants visibility
  into a contract the user could not already see.
- **Create a notice**: requires a `notice:create`-equivalent permission, and the
  acting user must already have read (or better) access to the underlying contract.
  A new notice is always scoped to the organization of the contract it is attached
  to; cross-org notice creation is not possible.
- **Update a notice's status or details**: requires a `notice:update`-equivalent
  permission (or a narrower `notice:manage` grouping — see FR-17 clarification),
  scoped the same way as creation.
- **Delete a notice** (if permitted at all — see FR-16): would require a distinct,
  more restrictive permission (e.g. `notice:delete` or `admin_panel:access`-level).
  [NEEDS CLARIFICATION: confirm the exact permission name/level once FR-16 and
  FR-17 are resolved, so it can be aligned with this platform's existing role
  definitions.]
- **Org-scoping**: every notice record belongs to exactly one organization (that of
  its associated contract), and every list/detail query is filtered by the
  requesting user's organization plus applicable contract-level ethical-wall/access
  rules — this is not optional or configurable.
- **Audit trail**: every create, field edit, status transition, and (if applicable)
  delete of a notice must produce an audit trail entry recording the acting user,
  their organization, a timestamp, the action taken, and enough detail to
  reconstruct the change (e.g. old value → new value for edits and status changes).
  Notice audit entries must be attributable back to the associated contract's audit
  history so a reviewer researching a contract can find related notice activity.

## Acceptance criteria

Given/When/Then scenarios that define "done". Every criterion maps to at least one
functional requirement.

- AC-1 (FR-1): Given a user with notice-create permission and read access to
  Contract A, when they submit a new notice for Contract A with required fields
  populated, then a notice record is created and associated with Contract A.
- AC-2 (FR-1, FR-13): Given a user who does not have access to Contract B (different
  organization or blocked by an ethical wall), when they attempt to create or view a
  notice for Contract B, then the system denies the action.
- AC-3 (FR-3, FR-4): Given a newly created notice, when it is saved, then it has a
  recorded direction (sent/received) and an initial status from the defined
  lifecycle.
- AC-4 (FR-5): Given a notice being created or edited, when the user enters delivery
  method and dates, then those values are saved and displayed on the notice detail
  view.
- AC-5 (FR-8, FR-9): Given a notice with a recorded cure/response deadline that has
  passed and the notice is not in a terminal status, when the notice list or detail
  view is rendered, then the notice is visibly flagged as overdue.
- AC-6 (FR-10): Given an existing notice, when an authorized user changes its status,
  then the new status is saved, and a status-change history entry records the
  previous status, new status, acting user, and timestamp.
- AC-7 (FR-11): Given a contract with two or more associated notices, when an
  authorized user views that contract's notice list, then all notices tied to that
  contract are shown and notices tied to other contracts are not.
- AC-8 (FR-12): Given multiple notices across several contracts in a user's
  organization, when the user views the org-wide notice list and applies a filter
  (type, status, direction, or contract), then only matching notices are shown.
- AC-9 (FR-14): Given any create, edit, status change, or delete action on a notice,
  when the action completes, then a corresponding audit trail entry exists recording
  the acting user, organization, timestamp, and nature of the change.
- AC-10 (FR-13): Given a user authorized in Org X, when they query for a notice that
  belongs to Org Y, then the system returns a not-found/forbidden result rather than
  the notice's data.

## Out of scope

- Automatic generation, drafting, or templating of notice letter content (e.g.
  AI-drafted termination notice text) — this feature only tracks notices as records,
  it does not author them.
- Integration with external mail/courier/e-signature delivery providers to send
  notices directly from the platform or auto-capture delivery confirmation.
- Automated legal-deadline calculation engines beyond storing a user-entered
  response/cure deadline (e.g. auto-computing a cure period from contract terms).
- Bulk import of historical notices from external systems.
- Notices unrelated to a contract (e.g. general correspondence not tied to a
  specific agreement) — every notice in this feature must reference a contract.

## Edge cases & error behavior

- Creating a notice without an associated contract SHALL be rejected with a
  validation error (FR-1).
- Creating a notice with a delivered/received date earlier than the sent date SHALL
  be rejected with a validation error.
- Attempting to view, edit, or change the status of a notice that does not exist (or
  is not visible to the user's organization/access scope) SHALL return a not-found
  result, not a data leak indicating existence in another org.
- Concurrent status updates to the same notice by two users SHALL not silently
  overwrite one another; the system must record both underlying actions in the
  status-change history / audit trail rather than losing one update. [NEEDS
  CLARIFICATION: is optimistic-locking/conflict-detection feedback to the second
  user required, or is "last write wins with full audit history" acceptable?]
- If the associated contract is deleted or archived, existing notices SHALL remain
  visible/queryable (as a historical record) rather than being silently removed.
  [NEEDS CLARIFICATION: confirm whether contract archival should also affect the
  editability of its notices.]
- A notice list with no results (e.g. a contract with no notices, or a filter with no
  matches) SHALL display a clear empty state rather than an error.

## Open questions

- [ ] [NEEDS CLARIFICATION: closed list of notice types and whether it is
  extensible per org — FR-2]
- [ ] [NEEDS CLARIFICATION: exact status lifecycle values and allowed transitions —
  FR-4]
- [ ] [NEEDS CLARIFICATION: required/optional delivery methods and whether
  proof-of-delivery is mandatory — FR-5]
- [ ] [NEEDS CLARIFICATION: is document attachment to a notice in scope for this
  feature or a later phase — FR-6]
- [ ] [NEEDS CLARIFICATION: is proactive reminder/escalation (notifications ahead of
  a cure/response deadline) required in v1, or is passive "overdue" flagging
  sufficient — FR-9]
- [ ] [NEEDS CLARIFICATION: does a notice become locked from editing once it reaches
  certain statuses, to preserve evidentiary integrity — FR-15]
- [ ] [NEEDS CLARIFICATION: can notices be deleted at all, and if so by whom and as a
  soft or hard delete — FR-16]
- [ ] [NEEDS CLARIFICATION: exact roles/permission levels needed for this feature and
  their mapping to this platform's existing roles — FR-17]
- [ ] [NEEDS CLARIFICATION: retention period requirements for notice records and
  their audit history — FR-18]
- [ ] [NEEDS CLARIFICATION: is optimistic-locking/conflict feedback required for
  concurrent status edits, or is audit-logged "last write wins" acceptable — Edge
  cases]
- [ ] [NEEDS CLARIFICATION: should archiving/deleting the parent contract restrict
  further edits to its notices — Edge cases]
