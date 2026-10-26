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
  canSendBack: boolean;
  onJumpToSendBack: () => void;
  onJumpToComment: () => void;
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
        <div className="card"><div className="ch"><div className="glyph flow">{svg('<circle cx="12" cy="12" r="9"/><path d="m9 12 2 2 4-4"/>')}</div><h2>Your decision</h2><span className="cnt">{human.stepName}</span></div><div className="cb">
          <div className={`decide ${human.warning.tone}`}>
            <div className={`dw${human.warning.tone === "ok" ? " ok" : ""}`}>{svg(human.warning.tone === "ok" ? OK_ICON : WARN_ICON)}{human.warning.text}</div>
            {human.rework ? (
              <div className="reply">
                <label className="lbl" htmlFor="rework-reply">Reply to the rejection</label>
                <div className="why">Rejected: “{human.rework.reason}”</div>
                <textarea id="rework-reply" rows={3} maxLength={2000} value={human.reply} onChange={(e) => human.onReply(e.target.value)}
                  placeholder="What did you change, or why does it stand? The approver sees this." />
              </div>
            ) : null}
            <div className="db">
              <button className="btn pri wide" disabled={human.busy || human.replyMissing} title={human.replyMissing ? "Reply to the rejection first" : undefined} onClick={human.onApprove}>{svg(OK_ICON)}Approve &amp; advance →</button>
              {human.canSendBack ? <button className="btn wide" disabled={human.busy} title="Opens “Send back for rework”: pick the step and say why" onClick={human.onJumpToSendBack}>{svg('<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 5 5v3"/>')}Send back for rework…</button> : null}
              <button className="btn wide" title="Jumps to the comment box. Nothing is sent until you press Post" onClick={human.onJumpToComment}>{svg('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>')}Add a comment</button>
            </div>
          </div>
        </div></div>
      )}
      {negotiation}
      {signature}
    </section>
  );
}

export const DECISION_CSS = `
.ro .decision{display:flex;flex-direction:column;gap:14px}
.ro .decide{border-radius:10px;padding:12px} .ro .decide.warn{border:1px solid color-mix(in srgb,var(--warn) 34%,var(--border));background:var(--warn-soft)} .ro .decide.ok{border:1px solid color-mix(in srgb,var(--good) 30%,var(--border));background:var(--good-soft)}
.ro .decide .dw{display:flex;align-items:flex-start;gap:7px;font-size:12.5px;color:var(--warn);font-weight:600;margin-bottom:10px;line-height:1.4} .ro .decide .dw.ok{color:var(--good)} .ro .decide .dw .ic{width:15px;height:15px;flex:none;margin-top:1px}
.ro .decide .db{display:flex;flex-direction:column;gap:7px}
.ro .decide .reply{display:flex;flex-direction:column;gap:6px;margin-bottom:10px} .ro .decide .reply .lbl{font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)} .ro .decide .reply .why{font-size:12.5px;line-height:1.45;color:var(--ink)}
.ro .decide .reply textarea{padding:8px 11px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:500 12.5px var(--sans);resize:vertical;outline:none}
`;
