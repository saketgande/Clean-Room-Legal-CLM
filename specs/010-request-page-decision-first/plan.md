# Implementation Plan: Request page, "Decision first" layout

Feature ID: 010-request-page-decision-first
Spec: ./spec.md (APPROVED)
Created: 2026-10-23
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Architecture overview

Frontend-only rearrangement of the request page. Nothing changes in the backend, the database, `src/lib/types.ts`,
`src/lib/endpoints.ts`, `src/components/ui.tsx`, `src/components/lifecycle-view.tsx` or `_approval-card.tsx`.

`_request-overview.tsx` (`RequestOverview`) keeps **all** data wiring: every `useQuery`, every `useMutation`, the two
`useEffect`s (review-ended refresh, step pump), the typed-text state (reply, comment, send-back target/reason,
reassign pick, Ask draft and chat log, template notice) and every derived value. It renders a fixed layout of new
**presentational** components from `_`-prefixed files in `frontend/src/app/(app)/intake/`. The children hold only
show/hide UI state (which detail is open, which severity group is open). Typed text stays in the parent, so a
refetch, focus refresh or poll can never drop a draft (spec edge case), and every mutation keeps its exact
`onSuccess`/`onError` (same toasts, same audit/timeline writes server-side: AC-17, AC-25).

```
RequestOverview (_request-overview.tsx — queries, mutations, derivations, RO_CSS base)
├─ MessageBar (template notice, unchanged)
├─ RequestHeader            (_request-header.tsx)  crumb · title · subline · pills
├─ ProgressStrip            (_request-header.tsx)  existing <LifecycleView compact> + one status line
└─ .body  grid: minmax(0,1fr) | 300px  (stacks < 1080px, main first)
   ├─ main column
   │  ├─ DecisionArea       (_decision-area.tsx)   ApprovalCard (any pending approval; buttons or read-only per 009) · "Your decision" · NegotiationPanel (canAct)
   │  ├─ WhatWasAsked       (_request-asked.tsx)    facts · description · RequestAnswers slot · notices · Aegis read
   │  ├─ RiskReview         (_request-risk.tsx)     only when the request has a contract
   │  ├─ CommentsCard       (_request-cards.tsx)
   │  └─ ActivityCard       (_request-cards.tsx)
   └─ side column
      ├─ CounterpartyCard   (_request-cards.tsx)   only when the relationship note adds something
      ├─ PeopleCard         (_request-cards.tsx)
      ├─ DocumentsCard      (_request-cards.tsx)
      ├─ StepsCard          (_request-steps.tsx)   flow state · Now · steps (+ details) · trace · send back
      └─ AskAegisCard       (_request-cards.tsx)
```

Convention template: the page's own existing pattern — `.ro`-scoped CSS strings injected with one `<style>` (as
`RO_CSS` and `_approval-card.tsx` do today), the global scoped-screen palette in `globals.css`
(`--surface/--ink*/--border*/--accent*/--good/--warn/--crit[-soft]`, each with a `.dark` value), lucide icons.

## Path mapping (ownership boundaries for THIS feature — exact files)

| Agent | Files in this feature |
|---|---|
| db-engineer | none |
| backend-dev | none (no backend field is needed — see D4) |
| frontend-dev | `frontend/src/lib/workflow-steps.ts` (add `plainStepName`, `readableResult`), `frontend/src/lib/workflow-steps.test.ts` (extend); new `frontend/src/app/(app)/intake/_request-header.tsx` + `_request-header.test.tsx`; new `_request-risk.tsx` + `_request-risk.test.tsx`; new `_request-steps.tsx` + `_request-steps.test.tsx`; new `_request-asked.tsx` + `_request-asked.test.tsx`; new `_decision-area.tsx` + `_decision-area.test.tsx`; new `_request-cards.tsx` + `_request-cards.test.tsx`; `_negotiation-panel.tsx` (delete one filler line, D5); `_request-overview.tsx` (rewire); `_request-overview.test.tsx` (update + layout tests) |
| qa-engineer | `specs/010-request-page-decision-first/verification.md` |

Shared-hotspot note: no task touches `app/models.py`, `app/jobs/tasks.py`, `src/lib/endpoints.ts`,
`src/lib/types.ts` or `src/components/ui.tsx`. The hotspot of this feature is `_request-overview.tsx` +
`_request-overview.test.tsx`: exactly one task (T008) edits them. Note the working tree still holds spec 009's
uncommitted edits to both files (ApprovalCard placement, chain decisions); T008 builds on the working tree as is.

## Interface freeze (contract-first — fixed before implementation starts)

### API contract

None. No endpoint is added, changed or called differently. Every request the page makes today it makes after
the change, with the same query keys, the same `enabled` conditions and the same polling rules.

Audit actions written by each mutation: unchanged — every mutation calls the same `intakeApi` / `workflowsApi` /
`contractsApi` / `trademarksApi` / `brainApi` / `approvalsApi` function with the same arguments as today, so the
server writes the same audit and timeline entries, once (AC-25).

### Database schema

None. No migration.

### Frontend TypeScript models (added to src/lib/types.ts)

None. Component prop types below live in their own component files (they are not API shapes).

### Endpoint client additions (src/lib/endpoints.ts)

None.

### Frozen helper contracts (`src/lib/workflow-steps.ts`, T001)

```ts
/** A step name a requester understands. Unnamed steps are saved as the title-cased type ("Clm Draft",
 *  backend workflows/service.py `_clean_steps`) or the raw key ("clm_draft", the brief's fallback). */
export function plainStepName(name: string | null | undefined, type?: string | null): string;
// Exact-match table (anything else is returned unchanged, trimmed):
//   "Clm Draft" | "clm_draft"       -> "Prepare draft"
//   "Ai Task" | "ai_task"           -> "AI check"
//   "Human Task" | "human_task"     -> "Review"
//   "Counterparty" | "counterparty" -> "Negotiation"
//   "Notify" | "notify"             -> "Notify people"
//   "approval" -> "Approval", "signature" -> "Signature"
//   empty/null name                 -> table[type] ?? "Step"

/** A step's result as readable label/value pairs, never raw JSON. Replaces `resultRows` in _request-overview.tsx. */
export function readableResult(res: Record<string, unknown> | null | undefined): [string, string][];
// Rows, in order, each only when present and non-empty:
//   "Risk"         risk_band: "unknown" -> RISK_UNKNOWN_LABEL, else capitalised ("High")
//   "Confidence"   confidence -> "30%"
//   "Category"     category, underscores -> spaces
//   "Matter"       matter_type, underscores -> spaces
//   "Severity"     severity
//   "Breach notice" notification_required -> "required" | "not required"
//   "Summary"      summary
//   "Drivers"      first of top_drivers | affected_data_categories | statutory_deadlines that is a non-empty array;
//                  each item: string -> itself; object -> `${Sev} · ${issue|what|label|name}` (Sev capitalised,
//                  only when `severity` is a string) plus ` (${date})` when `date` is a string; an object with
//                  none of issue/what/label/name is DROPPED (never JSON.stringify). First 3 kept, joined "; ".
//                  Row omitted when nothing readable is left.
```

