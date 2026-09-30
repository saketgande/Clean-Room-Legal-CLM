# Feature Specification: Dynamic, Condition-Driven Approval Chains

Feature ID: 004-approval-chain-reconciliation
Created: 2026-09-14
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Summary

Today, approval routing on this platform is decided once, up front, by matching a
routing rule's criteria and building a flat list of approval requests from it. This
feature adds a second capability on top: some approval requirements are conditional
on facts about the specific item being approved (e.g. a contract's value crossing a
threshold requires an additional Finance approver on top of the normal reviewer), and
those conditional requirements must be evaluated exactly once — at the moment an
approval chain is set up for that item — then fixed in place as an auditable record,
not silently re-evaluated every time someone checks status. Every approver added by a
condition must be traceable to the specific, human-readable condition that required
them, and approvers with an earlier position in a sequential chain must act before a
later approver's action counts. This capability applies only to approval chains
created after this feature ships; approvals already in flight at cutover finish
under today's mechanism, untouched. This is feature 4 (the final feature) of the User
Management / Menu-Screen Security / Workflow Eligibility initiative, building on
feature 002's org-unit-scoped role resolution and feature 003's screen-access model,
and it must reconcile its requirements against the platform's existing approval-
routing capability rather than assuming this is greenfield — see "Context for
planning" for the specific reconciliation questions left for the architect.

## User stories

- As a contract reviewer, I want an approval chain to automatically require an
  additional approver when the item meets a defined condition (e.g. contract value
  over a threshold), so that high-risk items get the right extra scrutiny without a
  human having to remember to route them differently.
