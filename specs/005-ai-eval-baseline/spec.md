# Feature Specification: AI Evaluation Baseline ("the exam")

Feature ID: 005-ai-eval-baseline
Created: 2026-10-06
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->
Amended: 2026-10-06 with the owner's planning-stage decisions D-11 to D-14 (cost, coverage, errors, notice period)

## Summary

AEGIS is about to change how it uses AI (read the whole document, new prompts, typed
facts, a database-style question tool). Today there is no trustworthy way to tell
whether any of those changes makes the AI better or worse. This feature builds a
repeatable "exam": a fixed set of questions with known-correct answers that the AI
takes before and after every change. We run it twice on today's unchanged system to
record today's score and how much that score wobbles from run to run (the "noise
band"). After that, a later change is only worth shipping if it beats the baseline by
more than the noise. The owner has no lawyers, so every answer key comes from one of
three places: public data labelled by real lawyers, contracts we deliberately break so
we know the answer, or checks that need no answer key at all. The only human labelling
is the owner confirming plain facts and routing choices in spreadsheets, and the model
being tested never writes its own answer key. Runs are started by hand, by the owner
only, and no money is spent without the owner's go-ahead for that run. This is Phase 0
of the AI redesign: Phase 1 (fail-closed checks, no model or prompt change) does not
depend on it; Phase 1b (whole-document review) does.

## User stories

- As the owner (not a lawyer), I want a fixed exam with answer keys that never came
  from the AI being tested, so that "the AI got better" is a measured fact and not a
  feeling.
- As the owner, I want today's score and its run-to-run wobble recorded before anything
  changes, so that I can tell a real improvement from luck.
- As the owner, I want to confirm facts on my 14 real contracts by reading one quoted
  sentence per fact in a spreadsheet, without needing legal training, so that the key
  for real contracts is trustworthy.
- As the owner, I want to pick the correct workflow for each of ~50 free-text requests,
  so that routing is scored against my decision, not an AI's guess.
- As the owner, I want to see an estimated cost and say "go" before any money is spent,
  and to have a cheap sample mode, so that I never get a surprise bill.
- As a developer on the redesign, I want a one-page baseline report and a way to
  compare a later change against it with a clear pass/fail rule, so that I can decide
  whether my change ships.
- As the person responsible for client confidentiality, I want real client contract text
  to go only to the AI and OCR providers we already use, and never into version control,
  so that running the exam cannot leak client data.
- As a finance owner, I want exam AI traffic and its cost kept apart from real usage, so
  that real cost reports stay clean.
- As the owner, I want the exam's test contracts kept in a separate throwaway database,
  so that my real development data is never touched.

## Functional requirements

Numbered, testable statements. Each must be verifiable by /verify.

Terms used here. **Exam**: the fixed questions plus the scoring. **Answer key**: the
known-correct answers. **Baseline**: the recorded scores of today's system from two full
runs on unchanged code. **Noise band**: how far a score moves between two runs when
nothing has changed. **Model under test**: the AI model doing the work being scored
(today claude-sonnet-4-6). **Past 25k**: an answer whose supporting text starts beyond
the first 25,000 characters of a document. **Owner**: the one person who runs the exam
and confirms its labels.

**The exam and where answer keys come from**

- FR-1: The system SHALL provide one exam that can be run on demand and that, given
  unchanged code, models, prompts and exam content, asks the same questions about the
  same documents every run and produces a scored report.
- FR-2: Every answer-key item SHALL record its source category (lawyer-labelled public
  data / constructed by us with a known answer / confirmed by the owner). For public data
  it SHALL also record the dataset name, version and licence. The report SHALL show
  scores split by source category.
- FR-3: No answer-key value SHALL be produced, chosen or corrected by the model under
  test. A value suggested by a different model (FR-16) SHALL count as part of the key
  only after the owner confirms it.
- FR-4: The exam SHALL include lawyer-labelled public data for (a) contract review and
  field extraction, expanded from the 30 contracts used today to about 40-50 long
  contracts, preferring items whose labelled passage is past 25k; (b) NDA verdicts
  (whether a statement about an NDA is supported, contradicted or not mentioned); and
  (c) questions that come with the exact passage that answers them. The report SHALL
  state the exact count in each set and how many items are past 25k.
- FR-5: The exam SHALL include constructed contracts built from public standard
  agreements and from AEGIS's own drafting templates. For each, a machine-readable answer
  record SHALL state every planted change: what was changed, the changed text (so it can
  be matched even when page positions differ), where it sits, the correct finding, and
  its severity. The severity SHALL be the one already set on the playbook rule the change
  breaks, fixed when the test contract is built, and SHALL never be set by an AI. The set
  SHALL also include unchanged control documents so false alarms can be counted.
- FR-6: The constructed set SHALL include Indian variants: Indian governing law, amounts
  in INR, and Indian-style company names and addresses, each with its own answer record.
- FR-7: The constructed set SHALL include documents long enough that some planted
  changes start past 25k. The report SHALL state how many, and SHALL score them
  separately from changes before 25k.
- FR-8: For a subset of constructed documents, the system SHALL produce scanned versions:
  each page rendered as an image, with a DocuSign-style stamp as the only text layer. Each
  scanned version SHALL share the answer record of its normal-text twin, and the report
  SHALL show the two scores side by side so the effect of the scan path is visible.
