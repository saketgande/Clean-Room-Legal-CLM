# Feature Specification: Template and Playbook Sync

Feature ID: 006-template-playbook-sync
Created: 2026-10-14
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->
Amended: 2026-10-15 with the owner's answers to the six open questions (D-5 to D-10)

## Summary

Our drafting templates (the standard paper a request is drafted from) and our playbooks
(the rules contracts are reviewed against) are not linked today: nobody can see whether a
template still passes its playbook, nothing re-checks it when the playbook publishes a new
version, and the first sign of a mismatch is a risk flagged on a freshly drafted contract.
This feature re-checks each template automatically whenever it is saved or its playbook
publishes a new version, keeps every result, and shows on the Templates page whether each
template currently passes. It also makes it easy to bring in wording from a Word or PDF
file, to add template types that do not exist today (for example a Customer Agreement), and
makes a contract drafted from a Word-based template a Word document that keeps the file's
formatting. Because the AI review is not repeatable (the same wording has come back with
between 0 and 2 findings on different runs), the feature reports what the latest review
found and never promises that a drafted contract will be free of risks.

## User stories

- As a template owner (the administrator who maintains our standard paper), I want to see
  on the Templates page, for every template, whether it currently passes its playbook, so
  that I know which paper needs attention without opening each one.
- As a template owner, I want a template re-checked automatically when I save it and when
  its playbook publishes a new version, so that a playbook change can never silently leave
  our paper out of date.
- As a template owner or legal reviewer, I want to check one template, or all of them, on
  demand and to be told first how many paid AI reviews that will start, so that I stay in
  control of cost.
- As a template owner, I want each finding shown with the exact passage, the issue and the
  suggested wording, a click to the place in the template, and, for templates edited on the
  page, a one-click way to apply the suggested wording (for a Word-based template I make the
  change in Word), so that I can fix our paper quickly.
- As a template owner, I want to replace a template's wording by uploading a Word or PDF
  file (a scanned PDF too, after I have checked the recognised text) and to say which names,
  dates and terms in it become fill-in fields, so that I do not edit codes by hand.
- As a template owner, I want to add a brand-new template type (for example a Customer
  Agreement) by naming the playbook that reviews it and the request form or kind of
  agreement that drafts from it, so that requests of that kind can be drafted.
- As a requester's reviewer, I want a contract drafted from a Word-based template to be a
  Word document that keeps the template's formatting with the request's details filled in,
  so that nobody has to re-format it.
- As a legal reviewer looking at a drafted contract, I want to know which template version
  it came from and how that template stood against its playbook at the time (and whoever
  starts a draft to be told when the template does not currently pass), so that I can tell a
  risk caused by our paper from a risk caused by the requester's answers.
- As a template owner, I want every wording kept as a version that can be loaded back, and
  drafts already made never changed by a later edit, so that history is never lost.
- As a template owner, I want a Templates page with clear loading, empty and error states,
  a warning before I lose unsaved work, and plain-language names instead of code for
  fill-in fields, so that the page is usable without knowing the placeholder syntax.
- As a compliance owner, I want every change to our standard paper and every check recorded
  in the audit trail, so that I can show who changed it and when it was last verified.
- As a finance owner, I want automatic checks to respect the organisation's AI spending
  limit and never repeat on their own, so that background checks cannot create a surprise
  bill.

## Functional requirements

Numbered, testable statements. Each must be verifiable by /verify.

Terms used here. **Template**: the standard paper a request is drafted from. **Template
type**: one kind of template (NDA, Master Services Agreement, Customer Agreement, ...);
each is tied to exactly one playbook that reviews it and to one request form or kind of
agreement that drafts from it. **Wording**: the full content of a template at a point in
time (for a template made from a Word file, its formatting as well). **Version**: one saved
or uploaded wording. **Fill-in field**: a place in the wording where a request's details go
(the other party's name, the effective date, governing law, and the other fields the
Templates page lists today). **Check**: one AI review of a template's wording by its
playbook's current published version; each check is a real, paid AI review. **Finding**: a
deviation a check reports. **Status**: the answer to "does this template currently pass its
playbook?". **Active / retired**: whether a template type is used for drafting. **Request
form or kind**: the form a requester files on or, on the New agreement form, the kind of
agreement they choose (for example "Selling to a customer"). **Person who may change
templates / run reviews / view playbooks**: see Permissions.

**A. The status of each template**

- FR-1: The Templates page SHALL list every active template type of the organisation and
  show for each one, without opening it: its name; the playbook that reviews it (or that
  none does); the request form or kind of agreement that drafts from it; whether it uses the
  shipped wording or an edited or uploaded wording, and which version; and its status.
- FR-2: Each template SHALL show exactly one status at a time, from: Passes; Has findings;
  Checking; Out of date; Partly checked; Couldn't check; Never checked; No playbook.
- FR-3: The status SHALL be Passes only when the most recent completed check (a) was of the
  template's current wording, (b) was against the playbook's current published version, (c)
  read the whole template, and (d) reported no findings. In every other case the status
  SHALL NOT be Passes.
- FR-4: WHEN the most recent completed check meets (a), (b) and (c) of FR-3 and reported one
  or more findings, the status SHALL be Has findings, showing how many and the highest
  severity. A finding at the playbook's fallback position, or of low severity, SHALL still
  count as a finding.
- FR-5: WHILE a check of a template is waiting or in progress, the status SHALL be Checking.
  The previous completed result SHALL stay viewable, labelled with the wording version and
  playbook version it covered.
- FR-6: WHEN the template's current wording, or its playbook's current published version,
  differs from what the most recent completed check covered, and no check is waiting or in
  progress, the status SHALL be Out of date and SHALL say which of the two changed.
- FR-7: WHEN the most recent completed check could not read the whole template because the
  template is longer than the review can read, the status SHALL be Partly checked and SHALL
  say which part was not read. It SHALL NOT be Passes, even when no findings were reported.
- FR-8: WHEN the most recent attempt to check the current wording against the current
  playbook version failed (the AI review was unavailable, the organisation's AI spending
  limit was reached, the playbook version has no rules, or the wording could not be
  filled), the status SHALL be Couldn't check, SHALL give the reason in plain words, and
  SHALL offer a retry to people who may run reviews. A failure SHALL NOT erase the earlier
  completed result and SHALL NOT be shown as Passes.
- FR-9: The status SHALL be Never checked for a template that has never had a check
  attempted, and No playbook for a template that no published playbook covers. For No
  playbook, no check SHALL start and no AI cost SHALL be incurred.
- FR-10: Wherever a status or a check result is shown, the page SHALL state that (a) the
  check is an AI review that can report different findings on identical wording, (b)
  Passes means the latest review found nothing, not a guarantee about any contract, and (c)
  the check judges the template's wording read as a request with no form answers would be
  drafted, not the answers a requester picks. The page SHALL also show when the latest
  completed check finished and which playbook version it used.
- FR-11: WHEN the completed checks recorded for one wording version against one playbook
  version disagree on whether there are findings (at least one reported findings and at least
  one reported none), the page SHALL show a "results vary" marker next to that template's
  status, saying that identical wording has given different results. The marker is shown in
  addition to the status, not as a ninth status. The status itself is still decided only by
  the most recent completed check (FR-3, FR-4), so it MAY change between two checks of
  identical wording. The marker SHALL be worked out only from stored check records and SHALL
  NOT start any AI review. It SHALL disappear when the wording or the playbook version
  changes, until completed checks of the new pair disagree. Checks of unsaved wording
  (FR-16) SHALL NOT count towards it.
- FR-12: A check SHALL be one review of the template's wording. This feature SHALL NOT review,
  flag or change the options a request form offers, and no check, status or message SHALL
  describe a check as covering a requester's form answers. A finding that a draft receives
  because of a form answer is outside what this feature reports (FR-10).

