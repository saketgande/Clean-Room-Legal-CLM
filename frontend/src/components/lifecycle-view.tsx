"use client";

// The contract lifecycle: the same 7 stages for every contract and request, with
// the workflow's steps listed under the stage they belong to (step.stage, set in
// the workflow builder — see backend app/workflows/stages.py). Click a stage to
// see its steps. Shared by the request ticket and the contract page so both show
// one lifecycle, not two.

import { useState } from "react";
import { Check, Clock, Minus, Undo2 } from "lucide-react";
import { cn, fmtRelative } from "@/lib/utils";
import type { IntakeTeam, LifecycleRow, LifecycleStage, WorkflowRun, WorkflowRunComment, WorkflowRunStep } from "@/lib/types";

export const LIFECYCLE: { key: LifecycleStage; label: string; about: string }[] = [
  { key: "intake", label: "Intake", about: "The request arrives and Legal takes ownership: the AI reads it, an owner is assigned and the workflow that runs every later stage is chosen." },
  { key: "drafting", label: "Drafting", about: "The first draft is produced, from an approved template or with AI." },
  { key: "review", label: "Review", about: "Every review the workflow asks for — AI checks, legal, finance, privacy and the negotiation with the other side." },
  { key: "approval", label: "Approval", about: "The sign-offs the workflow requires, in order. Each approval step names its own approver; nothing outside the workflow adds one." },
  { key: "signature", label: "Signature", about: "The approved version is sent for signature; the signed copy becomes the contract of record." },
  { key: "active", label: "Active", about: "The signed contract is in force: obligations are tracked and the renewal window is watched." },
  { key: "closed", label: "Closed", about: "The contract has ended — expired, terminated or not renewed. It stays searchable and held for retention." },
];
const RANK = Object.fromEntries(LIFECYCLE.map((s, i) => [s.key, i])) as Record<LifecycleStage, number>;
const isStage = (s: unknown): s is LifecycleStage => typeof s === "string" && s in RANK;

const DONE = new Set(["done", "complete"]);
const OPEN = new Set(["running", "waiting", "waiting_human", "waiting_job"]);
const STATUS_LABEL: Record<string, string> = {
  done: "Done", complete: "Done", skipped: "Skipped", running: "In progress", waiting: "Waiting",
  waiting_human: "Waiting", waiting_job: "Working", pending: "Not reached", failed: "Failed",
};

/** A step's stage; runs saved before stages existed default to Review. */
export const stepStage = (s: Pick<WorkflowRunStep, "stage">): LifecycleStage => (isStage(s.stage) ? s.stage : "review");

/** Where this contract/request is now. A live workflow is the truth while it
 * runs (its current step's stage); after that the contract's own stage; a
 * closed request with nothing drafted is Closed; otherwise it is still Intake. */
export function currentStage({ run, contractStage, requestClosed }: {
  run?: Pick<WorkflowRun, "status" | "current_index" | "steps"> | null;
  contractStage?: string | null;
  requestClosed?: boolean;
}): LifecycleStage {
  const live = run && (run.status === "running" || run.status === "waiting");
  const step = live ? run.steps?.[run.current_index] : undefined;
  if (step) return stepStage(step);
  if (isStage(contractStage)) return contractStage;
  if (requestClosed) return "closed";
  if (run?.status === "complete" && run.steps?.length) {
    return run.steps.reduce<LifecycleStage>((hi, s) => (RANK[stepStage(s)] > RANK[hi] ? stepStage(s) : hi), "intake");
  }
  return "intake";
}

function who(s: WorkflowRunStep) {
  return s.assignee_label || s.team_label || s.planned_team_label || (s.type === "counterparty" ? "The other side" : s.type === "ai_task" ? "Aegis AI" : "—");
}

const KIND: Record<string, string> = {
  ai_task: "AI", human_task: "Review", approval: "Approval", signature: "Signature",
  clm_draft: "Draft", counterparty: "Negotiation", notify: "Notify",
};