- FR-9: The exam SHALL include a test portfolio of about 40 constructed contracts with
  known facts, and portfolio-completeness questions (for example "which contracts renew
  automatically?"), each with the exact correct list. Each question SHALL be scored as
  found, missed and wrongly included.

**Checks that need no answer key**

- FR-10: For every review and extraction call, the report SHALL show how much of the
  document the AI was actually given (characters seen out of total), per call and on
  average.
- FR-11: The report SHALL show the truncation rate: the share of AI calls whose answer
  was cut off before it finished, per task type.
- FR-12: The report SHALL show the quote-validity rate: the share of quotes the AI returns
  that appear word for word in the source text (ignoring only differences in spacing and
  line breaks), and list the quotes that do not.
- FR-13: The report SHALL show run-to-run consistency: the share of items that received
  the same answer in both baseline runs.
- FR-14: The report SHALL show cost, token counts and time for each call type and in
  total.

**Owner-confirmed labels (the only human labelling)**

- FR-15: The owner-confirmed set SHALL consist of exactly these 14 contract files from
  the owner's Downloads folder and no other file: Employment Agreement.pdf; Franklin
  Madison - MSA - 1-23-19.pdf; INFRAHEDGE-MSA.pdf; IT Services Agreement - ABC_signed.pdf;
  MSA - Hearst.pdf; MSA.Pdf; MSA_1.Pdf; MSA_2020_6_000676.Pdf; MSA_2021_11_000942.Pdf;
  MSA_AAA PHARMACEUTICALS.pdf; MSA_Signed.pdf; "Mutual NDA — Northwind Robotics Inc..docx";
  NDA Form.pdf; Patent_License_Agreement 1.pdf. The system SHALL refuse to open any other
  file in that folder.
- FR-16: For each of the 14 contracts, Claude Sonnet 5.5 (claude-sonnet-5-5), at the AI
  provider the app already uses, SHALL pre-fill proposed answers for: parties, key dates
  (start and end or term), governing law, notice period (the notice needed to end the
  contract or to stop it renewing), and renewal terms, each with an
  exact quote from the contract. The pre-fill model SHALL NOT be the model under test; IF
  the two would be the same model, the system SHALL refuse to pre-fill.
- FR-17: Before a pre-filled answer goes to the owner, the system SHALL check that its
  quote appears word for word in the contract text. An answer whose quote does not appear
  SHALL be shown as "no supporting sentence found" and SHALL NOT be markable as correct
  as it stands (the owner can mark it wrong and supply the right value).
- FR-18: The system SHALL produce a spreadsheet for the owner to fill in; there is no
  in-app labelling page. Each row SHALL show the question, the pre-filled answer, and the
  exact sentence found in the contract with where it sits. The owner SHALL mark each row
  correct or wrong and, if wrong, write the correct value (including "not stated in the
  document" where that is the truth).
- FR-19: Only rows marked correct, or marked wrong with a corrected value, SHALL enter the
  answer key. A row left unmarked (the owner cannot tell) SHALL be left out of the key and
  counted in the report. The report SHALL show how often the owner changed the pre-filled
  answer. A returned spreadsheet whose rows cannot be matched to the questions it was made
  from SHALL be rejected as a whole, not loaded in part.
- FR-20: Each owner-confirmed key SHALL be tied to one frozen copy of the contract's text
  (for scans, the text read once from the scan) and to a fingerprint of the file. WHEN the
  file or its frozen text changes, the system SHALL mark that contract's key out of date
  and leave it out of scoring until the owner re-confirms. Reading the 11 image scans with
  the OCR provider already in use is approved; it is a one-time step that still shows its
  estimated cost and takes the owner's go-ahead (FR-31), and every later run SHALL reuse
  the frozen text rather than pay for OCR again.
- FR-21: The routing set SHALL contain about 50 free-text intake requests (email, chat or
  notice style, not form requests), drafted by an AI and spread across today's 32
  workflows; the report SHALL state how many of the workflows have at least one request
  and how many requests each has. The drafting model SHOULD NOT be the model under test,
  and the record SHALL say which model drafted them. Drafted requests are inputs only,
  never an answer key: for each, the owner SHALL pick the workflow it should go to, in the
  same spreadsheet style as FR-18, and that pick is the key. The system SHALL store which
  workflow list the pick was made against.

**Scoring and the baseline report**

- FR-22: Contract review SHALL be scored for precision, recall and F1 against the answer
  keys, and the report SHALL list each missed high or critical finding by name.
- FR-23: Field extraction SHALL be scored per field. Parties SHALL be scored as separate
  parties, not one text string. Both the key and the AI answer SHALL be normalised before
  comparing (dates, amounts, company-name suffixes, party order), so that formatting
  differences are not counted as errors. Accuracy SHALL be reported separately for items
  before 25k and past 25k. The 2 contracts whose only text layer is a stamp (DocuSign,
  DPDP) SHALL be scored twice: on the full frozen text in the main figures, and also as
  the app reads them today, reported on its own line and never mixed into the main
  figures, so the current OCR gap and its later fix are both visible.
- FR-24: Question answering SHALL be scored by evidence-found rate: whether the answer
  cites the key's passage. The AI SHALL be given the context the app's own search returns
  for that question, exactly as a user's question in the app would get, and never an empty
  one. The report SHALL show separately how often search found the key's passage and how
  often the final answer cited it, each split before 25k and past 25k.
- FR-25: Routing SHALL be scored as the share of requests sent to the owner's chosen
  workflow, against the workflows that exist today.
- FR-26: The baseline report SHALL contain, each with its item count (n) and a one-line
  plain-language explanation: review F1 and missed high/critical findings; per-field
  extraction accuracy before and after 25k; extraction accuracy for the 2 stamp-only scans
  as the app reads them today (own line); question-answering evidence-found rate;
  portfolio completeness; truncation rate; quote-validity rate; document coverage;
  consistency; routing accuracy; the noise band for each score; and the run's cost.
- FR-27: The baseline SHALL be a named pair of full runs on the same code version, model
  versions, prompt versions and exam content. The report SHALL show each run's score, the
  average and the noise band (the difference between the two runs) per headline measure.
  A pair in which either run had more than 1% of its AI calls fail SHALL NOT be accepted
  as a baseline; at or under 1% it may be accepted with its "contains errors" flag shown.
  A pair that differs in any of those, or in which either run is a sample or incomplete,
  SHALL NOT be accepted as a baseline. Replacing a baseline SHALL need an explicit owner
  action, and earlier baselines SHALL be kept.
- FR-28: The system SHALL compare a later change against the baseline in the same report
  format. The change SHALL be run twice (two full runs meeting the same rules as FR-27),
  and the average of its two runs compared with the baseline average. The change PASSES
  only if (a) the main measure for the area it targets improves by more than that
  measure's noise band, AND (b) no other headline quality measure worsens by more than its
  own noise band. For the lower-is-better quality measure (truncation rate) "improves" and
  "worsens" are reversed. Cost and time SHALL always be reported next to the scores (the
  change, the baseline and the difference) but SHALL NOT decide pass or fail; the owner
  weighs them. Document coverage and run-to-run consistency SHALL likewise be reported but
  SHALL NOT decide pass or fail. The targeted area and its main measure SHALL be named and
  recorded before the change's runs are made; the main measure is review F1 for review,
  per-field extraction accuracy for extraction, evidence-found rate for question
  answering, routing accuracy for routing, and portfolio completeness for portfolio
  questions. The report SHALL show per measure the change, its noise band and pass or
  fail, and name every measure that failed. The system SHALL refuse a comparison against
  different exam content. The system only reports the verdict; it does not itself block
  anything from shipping.
- FR-29: The earlier results (baseline files dated 2026-09-28 and 2026-09-30) SHALL be
  kept as history, labelled "earlier, superseded", and SHALL NOT be used as the baseline.
  Their three known defects (an empty search context in question answering, parties
  scored as one string with un-normalised key values, and a routing set built on 13 old
  workflows with AI-written labels) SHALL NOT be carried into this exam.

**Spending money**

- FR-30: The exam SHALL have a sample mode that runs a small fixed subset (including some
  items past 25k and some of each task type) and a full mode that runs everything. The
  report SHALL state the mode, and sample results SHALL be labelled as not comparable to
  the baseline.
- FR-31: In Phase 0 the exam SHALL be started only by deliberate manual action by the
  owner. There SHALL be no CI trigger, schedule or other automatic run. Before any run
  that makes paid calls (AI or OCR), the system SHALL show the estimated cost and the
  number of calls (OCR listed separately), and SHALL make no paid call until the owner
  gives an explicit go-ahead for that run. A go-ahead SHALL NOT carry over to another run.
  Anyone other than the owner, including a platform administrator, SHALL be refused when
  starting a run or giving a go-ahead.
- FR-32: The owner SHALL set a spending ceiling at go-ahead. WHEN the run's actual spend
  reaches it, the system SHALL stop making paid calls, mark the run incomplete and report
  what was spent. A full run is expected to cost about $8-10 (right-sized exam, discounted
  batch mode, FR-41).
- FR-41: To keep cost low, exam calls on public and constructed content SHALL use the AI
  provider's discounted batch mode (same model and same request settings, results returned
  later). Calls that involve the owner's 14 real contracts SHALL NOT use batch mode. The
  estimate SHALL tell the owner that results may take up to 24 hours (usually under one).
  Each run SHALL record its delivery mode; a baseline and any change compared against it
  SHALL use the same delivery mode, and the system SHALL refuse a comparison across modes.
  Items whose batch results do not come back within the provider's window SHALL be counted
  as failed calls (FR-40).
- FR-33: Every run SHALL report its own actual cost against its estimate, in total and per
  task type.
- FR-34: Exam AI calls SHALL use an AI-provider credential dedicated to the exam, and the
  system SHALL refuse to run rather than fall back to the credential used for real work.
  Every exam call SHALL be marked as exam traffic in the AI call record. Real-usage cost
  figures SHALL NOT include exam traffic, and exam traffic's cost SHALL be viewable on its
  own.

**Data safety and footprint**

- FR-35: Real client contract text (the 14 contracts, their frozen text, their OCR output)
  SHALL be sent only to the AI provider and the OCR provider the app already uses, and to
  no other third party, including when it is sent to the pre-fill model. Public datasets
  and constructed contracts are not client data and carry no such limit.
- FR-36: Real contract files, their text, OCR output, the label spreadsheets, and any
  answer-key or result entry that quotes them SHALL never be committed to version control
  and SHALL be kept in a location that version control ignores. Anything committed
  (including the baseline report) SHALL contain scores and counts only, with no contract
  text.
- FR-37: The exam SHALL read only its own fixtures and the 14 owner-supplied contracts. It
  SHALL NOT read, search or send to the AI any contract belonging to real users. The
  question-answering search SHALL run only over the exam's own contracts.
- FR-38: The exam's contracts, and everything derived from them (their text, search index
  and processing jobs), SHALL live only in a separate throwaway database. The real
  development data SHALL never be written to or read from for exam contracts, and the
  throwaway database SHALL be rebuildable from the stored fixtures. The only things the
  exam may add to the live database are AI call records marked as exam traffic (FR-34)
  and audit entries (FR-39). The exam SHALL NOT cause any outbound email, notification,
  reminder or workflow run.
