// "What was asked" — the requester's words, the form facts, the answers editor, the notices and the AI read.
// The description is shown as written; the old "— {source} by {requester}" line is system text and is gone.
import type { ReactNode } from "react";
import { AlertTriangle, Building2, CalendarRange, ClipboardList, FileText, Flag, GitCompare, Handshake, Quote, Tag, Wallet, type LucideIcon } from "lucide-react";
import { AiMark } from "@/components/lifecycle-view";

export const ASKED_CSS = `
.ro .card.asked .facts{grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:10px}
.ro .card.asked .facts .f{padding:11px 13px;border:0;border-radius:12px;background:color-mix(in srgb,var(--t) 9%,var(--surface))}
.ro .card.asked .facts .f .k{display:flex;align-items:center;gap:6px;font:700 10.5px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--t)}
.ro .card.asked .facts .f .k svg{width:14px;height:14px}
.ro .card.asked .facts .f .v{margin-top:5px;font-size:15px;font-weight:700;line-height:1.35;color:var(--ink);overflow-wrap:anywhere}
.ro .qwrap{margin-top:14px;padding:12px 14px;border-radius:12px;background:var(--inset)}
.ro .qhead{display:flex;align-items:center;gap:6px;font:700 10.5px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--c-ind)}
.ro .qhead svg{width:14px;height:14px}
.ro .qwrap .quote{margin:6px 0 0;padding:0;border:0;background:none;max-height:12em;font-size:14px;line-height:1.65;color:var(--ink)}
.ro .airead{--ai:var(--accent);margin-top:14px;padding:14px;border-radius:14px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border)}
.ro .aihead{display:flex;align-items:center;gap:9px}
.ro .aitile{display:grid;place-items:center;width:30px;height:30px;border-radius:10px;background:var(--ai);color:#fff}
.ro .aitile svg{width:18px;height:18px}
.ro .aihead b{font:700 14px var(--sans);color:var(--ink)}
.ro .aihead em{font:700 10px var(--sans);font-style:normal;letter-spacing:.06em;text-transform:uppercase;padding:2px 8px;border-radius:99px;background:color-mix(in srgb,var(--ai) 14%,transparent);color:var(--ai)}
.ro .aisum{margin:10px 0 0;font-size:14px;line-height:1.65;color:var(--ink);overflow-wrap:anywhere}
.ro .rlgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin-top:12px}
.ro .rl{padding:10px 12px;border-radius:12px;background:var(--surface);box-shadow:inset 3px 0 0 var(--t)}
.ro .rlh{display:flex;align-items:center;gap:6px;font:700 11.5px var(--sans);color:var(--t)}
.ro .rlh svg{width:14px;height:14px}
.ro .rlh span{margin-left:auto;font-size:10.5px;padding:1px 7px;border-radius:99px;background:color-mix(in srgb,var(--t) 14%,transparent)}
.ro .rl ul{margin:7px 0 0;padding-left:16px;list-style:disc;display:grid;gap:5px;font-size:13px;line-height:1.5;color:var(--ink-2);overflow-wrap:anywhere}
.ro .confline{padding:9px 12px;border-radius:12px;background:var(--inset)}
.ro .track{height:7px;width:120px}

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

// Each fact gets its own icon and tint so the row scans at a glance instead of reading as grey small print.
const FACT_LOOK: [RegExp, LucideIcon, string][] = [
  [/counterparty|party|vendor|supplier|customer/i, Building2, "var(--c-ind)"],
  [/type|kind|agreement/i, FileText, "var(--c-vio)"],
  [/value|amount|fee|price|spend/i, Wallet, "var(--c-sky)"],
  [/term|date|start|end|renew|duration/i, CalendarRange, "var(--c-emr)"],
];
const lookOf = (label: string) => FACT_LOOK.find(([re]) => re.test(label)) ?? [/./, Tag, "var(--c-ind)"] as [RegExp, LucideIcon, string];

const READ_LISTS = [
  ["Doesn’t match the form", "mismatches", AlertTriangle, "var(--c-amb)"],
  ["Beyond the template", "bespoke_asks", GitCompare, "var(--c-vio)"],
  ["Likely negotiated", "negotiation_points", Handshake, "var(--c-sky)"],
] as const;

export function WhatWasAsked(p: {
  /** "all" is the whole card; "main" is only what needs a person (answers, missing info, triage warning) without the card chrome; "ai" is the AI read and its confidence. */
  part?: "all" | "main" | "ai";
  sourceTag: string;
  facts: { label: string; value: string }[];
  priority: { value: string; aiSuggests: string | null; canApply: boolean; applying: boolean; onApply: () => void };
  description: string | null;
  answers: ReactNode;
  summary?: ReactNode;
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
  const part = p.part ?? "all";
  if (part === "main") {
    return (
      <div className="askmain empty:hidden">
        <div className="empty:hidden">{p.answers}</div>
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
      </div>
    );
  }
  return (
    <div className="card tint-ind asked">
      <div className="ch"><div className="glyph g-ind"><ClipboardList className="ic" aria-hidden /></div><h2>{part === "ai" ? "AI analysis" : "What was asked"}</h2><span className="cnt">{p.sourceTag}</span></div>
      <div className="cb">{part === "ai" ? null : <p className="hint">The request as it was filed.</p>}
        {part === "ai" ? null : <div className="facts">
          {p.facts.map((f) => { const [, I, c] = lookOf(f.label); return <div key={f.label} className="f" style={{ "--t": c } as React.CSSProperties}><div className="k"><I aria-hidden />{f.label}</div><div className="v">{f.value}</div></div>; })}
          <div className="f" style={{ "--t": "var(--c-amb)" } as React.CSSProperties}>
            <div className="k"><Flag aria-hidden />Priority</div>
            <div className="v">
              {pr.value}
              {suggests ? <span className="dim"> · AI suggests {suggests}</span> : null}
              {suggests && pr.canApply ? <> — <button type="button" className="sel" disabled={pr.applying} onClick={pr.onApply}>apply</button></> : null}
            </div>
          </div>
        </div>}
        {part === "ai" ? null : desc ? <div className="qwrap"><div className="qhead"><Quote aria-hidden />In the requester’s words</div><div className="quote">{desc}</div></div> : null}
        {part === "ai" ? null : <div className="mt-3 empty:hidden">{p.answers}</div>}
        {p.summary}
        {part !== "ai" && p.missingInfo.length > 0 ? (
          <div className="needsinfo">
            <AlertTriangle className="ic" aria-hidden />
            <div>
              <b>Needs info before drafting</b>
              <div className="ni-items">{p.missingInfo.map((m, i) => <span key={i} className="ni-chip">{m}</span>)}</div>
              <div className="ni-hint">Auto-drafting is held until these are provided — add them to the request or proceed manually.</div>
            </div>
          </div>
        ) : null}
        {part !== "ai" && p.degraded ? (
          <div className="needsinfo" style={{ borderColor: "var(--warn)" }}>
            <AlertTriangle className="ic" aria-hidden />
            <div>
              <b>AI triage unavailable — review the routing</b>
              <div className="ni-hint">{p.degraded.reason || "This request was auto-classified by keyword rules, not a model reading. Confirm the category and routing before it advances."}</div>
            </div>
          </div>
        ) : null}
        {read && (read.summary || lists.length) ? (
          <div className="airead">
            <div className="aihead"><span className="aitile"><AiMark /></span><b>Aegis read</b><em>AI summary</em></div>
            {read.summary ? <p className="aisum">{read.summary}</p> : null}
            {lists.length ? (
              <div className="rlgrid">
                {lists.map(([label, key, I, c]) => (
                  <div key={key} className="rl" style={{ "--t": c } as React.CSSProperties}>
                    <div className="rlh"><I aria-hidden />{label}<span>{(read[key] ?? []).length}</span></div>
                    <ul>{(read[key] ?? []).map((x, i) => <li key={i}>{x}</li>)}</ul>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
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
