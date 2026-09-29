# Feature Specification: [FEATURE NAME]

Feature ID: [NNN-slug]
Created: [DATE]
Status: DRAFT   <!-- DRAFT | APPROVED — only the user approves -->

## Summary

[2–4 sentences: what this feature is and why it's being built. Written for a
stakeholder, not a developer — no implementation details in this document.]

## User stories

- As a [role], I want [capability], so that [benefit].
- ...

## Functional requirements

Numbered, testable statements. Each must be verifiable by /verify.

- FR-1: The system SHALL ...
- FR-2: WHEN [condition], the system SHALL ...
- FR-3: [NEEDS CLARIFICATION: open question for the user]

## Permissions, scoping & audit

This platform is multi-tenant with role-based access, ethical walls, and an
immutable audit trail. Pin down, per action in this feature:

- Who may perform it (role / permission concept — in product terms, not code).
- What data it may see (own org only; any finer-grained visibility rules).
- What must be recorded in the audit trail.

## Acceptance criteria

Given/When/Then scenarios that define "done". Every criterion maps to at least one
functional requirement.

- AC-1 (FR-1): Given ..., when ..., then ...
- AC-2 (FR-2): Given ..., when ..., then ...

## Out of scope

- [Explicitly excluded behavior, to prevent scope creep during implementation.]

## Edge cases & error behavior

- [Empty states, invalid input, concurrency, permissions, limits.]

## Open questions

- [ ] [NEEDS CLARIFICATION: ...] — remove this section when empty; spec cannot be
  APPROVED while any [NEEDS CLARIFICATION] marker remains.