- FR-39: Every run SHALL leave a run record, and an entry in the platform's audit trail,
  capturing who started it, when, mode, code/model/prompt/exam versions, the estimate,
  the approved ceiling and who approved it, the outcome (complete, incomplete, stopped)
  and the actual cost. A finished run's record SHALL NOT be editable.
- FR-40: A failed AI or OCR call SHALL be counted and listed in the report, never silently
  dropped and never scored as if it were an answer. A run with any failed call SHALL be
  flagged "contains errors". A run that did not attempt every item (stopped, crashed, or
  hit its ceiling) SHALL be flagged "incomplete".

## Permissions, scoping & audit

This platform is single-tenant (one organisation; `org_id` is still written and filtered
on everywhere) with role-based access, ethical walls, and an immutable audit trail. This
feature is internal quality tooling, not a customer-facing feature.

- **Start a sample or full run** (FR-30, FR-31): the owner only. Platform administrators
  who are not the owner, and every ordinary user, are refused. Never automatic.
- **Give the spending go-ahead and set the ceiling** (FR-31, FR-32): the owner only, for
  each run separately (and for the one-time OCR step, FR-20). Audit: who, when, estimate
  shown, ceiling set, go or no-go.
- **Fill in the label spreadsheets** (FR-18, FR-19, FR-21): the owner only. No one else's
  marks enter the key, and the AI never confirms its own suggestion. Audit: when the
  completed spreadsheet is loaded, record who loaded it, when, the spreadsheet's
  fingerprint, and per row the suggested value and quote and the final value.
