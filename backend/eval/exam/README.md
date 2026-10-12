# The AI exam: operator guide

The exam measures how well AEGIS's AI does **today**, on a fixed set of questions and contracts,
so that a change (a new prompt, a new model, a code change) can later be judged against it: did it
really get better, or is the difference just noise?

It is a tool for **one person, the owner**. It does nothing on its own: no page, no schedule, and
CI never runs it. You start every step yourself, and every step that costs money shows you the
price first and waits for your go-ahead.

Design and decisions: `specs/005-ai-eval-baseline/` (spec, plan). Reports it writes: `reports/`
(see `reports/README.md`).

## What keeps it safe

- **A separate database.** Every run builds a throwaway database (`legal_clm_eval`) from nothing,
  puts the exam's contracts in it, and throws it away at the next run. Your real contracts,
  searches, notifications and workflows are never touched, and the exam's contracts never show up
  in the app. The only things the exam writes to the real database are the cost record of each AI
  call (so the AI Usage page can show "Exam traffic" apart from "Real usage") and audit entries.
- **A separate key.** The exam uses its own Anthropic key. If it is missing, or is the same key the
  app uses, the exam refuses to run.
- **A hard spending limit.** You type a ceiling for each run. The exam never lets the money spent
  pass it (see "Money" below).
- **Only you.** Every step that spends, sends one of your contracts anywhere, or changes the
  answers runs as the owner named in `exam.env` (no password: whoever can run the command on
  this machine is trusted, and the typed spending ceiling is the go-ahead for money).
- **Your contracts stay private.** Your 14 contracts, their text, the OCR results, the spreadsheets
  and your answers live under `backend/eval/private/`, which git and Docker both ignore. Your
  contracts go only to the two providers the app already uses: Anthropic (the AI) and Reducto
  (reading scans). The reports in `reports/` hold numbers only.
- **Everything is on the record.** Each approval, each time one of your contracts is sent to a
  provider, each spreadsheet you load and each baseline you set is written to the audit log.

## One-time setup

1. **Make an Anthropic key just for the exam.** In the Anthropic console, create a new key (give it
   a clear name and, if you can, a monthly spend limit). Do not reuse the key in `backend/.env`.
2. **Create `backend/eval/private/exam.env`** (make the `private` folder if it is not there). Three
   lines:

   ```
   EVAL_CLAUDE_API_KEY=<the new key>
   EVAL_OWNER_EMAIL=<the email you log in to AEGIS with>
   REDUCTO_USD_PER_CREDIT=<optional: what one Reducto credit costs, in dollars>
   ```

   Never put these in `backend/.env`. The file is ignored by git and kept out of Docker images.
   `REDUCTO_USD_PER_CREDIT` is optional: without it the OCR step still works, it just shows pages
   and credits instead of dollars and asks for its ceiling in credits.
