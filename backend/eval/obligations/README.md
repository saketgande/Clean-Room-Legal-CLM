# Obligation extraction baseline

A way to turn "extraction feels weak" into numbers, and to prove a redesign is better than today's pipeline
on the **same** answer key. Everything here is free (no database except `--stored`, no network, no model) except the paid rehearsal below.

```bash
docker compose exec backend python eval/run_eval.py --obligations --check    # keys valid, no holes, oracle 100%; committed baselines re-score
docker compose exec backend python eval/run_eval.py --obligations --stored   # score what dev holds today
docker compose exec backend python eval/run_eval.py --obligations --review-sheet [KEY]   # the sheet a lawyer ticks and signs
docker compose exec backend python eval/run_eval.py --obligations --confirm KEY          # record the lawyer's confirmation (a terminal)
docker compose exec backend python -m pytest tests/test_obligation_eval.py tests/test_obligation_baseline.py -q
```

## The paid rehearsal

```bash
docker compose exec backend python -m eval.exam obligations --label NAME [--runs 2] [--ceiling 1]   # PAID; needs a terminal
```

One call per key per run, with the production prompt (the shipped skill prompt, the job's own prompt builder, the same
tool schema and model) and the key's frozen text, then each answer is scored against its key. It is an exam command, so
it has the exam's guards: its own Anthropic key (`eval/private/exam.env`), a typed spending ceiling that is a hard
limit, the owner, a throwaway database. It never saves anything as obligations and never reuses a stored answer.
Each call is on the AI ledger as exam traffic, with no contract text in its input.

The estimate it prints is the price to read before typing a ceiling. Type the "ceiling of about" figure it suggests:
you pay only for what is used. Results go to `eval/obligations/results/scratch/<run id>/` (`results.json` with every
row and score, `scorecard.txt`); git ignores that folder. **Every scorecard is stamped PROVISIONAL REHEARSAL until all
its keys are lawyer-confirmed.** A call that fails is a result with its outcome (cut off, refused, unusable, busy,
rejected: `app/ai/outcomes.py`), never an empty extraction. With `--runs 2` it also says how many of the duties found in
any run were found in every run, which is what a later noise band is built from.

What one run on one small key can tell you: that the pipeline works end to end under eval conditions, the output
headroom, the cost and time, and one data point for recall and precision. It cannot tell you whether a change helped.

## What a key is

One contract: its text frozen in `texts/<id>.txt` (with its sha256) and `gold/<id>.json` listing **atoms**.
An atom is one actor, one action, one trigger. "Pay the fees and give notice" is two atoms.

| kind | meaning | scored |
|---|---|---|
| `duty`, `restriction` | something a party must do / must not do | headline recall and precision |
| `condition` | a step that gates a right ("30 days' notice before terminating") | its own line |
| `commitment` | a promise that is not a task ("grants a licence", "waives") | its own line |

A sentence judged **not** a duty goes in `not_duties` with a reason (`right`, `effect`, `definition`,
`representation`, `allocation`, `formality`), so a row that quotes it counts against precision.

Due dates are never typed. They are counted by production's own `obligation_deadline` from the atom's timing
and the key's `anchors`, so the key cannot drift from the code. Empty means "needs a person".

## How a row is judged (deterministic, no AI judge)

The row's quote is placed in the text (exact, then ignoring spacing, then 90 percent fuzzy). An atom is
**found** when a row's quote covers at least half of it. A row is **true** when it touches a duty or
restriction atom; otherwise it is classed `condition_only`, `commitment_only`, `not_a_duty`, `unlabelled` or
`quote_not_found`. A row that quotes a whole clause is credited for every atom in it and shows up as
**bundling** (atoms per true row above 1). Invented or wrong dates must be 0.

## Status: every key is a DRAFT until a lawyer confirms it

`status.state` is `draft` until a lawyer has read the whole text and confirmed every atom and every
not-duty; then `lawyer_confirmed`. Every scorecard prints the key status. **A baseline is named only when
all keys in it are confirmed**; until then a run is a "rehearsal". The AI never confirms its own suggestion.

