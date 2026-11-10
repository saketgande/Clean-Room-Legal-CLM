# Feature Specification: NDA already in force check at intake

Feature ID: 012-nda-in-force-check
Created: 2026-10-27
Status: SUPERSEDED   <!-- DRAFT | APPROVED | SUPERSEDED — only the user approves -->
Superseded: the NDA check was removed (migration 0079_drop_nda_check drops intake_request.nda_check);
the counterparty card already lists their contracts. Kept for history only.

## Summary

When someone files a new NDA request and picks the counterparty, Aegis should tell them
straight away if an NDA with that counterparty is already in force, or is already being
negotiated. It shows whether it is mutual or one-way (when known), when it ends, and a link
to it, so the requester can stop before filing a duplicate. The lawyer who picks up the
request sees the same answer on the request page. It is a plain look-up, with no AI, and it
only ever uses contracts the viewer is allowed to see. It never blocks filing: the requester
can cancel, or carry on. This replaces the weak "NDA on file" count that today's
counterparty note gives, which counts by title and ignores whether the NDA is still in force.

## User stories

- As a requester filing an NDA, I want to be told when an NDA with this counterparty is
  already in force, so that I do not file a request that is not needed.
- As a requester, I want to see its kind (mutual or one-way), end date and a link, so that I
  can check it covers what I need.
- As a requester, I want to still be able to carry on if the existing NDA does not fit, so
  that a warning never stops my work.
- As a lawyer handling the request, I want to see the same check on the request page, and
  whether the requester chose to carry on, so that I know the duplicate risk was considered.
- As any viewer, I want to see only NDAs I am allowed to see, so that the check never
  reveals a contract I could not open.
- As an administrator, I want the requester's decision recorded, so that there is a trail.

## Functional requirements

### When the check runs

- FR-1: WHEN a requester on the new-agreement form has chosen the agreement type NDA and has
  named a counterparty, the system SHALL check whether an NDA with that counterparty is in
  force or in progress and show the result on the form, before the request is filed.
- FR-2: WHEN the counterparty or the agreement type is changed, the result SHALL update to
  match. WHEN the agreement type is not NDA, or no counterparty is named, no result SHALL be
  shown.
- FR-3: The check SHALL be a look-up on existing contract records only. It SHALL NOT use AI
  and SHALL NOT depend on any AI result.
- FR-4: The check SHALL NOT block, delay or change the filing, routing or triage of the
  request in any way. Filing SHALL work the same whether or not a result was shown, seen or
  answered, and if the check fails to load.

### What counts

- FR-5: An NDA SHALL count as "in force" when it is an NDA, it has reached the active
  stage, it is not archived, and it has not passed its end date (compared with today's date
  as the app uses it elsewhere). An NDA with no end date recorded SHALL still count as in
  force and SHALL be shown as "no end date recorded".
- FR-6: An NDA that is still being drafted, reviewed, approved or signed (not yet active) and
  is not archived SHALL be shown separately as "in progress, not yet in force". It SHALL NOT
  be described as in force.
- FR-7: An NDA that is closed, archived or past its end date SHALL NOT be shown as in force
  or in progress.
