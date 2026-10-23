# Exam reports

Committed reports of the AI exam (spec `specs/005-ai-eval-baseline`). This folder holds
**scores, counts, ids and rule / field / workflow names only** — never contract text, never a
quote, never an answer key, never an owner file name (the owner's 14 contracts appear only as
`owner-01` … `owner-14`). That rule is FR-36: everything that quotes a real contract stays in the
git-ignored `backend/eval/private/`.

## What is written here

| File | Written by | What it is |
|---|---|---|
| `baseline-<NAME>.md` / `.json` | `python -m eval.exam baseline set <NAME> <RUN_A> <RUN_B>` | The named pair of full runs that is the baseline: each run's score, the average, and the noise band per measure. |
| `compare-<change>-<YYYYMMDD>.md` / `.json` | `python -m eval.exam compare <RUN_A> <RUN_B>` | A change's two runs set against the baseline, with the verdict. |

The `.md` is for reading, the `.json` (`"schema": "aegis-exam-report/1"`) is the same numbers for
tools. Reports are records and are not overwritten: replacing a baseline means a new name, and
the earlier baseline stays here as history. Do not edit a report by hand.

The report is built by `eval/exam/report.py` from `eval/exam/score.py`'s output. A report is
refused, and nothing is written, if it would contain a private field, an owner file name, or
eight consecutive words of an owner contract's frozen text.

## Reading a report

- **Header** — the delivery mode (`batch` or `sync`; a baseline and a change must use the same
  one), the exam-content fingerprint, the scorer version.
- **Banner** — flags a reader must not miss:
  - `SAMPLE — not comparable to the baseline`: a sample run (a small fixed subset) is only a
    rehearsal. It can never be part of a baseline or a comparison.
  - `contains errors`: at least one AI call failed. A pair is accepted as a baseline only if
    each run had **1% or fewer** failed calls; at or under 1% the flag stays on the report.
  - `incomplete`: the run did not attempt every item (ceiling reached, crashed, abandoned). An
    incomplete run cannot be part of a baseline.
- **Every score shows its `n`** — how many items it rests on. A small `n` (a past-25k bucket,
  a single field) is indicative, not conclusive.
- **Noise band** — how far the two runs of the same code differ from each other. A later change
  smaller than the band is not a real difference.
- **Three kinds of measure**
  - *Decide pass or fail* (headline quality): review F1, field extraction accuracy, question
    answering evidence found, portfolio completeness, routing accuracy, quote validity, and
    truncation rate (lower is better).
  - *Also reported, never decide*: precision and recall, the before / past 25,000-character
    splits, the stamp-only scans "as read today", scanned copies beside their text originals,
    document coverage and run-to-run consistency.
  - *Cost and time*: shown beside the scores (baseline, change, difference) and weighed by the
    owner; they never decide.
- **Missed high and critical findings** — named by item and rule, with the run(s) that missed
  them.
- **Failed calls and items not scored** — every failed AI call and every item left unscored is
  listed by id (FR-40); none is silently dropped or scored as an answer.
- **Verdict** (comparison reports) — `PASS` only if the target area's main measure improved by
  more than its noise band and no other deciding measure worsened by more than its own band;
  every failed measure is named. `NO VERDICT` when the target area was not recorded before the
  runs, or its main measure was not measured. The verdict is a report; it blocks nothing.

## Earlier results

Every report ends with the earlier branch results under *earlier, superseded*. They live in
`backend/eval/results/superseded/`, are kept as history only, and are not a baseline.

## Where the private detail is

Per-run detail (every measure, failed calls, invalid quotes, missed findings with their text,
per-call coverage) is written to `backend/eval/private/runs/<run_id>/detail.md` — git-ignored,
never copied into this folder.
