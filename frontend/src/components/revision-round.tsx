"use client";

// A counterparty's returned version, clause by clause against the one we sent
// (backend: app/contract_files/revisions.py). Two halves used by the CLM
// workspace: the changed clauses for the document pane, and the decisions for
// the agent panel. Styles are scoped under `.clm` like the rest of the workspace.

import { useState } from "react";
import type { RevisionChange, RevisionRound } from "@/lib/types";

const KIND: Record<RevisionChange["kind"], string> = {
  changed: "Changed by them",
  added: "Added by them",
  removed: "Removed by them",
  reverted: "Put back their wording",
  countered: "Changed our change",
  ours: "Took our wording",
};

const DECISION: Record<RevisionChange["decision"], string> = {
  open: "To decide",
  accepted: "Accepted",
  kept: "Keep ours",
  countered: "Countered",
  agreed: "Agreed",
};

// What "accept" and "keep ours" mean depends on what they did to the clause.
function choices(c: RevisionChange): { accept: string; keep: string } {
  if (c.kind === "removed") return { accept: "Accept removal", keep: "Keep the clause" };
  if (c.kind === "added") return { accept: "Accept addition", keep: "Leave it out" };
  return { accept: "Accept theirs", keep: "Keep ours" };
}

export function RevisionChanges({
  round, selected, onSelect, onShowDocument,
}: {
  round: RevisionRound;
  selected: string | null;
  onSelect: (id: string) => void;
  onShowDocument: () => void;
}) {
  return (
    <div className="rv">
      <div className="rv-hd">
        <div>
          <div className="lbl">Round {round.round_number} · their version against the one we sent</div>
          <div className="rv-sub">
            {round.changes.length === 0
              ? "They sent it back without changing a word."
              : round.tracked
                ? "Checked against their tracked changes — anything they didn't mark is flagged."
                : "Their file marks no changes, so every difference below was found by comparison."}
          </div>
        </div>
        <button className="btn sm" onClick={onShowDocument}>Show full document</button>
      </div>
      {round.changes.map((c) => (
        <button key={c.id} className={`rv-card${c.id === selected ? " on" : ""}`} onClick={() => onSelect(c.id)}>
          <span className="rv-row">
            <span className="rv-label">{c.label || "Clause"}</span>
            <span className={`rv-kind k-${c.kind}`}>{KIND[c.kind]}</span>
            {c.unmarked ? <span className="rv-kind k-unmarked">Not marked by them</span> : null}
            <span className={`rv-dec d-${c.decision}`}>{DECISION[c.decision]}</span>
          </span>
          <span className="rv-text">
            {c.parts.map(([op, text], i) =>
              op === "=" ? <span key={i}>{text} </span>
                : op === "-" ? <del key={i}>{text}</del>
                  : <ins key={i}>{text}</ins>,
            )}
          </span>
        </button>
      ))}
    </div>
  );
}

export function RevisionDecisions({
  round, selected, onSelect, busy, onDecide, onFinish,
}: {
  round: RevisionRound;
  selected: string | null;
  onSelect: (id: string) => void;
  busy: boolean;
  onDecide: (change: RevisionChange, decision: "open" | "accepted" | "kept" | "countered", counter?: string) => void;
  onFinish: () => void;
}) {
  const cur = round.changes.find((c) => c.id === selected) ?? round.changes[0];
  const [countering, setCountering] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const open = round.status === "open";
  const finishText = round.open > 0
    ? `Decide ${round.open} more to finish`
    : round.pushed_back > 0
      ? `Prepare our reply (${round.pushed_back} pushed back)`
      : "All agreed — finish negotiation";

  if (!open) {
    return (
      <div className="list">
        <div className="rv-done">
          <b>{round.outcome === "agreed" ? "Round agreed" : "Our reply is ready"}</b>
          <div>
            {round.outcome === "agreed"
              ? "Every change was accepted; the text is settled."
              : `The current version is our reply: their text with our wording back on ${round.pushed_back} clause${round.pushed_back === 1 ? "" : "s"}. Send it to them for the next round.`}
          </div>
          {round.note ? <div className="dim">{round.note}</div> : null}
        </div>
      </div>
    );
  }
  return (
    <div className="list">
      {cur ? (
        <div className="rv-detail">
          <div className="rv-row"><b>{cur.label || "Clause"}</b></div>
          <div className="rv-row">
            <span className={`rv-kind k-${cur.kind}`}>{KIND[cur.kind]}</span>
            {cur.unmarked ? <span className="rv-kind k-unmarked">Not marked by them</span> : null}
          </div>
          <div className="rv-box">
            <div className="lbl">Playbook</div>
            {cur.playbook.length === 0
              ? <div className="dim">No playbook finding quotes their wording here.</div>
              : cur.playbook.map((p) => (
                <div key={p.id} className="rv-find"><span className={`sev ${p.severity.toLowerCase()}`} />{p.issue}</div>
              ))}
          </div>
          {cur.carried.length > 0 ? (
            <div className="rv-box carried">
              <div className="lbl">Comments on our wording</div>
              {cur.carried.map((m) => (
                <div key={m.id}>“{m.body}” <span className="dim">— {m.still_there ? "the words are still there" : "the words it was on are gone"}</span></div>
              ))}
            </div>
          ) : null}
          {cur.kind === "ours" ? (
            <div className="rv-ok">Nothing to decide: they kept our wording.</div>
          ) : countering === cur.id ? (
            <div className="rv-counter">
              <label className="lbl" htmlFor="rv-counter-text">Your wording for this clause</label>
              <textarea id="rv-counter-text" value={draft} onChange={(e) => setDraft(e.target.value)} rows={6} />
              <div className="rf">
                <button className="btn sm pri" disabled={busy || !draft.trim()} onClick={() => { onDecide(cur, "countered", draft); setCountering(null); }}>Save counter</button>
                <button className="btn sm" onClick={() => setCountering(null)}>Cancel</button>
              </div>
            </div>
          ) : (
            <div className="rf">
              <button className={`btn sm${cur.decision === "accepted" ? " pri" : ""}`} disabled={busy} onClick={() => onDecide(cur, "accepted")}>{choices(cur).accept}</button>
              <button className={`btn sm${cur.decision === "kept" ? " pri" : ""}`} disabled={busy} onClick={() => onDecide(cur, "kept")}>{choices(cur).keep}</button>
              <button className={`btn sm${cur.decision === "countered" ? " pri" : ""}`} disabled={busy}
                onClick={() => { setCountering(cur.id); setDraft(cur.counter_text ?? cur.their_text ?? cur.our_text ?? ""); }}>Counter</button>
              {cur.decision !== "open" ? <button className="btn sm ghost" disabled={busy} onClick={() => onDecide(cur, "open")}>Undo</button> : null}
            </div>
          )}
        </div>
      ) : null}

      <div className="lbl" style={{ marginTop: 6 }}>All changes from them</div>
      {round.changes.map((c) => (
        <button key={c.id} className={`rv-item${c.id === cur?.id ? " on" : ""}`} onClick={() => onSelect(c.id)}>
          <span className="rv-ilabel">{c.label || "Clause"}</span>
          <span className={`rv-dec d-${c.decision}`}>{DECISION[c.decision]}</span>
        </button>
      ))}
      <button className="btn pri rv-finish" disabled={busy || round.open > 0} onClick={onFinish}>{finishText}</button>
    </div>
  );
}