- As a Finance approver added to a chain by a value-threshold condition, I want to
  see plainly why I was required to approve this item (e.g. "required because
  contract_value (1,200,000) > 1,000,000"), so that I understand my role in the
  decision without having to ask.
- As a compliance/security reviewer, I want the set of approvers required for a given
  approval chain to be fixed at the moment the chain starts and never silently
  change afterward — even if the underlying record's data is corrected later — so
  that I can trust the chain reflects what was actually evaluated at decision time.
- As a compliance/security reviewer, I want an explicit, logged action available for
  recalculating a chain's required approvers when circumstances genuinely warrant it
  (e.g. a data-entry correction), distinguishable in the audit trail from a normal
  approval action, so that I can see exactly when and why a chain's requirements
  changed.
- As an approver in a sequential chain, I want my approval action to be rejected (or
  clearly flagged) if an earlier-sequence required approval on the same chain is
  still pending, so that approvals happen in the intended order.
- As a platform administrator, I want the condition logic that adds approvers to a
  chain to be defined declaratively (comparisons like "greater than," "equals," "is
  one of") rather than as arbitrary code, so that a malformed or malicious condition
  definition can never execute unintended logic against the platform.
- As a platform administrator, I want to be able to express a compound business rule
  ("value over 1M AND jurisdiction is EU") as separate single-condition rules that
  happen to require the same role, so that I never need a more expressive (and
  riskier) condition language to cover realistic business cases.
- As a security reviewer, I want confidence that no condition definition, however
  malformed or adversarial, can cause the evaluator to do anything other than
  reject/ignore it, so that this feature cannot become a code-execution vector.
- As an organization administrator, I want to be able to see, for a blocked approval
  step, exactly which required role has no eligible holder, so that I know there is a
  gap without having to dig through logs to find it.
- As an org administrator, I want approval-chain condition rules, roles, and
  recalculation actions to be scoped to my own organization, consistent with how
  every other access-control feature in this initiative behaves, so that no
  cross-tenant leakage is possible.

## Functional requirements

Numbered, testable statements. Each must be verifiable by /verify.

**Condition-driven approver requirements**

- FR-1: The system SHALL allow an authorized administrator to define, for a step in
  an approval chain, a base set of required approver role(s) plus zero or more
  additional condition rules, where each condition rule specifies: a declarative
  condition to evaluate against the item's data, the role required if that condition
  is true, and where the added approver falls in the chain's sequence relative to
  other approvers.
- FR-2: A condition rule's declarative condition SHALL be expressed using EXACTLY the
  following fixed set of comparison operators — greater-than, less-than, equal-to,
  "is one of a list," "contains" — evaluated as a single comparison against one
  named field of the item's data. No condition definition may express arbitrary
  code, a general-purpose expression language, any operator outside this fixed set,
  or anything that would require executing logic supplied as data. A single
  condition rule SHALL NOT combine multiple comparisons with a boolean AND/OR or any
  other combinator — one condition rule expresses exactly one comparison.
- FR-3: A compound business rule that requires more than one comparison to be true
  (e.g. "contract_value > 1,000,000 AND jurisdiction == EU") SHALL be expressed as
  multiple separate single-condition rules on the same step, each requiring the same
  role. WHEN more than one condition rule targeting the same role evaluates true for
  the same chain instance, materialization (FR-5) SHALL produce exactly one required
  approver requirement for that role — never one requirement per matching rule — and
  its human-readable explanation (FR-7) SHALL reference every rule that fired for
  it, not just one arbitrarily.
- FR-4: WHEN evaluating a condition rule, the system SHALL treat the condition
  definition as untrusted structured data to be parsed and matched, never as logic
  to be executed. A condition using an operator outside the fixed set (FR-2), or
  otherwise malformed, SHALL cause the evaluator to fail closed — reject or skip
  that condition (treating it as not satisfied) rather than error into an undefined
  or attacker-influenced code path, and SHALL NOT prevent evaluation of the chain's
  other, well-formed conditions.
- FR-5: WHEN an item enters a step of an approval chain, the system SHALL evaluate
  every condition rule for that step against the item's data at that moment and
  materialize the resulting full list of required approvers (base requirements plus
  every condition that evaluated true, deduplicated per FR-3) as a fixed record for
  that chain instance. This evaluation SHALL happen exactly once, at chain-entry
  time — not on every subsequent read or status check.
- FR-6: Every materialized approver requirement that resulted from one or more
  condition rules SHALL record a traceable link back to the specific condition
  rule(s) that produced it, distinct from a requirement that is part of the step's
  unconditional base requirement.
- FR-7: WHEN displaying a materialized approver requirement that resulted from one or
  more condition rules, the system SHALL show a human-readable explanation of the
  specific condition(s) that fired, including, for each, the field name, the
  operator, the threshold/comparison value, and the item's actual value at
  evaluation time (e.g. "required because contract_value (1,200,000) >
  1,000,000") — not merely the fact that some rule matched.

**Snapshot immutability and recalculation**

- FR-8: Once a step's required-approver list has been materialized (FR-5) for a
  given chain instance, the system SHALL NOT automatically or silently regenerate,
  add to, or remove from that list as a consequence of the underlying item's data
  changing after materialization.
- FR-9: The system SHALL provide an explicit "recalculate required approvers"
  action, distinct from a normal approval decision, that only a user holding the
  appropriate administrative permission may invoke on a chain instance, to
  re-evaluate the step's condition rules and update the required-approver list to
  match current data. This action SHALL be triggered ONLY by an explicit,
  deliberate invocation by such a user. The system SHALL NOT automatically detect
  that an item's underlying data has changed, prompt for recalculation, or trigger
  recalculation itself under any circumstance — no part of the base workflow engine
  watches for "relevant" field changes on the administrator's behalf; noticing that
  data has changed and that a recalculation may be warranted is the administrator's
  responsibility, not the system's.
- FR-10: Every invocation of the recalculate action (FR-9) SHALL be recorded as its
  own distinct, append-only entry in the chain instance's history — separate in kind
  from an approve/reject decision entry — capturing who performed it, when, and the
  before/after state of the required-approver list, so a later reviewer can see
  exactly when and why the chain's requirements changed.
- FR-11: The chain instance's action/decision history (the record of who approved,
  rejected, recalculated, or otherwise acted, and when) SHALL be append-only by
  design: an entry, once written, SHALL NEVER be edited or deleted by any code path,
  including administrative ones. Correction of a mistaken entry is achieved only by
  appending a new, distinct entry, never by mutating or removing the original.

**Sequential ordering**

- FR-12: Each individual step of an approval chain SHALL independently declare
  whether its required approvals must happen in sequence order or may happen in
  parallel — this configuration is per step, not per whole workflow/chain
  definition, so a single chain MAY mix sequential steps and parallel steps. A step
  defaults to enforcing order unless explicitly configured for parallel approval.
- FR-13: WHEN a step is configured as sequential, an approver assigned a later
  sequence position SHALL be prevented from recording an approval decision (or, at
  minimum, SHALL have that decision clearly flagged as out-of-order) while any
  required approver at an earlier sequence position on the same step has not yet
  acted.
- FR-14: WHEN a step is configured as parallel, no ordering restriction per FR-13
  applies — any required approver on that step may act in any order.

**Chain progression and decisions**

- FR-15: The system SHALL allow an eligible approver to record an approve or reject
  decision against a required approver requirement assigned to them, and SHALL
  reject an attempt by a user who is not an eligible holder of the required role for
  that requirement.
- FR-16: A chain instance's step SHALL be considered complete only once every
  materialized required-approver requirement for that step (base and
  condition-triggered) has received an approval decision, respecting FR-12–FR-14's
  ordering rule where sequential.
- FR-17: WHEN any required approver requirement on a step receives a reject
  decision, the system SHALL mark the step (and, per existing chain behavior for
  the domain in question) the overall chain instance as rejected, rather than
  waiting for remaining approvers to act.

**Eligible-approver resolution**

- FR-18: WHEN a condition rule or a step's base requirement specifies a required
  role, the system SHALL determine the set of users eligible to fulfill that
  requirement using the platform's existing org-unit-scoped, hierarchy-aware role
  resolution (the same resolution mechanism feature 002 established), rather than
  a separately implemented eligibility check.
- FR-19: A required-approver requirement whose configured role has zero eligible
  users at the chain instance's org-unit scope at materialization time SHALL be
  detected and flagged as unfulfillable, and the step SHALL be visibly blocked from
  completing (per FR-16) while the gap persists. This spec defines only the
  detection-and-visibility behavior; the mechanism by which the gap is eventually
  resolved (e.g. reassignment, granting eligibility, escalation) is an
  implementation decision left to plan.md, not specified here.
- FR-20: A user holding the appropriate administrative permission SHALL be able to
  see, for any blocked step, which specific required role has no eligible holder —
  this SHALL be visible on or in connection with the chain instance itself (not only
  discoverable via logs or a separate audit query).

**Cutover and rollout scope**

- FR-21: This feature's condition-driven, materialized-chain behavior (FR-1–FR-20)
  SHALL apply only to approval chains/workflow instances created after this feature
  ships. The system SHALL NOT migrate or backfill any pre-existing, in-flight
  `ApprovalRequest`/`ApprovalDecision` record from the legacy rule-based routing
  engine into the new condition-driven model. Every approval request already in
  flight at cutover SHALL continue to be routed, decisioned, and completed entirely
  under the legacy mechanism it started under, unaffected by this feature.
- FR-22: WHEN a user submits a new **contract** for approval — i.e. through the
  platform's existing contract-approval-submission entry point (today, the
  `POST /approvals/requests` route, which calls `submit_contract_for_approval` /
  `submit_subject_for_approval` in `app.approvals.service`) — OR submits a new
  **intake request** for approval — i.e. through the platform's existing
  intake-request-approval-submission entry point (today,
  `submit_request_for_approval` in `app.intake.approval_bridge`, which also calls
  the shared `submit_subject_for_approval`) — AFTER this feature ships, the system
  SHALL determine that contract's or intake request's required approvers using
  this feature's condition-driven, materialized approval-chain mechanism
  (FR-1–FR-20), not the legacy routing-rule engine's flat `ApprovalRequest`
  construction. Both subject types — contracts and intake requests — are rerouted
  to this feature's mechanism going forward for submissions made after this
  feature ships; neither subject type continues on the legacy routing-rule engine
  for new submissions. This is consistent with, and does not contradict, FR-21 —
  FR-21 governs what happens to work already in flight at cutover (untouched,
  legacy, for both subject types), while FR-22 governs what happens to new
  submissions of either subject type made after cutover (rerouted to this
  feature's mechanism). This requirement states only that the reroute happens for
  both subject types; the mechanism by which the existing routing-rule
  configuration is used or translated to determine each subject type's chain's
  base requirements and condition rules — and whether the two subject types share
  one chain-definition concept or have their own — is an implementation decision
  left to plan.md, not specified here.

## Permissions, scoping & audit

This platform is multi-tenant with role-based access, ethical walls, and an
immutable audit trail. Pin down, per action in this feature:

- **Define / modify / deactivate a condition rule or a step's base approver
  requirement** (FR-1, FR-2, FR-3): restricted to an authorized administrator,
  scoped to configuration belonging to their own organization only — a condition
  rule, role requirement, or step definition SHALL never reference or be visible to
  a different organization. Audit: records actor, timestamp, the step/rule
  affected, and the prior and new definition (condition, required role, sequence
  position, base vs. conditional).
- **Record an approval or rejection decision against a required-approver
  requirement** (FR-15): restricted to a user who is, at the time of the decision,
  an eligible holder (per FR-18) of the requirement's required role at the chain
  instance's org-unit scope. Audit: every decision (approve or reject) is an
  append-only entry (FR-11) recording the acting user, the role they acted as, the
  requirement fulfilled, the decision, a timestamp, and — when the action was
  performed under an active delegation per feature 002 — both the acting user and
  the delegator as distinct fields, consistent with feature 002's FR-21 precedent.
- **Invoke the "recalculate required approvers" action** (FR-9): restricted to a
  user holding a distinct administrative permission for this action; never
  self-service by an ordinary approver on their own chain instance, and never
  triggered automatically by the system (FR-9). Scoped to chain instances within
  the acting user's own organization. Audit: a distinct, append-only history entry
  per FR-10, recording actor, timestamp, and the full before/after
  required-approver list.
- **View a chain instance's materialized required-approver list, condition
  explanations, and history** (FR-5, FR-7, FR-10): visible to eligible approvers on
  that chain instance, the requester, and organization administrators, scoped
  strictly to the viewer's own organization; a chain instance's contents SHALL
  never be visible to a user outside its organization. Existing ethical-wall
  row-level restrictions on the underlying item (e.g. a contract under an ethical
  wall) continue to apply and override visibility exactly as they do today,
  unaffected by this feature.
- **View which required role has no eligible holder on a blocked step** (FR-20):
  restricted to a user holding the appropriate administrative permission, scoped to
  chain instances within their own organization only.
- **Resolve who is eligible for a required role at a given org-unit scope**
  (FR-18): not a user-facing action; performed by the request pipeline using the
  existing org-unit-scoped resolver, which itself already enforces strict
  same-organization scoping (feature 002) — this feature does not alter that
  scoping behavior.

## Acceptance criteria

Given/When/Then scenarios that define "done". Every criterion maps to at least one
functional requirement.

- AC-1 (FR-1, FR-5): Given a step configured with a base requirement of "Contract
  Reviewer" and a condition rule "contract_value > 1,000,000 requires Finance
  Approver," when a contract instance with contract_value = 1,200,000 enters that
  step, then the materialized required-approver list includes both the Contract
  Reviewer (base) and the Finance Approver (condition-triggered).
- AC-2 (FR-1, FR-5): Given the same step and condition rule as AC-1, when a contract
  instance with contract_value = 500,000 enters that step, then the materialized
  required-approver list includes only the Contract Reviewer; the Finance Approver
  requirement is not created.
- AC-3 (FR-2, FR-4): Given a condition rule definition containing an operator
  outside the fixed set (e.g. an attempt to embed an arbitrary expression or code
  string, or a boolean AND/OR combinator, in the condition), when the system
  evaluates that step's conditions, then the malformed condition is rejected/skipped
  (treated as not satisfied), no code execution occurs, and the step's other
  well-formed condition rules still evaluate normally.
- AC-4 (FR-4): Given a battery of malformed/adversarial condition_expression
  payloads (e.g. missing fields, wrong types, nested/recursive structures, unknown
  operators, extremely large values), when each is evaluated, then the evaluator
  fails closed on every case (rejects/ignores, never executes, never raises an
  unhandled exception that aborts materialization of the rest of the step).
- AC-5 (FR-3): Given a step with two separate single-condition rules — "contract_
  value > 1,000,000 requires Finance Approver" and "jurisdiction == EU requires
  Finance Approver" — both expressing halves of one intended compound business rule,
  when a chain instance whose data satisfies both rules enters that step, then
  materialization produces exactly one Finance Approver requirement (not two), and
  its displayed explanation references both fired conditions.
- AC-6 (FR-6, FR-7): Given a materialized Finance Approver requirement triggered by
  the condition in AC-1, when that requirement is displayed to the assigned
  approver or an auditor, then it shows "required because contract_value
  (1,200,000) > 1,000,000" (or equivalent, using the item's actual value) and links
  back to the specific condition rule that produced it.
- AC-7 (FR-8): Given a chain instance whose required-approver list was materialized
  with contract_value = 1,200,000 (triggering the Finance Approver requirement),
  when the underlying contract's value is later corrected to 500,000 without the
  recalculate action being invoked, then the materialized required-approver list is
  unchanged — the Finance Approver requirement remains.
- AC-8 (FR-9, FR-10): Given the chain instance in AC-7, when a user holding the
  recalculate permission explicitly invokes the recalculate action, then the
  required-approver list is re-evaluated against the corrected data (removing the
  now-unwarranted Finance Approver requirement per the condition), and a distinct
  append-only history entry is written recording the actor, timestamp, and the
  before/after required-approver list, visibly different in kind from a normal
  approval decision entry.
- AC-9 (FR-9): Given the chain instance in AC-7, when the underlying contract's
  value is corrected and no user ever invokes the recalculate action, then no
  recalculation occurs automatically — no system process, scheduled job, or
  data-change hook re-evaluates or updates the required-approver list on its own,
  no matter how much time passes.
- AC-10 (FR-11): Given any entry already written to a chain instance's history, when
  any code path (including an administrative one) attempts to edit or delete that
  entry, then the attempt is rejected — the only means of reflecting a correction is
  appending a new entry.
- AC-11 (FR-12, FR-13): Given a step configured as sequential with a Contract
  Reviewer at sequence position 1 and a Finance Approver at sequence position 2,
  when the Finance Approver attempts to record a decision before the Contract
  Reviewer has acted, then the action is rejected (or clearly flagged as
  out-of-order, per the chosen enforcement mode) rather than silently accepted as
  if in order.
- AC-12 (FR-14): Given a step configured as parallel with two required approvers,
  when either approver records their decision first, then the action is accepted
  regardless of order.
- AC-13 (FR-12): Given one approval chain with step A configured sequential and
  step B configured parallel, when the chain progresses through both steps, then
  step A enforces order (per AC-11) while step B does not (per AC-12), confirming
  the sequential/parallel setting is independently configurable per step rather
  than fixed for the whole chain.
- AC-14 (FR-16, FR-17): Given a step with two materialized required-approver
  requirements, when one approver rejects, then the step (and chain instance, per
  existing chain-progression behavior) is marked rejected without waiting for the
  second approver to act.
- AC-15 (FR-18): Given a condition rule requiring "Finance Approver" and a user who
  holds the Finance Approver role at a parent org unit of the chain instance's
  org-unit scope (with hierarchy rollup enabled per feature 002), when eligibility
  for that requirement is resolved, then that user is included as eligible,
  demonstrating reuse of feature 002's ancestor-walk resolution rather than a
  separate mechanism.
- AC-16 (FR-19): Given a condition-triggered requirement for a role with zero
  eligible users at the chain instance's org-unit scope, when the step's
  completion is evaluated, then the step is blocked from completing and the gap is
  flagged rather than the requirement being silently dropped.
- AC-17 (FR-20): Given a step blocked per AC-16 due to a required role with no
  eligible holder, when a user holding the appropriate administrative permission
  views the chain instance, then they can see specifically which required role has
  no eligible holder, without needing to consult a separate log or audit query.
- AC-18 (organization scoping): Given two organizations each with their own
  condition rules and chain instances, when a user from Organization A attempts to
  view, act on, or recalculate a chain instance belonging to Organization B, then
  the system denies it.
- AC-19 (audit, delegation): Given an approver acting under an active feature-002
  delegation records a decision on a required-approver requirement, when the
  resulting history entry is inspected, then it shows the acting user and the
  delegator as two distinct fields, consistent with feature 002's FR-21.
- AC-20 (FR-21): Given an `ApprovalRequest` already in flight (created under the
  legacy rule-based routing engine) at the moment this feature ships, when that
  request is subsequently decisioned to completion, then it proceeds entirely under
  the legacy mechanism with no condition-driven materialization applied to it; only
  approval chains created after this feature ships use the new condition-driven,
  materialized-chain behavior.
- AC-21 (FR-22): Given a contract submitted for approval for the first time through
  the platform's contract-approval-submission entry point after this feature ships,
  when the submission is processed, then it produces a materialized approval chain
  per this feature's mechanism (a fixed, traceable required-approver list per
  FR-5/FR-6, evaluated via this feature's condition rules per FR-1–FR-4) rather than
  a legacy-style flat `ApprovalRequest` chain with no condition evaluation,
  materialization, or origin-traceability.
- AC-22 (FR-22): Given an intake request submitted for approval for the first time
  through the platform's intake-request-approval-submission entry point after this
  feature ships, when the submission is processed, then it likewise produces a
  materialized approval chain per this feature's mechanism (a fixed, traceable
  required-approver list per FR-5/FR-6, evaluated via this feature's condition
  rules per FR-1–FR-4) rather than a legacy-style flat `ApprovalRequest` chain —
  confirming the reroute applies to intake-request submissions exactly as it does
  to contract submissions (AC-21), not to contracts only.

## Out of scope

- Any change to feature 002's org-unit hierarchy, grant, or delegation resolution
  algorithm (`app.core.org_access`) — reused as-is for eligible-approver resolution
  (FR-18), never modified.
- Any change to feature 003's screen-access resolution algorithm
  (`app.core.screen_access`) — this feature is about who must approve an item, not
  about menu/screen VIEW/ADD/EDIT/DELETE access; the two are unrelated axes.
- Any change to the existing `AuthorityGrant` mechanism (value/type/jurisdiction/
  risk-band-bounded authority for contract:approve and contract:sign) — a distinct,
  pre-existing "is this specific actor authorized to act" check that continues to
  apply independently of, and unmodified by, this feature's "which approvers does
  this instance require" logic. Both may gate the same contract-approval action
  without one replacing the other.
- Field-level or row-level data masking — that remains ethical walls' job, unaffected
  by and continuing to override this feature exactly as it does for features 002 and
  003.
- Per-user permission overrides layered on top of role-based access.
- Single sign-on / identity-provider integration.
- Rate limiting.
- Any general-purpose expression language, sandboxed code execution, or dynamic
  evaluation mechanism for conditions — explicitly rejected by FR-2/FR-4, not merely
  deferred.
- Any boolean combination (AND/OR) of multiple comparisons within a single condition
  rule, and any comparison operator beyond the fixed five in FR-2 — a compound
  business rule is expressed as multiple single-condition rules per FR-3, not as a
  richer condition language.
- Migrating or backfilling any pre-existing, in-flight `ApprovalRequest`/
  `ApprovalDecision` record into the new condition-driven model at cutover (FR-21) —
  those records finish under the legacy mechanism, untouched.
- The `ApprovalToken` email-link external-approval mechanism. It continues to work
  exactly as it does today for whatever it is currently wired to; this feature's
  condition-driven materialized approvals do not provide or require an email-link/
  external-approval path in this version. Extending external approval to
  condition-driven chains, if ever needed, is a separate future feature.
- Any automatic detection of, or prompting for, a "recalculate required approvers"
  action based on underlying data changes — recalculation is a purely manual,
  explicitly administrator-triggered action (FR-9), never system-initiated.
- Deciding, in this document, whether condition-driven approval chains are built by
  extending the existing `approvals` domain's schema or by introducing the ER
  diagram's separate schema — that is an architectural decision for plan.md (see
  "Context for planning"), not a requirement this spec imposes either way.