/** One line on what a step needs or found: authority for approvals, why it was
 * skipped, its note, or the headline of its result. */
export function stepDetail(s: WorkflowRunStep): string {
  if (s.authority_note) return s.authority_note;
  if (s.note) return s.note;
  const r = (s.result ?? {}) as Record<string, unknown>;
  if (typeof r.summary === "string" && r.summary) return r.summary;
  if (r.risk_band) return `Risk ${r.risk_band}${r.risk_score != null ? ` (${r.risk_score})` : ""}`;
  if (r.category) return `Read as ${r.category}${r.confidence != null ? ` · ${Math.round(Number(r.confidence) * 100)}% confidence` : ""}`;
  if (r.contract_id && s.type === "clm_draft") return "Draft created";
  return "";
}

type Tone = "done" | "live" | "skipped" | "planned" | "failed";
type Row = { key: string; name: string; kind: string; detail: string; who: string; tone: Tone; label: string; when: string | null; idx?: number };

function fromStep(s: WorkflowRunStep): Row {
  const tone: Tone = DONE.has(s.status) ? "done" : OPEN.has(s.status) ? "live" : s.status === "skipped" ? "skipped" : s.status === "failed" ? "failed" : "planned";
  return { key: `s${s.idx}`, idx: s.idx, name: s.name, kind: KIND[s.type] ?? "Step", detail: stepDetail(s), who: who(s), tone,
    label: STATUS_LABEL[s.status] ?? s.status, when: tone === "done" || tone === "live" ? s.updated_at : null };
}

function fromExtra(r: LifecycleRow, i: number, stage: string): Row {
  const tone: Tone = r.status === "done" ? "done" : r.status === "waiting" ? "live" : r.status === "skipped" ? "skipped" : "planned";
  return { key: `x${stage}${i}`, name: r.name, kind: r.kind, detail: r.detail, who: r.who, tone,
    label: { done: "Done", live: "Waiting", skipped: "Skipped", planned: "Planned", failed: "Failed" }[tone], when: r.when };
}

function caption(rows: Row[], state: "done" | "current" | "upcoming") {
  if (!rows.length) return state === "done" ? "Passed" : "No steps";
  const live = rows.find((r) => r.tone === "live");
  if (state === "current" && live) return `Waiting · ${live.name}`;
  const done = rows.filter((r) => r.tone === "done" || r.tone === "skipped").length;
  return `${done} of ${rows.length} done`;
}

