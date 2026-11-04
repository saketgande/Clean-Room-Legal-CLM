"use client";

import type { ReactNode } from "react";

export interface HumanDecision {
  stepName: string;
  warning: { tone: "warn" | "ok"; text: string };
  rework: { reason: string } | null;
  reply: string;
  onReply: (v: string) => void;
  replyMissing: boolean;
  busy: boolean;
  onApprove: () => void;
  /** Return the work to an earlier step, with a reason; null on the first step. */
  sendBack: { options: { value: string; label: string }[]; to: string; onTo: (v: string) => void; note: string; onNote: (v: string) => void; open: boolean; onToggle: () => void; onSend: () => void } | null;
  /** Open high and critical issues, shown in the card so the person sees what they are approving. */
  risks: { id: string; severity: "critical" | "high"; clause: string; issue: string }[];
  onSeeRisks: () => void;
  /** Written with the decision and saved on the step. Not a comment in the thread. */
  note: string;
  onNote: (v: string) => void;
  /** Hand the work to someone in the step's own team; left out for someone who may not reassign. */
  reassign?: {
    teamName: string; options: { id: string; name: string; detail?: string }[];
    open: boolean; onToggle: () => void; picked: string; onPick: (id: string) => void; onConfirm: () => void;
  };
}

const svg = (p: string) => <svg className="ic" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: p }} />;
const WARN_ICON = '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>';
const OK_ICON = '<path d="M20 6 9 17l-5-5"/>';

// The approval card (any viewer) comes first, then the human-step card, then negotiation. Null when all three
// are null so an idle request keeps no empty block (FR-5). Reply text and handlers live in the parent.
export function DecisionArea({ approval, human, negotiation, signature = null }: {
  approval: ReactNode | null; human: HumanDecision | null; negotiation: ReactNode | null; signature?: ReactNode | null;
}) {
  if (!approval && !human && !negotiation && !signature) return null;
  return (
    <section className="decision" aria-label="Decision">
      {approval}
      {human && (
        <div className="card"><div className="ch"><div className="glyph flow">{svg('<circle cx="12" cy="12" r="9"/><path d="m9 12 2 2 4-4"/>')}</div><h2>Your decision</h2><span className="dnext">You&apos;re next</span><span className="cnt">{human.stepName}</span></div><div className="cb">
          {(() => {
            // "Not reviewed — needs a person — <why>": the first part is the headline, the rest explains it.
            const [head, ...rest] = human.warning.text.split(" — ");
            return (
          <div className={`decide ${human.warning.tone}`}>
            <p className="dprompt">Review the request and provide your decision.</p>
            {(() => {
              const clk = human.warning.tone === "warn";
              return (
                <div className={`dhead${clk ? " clk" : ""}`} {...(clk ? { role: "button", tabIndex: 0, title: "Open Risk & compliance", onClick: human.onSeeRisks, onKeyDown: (e: React.KeyboardEvent) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); human.onSeeRisks(); } } } : {})}>
                  <span className="dtile">{svg(human.warning.tone === "ok" ? OK_ICON : WARN_ICON)}</span>
                  <div className="dtx"><b>{head}</b>{rest.length ? <span>{rest.join(" — ")}</span> : null}</div>
                  {clk ? <span className="go" aria-hidden>›</span> : null}
                </div>
              );
            })()}
            {human.risks.length > 0 ? (
              <ul className="drisks" aria-label="Open high risks">
                {human.risks.slice(0, 3).map((k) => (
                  <li key={k.id} className={k.severity}><button type="button" className="rkbtn" onClick={human.onSeeRisks} title="Open Risk & compliance"><div className="rk"><span className={`sev ${k.severity}`}>{k.severity}</span><b>{k.clause}</b><span className="go" aria-hidden>›</span></div><p className="tx">{k.issue}</p></button></li>
                ))}
                {human.risks.length > 3 ? <li className="more">+{human.risks.length - 3} more</li> : null}
              </ul>
            ) : null}
            {human.rework ? (
              <div className="reply">
                <label className="lbl" htmlFor="rework-reply">Reply to the rejection</label>
                <div className="why">Rejected: “{human.rework.reason}”</div>
                <textarea id="rework-reply" rows={3} maxLength={2000} value={human.reply} onChange={(e) => human.onReply(e.target.value)}
                  placeholder="What did you change, or why does it stand? The approver sees this." />
              </div>
            ) : null}
            {!human.rework ? (
              <div className="dnotebox">
                <label className="lbl" htmlFor="decision-note">Decision note <i>optional</i></label>
                <div className="nbox">
                  <textarea id="decision-note" rows={3} maxLength={2000} value={human.note} onChange={(e) => human.onNote(e.target.value)}
                    placeholder="Why you are approving, or what you checked" />
                  <div className="nft"><span>Saved on this step with your decision, not posted as a comment</span><em>{human.note.length}/2000</em></div>
                </div>
              </div>
            ) : null}
            <div className="db">
              <div className="dbtns">
                <button className="btn pri" aria-label="Approve & advance →" disabled={human.busy || human.replyMissing} title={human.replyMissing ? "Reply to the rejection first" : undefined} onClick={human.onApprove}>{svg(OK_ICON)}{human.warning.tone === "warn" ? "Approve anyway" : "Approve"}</button>
                {human.sendBack ? <button className="btn alt-amb" aria-label="Send back for rework…" disabled={human.busy} title="Opens “Send back for rework” in the Workflow tab: pick the step and say why" onClick={human.sendBack.onToggle}>{svg('<path d="M9 14 4 9l5-5"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/>')}Send back</button> : null}
                {human.reassign ? <button className="btn alt-vio" aria-expanded={human.reassign.open} title={`Hand this to someone in ${human.reassign.teamName}`} onClick={human.reassign.onToggle}>{svg('<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>')}Reassign</button> : null}
              </div>
              {human.reassign?.open ? (
                <div className="rpanel" role="group" aria-label={`Reassign to ${human.reassign.teamName}`}>
                  <b>Hand this to someone in {human.reassign.teamName}</b>
                  {human.reassign.options.length === 0 ? <span className="dim">Nobody else is in this team.</span> : (
                    <div className="rlist" role="radiogroup">
                      {human.reassign.options.map((o) => (
                        <button key={o.id} type="button" role="radio" aria-checked={human.reassign!.picked === o.id} className="rrow" onClick={() => human.reassign!.onPick(o.id)}>
                          <span className="rav">{o.name.split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase()}</span>
                          <span className="rnm">{o.name}{o.detail ? <small>{o.detail}</small> : null}</span>
                          <span className="rck" aria-hidden />
                        </button>
                      ))}
                    </div>
                  )}
                  <button type="button" className="btn pri rgo" disabled={!human.reassign.picked || human.busy} onClick={human.reassign.onConfirm}>Reassign to selected person</button>
                </div>
              ) : null}
              {human.warning.tone === "warn" ? <p className="dnote">Open issues remain. You can still approve, after a confirmation.</p> : null}
            </div>
          </div>
            );
          })()}
        </div></div>
      )}
      {negotiation}
      {signature}
    </section>
  );
}

