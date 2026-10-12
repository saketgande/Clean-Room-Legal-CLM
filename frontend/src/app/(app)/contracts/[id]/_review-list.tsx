"use client";

// The Review tab: every suggestion for the contract in one list, decided the
// same way. A playbook issue shows the changes made for it; a change made for
// no issue (asked for in chat, or proposed by hand) stands as its own card.
// Styles come from the `.clm` scope of the workspace page.

import { useEffect, useRef, useState } from "react";
import { draftingText } from "@/lib/issue-fixes";
import { editOrigin, editState, isHighRisk, MIN_KEEP_REASON, type EditState, type IssueSuggestion, type Suggestions } from "@/lib/suggestions";
import type { ContractDeviation, ContractEditResponse } from "@/lib/types";

const SEV = (s: string) => {
  const v = (s || "").toLowerCase();
  return v === "critical" || v === "high" ? "high" : v === "their_change" ? "ext" : v;
};

const ORIGIN_LABEL = { playbook: "Playbook", assistant: "AI suggestion", manual: "Manual" } as const;

const clip = (text: string, max = 240) => (text.length > max ? `${text.slice(0, max).trimEnd()}…` : text);

function noteFor(state: Exclude<EditState, "ready">, edit: ContractEditResponse): string {
  switch (state) {
    case "stale":
      return "Out of date: the contract has changed since this was suggested, so it can only be rejected.";
    case "unplaceable":
      return "Can't be placed: its wording isn't in the text, so it can only be rejected.";
    case "blank":
      return `It would write a blank into the contract (${edit.blank}), so it can only be rejected. Type the real wording into the clause yourself.`;
  }
}

export interface ReviewListProps {
  suggestions: Suggestions;
  currentVersionId: string | null;
  busy: boolean;
  activeEditId: string | null;
  onDecide: (edit: ContractEditResponse, accept: boolean, issue?: ContractDeviation) => void;
  /** Close an issue: accepted = our wording went in; otherwise the original wording stays, with the reason (required for a high or critical risk). */
  onCloseIssue: (issue: ContractDeviation, accept: boolean, reason?: string) => void;
  onDraftFix: (issue: ContractDeviation) => void;
  onShow: (editId: string) => void;
  /** Issues that have no suggested change yet, and how to draft one for all of them at once. */
  unfixedCount?: number;
  onDraftAll?: () => void;
  /** A draft is being written: say so, since the result lands in this list. */
  drafting?: boolean;
  /** When the background job is writing the changes for every issue: how far it has got (total is 0 until it has counted). */
  draftProgress?: { done: number; total: number } | null;
  /** Changes already accepted or rejected, so the person can check what was decided. */
  decided?: ContractEditResponse[];
}