- **Designate or replace the baseline** (FR-27): the owner. Audit: who, when, which two
  runs, and what it replaced.
- **Read the 14 real contracts and send them to OCR/AI** (FR-15, FR-35): limited to the
  named files. Audit: each time a real contract is sent to the OCR provider or the pre-fill
  model, record which file, when, who started it and the cost.
- **Change exam content (questions or answer keys)**: through the normal reviewed change
  process. Any change makes earlier baselines non-comparable (FR-28) and needs a new
  baseline.
- **Read run results**: detailed per-item results from real contracts contain contract
  text, stay in the git-ignored location (FR-36), and are for the owner. The committed
  baseline report has no contract text and may be read by the whole team.
- **Data scope** (FR-37, FR-38): the exam's contracts live in their own throwaway database
  and never appear to real users in search, Ask, portfolio views, reports, analytics or
  notifications; the exam does not reach real users' contracts, so no ethical wall needs
  to be crossed.
- **Audit trail summary**: a run (FR-39), each spend approval or refusal (FR-31), each
  real-contract send to OCR or the pre-fill model, each spreadsheet load, each baseline
  designation.

## Acceptance criteria

Given/When/Then scenarios that define "done". Every criterion maps to at least one
functional requirement.

- AC-1 (FR-1): Given unchanged code, models, prompts and exam content, when the full exam
  runs twice, then both runs use the identical list of questions and documents and each
  produces a scored report.
- AC-2 (FR-2): Given the answer keys, when every item is inspected, then each has a source
  category, each public-data item names its dataset, version and licence, and the report
  shows scores split by category.
- AC-3 (FR-3): Given the record of how the answer keys were made, when it is checked, then
  the model under test authored or corrected no value, and a pre-filled value the owner
  has not confirmed is absent from the key.
- AC-4 (FR-4): Given the public sets, when the report is produced, then it states the
  count in each of the three sets and how many items are past 25k, and the review and
  extraction set is about 100 contracts.
- AC-5 (FR-5): Given a constructed contract, when its answer record is read, then it lists
  each planted change with its text, location, correct finding and severity, and the
  severity equals the one set on the playbook rule the change breaks; and given an
  unchanged control document, when the AI reports a problem in it, then it counts as a
  false alarm.
- AC-6 (FR-6): Given the constructed set, when it is inspected, then it contains
  documents with Indian governing law, INR amounts and Indian-style entities, each with an
  answer record.
- AC-7 (FR-7): Given the constructed set, when the answer records are checked, then a
  stated number of planted changes start past 25k and the report scores them apart from
  earlier ones.
- AC-8 (FR-8): Given a constructed document with a scanned version, when the scan is
  opened, then its pages are images whose only text is the stamp text, it uses the same
  answer record as its text twin, and the report shows both scores side by side.
- AC-9 (FR-9): Given a portfolio question whose correct answer is 7 contracts, when the
  AI names 5 right and 1 wrong, then the report shows 5 found, 2 missed, 1 wrongly
  included.