### Frozen component props

All components are top-level functions (never defined inside another component's body), `"use client"`, and
export one CSS string each (scoped `.ro …`), which the parent concatenates into its single `<style>`.

```ts
// _request-header.tsx  (T002) — exports RequestHeader, ProgressStrip, HEADER_CSS
export function RequestHeader(p: {
  title: string;               // D2: today's expression, unchanged; parent computes
  refCode: string;             // r.ref
  requesterName: string | null;
  raisedAt: string | null;     // r.created_at ?? r.submitted_at
  ownerLabel: string | null;   // r.assigned_to_label
  highRisk: boolean;           // !!cid && risk loaded && riskBand(risk) === "high"
  sla: string | null;          // r.sla_status
  onBack: () => void;
}): JSX.Element;
// Renders: crumb  <a>← Legal Intake</a> / {refCode}   (the <a> calls onBack)
//          <h1>{title}</h1>
//          subline "{refCode} · raised by {requesterName ?? "the requester"}{raisedAt ? ` on ${fmtDate(raisedAt)}` : ""} · owner {ownerLabel ?? "unassigned"}"
//          pills (each only when true): "High risk" (.pill.bad) if highRisk; "Overdue" (.pill.bad) if sla==="overdue";
//          "SLA at risk" (.pill.warn) if sla==="at_risk".

export function ProgressStrip(p: {
  steps: WorkflowRunStep[];    // plain-named run steps (D4), [] before a run
  current: LifecycleStage;     // currentStage(...) as today
  comments: WorkflowRunComment[];
  extras: Partial<Record<LifecycleStage, LifecycleRow[]>>;
  line: string;                // status words (rule below)
  tone: "info" | "warn";
  working: boolean;            // shows "Agent working…" with spinner, aria-live="polite"
}): JSX.Element;
// Renders <div className="lcwrap"><LifecycleView compact steps current comments extras/></div>
// then <div className={`stline ${tone}`}>{line}{working ? <span…>Agent working…</span> : null}</div>.
```

Status line rule (computed in the parent, T008):

| State | `line` | `tone` |
|---|---|---|
| `settingUp` | `Workflow being set up` | info |
| no run, no flow | `No workflow yet · ${beforeWorkflow(r).note}` | info |
| no run, suggested flow | `Not started · ${beforeWorkflow(r).note}` | info |
| run complete | `Workflow complete` | info |
| run failed / cancelled | `Workflow ${status}` | warn |
| run open | `${stageLabel} · ${openNames} · step ${wfActiveIdx+1} of ${wf.length}` + (`current` waiting ? ` · ${waitLabel(current, user.id)}` : running ? ` · ${stepMeta(type).running}` : "") + (`current.approval?.overdue` ? ` · overdue` : "") | warn when `current.approval?.overdue`, else info |

`openNames` = plain names of `openSteps(flowRun)` joined `" + "` (parallel steps all show), falling back to the
focus step's plain name. `working` = run open and `current.status` is `running` or `waiting_job`. Request-level SLA
overdue is NOT repeated here (it is the header pill).

```ts
// _decision-area.tsx  (T006) — exports DecisionArea, DECISION_CSS
export interface HumanDecision {
  stepName: string;                                   // plain name of `current`
  warning: { tone: "warn" | "ok"; text: string };     // parent builds from riskFacts, same 4 texts as today
  rework: { reason: string } | null;                  // flowRun.rework
  reply: string; onReply: (v: string) => void;
  replyMissing: boolean;
  busy: boolean;
  onApprove: () => void;                              // parent's approveStep() (window.confirm kept there)
  canSendBack: boolean;                               // curIdx > 0
  onJumpToSendBack: () => void;                       // scrolls/focuses StepsCard's select (ref in parent)
  onJumpToComment: () => void;                        // scrolls/focuses CommentsCard's input (ref in parent)
}
export function DecisionArea(p: {
  approval: ReactNode | null;      // <ApprovalCard …/> only when `pendingApproval` (parent rule below) — for EVERY viewer;
                                   // the card itself shows buttons (can_decide) or its read-only "Waiting on …" form
  human: HumanDecision | null;     // only when atReviewGate (unchanged condition)
  negotiation: ReactNode | null;   // <NegotiationPanel …/> only when canAct and the negotiate condition holds
}): JSX.Element | null;            // null when all three are null (FR-5: takes no space)
// Renders <section className="decision" aria-label="Decision">{approval}{human card}{negotiation}</section>.
// The approval node always comes first (FR-4: first item in the main column whenever an approval is pending).
// Human card = today's markup moved: card > ch > h2 "Your decision" + .cnt {stepName}; .decide.(warn|ok) > .dw;
// rework block (label "Reply to the rejection", "Rejected: “{reason}”", textarea#rework-reply maxLength 2000);
// .db buttons: "Approve & advance →" (disabled busy||replyMissing, title "Reply to the rejection first" when missing),
// "Send back for rework…" (only canSendBack), "Add a comment". Button names unchanged (tests depend on them).

// _request-asked.tsx  (T005) — exports WhatWasAsked, ASKED_CSS
export function WhatWasAsked(p: {
  sourceTag: string;                                  // ai.source==="form" ? "from the form" : "as the AI understood it"
  facts: { label: string; value: string }[];          // Counterparty, Type, Value?, Term? (Priority excluded)
  priority: { value: string; aiSuggests: string | null; canApply: boolean; applying: boolean; onApply: () => void };
  description: string | null;                         // r.description, trimmed; block omitted when empty
  answers: ReactNode;                                 // parent's <RequestAnswers …/> (wrapper .empty:hidden kept)
  missingInfo: string[];
  degraded: { reason: string | null } | null;         // null unless ai_triage.degraded
  read: { summary?: string; mismatches?: string[]; bespoke_asks?: string[]; negotiation_points?: string[] } | null;
  confidence: { value: number; complexity: string | null } | null; // null when source==="form" or absent
}): JSX.Element;
// Card h2 "What was asked" + tag. Order: facts grid (Priority last: "{value}" + when aiSuggests && aiSuggests!==value
// " · AI suggests {x}" + (canApply ? " — apply" link : "")); description (.quote, pre-line, NO attribution line);
// answers; notices (needsinfo list + its hint; degraded with reason or today's fallback sentence); "Aegis read" summary
// + three labelled lists stacked one per row (not a 2-col grid: long items); confidence line.

// _request-risk.tsx  (T003) — exports riskFacts, RiskReview, RISK_CSS, type Sev
export interface RiskFacts {
  band: RiskBand;                 // riskBand(risk)
  unknown: boolean;               // hasContract && band === "unknown"
  critCount: number; gateCount: number;          // critical; critical+high
  findHigh: number; findMedium: number; findLow: number; findingCount: number;
  openDevs: ContractDeviation[];  bySev: Record<Sev, ContractDeviation[]>;
  conceded: RiskDriver[];         decidedCount: number;  devsLoaded: boolean;
}
export function riskFacts(risk: ContractRiskSummary | null | undefined, deviations: ContractDeviation[] | undefined, hasContract: boolean): RiskFacts;
// Exactly today's arithmetic (lines 351-378 of the current file), moved. Called ONCE in the parent; the header pill,
// the decision warning and RiskReview all read this one object (FR-6: cannot disagree).
export function RiskReview(p: { risk: ContractRiskSummary | null | undefined; facts: RiskFacts; canRecompute: boolean; recomputing: boolean; onRecompute: () => void }): JSX.Element;
// h2 "AI risk review"; Recompute button in the header only when canRecompute (= canManage, as today).
// !risk -> "Loading the risk review…"; facts.unknown -> one line "<b>Not reviewed — needs a person.</b> {note}";
// else .risktop (gauge + "{band} risk" + the .rc line with TODAY'S exact wording), "No open issues." when
// band low && findingCount 0, severity groups (button .sevhead toggles; default open: critical+high), each issue
// .risk with clause (underscores -> spaces), issue, "Fix: …" when suggested_fix; conceded drivers listed
// "accepted as-is — still counts".

// _request-steps.tsx  (T004) — exports StepsCard, ApprovalChain, STEPS_CSS, type StepRow
export interface StepRow {
  label: string;                                  // plainStepName(...)
  tone: "done" | "cur" | "warn" | "todo";
  node: string;                                   // "✓" | "–" | "!" | "N"
  badge?: string; badgeTone: "done" | "cur" | "warn" | "skip";
  skipped?: boolean;
  sidx?: number;                                  // run step idx (undefined for previews)
  stage?: LifecycleStage | null;                  // run rows only: drives "Stage N · Label" headings as today
  assign?: string;
  detail: [string, string][];                     // readableResult(step.result); [] when none
  doorway?: { href: string; label: "Open the draft" | "Review & redline" | "Open to negotiate" };
  notice?: ReactNode;                             // <StepReviewNotice …/> on the interrupted step
}
export type FlowState =
  | { kind: "running"; name: string; status: WorkflowRun["status"] }
  | { kind: "suggested"; name: string; reasoning: string; canStart: boolean; starting: boolean; onStart: () => void }
  | { kind: "setting_up" }
  | { kind: "none"; reasoning: string | null; canPick: boolean; flows: { id: string; name: string }[]; picking: boolean; onPick: (flowId: string) => void };
export interface ExtraDetail { stepIdx: number | null; label: string; node: ReactNode } // null stepIdx -> under the list
export function StepsCard(p: {
  rows: StepRow[];
  countText: string | null;          // `${done} of ${applied} steps done${skipped ? `, ${skipped} skipped` : ""}`; null when no rows
  flow: FlowState;
  error: string | null;              // flowRun.error
  now: ReactNode | null;             // <NextStepCard run={plainRun} teams/> when hasRun
  check: { label: string; onClick: () => void; busy: boolean } | null;  // disabled + "Working…" while busy
  extras: ExtraDetail[];             // approval chain, negotiation rounds (read-only viewers)
  trace: WorkflowRunBriefEntry[];
  sendBack: { options: { value: string; label: string }[]; to: string; note: string; onTo: (v: string) => void; onNote: (v: string) => void; onSend: () => void; busy: boolean; selectRef: RefObject<HTMLSelectElement | null> } | null;
}): JSX.Element;
export function ApprovalChain(p: { rungs: IntakeApprovalRung[] }): JSX.Element; // today's .appr rows, unchanged text
```

StepsCard render order (top to bottom): header h2 "Steps" + `.cnt` countText → flow line → `error` (warn) →
Now block (`now` + `check` button, text `check.busy ? "Working…" : check.label`; `busy` is the parent's
existing combined `busy` flag) → `.steps` list → extras with
`stepIdx === null` → hand-off trace toggle → `.sendback`. Flow line texts: running `Workflow: {name}` (+ ` ·
complete` / ` · failed` / ` · cancelled` by status); suggested `Suggested: {name}. {reasoning}` + button **"Start it"**
when canStart; setting_up **"Setting up the workflow…"** + " This request was just filed. The workflow appears here in
a few seconds." (aria-live polite); none **"No workflow is set up for this request."** + `reasoning ?? "Pick one to
start it."` + `<select aria-label="Pick a workflow">` when canPick. A row: `.step.{tone}[.skip]` > `.snode`, `.shd`
(`.sname`, `.sbadge`, doorway link `.ministep.doorway`), notice, `.sassign` "Assigned to **{assign}**", then
toggles: "Review result" when `detail.length`, and each `extras` entry whose `stepIdx === sidx` (label as given).
Toggles are `<button aria-expanded>`; content hidden until opened. Trace toggle text `Agent hand-off trace (N)`;
entries `{plainStepName(b.step)}` bold + `: {b.summary}` (no agent/type names). Rows with no rows: one line
"No steps yet."

```ts
// _request-cards.tsx  (T007) — exports CounterpartyCard, PeopleCard, DocumentsCard, AskAegisCard, CommentsCard, ActivityCard, CARDS_CSS
export function CounterpartyCard(p: { screened: string | null; factsCounterparty: string | null; parties: { name: string }[]; note: string | null }): JSX.Element | null;
// h2 "Counterparty relationship". null unless screened. Shows "About {screened}" only when screened !== factsCounterparty; "All parties" when
// parties.length > 1; "On file {note}" when note. Returns null when none of the three lines would render.
export function PeopleCard(p: { owner: string | null; requester: string | null; department: string | null;
  reassign: { options: { id: string; name: string }[]; value: string; onChange: (v: string) => void; onReassign: () => void; busy: boolean } | null }): JSX.Element;
// h2 "Owner and people". Owner row (avatar, name or "Unassigned", "owner") + when reassign: select
// (first option "Pick a new owner…") and button "Reassign owner" (disabled busy || !value). Requester row.
// Department row only when present.
export function DocumentsCard(p: { docs: IntakeDocument[]; contractHref: string | null;
  contractActions: { docType: string; hasAttachment: boolean; busy: boolean; onIngest: () => void; onDraft: () => void } | null;
  trademark: { busy: boolean; onContinue: () => void } | null }): JSX.Element;
// docs rows (PDF/DOC chip, filename ellipsis, "parsed"/"attached") or one line "No documents attached.";
// contractActions: hasAttachment ? "Use attached as the {docType} →" + "Approve & draft fresh" : "Approve & draft the {docType} →";
// trademark: "Continue as Trademark →"; contractHref: link "Open in CLM editor →".
export function AskAegisCard(p: { scope: "this contract" | "portfolio"; chat: { role: "you" | "ai"; text: string; cites?: number }[];
  pending: boolean; q: string; onQ: (v: string) => void; onSend: () => void; onAsk: (question: string) => void }): JSX.Element;
// h2 "Ask Aegis" + .cnt scope; empty chat -> the three existing chips only (hint sentence removed); log; composer.
export function CommentsCard(p: { comments: { actor: string; at: string; text: string; kind: string; stepLabel: string | null }[];
  composer: { value: string; onChange: (v: string) => void; onPost: () => void; posting: boolean; inputRef: RefObject<HTMLInputElement | null> } | null }): JSX.Element;
// h2 "Comments and questions"; wrapper keeps class .wfcomments; list .clist (li.ret for "return"), .cmeta
// "{actor} · {fmtDateTime(at)} · on {stepLabel}" + " sent back" / " replied to the rejection"; .ctext.
// Empty -> one line "No comments yet." composer (input aria-label "Add a comment or question", Enter posts, "Post").
export function ActivityCard(p: { items: TrailItem[] }): JSX.Element; // ul.trail exactly as today
```

### Parent wiring rules (T008, frozen)

- `canAct = canManage && open` (unchanged). Every control below keeps today's gate:
  reassign, contract actions, trademark, start/pick workflow, check status, send back, comment composer,
  apply priority, doorway → `canAct`; Recompute → `canManage`; Retry review → `StepReviewNotice` own rule;
  Ask Aegis → everyone; ApprovalCard decide → its own `can_decide`.
- `pendingApproval = (approvals ?? []).some((a) => a.can_decide || (a.status === "pending" && !!a.approval_request_id))`
  — exactly ApprovalCard's own `active` rule, so DecisionArea never gets a node that renders nothing (FR-5). When
  true, `<ApprovalCard requestId rungs runId/>` (props as today) goes to `DecisionArea.approval` for every viewer;
  the card decides buttons vs read-only by `can_decide` (unchanged 009 behaviour, D3). It is the only ApprovalCard
  instance on the page.
- When `(approvals ?? []).length > 0`, an ExtraDetail `{ label: "Approval chain", node: <ApprovalChain rungs/> }`
  (chain only, never the card) is attached to `stepIdx` = the open approval step's idx, else the last done approval
  step, else the first approval step, else `null`.
- Negotiate condition (unchanged): `hasRun && !runComplete && current?.type === "counterparty" && (flowRun.contract_id ?? cid)`.
  `canAct` → DecisionArea.negotiation; otherwise ExtraDetail `{ stepIdx: current.idx, label: "Negotiation rounds" }`.
- `plainRun = flowRun && { ...flowRun, steps: flowRun.steps.map((s) => ({ ...s, name: plainStepName(s.name, s.type) })) }`
  is what ProgressStrip, NextStepCard and the rows read, so no internal name reaches LifecycleView or NextStepCard
  without changing those shared components.
- Title (D2): today's expression, unchanged — `r.subject || \`${r.type_label}${cp ? \` — ${cp}\` : ""}\``. The
  requester's typed subject is shown as written.
- Refs `commentRef` / `sendBackRef` stay in the parent and are passed down; the jump buttons scroll+focus as today.
- Removed from the parent: `STEP_DOORWAY` icon strings, `svg()`, `WARN_ICON`, `ini()`, `resultRows`, `WfStep`,
  `engineAction` header button, the header CLM link, and every card's inline markup.

## Inventory: every existing element → its place in layout A (the contract)

"Today" = current `_request-overview.tsx` (working tree with 009). Gates are unchanged unless stated.

| Existing element (today) | New location | Shown when |
|---|---|---|
| Query `intake-request` + poll while triage pending / setting up | parent (unchanged) | always |
| Query `intake-docs` | DocumentsCard rows; `hasAttachment` for contract actions | always |
| Query `intake-handoffs` | ActivityCard | always |
| Query `intake-assignees` (enabled canManage) | PeopleCard reassign options | canManage |
| Query `contract-risk` (+ poll while unknown) | `riskFacts` → RiskReview, header pill, decision warning | cid |
| Query `contract-devs` | `riskFacts` | cid |
| Query `intake-approval-chain` | DecisionArea ApprovalCard (when `pendingApproval`) + "Approval chain" detail | always fetched |
| Query `intake-teams` | NextStepCard in StepsCard | always fetched |
| Query `contract-review` (+ poll) | `currentStage`, StepReviewNotice, review-ended effect | cid |
| Query `flow-run` (+ capped poll) | everything workflow | always |
| Query `lifecycle-rows` | ProgressStrip `extras` | always |
| Query `flows` (enabled canManage) | StepsCard picker | canManage |
| Effects: review-ended refresh; step pump | parent (unchanged) | — |
| Template notice MessageBar | top of page (unchanged) | after a draft with `template_notice` |
| Crumb "← Legal Intake / REF" (back) | RequestHeader crumb | always |
| `<h1>` title (`subject` or type — cp) | RequestHeader h1, same expression (D2; typed title kept as written) | always |
| `.ref` span | RequestHeader subline | always |
| Badge: type | removed — "Type" fact in WhatWasAsked | — |
| Badge: value | removed — "Value" fact in WhatWasAsked | — |
| Badge: phase "Stage · N of M steps done, K skipped" | removed — stage in strip line, count in StepsCard `.cnt` (same wording) | — |
| Badge: "⚑ {band} risk" / "risk not reviewed" | "High risk" pill only when band high; other bands only in RiskReview | cid && band high |
| Badge: "⚠ overdue" | "Overdue" pill | sla overdue |
| Header "Open CLM editor →" | removed — DocumentsCard has the one link | — |
| Header engine button "Start workflow" | StepsCard flow line button "Start it" (merged with the old "start it →" link) | canAct && no run && suggested flow |
| Header engine button "Check approval status" / "Check signature status" | StepsCard Now block button (same label) | canAct && run open && current approval/signature |
| Header engine button "Check AI review status" | StepsCard Now block button | canAct && current ai_task running |
| Header "Agent working…" | ProgressStrip `working` indicator | run open && current running/waiting_job |
| "You are here" box (stage, step x of y, wait label / flow title, overdue) | ProgressStrip status line (rule above) | always |
| `LifecycleView compact` (clickable stages, sent-back line, stage panel) | ProgressStrip (same component, same props) | always |
| "The request": description quote | WhatWasAsked description | description non-empty |
| "— {source} by {requester}" attribution (the "— form by Local Admin" system suffix) | removed (requester is in the header summary + PeopleCard) | — |
| `RequestAnswers` (deliverables/answers editor, held note) | WhatWasAsked `answers` slot, same props | as today (component decides) |
| "Needs info before drafting" notice | WhatWasAsked | missing_info non-empty |
| "AI triage unavailable" notice | WhatWasAsked | ai_triage.degraded |
| "From the form" / "What the AI understood" label | WhatWasAsked card tag | always |
| Facts (Counterparty, Type, Value, Term, Priority) | WhatWasAsked facts grid | always (Value/Term when present) |
| "Aegis read" summary + 3 lists | WhatWasAsked | `ai_triage.read` |
| AI extraction confidence line | WhatWasAsked | confidence set && source ≠ form |
| AI Risk Review card (band, counts, groups, conceded, Recompute) | RiskReview (main) | cid; Recompute when canManage |
| "Workflow & stages" card title + `.cnt` + live/complete badge | StepsCard h2 "Steps" + `.cnt` countText; run state in flow line | always |
| "Running: {flow}. The workflow engine drives each step…" | flow line `Workflow: {flow}` (filler removed) | hasRun |
| "Suggested: {flow}. {reasoning} — start it →" | flow line + "Start it" button | no run && fs.flow_name; button canAct |
| "Setting up the workflow…" | flow line (same text) + strip line "Workflow being set up" | settingUp |
| "No workflow is set up…" + picker | flow line + `select[aria-label="Pick a workflow"]` | none; picker canAct |
| `flowRun.error` "⚠ …" | StepsCard error line | error set |
| Step ladder (`.steps`, stage headings, nodes, badges, assignee) | StepsCard list (same classes) | always |
| `.stype` internal type label ("AI step", "Human task", "Draft") | removed (FR-7) | — |
| Step doorway "Open the draft / Review & redline / Open to negotiate" | current step row in StepsCard (same rule) | canAct && current waiting_human && contract |
| `StepReviewNotice` + Retry review | interrupted step row (`notice`) | interrupted review step |
| `.sresult` raw result rows (drivers JSON) | collapsed "Review result" toggle on the step, `readableResult` | step has readable result |
| Agent hand-off trace block | collapsed "Agent hand-off trace (N)" under the list, plain step names | brief non-empty |
| NegotiationPanel | DecisionArea (canAct) / "Negotiation rounds" detail on the step (others) | negotiate condition |
| Send back for rework (select, reason, button, hint) | StepsCard `.sendback` (same texts) | hasRun && !complete && canAct && curIdx > 0 |
| Comments & questions (list + composer) | CommentsCard (main) | always; list empty → one line; composer when hasRun && canAct |
| Ask Aegis card (scope, hint, chips, log, composer) | AskAegisCard (side, last); hint sentence removed | always |
| "Next step" card (NextStepCard) | StepsCard Now block | hasRun |
| "Your decision" card | DecisionArea human card (same markup/labels) | atReviewGate (unchanged) |
| ApprovalCard (009) | DecisionArea, first in the main column, for every viewer: buttons for the decider, read-only "Waiting on …" for others (D3) | `pendingApproval` |
| "Approval chain" card (rungs, decisions, comments) | collapsed "Approval chain" detail on the Approval step, chain only (D3) | approvals non-empty |
| "Relationship" card | CounterpartyCard (side, first) | screening.counterparty && it adds a line |
| "Actions" card › Contract (ingest / draft / draft fresh) | DocumentsCard `contractActions` | canAct && !contract_id && docType |
| "Actions" card › Trademark | DocumentsCard `trademark` | canAct && isTrademarkRequest |
| "Actions" card › Owner reassign | PeopleCard `reassign` | canAct |
| At a glance › Raised by | RequestHeader subline (summary) + PeopleCard requester row (full detail) — allowed overlap (a), D12 | always |
| At a glance › Department | PeopleCard department row | department set |
| At a glance › Request owner | RequestHeader subline (summary) + PeopleCard owner row (full detail, Reassign) — allowed overlap (a), D12 | always |
| At a glance › Stage | ProgressStrip line | always |
| At a glance › Priority + "AI suggests X — apply" | WhatWasAsked Priority fact | always; apply link canAct |
| At a glance › SLA (overdue / at risk / on track) | header pills "Overdue" / "SLA at risk"; on track = no pill (D6) | sla overdue / at_risk |
| "Documents" card + "Open in CLM editor →" | DocumentsCard | always; link when contract |
| "People" card (requester, owner) | PeopleCard | always |
| "Activity" card | ActivityCard (main, last) | always |
| Loading / load error (retry + back) | parent (unchanged) | as today |

Mutations → control: `reassign` PeopleCard · `applyPriority` WhatWasAsked · `draft` DocumentsCard (both draft
buttons) · `ingest` DocumentsCard · `continueAsTrademark` DocumentsCard · `startFlow` StepsCard (Start it, picker) ·
`completeStep` DecisionArea Approve & advance (+ NegotiationPanel onConverged) · `retryReview` StepReviewNotice ·
`checkStep` StepsCard Now · `returnStep` StepsCard send back · `postComment` CommentsCard · `rerunRisk` RiskReview ·
`ask` AskAegisCard · `decide` (inside ApprovalCard, unchanged). Each action renders in exactly one place.

## Component design

### Backend
- Models / Schemas / Service / Router: none.
- Celery: none. Settings: none.

### Frontend (Next.js page/component tree)
- `src/app/(app)/intake/page.tsx` — unchanged; renders `<RequestOverview id canManage onBack>`.
  - `_request-overview.tsx` — data + mutations + derivations + layout + base CSS (T008).
  - `_request-header.tsx` — RequestHeader, ProgressStrip (T002).
  - `_decision-area.tsx` — DecisionArea (T006).
  - `_request-asked.tsx` — WhatWasAsked (T005).
  - `_request-risk.tsx` — riskFacts, RiskReview (T003).
  - `_request-steps.tsx` — StepsCard, ApprovalChain (T004).
  - `_request-cards.tsx` — CounterpartyCard, PeopleCard, DocumentsCard, AskAegisCard, CommentsCard, ActivityCard (T007).
  - Unchanged and reused: `_approval-card.tsx`, `_request-answers.tsx` (+ `_deliverables.tsx`), `_negotiation-panel.tsx`
    (one line removed), `@/components/lifecycle-view` (`LifecycleView`, `NextStepCard`, `currentStage`, `LIFECYCLE`,
    `stepStage`), `@/components/review-notice` (`StepReviewNotice`, `REVIEW_NOTICE_CSS`), `_governance-ladder.tsx`
    (`stepMeta`, `waitLabel`), `@/lib/request-facts`, `@/lib/review-retry`, `@/lib/workflow-steps`.
- New shared primitives in `src/components/ui.tsx`: none. Only `LoadError` and `MessageBar` are imported from ui
  (both exist today). Icons: lucide-react (`PenLine`, `ArrowLeftRight`, `RotateCw`, `Undo2`, `Check`,
  `MessageSquare`, `Send`, `AlertTriangle`, `Loader2`, `ChevronRight`/`ChevronDown`), sized by `.ro .ic`.
- State ownership: parent — reply, comment, backTo, backNote, reassignTo, q, chat, templateNotice (all typed text
  and mutation results). StepsCard — `open: Set<string>` of opened toggles (keys `result-{sidx}`,
  `extra-{label}-{stepIdx}`, `trace`). RiskReview — `openSevs` (default critical+high open). LifecycleView — its own
  `picked` stage. No other state.
- Demo-mode behavior: none to add; the request page has no demo data path today and none is introduced.

### CSS (D7)

One `<style>` in the parent: `RO_CSS + HEADER_CSS + DECISION_CSS + ASKED_CSS + RISK_CSS + STEPS_CSS + CARDS_CSS +
REVIEW_NOTICE_CSS`. Every rule stays under `.ro`; every colour is a global palette variable (each has a `.dark`
value in `globals.css`), so light and dark both work with no new tokens.

- Stays in `RO_CSS` (base, T008): `.lbl .ic .btn .btn.wide .card .ch .cb .cnt .glyph .facts .dim .cin .sel .composer
  .ministep(.doorway,[disabled]) .spin @keyframes ro-spin .analyzing .decide(.warn,.ok) .dw .db .reply` (the
  `.decide` family stays here because the unchanged ApprovalCard uses it), plus the new layout and pill rules:
  `.ro{min-width:0}`
  `.ro .body{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:16px;align-items:start;padding:16px 0 20px}`
  `.ro .col{min-width:0;display:flex;flex-direction:column;gap:14px}`
  `.ro .facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px 16px}` (4 across in
  the main column, 1 across in the side column and at phone width)
  `.ro .facts .f .v{overflow-wrap:anywhere;min-width:0}`
  `.ro .pill{display:inline-flex;padding:1px 9px;border-radius:99px;font:600 11px var(--sans);white-space:nowrap}`
  `.ro .pill.bad{background:var(--crit-soft);color:var(--crit)} .ro .pill.warn{background:var(--warn-soft);color:var(--warn)}`
  `@media (max-width:1080px){.ro .body{grid-template-columns:minmax(0,1fr)}}` (main column first, so the decision
  area stays first; the old `.rail2{order:-1}` is deleted).
- Moved to `HEADER_CSS`: `.rohd .crumb .hrow h1` (+ `overflow-wrap:anywhere`), new `.subl` (subline, wraps),
  `.pills`, `.lcwrap{min-width:0;overflow-x:auto}` (the 640px stage grid scrolls inside itself at phone width),
  new `.stline` (+ `.warn`).
- `ASKED_CSS`: `.quote .needsinfo .ni-items .ni-chip .ni-hint .confline .track`, new `.readlist`.
- `RISK_CSS`: `.risktop .rgauge .rmeta .rb .rc .sevgroup .sevhead .sevpill .sev-* .risk(.crit,.high,.med) .rh .rt .ri .rfix`.
- `STEPS_CSS`: `.flowmeta .flowpick .steps .step .snode .shd .sname(+overflow-wrap:anywhere) .sbadge .sassign
  .stagehd .sendback`, new `.sdetail` (replaces `.sresult`, a 2-col label/value list that wraps), new `.xtoggle`
  (detail toggle), new `.trace`, `.appr` family.
- `CARDS_CSS`: `.glance .person .av .docrow .wfcomments .clist .cmeta .ctext(+overflow-wrap:anywhere) .cret .trail
  .chat` family.
- Deleted (no longer rendered): `.youare` family, `.ya-ic`, `.flowstrip`, `.badges`, `.bdg.type`, `.bdg.val`,
  `.bdg.phase`, `.bdg` (replaced by `.pill`), `.ref`, `.stype`, `.sresult`, `.actions`, `.act`, `.cactions`,
  `.ministep.ghost`, `.rq`, `.rsum`, `.chat .hint`, `.rail2`.
- Hardcoded colours fixed (dark mode, AC-21): `.snode` and `.rgauge` `color:#fff` → `var(--accent-ink)`;
  `.sev-critical{background:#fdecec;color:#b42318}` → `background:var(--crit);color:var(--accent-ink)`;
  `.risk.crit{border-left-color:#b42318}` → `var(--crit)`. No `bg-white`, no hex in any new rule.

## Test strategy

- Backend pytest: none (no backend change).
- Frontend vitest (each new file ships its own; every test carries a docstring-style comment naming the failure mode):
  - `lib/workflow-steps.test.ts` (T001): `plainStepName` maps every table key ("Clm Draft", "clm_draft", "Ai Task",
    …), leaves "Legal review" unchanged, empty name → by type; `readableResult` on a playbook-review result with
    `top_drivers` RiskDriver objects yields "High · Liability is unlimited" and the joined output contains no `{`,
    `"`, `deviation_id`; deadline objects get "(date)"; an object with no text is dropped; unknown band →
    `RISK_UNKNOWN_LABEL`. (FR-7, FR-13, AC-7)
  - `_request-header.test.tsx` (T002): high+overdue → title, ref, "raised by … on …", "owner …", both pills; neither →
    no pills; at_risk → "SLA at risk"; ProgressStrip renders the line, `working` shows "Agent working…". (AC-2, FR-3)
  - `_request-risk.test.tsx` (T003): `riskFacts` arithmetic (moved cases: all decided / split / none decided — the
    `.rc` wording); one high + one conceded → band, "1 high", "still to decide", issue with clause + fix, conceded
    "accepted as-is — still counts"; medium group collapsed until clicked; Recompute only with canRecompute; unknown
    → one "Not reviewed" line; low & 0 → "No open issues.". (FR-12, AC-12)
  - `_request-steps.test.tsx` (T004): rows render vertically with done / here now / skipped / failed badges and
    assignees; "Review result" hidden until clicked and then readable; an "Approval chain" extra on the matching row
    and a `stepIdx:null` extra under the list; trace collapsed until clicked, plain names, no "Clm Draft"; send back
    options are only the given (earlier) steps, button disabled until picked, hint text; suggested → "Start it"
    only when canStart; setting_up text; none → picker only when canPick; empty rows → "No steps yet.".
    (FR-16, FR-19, AC-14, AC-15, AC-19)
  - `_request-asked.test.tsx` (T005): facts + tag; description once and no "form by"; notices only when given;
    Aegis read lists; confidence hidden for form; Priority "AI suggests … — apply" link only when canApply.
    (FR-10, FR-11, AC-8, AC-11)
  - `_decision-area.test.tsx` (T006): all null → renders nothing (AC-5, no-pending half); rework → reason, required
    reply, Approve & advance disabled until reply typed, Send back for rework…, Add a comment (AC-4); approval node
    renders first, before the human card, when both are given (owner who is also the approver: one section).
  - `_request-cards.test.tsx` (T007): CounterpartyCard null when it would repeat the facts' name only; "About …"
    when names differ; PeopleCard reassign only when given (AC-17); Documents one-line none, no CLM link without
    contract, link with (AC-16); Ask chips without the hint sentence; Comments one line "No comments yet.", sent-back
    entry marked with its step (AC-13); Activity order.
  - `_request-overview.test.tsx` (T008) — keep every existing case; required updates (the title tests, all
    `heading "Acme MSA"`, need no change: D2 keeps the typed subject):
    - "names the stage the way the stage bar does" (reads `.glance .k` Stage row) → read `.stline`; must contain
      "Review" and not "new".
    - "shows Approve and Reject above the chain to the assigned approver only" → additionally assert the buttons
      are inside `section.decision`, the first child of the main column.
    - "is read-only for a viewer who cannot decide" → no toggle click; additionally assert "Waiting on Finance team"
      is inside `section.decision`, the first child of the main column, with no Approve/Reject button (AC-5, AC-20).
    - "lists each rung's decisions…" → first click the "Approval chain" toggle, then assert as today (chain only).
    - Skipped-count cases (`/1 of 2 steps done, 2 skipped/`, `/1 of 2 steps done$/`) keep their wording, now on the
      StepsCard `.cnt`; class hooks `.steps .step .snode .sendback .wfcomments .cmeta .trail .rc` and the "Your
      decision" heading are kept so the rest pass unchanged.
    - New cases: AC-1 order (main column `h2` texts: [Approval, Your decision?] What was asked, AI risk review,
      Comments and questions, Activity; side: Counterparty relationship?, Owner and people, Documents, Steps, Ask
      Aegis); AC-3 exactly one `LifecycleView` (mock counts renders) and no "You are here", "At a glance",
      "Workflow & stages", "Next step" heading; AC-5 (a) a pending rung the viewer cannot decide → `section.decision`
      is the first child of the main column and holds the read-only card, (b) no pending rung and no human step →
      no `section.decision`, no `.apc`, and the first main-column `h2` is "What was asked"; AC-6 the header
      (`.rohd`) holds only the summary (title, ref, raised-by + date, owner, pills — no department, no counterparty
      card, no band word other than the "High risk" pill), and in `.body` minus `.apc` the owner, requester,
      counterparty and band each occur once; the `.apc` value/term/risk equal the WhatWasAsked Value/Term facts and
      the RiskReview band; AC-7 a step named "Clm Draft" with a `top_drivers` result → page text has no "Clm Draft",
      no `{"`; AC-8 form request (`request_form: "nda"`) with subject "Today_14_test" and description "Please
      review" → the `h1` is exactly "Today_14_test", the description appears as written in What was asked, and the
      page has no "form by" and no "workflow engine drives"; AC-9 no "workflow engine drives"
      and no "Ask about this request" text; AC-18/AC-20 action matrix — for each FR-24 state, every applicable
      action label is found exactly once with `canManage`, and none of the manage-only labels with `canManage=false`;
      with `canManage=false` and a pending rung the viewer cannot decide, the read-only card is present with no
      Approve/Reject (AC-20 third clause).
- Gates: `npm run typecheck` and `npm run test` from `frontend/` (never `npm run lint`).
- Acceptance verification (qa-engineer, `/verify`, live stack at the frontend port): sign in as each of — the owner
  (admin@example.com), an approver who is not the owner (a member of the approval step's team), the requester (a user
  without `intake:read`), and a staff user without manage rights where the dev DB has one — and open REQ-7705 plus
  one request per FR-24 state (no workflow yet, waiting on a human step, waiting on approval, rejected with reply
  owed, complete, no contract; pick them from `intake_request` / `workflow_run` in the dev DB). For each: light and
  dark mode screenshots; AC-22 at 400×800 — `document.documentElement.scrollWidth <= innerWidth`, the strip scrolls
  inside `.lcwrap`, every action from the T008 matrix reachable by scrolling; AC-24 side by side with screen A of
  `mockup.html` opened at a viewport where the mockup renders at scale 1 (≥ 1200px wide) and the app at the same
  viewport: record, via `getBoundingClientRect`, the top-to-bottom sequence of card headings per column (must match
  A's order exactly), the main:side width ratio, header-to-first-card distance and each card's height, and list
  every difference (expected: stage grid with dots vs A's top-bar strip; side column 300px vs 270px; no "Apply
  suggested wording"/"Nudge" buttons) for the user to accept. AC-25: perform Reassign, Post comment, Send back and
  Approve & advance once each and confirm one audit row and one timeline entry per action.

## Risks & decisions

- **D1 Parent keeps all wiring; children are presentational.** Rationale: one place for queries/mutations means
  toasts, invalidations and audit-triggering calls are untouched (AC-25), typed text survives refetches, and
  parallel tasks write disjoint files against frozen props. Rejected: each card owning its own query/mutation
  (moves the polling/invalidation web into seven files and risks a duplicated request or a lost invalidation).
- **D2 Title (decided by the user 2026-10-23).** The title expression is unchanged: the requester's typed `subject`
  (e.g. "Today_14_test") is the page title as written; without one, `type — counterparty` as today. Only
  system-added text goes: the "— {source} by {requester}" attribution ("— form by Local Admin"), the
  workflow-engine sentence and other D5 copy, internal step names (D4) and raw data (D4). Rejected: titling form
  requests `kind · counterparty` (replaces the requester's own wording, against FR-7).
- **D3 Approval card at the top for everyone; chain in the step detail (decided by the user 2026-10-23).** Whenever
  an approval is pending, 009's ApprovalCard is the first item in the main column for every viewer — with buttons
  for the decider, read-only (facts + "Waiting on …") for everyone else — exactly as 009 renders it. The "no space"
  rule (FR-5) applies only when no approval is pending. The collapsed "Approval chain" detail on the Approval step
  holds the chain only (rungs, decisions, comments, FR-19). Data shown to each viewer is unchanged (009 already
  shows the read-only card to everyone who can open the page). Rejected: putting the read-only card inside the
  collapsed chain detail (hides a pending approval's state; contradicts FR-4/AC-5 as amended).
- **D4 Readable names and results with no backend change.** The raw drivers JSON is `resultRows` falling back to
  `JSON.stringify` on `top_drivers` (RiskDriver objects have no `label`/`name`); "Clm Draft" is the backend's
  title-cased type for unnamed steps (`_clean_steps`) and the hand-off trace's `agent || type`. `readableResult` reads
  `issue`/`severity`; `plainStepName` maps the exact fallback strings; the parent hands plain-named steps to the
  shared `LifecycleView`/`NextStepCard` so those stay untouched. Rejected: renaming steps in the backend (needs a data
  migration of saved workflows and runs, out of scope).
- **D5 Fixed copy removed** (FR-8): "The workflow engine drives each step — agent runs, approvals, and signatures
  move it forward and back."; the "— {source} by {requester}" attribution ("form by Local Admin"); "No description
  provided." (block omitted instead); Ask hint "Ask about this request — its terms, risks, obligations, or how it
  compares to precedent."; "No comments yet — leave a note or ask a question about a stage." → "No comments yet.";
  "No stages defined." → "No steps yet."; "No open deviations — the contract is clean against the playbook." →
  "No open issues."; the NegotiationPanel footer "Send the current version to the … then accept or counter."
  (`_negotiation-panel.tsx`, one line); the "You are here" / phase badge / type / value badges (duplicates); `.stype`
  type names. Kept because they describe this request's real state: needs-info hint, degraded fallback reason,
  "Setting up the workflow…", the send-back hint, "Loading the risk review…". `LifecycleView`'s per-stage `about`
  sentence is kept: FR-20 requires the stage panel "in the same words as today" and it only appears after a click.
- **D6 SLA.** Overdue / at risk become header pills; on track shows nothing (FR-2: a pill only when its condition is
  true). Request-level overdue is not repeated in the strip line; a step's own overdue (`approval.overdue`) is.
- **D7 CSS stays `.ro`-scoped strings, split per component file.** Rationale: matches the module, lets parallel tasks
  own their own rules, and dark mode comes from the existing global variables. Rejected: Tailwind rewrite of the page
  (a reskin, against the "keep the app's look" constraint and a much larger diff).
- **D8 Progress strip = the existing `LifecycleView compact` grid**, not its `strip` mode. Rationale: the grid is
  today's lifecycle bar on this page, shows each stage's caption visibly ("2 of 2 done", "Waiting · X") as FR-20 and
  mockup A require, and is clickable; `strip` mode shows captions only as tooltips and its CSS is `.clm`-scoped.
  Cost: ~25px taller than A's top-bar strip — recorded as an accepted-difference candidate for AC-24.
- **D9 Side column 300px** (A: 270px at a 1100px screen; today: 316px). 300 keeps the send-back select, NextStepCard
  and the expanded approval chain legible. Recorded for AC-24.
- **D10 Negotiation panel**: in the decision area (after the approval card and human card) for people who can act;
  for everyone else it stays reachable as "Negotiation rounds" detail on the negotiate step (they see the rounds
  today). Unaffected by the amended D3: the panel is a working tool, not a read-only approval state, and FR-4 names
  only the approval card as shown to every viewer.
- **D12 Header vs side cards (decided by the user 2026-10-23).** The header is a one-line summary (title, ref,
  raised-by + date, owner, pills); the full owner, requester (and department) details and Reassign live once in
  Owner and people, the counterparty relationship once in its side card. Header summary and the approval card's
  value/term/risk are the only repeats (FR-6 (a)/(b)); both read the same `r` / `riskFacts` / approval summary
  data, so they cannot disagree. Rejected: dropping the names from Owner and people.
- **D11 Contract and trademark actions live in Documents** (the Actions card is dissolved). FR-4 limits the decision
  area to the approval card and human-step controls, and Documents is where the attached file and the CLM link are.
- Risk: long real data (clause text, Aegis read items, comments, file names) — handled by `minmax(0,1fr)` grids,
  `overflow-wrap:anywhere`, one-item-per-row read lists, and filename ellipsis; verify with REQ-7705 and the longest
  description in the dev DB, not mock text.
- Risk: `_request-overview.tsx` carries uncommitted 009 work; T008 must not revert it (check `git diff` first).
- Risk: hidden-until-opened details change test queries (`findByText` on chain text); covered in the test updates.
- Risk: `pendingApproval` drifting from ApprovalCard's own `active` rule would put an empty `section.decision` on
  the page (card renders null) or hide a pending approval. Mitigation: the parent uses the identical predicate
  (copied, with a comment pointing at `_approval-card.tsx`), and the AC-5 (a)/(b) tests pin both sides.
- Risk: the read-only card's "Waiting on {approver}" and the strip line's wait words both name who the approval
  waits on. Accepted: the strip line is FR-3 progress, the card is 009 content (out of scope to change).
- FR traceability: FR-1 → parent layout (`.body` columns, order); FR-2 → RequestHeader; FR-3 → ProgressStrip
  (+ status-line rule); FR-4 → DecisionArea + `pendingApproval` (D3); FR-5 → DecisionArea returns null when no
  approval is pending and no human step; FR-6 → `riskFacts` single source, inventory removals, CounterpartyCard
  name rule, D12; FR-7 → `plainStepName`, `readableResult`, `.stype` removed, trace plain names, typed title kept
  (D2); FR-8 → D5, D2; FR-9 → one-line empties in WhatWasAsked/RiskReview/Cards/StepsCard, CounterpartyCard
  null; FR-10/FR-11 → WhatWasAsked; FR-12 → RiskReview; FR-13 → StepsCard "Review result" detail; FR-14 →
  CommentsCard; FR-15 → ActivityCard; FR-16 → StepsCard (+ send back, suggested preview); FR-17 → CounterpartyCard,
  PeopleCard, DocumentsCard, AskAegisCard; FR-18 → inventory + mutations list (each action one place, same gates);
  FR-19 → StepsCard Now block, extras (chain, D3), trace, error line, flow line, template notice, header pills, PeopleCard
  department, Priority fact; FR-20 → ProgressStrip/LifecycleView (D8); FR-21 → D7 + hardcoded-colour fixes; FR-22 →
  1080px stack + `.lcwrap` + auto-fill facts; FR-23 → parent loading/error unchanged + setting-up line; FR-24 → qa
  side-by-side.

## Task split (proposal for /tasks)

| Task | Agent | Files | Depends on | Parallel |
|---|---|---|---|---|
| T001 helpers | frontend-dev | `lib/workflow-steps.ts`, `lib/workflow-steps.test.ts` | — | [P] |
| T002 header + strip | frontend-dev | `_request-header.tsx`, `_request-header.test.tsx` | — | [P] |
| T003 risk | frontend-dev | `_request-risk.tsx`, `_request-risk.test.tsx` | — | [P] |
| T004 steps | frontend-dev | `_request-steps.tsx`, `_request-steps.test.tsx` | T001 | after T001 |
| T005 what was asked | frontend-dev | `_request-asked.tsx`, `_request-asked.test.tsx` | — | [P] |
| T006 decision area | frontend-dev | `_decision-area.tsx`, `_decision-area.test.tsx`, `_negotiation-panel.tsx` | — | [P] |
| T007 side + thread cards | frontend-dev | `_request-cards.tsx`, `_request-cards.test.tsx` | — | [P] |
| T008 integrate | frontend-dev | `_request-overview.tsx`, `_request-overview.test.tsx` | T001–T007 | serial (hotspot) |
| T009 verify | qa-engineer | `specs/010-request-page-decision-first/verification.md` | T008 | serial |

Wave 1: T001, T002, T003, T005, T006, T007. Wave 2: T004. Wave 3: T008. Wave 4: T009. Each component task runs
`npm run typecheck` + its own vitest file; T008 runs the full `npm run test`.

## Open questions

None open. Resolved by the user on 2026-10-23 (spec "Decisions"): header summary vs side cards → D12; typed title
kept → D2; read-only approval card at the top of the main column → D3.
