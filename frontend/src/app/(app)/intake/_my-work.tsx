"use client";

// My Work board — a port of Screen 2: the "Your day" briefing, assigned-task
// cards grouped by urgency, and the "Requests you raised" table. Wired to the
// real intake API (myWork + mine). Scoped under `.mw` (reuses the `.li-board`
// palette/brief/tiles/btn from the shell's injected CSS).

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { intakeApi } from "@/lib/endpoints";
import { CenterSpinner, ErrorState } from "@/components/ui";
import type { IntakeRequest, IntakeTask } from "@/lib/types";

const POLL = { refetchInterval: 15_000 } as const;

const isOpenReq = (r: IntakeRequest) => r.status !== "closed" && r.status !== "approved";
function counterpartyOf(r: IntakeRequest): string | null {
  const parties = (r.parties ?? []) as { name?: string; role?: string }[];
  const cp = parties.find((p) => /counter|vendor|supplier|opposing|third|other/i.test(p.role ?? "")) ?? parties[0];
  if (cp?.name) return cp.name;
  const fv = (r.field_values ?? {}) as Record<string, unknown>;
  for (const k of ["counterparty", "company", "vendor", "party"]) if (typeof fv[k] === "string" && fv[k]) return fv[k] as string;
  return null;
}
const isMsa = (s: string) => /msa|master|service/i.test(s);
function riskOf(r: IntakeRequest): "high" | "med" | "low" {
  if (r.sla_status === "overdue" || r.priority === "Critical") return "high";
  if (r.sla_status === "at_risk" || r.priority === "High") return "med";
  return "low";
}
function urgency(r: IntakeRequest): "over" | "soon" | "later" {
  if (r.sla_status === "overdue") return "over";
  if (r.sla_status === "at_risk") return "soon";
  return "later";
}
function dueLabel(r: IntakeRequest): string {
  if (r.sla_status === "overdue") return "overdue";
  if (r.sla_status === "at_risk") return "due soon";
  return `${Math.max(0, Math.round(100 - (r.sla_pct ?? 0)))}% SLA left`;
}
function stageOf(r: IntakeRequest): { name: string; done: number; total: number } | null {
  const steps = r.workflow ?? [];
  if (!steps.length) return null;
  const done = steps.filter((s) => s.done).length;
  const active = steps.find((s) => s.active);
  return { name: active?.label ?? "In progress", done, total: steps.length };
}

function AssignedCard({ r, onOpen }: { r: IntakeRequest; onOpen: (id: string) => void }) {
  const cp = counterpartyOf(r);
  const ask = (r.subject || r.description || "").split("\n")[0].trim() || r.type_label;
  const st = stageOf(r);
  const risk = riskOf(r);
  const u = urgency(r);
  return (
    <div className={`tcard ${u === "over" ? "over" : ""}`}>
      <div className={`ticon ${isMsa(r.type_label) ? "approval" : "review"}`}>
        <svg className="ic" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4-4" /></svg>
      </div>
      <div className="tmid">
        <div className="tmatter"><span className="cp" onClick={() => onOpen(r.id)}>{cp ?? r.type_label}</span><span className="ref">{r.ref}</span></div>
        <div className="ask">{ask}</div>
        <div className="tdetail">{r.type_label}{r.requester_name ? ` · from ${r.requester_name}` : ""}</div>
        <div className="tmeta">
          {st ? <span className={`chip wf ${isMsa(r.type_label) ? "msa" : ""}`}>{st.name} · {st.done}/{st.total}</span> : <span className="chip">{r.stage}</span>}
          <span className={`chip risk-${risk}`}>{risk} priority</span>
        </div>
      </div>
      <div className="tright">
        <span className={`due ${u === "over" ? "over" : u === "soon" ? "today" : ""}`}>{dueLabel(r)}</span>
        <div className="actions"><button className="btn pri sm" onClick={() => onOpen(r.id)}>Open</button></div>
      </div>
    </div>
  );
}