**B. Checking: automatic and on demand**

- FR-13: WHEN a template's wording changes by any means (a save, an upload, loading back an
  earlier version, a reset to the shipped wording, creating a type, reactivating a type),
  the system SHALL start a check of the new wording automatically, with no one asking,
  provided a published playbook covers the template. The page SHALL tell the person, before
  they confirm the change, that a paid AI review will start.
- FR-14: WHEN a playbook publishes a new version, the system SHALL automatically start a
  check, against that new version, of every active template that playbook reviews, and of no
  other template.
- FR-15: For one template there SHALL be at most one check in progress and at most one
  waiting. WHEN the wording changes while a check is waiting or in progress, the older
  check SHALL no longer decide the template's status (its result MAY be kept, labelled with
  the older wording) and a check of the newer wording SHALL be started.
- FR-16: A person who may run reviews SHALL be able to check any one template at any time.
  The check SHALL cover the saved wording or, for a template edited on the page, the wording
  in the editor when it has unsaved changes. A check of unsaved wording SHALL be shown to the
  person, labelled as unsaved wording, and SHALL NOT become the template's status.
- FR-17: A person who may run reviews SHALL be able to start "Check all". Before it starts
  the page SHALL state how many AI reviews it will run and that each is a paid review, and
  SHALL require the person to confirm. It SHALL check every active template that has a
  covering playbook, except any already being checked, SHALL show progress, SHALL update
  each template's status as its result arrives, and SHALL carry on past a template whose
  check fails.
- FR-18: Every check SHALL be recorded with: the template and wording version; the playbook
  and playbook version; what started it (a save, an upload, loading back, a reset, a type
  being created or reactivated, a playbook publishing, a single manual check, Check all);
  who it is attributed to (a person, or the system on behalf of the person whose action
  started it); when it started and finished; its outcome (completed, or not completed and
  why); and, when completed, every finding. These records SHALL be viewable from the
  template and SHALL still be there after the person leaves the page, signs out, or uses
  another device.
- FR-19: A check SHALL continue and finish when the person who started it leaves the page,
  closes the browser or signs out, and the status SHALL be correct when anyone next opens
  the page.
- FR-20: A check SHALL respect the organisation's AI spending limit: when the limit is
  reached no further check SHALL start or continue, and each affected template SHALL show
  Couldn't check with that reason. A check that failed SHALL NOT be retried automatically;
  an automatic check SHALL start only because the wording or the playbook version changed.
- FR-21: A check SHALL NOT create or change any contract, review run, risk rating or finding
  on any contract. Apart from its own check record (FR-18), its only footprint SHALL be the
  normal record of AI usage.
- FR-22: A check SHALL be the same review the playbook performs on a contract drafted from
  that template: the same playbook version, the same rules and the same review instructions,
  so that a finding reported on a template is one a drafted contract would also receive.

**C. Findings**

- FR-23: Each finding SHALL show the clause it concerns, its severity, whether deviating
  from it needs approval, what the issue is, the passage the review quoted, and the
  suggested wording when the review gave one.
- FR-24: WHEN a finding's quoted passage can be located in the template's wording, the
  person SHALL be able to jump from the finding to that place in the template (in a
  read-only view, for a template made from a Word file) with the passage highlighted. WHEN it
  cannot be located (for example it lies in a filled-in value, or the wording has changed
  since the check), the finding SHALL still be shown and SHALL say it could not be located.
- FR-25: For a template edited on the page (every template except one made from a Word file),
  WHEN a finding has suggested wording and its quoted passage occurs exactly once in the
  current wording, the person who may change templates SHALL be able to apply the suggestion
  with one click. The click SHALL replace the passage in the editor with the suggested
  wording, marked as an unsaved change the person can discard; nothing SHALL be saved until
  the person saves, and the saved version's note SHALL record which finding it applied.
  Where the passage occurs zero times or more than once, or the wording has changed since the
  check, no apply SHALL be offered and the suggestion SHALL be shown for reading only. For a
  template made from a Word file no apply SHALL ever be offered; its findings SHALL still
  show the quoted passage and the suggested wording (FR-23) so that the person can make the
  change in Word (FR-40).

**D. Bringing wording in by upload**

- FR-26: A person who may change templates SHALL be able to replace an existing template's
  wording by uploading a Word (.docx) or PDF file, in one flow: choose the file, see what
  was read and how it looks filled in, confirm the fill-in fields, confirm. For a scanned PDF
  the steps in FR-30 and FR-71 come first. Nothing SHALL change until the final
  confirmation; cancelling at any step SHALL leave the template exactly as it was and create
  no version.
- FR-27: An uploaded file SHALL pass the same safety checks as a contract upload: only the
  accepted file types; the file's actual content must match the type it claims; the same
  size limit as contract uploads; and a virus scan when that is switched on. A refused file
  SHALL NOT be kept, and the person SHALL be told in plain words why it was refused. A
  refusal on safety grounds SHALL be recorded in the audit trail.
- FR-28: A file that cannot be read (damaged, password-protected, empty, or a Word file with
  no text in it) SHALL be refused with the specific reason, and the template's existing
  wording SHALL stay untouched. A PDF with no text layer is not refused here; FR-30, FR-73
  and FR-74 apply to it.
- FR-29: A Word file that contains tracked changes or comments SHALL be refused until they
  are resolved, and the message SHALL say what to clear.
- FR-30: A scanned PDF (pages that are images, with no usable text layer) SHALL be accepted
  as a template upload, and its text SHALL be read by paid text recognition. WHEN the system
  finds that an uploaded PDF is a scan, it SHALL tell the person, before any recognition
  runs, that a paid recognition step is needed, that the file will be sent to the
  text-recognition service the application already uses for scanned contracts, and the
  estimated cost where one can be given. Recognition SHALL NOT start until the person agrees
  to that step.
- FR-31: WHEN the file is a PDF, whether it has a text layer or is a scan, the person SHALL
  be told before confirming that a contract drafted from this template is plain text that
  carries the PDF's wording but not its page layout.
- FR-32: Any fill-in field already written into the uploaded file in the notation the
  Templates page uses today (a field name in curly braces) SHALL be recognised
  automatically and listed for the person to see.
- FR-33: The person SHALL be able to mark any passage of the uploaded text as one of the
  supported fill-in fields, picked from a list that shows each field's plain name, what it
  fills in and an example; to unmark it; and, when marking a passage, to apply the same mark
  to every identical occurrence at once. A passage SHALL be markable by its text however the
  Word file happens to store it. The system MAY suggest passages that look like names, dates,
  amounts or terms, but a suggestion SHALL be shown as a suggestion and SHALL have no effect
  until the person accepts it.
- FR-34: A template SHALL NOT be saved, confirmed or made active while its wording contains
  a fill-in field that drafting cannot fill, or one in a place that cannot be filled. The
  person SHALL be told which field and where, and SHALL be able to resolve it (choose a
  supported field, or keep the text as ordinary text). This SHALL hold for every way wording
  enters a template: typing or pasting, uploading, applying a suggestion, and creating a
  type.
- FR-35: WHEN the wording has no fill-in field for our contracting entity or for the other
  party, the person SHALL be warned that drafts will not name them, and SHALL have to
  confirm explicitly before the template can be saved.
- FR-36: Before confirming, the person SHALL see the wording as it will look when filled
  with example values (with its formatting, for a Word file) and the list of fill-in fields
  it uses.
- FR-37: WHEN an upload would leave the template exactly as it already is, the system SHALL
  say so, create no new version and start no check.
- FR-38: Each confirmed upload SHALL become a new version recording who uploaded it, when,
  an optional note and the original file name, and the original file SHALL be kept and
  downloadable by people who may view templates.
