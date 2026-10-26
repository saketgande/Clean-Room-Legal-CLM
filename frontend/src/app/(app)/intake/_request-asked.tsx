// "What was asked" — the requester's words, the form facts, the answers editor, the notices and the AI read.
// The description is shown as written; the old "— {source} by {requester}" line is system text and is gone.
import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

export const ASKED_CSS = `
.ro .quote{padding:12px 14px;background:var(--inset);border:1px solid var(--border);border-left:3px solid var(--border-strong);border-radius:9px;color:var(--ink-2);font-size:12.5px;line-height:1.65;white-space:pre-line;overflow-wrap:anywhere;margin-top:14px}
.ro .needsinfo{display:flex;gap:9px;margin-top:12px;padding:10px 12px;border-radius:9px;background:var(--warn-soft);border:1px solid color-mix(in srgb,var(--warn) 30%,var(--border))}
.ro .needsinfo .ic{width:16px;height:16px;color:var(--warn);flex:none;margin-top:1px}
.ro .needsinfo b{font:600 12.5px var(--sans);color:var(--warn)}
.ro .ni-items{display:flex;flex-wrap:wrap;gap:5px;margin-top:5px}
.ro .ni-chip{font:600 11px var(--sans);padding:2px 8px;border-radius:999px;background:var(--bg);border:1px solid color-mix(in srgb,var(--warn) 40%,var(--border));color:var(--ink-1);overflow-wrap:anywhere}
.ro .ni-hint{font:11px var(--sans);color:var(--ink-3);margin-top:5px;overflow-wrap:anywhere}
.ro .confline{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-top:12px;font-size:12px;color:var(--ink-2)}
.ro .track{flex:none;height:5px;width:90px;border-radius:3px;background:var(--surface-2);overflow:hidden} .ro .track .fill{height:100%}
.ro .readlist{margin-top:10px}
.ro .readlist .k{font:600 10px var(--sans);text-transform:uppercase;letter-spacing:.05em;color:var(--ink-3)}
.ro .readlist ul{margin:3px 0 0;padding-left:16px;list-style:disc;font-size:12.5px;line-height:1.5;display:grid;gap:4px;overflow-wrap:anywhere}
`;

const READ_LISTS = [
  ["Doesn’t match the form", "mismatches"],
  ["Beyond the template", "bespoke_asks"],
  ["Likely negotiated", "negotiation_points"],
] as const;

export function WhatWasAsked(p: {
  sourceTag: string;
  facts: { label: string; value: string }[];
  priority: { value: string; aiSuggests: string | null; canApply: boolean; applying: boolean; onApply: () => void };
  description: string | null;
  answers: ReactNode;
  missingInfo: string[];
  degraded: { reason: string | null } | null;
  read: { summary?: string; mismatches?: string[]; bespoke_asks?: string[]; negotiation_points?: string[] } | null;
  confidence: { value: number; complexity: string | null } | null;
}) {
  const { priority: pr, read } = p;
  const desc = p.description?.trim();
  const lists = read ? READ_LISTS.filter(([, key]) => read[key]?.length) : [];
  const pct = p.confidence ? Math.round(p.confidence.value * 100) : 0;
  const suggests = pr.aiSuggests && pr.aiSuggests !== pr.value ? pr.aiSuggests : null;
  return (
    <div className="card">
      <div className="ch"><h2>What was asked</h2><span className="cnt">{p.sourceTag}</span></div>
      <div className="cb">
        <div className="facts">
          {p.facts.map((f) => <div key={f.label} className="f"><div className="k">{f.label}</div><div className="v">{f.value}</div></div>)}
          <div className="f">
            <div className="k">Priority</div>
            <div className="v">
              {pr.value}
              {suggests ? <span className="dim"> · AI suggests {suggests}</span> : null}
              {suggests && pr.canApply ? <> — <button type="button" className="sel" disabled={pr.applying} onClick={pr.onApply}>apply</button></> : null}
            </div>
          </div>
        </div>
        {desc ? <div className="quote">{desc}</div> : null}
        <div className="mt-3 empty:hidden">{p.answers}</div>
        {p.missingInfo.length > 0 ? (
          <div className="needsinfo">
            <AlertTriangle className="ic" aria-hidden />
            <div>
              <b>Needs info before drafting</b>
              <div className="ni-items">{p.missingInfo.map((m, i) => <span key={i} className="ni-chip">{m}</span>)}</div>
              <div className="ni-hint">Auto-drafting is held until these are provided — add them to the request or proceed manually.</div>
            </div>
          </div>
        ) : null}
        {p.degraded ? (
          <div className="needsinfo" style={{ borderColor: "var(--warn)" }}>
            <AlertTriangle className="ic" aria-hidden />
            <div>
              <b>AI triage unavailable — review the routing</b>
              <div className="ni-hint">{p.degraded.reason || "This request was auto-classified by keyword rules, not a model reading. Confirm the category and routing before it advances."}</div>
            </div>
          </div>
        ) : null}
        {read && (read.summary || lists.length) ? (
          <>
            <div className="lbl" style={{ margin: "16px 0 0" }}>Aegis read</div>
            {read.summary ? <div style={{ fontSize: 13, lineHeight: 1.5, color: "var(--ink-2)", margin: "6px 0 0", overflowWrap: "anywhere" }}>{read.summary}</div> : null}
            {lists.map(([label, key]) => (
              <div key={key} className="readlist">
                <div className="k" style={key === "mismatches" ? { color: "var(--warn)" } : undefined}>{label}</div>
                <ul>{(read[key] ?? []).map((x, i) => <li key={i}>{x}</li>)}</ul>
              </div>
            ))}
          </>
        ) : null}
        {p.confidence ? (
          <div className="confline">
            <span>AI extraction confidence</span>
            <div className="track"><div className="fill" style={{ width: `${pct}%`, background: p.confidence.value >= 0.8 ? "var(--good)" : "var(--warn)" }} /></div>
            <b>{pct}%</b>{p.confidence.complexity ? <span className="dim"> · {p.confidence.complexity} complexity</span> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
