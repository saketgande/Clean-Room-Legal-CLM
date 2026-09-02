# Aegis CLM — Redesign Proposal

**Status:** proposal for discussion. Nothing here is built.
**Context:** follows the integration audit (`INTEGRATION_AUDIT.md`) and the access-control
rebuild (areas 1–3, already implemented on branch `claude/tencentdb-agent-memory-core-de022d`).

---

## 1. What is actually wrong

The audit found overlapping and disconnected subsystems. Working through them surfaced a
deeper problem that no individual fix addresses:

**The application models legal work as a pipeline. It is a negotiation.**

Three consequences, all confirmed in the code:

1. **The workflow engine cannot go backwards.** `current_index` is incremented in nine
   places and decremented in none. A reviewer who says "change clause 7" cannot send the
   work back — the run is marked `error = "Approval rejected"` and stops.
2. **A step is binary.** It is done or not done. It carries no statement of what is being
   asked, and no outcome other than "finished". "Legal Sign-off" is a label, not a decision.
3. **Nothing starts on its own.** A filed request waits in a queue until a human notices it
   and presses start. There is a workflow engine; there is no workflow *running*.

The predictable real-world failure is not that people complain. It is that they **route
around the tool** — the negotiation finishes over email, and the audit trail has a hole
exactly where the decisions were made. For a regulated manufacturer that is the worst
possible place for a gap.

---

## 2. Design principles

These are the rules the redesign should be judged against.

| # | Principle | Why |
|---|---|---|
| 1 | **One front door.** Email, form, chat and API converge on one Request with one number. | Channel is metadata, not a separate pipeline. |
| 2 | **AI proposes, a human disposes.** Every AI output carries reasoning + confidence and is confirmable. | Keeps AI advisory and auditable. A suggestion you cannot interrogate is a liability. |
| 3 | **Deterministic where it must be reproducible.** Compliance gates, sanctions screening and access decisions are rules, never models. | Same input must give the same answer to an inspector. |
| 4 | **Workflows are graphs with typed outcomes.** | Rework, escalation and rounds are normal, not exceptions. |
| 5 | **Deviation is allowed and recorded.** Skip a step, add a reviewer — with a reason. | Flexibility is what keeps the record complete. Rigidity is what empties it. |
| 6 | **Nothing is invisible.** Each step shows its output, confidence, who approved, and when. | The data already exists; no screen shows it together. |
| 7 | **Never block the front door.** A request is filed immediately, even if incomplete. | Otherwise the SLA clock lies and work goes missing. |

---

## 3. The architecture

### Layer 1 — Capture (one front door)

```
email ─┐
form  ─┼─►  normalise ─► extract ─► completeness ─► name + number ─► gates + screening
chat  ─┘                  (AI)       (AI, capped)     (rules)          (RULES)
api   ─┘
```

* **Extract** — pull structured fields out of unstructured text *and attachments*. This is
  the right use of an LLM: open-ended input, structured output.
* **Completeness check** *(new)* — identifies missing required fields and asks the sender,
  by the channel they used. **Capped at two chases, and never blocks filing.** The request
  exists from the first second with an honest clock; gaps become open items on it.
* **Gates and screening** — deterministic. See §5.

### Layer 2 — Decide (AI proposes, human disposes)

The workflow recommender already returns `flow_id`, `confidence`, plain-English
`reasoning`, `alternatives[]` each with a `why`, and a `needs_human` flag. It is not wired
to a human decision point.

*(new)* A notification asks a named person to **confirm or change** the workflow. Nothing
runs until they do. Low confidence is surfaced rather than hidden.

### Layer 3 — Execute (a graph, not a list)

**The single highest-leverage change in this document.**

Today a step completes. Instead, a step is a **decision with typed outcomes**, and each
outcome names where it goes:

| Outcome | Routes to |
|---|---|
| Approve | next step |
| Approve with comments | next step, comments attached |
| **Request changes** | **a named earlier step**, carrying the comments |
| Need information | pause, ask the requester, resume |
| Escalate | insert a senior rung, then continue |
| Reject | terminate, with a reason |

This one field gives you send-backs, rework rounds and escalation without a BPMN engine.

Also required:

* **Parallel groups** — Finance and Quality review simultaneously, not in a queue.
* **Deviation with a reason** — skip a step or add an ad-hoc reviewer; recorded, so it is
  an auditable exception rather than a bypass.
* **A step definition states what is being asked** — inputs, the question, allowed
  outcomes, SLA. Not just a name.

### Layer 4 — Negotiate (the round trip)

The largest missing capability. `COUNTERPARTY_REVISION` already exists as a version
source and a `counterparty` step type exists; the inbound path does not.

```
send link ─► they view ─► THEY UPLOAD their markup ─► version N+1 ─► diff
                                                                      │
        sign ◄── both agree ◄── re-approve only if material ◄── AI reviews ONLY the changes
```

Two rules that matter:

