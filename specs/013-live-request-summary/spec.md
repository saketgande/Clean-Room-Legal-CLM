# Feature Specification: Live request summary and extra instructions on the agreement form

Feature ID: 013-live-request-summary
Created: 2026-10-27
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Summary

While a requester answers an agreement form, a box beside the form writes the request in
plain sentences, for example "This is a mutual NDA between Aegis India Pvt Ltd and Initech
Software Ltd, on our template." The text is built by plain rules from the answers (no AI) and
changes every time an answer changes. Questions not yet answered show as gaps ("Counterparty:
not chosen yet"). On the final review step the same text appears in a larger box that the
requester can edit. If an edit clearly changes something an answer says, Aegis offers "Update
the answer?" and changes the answer only when the requester clicks. Edits it cannot match to an
answer leave the answers alone and are kept as extra instructions for Legal, with a note
"This differs from your answer: X". The draft contract is always built from the answers, never
from the text in the box. This works on all nine agreement forms. Reading the extra
instructions with AI, and marking AI-added terms in the draft, are later features.

## User stories

- As a requester, I want to see my request written in plain sentences as I answer, so that I
  can tell at once whether the form says what I mean.
- As a requester, I want to see which questions are still unanswered, so that I know what is
  missing before I file.
- As a requester, I want to fix a sentence on the review step by typing in it, so that I do not
  have to go back and find the right question.
- As a requester, I want Aegis to offer to update the matching answer, and to change nothing
  until I agree, so that my text and my answers never quietly disagree.
- As a requester, I want to add things the form has no question for, so that Legal knows what I
  need.
- As a lawyer, I want to see the requester's extra instructions, and where they differ from the
  answers, so that I do not miss a special ask.
- As an administrator, I want the extra instructions kept on the request with a record of who
  wrote them, so that there is a trail.

## Functional requirements

### The live box while answering

- FR-1: WHEN a requester is filling in any of the nine agreement forms, the system SHALL show a
  summary box beside the form on every step, written in plain sentences from the answers
  given so far.
- FR-2: The box text SHALL be built only from the answers by fixed rules. It SHALL NOT use AI
  and SHALL NOT depend on any AI result.
- FR-3: WHEN any answer is added, changed or cleared, the box SHALL update to match, without
  the requester pressing anything.
- FR-4: WHEN a question applies to the request (under the form's existing "only when" rules)
  and is unanswered, the box SHALL show it as a visible gap that names the question, for
  example "Counterparty: not chosen yet". Required questions SHALL be shown as gaps in a way
  that tells them apart from optional ones.
- FR-5: WHEN a question does not apply to the request, because of the form's "only when"
  rules, the box SHALL NOT mention it, neither as a sentence nor as a gap.
- FR-6: Each sentence SHALL cover answers only. The box SHALL NOT add legal advice, opinions,
  or statements about what is allowed or not.
- FR-7: Answers that are lists of rows (for example deliverables, or subprocessors) SHALL read
  as a short sentence with the count and the first few items, and SHALL NOT make the box
  longer than the form is.
- FR-8: Amounts SHALL be written with their currency, dates in one clear written form (for
  example "15 Nov 2026"), and choices as the option the requester picked.
- FR-9: The box SHALL NOT change what is asked, filed, routed or drafted. The form SHALL work
  the same whether or not the box has loaded.
- FR-10: The box SHALL sit with the other side-column blocks (the routing panel and the NDA
  check) without hiding them, and SHALL be reachable by keyboard and by a screen reader.
  WHERE the screen is too small for a side column, the box SHALL still be reachable from the
  form.

### The editable box on the review step

- FR-11: On the review and submit step, the same text SHALL appear in a larger box the requester
  can edit.
- FR-12: The review step SHALL keep showing the list of answers as it does today, so the
  requester can compare the text with the answers.
- FR-13: The requester SHALL be able to undo their edits to the box, getting back the text built
  from the answers.

### Smart edit

- FR-14: WHEN the requester changes a sentence that came from a choice answer (for example kind
  of NDA, governing law, whose paper) so that it now names another option of the same
  question, the system SHALL recognise it and offer "Update the answer?" showing the old and
  the new value.
- FR-15: WHEN the requester changes a date or number in a sentence that came from a date or
  number answer (for example needed-by date, term, value), the system SHALL read the new value
  and offer "Update the answer?" showing the old and the new value. Values SHALL be read from
  common ways of writing them (for example "15/11/2026", "5,000,000", "50 lakh"), and a value
  that cannot be read with certainty SHALL NOT be offered as an update (FR-18).
- FR-16: WHEN the requester edits a sentence that came from a free-text answer (for example
  purpose or scope), the system SHALL offer "Update the answer?" with the edited wording as
  the new answer.
- FR-17: An answer SHALL change only when the requester clicks the offer. The system SHALL NOT
  change any answer silently, on typing, on leaving the box, or on filing.
- FR-18: WHEN an edit cannot be matched to exactly one answer with certainty (for example a new
  party name, an option that does not exist, an unusual phrasing, or one sentence that changes
  two answers), the system SHALL leave every answer unchanged, SHALL NOT guess, and SHALL keep
  the edit as an extra instruction with the note "This differs from your answer: X", where X
  is the current answer.
- FR-19: WHEN one edit touches more than one answer, the system SHALL treat each answer
  separately: each one it can match with certainty gets its own offer, and the rest follow
  FR-18.
- FR-20: WHEN the requester accepts an offer, the matching answer SHALL change, the box SHALL
  show the new text built from the answers, the same answer-driven updates SHALL happen as if
  the requester had changed it on the form (for example the routing panel, the NDA check and
  which questions apply), and the offer SHALL close.
- FR-21: WHEN the requester declines an offer, or leaves it unanswered, the answer SHALL stay
  as it was and the edit SHALL be kept as an extra instruction with the note from FR-18.
- FR-22: A requester SHALL NOT be able to choose or create a person, company, entity or
  contract record by typing in the box. WHEN an edit names a counterparty, entity or parent
  contract that is not the current pick, the system SHALL NOT change the pick and SHALL NOT add
  anything to any register. It follows FR-18.
- FR-23: WHEN the requester changes an answer on the form after editing the box, the answer
  SHALL win: the box SHALL show the sentence rebuilt from the current answers, and the
  requester's earlier edit that no longer matches SHALL stay as an extra instruction with the
  note "This differs from your answer: X" (X being the current answer). Nothing the requester
  typed is lost.
- FR-24: WHEN the requester deletes a whole generated sentence, the system SHALL NOT clear the
  answer behind it silently. It SHALL offer "Clear the answer?" in the same way as other
  edits (FR-17, FR-21). WHEN the question is required, the system SHALL say it cannot be
  cleared and SHALL put the sentence back, leaving the answer unchanged.

### Extra instructions

- FR-25: Lines the requester writes that are not about any answer (for example a new ask such
  as "please send a copy to procurement") SHALL be kept as extra instructions on the request.
- FR-26: Extra instructions SHALL include: lines that are not about any answer (FR-25), edits
  that could not be matched (FR-18), and edits whose offer was declined or ignored (FR-21).
- FR-27: Each extra instruction that came from an unmatched or declined edit SHALL keep its
  note "This differs from your answer: X", and each SHALL stay in the requester's own words.
- FR-28: When the request is filed, the extra instructions SHALL be saved on the request with
  who wrote them and when, apart from the answers. Filing with no extra instructions SHALL be
  normal and SHALL save none.
- FR-29: The requester SHALL be able to see all their extra instructions as a list on the
  review step before filing, and to remove any of them.
- FR-30: The request page SHALL show the extra instructions to the people named in the
  Permissions section, with the note from FR-27, clearly separated from the answers.
- FR-31: Extra instructions SHALL be saved text only in this feature. They SHALL NOT be used by
  drafting, routing, triage, approvals or any AI step.
- FR-32: The extra instructions SHALL be limited to about 2,000 words in total, and the system
  SHALL tell the requester plainly before they reach it. WHEN they go past it, the system
  SHALL ask them to shorten the text or attach it as a file instead, and SHALL NOT delete or
  cut anything they typed. A request within the limit SHALL always be fileable.

### Source of truth, filing and saved drafts

- FR-33: The draft contract, routing, triage and all other uses of the request SHALL come only
  from the answers. The saved summary text SHALL never replace or change an answer.
- FR-34 (changed 2026-10-27 by the user): WHEN the requester presses Submit with an offer still
  open, the system SHALL NOT file yet. It SHALL say how many changes are waiting ("You have N
  changes waiting — update or keep each one, then submit."), move focus to the first waiting
  offer, and file only once every offer is answered. Nothing is changed or filed silently.
- FR-35: WHEN a request is filed, the system SHALL also save the summary text built from the
  final answers, so the request page can show what the requester saw. It SHALL be shown as
  built from the answers at filing time and SHALL NOT be treated as the source of truth.
- FR-36: WHEN the requester saves a half-filled form as a draft and later resumes, the extra
  instructions, and any open offers, SHALL come back as they were, and the box SHALL be rebuilt
  from the current answers.
- FR-37: WHEN a form is filed again after an error or a repeat click, the extra instructions
  SHALL NOT be saved twice.
- FR-38: The existing "Non-standard terms the customer asked for" and "Note to approvers"
  answers SHALL keep working as they do today as answers. The "Note to approvers" SHALL stay
  separate from extra instructions: it goes to approvers as today, extra instructions go with
  the request as in FR-30, and the box SHALL also read the approvers' note out as a normal
  sentence. The approvers' note SHALL NOT be turned into an extra instruction.

### Who can read the extra instructions after filing

- FR-39: After filing, everyone who can open the request (the requester, Legal, approvers and
  anyone else the request is visible to) SHALL see the extra instructions, under exactly the
  same visibility rules as the request itself, including ethical walls. Once filed, nobody
  SHALL be able to edit, add to or remove extra instructions. Later notes go in the request's
  comments as today.

## Permissions, scoping & audit

- Who may use the live box and the editable box: any signed-in person who may file an agreement
  request. It needs no right beyond filing a request.
- Who may write, accept, decline or remove edits and extra instructions: the person filing the
  request, only on their own form or draft. Nobody else makes these choices.
- Who may see extra instructions after filing: everyone who may open the request under the
  app's normal rules, including ethical walls (FR-39). Nobody may change them after filing. A person who may not see the request SHALL NOT see its extra instructions, its
  saved summary text, or any hint that they exist.
- The box is built only from what the requester typed and from register picks the requester
  already sees on the form. It SHALL NOT reveal anything about contracts or records the viewer
  could not already see. Where the NDA check or similar panels show per-viewer results, the
  box SHALL NOT repeat them.
- Data scoping: own organisation only. Extra instructions belong to one request in the
  requester's organisation.
- Audit trail: WHEN a request is filed with extra instructions, the audit trail and the
  request's visible history SHALL record that extra instructions were saved, who wrote them,
  when, and how many. WHEN the requester accepted an offer to update an answer, the filing
  record SHALL show that this answer was changed from the box and what the old and new values
  were. Merely typing, seeing offers, declining offers, or viewing the request page SHALL write
  no audit entry. Edits on a form that is never filed SHALL be recorded nowhere except the
  requester's saved draft.
- The audit entry SHALL NOT contain the full text of the extra instructions (it may be long
  and sensitive); the text is kept on the request itself, visible only as FR-39 allows.

## Acceptance criteria

- AC-1 (FR-1, FR-3): Given a requester on any of the nine forms, when they pick "NDA" and
  "Mutual", then the box says it is a mutual NDA; and when they change either answer, then the
  box changes at once.
- AC-2 (FR-2, FR-9): Given the box cannot build its text, when the requester files, then
  filing works as usual and nothing is blocked.
- AC-3 (FR-4): Given the counterparty is not chosen, when the box is read, then it shows
  "Counterparty: not chosen yet"; and once chosen, the gap is replaced by a sentence. A
  required gap looks different from an optional one.
- AC-4 (FR-5): Given a question that only applies for another agreement type, when the
  requester is on a type where it does not apply, then the box says nothing about it; and
  when the requester changes to the type where it applies, then it appears as a sentence or a
  gap.
- AC-5 (FR-7, FR-8): Given an SOW with six deliverables and a value of INR 5,000,000, when the
  box is read, then deliverables show as a count and a few items, and the value shows with
  currency.
- AC-6 (FR-11, FR-12): Given the requester reaches the review step, then the same text appears
  in a larger editable box, and the list of answers is still shown.
- AC-7 (FR-13): Given the requester edited the text, when they choose to undo edits, then the
  text built from the answers returns.
- AC-8 (FR-14, FR-17): Given the box says "under Indian law", when the requester types "under
  English law", then "Update the answer?" appears showing old and new, and the governing law
  answer is unchanged until they click.
- AC-9 (FR-15): Given "Needed by 15 Nov 2026", when the requester types "needed by 1/12/2026",
  then the offer shows the new date as read; and given "lasts 2 years", when they type "lasts
  3 years", then the offer shows 3 years. Given a date that can be read two ways and cannot be
  settled, then no update is offered and the edit is kept as an extra instruction (FR-18).
- AC-10 (FR-16): Given "Purpose: evaluating a joint analytics project", when the requester
  rewords it, then the offer carries the new wording as the purpose.
- AC-11 (FR-18, FR-27): Given an edit that names a new party, when the box is checked, then no
  answer changes, and the edit is listed under extra instructions with "This differs from your
  answer: Initech Software Ltd".
- AC-12 (FR-19): Given one sentence with both the term and the law changed, when checked, then
  each one that can be matched gets its own offer and the other follows FR-18.
- AC-13 (FR-20): Given the requester accepts an offer that changes the NDA kind, then the
  answer changes, the box rebuilds, the routing panel and any NDA check react as if changed on
  the form, and the offer closes.
- AC-14 (FR-21): Given the requester declines an offer, then the answer is unchanged and the
  edit appears under extra instructions with the note.
- AC-15 (FR-22): Given the requester types a counterparty name that is not in the register, then
  the counterparty pick is unchanged, no register entry is created, and the edit is kept as an
  extra instruction.
- AC-16 (FR-23): Given the requester edited "lasts 2 years" to "lasts 3 years" without
  accepting, and then changed the term to 4 years on the form, then the box says "lasts 4
  years" and "lasts 3 years" is listed under extra instructions with "This differs from your
  answer: 4 years".
- AC-17 (FR-24): Given the requester deletes the optional line "Needed by 15 Nov 2026", then
  "Clear the answer?" is offered and the answer is cleared only on a click; given they delete
  the line of a required question, then they are told it cannot be cleared, the line returns
  and the answer is unchanged.
- AC-18 (FR-25, FR-26, FR-28): Given the requester adds "please send a copy to procurement",
  when the request is filed, then it is saved as an extra instruction with who and when, apart
  from the answers; and given no extra instructions, then none are saved.
- AC-19 (FR-29): Given extra instructions on the review step, then the requester sees them as a
  list and can remove one, and it is not saved on filing.
- AC-20 (FR-30): Given a filed request with extra instructions, when a permitted person opens
  the request page, then the extra instructions show, with notes, apart from the answers.
- AC-21 (FR-31): Given a request with extra instructions, when it is drafted and routed, then
  the draft, routing and approvals are the same as without them.
- AC-22 (FR-32): Given instructions past about 2,000 words, then the requester is told plainly
  to shorten them or attach them as a file, nothing they typed is deleted, and within the
  limit filing is never blocked.
- AC-23 (FR-33, FR-35): Given the requester edited the text but accepted no offers, when the
  request is filed, then the contract draft is built from the answers only, and the request
  page shows the saved summary text as built from the answers at filing time.
- AC-24 (FR-34): Given an offer still open when the requester presses Submit, then nothing is
  filed, the waiting-changes message shows and focus moves to the offer; after the requester
  updates or keeps it, Submit files.
- AC-25 (FR-36): Given a half-filled form with extra instructions and an open offer saved as a
  draft, when the requester resumes it, then the extra instructions and the offer come back and
  the box is rebuilt from the current answers.
- AC-26 (FR-37): Given the requester clicks file twice, or files again after an error, then the
  extra instructions exist once.
- AC-27 (FR-38): Given a "Note to approvers" and extra instructions, then the note goes to
  approvers as today, extra instructions are not mixed with it, and the box reads the note
  out as a normal sentence.
- AC-28 (FR-39, Permissions): Given a person who may not see the request (for example behind an
  ethical wall), then they see no extra instructions and no sign of them; given the requester,
  Legal and an approver who can open the request, then each sees them.
- AC-32 (FR-39): Given a filed request, then nobody can edit, add to or remove its extra
  instructions, and a later note made in the request's comments leaves them unchanged.
- AC-29 (Audit): Given a requester files with extra instructions and one accepted offer, then
  the audit trail and history record the extra instructions saved (who, when, how many) and the
  changed answer with old and new values, without the full instruction text; and given a
  requester only types, declines or abandons the form, then no audit entry is written.
- AC-30 (FR-10): Given a keyboard-only or screen-reader user, then the box, the editable box,
  the offers and the extra instructions list can all be reached and used, and an offer is
  announced when it appears; on a small screen the box is still reachable.
- AC-31 (Scope): Given a request in one organisation, then extra instructions and summary
  text are never visible in another organisation.

## Out of scope

- Using AI to write, check or rewrite the summary, to understand edits, or to read extra
  instructions. Later feature.
- Drafting the contract from extra instructions, or adding terms beyond the template, including
  highlighting "AI added" terms in the draft for a reviewer to keep or remove. Later feature.
- Redlining against the playbook. Existing and separate.
- Changing which questions each form asks, their options, or their "only when" rules.
- Letting the requester pick or create a counterparty, entity or parent contract by typing in
  the box.
- Locking answers, or checking the text only after filing (other options considered and not
  chosen).
- Changing routing, triage, approvals or the draft because of extra instructions.
- Showing the summary in lists, search, reports or notifications.
- Translating the summary into other languages.
- Editing the summary text of a request that has already been filed.
- Changing who can see which requests or contracts.

## Edge cases & error behavior

- Answers change after the box was edited: the answers win; the old edit stays as an extra
  instruction with the "differs" note (FR-23).
- The requester deletes a whole generated sentence: "Clear the answer?" is offered, or for a
  required question the line returns (FR-24).
- One sentence changes two facts: each is handled on its own (FR-19).
- A question stops applying after an edit (the "only when" rules): its sentence goes from the
  box (FR-5); an unaccepted edit about it is kept as an extra instruction with the note, so
  nothing the requester wrote is lost. An offer about an answer that no longer applies is
  withdrawn.
- A required answer is empty: shown as a required gap (FR-4); the form's existing rules about
  filing apply unchanged.
- Row-table answers: read as a count and a few items (FR-7); an edit to one of those sentences
  is kept as an extra instruction unless it names exactly one clear change.
- Counterparty typed in the box but not in the register: never chosen, never created (FR-22).
- Money and dates in different forms ("INR 50 lakh", "5,000,000", "15/11/2026"): read where
  certain (FR-15); otherwise kept as an extra instruction. A value in another currency than the
  answer's is never offered as an update without the currency being clear.
- Offer ignored, then Submit: nothing is filed until the offer is updated or kept (FR-34).
- Very long extra instructions: about 2,000 words, plain warning, attach as a file instead,
  nothing lost (FR-32).
- Small screen: box still reachable (FR-10).
- Save as draft and resume: extra instructions and offers return (FR-36).
- Double submit: stored once (FR-37).
- The existing quick-retry check for repeated requests keeps working; extra instructions alone
  do not make two otherwise identical requests different. [Planning to confirm.]
- Box fails to load or build: one-line note; the form and filing work as usual (FR-9).
- Another side-panel block (the NDA check) is showing: both remain usable (FR-10).
- Requester without a right to see some register pick: the box shows only what the form
  already shows them.
- Empty form: the box lists the gaps only, with no sentences.
- Pasted text with formatting or very odd characters: stored as plain text.

## Open questions

None open. Decisions recorded:

- [x] FR-23: Edited a sentence, then changed the answer on the form: the box rebuilds from the answers and the old edit stays as an extra instruction with "This differs from your answer: X". Nothing typed is lost.
- [x] FR-24: Deleted a whole generated line: offer "Clear the answer?" like any other edit; if the question is required, say it cannot be cleared and put the line back.
- [x] FR-32: Extra instructions limited to about 2,000 words; past that, ask to shorten or attach as a file; never lose what was typed.
- [x] FR-38: "Note to approvers" stays separate (goes to approvers as today); extra instructions go with the request; the box also reads the approvers' note out as a normal sentence.
- [x] FR-39: Everyone who can open the request sees the extra instructions (same visibility and walls as the request). Fixed once filed; later notes go in the request's comments. Editing after filing stays out of scope.
- [x] FR-21/FR-34: An ignored or still-open offer at filing counts as declined; it never blocks filing.

## Context for planning

Non-binding notes for the architect; not requirements.

- The wizard is `frontend/src/app/(app)/intake/_agreement-forms.tsx`. Its last step is
  "Review and submit" and already shows an optional request name, a read-only list of every
  visible answer, a routing panel that updates live in the side column, and a "Note to
  approvers" textarea. `description` today comes from the first non-empty of purpose, reason,
  processing purpose or note to approvers.
- Question labels, options, required flags and "only when" rules live only in
  `backend/app/intake/agreement_forms.json` (loaded via the forms endpoint); the wizard adds
  layout only. Filing drops answers to questions that do not apply. The summary wording per
  question will need a home that does not duplicate that file (for example next to each
  question); this is a design question for the architect.
- Spec 012 put an "NDA check" panel first in the side column on every step; ordering with this
  box is a design choice.
- `nonstandard` ("Non-standard terms the customer asked for") is a textarea shown only for
  "Selling to a customer" or "Something else"; today it only shows on the approval card and is
  fed raw to the custom-draft prompt.
- Values are in INR unless a currency field says otherwise (`default_currency`).
- Half-filled wizards are saved through the existing draft-intake feature; extra instructions
  and open offers must survive it (FR-36).
- There is a duplicate-retry check on filing (same user, same answers, within 2 minutes);
  see FR-37 and the edge case.
- Filing is a mutation and follows the audit and timeline pattern (`write_audit_log`,
  `write_timeline_event`); beware the audit advisory-lock note in the project docs.
- Matching edits to answers (FR-14 to FR-21) is rule-based by design and could be done in the
  browser; the saved extra instructions and visibility rules are server-side. Where the
  matching runs is the architect's choice.
- FR-32 says "attach it as a file instead": the architect should check what request
  attachment path already exists and use it; this feature adds no new file handling.
- Per project rules this spec has no endpoint, table or component names beyond the notes above;
  those belong in plan.md.