- FR-39: A person who may view templates SHALL be able to download a template's current
  wording as a Word file in which each fill-in field appears as a visible code. WHEN that
  file is edited in Word and uploaded again, its fill-in fields SHALL be recognised (FR-32)
  with no re-marking.
- FR-40: The wording of a template made from a Word file SHALL change only by uploading a
  replacement file (FR-26), normally after downloading the current file (FR-39) and editing
  it in Word. The page SHALL NOT offer to edit such a template's wording on the page and
  SHALL tell the person how to change it (download, edit in Word, upload). Every other
  template (the shipped wording, typed or pasted wording, and PDF-based and scan-based
  templates) SHALL keep editing on the page. Loading back an earlier version and resetting
  remain available for every template.
- FR-71: WHEN text recognition of a scanned PDF has finished, the person SHALL be shown the
  recognised text alongside the scanned pages so that the two can be compared, with any page
  on which no text was found listed. The person MAY correct the recognised text. The person
  SHALL then have to confirm explicitly that the recognised text is correct, in a step
  separate from the final confirmation of the upload (FR-26). Marking fill-in fields
  (FR-33), the preview (FR-36) and the final confirmation SHALL work on the confirmed text
  and SHALL NOT be available before it is confirmed.
- FR-72: Wording read from a scan SHALL NOT become a template's wording, be saved as a
  version, be checked or be used for drafting until both the recognised text has been
  confirmed (FR-71) and the upload has been finally confirmed (FR-26). WHEN the person
  cancels at any step, whether before or after recognition has run, leaves the flow, or the
  flow is interrupted, the recognised text SHALL be discarded and the template SHALL stay
  exactly as it was, with no version created.
- FR-73: WHEN text recognition is unavailable or fails, the person SHALL be told in plain
  words that the text could not be read, the template SHALL stay exactly as it was with no
  version created, and the person SHALL be able to cancel or to try again as a new, separately
  agreed paid step. A failed recognition SHALL NOT be retried automatically.
- FR-74: WHEN text recognition finds no text at all in the file, the upload SHALL be refused
  with that reason, the file SHALL NOT be kept, and the template SHALL stay exactly as it was.
- FR-75: A template made from a scan SHALL be marked as such wherever its source is shown (the
  list, the template's details and its version history). Its version SHALL record that text
  recognition was used, the original scanned file (kept and downloadable as in FR-38), who
  confirmed the recognised text, and when.

**E. Drafting from a template**

- FR-41: WHEN a request is drafted from a template whose wording came from an uploaded Word
  file, the draft SHALL be a Word document carrying that file's formatting (its styles,
  headings, numbering, tables, headers and footers, and page setup), with each fill-in field
  replaced by the request's details.
- FR-42: A filled-in value SHALL take the formatting of the text around its place in the
  file. A draft SHALL contain no leftover fill-in code or brace, and a field with no value
  SHALL leave no stray gap or punctuation behind.
- FR-43: A draft made from a Word-based template SHALL go through the same intake as any
  uploaded Word contract (read, its clauses identified, reviewed automatically by the
  playbook), and every sentence of the filled template SHALL appear in the draft in the same
  order.
- FR-44: A draft made from a template that has no uploaded Word file (the shipped wording,
  typed or pasted wording, a PDF-based template, or a template made from a scan) SHALL be
  produced as plain text with no Word formatting, exactly as today. A template made from a
  scan drafts like a PDF-based one, from its confirmed recognised text.
- FR-45: Contracts already drafted SHALL never change because a template was later edited,
  replaced, reset, retired, or because its playbook published a new version.
- FR-46: Each draft SHALL record which template and which template version it was drafted
  from, and that template's status at that moment (FR-2) with the reason, shown in the draft
  contract's history, and this SHALL be recorded in the audit trail.
- FR-47: Drafting from an active template SHALL always be allowed, whatever its status,
  including a template that does not Pass and a new type that has never been checked. WHEN a
  person starts a draft from a template whose status at that moment is not Passes, the page
  SHALL show them a notice saying the template does not currently Pass and why (for example
  the number and highest severity of its findings, that it was never checked, that it is out
  of date and what changed, that it was only partly checked, that its last check could not
  run and why, or that no playbook covers it). The notice SHALL NOT block or delay the
  draft. WHEN a draft is started without a person present (by a workflow), the same notice
  text SHALL be recorded in the draft's history.

**F. New, retired and reset template types**

- FR-48: A person who may change templates SHALL be able to add a new template type by
  giving: its name; the playbook that reviews it; the request form or kind of agreement
  that drafts from it, chosen from those that exist (including "Selling to a customer"); and
  its wording, either by uploading a Word or PDF file or by typing or pasting text. Nothing
  SHALL be created until the person confirms.
- FR-49: A request form or kind of agreement SHALL draft from at most one active template.
  Choosing one that already has an active template SHALL be refused, naming that template.
- FR-50: The playbook choice SHALL offer only playbooks of the organisation that have a
  published version, and a template type's name SHALL be unique among the organisation's
  active types.
- FR-51: A contract drafted from a template SHALL be reviewed by the same playbook that
  template is checked against. WHEN the system cannot guarantee that for the playbook
  chosen for a new type, creating the type SHALL be refused before anything is saved, with a
  message saying why.
- FR-52: WHEN a new type is created, its wording SHALL meet FR-34 and FR-35 and its first
  check SHALL start automatically (FR-13). The type MAY be used for drafting at once, before
  that check has finished (FR-47).
- FR-53: WHEN an active template exists for "Selling to a customer", a request of that kind
  SHALL be drafted from it like any other kind of agreement. Until one exists, such requests
  SHALL keep being handled by Legal as today.
- FR-54: A person who may change templates SHALL be able to retire a template type,
  including a shipped one. A retired type SHALL no longer be used for drafting and SHALL
  not be checked automatically; its request form or kind SHALL then have no template, and
  its requests SHALL be handled by Legal as for any kind without a template today. Its
  versions, uploaded files, check results and the contracts already drafted from it SHALL
  remain readable. The person SHALL confirm after being told this.
- FR-55: A person who may change templates SHALL be able to reactivate a retired type. WHEN
  its request form or kind is now held by another active template, reactivation SHALL be
  refused, naming that template. Otherwise it becomes active and a check starts (FR-13).
- FR-56: A person who may change templates SHALL be able to reset a shipped template type to
  its shipped wording, as today. The reset SHALL be a new version and SHALL start a check.

**G. Versions and concurrent edits**

- FR-57: Every saved or uploaded wording SHALL stay readable as a version showing its number,
  who made it, when, its note, its source (shipped wording, typed text, the uploaded file's
  name, or a scan, FR-75) and the check results recorded against it. A version SHALL never be altered or
  deleted.
- FR-58: A person who may change templates SHALL be able to load an earlier version back.
  This SHALL create a new latest version with that wording (for an uploaded version, its
  original file and fill-in marks too), leave the earlier versions as they were, and start a
  check.
- FR-59: WHEN a template was changed by someone else after a person opened it, saving or
  uploading by that person SHALL be refused with a message saying so, and the person SHALL be
  able to see what changed. A change SHALL never silently overwrite another person's change.

**H. The Templates page**

- FR-60: The page SHALL show a loading state while it loads, an empty state when the
  organisation has no templates (saying what to do next), and an error state with a retry
  when the list or one template's details cannot be loaded. It SHALL never show a blank page.
- FR-61: The page SHALL warn the person and ask for confirmation before they lose unsaved
  work: when moving to another template or page, closing or refreshing the browser tab,
  or abandoning an unfinished upload (including one in which paid text recognition has
  already run, where the warning SHALL say the recognised text will be lost).