3. **Check `backend/.env`** (the stack's normal settings): `MOCK_CLAUDE=false`,
   `MOCK_REDUCTO=false`, `REDUCTO_API_KEY` set (for scans), and the embedding and reranking
   providers left on `local`. The exam refuses to run on a fake model, or if contract text could
   go to a third party.
4. **Start the stack** (`make dev-build`) and run every command from the repository folder like
   this, in a terminal (not from a script):

   ```
   docker compose exec backend python -m eval.exam <command> ...
   ```

   Gated commands run as the owner in `exam.env` (`--owner EMAIL` is optional). The paid ones show
   an estimate and ask for a spending ceiling; pressing Enter cancels. `python -m eval.exam --help` lists every
   command; `python -m eval.exam <command> --help` explains one. It only runs on a local or
   development stack, never a deployed one.

`status` (free) shows where you are at any time: the exam's content, your files and
answers, the runs so far, any run waiting on a batch, and the current baseline.

## The steps, in order

Money steps are marked. All dollar figures are the plan's estimates; every paid step prints its own
exact estimate first.

| # | Command | Cost | What it does, and what you do |
|---|---|---|---|
| 1 | `build` | free | Builds the exam's public questions and the constructed contracts (it downloads the public datasets the first time: about 400 MB, kept out of git) and prints one report of what it built. A dataset that cannot be downloaded is named as missing, never replaced. |
| 2 | `owner-import --from DIR --owner EMAIL` | free | Copies your 14 contract files into the private folder. See the note below. |
| 3 | `ocr --owner EMAIL` | **Reducto credits**, once | Reads your scanned contracts (`owner-import` tells you how many; expect 11 of the 14), and any generated scan the app would OCR, with the OCR provider, one time, and keeps the result so it is never paid for again. |
| 4 | `prefill --owner EMAIL` | about **$0.65**, once | A different, newer AI model reads each of your 14 contracts and proposes answers to six questions (parties, start date, end date or length, governing law, notice, renewal), each with the sentence it relied on. It writes a spreadsheet. |
| 5 | *(you)* fill in the facts spreadsheet | free | Open `backend/eval/private/sheets/facts-<id>.xlsx`. For every row choose `correct` or `wrong`; if wrong, type the right answer in `correct_value_if_wrong`. A blank mark means "can't tell": that row is left out and counted. A row that says "no supporting sentence found" can only be marked wrong. Change nothing in the other columns. |
| 6 | `load-facts facts-<id>.xlsx --owner EMAIL` | free | Loads your answers as the answer key. All or nothing: if anything is off the whole file is refused and every problem is listed. The AI never confirms its own suggestion; only your marks count. |
| 7 | `draft-routing --owner EMAIL` | about **$0.30**, once | Takes a snapshot of today's workflows and has the AI write about 50 realistic requests (email, chat, notice), at least one per workflow. It writes a second spreadsheet. |
| 8 | *(you)* fill in the routing spreadsheet | free | For each request, mark `correct` if the suggested workflow is right, or `wrong` and pick the right one (or "no workflow: a person should route it"). |
| 9 | `load-routing routing-<id>.xlsx --owner EMAIL` | free | Loads your picks as the routing key. |
| 10 | `run --mode sample --owner EMAIL` | about **$1** | A cheap rehearsal on a small fixed subset. It proves everything works and shows the real estimate. Its detail file is labelled "SAMPLE — not comparable to the baseline", and a sample run can never be part of a baseline. |
| 11 | `run --mode full --owner EMAIL` **twice** | about **$10** each (about $18 at list price) | The real exam. Run it twice on the same code: how far the two results differ from each other is the "noise band" every later change is judged against. |
| 12 | `baseline set NAME RUN_A RUN_B --owner EMAIL` | free | Names the two full runs as the baseline. Writes `reports/baseline-NAME.md` and `.json`: commit those. |

The step-5 and step-8 spreadsheets are only ever read back from `backend/eval/private/sheets/`.

**Step 2, your contract files.** They stay on your machine; the exam opens exactly the 14 file names
it was built for, one by one, and never lists or reads anything else in the folder. With the
files in your Downloads folder, mounted read-only:

```
docker compose run --rm -v "$HOME/Downloads:/owner_downloads:ro" backend \
  python -m eval.exam owner-import --from /owner_downloads --owner you@yourcompany.com
```

If one of the 14 is missing, nothing is imported.

### After a change

1. Make the change (a prompt, a model, some code).
2. Run the full exam twice, saying what you changed and which area it is meant to improve. The area
   is one of `review`, `extraction`, `qa`, `portfolio`, `routing`:

   ```
   ... run --mode full --change shorter-review-prompt --target review --owner EMAIL
   ```

   The area is written down **before** anything is asked of the AI. That is what stops a verdict
   from being chosen after seeing which score moved.
3. `compare RUN_A RUN_B` (free, and no target: it is read from the two runs). It
   writes `reports/compare-<change>-<date>.md`: PASS only if the target area improved by more
   than the noise band and nothing else got worse by more than its own band. Cost and time are
   shown beside the scores and never decide.

A baseline and a change must use the same exam content and the same delivery mode (see below);
if either differs the comparison is refused. Changing the exam's questions or answer keys on
purpose means a new baseline.

## Money

- **You always see the price first.** Before any paid step the exam prints how many AI calls it
  will make, the expected cost (at the price you will actually pay, and at list price), and the
  **worst case** (every answer as long as it is allowed to be). Then it asks for your
  **ceiling**: typing it is the go-ahead, and pressing Enter cancels with nothing spent.
- **The ceiling is a hard limit.** The exam only starts a call if, even if its answer came back as
  long as allowed, the total would still be under your ceiling. So spending can never pass it, but
  the run may stop early and be marked `incomplete` if you set it too low. An incomplete run is
  reported but cannot be part of a baseline.
- **What to type.** About **2.5 times the expected cost** is the safe choice: it is the worst-case
  figure the estimate prints, and it guarantees the run completes. For a full run that is about
  $25. You are charged only for what is actually used, not the ceiling.
- **Long contracts drive the cost.** The AI reads the contract text, so a few long contracts cost
  more than many short ones. The 40 long public contracts and your 14 contracts (each sent whole to
  the pre-fill step, and to the exam at full price: real contracts are never batched) are where the
  money goes. Cost is reported per kind of call so you can see which.
- **Batch delivery (the default) is half price, but slower.** The exam's public and constructed
  questions are sent as a *batch*: the provider answers at half price, usually within an hour and
  at most within 24 hours, in two stages. Your own contracts are always sent normally. `--delivery
  sync` sends everything normally at full price. A baseline and its changes must use the same
  delivery mode.
- **The portfolio questions ("which contracts ...") are always sent normally, at full price.** A
  batch can answer one call per question, and the app answers these in several: one call turns
  the question into a filter, which the app then runs over every contract; for wording no
  contract field holds ("renews automatically", "needs 90 days' notice") it also has the AI read
  each contract, one call each. In a full run that is 2 questions x 40 contracts, about $2 in
  all; the estimate lists them as `portfolio_filter` and `tabular_cell_extraction`. A question
  about a field (governing law, end date, value, other party, type) reads nothing.

## Waiting, stopping, resuming

A full run in batch mode usually takes around two hours. You can leave it running. If the terminal
closes, the laptop sleeps or you press Ctrl-C while it is waiting for a batch, nothing is lost:

- `status` shows the run as *waiting on a batch*.
- `run --resume RUN_ID --owner EMAIL` carries on (it checks the batch, collects the answers and
  finishes). It continues under the ceiling you already approved; it never starts a new run.
- `run --abandon RUN_ID --owner EMAIL` gives up: it cancels the batch and closes the run as
  `incomplete`.

You cannot start a new run while another is waiting. A run that simply died is closed as
`incomplete` automatically the next time a run starts. Run ids can be typed in full or as the first
8 or more characters.

Tip: start full runs early in the (UTC) day. A few of the exam's questions depend on today's date,
and a run that crosses midnight can fail one or two of them.

## Reading the results

- **The summary** is printed when a run finishes: cost against the estimate, per kind of call, and
  how many items were scored, failed or not attempted.
- **`backend/eval/private/runs/<run id>/detail.md`** (private) lists every measure, every failed
  call, every quote that was not found word for word, every missed or falsely raised finding. It
  quotes contract text: do not copy it anywhere.
- **`reports/`** (committed) holds the baseline and comparison reports: numbers, counts, and
  plain-language lines, no contract text.
- **Flags to notice**: `contains errors` (at least one AI call failed; a baseline allows at most 1%
  of calls failing), `incomplete` (the run did not attempt everything), `SAMPLE`.
- Every score shows how many items it rests on (`n`); a small `n` is a hint, not a conclusion.

## When something is refused

| Message | What to do |
|---|---|
| "paid commands need their own Anthropic key" | Put `EVAL_CLAUDE_API_KEY` in `backend/eval/private/exam.env`. |
| "same as the app's Claude key" | Make a separate key for the exam. |
| "MOCK_CLAUDE is on" / "contract text may go only to Anthropic and Reducto" | Fix `backend/.env` as in step 3 of the setup, and restart the backend. |
| "only the exam owner can run this command" | Leave `--owner` out, or give the email in `exam.env`. |
| "needs a terminal" | Run it with `docker compose exec backend ...` in a terminal (no `-T`, no piping). |
| "these scans have not been read by OCR yet" | Run `ocr` first. |
| "waiting on a batch" | `run --resume` or `run --abandon` that run first. |
| "the sheet was rejected and nothing was loaded" | The list shows every problem (a changed locked column, a missing row, `correct` on an unsupported row, a correction typed against `correct`...). Fix the sheet, or regenerate it. |
| An answer shows "out of date" in `status` | A contract file or its text changed after you confirmed it. `sheet facts --reconfirm --owner EMAIL` writes a sheet with just those answers to confirm again. Until then they are left out of runs. |
| Routing "OUT OF DATE" | The workflow list changed since you picked. Routing is left out of runs until you redo it (delete the three files the `draft-routing` message names, then draft and load again; that is a new baseline). |
| "the exam content has changed since these runs were made" | The questions or answer keys changed after those runs. Make a new baseline. |

## What is committed and what is private

| Where | What | In git? |
|---|---|---|
| `backend/eval/exam/*.py`, `content/`, `reports/`, `README.md` | The tool, the public and constructed questions and answer keys, the numbers-only reports | yes |
| `backend/eval/results/superseded/` | Earlier results from the old harness, kept as history and labelled "earlier, superseded" | yes |
| `backend/eval/private/` | `exam.env`, your 14 contracts, their text, OCR results, spreadsheets, your answers, per-run results and details | **no** |
| `backend/eval/contracts/` | The downloaded public datasets and the generated contract files | **no** |
| `backend/eval/.work/` | The exam database's stored files | **no** |

After a session `git status` should show only the reports you meant to add. The exam refuses to
write a public report that contains one of your file names or eight words in a row from one of your
contracts.

## Licences and credits

The public questions come from CUAD v1 (The Atticus Project, CC BY 4.0), ContractNLI v1.0 (Koreeda
and Manning, CC BY 4.0), LegalBench-RAG (ZeroEntropy, MIT) and the Common Paper templates
(CC BY 4.0). Every item in the exam records its dataset, version and licence.

## Command reference

| Command | Who | Spends |
|---|---|---|
| `build [--only public\|constructed]` | anyone | nothing |
| `status` | anyone | nothing |
| `owner-import --from DIR` | owner | nothing |
| `ocr` | owner | Reducto credits |
| `prefill` | owner | Anthropic (about $0.65) |
| `load-facts PATH` | owner | nothing |
| `sheet facts [--reconfirm]` | owner | nothing |
| `draft-routing` | owner | Anthropic (about $0.30) |
| `load-routing PATH` | owner | nothing |
| `run --mode sample\|full [--delivery batch\|sync] [--change NAME --target AREA]` | owner | Anthropic (go-ahead and ceiling every time) |
| `run --resume RUN_ID` / `run --abandon RUN_ID` | owner | nothing new (resume stays under the approved ceiling) |
| `baseline set NAME RUN_A RUN_B [--replace]` | owner | nothing |
| `compare RUN_A RUN_B [--baseline NAME]` | anyone | nothing |

`baseline set --replace` only changes which baseline is *current*; a report that is already
published is never overwritten (a new baseline needs a new name, and the earlier report stays).
