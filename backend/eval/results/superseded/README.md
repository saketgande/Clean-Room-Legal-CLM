# Earlier, superseded — not a baseline

These seven files are the results of the first evaluation attempt, run on the
`ai-redesign/step-0` branch on 2026-09-28 and 2026-09-30. They are kept as
**history only**. The baseline is the exam in `backend/eval/exam/` (spec
`specs/005-ai-eval-baseline`, FR-29): nothing here is compared against, and the
report's `compare` step refuses any run whose exam-content fingerprint differs,
which these files do not have.

The files are byte-for-byte copies of the branch's `backend/eval/results/*.json`.
Do not edit, re-score or "fix" them; the point is that they show what was
measured then, defects included.

| File | Date | Model | Scope | Headline numbers in the file |
|---|---|---|---|---|
| `baseline-sample-2026-09-28.json` | 2026-09-28 | claude-sonnet-4-6 | sample, all four tasks | review F1 0.83 · extraction 46% (100 fields) · Q&A evidence 65% (40 questions; 13% of the 15 past 25k) · routing 0.90 (10 requests) |
| `baseline-routing-2026-09-28.json` | 2026-09-28 | claude-sonnet-4-6 | routing only, 50 requests | workflow accuracy 0.90 · escalation 0.68 |
| `baseline-routing-claude-haiku-4-5-2026-09-28.json` | 2026-09-28 | claude-haiku-4-5 | routing only, 50 requests | workflow accuracy 0.64 · escalation 0.60 |
| `baseline-sample-extraction-review-2026-09-30.json` | 2026-09-30 | claude-sonnet-4-6 | sample extraction + review | review F1 0.72 · extraction 49% |
| `baseline-sample-qa-2026-09-30.json` | 2026-09-30 | claude-sonnet-4-6 | sample Q&A | evidence 50% (40 questions; 20% of the 15 past 25k) |
| `baseline-sample-review-r2-2026-09-30.json` | 2026-09-30 | claude-sonnet-4-6 | sample review, second run | review F1 0.73 |
| `baseline-sample-review-whole-2026-09-30.json` | 2026-09-30 | claude-sonnet-4-6 | sample review, whole-document A/B | review F1 0.79 |

Review, extraction and Q&A used public data (ContractNLI, Common Paper
templates with planted changes, CUAD, LegalBench-RAG). Routing used 50 requests
written by an AI.

## The three known defects

1. **Empty search context in question answering.** The branch's `baseline.py`
   sent `"retrieved_context": ""` to the answering skill, so a Q&A score tested
   the model reading the contract text, not the app's search. The Q&A numbers
   above say nothing about whether Find / Ask retrieves the right passage.
2. **Parties scored as one string against un-normalised keys.** Extraction took
   the single `counterparty_name` string and matched it by containment against
   the label; several parties were one blob, and values were only lower-cased
   and whitespace-trimmed, so company-name forms ("Inc.", "Ltd", "Pvt Ltd") and
   amounts were compared as written. The 46-49% extraction figure mixes real
   misses with formatting noise.
3. **Routing on 13 old workflows with AI-written labels.** The 50 routing
   requests, and the workflow each "should" go to, were drafted by an AI and
   never confirmed by a person, against a workflow list that no longer matches
   the platform. A 0.90 here is agreement with an AI's guess about workflows
   that have since changed.

## How the exam removes each

1. Q&A calls the app's own Ask route, so the context is whatever the app's
   search (`hybrid_sources`) returned, never replaced; the report counts empty
   contexts (FR-24).
2. Parties are a list of organisation names compared as sets of normalised
   keys, each party scored on its own, and dates, amounts and jurisdictions are
   normalised before comparing (FR-23).
3. Routing keys are the owner's own picks against a snapshot of today's enabled
   workflows (32 at planning time), and the report says when the snapshot is
   out of date (FR-21, FR-25).