export function LifecycleView({
  steps, current, selectedStepIdx = null, onSelectStep, compact = false, comments = [], extras = {},
}: {
  steps: WorkflowRunStep[];
  current: LifecycleStage;
  /** The run's thread; its "return" entries are the send-backs shown under the bar. */
  comments?: WorkflowRunComment[];
  /** Rows that aren't workflow steps: request events, signers, obligations, renewal, expiry. */
  extras?: Partial<Record<LifecycleStage, LifecycleRow[]>>;
  selectedStepIdx?: number | null;
  onSelectStep?: (idx: number) => void;
  /** Page-header use: no stage is open until one is clicked. */
  compact?: boolean;
}) {
  const [picked, setPicked] = useState<LifecycleStage | null>(null);
  const open = compact ? picked : picked ?? current;
  // Intake's own events come before any workflow step in it; elsewhere the
  // workflow's steps lead and the stage's own rows (signers, obligations…) follow.
  const rowsOf = (k: LifecycleStage): Row[] => {
    const own = steps.filter((s) => stepStage(s) === k).map(fromStep);
    const extra = (extras[k] ?? []).map((r, i) => fromExtra(r, i, k));
    return k === "intake" ? [...extra, ...own] : [...own, ...extra];
  };
  const sel = LIFECYCLE.find((s) => s.key === open);
  const stateOf = (k: LifecycleStage) => (RANK[k] < RANK[current] ? "done" : k === current ? "current" : "upcoming");

  return (
    <div>
      <ol className="grid min-w-[640px] grid-cols-7 gap-1 overflow-x-auto">
        {LIFECYCLE.map((s, i) => {
          const st = stateOf(s.key);
          const isOpen = open === s.key;
          return (
            <li key={s.key} className="relative">
              {i > 0 && <span aria-hidden className={cn("absolute right-1/2 top-[21px] h-0.5 w-[calc(100%+4px)]", st === "upcoming" ? "bg-slate-200" : "bg-brand-600")} />}
              <button type="button" aria-pressed={isOpen} onClick={() => setPicked(compact && isOpen ? null : s.key)}
                aria-label={`${s.label}: ${st === "done" ? "done" : st === "current" ? "now" : "not reached yet"}. Show its steps.`}
                className={cn("relative z-10 flex w-full flex-col items-center gap-1 rounded-lg px-1 py-1.5 text-center transition-colors hover:bg-slate-50",
                  isOpen && "bg-slate-50 ring-1 ring-slate-300")}>
                <span className={cn("grid h-[30px] w-[30px] place-items-center rounded-full border-2 text-xs font-semibold",
                  st === "done" ? "border-brand-600 bg-brand-600 text-white"
                    : st === "current" ? "border-brand-600 bg-brand-50 text-brand-700 ring-4 ring-brand-100"
                    : "border-slate-300 bg-slate-50 text-slate-400")}>
                  {st === "done" ? <Check className="h-4 w-4" /> : st === "current" ? <Clock className="h-4 w-4" /> : i + 1}
                </span>
                <span className={cn("text-[12.5px] font-semibold", st === "upcoming" ? "text-slate-400" : "text-slate-900")}>{s.label}</span>
                <span className="line-clamp-1 max-w-full text-[11px] text-slate-500">{caption(rowsOf(s.key), st)}</span>
              </button>
            </li>
          );
        })}
      </ol>

      {(() => {
        const back = comments.filter((c) => c.kind === "return");
        const last = back[back.length - 1];
        if (!last) return null;
        return (
          <p className="mt-2 flex items-start gap-1.5 text-[12px] text-warning">
            <Undo2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              Sent back {back.length === 1 ? "once" : `${back.length} times`} · latest {fmtRelative(last.at)}
              {last.actor_name ? ` by ${last.actor_name}` : ""}: <span className="text-slate-600">{last.text}</span>
            </span>
          </p>
        );
      })()}

      {sel && (
        <div className={cn("mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3.5", compact && "max-h-80 overflow-y-auto")}>
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-slate-400">Stage {RANK[sel.key] + 1} of 7</span>
            <span className="text-[14px] font-semibold text-slate-900">{sel.label}</span>
            <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-semibold",
              stateOf(sel.key) === "done" ? "bg-success-subtle text-success"
                : stateOf(sel.key) === "current" ? "bg-brand-50 text-brand-700" : "bg-slate-200 text-slate-500")}>
              {stateOf(sel.key) === "done" ? "Done" : stateOf(sel.key) === "current" ? "Now" : "Not reached yet"}
            </span>
          </div>
          <p className="mt-1 text-[12.5px] leading-relaxed text-slate-500">{sel.about}</p>
          {rowsOf(sel.key).length === 0 ? (
            <p className="mt-2.5 text-[12.5px] text-slate-400">Nothing happens in {sel.label} for this contract.</p>
          ) : (
            <ul className="mt-2.5 divide-y divide-slate-200 rounded-md border border-slate-200 bg-slate-100">
              {rowsOf(sel.key).map((r) => (
                <li key={r.key}>
                  <button type="button" disabled={r.idx == null || !onSelectStep} onClick={() => r.idx != null && onSelectStep?.(r.idx)}
                    className={cn("flex w-full items-center gap-3 px-3 py-2 text-left", r.idx != null && onSelectStep && "hover:bg-slate-50",
                      r.idx != null && selectedStepIdx === r.idx && "bg-brand-50")}>
                    <span className={cn("grid h-5 w-5 shrink-0 place-items-center rounded-full",
                      r.tone === "done" ? "bg-success text-white" : r.tone === "live" ? "bg-warning text-white"
                        : r.tone === "skipped" ? "bg-slate-200 text-slate-500" : r.tone === "failed" ? "bg-danger text-white" : "border-2 border-slate-300")}>
                      {r.tone === "done" ? <Check className="h-3 w-3" /> : r.tone === "live" ? <Clock className="h-3 w-3" /> : r.tone === "skipped" ? <Minus className="h-3 w-3" /> : null}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <span className="truncate text-[13px] font-medium text-slate-900">{r.name}</span>
                        <span className="shrink-0 rounded bg-slate-200 px-1.5 py-px text-[10px] font-semibold uppercase tracking-[0.04em] text-slate-600">{r.kind}</span>
                      </span>
                      {r.detail ? <span className="block truncate text-[11.5px] text-slate-500" title={r.detail}>{r.detail}</span> : null}
                    </span>
                    <span className="hidden w-40 shrink-0 truncate text-[12px] text-slate-600 sm:block" title={r.who}>{r.who}</span>
                    <span className="w-20 shrink-0 text-right">
                      <span className={cn("block text-[12px] font-semibold", r.tone === "done" ? "text-success" : r.tone === "live" ? "text-warning" : r.tone === "failed" ? "text-danger" : "text-slate-400")}>
                        {r.label}
                      </span>
                      {r.when ? <span className="block text-[11px] text-slate-400">{fmtRelative(r.when)}</span> : null}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/** The step the work is waiting on, who can do it, by when, and what comes after. */
export function NextStepCard({ run, teams }: { run: WorkflowRun; teams?: IntakeTeam[] }) {
  const steps = run.steps ?? [];
  if (run.status === "complete") {
    return <div className="text-[13px] text-slate-600"><b className="text-slate-900">Workflow complete.</b> Every step is done.</div>;
  }
  const cur = steps.find((s) => OPEN.has(s.status)) ?? steps[run.current_index];
  if (!cur) return null;
  const team = (teams ?? []).find((t) => t.id === cur.team_id) ?? (teams ?? []).find((t) => t.name === (cur.team_label ?? cur.planned_team_label));
  const members = (team?.members ?? []).filter((m) => m.active).map((m) => m.name ?? m.user_id);
  const sla = Number(cur.sla_hours);
  const due = sla > 0 && cur.updated_at ? new Date(new Date(cur.updated_at).getTime() + sla * 3_600_000) : null;
  const overdue = due != null && due.getTime() < Date.now();
  let end = run.current_index + 1;
  while (end < steps.length && steps[end].parallel) end++;
  const then = steps[end];
  const stageName = (s: WorkflowRunStep) => LIFECYCLE.find((x) => x.key === stepStage(s))?.label;
  return (
    <div className="space-y-1.5 text-[13px] leading-relaxed text-slate-600">
      <div className="text-[14px] font-semibold text-slate-900">{cur.name} <span className="font-normal text-slate-500">· {stageName(cur)}</span></div>
      <div>
        {cur.assignee_label ? <>With <b className="text-slate-800">{cur.assignee_label}</b>{team ? ` (${team.name})` : ""}. </> : null}
        {!cur.assignee_label && team ? <><b className="text-slate-800">{team.name}</b> team — {members.length ? members.join(", ") : "no members yet"}. </> : null}
        {!cur.assignee_label && !team ? <>{who(cur)}. </> : null}
      </div>
      {cur.authority_note ? <div>{cur.authority_note}.</div> : null}
      {due ? <div className={overdue ? "font-semibold text-danger" : ""}>{overdue ? "Overdue since" : "Due"} {due.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}.</div> : null}
      <div className="text-slate-500">{then ? <>Then: <b className="text-slate-700">{then.name}</b> ({stageName(then)}).</> : "Then the workflow is complete."}</div>
    </div>
  );
}