## Edge cases & error behavior

- A condition rule referencing an item field that does not exist on the item's data
  at evaluation time SHALL be treated as not satisfied (fails closed), not as an
  error that blocks materialization of the rest of the step.
- A step with zero condition rules (base requirements only) SHALL materialize just
  the base requirements — condition evaluation is additive, never a precondition for
  the base requirements to apply.
- Two condition rules that both evaluate true and both require the same role at the
  same sequence position SHALL result in a single materialized requirement for that
  role/position, not a duplicate (per FR-3; the human-readable explanation, per
  FR-7, SHALL reference all rules that fired for it, not just one arbitrarily).
- Recalculation (FR-9) invoked on a chain instance where no approver has yet acted
  SHALL be permitted and behaves the same as materialization at entry, just
  re-triggered explicitly.
- Recalculation invoked on a chain instance where some approvers have already
  acted: an already-recorded approval/rejection decision is never retroactively
  invalidated by recalculation; if recalculation removes a requirement that already
  received a decision, that decision remains in history (append-only, FR-11) but is
  no longer counted toward step completion; if recalculation adds a new requirement,
  the step is not considered complete until it too receives a decision.
- Attempting to record a decision against a required-approver requirement that has
  already received a decision SHALL be rejected (no double-decisioning).
- An approver who is eligible for a required role at chain-instance materialization
  time but loses that eligibility (e.g. role grant revoked) before acting SHALL be
  re-checked for eligibility at the moment they attempt to act, consistent with
  feature 002's resolver always evaluating fresh — a stale eligibility snapshot from
  materialization time SHALL NOT itself authorize the decision.
