"use client";

// The contract lifecycle: the same 7 stages for every contract and request, with
// the workflow's steps listed under the stage they belong to (step.stage, set in
// the workflow builder — see backend app/workflows/stages.py). Click a stage to
// see its steps. Shared by the request ticket and the contract page so both show
// one lifecycle, not two.

import React, { useState, type ReactNode } from "react";
import { ArrowRight, Check, ChevronDown, ChevronRight, Circle, Clock, Minus, Undo2, X } from "lucide-react";
import { cn, fmtRelative, RISK_UNKNOWN_LABEL } from "@/lib/utils";
import type { IntakeTeam, LifecycleAction, LifecycleRow, LifecycleStage, WorkflowRun, WorkflowRunComment, WorkflowRunStep } from "@/lib/types";

/** Aegis's own mark for AI work: a shield holding a small neural net. Its nodes breathe all the time and a signal
 *  runs along the links while a step is working (the styling is in LC_PANEL_CSS). */
export function AiMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={cn("aimark", className)} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path className="sh" d="M12 2.8 19.5 6v6c0 4.2-3.2 7.4-7.5 9.2C7.7 19.4 4.5 16.2 4.5 12V6z" />
      <path className="net" strokeWidth="1.2" d="M12 8.2 8.6 14.4h6.8zM12 8.2v3.6M8.6 14.4 12 11.8l3.4 2.6" />
      <circle className="nd n1" cx="12" cy="8.2" r="1.3" fill="currentColor" stroke="none" />
      <circle className="nd n2" cx="8.6" cy="14.4" r="1.3" fill="currentColor" stroke="none" />
      <circle className="nd n3" cx="15.4" cy="14.4" r="1.3" fill="currentColor" stroke="none" />
      <circle className="nd core" cx="12" cy="11.8" r="1.5" fill="currentColor" stroke="none" />
    </svg>
  );
}

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

/** What `who` prints when a step has no named owner; callers that build a sentence leave it out. */
const NOBODY = "—";

function who(s: WorkflowRunStep) {
  return s.assignee_label || s.team_label || s.planned_team_label || (s.type === "counterparty" ? "The other side" : s.type === "ai_task" ? "Aegis AI" : NOBODY);
}

const KIND: Record<string, string> = {
  ai_task: "AI", human_task: "Review", approval: "Approval", signature: "Signature",
  clm_draft: "Draft", counterparty: "Negotiation", notify: "Notify",
};

/** One line on what a step needs or found: authority for approvals, why it was
 * skipped, its note, or the headline of its result. */
function stepDetail(s: WorkflowRunStep): string {
  if (s.authority_note) return s.authority_note;
  if (s.note) return s.note;
  const r = (s.result ?? {}) as Record<string, unknown>;
  if (typeof r.summary === "string" && r.summary) return r.summary;
  // The band the step saw; the 0-100 score was the deleted AI risk score.
  if (r.risk_band) return r.risk_band === "unknown" ? RISK_UNKNOWN_LABEL : `Risk ${r.risk_band}`;
  if (r.category) return `Read as ${r.category}${r.confidence != null ? ` · ${Math.round(Number(r.confidence) * 100)}% confidence` : ""}`;
  if (r.contract_id && s.type === "clm_draft") return "Draft created";
  return "";
}

type Tone = "done" | "live" | "skipped" | "planned" | "failed";
type Row = { key: string; name: string; kind: string; detail: string; who: string; tone: Tone; label: string; when: string | null; idx?: number; action?: LifecycleAction | null };

/** The drafting step is skipped by the engine when the contract already exists (uploaded, or drafted before). For a
 *  reader that is not a skip: the document is there. A skip with a reason of its own (a Processor DPA) stays one. */
const draftWasProvided = (s: WorkflowRunStep) => s.type === "clm_draft" && s.status === "skipped" && !(s.result as { skipped?: string } | null)?.skipped;

function fromStep(s: WorkflowRunStep, draftNote?: string | null): Row {
  if (draftWasProvided(s)) {
    return { key: `s${s.idx}`, idx: s.idx, name: s.name, kind: KIND[s.type] ?? "Step", detail: draftNote ?? "The contract was already in place", who: who(s), tone: "done", label: "Done", when: s.updated_at };
  }
  const tone: Tone = DONE.has(s.status) ? "done" : OPEN.has(s.status) ? "live" : s.status === "skipped" ? "skipped" : s.status === "failed" ? "failed" : "planned";
  return { key: `s${s.idx}`, idx: s.idx, name: s.name, kind: KIND[s.type] ?? "Step", detail: stepDetail(s), who: who(s), tone,
    label: STATUS_LABEL[s.status] ?? s.status, when: tone === "done" || tone === "live" ? s.updated_at : null };
}

function fromExtra(r: LifecycleRow, i: number, stage: string): Row {
  const tone: Tone = r.status === "done" ? "done" : r.status === "waiting" ? "live" : r.status === "skipped" ? "skipped" : r.status === "failed" ? "failed" : "planned";
  return { key: `x${stage}${i}`, name: r.name, kind: r.kind, detail: r.detail, who: r.who, tone,
    label: { done: "Done", live: "Waiting", skipped: "Skipped", planned: "Planned", failed: "Failed" }[tone], when: r.when, action: r.action ?? null };
}

/** True while something the page is waiting on is being worked on by the AI (a row waiting with nothing to press),
 *  so the page that owns the data keeps refetching until it settles. */
export function aiIsWorking(extras: Partial<Record<LifecycleStage, LifecycleRow[]>> | undefined): boolean {
  return Object.values(extras ?? {}).some((rows) => (rows ?? []).some((r) => r.kind === "AI" && r.status === "waiting" && !r.action));
}

/** What needs a person in this stage, if anything: a failed step or row. Shown without opening the stage. */
const needsAttention = (rows: Row[], state: "done" | "current" | "upcoming") =>
  state === "upcoming" ? undefined : rows.find((r) => r.tone === "failed");

/** The stage button's accessible name. It replaces the button's content for a screen reader, so what needs a person
 *  has to be in it, or the caption "Needs attention" is never announced. */
const stageLabel = (label: string, state: "done" | "current" | "upcoming", failed: Row | undefined) =>
  `${label}: ${state === "done" ? "done" : state === "current" ? "now" : "not reached yet"}. ${failed ? `Needs attention: ${failed.name}. ` : ""}Show its steps.`;

function caption(rows: Row[], state: "done" | "current" | "upcoming") {
  if (!rows.length) return state === "done" ? "Passed" : "No steps";
  const failed = needsAttention(rows, state);
  if (failed) return `Needs attention · ${failed.name}`;
  const live = rows.find((r) => r.tone === "live");
  if (state === "current" && live) return `Waiting · ${live.name}`;
  // A skipped step (its condition was not met) was never done, so it is not counted as done, and it is not
  // work this request needed either: the count is of the steps that applied, with the skipped ones named.
  const skipped = rows.filter((r) => r.tone === "skipped").length;
  const needed = rows.length - skipped;
  if (needed === 0) return "All skipped";
  const done = rows.filter((r) => r.tone === "done").length;
  return `${done} of ${needed} done${skipped ? `, ${skipped} skipped` : ""}`;
}

