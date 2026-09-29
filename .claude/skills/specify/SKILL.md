---
name: specify
description: Phase 1 of the spec-driven workflow. Turn a feature idea into specs/NNN-slug/spec.md via the spec-analyst agent, resolve clarifications with the user, and get the spec approved.
---

# /specify — create a feature specification

Input: the user's feature idea, passed as arguments (e.g. `/specify renewal reminders by email`).
If no idea was given, ask for one.

## Procedure

1. **Determine the feature ID.** List `specs/` (create it if missing). The new ID is
   the next zero-padded number: existing `001-`, `002-` → new `003-<slug>`, where
   slug is 2–4 kebab-case words from the idea.
2. **Create** `specs/NNN-slug/`.
3. **Spawn the `spec-analyst` agent** (foreground) with: the feature ID and directory,
   the user's idea verbatim, any extra context the user provided, and the instruction
   to follow `.claude/templates/spec-template.md`.
4. **Resolve clarifications.** Present the analyst's `[NEEDS CLARIFICATION]` questions
   to the user (AskUserQuestion works well). Update spec.md with the answers —
   directly for simple substitutions, or via the spec-analyst for structural changes.
   Repeat until no markers remain.
5. **Request approval.** Show the user a concise summary of the spec (stories,
   FR count, acceptance criteria, out-of-scope) and the file link. Ask them to review.
   Only on an explicit yes, change the header to `Status: APPROVED`.
6. **Tell the user the next step is `/plan`.**

## Rules

- Never approve the spec yourself; never skip the clarification step by guessing.
- The spec must contain zero implementation detail — if the user's idea includes
  tech choices, note them in a "Context for planning" footnote, not in requirements.
- Permissions, org scoping, and audit expectations are requirements in this product,
  not implementation detail — make sure the spec pins them down.
