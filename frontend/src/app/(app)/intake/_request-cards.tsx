"use client";

// Side cards and the discussion cards of the request page. Presentational only: every query, mutation and
// typed string lives in RequestOverview and arrives as props, so a refetch can never drop a draft.

import { useState, type MouseEvent, type RefObject } from "react";
import Link from "next/link";
import { FileText, MessageSquare, PenLine, Send } from "lucide-react";
import type { IntakeDocument, RequestActivity, RequestCounterparty } from "@/lib/types";
import { counterpartySummary, groupActivityByDay } from "@/lib/request-facts";
import { LIFECYCLE } from "@/components/lifecycle-view";
import { fmtDate, fmtDateTime, fmtMoney, titleCase } from "@/lib/utils";

const ini = (n: string) => n.split(/[ /]/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

export function PeopleCard(p: {
  owner: string | null; requester: string | null; department: string | null;
  reassign: { options: { id: string; name: string }[]; value: string; onChange: (v: string) => void; onReassign: () => void; busy: boolean } | null;
}) {
  const r = p.reassign;
  return (
    <div className="card"><div className="ch"><h2>Owner and people</h2></div><div className="cb">
      <div className="person"><div className="av">{ini(p.owner ?? "?")}</div><div><div className="n">{p.owner ?? "Unassigned"}</div><div className="r">owner</div></div></div>
      {r ? (
        <div className="reassign">
          <select className="sel" aria-label="New owner" value={r.value} onChange={(e) => r.onChange(e.target.value)}>
            <option value="">Pick a new owner…</option>
            {r.options.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <button className="btn wide" disabled={r.busy || !r.value} onClick={r.onReassign}>Reassign owner</button>
        </div>
      ) : null}
      <div className="person"><div className="av">{ini(p.requester ?? "R")}</div><div><div className="n">{p.requester ?? "Requester"}</div><div className="r">requester</div></div></div>
      {p.department ? <div className="person"><div className="av">{ini(p.department)}</div><div><div className="n">{p.department}</div><div className="r">department</div></div></div> : null}
    </div></div>
  );
}

export function DocumentsCard(p: {
  docs: IntakeDocument[]; contractHref: string | null;
  contractActions: { docType: string; hasAttachment: boolean; busy: boolean; onIngest: () => void; onDraft: () => void } | null;
  trademark: { busy: boolean; onContinue: () => void } | null;
}) {
  const a = p.contractActions;
  return (
    <div className="card"><div className="ch"><h2>Documents</h2></div><div className="cb">
      {p.docs.length === 0 ? <div className="dim" style={{ fontSize: 12 }}>No documents attached.</div> :
        p.docs.map((d) => (
          <div key={d.id} className="docrow"><div className={`x ${/pdf/i.test(d.mime_type) ? "pdf" : "doc"}`}>{/pdf/i.test(d.mime_type) ? "PDF" : "DOC"}</div>
            <div style={{ flex: 1, minWidth: 0 }}><div className="n" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.filename}</div><div className="m">{d.extracted_chars > 0 ? "parsed" : "attached"}</div></div></div>
        ))}
      {a || p.trademark || p.contractHref ? (
        <div className="docacts">
          {a ? (a.hasAttachment ? (
            <>
              <button className="btn pri wide" disabled={a.busy} onClick={a.onIngest}>Use attached as the {a.docType} →</button>
              <button className="btn wide" disabled={a.busy} onClick={a.onDraft}>Approve &amp; draft fresh</button>
            </>
          ) : <button className="btn pri wide" disabled={a.busy} onClick={a.onDraft}>Approve &amp; draft the {a.docType} →</button>) : null}
          {p.trademark ? <button className="btn pri wide" disabled={p.trademark.busy} onClick={p.trademark.onContinue}>Continue as Trademark →</button> : null}
          {p.contractHref ? <Link href={p.contractHref} className="btn wide"><PenLine className="ic" aria-hidden />Open in CLM editor →</Link> : null}
        </div>
      ) : null}
    </div></div>
  );
}

const CHIPS = ["What are the key risks here?", "What obligations does this create?", "How does this compare to our standard terms?"];

export function AskAegisCard(p: {
  scope: "this contract" | "portfolio"; chat: { role: "you" | "ai"; text: string; cites?: number }[];
  pending: boolean; q: string; onQ: (v: string) => void; onSend: () => void; onAsk: (question: string) => void;
}) {
  return (
    <div className="card"><div className="ch"><MessageSquare className="ic" aria-hidden /><h2>Ask Aegis</h2><span className="cnt">{p.scope}</span></div>
      <div className="cb chat">
        <div className="log">
          {p.chat.length === 0 ? (
            <div className="chips">{CHIPS.map((s) => <button key={s} className="chip" disabled={p.pending} onClick={() => p.onAsk(s)}>{s}</button>)}</div>
          ) : p.chat.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              <div className="bub">{m.text}{m.role === "ai" && m.cites ? <span className="cites">· {m.cites} source{m.cites === 1 ? "" : "s"}</span> : null}</div>
            </div>
          ))}
          {p.pending ? <div className="msg ai"><div className="bub dim">Thinking…</div></div> : null}
        </div>
        <div className="composer">
          <input className="cin" value={p.q} aria-label="Ask a question" placeholder="Ask a question…" onChange={(e) => p.onQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") p.onSend(); }} />
          <button className="btn pri" aria-label="Send" disabled={!p.q.trim() || p.pending} onClick={p.onSend}><Send className="ic" aria-hidden /></button>
        </div>
      </div></div>
  );
}