export function LifecycleView({
  steps, current, selectedStepIdx = null, onSelectStep, compact = false, strip = false, row = false, timeline = false, draftNote = null, comments = [], extras = {}, trailing, onAction,
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
  /** Seven stages across the page, state and progress under each; no stage panel (the Workflow tab lists the steps). */
  row?: boolean;
  /** A vertical timeline for a narrow side column: a dot per stage on a dashed line, each stage opening to its steps. */
  timeline?: boolean;
  /** How the draft came about ("Drafted from our template", "Uploaded document provided: …"), shown on the drafting step. */
  draftNote?: string | null;
  /** With `compact`: draw the stages as one slim row instead of the grid. Its styles
   *  (`LIFECYCLE_STRIP_CSS`) need the contract page's `.clm` colour variables, so only that page asks for it. */
  strip?: boolean;
  /** With `strip`: shown at the right end of the row (the stage's time against its target). */
  trailing?: ReactNode;
  /** Pressing a row's action (Retry, Re-read). Left out, no action button is drawn: the page passes it only to
   *  someone who may do it. It may reject; the message is shown under the list. */
  onAction?: (action: LifecycleAction) => Promise<unknown> | void;
}) {
  const [picked, setPicked] = useState<LifecycleStage | null>(null);
  // Timeline: undefined = the current stage is open until a person chooses; "" = all closed.
  const [tlPick, setTlPick] = useState<LifecycleStage | "" | undefined>(undefined);
  const [busy, setBusy] = useState<string | null>(null);
  // The refusal belongs to the stage it came from: opening another stage must not show it there.
  const [problem, setProblem] = useState<{ stage: LifecycleStage; text: string } | null>(null);
  async function press(r: Row, stage: LifecycleStage) {
    if (!r.action || !onAction) return;
    setBusy(r.key);
    setProblem(null);
    try {
      await onAction(r.action);
    } catch (e) {
      setProblem({ stage, text: e instanceof Error && e.message ? e.message : "That did not work. Try again." });
    } finally {
      setBusy(null);
    }
  }
  const open = compact && (strip || row) ? picked : picked ?? current;
  // Intake's own events come before any workflow step in it; elsewhere the
  // workflow's steps lead and the stage's own rows (signers, obligations…) follow.
  const rowsOf = (k: LifecycleStage): Row[] => {
    // A step whose condition was not met does not apply to this contract: it is not listed.
    const own = steps.filter((s) => stepStage(s) === k && !(s.status === "skipped" && s.cond)).map((s) => fromStep(s, draftNote));
    const extra = (extras[k] ?? []).map((r, i) => fromExtra(r, i, k));
    return k === "intake" ? [...extra, ...own] : [...own, ...extra];
  };
  const sel = LIFECYCLE.find((s) => s.key === open);
  const stateOf = (k: LifecycleStage) => (RANK[k] < RANK[current] ? "done" : k === current ? "current" : "upcoming");

  const compactRow = (
    <div className="lcs-row">
      <ol className="lcs-strip">
        {LIFECYCLE.map((s) => {
          const st = stateOf(s.key);
          const isOpen = open === s.key;
          return (
            <li key={s.key} className={`lcs-st ${st}${needsAttention(rowsOf(s.key), st) ? " attn" : ""}`}>
              <button type="button" aria-pressed={isOpen} onClick={() => setPicked(isOpen ? null : s.key)}
                title={caption(rowsOf(s.key), st)}
                aria-label={stageLabel(s.label, st, needsAttention(rowsOf(s.key), st))}>
                <span className="lcs-dot" aria-hidden>{st === "done" ? <Check className="h-2.5 w-2.5" strokeWidth={3} /> : null}</span>
                <span className="lcs-name">{s.label}</span>
              </button>
            </li>
          );
        })}
      </ol>
      {trailing ? <div className="lcs-trail">{trailing}</div> : null}
    </div>
  );

  if (timeline) {
    const tlOpen = tlPick === undefined ? current : tlPick || null;
    const back = comments.filter((c) => c.kind === "return");
    const last = back[back.length - 1];
    return (
      <div className="tl">
        <style>{LC_TIMELINE_CSS}</style>
        {last ? (
          <p className="tl-back"><Undo2 aria-hidden />Sent back {back.length === 1 ? "once" : `${back.length} times`} · {fmtRelative(last.at)}{last.actor_name ? ` by ${last.actor_name}` : ""}</p>
        ) : null}
        <ol className="tl-list" aria-label="Lifecycle stages">
          {LIFECYCLE.map((s, i) => {
            const st = stateOf(s.key);
            const rs = rowsOf(s.key);
            const isOpen = tlOpen === s.key;
            const attn = needsAttention(rs, st);
            return (
              <li key={s.key} className="tl-item" data-state={st} data-open={isOpen ? "1" : undefined} data-attn={attn ? "1" : undefined} style={{ "--i": i } as React.CSSProperties}>
                <span className="tl-dot" aria-hidden>{st === "done" ? <Check strokeWidth={3} /> : null}</span>
                <div className="tl-card">
                <button type="button" className="tl-head" aria-expanded={isOpen} aria-label={stageLabel(s.label, st, attn)}
                  onClick={() => setTlPick(isOpen ? "" : s.key)}>
                  <span className="tl-tx">
                    <b>{s.label}</b>
                    <small>{st === "done" ? "Completed" : st === "current" ? "In progress" : "Pending"}{rs.length ? ` · ${rs.filter((r) => r.tone === "done" || r.tone === "skipped").length} of ${rs.length} done` : ""}</small>
                  </span>
                  <ChevronDown className="tl-chev" aria-hidden />
                </button>
                {isOpen ? (
                  rs.length === 0 ? <p className="tl-none">Nothing happens in {s.label} for this contract.</p> : (
                    <ul className="tl-steps">
                      {rs.map((r, ri) => {
                        const go = r.idx != null && !!onSelectStep;
                        return (
                          <li key={r.key} data-tone={r.tone} data-ai={r.kind === "AI" ? "1" : undefined} data-sel={r.idx != null && selectedStepIdx === r.idx ? "1" : undefined} style={{ "--k": ri } as React.CSSProperties}>
                            <button type="button" disabled={!go} onClick={() => r.idx != null && onSelectStep?.(r.idx)} aria-label={`${r.name}: ${r.label}`} className="tl-step">
                              <span className="tl-ic" aria-hidden>
                                {r.tone === "done" ? <Check /> : r.tone === "failed" ? <X /> : r.tone === "skipped" ? <Minus /> : r.kind === "AI" ? <AiMark /> : r.tone === "live" ? <Clock /> : <Circle />}
                              </span>
                              <span className="tl-st-tx">
                                <b title={r.name}>{r.name}</b>
                                <small>{r.kind === "AI" ? "AI step" : r.kind} · {r.label}{r.who ? ` · ${r.who}` : ""}</small>
                                {r.detail ? <em className="tl-note" title={r.detail}>{r.detail}</em> : null}
                              </span>
                            </button>
                            {r.action && onAction ? (
                              <button type="button" disabled={busy === r.key} onClick={() => void press(r, s.key)} className="tl-act">
                                {busy === r.key ? "Starting…" : <>{r.action.label}<ArrowRight /></>}
                              </button>
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                  )
                ) : null}
                {problem && problem.stage === s.key && isOpen ? <p role="alert" className="tl-err">{problem.text}</p> : null}
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    );
  }

  return (
    <div>
      <style>{LC_TABS_CSS}</style>
      {compact && strip ? compactRow : null}
      <div className={compact && strip ? undefined : `lct-wrap${row ? " row" : ""}`}>
      {compact && strip ? null : (
      <ol className="lct-strip" role="tablist" aria-label="Lifecycle stages" onKeyDown={(e) => {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        const btns = Array.from(e.currentTarget.querySelectorAll("button"));
        const at = btns.indexOf(document.activeElement as HTMLButtonElement);
        if (at < 0) return;
        e.preventDefault();
        btns[Math.max(0, Math.min(btns.length - 1, at + (e.key === "ArrowRight" ? 1 : -1)))]?.focus();
      }}>
        {LIFECYCLE.map((s, i) => {
          const st = stateOf(s.key);
          const isOpen = open === s.key;
          const attn = needsAttention(rowsOf(s.key), st);
          return (
            <li key={s.key} className="lct" data-state={st} data-open={isOpen ? "1" : undefined} data-attn={attn ? "1" : undefined} style={{ "--i": i } as React.CSSProperties}>
              <button type="button" aria-pressed={isOpen} onClick={() => setPicked(compact && (strip || row) && isOpen ? null : s.key)}
                aria-label={stageLabel(s.label, st, attn)} title={row ? caption(rowsOf(s.key), st) : undefined} className="lct-btn">
                <span className="lct-n">{st === "done" ? <Check /> : st === "current" && !row ? <Clock /> : i + 1}</span>
                <span className="lct-tx">
                  <b>{row ? <span className="lct-no">{i + 1}</span> : null}{s.label}</b>
                  {row ? <small className="lct-st">{st === "done" ? "Completed" : st === "current" ? "In progress" : "Pending"}</small> : null}
                  <small>{caption(rowsOf(s.key), st)}</small>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      )}
      <div className="lct-main">

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
        <div key={sel.key} className={cn("lc-panel mt-2 overflow-hidden rounded-xl border border-slate-200 bg-slate-100", compact && "max-h-80 overflow-y-auto")}>
          <div className={cn("flex items-center gap-x-3 border-b border-slate-200 px-3 py-2",
            stateOf(sel.key) === "done" ? "bg-success-subtle/60" : stateOf(sel.key) === "current" ? "bg-brand-50" : "bg-slate-50")}>
            <span className={cn("grid h-6 w-6 shrink-0 place-items-center rounded-md text-[12px] font-bold text-white",
              stateOf(sel.key) === "done" ? "bg-success" : stateOf(sel.key) === "current" ? "bg-brand-600" : "bg-slate-400")}>{RANK[sel.key] + 1}</span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[13.5px] font-bold tracking-[-0.01em] text-slate-900">{sel.label}</span>
                <span className={cn("rounded-full px-2.5 py-0.5 text-[11px] font-bold",
                  stateOf(sel.key) === "done" ? "bg-success text-white"
                    : stateOf(sel.key) === "current" ? "bg-brand-600 text-white" : "bg-slate-200 text-slate-600")}>
                  {stateOf(sel.key) === "done" ? "Done" : stateOf(sel.key) === "current" ? "Now" : "Not reached yet"}
                </span>
              </div>
              <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-slate-500">Stage {RANK[sel.key] + 1} of 7</span>
            </div>
            {(() => {
              const rs = rowsOf(sel.key);
              if (!rs.length) return null;
              const n = rs.filter((r) => r.tone === "done" || r.tone === "skipped").length;
              return (
                <div className="lc-prog ml-auto hidden items-center gap-2 sm:flex" title={`${n} of ${rs.length} steps finished`}>
                  <span className="lc-bar"><i style={{ width: `${(n / rs.length) * 100}%` }} /></span>
                  <span className="text-[11.5px] font-semibold tabular-nums text-slate-600">{n}/{rs.length} steps</span>
                </div>
              );
            })()}
            {compact && (strip || row) && <button type="button" aria-label="Close stage details" onClick={() => setPicked(null)} className="ml-auto grid h-6 w-6 place-items-center rounded-md text-slate-500 hover:bg-slate-200"><X className="h-3.5 w-3.5" /></button>}
          </div>
          <style>{LC_PANEL_CSS}</style>
          <p className="line-clamp-1 px-3 pt-2 text-[12px] leading-relaxed text-slate-600" title={sel.about}>{sel.about}</p>
          {rowsOf(sel.key).length === 0 ? (
            <p className="px-4 pb-4 pt-2.5 text-[12.5px] text-slate-400">Nothing happens in {sel.label} for this contract.</p>
          ) : (
            <ul className="lcr-list">
              {rowsOf(sel.key).map((r, ri) => {
                const ini = r.who.split(/[\s·()]+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
                const go = r.idx != null && !!onSelectStep;
                const ai = r.kind === "AI";
                return (
                  <li key={r.key} data-tone={r.tone} data-ai={ai ? "1" : undefined} data-sel={r.idx != null && selectedStepIdx === r.idx ? "1" : undefined} style={{ "--r": ri } as React.CSSProperties} className="lcr">
                    <button type="button" disabled={!go} onClick={() => r.idx != null && onSelectStep?.(r.idx)} className="lcr-main" aria-label={`${r.name}: ${r.label}`}>
                      <span className="lcr-top">
                        <span className="lcr-node" aria-hidden>
                          {r.tone === "failed" ? <X /> : r.tone === "skipped" ? <Minus /> : ai ? <span className="lcr-orb"><i className="halo" /><i className="orbit" /><AiMark className="spark" />{r.tone === "done" ? <b className="chk"><Check /></b> : null}</span> : r.tone === "done" ? <Check /> : r.tone === "live" ? <Clock /> : <Circle />}
                        </span>
                        <span className="lcr-no">Step {ri + 1}</span>
                        <span className="lcr-state"><b>{ai && r.tone === "live" ? "Thinking" : r.label}{ai && r.tone === "live" ? <span className="lcr-dots" aria-hidden><i /><i /><i /></span> : null}</b></span>
                      </span>
                      <span className="lcr-title"><b title={r.name}>{r.name}</b></span>
                      <span className="lcr-meta"><em>{ai ? <AiMark /> : null}{ai ? "AI step" : r.kind}</em>{r.detail ? <span title={r.detail}>{r.detail}</span> : null}</span>
                      {ai && r.tone === "live" ? <span className="lcr-bar" aria-hidden><i /></span> : null}
                      <span className="lcr-foot">
                        <span className="lcr-who" title={r.who}>{ai ? <i className="lcr-bot"><AiMark /></i> : ini ? <i>{ini}</i> : null}<span>{r.who}</span></span>
                        {r.when ? <small>{fmtRelative(r.when)}</small> : null}
                        {go ? <ChevronRight className="lcr-go" aria-hidden /> : null}
                      </span>
                    </button>
                    {r.action && onAction ? (
                      <button type="button" disabled={busy === r.key} onClick={() => void press(r, sel.key)} className="lcr-act lc-act">
                        {busy === r.key ? "Starting…" : <>{r.action.label}<ArrowRight /></>}
                      </button>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
          {problem && problem.stage === sel.key ? <p role="alert" className="mt-2 text-[12px] text-danger">{problem.text}</p> : null}
        </div>
      )}
      </div>
      </div>
    </div>
  );
}

export interface StepFacts {
  who: string | null;
  members: string[];
  authorityNote: string | null;
  due: Date | null;
  overdue: boolean;
}

function stepTeam(step: WorkflowRunStep, teams?: IntakeTeam[]) {
  return (teams ?? []).find((t) => t.id === step.team_id) ?? (teams ?? []).find((t) => t.name === (step.team_label ?? step.planned_team_label));
}

/** Who an open step waits on and by when. NextStepCard and the request page's workflow tiles both
 *  read this, so the "who / due / overdue / team" rules cannot drift between the two screens. */
export function stepFacts(step: WorkflowRunStep, teams?: IntakeTeam[], now: number = Date.now()): StepFacts {
  const team = stepTeam(step, teams);
  // An approval step reports the live approval itself (who holds it now, and its own due date), which is
  // what Delegate and Escalate change; the step's team and "started + SLA hours" are only the plan.
  const live = step.approval ?? null;
  const sla = Number(step.sla_hours);
  const slaDue = sla > 0 && step.updated_at ? new Date(new Date(step.updated_at).getTime() + sla * 3_600_000) : null;
  const due = live ? (live.due_at ? new Date(live.due_at) : null) : slaDue;
  const overdue = live ? live.overdue : due != null && due.getTime() < now;
  const noOwner = !live && !step.assignee_label;
  const members = noOwner && team ? (team.members ?? []).filter((m) => m.active).map((m) => m.name ?? m.user_id) : [];
  const text = live ? live.approver_label
    : step.assignee_label ? `${step.assignee_label}${team ? ` (${team.name})` : ""}`
    : team ? `${team.name} team`
    : step.type === "signature" ? "the signers"
    : who(step) !== NOBODY ? who(step) : null;
  return { who: text, members, authorityNote: step.authority_note ?? null, due, overdue };
}

/** The step the work is waiting on, who can do it, by when, and what comes after. */
export function NextStepCard({ run, teams }: { run: WorkflowRun; teams?: IntakeTeam[] }) {
  const steps = run.steps ?? [];
  if (run.status === "complete") {
    return <div className="text-[13px] text-slate-600"><b className="text-slate-900">Workflow complete.</b> Every step is done.</div>;
  }
  const cur = steps.find((s) => OPEN.has(s.status)) ?? steps[run.current_index];
  if (!cur) return null;
  const team = stepTeam(cur, teams);
  const live = cur.approval ?? null;
  const { members, due, overdue } = stepFacts(cur, teams);
  let end = run.current_index + 1;
  while (end < steps.length && steps[end].parallel) end++;
  const then = steps[end];
  const stageName = (s: WorkflowRunStep) => LIFECYCLE.find((x) => x.key === stepStage(s))?.label;
  return (
    <div className="space-y-1.5 text-[13px] leading-relaxed text-slate-600">
      <div className="text-[14px] font-semibold text-slate-900">{cur.name} <span className="font-normal text-slate-500">· {stageName(cur)}</span></div>
      <div>
        {live ? <>With <b className="text-slate-800">{live.approver_label}</b>. </> : null}
        {!live && cur.assignee_label ? <>With <b className="text-slate-800">{cur.assignee_label}</b>{team ? ` (${team.name})` : ""}. </> : null}
        {!live && !cur.assignee_label && team ? <><b className="text-slate-800">{team.name}</b> team — {members.length ? members.join(", ") : "no members yet"}. </> : null}
        {!live && !cur.assignee_label && !team ? (
          cur.type === "signature" ? <>Waiting for the signers. </>
            : who(cur) !== NOBODY ? <>{who(cur)}. </> : null
        ) : null}
      </div>
      {cur.authority_note ? <div>{cur.authority_note}.</div> : null}
      {due ? <div className={overdue ? "font-semibold text-danger" : ""}>{overdue ? "Overdue since" : "Due"} {due.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}.</div> : null}
      <div className="text-slate-500">{then ? <>Then: <b className="text-slate-700">{then.name}</b> ({stageName(then)}).</> : "Then the workflow is complete."}</div>
    </div>
  );
}

/** The slim stage row used by `strip` (the contract page): a dot and a name per stage, the
 *  current stage as a filled pill, done stages with a tick. Needs the `.clm` colour variables. */
export const LIFECYCLE_STRIP_CSS = `
.clm .lcs-row{display:flex;align-items:center;justify-content:space-between;gap:14px}
.clm .lcs-strip{display:flex;align-items:center;list-style:none;margin:0;padding:0;min-width:0;overflow-x:auto;scrollbar-width:none;flex:1}
.clm .lcs-strip::-webkit-scrollbar{display:none}
.clm .lcs-st{display:flex;align-items:center;flex:none}
.clm .lcs-st+.lcs-st::before{content:"";width:16px;height:2px;border-radius:2px;background:var(--border);margin:0 3px;transition:background .3s}
.clm .lcs-st.done+.lcs-st::before{background:var(--good)}
.clm .lcs-st button{display:inline-flex;align-items:center;gap:7px;padding:4px 11px 4px 5px;border-radius:99px;color:var(--ink-3);font-size:12.5px;font-weight:500;white-space:nowrap;transition:background .2s,color .2s}
.clm .lcs-st button:hover{background:var(--surface-2)}
.clm .lcs-dot{width:18px;height:18px;border-radius:50%;border:1.5px solid var(--border-strong);display:grid;place-items:center;color:#fff;flex:none;transition:background .3s,border-color .3s}
.clm .lcs-st.done .lcs-dot{background:var(--good);border-color:var(--good)}
.clm .lcs-st.done button{color:var(--ink)}
.clm .lcs-st.current button{background:var(--accent);color:var(--accent-ink);font-weight:600}
.clm .lcs-st.current button:hover{background:var(--accent)}
.clm .lcs-st.current .lcs-dot{border-color:transparent;background:var(--accent-ink)}
.clm .lcs-st.current .lcs-dot::after{content:"";width:6px;height:6px;border-radius:50%;background:var(--accent)}
.clm .lcs-st button[aria-pressed="true"]{box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 35%,transparent)}
.clm .lcs-st.attn .lcs-dot{border-color:var(--crit)}
.clm .lcs-st.attn:not(.current) .lcs-name{color:var(--crit);font-weight:600}
.clm .lcs-st.attn .lcs-name::after{content:"!";display:inline-grid;place-items:center;width:14px;height:14px;margin-left:6px;border-radius:50%;background:var(--crit);color:#fff;font-size:10px;font-weight:700;line-height:1}
.clm .lc-act{border:1px solid var(--border-strong);background:var(--surface-2);color:var(--ink);font-size:12px;font-weight:600;border-radius:6px;padding:4px 10px;white-space:nowrap}
.clm .lc-act:hover{background:var(--surface-3,var(--surface-2));border-color:var(--ink-3)}
.clm .lc-act:disabled{opacity:.6;cursor:default}
.clm .lcs-trail{display:flex;align-items:center;gap:8px;flex:none}
@media (max-width:640px){.clm .lcs-st:not(.current) .lcs-name{display:none}.clm .lcs-st+.lcs-st::before{width:6px;margin:0 1px}.clm .lcs-st button{padding:3px}.clm .lcs-st.current button{padding:3px 11px 3px 4px}}
`;

/** The seven stages as an ordered tab bar: numbered tabs in lifecycle order, a status bar under each, the open tab lifted. */
/** The vertical timeline (`timeline`): the side column of the request page. */
const LC_TIMELINE_CSS = `
.tl-list{list-style:none;margin:0;padding:0;position:relative}
.tl-item{position:relative;padding:0 0 10px 34px;animation:lct-in .4s cubic-bezier(.2,.9,.25,1) backwards;animation-delay:calc(var(--i) * 60ms)}
.tl-item:last-child{padding-bottom:2px}
.tl-item::before{content:"";position:absolute;left:11px;top:30px;bottom:-4px;width:2px;border-radius:2px;background:repeating-linear-gradient(to bottom,var(--border-strong,#c3cad6) 0 4px,transparent 4px 8px)}
.tl-item:last-child::before{display:none}
.tl-item[data-state=done]::before{background:var(--good,#16a34a)}
.tl-item[data-state=current]::before{background:repeating-linear-gradient(to bottom,var(--accent,#4f46e5) 0 4px,transparent 4px 8px);background-size:2px 8px;animation:tl-march .9s linear infinite}
.tl-dot{position:absolute;left:1px;top:12px;width:22px;height:22px;border-radius:50%;box-sizing:border-box;display:grid;place-items:center;background:var(--surface,#fff);border:2px solid var(--border-strong,#b8c0cc);transition:transform .2s}
.tl-dot svg{width:12px;height:12px;color:#fff}
.tl-item[data-state=done] .tl-dot{background:var(--good,#16a34a);border-color:var(--good,#16a34a)}
.tl-item[data-state=current] .tl-dot{border-color:var(--accent,#4f46e5);background:var(--accent,#4f46e5)}
.tl-item[data-state=current] .tl-dot::after{content:"";position:absolute;inset:-5px;border-radius:50%;border:2px solid var(--accent,#4f46e5);opacity:0;animation:tl-ping 1.8s ease-out infinite}
.tl-item[data-attn="1"] .tl-dot{border-color:var(--crit,#dc2626)}
.tl-item:hover .tl-dot{transform:scale(1.1)}
.tl-card{border:1px solid var(--border,#dfe3ea);border-radius:14px;background:var(--surface,#fff);overflow:hidden;transition:box-shadow .2s,border-color .2s,transform .2s}
.tl-card:hover{transform:translateY(-1px);box-shadow:0 6px 16px -8px rgba(20,26,40,.22)}
.tl-item[data-state=done] .tl-card{background:color-mix(in srgb,var(--good,#16a34a) 8%,var(--surface,#fff));border-color:color-mix(in srgb,var(--good,#16a34a) 30%,var(--border,#dfe3ea))}
.tl-item[data-state=current] .tl-card{background:color-mix(in srgb,var(--accent,#4f46e5) 7%,var(--surface,#fff));border-color:color-mix(in srgb,var(--accent,#4f46e5) 45%,var(--border,#dfe3ea));box-shadow:0 0 0 1px color-mix(in srgb,var(--accent,#4f46e5) 25%,transparent)}
.tl-item[data-state=upcoming] .tl-card{background:var(--surface-2,#f4f6f9)}
.tl-head{display:flex;align-items:center;gap:8px;width:100%;padding:10px 12px;border:0;background:transparent;text-align:left;cursor:pointer}
.tl-head:focus-visible{outline:2px solid var(--accent,#4f46e5);outline-offset:-2px;border-radius:14px}
.tl-tx{flex:1;min-width:0;display:flex;flex-direction:column;gap:1px}
.tl-tx b{font-size:13.5px;font-weight:700;color:var(--ink,#0f172a);letter-spacing:-.01em}
.tl-tx small{font-size:11.5px;color:var(--ink-3,#64748b)}
.tl-item[data-state=done] .tl-tx small{color:var(--good,#16a34a)}
.tl-item[data-state=current] .tl-tx small{color:var(--accent,#4f46e5);font-weight:600}
.tl-item[data-state=upcoming] .tl-tx b{color:var(--ink-3,#64748b)}
.tl-chev{flex:none;width:16px;height:16px;color:var(--ink-3,#64748b);transition:transform .25s cubic-bezier(.2,.9,.25,1),color .15s}
.tl-item[data-open="1"] .tl-chev{transform:rotate(180deg);color:var(--accent,#4f46e5)}
.tl-steps{list-style:none;margin:0;padding:0 8px 8px;display:flex;flex-direction:column;gap:2px;border-top:1px solid color-mix(in srgb,var(--ink,#0f172a) 7%,transparent);padding-top:6px;transform-origin:top;animation:tl-open .28s cubic-bezier(.2,.9,.25,1) backwards}
.tl-steps>li{display:flex;align-items:center;gap:4px;animation:tl-step .3s cubic-bezier(.2,.9,.25,1) backwards;animation-delay:calc(var(--k) * 45ms + 60ms)}
.tl-step{flex:1;min-width:0;display:flex;align-items:flex-start;gap:9px;padding:7px 8px;border:0;border-radius:10px;background:transparent;text-align:left;cursor:pointer;transition:background .15s}
.tl-step:disabled{cursor:default}.tl-step:not(:disabled):hover{background:color-mix(in srgb,var(--surface,#fff) 70%,transparent)}
.tl-steps>li[data-sel="1"] .tl-step{background:var(--surface,#fff);box-shadow:inset 3px 0 0 var(--accent,#4f46e5)}
.tl-ic{flex:none;display:grid;place-items:center;width:26px;height:26px;border-radius:8px;background:var(--surface,#fff);color:var(--ink-3,#64748b);box-shadow:0 0 0 1px var(--border,#dfe3ea)}
.tl-ic svg{width:14px;height:14px}
[data-tone=done]>.tl-step .tl-ic{background:color-mix(in srgb,var(--good,#16a34a) 16%,var(--surface,#fff));color:var(--good,#16a34a);box-shadow:none}
[data-tone=live]>.tl-step .tl-ic{background:color-mix(in srgb,var(--warn,#d97706) 18%,var(--surface,#fff));color:var(--warn,#d97706);box-shadow:none;animation:lcr-breathe 1.8s ease-in-out infinite}
[data-tone=failed]>.tl-step .tl-ic{background:color-mix(in srgb,var(--crit,#dc2626) 16%,var(--surface,#fff));color:var(--crit,#dc2626);box-shadow:none}
[data-ai="1"]>.tl-step .tl-ic{color:var(--accent,#4f46e5)}
.tl-st-tx{min-width:0;display:flex;flex-direction:column}
.tl-st-tx b{font-size:12.5px;font-weight:600;color:var(--ink,#0f172a);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tl-st-tx small{font-size:11px;color:var(--ink-3,#64748b);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tl-note{font-style:normal;font-size:11px;line-height:1.35;color:var(--ink-2,#475569);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;margin-top:1px}
[data-tone=skipped]>.tl-step .tl-st-tx b{color:var(--ink-3,#64748b)}
.tl-act{flex:none;display:inline-flex;align-items:center;gap:4px;padding:4px 9px;border:1px solid var(--accent,#4f46e5);border-radius:8px;background:transparent;color:var(--accent,#4f46e5);font-size:11.5px;font-weight:600;cursor:pointer}
.tl-act svg{width:12px;height:12px}.tl-act:hover:not(:disabled){background:var(--accent,#4f46e5);color:#fff}
.tl-none,.tl-err{margin:0;padding:8px 14px 10px;font-size:11.5px;color:var(--ink-3,#64748b);border-top:1px solid color-mix(in srgb,var(--ink,#0f172a) 7%,transparent)}.tl-err{color:var(--crit,#dc2626)}
.tl-back{display:flex;gap:6px;align-items:flex-start;margin:0 0 8px;font-size:11.5px;color:var(--warn,#d97706)}.tl-back svg{width:13px;height:13px;flex:none;margin-top:1px}
@keyframes tl-march{to{background-position:0 8px}}
@keyframes tl-ping{0%{transform:scale(.7);opacity:.7}100%{transform:scale(1.5);opacity:0}}
@keyframes tl-open{from{opacity:0;transform:scaleY(.92) translateY(-6px)}to{opacity:1;transform:none}}
@keyframes tl-step{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:none}}
/* The app's reduced-motion rule caps every animation at 0.2s, once. The status motion here is what tells a person
   something is happening, so it keeps running, gently; the entrance animations simply play. */
@media (prefers-reduced-motion:reduce){
.tl-item,.tl-steps,.tl-steps>li{animation-duration:.3s!important}
.tl-item[data-state=current]::before{animation-duration:1.4s!important;animation-iteration-count:infinite!important}
.tl-item[data-state=current] .tl-dot::after{animation-duration:2.4s!important;animation-iteration-count:infinite!important}
[data-tone=live]>.tl-step .tl-ic{animation-duration:2.4s!important;animation-iteration-count:infinite!important}
}
`;

const LC_TABS_CSS = `
.lct-wrap{display:grid;grid-template-columns:176px minmax(0,1fr);gap:14px;align-items:start}
.lct-main{min-width:0}
.lct-strip{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;position:relative}
.lct-strip::before{content:"";position:absolute;left:13px;top:18px;bottom:18px;width:2px;border-radius:2px;background:var(--border,#dfe3ea)}
.lct{position:relative;animation:lct-in .35s cubic-bezier(.2,.9,.25,1) backwards;animation-delay:calc(var(--i) * 40ms)}
@keyframes lct-in{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:none}}
.lct-btn{position:relative;display:flex;align-items:center;gap:9px;width:100%;padding:5px 8px 5px 0;border-radius:9px;text-align:left;transition:background .15s}
.lct-btn:hover{background:var(--surface-2,#eef1f5)}
.lct[data-open="1"] .lct-btn{background:var(--surface-2,#eef1f5)}
.lct-n{position:relative;z-index:1;flex:none;display:grid;place-items:center;width:28px;height:28px;border-radius:50%;font:700 11.5px var(--sans,inherit);background:var(--surface,#fff);color:var(--ink-3,#64748b);box-shadow:inset 0 0 0 1.5px var(--border-strong,#b8c0cc)}
.lct-n svg{width:13px;height:13px;stroke-width:2.8}
.lct[data-state=done] .lct-n{background:var(--good,#16a34a);color:#fff;box-shadow:none}
.lct[data-state=current] .lct-n{background:var(--accent,#4f46e5);color:var(--accent-ink,#fff);box-shadow:0 0 0 4px color-mix(in srgb,var(--accent,#4f46e5) 18%,transparent)}
.lct[data-state=current] .lct-n svg{animation:lct-tick 2.4s ease-in-out infinite}
@keyframes lct-tick{50%{transform:rotate(25deg) scale(1.1)}}
.lct[data-state=done]:not(:last-child)::before{content:"";position:absolute;left:13px;top:22px;bottom:-14px;width:2px;background:var(--good,#16a34a);z-index:0;transform-origin:top;animation:lct-line .5s ease-out backwards;animation-delay:calc(var(--i) * 60ms)}
@keyframes lct-line{from{transform:scaleY(0)}to{transform:none}}
.lct-tx{min-width:0;display:flex;flex-direction:column;line-height:1.25}
.lct-tx b{font-size:12.5px;font-weight:650;color:var(--ink,#0f172a)}
.lct[data-state=upcoming] .lct-tx b{color:var(--ink-3,#64748b);font-weight:600}
.lct-tx small{font-size:10.5px;color:var(--ink-3,#64748b);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lct[data-attn="1"] .lct-tx small{color:var(--crit,#dc2626);font-weight:600}
.lct-wrap.row{display:block}
.lct-wrap.row .lct-strip{flex-direction:row;gap:2px;padding:0;overflow-x:auto;scrollbar-width:thin;border-bottom:1px solid var(--border,#dfe3ea)}
.lct-wrap.row .lct-strip::before,.lct-wrap.row .lct[data-state=done]:not(:last-child)::before{display:none}
.lct-wrap.row .lct{flex:1 0 auto;min-width:0}
.lct-wrap.row .lct-btn{flex-direction:row;align-items:center;justify-content:flex-start;gap:10px;padding:10px 18px 9px;height:100%;cursor:pointer;border:0;border-bottom:3px solid transparent;border-radius:10px 10px 0 0;background:transparent;white-space:nowrap;transition:background .15s,border-color .15s}
.lct-wrap.row .lct-btn:hover{background:var(--surface-2,#eef1f5);border-bottom-color:var(--border-strong,#b8c0cc)}
.lct-wrap.row .lct-btn:focus-visible{outline:2px solid var(--accent,#4f46e5);outline-offset:-2px}
.lct-wrap.row .lct[data-open="1"] .lct-btn{background:color-mix(in srgb,var(--accent,#4f46e5) 8%,var(--surface,#fff));border-bottom-color:var(--accent,#4f46e5)}
.lct-wrap.row .lct[data-open="1"] .lct-tx b{color:var(--accent,#4f46e5)}
.lct-wrap.row .lct-tx small:not(.lct-st){display:none}
.lct-wrap.row .lct-n{width:28px;height:28px;flex:none}
.lct-wrap.row .lct-tx b{font-size:13px}
.lct-wrap.row .lct-tx small{white-space:normal;font-size:11.5px;line-height:1.35}
.lct-wrap.row .lct-no{display:none}
.lct-wrap.row .lct-st{font-weight:600;color:var(--ink-2)}
.lct-wrap.row .lct[data-state=done] .lct-st{color:var(--good,#16a34a)}
.lct-wrap.row .lct[data-state=current] .lct-st{color:var(--accent,#4f46e5)}
.lct-wrap.row .lct[data-state=current] .lct-n{background:var(--surface,#fff);color:var(--accent,#4f46e5);box-shadow:0 0 0 2px var(--accent,#4f46e5),0 0 0 6px color-mix(in srgb,var(--accent,#4f46e5) 14%,transparent)}
.lct-wrap.row .lct-main:empty{display:none}
.lct-wrap.row .lct-main .lc-panel{margin-top:6px}
@media (max-width:760px){.lct-wrap{grid-template-columns:1fr}.lct-strip{flex-direction:row;overflow-x:auto}.lct-strip::before,.lct[data-state=done]::before{display:none}.lct-tx small{display:none}}
@media (prefers-reduced-motion:reduce){.lct,.lct::before,.lct-n svg{animation:none!important}}
`;

/** Look of the stage panel's step rows: a connected stepper, a person chip, a status pill; the open step is tinted and pulses. */
const LC_PANEL_CSS = `
.lc-panel{animation:lcp-open .4s cubic-bezier(.2,.9,.25,1) backwards}
@keyframes lcp-open{from{opacity:0;transform:translateY(-10px) scale(.985)}to{opacity:1;transform:none}}
.lcr-list{list-style:none;margin:0;padding:6px 14px 16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.lcr{--ai:var(--accent,#4f46e5);display:flex;flex-direction:column;gap:8px;min-width:0;animation:lcr-in .45s cubic-bezier(.2,.9,.25,1) backwards;animation-delay:calc(var(--r) * 80ms + 100ms)}
@keyframes lcr-in{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
.lcr-main{position:relative;flex:1;display:flex;flex-direction:column;gap:8px;min-width:0;padding:14px;text-align:left;border:0;border-radius:16px;overflow:hidden;background:var(--surface,#fff);box-shadow:0 1px 2px rgba(20,26,40,.06),0 6px 16px -8px rgba(20,26,40,.14);transition:transform .22s cubic-bezier(.2,.9,.25,1),box-shadow .22s}
.lcr-main:not(:disabled):hover{transform:translateY(-4px);box-shadow:0 2px 4px rgba(20,26,40,.06),0 16px 28px -10px rgba(20,26,40,.22)}
.lcr-main:disabled{cursor:default}
.lcr[data-sel="1"] .lcr-main{box-shadow:0 0 0 1px var(--border-strong,#b8c0cc)}
.lcr[data-tone=pending] .lcr-main,.lcr[data-tone=skipped] .lcr-main{background:color-mix(in srgb,var(--surface,#fff) 55%,transparent);box-shadow:inset 0 0 0 1.5px var(--border,#dfe3ea)}
.lcr[data-tone=pending] .lcr-title,.lcr[data-tone=skipped] .lcr-title,.lcr[data-tone=pending] .lcr-meta,.lcr[data-tone=skipped] .lcr-meta{opacity:.6}
.lcr-top{display:flex;align-items:center;gap:10px}
.lcr-node{flex:none;display:grid;place-items:center}
.lcr-node>svg,.lcr-node>i{display:block;width:34px;height:34px;padding:8px;border-radius:11px;box-sizing:border-box;background:var(--surface-2,#eef1f5);color:var(--ink-3,#64748b)}
.lcr[data-tone=done] .lcr-node>svg{background:color-mix(in srgb,var(--good,#16a34a) 16%,transparent);color:var(--good,#16a34a)}
.lcr[data-tone=live] .lcr-node>svg{background:color-mix(in srgb,var(--warn,#d97706) 18%,transparent);color:var(--warn,#d97706);animation:lcr-breathe 1.8s ease-in-out infinite}
.lcr[data-tone=failed] .lcr-node>svg{background:color-mix(in srgb,var(--crit,#dc2626) 16%,transparent);color:var(--crit,#dc2626)}
@keyframes lcr-breathe{0%,100%{transform:scale(1)}50%{transform:scale(1.1)}}
.lcr-no{font:600 11px var(--sans,inherit);color:var(--ink-3,#64748b);letter-spacing:.02em}
.lcr-state{margin-left:auto}
.lcr-state b{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:650;color:var(--ink-3,#64748b)}
.lcr-state b::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
.lcr[data-tone=done] .lcr-state b{color:var(--good,#16a34a)}
.lcr[data-tone=live] .lcr-state b{color:var(--warn,#d97706)}
.lcr[data-tone=live] .lcr-state b::before{animation:lcr-blink 1.2s ease-in-out infinite}
.lcr[data-tone=failed] .lcr-state b{color:var(--crit,#dc2626)}
@keyframes lcr-blink{50%{opacity:.25}}
.lcr-title b{display:block;font-size:15px;font-weight:700;letter-spacing:-.01em;line-height:1.3;color:var(--ink,#0f172a);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lcr-meta{display:flex;align-items:center;gap:7px;min-width:0;font-size:12px;color:var(--ink-3,#64748b)}
.lcr-meta em{flex:none;display:inline-flex;align-items:center;gap:4px;font-style:normal;font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--accent,#4f46e5)}
.lcr-meta em svg{width:11px;height:11px}
.lcr-meta span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lcr-foot{display:flex;align-items:center;gap:8px;margin-top:auto;padding-top:6px;font-size:12px;color:var(--ink-2,#334155)}
.lcr-who{display:flex;align-items:center;gap:8px;min-width:0;flex:1;font-weight:550}
.lcr-who i{flex:none;width:24px;height:24px;border-radius:50%;display:grid;place-items:center;font:700 10px/1 var(--sans,inherit);font-style:normal;background:color-mix(in srgb,var(--accent,#4f46e5) 14%,transparent);color:var(--accent,#4f46e5)}
.lcr-who span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lcr-foot small{flex:none;font-size:11px;color:var(--ink-3,#64748b)}
.lcr-go{flex:none;width:16px;height:16px;color:var(--ink-3,#64748b);opacity:0;transform:translateX(-6px);transition:opacity .2s,transform .2s}
.lcr-main:hover .lcr-go{opacity:1;transform:none;color:var(--accent,#4f46e5)}
.lcr-act{display:inline-flex;align-items:center;justify-content:center;gap:6px;width:100%;padding:9px 12px;border-radius:12px;border:0;background:var(--accent,#4f46e5);color:var(--accent-ink,#fff);font-size:12.5px;font-weight:650;box-shadow:0 6px 14px -6px color-mix(in srgb,var(--accent,#4f46e5) 70%,transparent);transition:transform .2s,filter .2s}
.lcr-act:hover{filter:brightness(1.1);transform:translateY(-1px)}.lcr-act svg{width:14px;height:14px;transition:transform .2s}.lcr-act:hover svg{transform:translateX(3px)}
/* AI step: violet glass tile, glowing sparkle, indeterminate progress while it works */
.lcr[data-ai="1"] .lcr-main{background:color-mix(in srgb,var(--ai) 8%,var(--surface,#fff));box-shadow:0 1px 2px rgba(20,26,40,.05),0 10px 24px -10px color-mix(in srgb,var(--ai) 55%,transparent)}
.lcr[data-ai="1"][data-tone=pending] .lcr-main{background:transparent;box-shadow:0 0 0 1px var(--border,#dfe3ea)}
.lcr[data-ai="1"] .lcr-meta em{color:var(--ai)}
.lcr[data-ai="1"] .lcr-who i.lcr-bot{background:var(--ai);color:#fff}.lcr-bot svg{width:12px;height:12px}
.lcr-orb{position:relative;display:grid;place-items:center;width:34px;height:34px;border-radius:11px;background:var(--ai);color:#fff}
.lcr-orb .spark{position:relative;z-index:1;width:18px;height:18px}
.lcr-orb .halo{position:absolute;inset:0;border-radius:11px;background:var(--ai);opacity:.0}
.lcr[data-ai="1"][data-tone=pending] .lcr-orb{background:color-mix(in srgb,var(--ai) 14%,transparent);color:var(--ai)}
.lcr-orb .chk{position:absolute;right:-5px;bottom:-5px;width:16px;height:16px;border-radius:50%;display:grid;place-items:center;background:var(--good,#16a34a);color:#fff;box-shadow:0 0 0 2.5px var(--surface,#fff);animation:lcr-pop .45s cubic-bezier(.3,1.6,.5,1) backwards .35s}
.lcr-orb .chk svg{width:9px;height:9px;stroke-width:4}
@keyframes lcr-pop{from{transform:scale(0)}to{transform:none}}
.lcr[data-ai="1"][data-tone=live] .lcr-main{box-shadow:0 0 0 2px color-mix(in srgb,var(--ai) 45%,transparent),0 14px 30px -10px color-mix(in srgb,var(--ai) 65%,transparent)}
.lcr[data-ai="1"][data-tone=live] .lcr-orb .halo{animation:lcr-halo 1.7s ease-out infinite}
@keyframes lcr-halo{0%{opacity:.55;transform:scale(1)}100%{opacity:0;transform:scale(1.9)}}
@keyframes lcr-twinkle{0%,100%{transform:scale(1) rotate(0)}50%{transform:scale(1.2) rotate(20deg)}}
.lcr[data-ai="1"][data-tone=live] .lcr-state b{color:var(--ai)}
.lcr-dots{display:inline-flex;gap:2px;margin-left:2px}
.lcr-dots i{width:4px;height:4px;border-radius:50%;background:currentColor;animation:lcr-bounce 1s ease-in-out infinite}
.lcr-dots i:nth-child(2){animation-delay:.15s}.lcr-dots i:nth-child(3){animation-delay:.3s}
@keyframes lcr-bounce{0%,60%,100%{transform:none;opacity:.4}30%{transform:translateY(-3px);opacity:1}}
.lcr-bar{display:block;height:4px;border-radius:99px;background:color-mix(in srgb,var(--ai) 16%,transparent);overflow:hidden}
.lcr-bar i{display:block;height:100%;width:38%;border-radius:99px;background:var(--ai);animation:lcr-slide 1.3s cubic-bezier(.5,0,.5,1) infinite}
@keyframes lcr-slide{from{transform:translateX(-110%)}to{transform:translateX(280%)}}
/* Aegis AI mark: always alive (nodes breathe, a dot orbits), busier while a step runs */
.aimark{display:block}
.aimark .nd{transform-box:fill-box;transform-origin:center;animation:ai-nd 3.2s ease-in-out infinite}
.aimark .n2{animation-delay:.5s}.aimark .n3{animation-delay:1s}.aimark .core{animation-delay:1.5s}
@keyframes ai-nd{0%,100%{opacity:.55;transform:scale(.8)}50%{opacity:1;transform:scale(1.25)}}
.aimark .net{stroke-dasharray:2.2 2.2}
.lcr-orb .orbit{position:absolute;inset:-5px;border-radius:50%;animation:lcr-spin 9s linear infinite}
.lcr-orb .orbit::before{content:"";position:absolute;top:-1px;left:50%;width:5px;height:5px;margin-left:-2.5px;border-radius:50%;background:var(--ai);opacity:.75}
@keyframes lcr-spin{to{transform:rotate(360deg)}}
.lcr[data-ai="1"][data-tone=live] .aimark .net{animation:ai-flow .7s linear infinite}
.lcr[data-ai="1"][data-tone=live] .aimark .nd{animation-duration:.9s}
.lcr[data-ai="1"][data-tone=live] .aimark .sh{stroke-dasharray:6 3;animation:ai-flow 1.2s linear infinite}
@keyframes ai-flow{to{stroke-dashoffset:-9}}
.lcr[data-ai="1"][data-tone=live] .lcr-orb .orbit{animation-duration:1.6s}
.lcr[data-ai="1"][data-tone=live] .lcr-orb .orbit::before{opacity:1;box-shadow:0 0 0 3px color-mix(in srgb,var(--ai) 28%,transparent)}
/* minimal pass: hairline cards, one accent for AI, no tints or glow */
.lcr-main{box-shadow:0 0 0 1px var(--border,#dfe3ea);border-radius:14px;padding:13px 14px}
.lcr-main:not(:disabled):hover{transform:translateY(-2px);box-shadow:0 0 0 1px var(--border-strong,#b8c0cc),0 8px 18px -10px rgba(20,26,40,.2)}
.lcr[data-sel="1"] .lcr-main{box-shadow:0 0 0 1px var(--border-strong,#b8c0cc),0 6px 16px -8px rgba(20,26,40,.25)}
.lcr[data-tone=pending] .lcr-main,.lcr[data-tone=skipped] .lcr-main{background:transparent;box-shadow:0 0 0 1px var(--border,#dfe3ea)}
.lcr-node>svg,.lcr-node>i{width:32px;height:32px;padding:8px;border-radius:10px;background:var(--surface-2,#eef1f5)}
.lcr[data-ai="1"] .lcr-main{background:var(--surface,#fff);box-shadow:0 0 0 1px var(--border,#dfe3ea)}
.lcr[data-ai="1"][data-tone=pending] .lcr-main{background:transparent}
.lcr[data-ai="1"][data-tone=live] .lcr-main{box-shadow:0 0 0 1px var(--border-strong,#b8c0cc),0 8px 20px -10px rgba(20,26,40,.28)}
.lcr-orb{width:32px;height:32px;border-radius:10px;box-shadow:none}
.lcr[data-ai="1"][data-tone=pending] .lcr-orb{background:color-mix(in srgb,var(--ai) 12%,transparent);color:var(--ai);box-shadow:none}
.lcr[data-ai="1"] .lcr-who i.lcr-bot{background:color-mix(in srgb,var(--ai) 14%,transparent);color:var(--ai)}
.lcr-act{box-shadow:none}
/* Reduced motion: decorative loops stop, but a running AI step must still look alive (the global rule would squash
   every loop to 0.2s), so its working animations get their real speed back. */
@media (prefers-reduced-motion:reduce){
.aimark .nd,.lcr-orb .orbit{animation:none!important}
.lcr[data-tone=live] *,.lcr[data-tone=live] *::before{animation-iteration-count:infinite!important}
.lcr[data-ai="1"][data-tone=live] .aimark .net{animation-duration:.7s!important}
.lcr[data-ai="1"][data-tone=live] .aimark .sh{animation-duration:1.2s!important}
.lcr[data-ai="1"][data-tone=live] .aimark .nd{animation:ai-nd .9s ease-in-out infinite!important}
.lcr[data-ai="1"][data-tone=live] .lcr-orb .orbit{animation:lcr-spin 1.6s linear infinite!important}
.lcr[data-ai="1"][data-tone=live] .lcr-orb .halo{animation-duration:1.7s!important}
.lcr[data-tone=live] .lcr-state b::before{animation-duration:1.2s!important}
.lcr[data-ai="1"][data-tone=live] .lcr-dots i{animation-duration:1s!important}
.lcr[data-ai="1"][data-tone=live] .lcr-bar i{animation-duration:1.3s!important}
}
`;