export const DECISION_CSS = `
.ro .drisks{list-style:none;margin:0 0 14px;padding:0;display:flex;flex-direction:column;gap:8px}
.ro .drisks li:not(.more):not(.all){padding:0;border-radius:12px;background:var(--surface);border:1px solid var(--border);box-shadow:0 1px 3px rgba(20,26,40,.05);position:relative;overflow:hidden;transition:border-color .15s,box-shadow .15s}
.ro .drisks li:not(.more):not(.all):hover{border-color:color-mix(in srgb,var(--warn) 50%,var(--border));box-shadow:0 8px 18px -12px rgba(20,26,40,.35)}
.ro .drisks li:not(.more):not(.all)::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--warn)}
.ro .drisks li.critical::before{background:var(--crit)}
.ro .drisks .rkbtn{display:flex;flex-direction:column;gap:6px;width:100%;padding:12px 14px 12px 18px;border:0;background:none;text-align:left;cursor:pointer}
.ro .drisks .rk{display:flex;align-items:center;gap:8px}.ro .drisks b{font:650 13px var(--sans);text-transform:capitalize;color:var(--ink)}
.ro .drisks .go{margin-left:auto;color:var(--ink-3);font-size:18px;line-height:1}
.ro .drisks .tx{margin:0;font-size:12.5px;line-height:1.5;color:var(--ink-2);overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.ro .drisks .sev{flex:none;padding:2px 9px;border-radius:99px;font:700 10px var(--sans);text-transform:uppercase;letter-spacing:.05em;background:var(--warn-soft);color:var(--warn)}
.ro .drisks .sev.critical{background:var(--crit-soft);color:var(--crit)}
.ro .drisks .more{padding:0 2px;font-size:12px;color:var(--ink-3)}
.ro .drisks .all{padding:0}.ro .drisks .all button{width:100%;min-height:36px;border:1px dashed var(--border-strong);border-radius:10px;background:none;font:600 12.5px var(--sans);color:var(--c-ind);cursor:pointer}.ro .drisks .all button:hover{background:color-mix(in srgb,var(--c-ind) 8%,transparent)}
.ro .decide .dhead.clk{cursor:pointer;align-items:center;padding:12px 14px;border-radius:12px;background:var(--surface);border:1px solid color-mix(in srgb,var(--warn) 35%,var(--border));box-shadow:0 1px 3px rgba(20,26,40,.05);transition:border-color .15s,box-shadow .15s}
.ro .decide .dhead.clk:hover{border-color:var(--warn);box-shadow:0 8px 18px -12px rgba(20,26,40,.35)}
.ro .decide .dhead.clk:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.ro .decide .dhead .go{margin-left:auto;flex:none;color:var(--ink-3);font-size:20px;line-height:1}
.ro .dnotebox{display:flex;flex-direction:column;gap:6px;margin-bottom:12px}
.ro .dnotebox .lbl{font:650 12.5px var(--sans);color:var(--ink)}.ro .dnotebox .lbl i{font:500 11.5px var(--sans);color:var(--ink-3);margin-left:6px;font-style:normal}
.ro .dnotebox .nbox{border:1px solid var(--border-strong);border-radius:12px;background:var(--surface);box-shadow:0 1px 3px rgba(20,26,40,.05);transition:border-color .15s,box-shadow .15s}
.ro .dnotebox .nbox:focus-within{border-color:var(--c-ind);box-shadow:0 0 0 3px color-mix(in srgb,var(--c-ind) 16%,transparent)}
.ro .dnotebox textarea{display:block;width:100%;padding:11px 13px 4px;border:0;background:none;color:var(--ink);font:500 13px/1.5 var(--sans);resize:none;outline:none}
.ro .dnotebox .nft{display:flex;justify-content:space-between;gap:10px;padding:6px 13px 9px;font-size:11px;color:var(--ink-3)}.ro .dnotebox .nft em{font-style:normal;white-space:nowrap}
.ro .decide .rpanel{display:flex;flex-direction:column;gap:10px;margin:10px 0 2px;padding:12px;border-radius:12px;background:var(--surface);border:1px solid var(--border);box-shadow:0 1px 3px rgba(20,26,40,.05)}
.ro .decide .rpanel>b{font:650 12.5px var(--sans);color:var(--ink)}
.ro .decide .rlist{display:flex;flex-direction:column;gap:6px;max-height:220px;overflow-y:auto}
.ro .decide .rrow{display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border);border-radius:10px;background:var(--surface);text-align:left;cursor:pointer;transition:border-color .15s,background-color .15s}
.ro .decide .rrow:hover{background:var(--surface-2)}.ro .decide .rrow[aria-checked=true]{border-color:var(--c-vio);background:color-mix(in srgb,var(--c-vio) 8%,var(--surface))}
.ro .decide .rav{flex:none;display:grid;place-items:center;width:30px;height:30px;border-radius:50%;background:color-mix(in srgb,var(--c-vio) 16%,transparent);color:var(--c-vio);font:700 11px var(--sans)}
.ro .decide .rnm{flex:1;min-width:0;font:600 13px var(--sans);color:var(--ink);display:flex;flex-direction:column}.ro .decide .rnm small{font:500 11.5px var(--sans);color:var(--ink-3)}
.ro .decide .rck{flex:none;width:16px;height:16px;border-radius:50%;border:2px solid var(--border-strong)}.ro .decide .rrow[aria-checked=true] .rck{border-color:var(--c-vio);background:radial-gradient(var(--c-vio) 45%,transparent 50%)}
.ro .decide .rgo{width:100%;min-height:40px;grid-column:auto}
.ro .dnext{padding:3px 10px;border-radius:99px;font:650 11.5px var(--sans);background:color-mix(in srgb,var(--c-ind) 12%,transparent);color:var(--c-ind);white-space:nowrap}
.ro .dprompt{margin:0 0 10px;font-size:13px;color:var(--ink-2)}
.ro .decide .dbtns{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.ro .decide .dbtns .btn.pri{grid-column:1/-1;min-height:44px;font-size:14px}
.ro .decide .dbtns .btn:nth-child(2):last-child{grid-column:1/-1}
.ro .decide .dbtns .btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:40px;padding:0 10px;border-radius:10px;font:650 13px var(--sans);white-space:nowrap}
.ro .decide .dbtns .btn .ic{width:15px;height:15px}
.ro .decide .db .dbtns .btn.pri{background:var(--c-emr);border-color:var(--c-emr);color:#fff}
.ro .decide.warn .db .dbtns .btn.pri{background:var(--c-amb);border-color:var(--c-amb)}
.ro .decide .db .dbtns .btn.alt-amb{background:color-mix(in srgb,var(--c-amb) 10%,var(--surface));border-color:color-mix(in srgb,var(--c-amb) 45%,var(--border));color:var(--c-amb)}
.ro .decide .db .dbtns .btn.alt-vio{background:color-mix(in srgb,var(--c-vio) 10%,var(--surface));border-color:color-mix(in srgb,var(--c-vio) 45%,var(--border));color:var(--c-vio)}
.ro .decide .dcomment{display:inline-flex;align-items:center;gap:6px;margin-top:10px;padding:4px 2px;font:600 12.5px var(--sans);color:var(--c-ind);border-radius:6px}
.ro .decide .dcomment .ic{width:14px;height:14px}.ro .decide .dcomment:hover{text-decoration:underline}
.ro .decide .dbtns .btn:focus-visible,.ro .decide .dcomment:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

.ro .decide .dhead{display:flex;align-items:flex-start;gap:11px;margin-bottom:12px}
.ro .decide .dtile{flex:none;display:grid;place-items:center;width:36px;height:36px;border-radius:12px;background:color-mix(in srgb,var(--warn) 22%,transparent);color:var(--warn)}
.ro .decide.ok .dtile{background:color-mix(in srgb,var(--good) 20%,transparent);color:var(--good)}
.ro .decide .dtile .ic{width:19px;height:19px}
.ro .decide .dtx{display:flex;flex-direction:column;gap:3px;min-width:0}
.ro .decide .dtx b{font:700 14.5px/1.3 var(--sans);color:var(--ink)}
.ro .decide .dtx span{font:500 12.5px/1.5 var(--sans);color:var(--ink-2);overflow-wrap:anywhere}
.ro .decide .dnote{margin:0;font-size:11.5px;color:var(--ink-3);text-align:center}
.ro .decide .dalt{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:2px}
.ro .decide .dalt.one{grid-template-columns:1fr}
.ro .decide .dalt .btn{flex:none;flex-direction:column;gap:4px;padding:9px 6px;height:auto;line-height:1.25;text-align:center;white-space:normal}
.ro .decide .db .dalt .btn.btn.alt-amb{background:color-mix(in srgb,var(--c-amb) 12%,var(--surface));border-color:color-mix(in srgb,var(--c-amb) 45%,var(--border));color:var(--c-amb)}
.ro .decide .db .dalt .btn.btn.alt-ind{background:color-mix(in srgb,var(--c-ind) 10%,var(--surface));border-color:color-mix(in srgb,var(--c-ind) 40%,var(--border));color:var(--c-ind)}
.ro .decide .dalt .btn:hover{filter:brightness(.97);transform:translateY(-1px)}

.ro .decision{display:flex;flex-direction:column;gap:14px}
.ro .decide{border-radius:10px;padding:12px} .ro .decide.warn{border:1px solid color-mix(in srgb,var(--warn) 34%,var(--border));background:var(--warn-soft)} .ro .decide.ok{border:1px solid color-mix(in srgb,var(--good) 30%,var(--border));background:var(--good-soft)}
.ro .decide .dw{display:flex;align-items:flex-start;gap:7px;font-size:12.5px;color:var(--warn);font-weight:600;margin-bottom:10px;line-height:1.4} .ro .decide .dw.ok{color:var(--good)} .ro .decide .dw .ic{width:15px;height:15px;flex:none;margin-top:1px}
.ro .decide .db{display:flex;flex-direction:column;gap:7px}
.ro .decide .reply{display:flex;flex-direction:column;gap:6px;margin-bottom:10px} .ro .decide .reply .lbl{font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)} .ro .decide .reply .why{font-size:12.5px;line-height:1.45;color:var(--ink)}
.ro .decide .reply textarea{padding:8px 11px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:500 12.5px var(--sans);resize:vertical;outline:none}
`;