export function CommentsCard(p: {
  comments: { actor: string; at: string; text: string; kind: string; stepLabel: string | null }[];
  composer: { value: string; onChange: (v: string) => void; onPost: () => void; posting: boolean; inputRef: RefObject<HTMLInputElement | null> } | null;
}) {
  const c = p.composer;
  return (
    <div className="card wfcomments"><div className="ch"><h2>Comments and questions</h2></div><div className="cb">
      {p.comments.length === 0 ? <div className="dim" style={{ fontSize: 12.5 }}>No comments yet.</div> :
        <ul className="clist">{p.comments.map((m, i) => (
          <li key={i} className={m.kind === "return" ? "ret" : ""}>
            <div className="cmeta"><b>{m.actor}</b><span className="dim"> · {fmtDateTime(m.at)}</span>{m.stepLabel ? <span className="dim"> · on {m.stepLabel}</span> : null}{m.kind === "return" ? <span className="cret"> sent back</span> : null}{m.kind === "reply" ? <span className="cret"> replied to the rejection</span> : null}</div>
            <div className="ctext">{m.text}</div>
          </li>
        ))}</ul>}
      {c ? (
        <div className="composer">
          <input className="cin" ref={c.inputRef} value={c.value} aria-label="Add a comment or question" placeholder="Add a comment or question…" onChange={(e) => c.onChange(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && c.value.trim()) c.onPost(); }} />
          <button className="btn" disabled={!c.value.trim() || c.posting} onClick={c.onPost}>Post</button>
        </div>
      ) : null}
    </div></div>
  );
}

const BATCH = 20;
const time = (at: string) => { const d = new Date(at); return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); };

export function ActivityCard(p: { status: "loading" | "error" | "ready"; data: RequestActivity | undefined; onRetry: () => void }) {
  const [showSystem, setShowSystem] = useState(false);
  const [limit, setLimit] = useState(BATCH);
  const items = p.status === "ready" && p.data ? p.data.items : [];
  const hidden = items.filter((i) => i.system).length;
  const visible = items.filter((i) => showSystem || !i.system);
  const groups = groupActivityByDay(visible.slice(0, limit));
  const toggle = hidden > 0 ? <button type="button" className="lnk" onClick={() => setShowSystem((v) => !v)}>{showSystem ? "Hide system details" : `Show system details (${hidden})`}</button> : null;
  return (
    <div className="card"><div className="ch"><h2>Activity</h2>{items.some((i) => i.source === "contract") ? <span className="chsub">from this request and its contract</span> : null}</div><div className="cb">
      {p.status === "loading" ? <div className="dim" style={{ fontSize: 12.5 }}>Loading activity…</div>
        : p.status === "error" ? <div className="dim" style={{ fontSize: 12.5 }}>Activity could not be loaded. <button type="button" className="lnk" onClick={p.onRetry}>Retry</button></div>
        : (
          <>
            {visible.length === 0 ? <div className="dim" style={{ fontSize: 12.5 }}>No activity yet.</div> : (
              <div className="actv">
                {groups.map((g) => (
                  <div key={g.day + g.items[0].id}>
                    <div className="day">{g.day}</div>
                    {g.items.map((i) => (
                      <div key={i.id} className="ev"><i className={i.tone} aria-hidden /><span><span className="sent">{i.sentence}</span> <span className="who">· {i.who}</span></span><small>{time(i.at)}</small></div>
                    ))}
                  </div>
                ))}
              </div>
            )}
            <div className="actfoot">
              {visible.length > limit ? <button type="button" className="lnk" onClick={() => setLimit((n) => n + BATCH)}>Show earlier activity</button> : null}
              {toggle}
              {p.data?.truncated && visible.length <= limit ? <span className="dim">Older activity is not listed here.</span> : null}
            </div>
          </>
        )}
    </div></div>
  );
}