- A malformed condition rule that fails closed (FR-4) on a step where it is the
  ONLY source of a given role requirement SHALL NOT be treated as satisfying that
  requirement — "fails closed" means the condition is treated as not true, which for
  an additive requirement means it is simply not added, not that some default
  approver is substituted.
- An `ApprovalRequest` created before this feature ships, still pending at cutover,
  SHALL NOT be retroactively subjected to condition evaluation, materialization, or
  the append-only history requirement — it remains governed entirely by the legacy
  mechanism's existing behavior (FR-21).
- A step blocked per FR-19 (no eligible approver for a required role) remains
  blocked indefinitely until the underlying gap is resolved by whatever mechanism
  plan.md defines — this spec does not require or assume any automatic timeout,
  escalation, or default resolution.

---

### Context for planning (non-normative)

The following implementation-adjacent facts and open architectural questions from
the reviewed source material and the verified current codebase are recorded here
only to save the architect re-deriving them; they are not requirements of this spec
and carry no normative weight. **The central open question for plan.md is how this
feature's behavioral requirements (FR-1–FR-22) get reconciled with the two existing,
structurally different approval-adjacent domains already in the codebase** — this
spec deliberately does not resolve it.

- **Reroute decision (FR-22) — WHETHER is settled for both subject types, HOW is
  not.** During plan.md's first pass, the architect surfaced that the initial
  design let the new condition-driven engine coexist alongside the legacy
  `app/approvals/` engine untouched, reachable only via a new, separate, dedicated
  endpoint — i.e. nothing about submitting a new contract (or intake request) for
  approval today would actually start using this feature. The user was asked
  explicitly whether that coexistence-with-nothing-rerouted design was acceptable
  and said **no**. In a follow-up decision, the user was also asked whether the
  reroute should cover contracts only or both subject types the shared entry point
  already serves, and confirmed **both**: new contract-approval submissions (via
  `POST /approvals/requests` → `submit_contract_for_approval` →
  `submit_subject_for_approval` in `app.approvals.service`) AND new
  intake-request-approval submissions (via `submit_request_for_approval` in
  `app.intake.approval_bridge`, which also calls the shared
  `submit_subject_for_approval` — both entry points verified as read) MUST use this
  feature's mechanism going forward (FR-22). This WHETHER is now fully settled for
  both subject types, not an open question. What remains open for plan.md to
  decide is the HOW: e.g. whether existing `ApprovalRoutingRule`/
  `ApprovalRoutingStep` configuration is translated automatically into
  chain-definition data (base requirements + condition rules) for each subject
  type, whether contracts and intake requests share one chain-definition concept
  or have their own, whether org administrators must separately (re)define chain
  definitions before cutover, and what happens for an org that has routing rules
  but no chain definition yet configured at the moment this feature ships.
  **Consequence for the condition evaluator's fact surface:** because intake
  requests are now in scope alongside contracts, the condition rules' "named field
  of the item's data" (FR-2/FR-3) must be able to reference intake-request fields,
  not only contract fields — the architect should read the actual `IntakeRequest`
  model (`app/intake/models.py`) to determine the real, concrete fact names
  available for intake-request condition rules (e.g. whatever it tracks — this
  spec does not enumerate them), mirroring how contract fields like
  `contract_value` are already used as the worked example throughout this spec.
  This expanded fact-mapping surface (two subject types' fields, not one) is
  something plan.md's condition-rule/field-registry design must account for.

- **`backend/app/approvals/` (verified as read) is a rule-based routing engine, not
  greenfield.** `ApprovalRoutingRule` (criteria JSON, priority, legacy single
  approver_role/approver_user_id fallback) has an ordered `ApprovalRoutingStep`
  list (rule_id FK, step_order, approver_group_id/approver_user_id/approver_role,
  mode any|all). `ApprovalRequest` (contract_id/intake_request_id/
  contract_version_id — exactly one, requested_by_user_id, approver_user_id/
  approver_group_id/approver_role, routing_rule_id, step_order, mode, status,
  due_at, metadata_json JSON) represents one materialized rung — one row is created
  per step at submission time, with only step 1 starting PENDING and the rest
  WAITING, activated in order. `ApprovalDecision` (approval_request_id,
  approver_user_id, decision, comment, decided_at) is a flat decision log, not an
  append-only-by-schema-design table (no explicit "no update/delete" enforcement
  observed at the model level, unlike the ER diagram's `workflow_instance_history`).
  `ApprovalToken` supports email-link external approval independent of an
  authenticated session — confirmed out of scope for this feature (FR-21's cutover
  boundary keeps it serving only the legacy path it already serves). This engine
  already does rule-matched routing, step-ordering (`step_order` + any/all `mode`),
  and per-step materialization at submission time — it is conceptually close to but
  not identical to what FR-1–FR-20 ask for; it currently has no condition-JSONB-
  triggered *additional* approver concept layered onto a base requirement, and no
  explicit append-only history table or "why was this approver required"
  traceability field. Per FR-22, this engine can no longer simply be left alone
  running in parallel forever for contracts — new contract submissions must produce
  a materialized chain per this feature, so whatever plan.md builds must actually
  intercept/replace the contract path through `submit_contract_for_approval`, not
  merely add a second, separately-reachable engine beside it.
- **`backend/app/workflows/` (verified as read) is a distinct automation-flow
  engine — confirmed NOT the same concept as the ER diagram's
  `workflow_definitions`.** `Workflow` (criteria JSON matcher + an ordered JSON
  `steps` list of `{id, type, name, config}`, `STEP_TYPES` including `ai_task`,
  `human_task`, `clm_draft`, `approval`, `signature`, `counterparty`, `notify`) is a
  Zapier-style flow builder; `WorkflowRun`/`WorkflowStepRun` execute one instance of
  it. Its `approval` step type presumably defers to `app.approvals` for the actual
  approval mechanics rather than reimplementing them (not independently confirmed
  from `models.py` alone — the architect should trace the `approval` step handler in
  `workflows/service.py` to confirm). This module's naming overlap with the ER
  diagram's "workflow_definitions/workflow_steps/workflow_instances" is a source of
  confusion to actively avoid, not a hint that they should merge.
- **The two reconciliation paths, as originally framed, both remain open:** (a)
  extend `app.approvals`' existing schema — add a JSONB condition-rule concept
  hanging off `ApprovalRoutingStep` (or a new sibling table), a materialized-
  snapshot marker and origin-traceability field on `ApprovalRequest`, and an
  append-only history table alongside the existing flat `ApprovalDecision` — versus
  (b) adopt the ER diagram's separate schema (`workflow_definitions`,
  `workflow_steps`, `workflow_step_roles`, `workflow_step_approval_rules`,
  `workflow_instances`, `workflow_instance_approvals`, `workflow_instance_history`)
  as new tables, and separately decide how/whether it interoperates with or replaces
  `app.approvals` for contract/intake approval routing. Plan.md must choose and
  justify one (or an explicit hybrid), informed by: `ApprovalToken` remaining
  untouched/legacy-only (FR-21, confirmed), no migration of in-flight
  `ApprovalRequest` rows being required (FR-21, confirmed), and the `workflows`
  domain's existing `approval` step type's current integration point.
- **Eligible-approver resolution precedent:** `backend/app/core/org_access.py`'s
  `resolve_access(db, *, user, permission, org_unit_id, ...)` resolves whether a
  specific user holds a permission at an org-unit scope (ancestor-walk + expiry +
  delegation), and `active_grants_for_user`/`ancestor_unit_ids` are its composable
  primitives. `backend/app/core/screen_access.py`'s `resolve_screen_access`
  demonstrates the established pattern for reusing those primitives for a
  *different* axis (screen action level) without modifying `org_access.py` itself
  (screen_access.py imports `org_access.ancestor_unit_ids` and
  `org_access.active_grants_for_user` directly rather than importing
  `resolve_access`). FR-18's "which users are eligible for role X at org-unit Y"
  question is the inverse direction from `resolve_access` (which answers "does user
  U qualify," not "list all qualifying users") — plan.md needs a query that answers
  "all users holding an active grant of role X at org unit Y or an ancestor with
  rollup allowed," most naturally implemented as a new function in or alongside
  `org_access.py` (or a sibling module, following `screen_access.py`'s
  reuse-without-modification precedent) rather than a per-call loop over
  `resolve_access`. That same "list eligible users" query is also what FR-19's
  zero-eligible-approver detection at materialization time needs to call.
- **`backend/app/authority/models.py`'s `AuthorityGrant`** (verified as read) gates
  `contract:approve`/`contract:sign` by value/type/jurisdiction/risk-band limits,
  with its own `delegated_by_user_id` field for temporary authority handoff. It
  answers "is this specific actor authorized to take this action at all," which is
  adjacent to but distinct from this feature's "which approvers does this instance
  require" — both may need to be satisfied for the same real-world contract
  approval, but neither replaces the other. Confirmed out of scope; not to be
  modified.
- Source ER diagram entity names for reference (not binding if plan.md chooses path
  (a) above): `workflow_definitions` (module, name/version), `workflow_steps`
  (workflow_definition_id FK, step_key/sequence_order, step_type action|approval),
  `workflow_step_roles` (step_id FK, role_id FK, org_unit_scope_strategy),
  `workflow_step_approval_rules` (step_id FK, condition_expression jsonb,
  required_role_id FK, sequence_order, is_base_requirement), `workflow_instances`
  (workflow_definition_id FK, module_record_id uuid, current_step_id FK, org_unit_id
  FK, status), `workflow_instance_approvals` (instance_id/step_id FK, required_role_id
  FK, sequence_order, triggered_by_rule_id FK nullable, status pending|approved|...,
  acted_by_user_id FK), `workflow_instance_history` (instance_id/step_id FK,
  acted_by_user_id FK, acted_as_role_id FK, delegated_from_user_id FK nullable,
  action approved|rejected|..., acted_at/comments — explicitly append-only by
  design, no updated/deleted columns at all). Per FR-3, `workflow_step_approval_
  rules.condition_expression` (or its equivalent under path (a)) holds exactly one
  comparison per row — a compound rule is multiple rows, not a nested boolean tree.
- The security spec's own framing of this section's satisfied requirement:
  "audit defensibility of *why* a given set of approvers was required" — this is
  the throughline FR-6/FR-7/FR-10 exist to satisfy regardless of which schema path
  plan.md picks.
- This is the final feature (4 of 4) of the initiative; no feature 5 follows. After
  this feature, per the user's memory notes, the four-item security plan (RBAC
  re-enable, org-hierarchy/RBAC resolver, menu/screen security, approval-chain
  reconciliation) is complete.