### The lawyer step, in order

1. `--review-sheet KEY` writes `review/KEY.html`: the whole text with every labelled passage coloured and tagged, each atom
   and not-duty as a row to tick (Correct / Change / Not a duty), room for what the key missed, and a sign-off. It is one
   self-contained page that prints to PDF. It holds the full contract text, so the folder is git-ignored: hand it over, do
   not commit it. Regenerate it after any edit (its fingerprint says which key it was made from).
2. The lawyer returns their ticks and changes. Someone edits `gold/KEY.json` to match, then `--check` must pass (no uncovered
   duty sentence, oracle 100%).
3. A person runs `--confirm KEY` **at a terminal**: it asks for the lawyer's name, the key id and **the fingerprint printed at
   the foot of the sheet the lawyer signed**, typed by hand. It refuses with no terminal (an agent's shell, CI or a script), with
   a key that fails `--check`, without a name, and when the fingerprint is not the key's own (the sheet was made from another
   version: make a new one). It writes `confirmed_by`, `confirmed_at` and `confirmed_fingerprint` into the key's `status` block
   only, replacing the file in one step.
4. The confirmation covers exactly what was read: the fingerprint is over the text, parties, dates, atoms and not-duties. Edit
   any of them and the key is a draft again by itself (the scorecard, `--check` and the baseline command all say so). A key
   whose state says `lawyer_confirmed` without a name, a date and a matching fingerprint is not confirmed.

What this does not stop: someone who edits the key file by hand (or drives a pseudo-terminal) can write a confirmation. The
terminal requirement keeps a script or an AI shell from doing it by accident or on its own; the file's diff, the name on it and
`manifest.json` in every baseline are the record of who said so.

## The named baseline

```bash
docker compose exec backend python -m eval.exam obligations --baseline NAME --runs 2 [--ceiling 5]   # PAID; needs a terminal
```

Same call as the rehearsal, with the gates of a baseline: refused (before any estimate is shown or anything is sent) unless
**every** key is lawyer-confirmed and unchanged since, `--runs` is exactly 2, NAME is new (`results/baseline-NAME/` is never
overwritten) and the typed ceiling is at most $5 (about $2 is expected). It is **named** only if every key answered in both
passes with the ledger holding every call; otherwise it is saved as a scratch rehearsal and says why it was not named, so a
baseline never has a hole in it. A named baseline is written to `results/baseline-NAME/`: `results.json` (everything),
`run-a.json` and `run-b.json` (each pass), `scorecard.txt` (stamped `BASELINE NAME`, key status per key) and `manifest.json`
(the keys' fingerprints and who confirmed them, the prompt, schema and model, the cost, and the digest of `results.json`).
**Commit that folder with the keys**: it is the number a redesign must beat. `--check` re-verifies it every time: results
untouched, run files equal to the results, each key still the one it was named from and still confirmed, and every answered
call re-scoring to the stored numbers with today's scorer.

## Known limits (say them on every scorecard)

- Four of the five planned texts are our own templates with made-up names; one atom is a large share of a
  small contract, so small moves are noise.
- The lint catches uncovered duty sentences but cannot see a duty hidden in a definition.
- The schema cannot hold "hours" (a 48-hour duty): the key can, so a redesign that adds it shows as a gain.
- The production prompt asks for fields the schema lacks (due_date, trigger_type, partial_document...).
  Recorded here as a Tier 2 input, not fixed.

## Calibration (2026-10-27, `--stored`, key `msa-small`)

The audit hand-counted this contract: one named miss (clause 2.1, taxes on top of the fees) and strict
precision of 5 of 12 rows. The scorer on the 12 rows stored today: **11 of 12** duties found (the one miss is
exactly 2.1) and **7 of 12** rows true. The gap of two rows is two judgement calls the lawyer should settle:
the row on the 30-day notice for terminating an SOW (it also quotes "paying for Services performed through the
termination date", a duty) and the assignment restriction (a negative covenant the audit called boilerplate).
The draft key has 15 atoms against the audit's hand count of about 20; the audit's atom list was not saved.