- FR-62: Wherever wording is shown, previewed or edited, fill-in fields SHALL appear under
  their plain names (for example "The other party"), visibly distinct from ordinary text and
  not as raw code, and SHALL be insertable from a list.
- FR-63: The page SHALL show a count of templates by status and SHALL let the person narrow
  the list to the templates that need attention (Has findings, Out of date, Partly checked,
  Couldn't check, Never checked).
- FR-64: A person who may not use an action SHALL NOT be offered it as a working control; they
  SHALL see why. A person who may only view SHALL still see statuses, findings, check
  history, versions and downloads.
- FR-65: Status SHALL be conveyed in words, not by colour alone, and every action on the page
  SHALL be operable by keyboard.

**I. Permissions, scoping and audit** (details in the next section)

- FR-66: Viewing templates, statuses, findings, check history, versions and downloading
  files SHALL be allowed only to people who may view playbooks.
- FR-67: Checking one template, Check all, and retrying a failed check SHALL be allowed only
  to people who may run playbook reviews.
- FR-68: Only the people who may edit playbooks (administrators by default) SHALL be able to
  save, upload, replace, create, retire, reactivate, reset, load back an earlier version and
  apply a suggestion, and to take the steps inside an upload (agreeing to paid text
  recognition, correcting and confirming recognised text, marking fill-in fields). This
  feature SHALL NOT introduce a new permission. Legal reviewers, who by default may view
  playbooks and run reviews but not edit them, SHALL NOT be able to do any of these.
- FR-69: Every template, version, uploaded file, check record and status SHALL belong to one
  organisation and SHALL be visible and changeable only within it. A playbook of another
  organisation SHALL NOT be selectable.
- FR-70: Each action listed under "Audit trail" in the next section SHALL leave an audit
  record at the time it happens, and no user SHALL be able to edit or delete that record.

## Permissions, scoping & audit

This platform has role-based access, ethical walls and an immutable audit trail. Templates
are the organisation's own standard paper: they belong to the organisation, not to any
contract.

**Who may do what**

| Action | Who may perform it |
|---|---|
| See the list, statuses, findings, check history, versions; download an original or current file | People who may view playbooks (today: administrators and legal reviewers) |
| Check one template; Check all; retry a failed check | People who may run playbook reviews (today: administrators and legal reviewers) |
| Save, upload or replace wording (including agreeing to paid text recognition of a scan and confirming the recognised text), mark fill-in fields, apply a suggestion, load back a version, reset, add a type, retire, reactivate | People who may edit playbooks (today: administrators only; legal reviewers cannot). No new permission is introduced (FR-68) |
| Automatic checks (after a change of wording or a playbook publishing) | The system, attributed to the person whose action started it (see A-7) |
| Everyone else (ordinary members, approvers) | No access to the Templates page |

**Scoping**

- Everything this feature stores (templates, versions, uploaded files, check records,
  statuses) is scoped to the organisation. Another organisation's data is never visible,
  downloadable, checkable or selectable (FR-69).
- Templates are not tied to a contract, so ethical walls and per-contract access grants do
  not apply to them. They do apply, unchanged, to the contracts drafted from templates:
  a draft is owned and visible exactly as any drafted contract is today.
- A check sends only the template's wording (filled with example values) to the AI
  provider; no contract and no personal data of a real counterparty is involved.
- A scanned template file is sent to the text-recognition service the application already
  uses for scanned contracts, and only after the person has agreed to that paid step
  (FR-30). Templates are standard paper and hold no real counterparty's data.

**Audit trail.** Each of these SHALL be recorded, with who (or "system on behalf of" whom),
when, the template and what changed (FR-70):

- A wording saved, an upload confirmed (file name, size, type, a fingerprint of the file,
  version number, the fill-in fields it uses), loading back, reset.
- An upload refused on safety grounds (file name, type, reason).
- A scanned upload: the paid recognition step agreed (who, file name, the estimate shown),
  recognition failed or found no text, the recognised text confirmed (who, when), and an
  upload cancelled after paid recognition had run.
- A suggestion applied: recorded on the save that includes it, naming the findings applied.
- A template type created (name, playbook, request form or kind), retired, reactivated.
- A check started and a check finished or failed (template and wording version, playbook and
  version, trigger, who or system, outcome, number of findings, reason if it failed), and
  a Check all started (how many reviews).
- A contract drafted from a template (which template and version, and the template's status
  at that moment, per FR-46).

A playbook publishing is already audited by the playbook feature; the checks it starts are
audited here and name the publication as their trigger.

## Acceptance criteria

Given/When/Then scenarios that define "done". Every criterion maps to at least one
functional requirement. AC-65 to AC-75 were added when the open questions were answered.

- AC-1 (FR-1): Given an organisation with the seven shipped templates and a Customer
  Agreement template, when a person who may view playbooks opens the Templates page, then
  every active template is listed with its name, playbook (or "none"), request form or kind,
  wording source and version, and status, without opening any of them.
- AC-2 (FR-2): Given any template in any condition, when it is shown, then it displays
  exactly one of the eight statuses.
- AC-3 (FR-3): Given a template whose latest completed check covered its current wording,
  used the playbook's current published version, read the whole template and reported no
  findings, when its status is shown, then it is Passes; and given that any one of those four
  conditions does not hold, then the status is not Passes.
- AC-4 (FR-4): Given a completed current check with two findings, one of them at the
  playbook's fallback position, when its status is shown, then it is Has findings with count
  2 and the highest severity; and given a single low-severity finding at the fallback
  position, then the status is still Has findings.
- AC-5 (FR-5): Given a check is waiting or running for a template that previously Passed,
  when the page is shown, then the status is Checking and the earlier result can still be
  opened, labelled with the wording version and playbook version it covered.
- AC-6 (FR-6): Given a template whose wording changed after its last completed check and no
  check is waiting or running, when its status is shown, then it is Out of date and says the
  wording changed; and given the playbook version changed instead, it says the playbook
  changed.
- AC-7 (FR-7): Given a template longer than the review can read, when a check completes with
  no findings, then the status is Partly checked, states which part was not read, and is not
  Passes.
- AC-8 (FR-8): Given the AI review is unavailable when a check runs, when it fails, then the
  status is Couldn't check with the reason, a person who may run reviews is offered a retry,
  the earlier completed result is still viewable, and the status is not Passes; and given the
  spending limit was reached, then the reason says so.
- AC-9 (FR-9): Given a template for which no check was ever attempted, then its status is
  Never checked; and given a template no published playbook covers, when its wording is saved,
  then its status is No playbook, no check starts and no AI usage is recorded.
- AC-10 (FR-10): Given any view of a status or a check result, when it is displayed, then the
  page states that AI reviews can differ on identical wording, that Passes is not a guarantee
  about any contract, and that form answers are not covered, and it shows when the latest
  completed check finished and which playbook version it used.
- AC-11 (FR-13): Given an administrator editing a template, when they go to save changed
  wording, then the page tells them a paid AI review will start; and when they confirm, then
  a check of the new wording starts without further action, the status shows Checking and
  then the result, and the check record names the save as its trigger. The same holds for an
  upload, loading back, a reset, a type being created and a type being reactivated.
- AC-12 (FR-14): Given a playbook that reviews two active templates and a third template
  reviewed by another playbook, when the first playbook publishes a new version, then a check
  against the new version starts for those two templates and for no other, and none starts
  for a retired template.
- AC-13 (FR-15): Given a check is running for a template, when its wording is saved again,
  then the older check no longer decides the status, its result (if it finishes) is kept
  labelled with the older wording, one check of the newer wording is started, and at no time
  are two checks of that template waiting.
- AC-14 (FR-16): Given a person who may run reviews with unsaved changes in the editor, when
  they run Check now, then the unsaved wording is reviewed, the result is labelled "unsaved
  wording" and shown to them, and the template's status is unchanged; and given no unsaved
  changes, then the saved wording is checked and the result becomes the template's status.
- AC-15 (FR-17): Given eight active templates with covering playbooks, when a person who may
  run reviews chooses Check all, then the page states eight paid AI reviews and asks to
  confirm, none starts before confirmation, statuses update as results arrive, a template
  already being checked is not started twice, and when one check fails the other seven still
  complete.
- AC-16 (FR-18): Given any finished check, when its record is opened, then it shows the
  wording version, playbook and version, trigger, attribution, start and finish times,
  outcome and (if completed) every finding; and given the person signs out and later returns
  on another device, then the record is still there.
- AC-17 (FR-19): Given a person starts a check and immediately closes the browser, when
  anyone opens the page afterwards, then the check has finished (or is still shown as
  Checking) and the status is correct.
- AC-18 (FR-20): Given the organisation's spending limit is reached, when changed wording is
  saved, then no check runs and the template shows Couldn't check with that reason; and given
  a check failed, when time passes, then it is not retried by itself and only a person's retry
  starts another.
- AC-19 (FR-21): Given a number of checks have run, when the counts of contracts, review runs,
  contract findings and risk ratings are compared with before, then none has changed.
- AC-20 (FR-22): Given a template and its playbook, when a check runs and when the matching
  contract is drafted and reviewed, then both reviews are given the same playbook version, the
  same rules and the same review instructions.
- AC-21 (FR-23): Given a check with a finding, when the finding is shown, then it displays the
  clause, severity, whether approval is needed, the issue, the quoted passage and the
  suggested wording (when given).
- AC-22 (FR-24): Given a finding whose quoted passage is in the template, when the person
  selects it, then the template opens at that passage with it highlighted; and given a
  passage that cannot be found, then the finding is still shown and says it could not be
  located.
- AC-23 (FR-25): Given a text-based template and a finding with suggested wording whose
  quoted passage occurs exactly once, when a person who may change templates applies it, then
  the passage is replaced in the editor as an unsaved change they can discard, nothing is
  saved until they save, and the saved version's note names the finding; given a passage
  that occurs twice, or zero times, or a check of older wording, then no apply is offered;
  and given a template made from a Word file, then no apply is ever offered and its finding
  still shows the quoted passage and the suggested wording.
- AC-24 (FR-26): Given a person who may change templates, when they upload a .docx file to an
  existing template and reach the last step, then the template is unchanged until they
  confirm; and given they cancel at any step, then the template is exactly as before and no
  version exists.
- AC-25 (FR-27): Given a file that claims to be a Word file but whose content is something
  else, or is larger than the limit for contract uploads, or is of another type (an image, an
  older .doc), when it is uploaded, then it is refused with a plain reason and is not kept;
  and given a refusal on safety grounds, then an audit record exists for it.
- AC-26 (FR-28): Given a damaged, password-protected or empty file, or a Word file with no
  text, when it is uploaded, then it is refused with the specific reason and the existing
  wording is untouched; and given a PDF with no text layer, then it is not refused at this
  point but goes to the scan steps (AC-67).
- AC-27 (FR-29): Given a Word file with tracked changes and comments, when it is uploaded,
  then it is refused, the message says to resolve them, and after the file is cleaned and
  uploaded again it is accepted.
- AC-28 (FR-31): Given a PDF, whether with a text layer or a scan, when the person reaches
  the confirmation step, then they have been told the draft will be plain text carrying the
  wording but not the page layout.
- AC-29 (FR-32): Given a Word file that already contains the fields {counterparty} and
  {effective_date} written in curly braces, when it is uploaded, then both are recognised and
  listed without the person marking anything.
- AC-30 (FR-33): Given an uploaded file in which "Globex Corporation" appears twelve times,
  when the person marks one occurrence as "The other party" and chooses to apply it to all,
  then all twelve become that field, including occurrences Word stored split across
  formatting changes; and given a suggested passage the person ignores, then it has no effect.
- AC-31 (FR-34): Given wording containing {client_name}, which drafting does not fill, when
  the person tries to save, confirm or make it active by typing, pasting, uploading, applying
  a suggestion or creating a type, then it is refused naming the field and where it is, and
  it can be resolved by choosing a supported field or keeping the text as ordinary text.
- AC-32 (FR-35): Given wording with no field for the other party, when the person tries to
  save it, then they are warned that drafts will not name the party and the save needs their
  explicit confirmation.
- AC-33 (FR-36): Given an uploaded Word file, when the person reaches the preview, then it
  shows the filled-in document with the file's formatting and lists the fields used.
- AC-34 (FR-37): Given an upload that leaves the template exactly as it is, when it is
  confirmed, then the system says nothing changed, no new version exists and no check starts.
- AC-35 (FR-38): Given a confirmed upload, when the history is opened, then the new version
  shows who, when, the note and the original file name, and a person who may view templates
  can download the original file.
- AC-36 (FR-39): Given a Word-based template, when a person who may view templates downloads
  its current wording, edits one sentence in Word and uploads the file, then the fill-in
  fields are recognised with no re-marking and the preview shows them filled.
- AC-37 (FR-41): Given a template uploaded from Word with heading styles, numbered clauses, a
  table and a footer containing the other party's name field, when a request is drafted from
  it, then the draft is a Word document in which headings, numbering, table and footer keep
  the template's formatting and every field is replaced by the request's details.
- AC-38 (FR-42): Given that draft, when its text and formatting are inspected, then no fill-in
  code or brace remains anywhere, a value takes the formatting of the text around it, and a
  field with no value (such as a total-value sentence when no value was given) leaves no
  stray gap or punctuation.
- AC-39 (FR-43): Given a draft from a Word-based template, when intake completes, then it is
  read and reviewed by the playbook like any uploaded Word contract and every sentence of the
  filled template appears in the draft in the same order.
- AC-40 (FR-44): Given a template with the shipped wording, a pasted-text wording, a PDF
  upload or a confirmed scan, when a request is drafted from it, then the draft is plain
  text with no Word formatting, exactly as before; and for the scan-based template it is
  drafted from the confirmed recognised text, like the PDF-based one.
- AC-41 (FR-45): Given contracts already drafted from a template, when the template is
  edited, replaced, reset or retired, or its playbook publishes, then those contracts are
  unchanged.
- AC-42 (FR-46): Given a request drafted from version 4 of a template that has two findings
  (highest severity High), when the draft contract's history and the audit trail are read,
  then both name that template, version 4, and its status at that moment (Has findings, 2,
  highest High).
- AC-43 (FR-48): Given a person who may change templates, when they add "Customer Agreement"
  with the Customer Agreement Playbook, the "Selling to a customer" kind and an uploaded Word
  file, and confirm, then the type exists with that wording as its first version; and given
  they stop before confirming, then nothing was created.
- AC-44 (FR-49): Given an active template already drafts for "Services (MSA)", when a person
  tries to add a second one for that kind, then it is refused naming the existing template.
- AC-45 (FR-50): Given an organisation with a playbook that has no published version and
  another organisation's playbook, when a person chooses a playbook for a new type, then
  neither is offered; and given a name already used by an active type, then it is refused.
- AC-46 (FR-51): Given a chosen playbook that a contract drafted from the new type would not
  be reviewed by, when the person confirms, then creation is refused with a reason before
  anything is saved; and given a playbook that would review its drafts, then it is accepted.
- AC-47 (FR-52): Given a new type is created with valid wording, then the wording passed FR-34
  and FR-35, its first check starts automatically, and a request can be drafted from the type
  before that check has finished.
- AC-48 (FR-53): Given an active Customer Agreement template for "Selling to a customer",
  when such a request is drafted, then it is drafted from that template; and given none
  exists, then the request is handled by Legal as today.
- AC-49 (FR-54): Given a person retires a type, when they confirm after reading the
  consequences, then the type is no longer used for drafting or checked automatically, its
  request kind has no template, and its versions, files, results and earlier drafts remain
  readable.
- AC-50 (FR-55): Given a retired type whose request kind is now held by another active
  template, when a person reactivates it, then that is refused naming the holder; and given
  the kind is free, then it becomes active and a check starts.
- AC-51 (FR-56): Given a shipped template with edited wording, when a person resets it, then
  a new version holding the shipped wording exists, earlier versions are intact, and a check
  starts.
- AC-52 (FR-57): Given a template with five versions, one of them uploaded, when the history
  is opened, then each shows number, author, time, note, source and its check results, and
  none can be altered or deleted.
- AC-53 (FR-58): Given an earlier uploaded version, when a person loads it back, then a new
  latest version holds that wording with its original file and fill-in marks, the earlier
  versions are unchanged, and a check starts.
- AC-54 (FR-59): Given two people have the same template open, when the first saves and then
  the second tries to save, then the second is refused with a message and can see what
  changed, and the first person's change is not overwritten.
- AC-55 (FR-60): Given the list is loading, the organisation has no templates, or loading
  fails, when the page is shown, then it shows a loading state, an empty state saying what to
  do next, or an error with a retry; never a blank page.
- AC-56 (FR-61): Given unsaved edits or an unfinished upload, when the person switches
  template, navigates away, or closes or refreshes the tab, then they are asked to confirm
  before the work is lost; given paid text recognition has already run in the unfinished
  upload, then the warning says the recognised text will be lost; and when they have no
  unsaved work, no question is asked.
- AC-57 (FR-62): Given any view of wording (editing, preview, history, upload), when it is
  shown, then fill-in fields appear under plain names distinct from ordinary text, with no
  raw code, and can be inserted from a list.
- AC-58 (FR-63): Given templates in several statuses, when the page is shown, then it shows a
  count per status and the person can narrow the list to those that need attention.
- AC-59 (FR-64): Given a legal reviewer (view and run, no change), when they open the page,
  then they see statuses, findings, history, versions and downloads and can check, but are
  not offered the change actions as working controls and see why.
- AC-60 (FR-65): Given any status on the page, when read by a person who cannot see colour or
  who uses only a keyboard, then the status is stated in words and every action is reachable
  and operable by keyboard.
- AC-61 (FR-66): Given a person who may not view playbooks, when they open the Templates
  page or ask for a template, a status or a file, then they are refused and see nothing.
- AC-62 (FR-67): Given a person who may view but not run reviews, when they try to check a
  template, run Check all or retry, then it is refused and no AI review starts.
- AC-63 (FR-69): Given two organisations, when a person in one lists templates or asks for a
  template, version, file, check record or status that belongs to the other, then it is not
  found, and a playbook of the other organisation cannot be chosen.
- AC-64 (FR-70): Given each action under "Audit trail", when it is performed, then an audit
  record exists with who (or system on behalf of whom), when, the template and what changed,
  and no user can edit or delete it.
- AC-65 (FR-11): Given a wording version with two completed checks against the same playbook
  version, one reporting a finding and one reporting none, when the template is shown, then
  its status follows the more recent of the two, a "results vary" marker is shown beside it,
  and showing the marker starts no AI review and records no AI usage; given both checks
  report findings (or both report none), then no marker is shown; given the wording or the
  playbook version then changes, then the marker is gone until completed checks of the new
  pair disagree; and given a check of unsaved wording that disagrees with the saved checks,
  then it does not create the marker.
- AC-66 (FR-12): Given a request form that offers an option the playbook would reject, when
  any check runs, then it reviews only the template's wording, with no extra review for any
  form option, no result describes form options as checked, and the page states that form
  answers are not covered; and given Check all over eight templates, then exactly eight
  reviews are started.
- AC-67 (FR-30): Given a person who may change templates uploads a PDF whose pages are images
  with no usable text layer, when the system finds this, then before anything is recognised
  the page says a paid recognition step is needed, that the file goes to the text-recognition
  service already used for scanned contracts, and the estimated cost where one can be given;
  no recognition runs until the person agrees; and given the person does not agree, then
  nothing is recognised and the template is unchanged.
- AC-68 (FR-40): Given a template made from a Word file, when a person who may change
  templates opens it, then the page offers no in-page editing of its wording and says to
  download it, edit it in Word and upload the replacement, and that route works (AC-36);
  given a shipped, typed or pasted, PDF-based or scan-based template, then in-page editing is
  still offered; and given a Word-based template, then loading back an earlier version and
  resetting are still available.
- AC-69 (FR-47): Given one template in each of the states Has findings, Never checked, Out of
  date, Partly checked, Couldn't check and No playbook, when a person starts a draft from
  each, then each draft is created without delay or block, the person sees a notice naming
  that state and its reason, and the draft's history records the template version and that
  state; given a template that Passes, then the draft is created, no notice is shown, and the
  history still records the version and Passes; given a workflow starts a draft from a
  template that does not Pass, then the notice text is recorded in the draft's history; and
  given a new type created moments ago whose first check is still running, then a request
  can be drafted from it.
- AC-70 (FR-68): Given an administrator, when they save, upload, replace, create, retire,
  reactivate, reset, load back or apply a suggestion on a template, then each is allowed;
  given a legal reviewer (may view playbooks and run reviews, may not edit them), when they
  try any of these, then each is refused and no wording, type, version or check is created or
  changed; and given the application's list of permissions before and after this feature,
  then the list is the same.
- AC-71 (FR-71): Given recognition has finished on a three-page scan in which page 2 yielded
  no text, when the person reaches the next step, then the recognised text is shown alongside
  the scanned pages with page 2 listed as having no text, the person can correct the text,
  and marking fill-in fields, the preview and the final confirmation are unavailable until
  the person has explicitly confirmed the text, in a step separate from the final
  confirmation.
- AC-72 (FR-72): Given recognised text that has not been confirmed, or text that was
  confirmed but whose upload was not finally confirmed, when the person cancels, leaves the
  page or the browser closes, then the recognised text is discarded, the template is exactly
  as it was, no version exists, no check has started, and drafting still uses the previous
  wording; and given the person cancels after recognition has already run, then the same
  holds.
- AC-73 (FR-73): Given text recognition is unavailable or fails, when the person is told,
  then the message says the text could not be read, the template is unchanged with no version
  created, the person can cancel or try again as a new paid step they agree to, and no retry
  happens by itself.
- AC-74 (FR-74): Given a PDF in which recognition finds no text at all, when recognition
  ends, then the upload is refused with that reason, the file is not kept, and the template
  is unchanged.
- AC-75 (FR-75): Given a confirmed template made from a scan, when the list, the template's
  details and its version history are shown, then each marks it as made from a scan, and the
  version records that text recognition was used, the original scanned file (downloadable),
  who confirmed the recognised text, and when.

## Out of scope

- Making the AI review repeatable, changing the reviewer's prompts or models, or any
  promise that a template will always Pass. The feature reports what the latest review
  found (FR-10).
- Changing how playbooks are written, versioned or published, or editing a playbook from the
  Templates page. Also changing how a playbook is matched to a contract by name, except what
  FR-51 needs so that a draft is reviewed by the playbook its template is checked against.
- Writing or rewriting legal wording: the system does not draft the Customer Agreement
  wording or rewrite a template to make it pass. A person supplies wording; the only
  system-produced wording is the review's suggested change, which a person applies and saves,
  and text recognised from a scan, which a person must read and confirm (FR-71).
- Accepting, waiving or silencing a finding: a finding stays a finding until the wording or
  the playbook changes and a later check says otherwise. The "results vary" marker (FR-11) is
  the only acknowledgement of review variance.
- Checking request-form options against the playbook, or changing the form's options. A
  finding a draft gets from a requester's form answers is outside the check (FR-12).
- Editing the wording of a Word-based template on the page, and one-click apply of
  suggestions on it (FR-25, FR-40); such a template changes only by uploading a replacement
  file.
- Standalone image files (photos, PNG, JPEG) as templates, and any text recognition that runs
  without the person's agreement or whose result is accepted without the person's
  confirmation (FR-30, FR-71).
- More than one active template per request form or kind of agreement, choosing between
  templates at drafting time, and variants by entity, country or counterparty.
- New fill-in fields beyond those the Templates page lists today; optional or conditional
  clauses inside a template; per-request overrides of template wording.
- Converting a Word-based template's draft to PDF, e-signature set-up, or any change to
  what happens to a draft after it is created.
- Re-drafting or updating contracts already drafted.
- A second person approving template changes, or any approval workflow for templates.
- Notifications (email, chat) when a template stops passing, and showing template status on
  the playbook, request or contract pages. The Templates page is where status is seen.
- Bulk import of many templates at once; Check all is the only bulk action.
- Spending limits or budgets specific to template checks; the organisation's existing AI
  spending limit applies (FR-20).
- Languages other than English.
- A global redesign, new navigation, a new theme or new shared components. The change is the
  flow and layout of this one page, using the application's existing components and look
  (a global redesign was rejected on 2026-09-29).
- Deleting versions, uploaded files, check records or audit entries.

## Edge cases & error behavior

- Release: shipping this feature SHALL NOT start paid checks by itself. Existing templates
  show Never checked until a change, a playbook publication or a person's Check all starts a
  check (FR-9, FR-13, FR-17). Versions saved before this feature become the first entries of
  each template's history (FR-57).
- A playbook publishes while a check of the old version is running: the old check finishes
  and is recorded against the old version, the status becomes Out of date until the check of
  the new version is under way, then Checking (FR-6, FR-14, FR-15).
- A playbook is unpublished, renamed so it no longer covers a template, or deleted: the
  template shows No playbook, earlier results stay viewable, no new check starts (FR-9).
- A playbook version has no rules: the check cannot run and the template shows Couldn't check
  with that reason (FR-8).
- The AI provider has an outage, or the spending limit is hit part-way through Check all or a
  burst of publications: the affected templates show Couldn't check with the reason, the
  rest finish, nothing is retried by itself (FR-17, FR-20).
- Many saves in quick succession: each changed wording is a paid review, but only the newest
  is checked after the one in progress (FR-15); the page said so before each save (FR-13).
- A template longer than the review can read: Partly checked, never Passes (FR-7).
- A wording or an upload is saved with no change: no new version, no check (FR-37; saving
  unchanged text behaves the same).
- Reset on a template already on its shipped wording: no new version, no check.
- A request is drafted while its template is being replaced: the draft uses the version in
  effect at that moment and records it (FR-46); a later change does not alter it (FR-45).
- A request of a kind whose template was retired and which is waiting to be drafted: it has no
  template and is handled by Legal (FR-54).
- A fill-in field sits in a place the system cannot fill (for example a text box or footnote):
  it is reported like an unsupported field and blocks confirmation (FR-34).
- Fill-in values that contain quotes, braces, very long names or non-English characters are
  inserted as plain text and never break the draft (FR-42).
- Two active types named alike, a blank name, or an over-long name: refused with a message
  (FR-50).
- A person leaves mid-upload or mid-edit: they are warned (FR-61); if they leave anyway,
  nothing is saved, no version appears in history, and nothing is left half-done.
- A scanned PDF (FR-30, FR-71 to FR-75): the paid step is announced before it runs; if
  recognition is unavailable or fails, nothing changes and trying again is a new agreed paid
  step; if it finds no text at all, the upload is refused; if it finds no text on some pages,
  those pages are listed before the person confirms; if the person cancels or leaves at any
  point, the recognised text is discarded; and an unconfirmed scan never becomes wording that
  is saved, checked or drafted from. A scan-based template then drafts like a PDF-based one.
- A person wants to change one word in a Word-based template: the page offers no in-page edit
  and says to download the file, edit it in Word and upload it again; the fill-in fields come
  back without re-marking (FR-39, FR-40).
- Drafting from a template that does not Pass, or whose playbook has since been unpublished:
  the draft is created, the person sees the notice (or, for a workflow-started draft, it is
  recorded in the draft's history), and the draft's automatic review behaves as it does
  today when no playbook applies (FR-47).
- Recent checks of identical wording disagree: the status follows the most recent completed
  check and the "results vary" marker is shown; changing the wording or the playbook version
  clears the marker (FR-11).
- A person without change permission opens a template: read-only with the reason shown
  (FR-64); a person without view permission gets nothing (FR-66).
- A finding's quote spans a filled-in value or an older wording: shown but not located, no
  apply offered (FR-24, FR-25).
- A check result is for wording that is no longer the current wording: labelled with its own
  version and never used as the current status (FR-5, FR-15).

## Decisions

Answers the owner gave; each is folded into the requirements above. D-1 to D-4 were given
before drafting; D-5 to D-10 on 2026-10-15, in answer to the six open questions, which are
all closed.

- D-1: Upload covers both replacing the wording of an existing template with a Word or PDF
  file and adding brand-new template types; a new type names the playbook that reviews it and
  the request form or kind of agreement that drafts from it. (FR-26, FR-48)
- D-2: A contract drafted from a template uploaded as a Word file is itself a Word document
  carrying that file's formatting, with the request's details filled in. Today every draft is
  plain text. (FR-41, FR-42)
- D-3: Re-checking is automatic: in the background whenever a template is saved or its
  playbook publishes a new version, plus a manual "Check all". Each check is one real AI
  review. (FR-13, FR-14, FR-17)
- D-4: The work goes through the spec-driven workflow (this document is its first gate).
- D-5 (status wobble): "Passes" means the latest completed check found zero findings, and it
  may change between checks of identical wording. The page shows a "results vary" marker when
  recent checks of the same wording and the same playbook version disagree, worked out from
  stored history only, with no extra AI reviews. There is no accepting or waiving of
  findings. (FR-3, FR-4, FR-11)
- D-6 (drafting from a template that does not pass): drafting is always allowed, including
  from a template that does not Pass and from a never-checked new type. The person drafting
  sees a notice saying the template does not currently Pass and why, and the draft's history
  records the template version and that template's status at that moment. (FR-46, FR-47,
  FR-52)
- D-7 (form options): this feature does not check request-form options. The page states
  plainly that a check covers the template's wording, not the requester's form answers.
  Form-option checking stays out of scope. (FR-10, FR-12)
- D-8 (scanned PDFs): a scanned PDF can be uploaded as a template. It goes through paid text
  recognition, with the paid step and its cost made clear before it runs. The person must
  read and explicitly confirm the recognised text before it can be saved; a template made from
  a scan is marked as such; an unconfirmed scan can never become the active wording. A
  scan-based template drafts like a PDF-based one (plain text, no Word formatting). (FR-30,
  FR-71 to FR-75, FR-44)
- D-9 (who may do what): the people who may edit playbooks (administrators by default) may
  save, upload, replace, create, retire, reactivate, reset, load back and apply a suggestion.
  Viewing needs the permission to view playbooks. Running a single check or Check all needs
  the permission to run a playbook review. No new permission. (FR-66, FR-67, FR-68)
- D-10 (changing a Word-based template): the wording of a template made from a Word file
  changes only by downloading the file, editing it in Word and uploading a replacement; the
  download keeps the fill-in fields (FR-39). Such templates cannot be edited on the page and
  offer no one-click apply, but their findings still show the quote and the suggested wording.
  Text-based templates keep in-page editing and one-click apply. (FR-25, FR-39, FR-40)

Wording added by the analyst to make the decisions testable. The owner has not yet reviewed
these assumptions; they are reviewed at approval and any of them may be reversed:

- A-1: Accepted upload types are Word (.docx) and PDF, including scanned PDFs (D-8). Older
  .doc files, standalone image files (photos, PNG, JPEG) and other types are refused
  (FR-27).
- A-2: A Word file with tracked changes or comments is refused until they are resolved,
  rather than guessing whether they belong in standard paper (FR-29).
- A-3: A template with no field for our entity or the other party can be saved only after an
  explicit confirmation, not blocked outright (FR-35).
- A-4: A request form or kind drafts from at most one active template (FR-49).
- A-5: Retiring is reversible, is allowed for shipped types too, and never deletes anything
  (FR-54, FR-55).
- A-6: A failed automatic check, and a failed text recognition, are not retried by
  themselves, because a retry loop is a cost risk; a retry is a person's action (FR-20,
  FR-73).