export const REVIEW_LIST_CSS = `
.clm .rbar{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:12.5px;color:var(--ink-2);border:1px solid var(--border);background:var(--surface-2);border-radius:10px;padding:9px 12px;margin-bottom:10px}
.clm .rev{position:relative;border:1px solid var(--border);border-radius:12px;background:var(--surface);padding:12px 14px 12px 18px;margin-bottom:10px;overflow:hidden;transition:border-color .15s,box-shadow .15s}
.clm .rev::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--border-strong)}
.clm .rev.high::before{background:var(--crit)} .clm .rev.medium::before{background:var(--risk-med)} .clm .rev.low::before{background:var(--risk-low)} .clm .rev.ext::before{background:var(--ext)} .clm .rev.sug::before{background:var(--ai)}
.clm .rev.focus{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft),var(--shadow)}
.clm .rt{display:flex;align-items:center;gap:8px;font-size:13.5px;font-weight:600;color:var(--ink)}
.clm .cstat,.clm .otag{margin-left:auto;display:inline-flex;align-items:center;gap:5px;font:600 11px var(--sans);text-transform:capitalize;padding:2px 9px;border-radius:99px;background:var(--surface-2);color:var(--ink-3);white-space:nowrap}
.clm .cstat::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.clm .rev.high .cstat{background:var(--crit-soft);color:var(--crit)} .clm .rev.medium .cstat{background:var(--warn-soft);color:var(--warn)} .clm .rev.low .cstat{background:var(--good-soft);color:var(--good)} .clm .rev.ext .cstat{background:var(--ext-soft);color:var(--ext)}
.clm .otag{background:var(--ai-soft);color:var(--ai)}
.clm .rd{margin-top:8px;font-size:12.5px;line-height:1.55;color:var(--ink-2)}
.clm .rd b{color:var(--ink);font-weight:600}
.clm .rd.warnnote{color:var(--warn);background:var(--warn-soft);border-radius:8px;padding:7px 10px}
.clm .sdiff{margin-top:9px;font-size:13px;line-height:1.65;color:var(--ink)}
.clm .sdiff del{color:var(--crit);background:var(--crit-soft);text-decoration:line-through;text-decoration-thickness:1px;border-radius:3px;padding:0 3px}
.clm .sdiff ins{color:var(--good);background:var(--good-soft);text-decoration:none;border-radius:3px;padding:0 3px;margin-left:5px}
.clm .rf{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:12px}
.clm .rf .btn.acc{background:var(--good-soft);color:var(--good);border-color:color-mix(in srgb,var(--good) 30%,transparent);font-weight:600}
.clm .rf .btn.acc:hover{background:color-mix(in srgb,var(--good) 16%,var(--good-soft))}
.clm .rf .btn.ghost{margin-left:auto;border-color:transparent;background:none;color:var(--accent);padding:0 2px}
.clm .rf .btn.ghost:hover{background:none;text-decoration:underline}
.clm .decided{margin-top:14px;border-top:1px solid var(--border);padding-top:10px}
.clm .decided>summary{cursor:pointer;font-size:12.5px;font-weight:600;color:var(--ink-2);list-style:revert}
.clm .drow{display:flex;gap:10px;align-items:flex-start;padding:9px 2px;border-bottom:1px solid var(--border)}
.clm .drow:last-child{border-bottom:0}
.clm .dchip{flex:none;font:600 11px var(--sans);padding:2px 9px;border-radius:99px;margin-top:1px;background:var(--surface-2);color:var(--ink-3)}
.clm .dchip.accepted{background:var(--good-soft);color:var(--good)} .clm .dchip.rejected{background:var(--crit-soft);color:var(--crit)}
.clm .drow .rd{margin-top:0}
.clm .drow .sdiff{margin-top:4px;font-size:12.5px}
`;