- AC-10 (FR-10): Given a review call where the AI was given 25,000 of a 60,000-character
  document, when the report is produced, then that call shows 42% coverage and the report
  shows the average coverage.
- AC-11 (FR-11): Given a call whose answer was cut off before it finished, when the report
  is produced, then it is counted as truncated and the rate per task type is shown.
- AC-12 (FR-12): Given an AI answer with 10 quotes, 8 word for word in the source and 2
  not, when the report is produced, then quote validity is 80% and the 2 quotes are
  listed.
- AC-13 (FR-13): Given two baseline runs, when the report is produced, then it shows the
  share of items answered the same in both.
- AC-14 (FR-14): Given a finished run, when the report is produced, then it shows cost,
  tokens and time per call type and in total, and the totals equal the sum of the parts.
- AC-15 (FR-15): Given a request to load a file that is not among the 14 (for example a
  personal document in the same folder), when the exam tries to use it, then it is
  refused and the file is not opened.
- AC-16 (FR-16): Given the pre-fill step, when it finishes, then the pre-fill model
  recorded is claude-sonnet-5-5 and it is not the model under test; and given a setup in
  which the pre-fill model and the model under test are the same, when pre-fill is
  requested, then it is refused.
- AC-17 (FR-17): Given a pre-filled answer whose quote is not in the contract text, when
  the spreadsheet is produced, then the row reads "no supporting sentence found" and
  cannot be marked correct as it stands.
- AC-18 (FR-18, FR-19): Given a spreadsheet row with a pre-filled governing-law answer and
  its quoted sentence, when the owner marks it correct, then it enters the key; when the
  owner marks it wrong and writes a value, the written value enters the key; when the row
  is left unmarked, it is left out of the key and counted; and given a returned
  spreadsheet whose rows do not match the questions it was made from, then the whole
  file is rejected.
- AC-19 (FR-19): Given finished labelling, when the report is produced, then it shows how
  often the owner changed the pre-filled answer.
- AC-20 (FR-20): Given a contract with a confirmed key, when its file or frozen text
  changes, then its key is marked out of date and left out of the next run's scoring
  until re-confirmed; and given the 11 scans have not yet been read, when OCR is about to
  run, then the estimate is shown and no OCR call is made until the owner's go-ahead, and
  afterwards later runs make no OCR call for them.
- AC-21 (FR-21): Given the ~50 AI-drafted requests, when the set is inspected, then the
  drafting model is recorded, the report states how many of the 32 workflows have at least
  one request, and each request's key is the owner's pick together with the workflow list
  it was made against; a request with no owner pick is left out and counted; and when a
  workflow is later added or removed, the routing section is flagged out of date.
- AC-22 (FR-22): Given a key with 10 planted changes of which the AI finds 8 (one missed
  change is critical) and raises 2 false alarms, when scored, then the report shows
  precision, recall and F1, and names the missed critical finding.
- AC-23 (FR-23): Given a key with parties "Acme Ltd" and "Beta Pvt Ltd" and start date
  "1 April 2024", when the AI answers "Beta Pvt Ltd, Acme Ltd." and "2024-04-01", then
  both count as correct; given an item past 25k, then it is counted in the past-25k
  figure only; and given the 2 stamp-only scans, when scored, then the main figures use
  their full frozen text and a separate line shows the score when the app reads them as it
  does today.
- AC-24 (FR-24): Given a question whose answer passage exists in an exam contract, when
  question answering runs, then the AI was given the context the app's search returned
  (not empty), and the report shows separately whether search found the passage and
  whether the answer cited it, each before and past 25k.
- AC-25 (FR-25): Given the owner's routing picks, when scored, then the report shows the
  share of requests routed to the picked workflow among today's workflows.
- AC-26 (FR-26): Given a finished baseline, when the report is read, then every measure
  listed in FR-26 is present with its item count and a one-line plain explanation.
- AC-27 (FR-27): Given two full runs on the same code, models, prompts and exam content,
  when they are designated the baseline, then the report shows each run, the average and
  the noise band per headline measure; and given a second run made on different code, a
  sample run or an incomplete run, then it is refused as part of a baseline pair.
- AC-28 (FR-28): Given a baseline with review F1 runs of 0.80 and 0.84 (average 0.82,
  noise band 0.04), when a change targeting review has two runs averaging 0.87 and every
  other measure moved less than its own noise band, then the verdict is pass; when instead
  quote validity fell by more than its noise band, then the verdict is fail and names
  quote validity; when truncation rate (lower is better) rose by more than its noise
  band, then that counts as worsening; when cost per run doubled but every quality measure
  passed, then the verdict is still pass and the cost and time differences are shown
  next to it; when the targeted area and main measure were not
  recorded before the change's runs, then no verdict is given; and given a run made
  against different exam content, then the comparison is refused.
- AC-29 (FR-29): Given the report and the stored history, when inspected, then the earlier
  results are labelled "earlier, superseded" and are not in the baseline, and the three
  known defects are absent from the exam.
- AC-30 (FR-30): Given sample mode, when it runs, then it uses a small fixed subset
  including past-25k items and every task type, its report is labelled not comparable to
  the baseline, and its estimated cost is lower than a full run's.