- A-7: An automatic check runs even when the person whose save or playbook publication started
  it does not themselves hold the permission to run reviews; it is attributed to the system
  on that person's behalf (Permissions).
- A-8: A check of unsaved wording is shown but never becomes a template's status (FR-16).
- A-9: Templates are organisation-wide standard paper, so ethical walls do not apply to them
  (Permissions).
- A-10: Templates with no uploaded Word file (shipped, typed or pasted, PDF-based or
  scan-based) keep drafting plain text; only Word-based templates produce Word drafts
  (FR-44).
- A-11: Shipping the feature starts no paid checks by itself (Edge cases).
- A-12: For a scan, the person may correct the recognised text before confirming it, and
  confirming the text is a step separate from the final confirmation (FR-71).
- A-13: The answer to D-10 names only Word-based templates, so every other template,
  including PDF-based and scan-based ones, is edited on the page and gets one-click apply
  (FR-25, FR-40).
- A-14: "Recent checks" in D-5 means the completed checks recorded for one wording version
  against one playbook version, rather than a fixed number of latest checks (FR-11).
- A-15: When a workflow starts a draft with no person present, the not-passing notice is
  recorded in the draft's history instead of being shown (FR-47).

---

### Context for planning (non-normative)

Implementation-adjacent facts recorded to save the architect re-deriving them. They are not
requirements and carry no normative weight.