* **Review only what changed.** Re-running the whole workflow each round asks approvers to
  re-approve untouched terms; they will start rubber-stamping and the control dies.
* **Re-approve only if the change is material.** Define materiality explicitly (value,
  liability, term, indemnity, IP). Everything else is a comment round.

### Layer 5 — Live (after signature)

* Obligations extracted (exists).
* **Reminders go to the obligation's owner, not the signer** *(change)*. The GC signs
  everything and would receive every reminder in the company; the signer is the escalation
  path, not the first contact.
* Renewal windows watched (exists).
* An active contract can **raise new requests against itself** (built in area 2) — this is
  what turns a pipeline into a lifecycle.

### Cross-cutting

* **One access decision** — `app/core/policy.py`, already built. Departments join by
  lifecycle stage.
* **One audit chain** — every extraction, gate hit, outcome, deviation and reason.

---

## 4. What already exists

Roughly 70% of this proposal is connection work, not new construction.

| Capability | Status |
|---|---|
| Email / form / chat intake | ✅ all three |
| Reference number, derived subject | ✅ |
| Attachment text extraction | ✅ |
| Workflow suggestion **with reasoning, confidence, alternatives** | ✅ `flow_agent.py` |
| Deterministic gates (keyword detector) | ✅ present, but wired as the *fallback* |
| Sanctions + conflict screening | ✅ deterministic, correctly reports "unavailable" not "clear" |
| Version compare / diff | ✅ |
| E-signature, obligations, reminders | ✅ |
| Background job runner with retries + dead-letter | ✅ |
| Counterparty **view** link | ✅ view-only |
| **Typed step outcomes / send-back** | ❌ |
| **Parallel step groups** | ❌ |
| **Deviation with reason** | ❌ |
| **Completeness checker** | ❌ |
| **"Choose your workflow" decision point** | ❌ |
| **Per-step evidence view** | ❌ (data exists, no screen) |
| **Counterparty upload** | ❌ |

---

## 5. The AI position

A deliberate stance, in response to the concern that AI is overused and inconsistent.

**Rules — never a model.** Compliance gates, sanctions screening, routing, access,
risk scoring, SLA. These must be reproducible and defensible.

> **Recommended change:** the Tier-0 gates currently make one LLM call **in the filing
> path**, with the deterministic keyword detector as the fallback. Invert it — rules
> decide, the LLM only *suggests* additional gates for human confirmation. This is the
> single best cost, latency and consistency win available, and both halves already exist.

**Classical ML — the current gap.** Request categorisation once regex plateaus, email
type classification, duplicate detection, priority prediction. Local embeddings are
already running. Cheap, fast, and measurable with precision/recall.

**LLM — where it genuinely earns the cost.** Extracting fields from unstructured email
and unseen counterparty paper; explaining playbook deviations; drafting; open-ended Q&A.
No rule set drafts a contract.

**Measure before replacing anything else.** Every classification already records a
`source` (`regex` / `keyword` / `ai`) and a confidence. Log where the model and the rules
*disagree*, and the question stops being a matter of opinion.

---

## 6. Suggested sequence

Ordered by leverage per unit of risk.

| # | Change | Size | Why first |
|---|---|---|---|
| 1 | Invert the Tier-0 gates to rules-primary | S | Instant filing, reproducible compliance, both halves exist |
| 2 | Typed step outcomes + send-back | M | Unlocks rework, escalation and rounds in one change |
| 3 | "Choose your workflow" decision point | S | Ends the silent queue; recommender already returns everything needed |
| 4 | Per-step evidence view | S | Pure UI over data already recorded |
| 5 | Parallel step groups | M | Removes the artificial queue between Finance and Quality |
| 6 | Deviation with a reason | S | The flexibility that keeps people inside the tool |
| 7 | Completeness checker | M | Needs conversation state; cap and give-up path are essential |
| 8 | Counterparty upload | L | **External-facing.** Virus scanning, file-type limits, rate limiting, and a leaked link must not attach anything to any contract. Build last, budget most. |
| 9 | Obligation reminders to owner | S | Small, prevents the GC drowning |

**Do 1–4 first.** They are small, high-leverage, and each is independently shippable.

---

## 7. Open decisions

These are product calls, not engineering ones:

1. **Materiality** — what change to a contract forces re-approval rather than a comment
   round? Needs a written definition before layer 4 is built.
2. **Completeness chasing** — two attempts then file as incomplete, or a different policy?
3. **Deviation authority** — who may skip a mandatory step, and does it need a second
   approver?
4. **Counterparty identity** — link plus passcode, or named accounts for frequent
   counterparties?
5. **RBAC** — this proposal assumes object access (built) with roles deferred. Confirm
   that still holds.

---

## 8. What this is not

* Not a rewrite. The data model, job runner, approvals engine, audit chain and access
  policy stay.
* Not more AI. It removes a model call from the critical path and adds one human
  decision point.
* Not a workflow product. Six typed outcomes and parallel groups, not BPMN.
