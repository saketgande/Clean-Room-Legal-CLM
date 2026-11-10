"use client";

// The Overview tab: the request as a labelled sheet, the file it concerns, what just happened and the dates that matter.
// Presentational only; every row is computed in RequestOverview from data the page already holds.

import { CalendarDays, Check, Clock, FileText, History } from "lucide-react";
import { fmtRelative } from "@/lib/utils";
import type { IntakeDocument, RequestActivityItem } from "@/lib/types";

export interface KeyDate { label: string; value: string; tone: "done" | "warn" | "late" | "todo" }

const kb = (n: number) => (n >= 1_048_576 ? `${(n / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

export function OverviewPane(p: {
  rows: { label: string; value: string }[];
  docs: IntakeDocument[];
  activity: RequestActivityItem[];
  keyDates: KeyDate[];
  onTab: (t: "documents" | "activity") => void;
}) {
  const doc = p.docs[0];
  const recent = p.activity.filter((a) => !a.system).slice(0, 4);
  return (
    <div className="ov2">
      <div className="card tint-ind ov2-details">
        <div className="ch"><div className="glyph g-ind"><FileText className="ic" aria-hidden /></div><h2>Request details</h2></div>
        <dl className="kv">
          {p.rows.map((r) => <div key={r.label}><dt>{r.label}</dt><dd>{r.value}</dd></div>)}
        </dl>
      </div>
      <div className="ov2-stack">
        <div className="card tint-sky">
          <div className="ch"><div className="glyph g-sky"><FileText className="ic" aria-hidden /></div><h2>Contract file</h2>
            {p.docs.length > 0 ? <button type="button" className="lnk" onClick={() => p.onTab("documents")}>View all{p.docs.length > 1 ? ` (${p.docs.length})` : ""}</button> : null}</div>
          {doc ? (
            <div className="pv"><span className="pvx">{/pdf/i.test(doc.mime_type) ? "PDF" : "DOC"}</span>
              <span className="pvt"><b title={doc.filename}>{doc.filename}</b><small>{doc.mime_type.split("/").pop()} · {kb(doc.size_bytes)}{doc.created_at ? ` · ${fmtRelative(doc.created_at)}` : ""}</small></span></div>
          ) : <p className="dim ov2-empty">No file attached yet.</p>}
        </div>
        <div className="card tint-emr">
          <div className="ch"><div className="glyph g-emr"><History className="ic" aria-hidden /></div><h2>Latest activity</h2>
            <button type="button" className="lnk" onClick={() => p.onTab("activity")}>View all</button></div>
          {recent.length === 0 ? <p className="dim ov2-empty">Nothing has happened yet.</p> : (
            <ul className="la">
              {recent.map((a) => <li key={a.id}><i data-tone={a.tone} aria-hidden><u /></i><span>{a.sentence}</span><time>{fmtRelative(a.at)}</time></li>)}
            </ul>
          )}
        </div>
        <div className="card tint-amb">
          <div className="ch"><div className="glyph g-amb"><CalendarDays className="ic" aria-hidden /></div><h2>Key dates</h2></div>
          <ul className="kd">
            {p.keyDates.map((d) => <li key={d.label} data-tone={d.tone}><i aria-hidden>{d.tone === "done" ? <Check /> : <Clock />}</i><span>{d.label}</span><b>{d.value}</b></li>)}
          </ul>
        </div>
      </div>
    </div>
  );
}

export const OVERVIEW_PANE_CSS = `
.ro .ov2{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:12px;align-items:start}
.ro .ov2-stack{display:flex;flex-direction:column;gap:12px;min-width:0}
.ro .ov2 .kv{margin:0;padding:4px 14px 12px;display:flex;flex-direction:column}
.ro .ov2 .kv>div{display:grid;grid-template-columns:150px minmax(0,1fr);gap:12px;padding:9px 0;border-bottom:0}
.ro .ov2 .kv dt{font-size:12.5px;color:var(--ink-3)}
.ro .ov2 .kv dd{margin:0;font-size:13.5px;font-weight:550;color:var(--ink);overflow-wrap:anywhere;white-space:pre-line}
.ro .ov2-empty{margin:0;padding:4px 14px 14px;font-size:12.5px}
.ro .ov2 .pv{display:flex;align-items:center;gap:12px;margin:2px 14px 14px;padding:12px;border-radius:12px;background:var(--inset)}
.ro .ov2 .pvx{flex:none;display:grid;place-items:center;width:40px;height:44px;border-radius:10px;font:700 11px var(--sans);background:color-mix(in srgb,var(--c-sky) 16%,transparent);color:var(--c-sky)}
.ro .ov2 .pvt{min-width:0;display:flex;flex-direction:column}
.ro .ov2 .pvt b{font:650 13px var(--sans);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ro .ov2 .pvt small{font-size:11.5px;color:var(--ink-3)}
.ro .ov2 .la,.ro .ov2 .kd{list-style:none;margin:0;padding:0 14px 12px;display:flex;flex-direction:column}
.ro .ov2 .la li{display:grid;grid-template-columns:28px minmax(0,1fr) auto;gap:10px;align-items:center;padding:8px 0;font-size:12.5px;color:var(--ink-2)}
.ro .ov2 .la i{--d:var(--ink-3);display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:color-mix(in srgb,var(--d) 14%,transparent)}
.ro .ov2 .la i u{width:8px;height:8px;border-radius:50%;background:var(--d)}
.ro .ov2 .la i[data-tone=done]{--d:var(--good)}.ro .ov2 .la i[data-tone=stage]{--d:var(--c-amb)}.ro .ov2 .la i[data-tone=ai]{--d:var(--c-vio)}
.ro .ov2 .la time{font-size:11.5px;color:var(--ink-3);white-space:nowrap}
.ro .ov2 .kd li{display:grid;grid-template-columns:18px minmax(0,1fr) auto;gap:10px;align-items:center;padding:8px 0;font-size:12.5px;color:var(--ink-2)}
.ro .ov2 .kd i svg{width:15px;height:15px;color:var(--ink-3)}
.ro .ov2 .kd b{font-weight:650;color:var(--ink);font-variant-numeric:tabular-nums}
.ro .ov2 .kd li[data-tone=done] i svg{color:var(--good)}.ro .ov2 .kd li[data-tone=warn] i svg,.ro .ov2 .kd li[data-tone=warn] b{color:var(--c-amb)}
.ro .ov2 .kd li[data-tone=late] i svg,.ro .ov2 .kd li[data-tone=late] b{color:var(--crit)}
@media (max-width:1100px){.ro .ov2{grid-template-columns:1fr}}
`;