- AC-31 (FR-31): Given no deliberate action by the owner, when time passes or CI runs,
  then no exam run starts; given a run started by the owner with no go-ahead, then the
  estimate is shown and no paid call is made; given a go-ahead for run A, when run B
  starts, then run B asks again; and given a platform administrator who is not the owner,
  when they try to start a run or give a go-ahead, then they are refused.
- AC-32 (FR-32): Given a ceiling of $30, when actual spend reaches $30, then paid calls
  stop, the run is marked incomplete, and the report states the spend.
- AC-33 (FR-33): Given a finished run, when the report is read, then actual cost is shown
  next to the estimate, in total and per task type.
- AC-34 (FR-34): Given the exam credential is not set, when a run starts, then it refuses
  and does not use the real-work credential; and given a finished run, then its calls are
  marked as exam traffic, absent from real-usage cost figures, and viewable on their own.
- AC-35 (FR-35): Given a full run including the 14 real contracts and the pre-fill step,
  when outbound destinations are inspected, then client contract text went only to the AI
  provider and the OCR provider already in use.
- AC-36 (FR-36): Given a finished labelling and run session, when version control is
  checked, then no real contract file, text, OCR output, label spreadsheet or quoting
  key/result entry is tracked or staged, and the committed baseline report contains no
  contract text.
- AC-37 (FR-37): Given real users' contracts also exist in the live database, when
  question answering runs, then none of them appears in the context sent to the AI.
- AC-38 (FR-38): Given a full run, when it finishes, then the live database contains no
  exam contract and nothing exam-derived apart from AI call records marked as exam traffic
  and audit entries; no email, notification, reminder or workflow run was triggered; and
  when the throwaway database is deleted, then it can be rebuilt from the stored fixtures.
- AC-39 (FR-39): Given a finished run, when the audit trail is read, then it has an entry
  with who, when, mode, versions, estimate, ceiling and approver, outcome and actual cost,
  and the run record cannot be edited.
- AC-40 (FR-40): Given an AI call that fails after retries, when the run finishes, then
  the failure is listed, the run is flagged "contains errors", and the item is not scored
  as an answer; and given a run that crashes part-way, then it is flagged "incomplete" and
  cannot become part of a baseline.
- AC-41 (FR-41): Given a full run, when it runs, then public and constructed items are sent
  in the provider's discounted batch mode and every call involving the 14 real contracts is
  sent normally; the estimate states the up-to-24-hour wait; the report records the
  delivery mode; given a baseline made in batch mode and a change run made normally, then
  the comparison is refused; and given batch items that never come back, then they are
  counted as failed calls.

## Out of scope

- Any change to production AI behaviour: prompts, models, settings, or how contracts are
  read. This feature only measures. Fixing what it finds (truncation, stamp-only scans
  skipping OCR, misses past 25k) belongs to later phases.
- Phase 1 (fail-closed checks) and Phase 1b (whole-document review), document-first
  prompts, typed facts, and the database-style question tool. This feature is what they
  will be measured against, not part of them.
- Merging the `ai-redesign/step-0` branch (603 files diverged). Only selected eval work is
  brought across.
- Any answer key or label from a lawyer, or any claim that a score proves legal
  correctness. The exam measures agreement with the keys defined here.
- Comparing other AI vendors' or models' quality as such. Later phases will use the exam
  for that; this feature only builds it and records today's baseline.
- Any change to the AEGIS drafting templates or playbooks themselves. The exam only reads
  them.
- Any contract file other than the 14 named, and the other documents in the owner's
  Downloads folder (never opened).
- Running the exam in CI, on a schedule, or by anyone other than the owner (FR-31).
- An in-app page for labelling or for viewing results; labelling is by spreadsheet
  (FR-18).
- Automatic quality gates that block deployment or shipping. The exam reports a pass or
  fail verdict (FR-28); the owner decides what to do with it.
- Languages other than English, and contract types beyond those in the public sets and
  the NDA / MSA / DPA / CSA templates covered here.
- A public leaderboard, publishing the datasets or results externally, or a customer-
  facing quality dashboard.

## Edge cases & error behavior

- An answer that is genuinely absent from a document: the key says "not stated"; an AI
  answer of "not stated" is correct and an invented value is wrong. Exception (D-16): such a
  key on renewal_term, notice_period or termination_for_convenience is counted, not scored.
- The owner cannot decide: the row is left unmarked and counted (FR-19); the system never
  guesses on the owner's behalf.
- A pre-fill quote that is not in the text (FR-17) is shown as unsupported, never
  silently accepted.
- Scanned documents have different text positions from their twins; planted changes are
  matched by their text, not by position (FR-5, FR-8).
- The 2 stamp-only scans are scored both on full frozen text and as the app reads them
  today, with low coverage (FR-10) shown, never hidden (FR-23).
- If the pre-fill model and the model under test would be the same (for example if a
  later phase makes claude-sonnet-5-5 the model under test), pre-fill is refused until a
  different pre-fill model is chosen; keys the owner already confirmed stay valid (FR-16).
- A playbook rule with no severity set: no change is planted for it and the build says so;
  a severity is never made up (FR-5).
- The workflow list changes after routing picks were made: routing is flagged out of date
  rather than scored against picks that no longer apply (FR-21).
- Exam content changes (new questions, new keys, new datasets): earlier baselines are not
  comparable and a new baseline pair is needed (FR-28).