- **Current drafting path.** `intake/drafting.py`: `resolve_doc_type` maps a request to a
  template key via `_FORM_DOC` (sow, dpa) and `_KIND_DOC` (the five New-agreement kinds);
  "Selling to a customer" and "Something else" map to nothing, so Legal drafts them.
  `template_values` builds the 13 placeholder values, `render_document` fills with Python
  `str.format`, and `draft_contract_for_request` encodes the result as `.txt` and passes it
  to `create_contract_from_upload`. Word-formatted drafts need a Word-aware fill: Word often
  splits one visible phrase across several runs, so a naive text replace will miss fields.
- **Templates.** `drafting_templates/service.py` holds the fixed `TEMPLATES` dict (7 keys)
  and the `PLACEHOLDERS` list; `models.py` stores `DraftingTemplateVersion` (text body,
  NULL = shipped default). New types need a data-driven registry, and
  `CONTRACT_TYPE_OF_KIND` / `CONTRACT_TYPE_OF_FORM` in `playbooks/library.py` already maps
  "Selling to a customer" to the contract type "Customer Agreement". `check_body` is the
  guarantee FR-34 extends to uploads.
- **Playbook pick is by name.** `pick_playbook_for_contract` matches a contract type to a
  playbook by name tokens, and `review_text` is what the existing check uses. A playbook
  chosen explicitly for a template can therefore differ from the one the drafted contract's
  automatic review picks; FR-51 is the requirement that closes that gap, and the plan must
  decide how (an explicit link, or validating the name match).