function RaisedRow({ r, i, onOpen }: { r: IntakeRequest; i: number; onOpen: (id: string) => void }) {
  const cp = counterpartyOf(r);
  const st = stageOf(r);
  const stuck = r.sla_status === "overdue";
  const status = r.status === "closed" || r.status === "approved" ? "done" : stuck ? "stuck" : (r.workflow ?? []).some((s) => s.active) ? "progress" : "input";
  const statlbl = status === "done" ? "executed" : status === "stuck" ? "overdue" : status === "progress" ? "in progress" : "awaiting triage";
  const segs = st ? Array.from({ length: st.total }, (_, k) => {
    const cls = k < st.done - 1 ? "done" : k === st.done - 1 ? "cur" : "";
    return <span key={k} className={`sseg ${cls}${cls && stuck ? " st" : ""}`} />;
  }) : null;
  return (
    <tr onClick={() => onOpen(r.id)}>
      <td className="c-idx">{i + 1}</td>
      <td><div className="req"><div className={`rico ${isMsa(r.type_label) ? "MSA" : "NDA"}`}>
        <svg className="ic" viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></svg></div>
        <div style={{ minWidth: 0 }}><div style={{ display: "flex", alignItems: "center", gap: 7 }}><span className="nm">{(r.subject || r.type_label)}</span><span className="ref">{r.ref}</span></div>
        <div className="dim" style={{ fontSize: 11.5 }}>{r.type_label}</div></div></div></td>
      <td><div style={{ fontWeight: 600 }}>{cp ?? "—"}</div></td>
      <td className="stagecell">{st ? <><div className="stagenm">{st.name}<span className="cnt">{st.done}/{st.total}</span></div><div className="sbar">{segs}</div></> : <span className="dim">not started</span>}</td>
      <td><span className={`rstat ${status}`}>{statlbl}</span></td>
      <td><button className="btn sm" onClick={(e) => { e.stopPropagation(); onOpen(r.id); }}>Open →</button></td>
    </tr>
  );
}