- A change improves quality but costs more or is slower (for example reading the whole
  document uses more tokens): it can still pass, because cost and time never decide pass
  or fail; the report shows the cost and time difference prominently and the owner weighs
  it (FR-28, D-10).
- Small buckets (for example the handful of items past 25k) wobble more: every number
  shows its item count so a small bucket is not over-read (FR-26).
- The AI provider has an outage mid-run: failures are counted and the run flagged; the
  owner decides whether to re-run (FR-40). A re-run needs its own go-ahead (FR-31).
- The spending ceiling is hit part-way: the run stops, partial results are shown but
  marked incomplete and cannot be a baseline (FR-32).
- A required public dataset is unavailable or its licence cannot be recorded: that set is
  reported as missing rather than substituted silently (FR-2, FR-4).
- Score noise is zero or tiny on some measure: the noise band is reported as measured, not
  widened, and a later change on it needs only to beat that measured band (FR-27, FR-28).
- A real contract text file, answer key or label spreadsheet is about to be staged for
  commit: the commit must not include it (FR-36).

## Decisions

Answers the owner gave to the questions raised while drafting; each is folded into the
requirements above.

- D-1: The pre-fill model for the 14 real contracts is Claude Sonnet 5.5
  (claude-sonnet-5-5), at the provider already in use, so no new third party. It must
  never be the model under test (claude-sonnet-4-6). The owner confirms every pre-filled
  answer against its quoted sentence. (FR-16, FR-35)
- D-2: Phase 0 runs are manual only, started only by the owner, who also approves the
  spend for each run. No CI and no schedule. (FR-31, Permissions)
- D-3: "Beats the baseline" means: over two runs, the main measure for the area improves by
  more than its noise band AND no other measure worsens by more than its noise band;
  lower-is-better measures such as truncation rate and cost are reversed. (FR-28)
- D-4: Labelling is by spreadsheet: each row shows the question, the pre-filled answer and
  the exact sentence found; the owner marks correct or wrong and corrects the value if
  wrong. No in-app page. (FR-18, FR-19)
- D-5: OCR of all 11 scans with the provider already in use is approved. The 2 stamp-only
  scans are also scored as the app reads them today, reported separately. (FR-20, FR-23)
- D-6: A separate throwaway test database for the exam's contracts is approved; real
  development data is never written to or read from for exam contracts. (FR-37, FR-38)
- D-7: The ~50 routing requests are drafted by an AI (preferably not the model under test)
  across today's 32 workflows; drafting is not an answer key. The owner picks the correct
  workflow for each, and that pick is the key. (FR-21)
- D-8: Same as D-2: only the owner starts runs and gives go-aheads. (FR-31, Permissions)
- D-9: The severity of a planted change is the one already set on the playbook rule it
  breaks, fixed when the test contract is built, never set by an AI. (FR-5)
- D-10: Cost and time are always reported but never decide pass or fail; only quality
  measures do (truncation rate counts as a quality measure). This refines D-3. (FR-28)
- D-11: Cheapest real exam: a right-sized set (about half the originally planned volume)
  run in the provider's discounted batch mode, about $8-10 per full run; calls involving
  the 14 real contracts stay out of batch mode. A free model cannot stand in for the model
  under test, because the exam measures the AI the app actually uses. (FR-4, FR-32, FR-41)
- D-12: Document coverage and run-to-run consistency are report-only, like cost. (FR-28)
- D-13: A run may form part of a baseline only if 1% or fewer of its AI calls failed,
  with the "contains errors" flag shown. (FR-27)
- D-14: "Notice period" means the notice needed to end the contract or to stop it
  renewing. (FR-16)