- **Existing check.** `POST /drafting-templates/{key}/check` calls
  `PlaybooksService.review_text`; its result already has `text_was_truncated` (basis of
  Partly checked), the playbook version id, rule count and findings. Nothing is persisted
  except the AI call ledger rows. `tests/test_template_check.py` pins that the check sends
  exactly what a bare request drafts and creates no run, finding or contract; keep it green.
- **Publish hook.** The only human path that makes a playbook version published is
  `publish_playbook` in `playbooks/routes.py` (audit `playbook.published`); `seed_playbook_library`
  also creates published versions. Any other path found later must start checks too (FR-14).
- **Background work.** Celery jobs via `create_job` / `dispatch_job` with the idempotency
  pattern in `jobs/tasks.py` (key could be template version plus playbook version). Mind the
  audit advisory lock: write audit rows on the request's own session and never hold one open
  across the AI call (see CLAUDE.md, "Audit-lock freeze").
- **Permissions today.** Read: `playbook:read`; change: `playbook:update`; check:
  `playbook:run`. Default roles: admin holds all; legal_reviewer holds read and run; member
  and approver hold none. Note `has_permission()` is a pass-through when `DISABLE_RBAC` is on
  (dev compose).
- **Upload safety to reuse.** `validate_upload_mime` and `_scan_for_malware` in
  `contract_files/service.py`; `settings.allowed_mime_types` is wider than FR-27 (it includes
  .doc, images and text), so templates need a narrower list. `max_upload_size_bytes` is 50 MB.
  The Documents reader (`app/documents/reader`) reads Word and PDF and can detect a scan. The
  Word editor (ONLYOFFICE) on the contract page is not used here: Word-based templates change
  by upload only (D-10).