const FIRST = 5;
const stageLabel = (c: RequestCounterparty["contracts"][number]) => (c.ended ? "Ended" : LIFECYCLE.find((s) => s.key === c.stage)?.label ?? titleCase(c.stage));

export function CounterpartyCard(p: {
  status: "loading" | "error" | "ready"; data: RequestCounterparty | undefined; parties: { name: string }[];
  onRetry: () => void; onOpenRequest: (id: string) => void;
}) {
  const [moreC, setMoreC] = useState(false);
  const [moreR, setMoreR] = useState(false);
  if (p.status === "loading") return null;
  if (p.status === "error") {
    return <div className="card"><div className="cb dim" style={{ fontSize: 12.5 }}>Counterparty files could not be loaded. <button type="button" className="lnk" onClick={p.onRetry}>Retry</button></div></div>;
  }
  const d = p.data;
  if (!d || !d.name) return null;
  const summary = counterpartySummary(d.summary);
  const cs = moreC ? d.contracts : d.contracts.slice(0, FIRST);
  const rs = moreR ? d.requests : d.requests.slice(0, FIRST);
  const open = (id: string) => (e: MouseEvent) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    p.onOpenRequest(id);
  };
  const any = d.contracts.length + d.requests.length > 0;
  return (
    <div className="card"><div className="ch"><h2>Counterparty</h2></div><div className="cb cpbody">
      <div className="cpname">{d.name}</div>
      {summary ? <div className="cpstat">{summary}</div> : null}
      {p.parties.length > 1 ? <div className="dim cpall">All parties: {p.parties.map((x) => x.name).join(", ")}</div> : null}
      {any ? (
        <>
          <div className="cph">Contracts</div>
          {d.contracts.length === 0 ? <div className="dim cpnone">No other contracts on file you can see</div> : cs.map((c) => (
            <div key={c.id} className="cpfile"><FileText className="ic" aria-hidden />
              <div><Link href={`/contracts/${c.id}`}>{c.title}</Link>
                <div className="cpmeta"><span className="cppill">{stageLabel(c)}</span>{c.contract_type ? <span>{titleCase(c.contract_type)}</span> : null}{c.value != null ? <span>{fmtMoney(c.value, c.currency)}</span> : null}<span>{fmtDate(c.created_at)}</span></div></div></div>
          ))}
          {d.contracts.length > FIRST && !moreC ? <button type="button" className="lnk" onClick={() => setMoreC(true)}>Show {d.contracts.length - FIRST} more</button> : null}
          <div className="cph">Earlier requests</div>
          {d.requests.length === 0 ? <div className="dim cpnone">No earlier requests you can see</div> : rs.map((r) => (
            <div key={r.id} className="cpfile"><FileText className="ic" aria-hidden />
              <div><a href={`/intake?open=${r.id}`} onClick={open(r.id)}>{r.ref} · {r.type_label}</a>
                <div className="cpmeta"><span>{titleCase(r.status)}</span><span>{fmtDate(r.date)}</span></div></div></div>
          ))}
          {d.requests.length > FIRST && !moreR ? <button type="button" className="lnk" onClick={() => setMoreR(true)}>Show {d.requests.length - FIRST} more</button> : null}
        </>
      ) : null}
      {d.note ? <div className="dim cpnote">{d.note}</div> : null}
    </div></div>
  );
}