- D-15 (2026-10-07, owner's instruction): the owner delegated the first facts sheet
  (`facts-282c9b1ca22f`) to Claude Opus 5.5: a blind read per contract, a judge against the
  pre-fill, a skeptic on every contested row, ambiguous rows left blank. Each row's notes say
  so. The routing sheet (`routing-3b07e5f8dcb1`) was delegated the same way (blind pick,
  judge, skeptic; ties broken by the app's own select_flow order). Those keys are AI-checked,
  not owner-confirmed, until the owner spot-checks them; an error every Claude model shares
  can survive this check.
- D-17 (2026-10-08, owner's instruction): no AEGIS password and no typed run code. Commands run
  as the owner in `exam.env` (`--owner` optional); a paid run shows its estimate and asks only
  for the spending ceiling, which is the go-ahead (Enter cancels). Supersedes the password and
  run-code parts of FR-31.
- D-16 (2026-10-07, owner approved): a "not stated" key on renewal_term, notice_period or
  termination_for_convenience is counted, not scored. The exam answers those fields with every
  app clause of a broad kind (term and termination, notices), so nearly every contract
  "answers" them and the key could never be passed. Shown as its own count in both reports.

Wording added by the analyst to make the decisions testable (the owner may reverse any of
these at approval):

- A-1: The main measure per area for D-3 is review F1, per-field extraction accuracy,
  evidence-found rate, routing accuracy, and portfolio completeness, and the area and its
  main measure are named before the change's runs (FR-28).
- A-2: The one-time OCR step (D-5) still shows its estimate and needs the owner's go-ahead
  like any paid step (FR-20, FR-31).
- A-3: Exam AI call records and audit entries stay visible in the live cost and audit
  views, marked as exam traffic, while exam contracts live only in the throwaway database
  (FR-34, FR-38).
- A-4: Detailed results from real contracts are for the owner only; the committed report
  has no contract text (Permissions).
- A-5: Pre-fill is refused if it would be the model under test; a playbook rule with no
  severity is never used for a planted change; a spreadsheet that does not match its
  questions is rejected whole (FR-16, FR-5, FR-19).

---

### Context for planning (non-normative)

Implementation-adjacent facts recorded to save the architect re-deriving them. They are
not requirements and carry no normative weight.

- **Earlier work on `ai-redesign/step-0`** (verified by the coordinator via git; not
  merged). `backend/eval/` there holds `__init__.py`, `audit_pipeline.py`, `baseline.py`,
  `build_datasets.py`, `golden.json`, `labels.py`, `labels/README.md`,
  `labels/extraction.json` (31 items), `labels/review.json` (51), `labels/qa.json` (202),
  `labels/routing.json` (51), `labels/playbooks/` (`dpa_eval_playbook.json`,
  `msa_eval_playbook.json`, `nda_checklist.json`), `results/` (baseline JSON dated
  2026-09-28 and 2026-09-30), `run_eval.py`, `score.py`, `seed_golden_fixtures.py`. Its
  README gives the sources: review = 20 NDAs from ContractNLI plus 20 MSAs and 10 DPAs
  built from Common Paper templates with planted changes (answers known by construction);
  Q&A = 200 LegalBench-RAG questions with exact answer passages; extraction = 30 CUAD
  contracts, 20 of them longer than 25,000 characters; routing = 50 requests marked
  "Drafted by AI - a person must confirm each one". Raw contracts sit in a git-ignored
  `eval/contracts/` folder rebuilt by `python -m eval.build_datasets`. Its 2026-09-30
  numbers (review F1 0.79-0.83, extraction about 46-49%, Q&A evidence 65% with 2 of 15
  past 25k, routing 0.90) are the "earlier, superseded" history of FR-29. Its `baseline.py`
  sends `"retrieved_context": ""` for Q&A (confirmed, around line 166), so its Q&A score
  tests the model, not the app's search. Port selected files only.
- **Public data named by the requester** (for FR-4): CUAD (510 contracts; 30 used today,
  target about 100, prioritising labelled passages past 25,000 characters), ContractNLI
  (NDA verdicts), LegalBench-RAG (questions with exact answer passages). Licences must be
  recorded (FR-2); not checked. Constructed set sources: Common Paper CSA / DPA / NDA and
  `backend/app/drafting_templates/defaults/` (nda, msa, dpa, saas, consultancy, sow,
  vendor).
- **Severity source (FR-5), for the planner to settle.** The branch ships three eval
  playbook files; the platform also has its own playbooks. Confirm which one a planted
  change's severity is read from.
- **Existing eval footprint on `drl-saket-v2`.** `seed_golden_fixtures.py` creates one real
  contract through `create_contract_from_upload` and runs the real AI jobs, so today's
  eval already writes real rows. `run_eval.py` runs retrieval with `scope="portfolio"` as
  `admin@example.com`, so it also searches real contracts, which FR-37 forbids for the new
  exam. Q&A retrieval to use is `hybrid_sources` in `contract_brain/retrieval.py` (the
  same function Find and Ask use), restricted via `resolve_scope_contract_ids`. The
  `run_faithfulness` in this tree already passes real `hybrid_sources` output; the
  empty-context runner is only on the branch.
- **AI call record.** `AICallLog` (`app/core/models.py`, written by
  `app/ai/gateway/ledger.py`) already stores model, token counts including cache tokens,
  latency, status, `stop_reason` (usable for the truncation rate, FR-11) and a resource
  type/id pair. It has no explicit "exam" tag today. Adding one, the dedicated provider
  key (FR-34), how cost pages exclude exam traffic, and where exam call records and audit
  entries are written while exam contracts live in a throwaway database (A-3) are the
  planner's calls, as is how "the owner" is identified for FR-31.
- **Git hygiene (FR-36).** The `.gitignore` in this tree has a comment ("`make phase1`
  output ... must never be committed", line 53) with no ignore rule under it, and nothing
  for an eval contracts folder or label spreadsheets. The plan must add rules before any
  real contract is placed on disk. Per `CLAUDE.md`, offsets point into the frozen text
  snapshot, so labels for scans must be tied to one pinned OCR output (FR-20).
- **Quote check.** The app's own citation checker accepts a similarity score of 85 or
  higher (`align_citation_to_source`). FR-12 asks for word-for-word; the planner may
  report both numbers.
- **Measured facts motivating this** (from the requester): 12 of the 14 real contracts
  exceed 25,000 characters; 11 of 14 are image scans; 2 scans have stamp-only text layers
  ("DocuSign Envelope ID...", "DPDP DPDP...") and skip OCR today; review run-to-run noise
  is about plus or minus 0.04 F1; a measured routing comparison (Haiku 0.64 vs Sonnet 0.90)
  is the kind of decision the exam exists to make.
- **Dependency order.** Phase 1 does not depend on this feature; Phase 1b does. Redesign
  page: https://claude.ai/artifact/BewPmacmW8atK11LLy5TfK. The app is single-tenant today
  (`org_id` is scaffolding), so "organisation-scoped" here mainly means exam rows must be
  filed under the one org and invisible to real users.