export function ReviewList(props: ReviewListProps) {
  const { suggestions, activeEditId, drafting, draftProgress, unfixedCount = 0, onDraftAll, busy, decided = [], currentVersionId } = props;
  const root = useRef<HTMLDivElement>(null);

  // A change picked in the document: bring its card into view.
  useEffect(() => {
    if (!activeEditId) return;
    const card = root.current?.querySelector(`[data-edit-ids~="${CSS.escape(activeEditId)}"]`);
    card?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [activeEditId]);

  if (suggestions.toDecide === 0)
    return (
      <div className="list" ref={root}>
        <div className="dim" style={{ fontSize: 12.5, padding: 8 }}>
          Nothing waiting for your decision: there are no suggested changes to accept or reject.
        </div>
        <DecidedList edits={decided} currentVersionId={currentVersionId} />
      </div>
    );

  return (
    <div className="list" ref={root}>
      {drafting || (unfixedCount > 0 && onDraftAll) ? (
        <div className="rbar" role="status">
          <div>
            <div>{drafting ? draftingText(draftProgress) : `${unfixedCount} open issue${unfixedCount === 1 ? " has" : "s have"} no suggested change yet.`}</div>
            {!drafting ? <div className="dim" style={{ fontSize: 11.5, marginTop: 2 }}>Aegis can write a suggested change for each one. You still accept or reject it below.</div> : null}
          </div>
          {!drafting && onDraftAll ? <button className="btn sm pri" disabled={busy} onClick={onDraftAll}>Draft fixes for all</button> : null}
        </div>
      ) : null}
      <div className="dim" style={{ fontSize: 11.5, padding: "0 2px 10px", lineHeight: 1.5 }}>
        Accepted changes are added to the contract once every suggestion here has been decided.
      </div>
      {suggestions.issues.map((issue) => (
        <IssueCard key={issue.deviation.id} issue={issue} {...props} />
      ))}
      {suggestions.standalone.map((edit) => (
        <StandaloneCard key={edit.id} edit={edit} {...props} />
      ))}
      <DecidedList edits={decided} currentVersionId={currentVersionId} />
    </div>
  );
}

/** What was accepted and rejected, so a person can check their own decisions. Collapsed by default. */
function DecidedList({ edits, currentVersionId }: { edits: ContractEditResponse[]; currentVersionId: string | null }) {
  if (edits.length === 0) return null;
  return (
    <details className="decided">
      <summary>Decided ({edits.length})</summary>
      {edits.map((e) => (
        <div className="drow" key={e.id}>
          <span className={`dchip ${e.status}`}>{e.status === "accepted" ? "Accepted" : "Rejected"}</span>
          <div>
            {e.rationale ? <div className="rd">{clip(e.rationale, 160)}</div> : null}
            <Change edit={e} />
            {currentVersionId && e.contract_version_id !== currentVersionId ? <div className="dim" style={{ fontSize: 11.5, marginTop: 3 }}>Decided on an earlier version of the contract.</div> : null}
          </div>
        </div>
      ))}
    </details>
  );
}

function IssueCard({ issue, busy, drafting, activeEditId, currentVersionId, onDecide, onCloseIssue, onDraftFix, onShow }: { issue: IssueSuggestion } & ReviewListProps) {
  const d = issue.deviation;
  const proposed = issue.edits.filter((e) => e.status === "proposed");
  const decided = issue.edits.filter((e) => e.status !== "proposed");
  const focused = issue.edits.some((e) => e.id === activeEditId);
  const wasAccepted = decided.some((e) => e.status === "accepted");
  return (
    <div className={`rev ${SEV(d.severity)}${focused ? " focus" : ""}`} data-edit-ids={issue.edits.map((e) => e.id).join(" ")}>
      <div className="rt">
        <span style={{ textTransform: "capitalize" }}>{(d.clause_type || "clause").replace(/_/g, " ")}</span>
        <span className="cstat">{d.severity}</span>
      </div>
      <div className="rd">{d.issue}</div>
      {d.suggested_fix ? (
        <div className="rd" style={{ color: "var(--good)" }}>
          <b style={{ color: "var(--ink)" }}>Fix:</b> {d.suggested_fix}
        </div>
      ) : null}
      {proposed.length > 0 ? (
        proposed.map((e) => (
          <EditActions key={e.id} edit={e} state={editState(e, currentVersionId)} busy={busy} onDecide={(accept) => onDecide(e, accept, d)} onShow={() => onShow(e.id)} />
        ))
      ) : decided.length > 0 ? (
        <>
          <div className="rd dim">
            Change already {wasAccepted ? "accepted" : "rejected"}.
            {!wasAccepted && isHighRisk(d) ? " This is a high risk, so it stays open until you say why it can stay." : ""}
          </div>
          <CloseIssue issue={d} accepted={wasAccepted} label="Close issue" busy={busy} onCloseIssue={onCloseIssue} />
        </>
      ) : (
        <div className="rf">
          <button className="btn sm pri" disabled={busy || !!drafting} onClick={() => onDraftFix(d)}>Draft fix with AI</button>
          <CloseIssue issue={d} accepted={false} label="Keep as is" busy={busy} onCloseIssue={onCloseIssue} inline />
        </div>
      )}
    </div>
  );
}

/** Closing an issue. Keeping the original wording of a high or critical risk first asks why, because the
 *  reason is the only record that someone chose to carry it. Anything else closes at once. */
function CloseIssue({ issue, accepted, label, busy, onCloseIssue, inline }: { issue: ContractDeviation; accepted: boolean; label: string; busy: boolean; onCloseIssue: ReviewListProps["onCloseIssue"]; inline?: boolean }) {
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const needsReason = !accepted && isHighRisk(issue);
  if (!needsReason)
    return (
      <div className={inline ? undefined : "rf"}>
        <button className={`btn sm${inline ? "" : " pri"}`} disabled={busy} onClick={() => onCloseIssue(issue, accepted)}>{label}</button>
      </div>
    );
  if (!asking)
    return (
      <div className={inline ? undefined : "rf"}>
        <button className={`btn sm${inline ? "" : " pri"}`} disabled={busy} onClick={() => setAsking(true)}>{label}</button>
      </div>
    );
  const ready = reason.trim().length >= MIN_KEEP_REASON;
  return (
    <div className="keep" style={{ flexBasis: "100%", marginTop: 8 }}>
      <label className="rd" htmlFor={`keep-${issue.id}`} style={{ display: "block", marginTop: 0 }}>
        <b>Why can this {issue.severity.toLowerCase()} risk stay?</b> The reason is saved with the issue.
      </label>
      <textarea
        id={`keep-${issue.id}`}
        rows={2}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="For example: the customer's standard term, agreed with the GC on 3 Oct"
        style={{ width: "100%", marginTop: 6, font: "inherit", fontSize: 12.5, padding: "6px 8px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--surface)", color: "var(--ink)" }}
      />
      <div className="rf" style={{ marginTop: 8 }}>
        <button className="btn sm pri" disabled={busy || !ready} onClick={() => onCloseIssue(issue, false, reason.trim())}>Keep original wording</button>
        <button className="btn sm" disabled={busy} onClick={() => { setAsking(false); setReason(""); }}>Cancel</button>
        {!ready ? <span className="dim" style={{ fontSize: 11.5 }}>A sentence is enough.</span> : null}
      </div>
    </div>
  );
}

function StandaloneCard({ edit, busy, activeEditId, currentVersionId, onDecide, onShow }: { edit: ContractEditResponse } & ReviewListProps) {
  const origin = editOrigin(edit);
  return (
    <div className={`rev sug${edit.id === activeEditId ? " focus" : ""}`} data-edit-ids={edit.id}>
      <div className="rt">
        <span>Suggested change</span>
        <span className="otag">{ORIGIN_LABEL[origin]}</span>
      </div>
      {edit.rationale ? <div className="rd">{edit.rationale}</div> : null}
      <EditActions edit={edit} state={editState(edit, currentVersionId)} busy={busy} onDecide={(accept) => onDecide(edit, accept)} onShow={() => onShow(edit.id)} />
    </div>
  );
}

function EditActions({ edit, state, busy, onDecide, onShow }: { edit: ContractEditResponse; state: EditState; busy: boolean; onDecide: (accept: boolean) => void; onShow: () => void }) {
  return (
    <>
      <Change edit={edit} />
      {state !== "ready" ? <div className="rd warnnote">{noteFor(state, edit)}</div> : null}
      <div className="rf">
        {state === "ready" ? <button className="btn sm acc" disabled={busy} onClick={() => onDecide(true)}>Accept change</button> : null}
        <button className="btn sm" disabled={busy} onClick={() => onDecide(false)}>Reject</button>
        {state === "ready" || state === "blank" ? <button className="btn sm ghost" onClick={onShow}>Show in document</button> : null}
      </div>
    </>
  );
}

function Change({ edit }: { edit: ContractEditResponse }) {
  const from = (edit.original_text ?? "").trim();
  const to = (edit.replacement_text ?? "").trim();
  if (!from && !to) return null;
  return (
    <div className="sdiff">
      {from ? <del>{clip(from)}</del> : null}
      {to ? <ins>{clip(to)}</ins> : <span className="dim">This text is removed.</span>}
    </div>
  );
}