export const CARDS_CSS = `
.ro .glance .row{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);font-size:12.5px} .ro .glance .row:last-child{border-bottom:0}
.ro .glance .k{color:var(--ink-3);flex:none} .ro .glance .v{font-weight:600;text-align:right;min-width:0;overflow-wrap:anywhere}
.ro .person{display:flex;align-items:center;gap:9px;padding:8px 0;min-width:0}
.ro .person .av{width:28px;height:28px;border-radius:50%;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;font-weight:700;font-size:11px;flex:none}
.ro .person .n{font-weight:600;font-size:12.5px;overflow-wrap:anywhere} .ro .person .r{font-size:11px;color:var(--ink-3)}
.ro .reassign{display:flex;flex-direction:column;gap:8px;margin:2px 0 8px}
.ro .docrow{display:flex;align-items:center;gap:9px;padding:9px 0;border-bottom:1px solid var(--border)} .ro .docrow:last-of-type{border-bottom:0}
.ro .docrow .x{width:28px;height:28px;border-radius:7px;display:grid;place-items:center;font:700 8.5px var(--sans);flex:none}
.ro .docrow .x.pdf{background:var(--crit-soft);color:var(--crit)} .ro .docrow .x.doc{background:var(--accent-soft);color:var(--accent)}
.ro .docrow .n{font-weight:600;font-size:12.5px} .ro .docrow .m{font-size:11px;color:var(--ink-3)}
.ro .docacts{display:flex;flex-direction:column;gap:8px;margin-top:12px}
.ro .wfcomments .clist{list-style:none;margin:0 0 10px;padding:0;display:flex;flex-direction:column;gap:9px}
.ro .wfcomments .clist li{padding:8px 11px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2)} .ro .wfcomments .clist li.ret{border-color:color-mix(in srgb,var(--warn) 34%,var(--border));background:var(--warn-soft)}
.ro .wfcomments .cmeta{font-size:11px;color:var(--ink-2);margin-bottom:3px} .ro .wfcomments .cmeta b{color:var(--ink)} .ro .wfcomments .cret{color:var(--warn);font-weight:600}
.ro .wfcomments .ctext{font-size:12.5px;color:var(--ink);line-height:1.55;white-space:pre-wrap;overflow-wrap:anywhere}
.ro .wfcomments .cb>.composer{margin-top:10px}
.ro .lnk{background:none;border:0;padding:0;color:var(--accent);font:600 12px var(--sans);cursor:pointer;text-align:left} .ro .lnk:hover{text-decoration:underline}
.ro .chsub{font-size:11px;color:var(--ink-3);margin-left:auto}
.ro .actv .day{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);padding:8px 0 4px}
.ro .actv .ev{display:flex;gap:10px;padding:5px 0;font-size:12.5px;overflow-wrap:anywhere}
.ro .actv .ev i{width:9px;height:9px;border-radius:50%;background:var(--border);flex:none;margin-top:5px}
.ro .actv .ev i.ai{background:var(--accent)} .ro .actv .ev i.done{background:var(--good)} .ro .actv .ev i.stage{background:var(--warn)}
.ro .actv .ev .who{color:var(--ink-3);font-size:12px} .ro .actv .ev small{color:var(--ink-3);font-size:11px;margin-left:auto;white-space:nowrap;padding-left:8px}
.ro .actfoot{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:8px;font-size:12px}
.ro .cpbody{display:flex;flex-direction:column;gap:2px;min-width:0}
.ro .cpname{font-size:14px;font-weight:600;overflow-wrap:anywhere} .ro .cpstat{font-size:12px;color:var(--ink-3);margin-bottom:4px}
.ro .cpall,.ro .cpnone,.ro .cpnote{font-size:12px;overflow-wrap:anywhere} .ro .cpnote{margin-top:8px}
.ro .cph{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);padding:8px 0 2px}
.ro .cpfile{display:flex;gap:8px;align-items:flex-start;padding:7px 0;border-top:1px solid var(--border);font-size:12.5px;min-width:0}
.ro .cpfile .ic{width:16px;height:16px;flex:none;margin-top:2px;color:var(--ink-3)}
.ro .cpfile a{color:var(--accent);font-weight:500;text-decoration:none;display:block;overflow-wrap:anywhere} .ro .cpfile a:hover,.ro .cpfile a:focus-visible{text-decoration:underline}
.ro .cpmeta{display:flex;flex-wrap:wrap;gap:2px 8px;align-items:center;font-size:11.5px;color:var(--ink-3);margin-top:1px}
.ro .cppill{padding:0 8px;border-radius:99px;background:var(--accent-soft);color:var(--accent);font-weight:600}
.ro .chat{display:flex;flex-direction:column;gap:10px}
.ro .chat .log{display:flex;flex-direction:column;gap:9px;max-height:320px;overflow-y:auto}
.ro .chat .chips{display:flex;flex-direction:column;gap:6px}
.ro .chat .chip{text-align:left;padding:7px 11px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:var(--ink-2);font:500 12px var(--sans);cursor:pointer}
.ro .chat .chip:hover{background:var(--accent-soft);color:var(--accent);border-color:color-mix(in srgb,var(--accent) 30%,var(--border))}
.ro .chat .msg{display:flex} .ro .chat .msg.you{justify-content:flex-end}
.ro .chat .bub{max-width:82%;padding:9px 12px;border-radius:12px;font-size:12.5px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}
.ro .chat .msg.you .bub{background:var(--accent);color:var(--accent-ink);border-bottom-right-radius:4px}
.ro .chat .msg.ai .bub{background:var(--surface-2);color:var(--ink);border:1px solid var(--border);border-bottom-left-radius:4px}
.ro .chat .bub.dim{color:var(--ink-3)} .ro .chat .cites{margin-left:6px;font-size:10.5px;color:var(--ink-3)}
.ro .chat .composer{display:flex;gap:8px} .ro .chat .cin{flex:1;min-width:0}
.ro .chat .composer .btn.pri{padding:9px 12px} .ro .chat .composer .btn .ic{margin:0}
`;