- FR-8: A contract SHALL be recognised as an NDA by its contract type only, treating every
  NDA spelling as NDA (for example "NDA", "Mutual Non-Disclosure Agreement", "Mutual
  Non-Disclosure Agreement (MNDA)"). The title and other names SHALL NOT be used. A contract
  with a blank or unrecognised type SHALL NOT be treated as an NDA; FR-34 to FR-40 make sure
  types are filled in so such contracts are not missed.
- FR-9: A contract that is not an NDA but contains confidentiality terms SHALL NOT count as
  "an NDA is in force". Only live MSAs get the separate, weaker hint in FR-29 to FR-33. No
  other agreement type is checked.
- FR-10: WHEN the requester picked the counterparty from the register, the system SHALL
  match contracts linked to that counterparty plus contracts with the same name that are
  not linked. WHEN the requester typed a free-text name not in the register, the system
  SHALL match by name only (using the app's existing name-matching rules) and SHALL say
  that the match was made by name.

### What is shown

- FR-11: For each NDA found, the system SHALL show: its title, whether it is mutual or
  one-way, its end date (or "no end date recorded"), its stage (in force or in progress),
  and a link that opens it.
- FR-12: WHEN mutual or one-way is not known for an NDA (it is not recorded on the contract,
  for example an uploaded NDA), the system SHALL show "Type not recorded" and SHALL NOT guess.
- FR-13: For a one-way NDA, WHERE the direction is known (who discloses to whom), the system
  SHALL show it, so the requester can see if it matches the direction they need. Where it is
  not known, it SHALL say so. Deciding whether the NDA fits is left to the requester; the
  system SHALL NOT say that an NDA "covers" or "does not cover" the new purpose.
- FR-14: WHEN several NDAs are in force, all SHALL be listed, soonest end date first, with
  no-end-date ones after the dated ones.
- FR-15: An in-force NDA whose end date is within 60 days of today SHALL be flagged "ending
  soon", shown with the date and a plain "ends in N days" note.
- FR-16: WHEN the viewer can see no in-force and no in-progress NDA for the counterparty,
  the form SHALL show a quiet "No NDA in force found" line (or nothing). It SHALL NOT claim
  that none exists anywhere, and SHALL NOT hint that something hidden may exist.

### What the requester can do

- FR-17: WHEN at least one in-force or in-progress NDA is shown, the requester SHALL be able
  to (a) cancel the request, or (b) continue filing. Continuing SHALL always be possible.
- FR-18: Cancelling SHALL leave the requester without a filed request and SHALL NOT create a
  request, a contract, an audit entry or any other record.
- FR-19: WHEN the requester continues, the system SHALL offer an OPTIONAL short reason (for
  example "different purpose"). Leaving it empty SHALL NOT block or slow filing.
- FR-20: WHEN a request is filed after a result was shown, the request SHALL keep: that a
  result was shown, which NDAs were listed (as the filing viewer saw them), and the
  requester's choice (and reason, if given).

### On the request page (lawyer view)

- FR-21: The request page for an NDA request SHALL show the check as a section for staff
  and for the requester, listing the NDAs found for the counterparty, with the same details
  and links as FR-11 to FR-14.
- FR-22: The request page result SHALL be worked out again for each viewer when the page is
  opened, using what that viewer may see now. It SHALL NOT replay the list the requester
  saw at filing time.
- FR-23: IF the requester continued after being warned, the page SHALL show that fact and
  the reason, when one was given, to staff who may see the request. The earlier list shown to the
  requester SHALL appear only as far as the current viewer may see those NDAs.
- FR-24: The existing counterparty note on the request SHALL stop saying "NDA on file" based
  on title words. WHERE it still mentions NDAs, it SHALL use the same in-force rule as
  FR-5 to FR-7 and the same viewer-visible set as FR-25.

### Visibility

- FR-25: The result (the list, any count, any wording, and the choice to warn at all) SHALL
  be built only from contracts the viewer may see under the app's normal contract rules:
  ethical walls (which bind everyone, including administrators), clearance and
  confidentiality level, access by ownership, creation, grant or administrator role, and
  the right to read contracts.
- FR-26: A viewer SHALL NOT be able to tell that an NDA exists which they may not see. This
  covers the list, counts, dates, wording, timing differences visible on screen, the
  request's saved record, the audit trail view, and error messages. A viewer who may not see
  the only NDA SHALL get exactly the same answer as when none exists.
- FR-27: Every listed link SHALL open for the viewer who sees it. A listed NDA never opens
  to a refusal.
- FR-28: Only contracts of the viewer's own organisation SHALL be considered.

### Weaker hint: live MSAs

- FR-29: WHEN the viewer can see one or more MSAs with the counterparty that are in force
  (same rule as FR-5: active, not archived, not past end date), the system SHALL list them
  in a separate, clearly weaker hint, labelled along the lines of "has confidentiality
  terms, check whether it covers this". Each shows title, end date (or "no end date
  recorded") and a link.
- FR-30: A live MSA SHALL NEVER be described as, or counted as, "an NDA is in force". The
  hint SHALL be visibly different from the NDA result and SHALL NOT claim that the MSA
  covers the need.
- FR-31: MSAs are recognised by contract type (every MSA spelling, such as "MSA" or "Master
  Services Agreement") only, never by title, as in FR-8. Counterparty matching follows
  FR-10.
- FR-32: MSAs SHALL follow exactly the same visibility rules as NDAs (FR-25 to FR-28). An
  MSA the viewer may not see SHALL leave no trace: the viewer SHALL get the same answer as
  if it did not exist.
- FR-33: WHEN both an in-force NDA and a live MSA are visible, both SHALL be shown, the NDA
  result first. WHEN only an MSA is visible, the form SHALL show the MSA hint and no
  "NDA in force" statement. The hint applies on the form and on the request page like the
  NDA result, never blocks filing, and does not change what is recorded at filing beyond
  listing what was shown.

### Contract types filled in and cleaned up

Because FR-8 and FR-31 use the contract type only, a contract whose type is blank or
unrecognised would be missed. These requirements keep types filled in.

- FR-34: A contract drafted from a request SHALL get its contract type from the request's
  form answer (for example the agreement type NDA) when it is created. It SHALL NOT be left
  blank.
- FR-35: WHEN an uploaded contract ends up with a blank or unrecognised type after the
  usual reading of the document, the contract SHALL be marked "type needed" and the contract
  page SHALL ask, to anyone allowed to edit the contract's details, for the type to be set.
  A type the document reading suggests MAY be offered as a starting choice; a person
  confirms it.
- FR-36: Setting the type SHALL be a choice from a fixed list of contract types (covering at
  least NDA, MSA, SOW, SaaS / software, vendor, consultancy, DPA, and other), so new
  spellings are not created. "Other" SHALL remain allowed.
- FR-37: The system SHALL give staff a list of existing contracts that are not closed or
  archived and whose type is blank or unrecognised (including "other" when the title
  suggests a known type), within their own
  organisation and only contracts they may see (FR-25 to FR-28), so they can set each type.
  Each row SHALL show the title, counterparty, stage and a link, and allow setting the type
  from the fixed list.
- FR-38: Existing NDA spellings (for example "Mutual Non-Disclosure Agreement (MNDA)") SHALL
  keep being recognised as NDA (FR-8); cleaning them up to one spelling is allowed but not
  required.
- FR-39: Every change of a contract's type SHALL write an audit entry and a visible history
  entry on the contract: who, when, old type, new type.
- FR-40: Changing a contract's type SHALL NOT change its stage, approvals, workflow or any
  other field. It only changes what the NDA check (and the type list) sees.

## Permissions, scoping & audit

- Who may see the check: any signed-in person who may file a new-agreement request (on the
  form), and any person who may see the request (on the request page). Seeing it needs no
  special right beyond the right to read contracts; a person without the right to read
  contracts SHALL see the "No NDA in force found" result (FR-16, FR-26) and nothing else,
  and still can file.
- Who may choose cancel or continue: the person filing the request. Nobody else makes this
  choice.
- Who may see the recorded choice and reason (FR-23): staff and owners who may see the
  request, and the requester.
- Data scoping: own organisation only (FR-28). Within it, each viewer sees only the NDAs
  they may see (FR-25). Two viewers of the same request can see different results; that is
  expected.
- Audit trail: when a request is filed after a result was shown, the audit trail and the
  request's visible history SHALL record that a result was shown, what was listed (NDAs and
  MSAs), the requester's choice (continue) and the reason if given. A cancelled attempt
  that never became a request SHALL NOT be recorded anywhere. The record SHALL NOT contain
  details of contracts that the person who made the choice could not see. Merely viewing the check on the form or the
  request page SHALL write no audit entry.
- Who may set a contract's type (FR-35, FR-37): anyone allowed to edit that contract's
  details under the app's normal rules. The "type needed" list shows only contracts the
  viewer may see. Every type change is audited (FR-39).

## Acceptance criteria

- AC-1 (FR-1, FR-2): Given a requester on the new-agreement form, when they choose NDA and
  pick a counterparty with a visible in-force NDA, then the result appears before filing;
  and when they change the counterparty or the type to something else, then the result
  updates or disappears.
- AC-2 (FR-3, FR-4): Given the check cannot load, when the requester files, then filing
  works as usual and no error blocks it.
- AC-3 (FR-5): Given an active, unarchived NDA with an end date in the future, when the
  check runs, then it is shown as in force. Given an active NDA with no end date, then it is
  shown as in force with "no end date recorded".
- AC-4 (FR-7): Given a closed NDA, an archived NDA, and an active NDA whose end date was
  yesterday, when the check runs, then none appears as in force or in progress.
- AC-5 (FR-6): Given an NDA in review for the same counterparty, when the check runs, then
  it appears as "in progress, not yet in force", separately from any in-force NDA.
- AC-6 (FR-11): Given an in-force NDA, when the result is read, then it shows title, kind,
  end date, stage and a working link.
- AC-7 (FR-12, FR-13): Given an uploaded NDA with no recorded kind, when the result is read,
  then it shows "Type not recorded". Given a one-way NDA with a known direction, then the
  direction is shown and no statement is made that it covers or does not cover the new
  purpose.
- AC-8 (FR-14): Given three in-force NDAs, when the result is read, then all three are
  listed with the soonest end date first and the no-end-date one last.
- AC-9 (FR-15): Given an in-force NDA ending within 60 days, when the result is
  read, then it is marked as ending soon with the number of days left.
- AC-10 (FR-16, FR-26): Given a counterparty whose only NDA is behind an ethical wall for the
  viewer, when the viewer reads the form and the request page, then they see exactly what
  they would see for a counterparty with no NDA, with no count, date, name or hint.
- AC-11 (FR-17, FR-18): Given a result is shown, when the requester cancels, then nothing is
  filed; and when they choose to continue, then they can file.
- AC-12 (FR-19, FR-20): Given a result is shown, when the requester continues with or
  without a reason, then the request is filed and keeps their choice (and reason, if given)
  and the NDAs and MSAs they were shown.
- AC-13 (FR-21, FR-22): Given a filed NDA request, when the assigned lawyer opens it, then
  the check is shown, worked out for the lawyer at that moment. Given the NDA ended after
  filing, then it no longer appears as in force.
- AC-14 (FR-23, FR-26): Given the requester continued after a warning that included an NDA
  the lawyer may not see, when the lawyer opens the request, then the lawyer sees that the
  requester continued, and the reason, but nothing about the NDA they may not see.
- AC-15 (FR-24): Given a counterparty with a past NDA only (ended), when the counterparty
  note is read, then it does not report an NDA on file as if in force.
- AC-16 (FR-25, FR-28): Given NDAs in another organisation with the same counterparty name,
  when the check runs, then they are never shown or counted.
- AC-17 (FR-27): Given any link in the result, when clicked, then it opens that NDA.
- AC-18 (FR-8): Given contracts typed "NDA" and "Mutual Non-Disclosure Agreement (MNDA)",
  when the check runs, then both are recognised as NDAs; and given a contract with a blank
  type titled "Non-Disclosure Agreement", or typed "Services Agreement" with "NDA" in its
  title, then it is not treated as an NDA.
- AC-19 (Audit): Given the form or request page is only viewed, then no audit entry is
  written; given a requester files after a result, then exactly one entry records the choice
  (and reason), with no details of NDAs they could not see.
- AC-20 (FR-9, FR-30): Given a live MSA with the counterparty and no NDA, when the check
  runs, then no "NDA in force" statement appears.
- AC-21 (FR-29, FR-31): Given a live MSA, when the result is read, then it appears in a
  separate weaker hint with title, end date and a working link, labelled as having
  confidentiality terms to check; and an ended or archived MSA does not appear.
- AC-22 (FR-33): Given both an in-force NDA and a live MSA, when the result is read, then
  both show, the NDA result first and the MSA in the weaker hint.
- AC-23 (FR-32): Given a live MSA behind an ethical wall for the viewer and no visible NDA,
  when the viewer reads the form and the request page, then they see exactly what they
  would see with no NDA and no MSA.
- AC-24 (FR-10): Given a counterparty picked from the register, then contracts linked to it
  and same-name unlinked contracts are found; given a typed free-text name, then contracts
  are matched by name only and the result says so.
- AC-25 (FR-15): Given an in-force NDA ending in 59 days and another ending in 61 days, then
  only the first is flagged "ending soon".
- AC-26 (FR-18, FR-19): Given a result is shown, when the requester cancels, then no audit
  entry or other record exists; and when they continue with the reason left empty, then
  filing proceeds without delay.
- AC-27 (FR-34): Given an NDA request is drafted into a contract, then the contract's type
  is NDA, never blank.
- AC-28 (FR-35, FR-36): Given an uploaded contract whose type comes out blank, then it is
  marked "type needed", the contract page asks an editor to set it from the fixed list, and
  once set to NDA it appears in the NDA check.
- AC-29 (FR-37): Given existing contracts with a blank type, then they appear on the "type
  needed" list for staff who may see them, and a contract behind a wall for the viewer does
  not appear.
- AC-30 (FR-39, FR-40): Given an editor changes a contract's type from blank to NDA, then one
  audit entry and one history entry record who, when, old and new type, and nothing else on
  the contract changes.
- AC-31 (FR-38): Given a contract typed "Mutual Non-Disclosure Agreement (MNDA)" that is not
  cleaned up, then it is still recognised as an NDA.

## Out of scope

- Blocking, delaying or auto-rejecting any request, or changing routing, triage or
  approvals because an NDA exists.
- Using AI to read NDA text, to judge whether an NDA covers a new purpose, or to compare
  purposes.
- Automatically linking the new request to the existing NDA, renewing it, extending it or
  amending it from this check.
- Any check on agreement types other than NDA and, as a weaker hint only, MSA. Other
  agreements with confidentiality terms (SOW, vendor, SaaS and so on) are not checked.
- Changing existing contract types automatically without a person confirming (FR-35 to
  FR-37 always have a person set the type).
- Adding a new mutual/one-way field to uploaded contracts.
- Sanctions, conflict or other counterparty screening.
- Changing who can see which contracts.
- Showing this check to people who cannot file or see the request.

## Edge cases & error behavior

- Requester cannot see the existing NDA (wall, clearance, no right to read contracts): they
  get the "none found" result, with no trace of it anywhere (FR-26).
- Several NDAs in force: all are listed (FR-14).
- NDA ending soon: flagged by the agreed window (FR-15).
- NDA with no end date: shown as in force, "no end date recorded" (FR-5).
- NDA being negotiated: shown separately, not as in force (FR-6).
- MSA in force and no NDA: only the weaker hint shows (FR-30, FR-33).
- MSA and NDA both present: both shown, NDA first (FR-33).
- MSA hidden by a wall: no trace (FR-32).
- MSA still being negotiated, ended or archived: not shown.
- Free-text counterparty not in the register: matched by name only; the result says so
  (FR-10).
- One-way NDA in the wrong direction: shown with its direction; the requester decides
  (FR-13).
- NDA that may not cover the new purpose: not judged by the system (FR-13).
- Counterparty with similar names (for example "Umbrella Corp" and "Umbrella Corporation"):
  matched per FR-10.
- The request being filed is itself an NDA contract in progress for the same counterparty:
  it is never listed against itself on the request page.
- The check fails to load: a one-line note, filing continues (FR-4).
- Visibility changes while the page is open: the next refresh reflects it (FR-22).
- A typed form answer is never lost when the result appears, updates or is dismissed.
- Withdrawn or cancelled earlier requests are not contracts and are not part of this check.
- An NDA whose type is still blank (not yet cleaned up) is not found by the check. This is a
  known gap until it is set on the "type needed" list (FR-37).
- A contract typed "other" whose title says NDA: not found by the check; it is listed on the
  "type needed" list so staff can correct it (FR-37).

## Open questions

None open. Decisions recorded:

- [x] FR-8: NDA recognised by contract type only (every NDA spelling); titles never used. Changed on 2026-10-27 from "title when type is blank" at the user's request, with type fill-in and cleanup added (FR-34 to FR-40).
- [x] FR-9: Non-NDA agreements never count as "NDA in force". Live MSAs only get a separate weaker hint (FR-29 to FR-33).
- [x] FR-10: Register pick matches linked plus same-name unlinked contracts; free text matches by name only and says so.
- [x] FR-15: "Ending soon" means within 60 days.
- [x] FR-19: Reason is optional, shown to the lawyer when given, never slows filing.
- [x] Audit: a cancelled attempt that never became a request is not recorded.
- [x] Also confirmed: in-progress NDAs shown separately; system never judges purpose or direction fit; soonest end date first, no-end-date last.

## Context for planning

Non-binding notes for the architect; not requirements.

- `app/intake/screening.py`, `visible_counterparty(user, request)`, already works out at
  read time what one viewer may see of the primary counterparty, using
  `accessible_contract_filter(user)` and `contract:read`, matching by fuzzy name. It counts
  NDAs by "nda"/"non-disclosure" in the title and does not check in-force. This feature
  likely extends or sits beside it. The stored screening note is written at filing time and
  must not be the source of the page result (FR-22, FR-26).
- Contract fields available: `contract_type` (free string), `lifecycle_stage`, `archived`,
  `counterparty_name`, `counterparty_id`, `expiration_date` (nullable). "Ended" as used by
  the app today is closed stage, archived, or end date before today (UTC; see
  `tests/test_one_today.py`).
- Dev data: 20 contracts typed "NDA" (19 with an end date, 17 linked to the register), plus
  "Mutual Non-Disclosure Agreement (MNDA)" and "Mutual Non-Disclosure Agreement" types and
  blanks. Measured 2026-10-27: 22 contracts typed as an NDA spelling; 9 blank-type
  contracts, 5 of them titled as NDAs; 2 typed "other", both titled as NDAs. Those 7 are what
  the "type needed" list (FR-37) must surface on day one. Contract type is filled today by
  metadata extraction on upload and by drafting; FR-34 needs the drafting path checked.
- Mutual versus one-way is not a contract field. It is the form answer `nda_kind`
  (Mutual / One-way), with `nda_direction`, on the request, and in the drafted text. For
  uploaded NDAs it is unknown, hence FR-12.
- Form definitions are in `app/intake/agreement_forms.json`; the wizard is
  `frontend/src/app/(app)/intake/_agreement-forms.tsx`. The request page's Counterparty card
  (feature 011) already shows a per-viewer result and is a natural neighbour.
- Audit and timeline writes follow the app pattern (`write_audit_log`, `write_timeline_event`
  in `app/core/audit.py`). Beware the audit advisory lock note in the project docs.
- Per project rules the spec stays free of endpoint and table names; those belong in
  plan.md.