- **Scans (FR-30, FR-71 to FR-75).** The contract path already falls back to OCR through
  Reducto, the only OCR provider, when extracted text quality is low (see CLAUDE.md, "The
  document pipeline"); the scan flow can reuse that recognition. It is paid and has its own
  failure modes (provider not configured, error, timeout, nothing found), and the text it
  returns is a pinned artifact, so the confirmed text, not a re-run, must be what is stored.
- **Drafting notice and record (FR-46, FR-47).** `draft_contract_for_request` is called both
  from the intake page by a person and by the workflow engine (`clm_draft` step), so the
  notice needs a person-facing surface in the first case and a history record in both. The
  existing `intake.contract_drafted` audit entry and timeline event already carry the
  document type and are the natural place for the template version and status.
- **Evidence behind FR-10 and FR-11 (measured 2026-10-14).** Identical template text gave
  2, 2, 1, 1, 2 findings (MSA), 1, 0, 1, 1, 1, 0 (Vendor), 0, 0, 0, 2, 1 (SaaS) across
  repeated runs. Some are real wording gaps (the MSA defers all service levels and credits to
  the SoW and was flagged as a fallback in 4 of 5 runs); others are judgement calls that come
  and go. The NDA playbook published v2 the same day and nothing showed the NDA template was
  checked against it.
- **Form answers (FR-12, out of scope by D-7).** `agreement_forms.json` drives the options; a test pins only that
  the NDA term offers nothing under 2 years. A real draft is filled with form answers while a
  check fills blank answers with the playbook's preferred positions.
- **Page.** `frontend/src/app/(app)/templates/` (`page.tsx`, `_check-panel.tsx`,
  `page.test.tsx`). Shared primitives from `@/components/ui` only; theme via `slate-*`
  (never `bg-white`); keep the page working in demo mode if the surrounding feature does.
- **Working tree.** `drafting_templates/*`, `playbooks/routes.py`, `playbooks/service.py`
  and the templates page have uncommitted changes (the check feature itself); plan against
  the working tree, not the last commit.