export const REVISION_CSS = `
.clm .rv{display:flex;flex-direction:column;gap:10px;padding:18px 22px;overflow:auto;height:100%}
.clm .rv-hd{display:flex;align-items:flex-start;gap:12px;justify-content:space-between}
.clm .rv-sub{font-size:12.5px;color:var(--ink-2);margin-top:3px}
.clm .rv-card{display:flex;flex-direction:column;gap:8px;text-align:left;padding:12px 14px;border:1px solid var(--border);border-radius:11px;background:var(--surface)}
.clm .rv-card.on{border:2px solid var(--accent);box-shadow:var(--shadow)}
.clm .rv-row{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.clm .rv-label{font-weight:650;font-size:13px}
.clm .rv-kind{font:600 10.5px var(--sans);padding:2px 7px;border-radius:5px;background:var(--surface-2);color:var(--ink-2)}
.clm .rv-kind.k-countered,.clm .rv-kind.k-reverted{background:var(--warn-soft);color:var(--warn)}
.clm .rv-kind.k-unmarked,.clm .rv-kind.k-removed{background:var(--crit-soft);color:var(--crit)}
.clm .rv-kind.k-ours{background:var(--good-soft);color:var(--good)}
.clm .rv-dec{margin-left:auto;font:600 11px var(--sans);color:var(--ink-3)}
.clm .rv-dec.d-open{color:var(--warn)} .clm .rv-dec.d-accepted,.clm .rv-dec.d-agreed{color:var(--good)} .clm .rv-dec.d-kept,.clm .rv-dec.d-countered{color:var(--accent)}
.clm .rv-text{font-family:var(--serif);font-size:14px;line-height:1.6;color:var(--ink)}
.clm .rv-text del{background:var(--crit-soft);color:var(--crit);margin-right:4px}
.clm .rv-text ins{background:var(--good-soft);color:var(--good);text-decoration:underline;margin-right:4px}
.clm .rv-detail{display:flex;flex-direction:column;gap:9px;padding:4px 2px 12px;border-bottom:1px solid var(--border)}
.clm .rv-box{display:flex;flex-direction:column;gap:5px;padding:9px 11px;border-radius:9px;background:var(--surface-2);border:1px solid var(--border);font-size:12.5px;line-height:1.5}
.clm .rv-box.carried{background:var(--accent-soft)}
.clm .rv-find{display:flex;gap:7px;align-items:flex-start}
.clm .rv-ok{font-size:12.5px;font-weight:600;color:var(--good)}
.clm .rv-counter{display:flex;flex-direction:column;gap:6px}
.clm .rv-counter textarea{width:100%;border:1px solid var(--border-strong);border-radius:8px;padding:8px;background:var(--surface);color:var(--ink);font:13px/1.5 var(--serif)}
.clm .rv-item{display:flex;align-items:center;gap:8px;padding:7px 8px;border-radius:7px;font-size:12.5px;text-align:left}
.clm .rv-item.on{background:var(--accent-soft)}
.clm .rv-ilabel{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.clm .rv-finish{margin-top:8px;padding:9px 12px;font-size:13px}
.clm .rv-done{display:flex;flex-direction:column;gap:6px;padding:12px;border-radius:10px;background:var(--good-soft);font-size:12.5px;line-height:1.5}
`;