export function MyWorkBoard({ onOpen }: { onOpen: (id: string) => void }) {
  const { data: mw, isLoading: mwLoading, error: mwError } = useQuery({ queryKey: ["intake-mywork"], queryFn: intakeApi.myWork, ...POLL });
  const { data: mineData, isLoading: mineLoading } = useQuery({ queryKey: ["intake-mine"], queryFn: intakeApi.mine, ...POLL });

  const assigned = useMemo(() => {
    const seen = new Set<string>();
    return [...(mw?.awaiting_review ?? []), ...(mw?.my_tickets ?? [])].filter((r) => { if (seen.has(r.id)) return false; seen.add(r.id); return true; });
  }, [mw]);
  const raised = useMemo(() => (mineData ?? []).filter((r) => true), [mineData]);
  const tasks = mw?.my_tasks ?? [];

  const over = assigned.filter((r) => urgency(r) === "over");
  const soon = assigned.filter((r) => urgency(r) === "soon");
  const later = assigned.filter((r) => urgency(r) === "later");
  const onDesk = assigned.length + tasks.length;
  const starter = over[0] ?? soon[0] ?? assigned[0];
  const doneRecent = raised.filter((r) => r.status === "approved" || r.status === "closed").length;

  const tiles: { n: number; l: string; c?: string }[] = [
    { n: onDesk, l: "on your desk", c: "c" },
    { n: over.length, l: "overdue", c: "c" },
    { n: soon.length, l: "due soon", c: "w" },
    { n: tasks.length, l: "tasks" },
    { n: raised.length, l: "raised by you" },
  ];

  const group = (label: string, list: IntakeRequest[], cls: string) => list.length ? (
    <>
      <div className={`grouphd ${cls}`}>{label}<span className="gc">{list.length}</span></div>
      {list.map((r) => <AssignedCard key={r.id} r={r} onOpen={onOpen} />)}
    </>
  ) : null;

  const rstatOrder: Record<string, number> = { stuck: 0, input: 1, progress: 2, done: 3 };
  const raisedSorted = [...raised].sort((a, b) => {
    const s = (r: IntakeRequest) => r.status === "closed" || r.status === "approved" ? "done" : r.sla_status === "overdue" ? "stuck" : (r.workflow ?? []).some((x) => x.active) ? "progress" : "input";
    return rstatOrder[s(a)] - rstatOrder[s(b)];
  });

  if ((mwLoading || mineLoading) && !mw && !mineData)
    return <CenterSpinner label="Loading your work…" />;
  if (mwError) return <ErrorState error={mwError} />;

  return (
    <div className="li-board mw">
      <style dangerouslySetInnerHTML={{ __html: MW_CSS }} />

      {/* briefing */}
      <div className="brief">
        <div className="briefhd"><div className="aiglyph">✦</div><div><b>Your day</b> <span className="when">· AI read your desk · updated just now</span></div></div>
        <div className="briefbody">
          <p className="summary">
            <b>{onDesk} items on your desk.</b>{" "}
            {over.length ? <><span className="c">{over.length} overdue</span>, </> : null}{soon.length} due soon.{" "}
            {starter ? <>Start with <b>{starter.ref}</b> — {(starter.subject || starter.type_label)}. </> : null}
            You&rsquo;ve raised <b>{raised.length}</b>{doneRecent ? <>, {doneRecent} executed</> : null}.
          </p>
          <div className="tiles">
            {tiles.map((t) => <div key={t.l} className={`tile ${t.c ?? ""}`}><div className="tn num">{t.n}</div><div className="tl">{t.l}</div></div>)}
          </div>
          {starter ? (
            <div className="starthere">
              <div className="bolt"><svg className="ic" viewBox="0 0 24 24"><path d="M13 2L4 14h7l-2 8 9-12h-7z" /></svg></div>
              <div className="st-t"><span className="lbl">Start here</span> · <b>{starter.ref} — {counterpartyOf(starter) ?? starter.type_label}</b><br />{(starter.subject || starter.type_label)} · <span className={`due ${urgency(starter) === "over" ? "over" : urgency(starter) === "soon" ? "today" : ""}`}>{dueLabel(starter)}</span></div>
              <span style={{ flex: 1 }} /><button className="btn pri" onClick={() => onOpen(starter.id)}>Open</button>
            </div>
          ) : null}
        </div>
      </div>

      {/* assigned */}
      {assigned.length === 0 && tasks.length === 0 ? (
        <div className="grouphd" style={{ marginTop: 22 }}>Nothing on your desk — inbox zero 🎉</div>
      ) : (
        <>
          {group("Overdue", over, "crit")}
          {group("Due soon", soon, "warn")}
          {group("Later", later, "")}
          {tasks.length ? (
            <>
              <div className="grouphd">My tasks<span className="gc">{tasks.length}</span></div>
              {tasks.map((t: IntakeTask) => (
                <div key={t.id} className="tcard">
                  <div className="ticon info"><svg className="ic" viewBox="0 0 24 24"><path d="M9 11l3 3 8-8" /><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9" /></svg></div>
                  <div className="tmid"><div className="ask">{t.title}</div>{t.description ? <div className="tdetail">{t.description}</div> : null}<div className="tmeta"><span className="chip">{t.status}</span></div></div>
                  <div className="tright"><div className="actions"><button className="btn sm">{t.status === "done" ? "Done" : "Mark done"}</button></div></div>
                </div>
              ))}
            </>
          ) : null}
        </>
      )}

      {/* raised */}
      <div className="grouphd" style={{ marginTop: 26 }}>Requests you raised<span className="gc">{raised.length}</span></div>
      {raised.length === 0 ? (
        <p className="raisedsum">You haven&rsquo;t raised any requests yet.</p>
      ) : (
        <div className="rtablewrap">
          <table className="rt">
            <thead><tr><th className="c-idx">#</th><th>Request</th><th>Counterparty</th><th>Stage / progress</th><th>Status</th><th /></tr></thead>
            <tbody>{raisedSorted.map((r, i) => <RaisedRow key={r.id} r={r} i={i} onOpen={onOpen} />)}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const MW_CSS = `
.mw .tiles{grid-template-columns:repeat(5,1fr)}
.mw .starthere{display:flex;align-items:center;gap:11px;padding:11px 13px;border:1px solid color-mix(in srgb,var(--accent) 35%,var(--border));background:var(--accent-soft);border-radius:11px}
.mw .starthere .bolt{width:26px;height:26px;border-radius:7px;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;flex:none}
.mw .starthere .st-t{font-size:12.5px;color:var(--ink)} .mw .starthere .st-t b{font-weight:680} .mw .starthere .lbl{color:var(--accent);font-weight:700}
.mw .grouphd{display:flex;align-items:center;gap:9px;margin:22px 2px 10px;font:600 11px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.mw .grouphd .gc{font:700 10px var(--mono);background:var(--surface-2);color:var(--ink-2);border-radius:99px;padding:1px 7px}
.mw .grouphd.crit{color:var(--crit)} .mw .grouphd.crit .gc{background:var(--crit-soft);color:var(--crit)}
.mw .grouphd.warn{color:var(--warn)} .mw .grouphd.warn .gc{background:var(--warn-soft);color:var(--warn)}
.mw .tcard{display:grid;grid-template-columns:38px 1fr auto;gap:14px;align-items:start;padding:14px 16px;border:1px solid var(--border);border-radius:12px;background:var(--surface);box-shadow:var(--shadow);margin-bottom:10px}
.mw .tcard.over{border-left:3px solid var(--crit)}
.mw .ticon{width:38px;height:38px;border-radius:9px;display:grid;place-items:center;background:var(--surface-2);color:var(--ink-2)}
.mw .ticon.review{background:var(--ai-soft);color:var(--ai)} .mw .ticon.approval{background:var(--accent-soft);color:var(--accent)} .mw .ticon.info{background:var(--warn-soft);color:var(--warn)}
.mw .ticon .ic{width:15px;height:15px;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.mw .tmid{min-width:0}
.mw .tmatter{display:flex;align-items:center;gap:8px}
.mw .tmatter .cp{font-weight:680;font-size:14px;color:var(--ink);cursor:pointer} .mw .tmatter .cp:hover{text-decoration:underline}
.mw .ref{font:600 10.5px var(--mono);color:var(--ink-3)}
.mw .ask{font-weight:600;font-size:13px;margin-top:5px;color:var(--ink)}
.mw .tdetail{color:var(--ink-2);font-size:12.5px;margin-top:2px}
.mw .tmeta{display:flex;gap:7px;margin-top:9px;flex-wrap:wrap;align-items:center}
.mw .chip{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:99px;font:600 10.5px var(--sans);white-space:nowrap;background:var(--surface-2);color:var(--ink-2)}
.mw .chip.wf{background:var(--accent-soft);color:var(--accent)} .mw .chip.wf.msa{background:var(--ext-soft);color:var(--ext)}
.mw .chip.risk-high{background:var(--crit-soft);color:var(--crit)} .mw .chip.risk-med{background:var(--warn-soft);color:var(--warn)} .mw .chip.risk-low{background:var(--good-soft);color:var(--good)}
.mw .tright{display:flex;flex-direction:column;align-items:flex-end;gap:10px;white-space:nowrap}
.mw .due{font:600 11.5px var(--sans);color:var(--ink-3)} .mw .due.over{color:var(--crit)} .mw .due.today{color:var(--warn)}
.mw .actions{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}
.mw .raisedsum{margin:6px 0 4px;font-size:13px;color:var(--ink-2)}
.mw .rtablewrap{border:1px solid var(--border);border-radius:12px;overflow-x:auto;background:var(--surface);box-shadow:var(--shadow);margin-top:6px}
.mw table.rt{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px}
.mw table.rt thead th{text-align:left;font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);padding:10px 12px;border-bottom:1px solid var(--border);background:var(--inset);white-space:nowrap}
.mw table.rt tbody td{padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:middle}
.mw table.rt tbody tr:last-child td{border-bottom:0}
.mw table.rt tbody tr{cursor:pointer} .mw table.rt tbody tr:hover td{background:var(--inset)}
.mw table.rt td.c-idx,.mw table.rt th.c-idx{width:34px;text-align:right;color:var(--ink-3);font:600 11px var(--mono)}
.mw .rt .req{display:flex;gap:10px;align-items:center;min-width:0}
.mw .rt .rico{width:30px;height:30px;border-radius:8px;flex:none;display:grid;place-items:center}
.mw .rt .rico.MSA{background:var(--ext-soft);color:var(--ext)} .mw .rt .rico.NDA{background:var(--accent-soft);color:var(--accent)}
.mw .rt .rico .ic{width:15px;height:15px;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.mw .rt .nm{font-weight:650;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:280px;display:inline-block;vertical-align:bottom}
.mw .rt .stagecell{min-width:170px}
.mw .rt .stagenm{font-weight:600;color:var(--ink);font-size:12.5px}
.mw .rt .stagenm .cnt{color:var(--ink-3);font-weight:600;font-family:var(--mono);font-size:11px;margin-left:5px}
.mw .sbar{display:flex;gap:2px;margin-top:7px;max-width:210px}
.mw .sseg{height:7px;flex:1 1 0;min-width:3px;border-radius:2px;background:var(--surface-2)}
.mw .sseg.done{background:color-mix(in srgb,var(--good) 42%,transparent)} .mw .sseg.cur{background:var(--good)}
.mw .sseg.done.st{background:color-mix(in srgb,var(--crit) 42%,transparent)} .mw .sseg.cur.st{background:var(--crit)}
.mw .rstat{font:600 11.5px var(--sans);white-space:nowrap} .mw .rstat.progress{color:var(--ai)} .mw .rstat.input{color:var(--warn)} .mw .rstat.stuck{color:var(--crit)} .mw .rstat.done{color:var(--good)}
`;
