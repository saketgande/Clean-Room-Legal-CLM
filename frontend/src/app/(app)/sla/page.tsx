"use client";

// SLA — how the legal intake queue is performing: volume, SLA compliance, resolution time, and who or
// which team is carrying it. Everything is worked out in the browser from the requests the person can
// see (the same list as Legal Intake) plus the teams, so a team or a person is one click to filter on.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, BarChart3, CheckCircle2, ChevronRight, Clock, Flame, Gauge, Inbox, Timer, UserRound, Users, X } from "lucide-react";
import { intakeApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { describeSlaScan } from "@/lib/sla-scan";
import { can } from "@/lib/intake";
import { CenterSpinner, ErrorState } from "@/components/ui";
import type { IntakeRequest } from "@/lib/types";
import { MockupShell } from "../intake/_legal-intake";

type Range = 7 | 30 | 90 | 0;
const RANGES: { v: Range; l: string }[] = [{ v: 7, l: "7 days" }, { v: 30, l: "30 days" }, { v: 90, l: "90 days" }, { v: 0, l: "All time" }];
const DAY = 86_400_000;

const COLORS = { ok: "var(--c-emr)", risk: "var(--c-amb)", late: "var(--c-red)", info: "var(--c-ind)", sky: "var(--c-sky)", mute: "var(--border-strong)" };

const tsOf = (r: IntakeRequest) => new Date(r.submitted_at ?? r.created_at ?? 0).getTime();
const isClosed = (r: IntakeRequest) => !!r.closed_at;
/** Hours from filing to closing; null while still open. */
const resolutionH = (r: IntakeRequest) => (r.closed_at ? Math.max(0, (new Date(r.closed_at).getTime() - tsOf(r)) / 3_600_000) : null);
const metSla = (r: IntakeRequest) => { const h = resolutionH(r); return h != null && h <= (r.sla_hours || Infinity); };
const fmtH = (h: number | null) => (h == null ? "–" : h < 48 ? `${Math.round(h)}h` : `${(h / 24).toFixed(1)}d`);
const pct = (n: number, d: number) => (d ? Math.round((n / d) * 100) : null);

interface Stat { key: string; label: string; open: number; late: number; risk: number; closed: number; met: number; hours: number[]; total: number }
const blank = (key: string, label: string): Stat => ({ key, label, open: 0, late: 0, risk: 0, closed: 0, met: 0, hours: [], total: 0 });
function add(map: Map<string, Stat>, key: string, label: string, r: IntakeRequest) {
  const s = map.get(key) ?? blank(key, label);
  s.total++;
  if (isClosed(r)) { s.closed++; if (metSla(r)) s.met++; const h = resolutionH(r); if (h != null) s.hours.push(h); }
  else { s.open++; if (r.sla_status === "overdue") s.late++; else if (r.sla_status === "at_risk") s.risk++; }
  map.set(key, s);
}
const avg = (a: number[]) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);

/** A number that counts up to its value, so the cards feel live. */
function CountUp({ n, suffix = "" }: { n: number; suffix?: string }) {
  const [v, setV] = useState(0);
  useEffect(() => {
    if (typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { setV(n); return; }
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / 700);
      setV(Math.round(n * (1 - Math.pow(1 - k, 3))));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [n]);
  return <>{v}{suffix}</>;
}

const tipStyle = { background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 10, boxShadow: "0 8px 24px rgba(20,26,40,.18)", fontSize: 12, color: "var(--ink)" } as const;

export default function SlaPage() {
  const { user } = useAuth();
  const isStaff = can(user, "intake:read");
  const isAdmin = can(user, "admin_panel:access");
  const qc = useQueryClient();
  const { notify } = useToast();
  const [range, setRange] = useState<Range>(30);
  const [team, setTeam] = useState("");
  const [person, setPerson] = useState<{ id: string; name: string } | null>(null);
  const [showAllPeople, setShowAllPeople] = useState(false);

  const { data: all, isLoading, error } = useQuery({ queryKey: ["intake-requests-all"], queryFn: () => intakeApi.list(), enabled: isStaff, staleTime: 30_000 });
  const { data: teams } = useQuery({ queryKey: ["intake-teams"], queryFn: intakeApi.teams, enabled: isStaff, retry: false, staleTime: 60_000 });
  const { data: ops } = useQuery({ queryKey: ["intake-slaops"], queryFn: intakeApi.slaOps, enabled: isStaff, retry: false });
  const scan = useMutation({
    mutationFn: () => intakeApi.slaScan(),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["intake-slaops"] }); qc.invalidateQueries({ queryKey: ["intake-requests-all"] }); notify(describeSlaScan(r), "success"); },
    onError: (e) => notify(e instanceof Error ? e.message : "Scan failed", "error"),
  });

  // Which team a person works in (the first team they are an active member of).
  const teamOfUser = useMemo(() => {
    const m = new Map<string, string>();
    (teams ?? []).forEach((t) => t.members.forEach((mb) => { if (mb.active && !m.has(mb.user_id)) m.set(mb.user_id, t.name); }));
    return m;
  }, [teams]);
  const teamNames = useMemo(() => [...new Set([...(teams ?? []).filter((t) => t.active).map((t) => t.name), "Unassigned"])], [teams]);

  const now = useMemo(() => Date.now(), []);
  const scoped = useMemo(() => {
    const cutoff = range ? now - range * DAY : 0;
    return (all ?? []).filter((r) => {
      if (range && tsOf(r) < cutoff) return false;
      const tm = r.assigned_to_user_id ? teamOfUser.get(r.assigned_to_user_id) ?? "Other" : "Unassigned";
      if (team && tm !== team) return false;
      if (person && r.assigned_to_user_id !== person.id) return false;
      return true;
    });
  }, [all, range, team, person, teamOfUser, now]);

  const k = useMemo(() => {
    const open = scoped.filter((r) => !isClosed(r));
    const closed = scoped.filter(isClosed);
    const late = open.filter((r) => r.sla_status === "overdue").length;
    const risk = open.filter((r) => r.sla_status === "at_risk").length;
    const met = closed.filter(metSla).length;
    return { total: scoped.length, open: open.length, late, risk, onTrack: open.length - late - risk, closed: closed.length, met, missed: closed.length - met, compliance: pct(met, closed.length), avgH: avg(closed.map((r) => resolutionH(r) ?? 0)) };
  }, [scoped]);

  // Volume: opened vs closed per day (short ranges) or per week.
  const volume = useMemo(() => {
    const span = range || Math.max(30, Math.ceil((now - Math.min(now, ...scoped.map(tsOf))) / DAY) + 1);
    const step = span <= 31 ? 1 : 7;
    const n = Math.min(26, Math.ceil(span / step));
    const buckets = Array.from({ length: n }, (_, i) => {
      const end = now - (n - 1 - i) * step * DAY;
      return { start: end - step * DAY, end, label: new Date(end).toLocaleDateString([], { day: "numeric", month: "short" }), opened: 0, closed: 0 };
    });
    for (const r of scoped) {
      const o = buckets.find((b) => tsOf(r) > b.start && tsOf(r) <= b.end);
      if (o) o.opened++;
      if (r.closed_at) { const t = new Date(r.closed_at).getTime(); const c = buckets.find((b) => t > b.start && t <= b.end); if (c) c.closed++; }
    }
    return buckets;
  }, [scoped, range, now]);

  const posture = [
    { name: "On track", value: k.onTrack, color: COLORS.ok },
    { name: "At risk", value: k.risk, color: COLORS.risk },
    { name: "Overdue", value: k.late, color: COLORS.late },
    { name: "Met SLA", value: k.met, color: COLORS.sky },
    { name: "Missed SLA", value: k.missed, color: COLORS.info },
  ].filter((p) => p.value > 0);

  const byTeam = useMemo(() => {
    const m = new Map<string, Stat>();
    for (const r of scoped) { const t = r.assigned_to_user_id ? teamOfUser.get(r.assigned_to_user_id) ?? "Other" : "Unassigned"; add(m, t, t, r); }
    return [...m.values()].sort((a, b) => b.late - a.late || b.open - a.open || b.total - a.total);
  }, [scoped, teamOfUser]);
  const byPerson = useMemo(() => {
    const m = new Map<string, Stat>();
    for (const r of scoped) if (r.assigned_to_user_id) add(m, r.assigned_to_user_id, r.assigned_to_label ?? "Someone", r);
    return [...m.values()].sort((a, b) => b.late - a.late || b.open - a.open || b.total - a.total);
  }, [scoped]);
  const byType = useMemo(() => {
    const m = new Map<string, { type: string; hours: number[]; sla: number[]; n: number }>();
    for (const r of scoped) {
      const e = m.get(r.type_label) ?? { type: r.type_label, hours: [], sla: [], n: 0 };
      e.n++; e.sla.push(r.sla_hours);
      const h = resolutionH(r); if (h != null) e.hours.push(h);
      m.set(r.type_label, e);
    }
    return [...m.values()].filter((e) => e.hours.length).sort((a, b) => b.n - a.n).slice(0, 7)
      .map((e) => ({ type: e.type.length > 22 ? `${e.type.slice(0, 21)}…` : e.type, full: e.type, resolved: Math.round((avg(e.hours) ?? 0) * 10) / 10, sla: Math.round(avg(e.sla) ?? 0), n: e.n }));
  }, [scoped]);
  const byPriority = useMemo(() => (["Critical", "High", "Medium", "Low"] as const).map((p) => {
    const rs = scoped.filter((r) => r.priority === p);
    const open = rs.filter((r) => !isClosed(r));
    return { p, total: rs.length, late: open.filter((r) => r.sla_status === "overdue").length, risk: open.filter((r) => r.sla_status === "at_risk").length, ok: open.length - open.filter((r) => r.sla_status !== "on_track").length, closed: rs.length - open.length };
  }), [scoped]);
  const attention = useMemo(() => scoped.filter((r) => !isClosed(r) && r.sla_status !== "on_track").sort((a, b) => Number(b.sla_status === "overdue") - Number(a.sla_status === "overdue") || b.sla_pct - a.sla_pct).slice(0, 7), [scoped]);

  const filtersOn = !!team || !!person;
  const rangeLabel = RANGES.find((r) => r.v === range)?.l ?? "";
  const peopleShown = showAllPeople ? byPerson : byPerson.slice(0, 8);
  const maxPersonLoad = Math.max(1, ...byPerson.map((p) => p.total));

  return (
    <MockupShell
      icon={<BarChart3 />}
      title="SLA"
      subtitle="how the legal queue is performing, by request, team and person"
      actions={isStaff && isAdmin ? <button className="btn" disabled={scan.isPending} onClick={() => scan.mutate()}>{scan.isPending ? "Scanning…" : "Run breach scan"}</button> : undefined}
    >
      <div className="slad">
        <style dangerouslySetInnerHTML={{ __html: SLAD_CSS }} />
        {!isStaff ? (
          <div className="sdempty"><div className="eic"><UserRound size={22} /></div><p className="et">Access restricted</p><p className="ed">SLA tracking is available to legal operations staff.</p></div>
        ) : isLoading ? (
          <CenterSpinner label="Reading the queue…" />
        ) : error ? (
          <ErrorState error={error} />
        ) : (
          <>
            {/* filters */}
            <div className="sdtools">
              <div className="sdseg" role="group" aria-label="Time range" style={{ ["--i" as string]: RANGES.findIndex((r) => r.v === range) }}>
                <span className="sdseg-pill" aria-hidden />
                {RANGES.map((r) => <button key={r.v} className={range === r.v ? "on" : ""} onClick={() => setRange(r.v)}>{r.l}</button>)}
              </div>
              <label className="sdsel">
                <Users size={14} />
                <select aria-label="Team" value={team} onChange={(e) => { setTeam(e.target.value); setPerson(null); }}>
                  <option value="">All teams</option>
                  {teamNames.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
              {person && <button className="sdchip" onClick={() => setPerson(null)}><UserRound size={13} />{person.name}<X size={13} /></button>}
              {filtersOn && <button className="sdclear" onClick={() => { setTeam(""); setPerson(null); }}>Clear filters</button>}
              <span className="sdnote">{k.total} request{k.total === 1 ? "" : "s"} · {rangeLabel.toLowerCase()}{ops ? ` · ${ops.breaches_7d} newly overdue this week` : ""}</span>
            </div>

            {k.total === 0 ? (
              <div className="sdempty sdin"><div className="eic"><Inbox size={22} /></div><p className="et">No requests in this view</p><p className="ed">Widen the time range or clear a filter.</p></div>
            ) : (
              <div key={`${range}-${team}-${person?.id ?? ""}`} className="sdgrid">
                {/* KPIs */}
                <div className="kcards sdkpis">
                  {[
                    { I: Inbox, l: "Requests", n: k.total, c: "ind", sub: `${k.open} open · ${k.closed} closed` },
                    { I: Clock, l: "Open now", n: k.open, c: "sky", sub: `${k.onTrack} on track` },
                    { I: Flame, l: "Overdue", n: k.late, c: "red", sub: `${k.risk} more at risk` },
                    { I: CheckCircle2, l: "SLA met", n: k.compliance ?? 0, suffix: k.compliance == null ? "–" : "%", c: "emr", sub: k.closed ? `${k.met} of ${k.closed} closed in time` : "nothing closed yet" },
                    { I: Timer, l: "Avg resolution", text: fmtH(k.avgH), c: "vio", sub: "from filing to closed" },
                    { I: Gauge, l: "At risk", n: k.risk, c: "amb", sub: "close to their SLA" },
                  ].map((c, i) => (
                    <div key={c.l} className={`kcard kc-${c.c} sdrise`} style={{ animationDelay: `${i * 50}ms`, cursor: "default" }}>
                      <span className="kic"><c.I className="ic" aria-hidden /></span>
                      <span className="ktx">
                        <span className="kl">{c.l}</span>
                        <b className="kn num">{"text" in c && c.text ? c.text : c.suffix === "–" ? "–" : <CountUp n={c.n ?? 0} suffix={c.suffix} />}</b>
                        <span className="ks">{c.sub}</span>
                      </span>
                    </div>
                  ))}
                </div>

                {/* volume + posture */}
                <section className="sdcard sdrise" style={{ gridColumn: "span 8", animationDelay: "120ms" }}>
                  <header><h3>Requests opened and closed</h3><span>{volume.length > 14 ? "per week" : "per day"}</span></header>
                  <div className="sdchart">
                    <ResponsiveContainer width="100%" height={250}>
                      <AreaChart data={volume} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                        <defs>
                          <linearGradient id="gOpen" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={COLORS.info} stopOpacity={0.35} /><stop offset="100%" stopColor={COLORS.info} stopOpacity={0} /></linearGradient>
                          <linearGradient id="gClosed" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={COLORS.ok} stopOpacity={0.35} /><stop offset="100%" stopColor={COLORS.ok} stopOpacity={0} /></linearGradient>
                        </defs>
                        <CartesianGrid stroke="var(--border)" strokeDasharray="3 4" vertical={false} />
                        <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--ink-3)" }} tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={24} />
                        <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "var(--ink-3)" }} tickLine={false} axisLine={false} />
                        <Tooltip contentStyle={tipStyle} cursor={{ stroke: "var(--border-strong)" }} />
                        <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
                        <Area type="monotone" dataKey="opened" name="Opened" stroke={COLORS.info} strokeWidth={2.2} fill="url(#gOpen)" animationDuration={1100} />
                        <Area type="monotone" dataKey="closed" name="Closed" stroke={COLORS.ok} strokeWidth={2.2} fill="url(#gClosed)" animationDuration={1300} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </section>

                <section className="sdcard sdrise" style={{ gridColumn: "span 4", animationDelay: "170ms" }}>
                  <header><h3>Where requests stand</h3><span>{k.total} in view</span></header>
                  <div className="sdchart sddonut">
                    <ResponsiveContainer width="100%" height={250}>
                      <PieChart>
                        <Pie data={posture} dataKey="value" nameKey="name" innerRadius={62} outerRadius={92} paddingAngle={3} cornerRadius={5} stroke="none" animationDuration={1000}>
                          {posture.map((p) => <Cell key={p.name} fill={p.color} />)}
                        </Pie>
                        <Tooltip contentStyle={tipStyle} />
                      </PieChart>
                    </ResponsiveContainer>
                    <div className="sdcenter"><b className="num">{k.compliance == null ? "–" : <CountUp n={k.compliance} suffix="%" />}</b><span>met SLA</span></div>
                  </div>
                  <ul className="sdlegend">{posture.map((p) => <li key={p.name}><i style={{ background: p.color }} />{p.name}<b>{p.value}</b></li>)}</ul>
                </section>

                {/* teams */}
                <section className="sdcard sdrise" style={{ gridColumn: "span 7", animationDelay: "220ms" }}>
                  <header><h3>Teams</h3><span>click one to filter</span></header>
                  <div role="table" className="sdtbl t-teams">
                    <div className="sdthead" role="rowgroup"><div className="sdtrh" role="row"><div role="columnheader">Team</div><div role="columnheader">Distribution</div><div role="columnheader" className="r">Open</div><div role="columnheader" className="r">Late</div><div role="columnheader" className="r">Met SLA</div></div></div>
                    <div className="sdtbody" role="rowgroup">
                      {byTeam.map((t, i) => {
                        const comp = pct(t.met, t.closed);
                        return (
                          <div key={t.key} tabIndex={0} role="button" aria-pressed={team === t.key} className={`sdtr${team === t.key ? " on" : ""}`} style={{ animationDelay: `${260 + i * 55}ms` }}
                            onClick={() => { setTeam(team === t.key ? "" : t.key); setPerson(null); }}
                            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setTeam(team === t.key ? "" : t.key); setPerson(null); } }}>
                            <div role="cell" className="sdtd"><span className="pcell"><span className="sdinit sq"><Users size={15} /></span><span className="ptx"><b title={t.label}>{t.label}</b><em>{t.total} request{t.total === 1 ? "" : "s"} · {fmtH(avg(t.hours))} avg</em></span></span></div>
                            <div role="cell" className="sdtd"><span className="sdbar" title={`${t.late} overdue · ${t.risk} at risk · ${t.open - t.late - t.risk} on track · ${t.closed} closed`}><i style={{ width: "100%" }}><b className="late" style={{ flex: t.late }} /><b className="risk" style={{ flex: t.risk }} /><b className="ok" style={{ flex: t.open - t.late - t.risk }} /><b className="done" style={{ flex: t.closed }} /></i></span></div>
                            <div role="cell" className="sdtd r"><b className="cell"><CountUp n={t.open} /></b></div>
                            <div role="cell" className="sdtd r">{t.late ? <span className="latepill"><CountUp n={t.late} /></span> : <span className="dimn">0</span>}</div>
                            <div role="cell" className="sdtd r"><span className={`pctn${comp != null && comp < 70 ? " crit" : comp != null && comp >= 90 ? " good" : ""}`}>{comp == null ? "–" : <CountUp n={comp} suffix="%" />}</span></div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                  <p className="sdkey"><i className="late" />Overdue <i className="risk" />At risk <i className="ok" />On track <i className="done" />Closed</p>
                </section>

                {/* types */}
                <section className="sdcard sdrise" style={{ gridColumn: "span 5", animationDelay: "270ms" }}>
                  <header><h3>Resolution time against the SLA</h3><span>average hours, by request type</span></header>
                  {byType.length === 0 ? <p className="sdnone">Nothing in this view has been closed yet.</p> : (
                    <div className="sdchart">
                      <ResponsiveContainer width="100%" height={Math.max(220, byType.length * 44)}>
                        <BarChart data={byType} layout="vertical" margin={{ top: 4, right: 12, left: 8, bottom: 0 }} barGap={3}>
                          <CartesianGrid stroke="var(--border)" strokeDasharray="3 4" horizontal={false} />
                          <XAxis type="number" tick={{ fontSize: 11, fill: "var(--ink-3)" }} tickLine={false} axisLine={false} />
                          <YAxis type="category" dataKey="type" width={120} tick={{ fontSize: 11, fill: "var(--ink-2)" }} tickLine={false} axisLine={false} />
                          <Tooltip contentStyle={tipStyle} cursor={{ fill: "var(--surface-2)" }} />
                          <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
                          <Bar dataKey="resolved" name="Took (avg h)" radius={[0, 6, 6, 0]} barSize={11} animationDuration={1000} fill={COLORS.info}>
                            {byType.map((b) => <Cell key={b.full} fill={b.resolved > b.sla ? COLORS.late : COLORS.info} />)}
                          </Bar>
                          <Bar dataKey="sla" name="SLA (h)" radius={[0, 6, 6, 0]} barSize={11} animationDuration={1300} fill={COLORS.mute} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </section>

                {/* people */}
                <section className="sdcard sdrise" style={{ gridColumn: "span 8", animationDelay: "320ms" }}>
                  <header><h3>People</h3><span>{byPerson.length} with assigned work · click one to filter</span></header>
                  {byPerson.length === 0 ? <p className="sdnone">No request in this view is assigned to anyone.</p> : (
                    <>
                      <div role="table" className="sdtbl t-people">
                        <div className="sdthead" role="rowgroup"><div className="sdtrh" role="row"><div role="columnheader">Person</div><div role="columnheader">Workload</div><div role="columnheader" className="r">Open</div><div role="columnheader" className="r">Overdue</div><div role="columnheader" className="r">Closed</div><div role="columnheader" className="r">Avg time</div><div role="columnheader" className="r">Met SLA</div></div></div>
                        <div className="sdtbody" role="rowgroup">
                          {peopleShown.map((p, i) => {
                            const comp = pct(p.met, p.closed);
                            return (
                              <div key={p.key} tabIndex={0} role="button" aria-pressed={person?.id === p.key} className={`sdtr${person?.id === p.key ? " on" : ""}`} style={{ animationDelay: `${360 + i * 45}ms` }}
                                onClick={() => setPerson(person?.id === p.key ? null : { id: p.key, name: p.label })}
                                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setPerson(person?.id === p.key ? null : { id: p.key, name: p.label }); } }}>
                                <div role="cell" className="sdtd"><span className="pcell"><span className="sdinit">{p.label.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase()}</span><span className="ptx"><b title={p.label}>{p.label}</b><em>{teamOfUser.get(p.key) ?? "No team"}</em></span></span></div>
                                <div role="cell" className="sdtd"><span className="sdbar" title={`${p.late} overdue · ${p.risk} at risk · ${p.open - p.late - p.risk} on track · ${p.closed} closed`}><i style={{ width: `${Math.max(14, (p.total / maxPersonLoad) * 100)}%` }}><b className="late" style={{ flex: p.late }} /><b className="risk" style={{ flex: p.risk }} /><b className="ok" style={{ flex: p.open - p.late - p.risk }} /><b className="done" style={{ flex: p.closed }} /></i></span></div>
                                <div role="cell" className="sdtd r"><b className="cell"><CountUp n={p.open} /></b></div>
                                <div role="cell" className="sdtd r">{p.late ? <span className="latepill"><CountUp n={p.late} /></span> : <span className="dimn">0</span>}</div>
                                <div role="cell" className="sdtd r"><b className="cell"><CountUp n={p.closed} /></b></div>
                                <div role="cell" className="sdtd r"><b className="cell">{fmtH(avg(p.hours))}</b></div>
                                <div role="cell" className="sdtd r"><span className="sdmeter" title={comp == null ? "Nothing closed yet" : `${p.met} of ${p.closed} closed in time`}><span className="mt"><i className={comp == null ? "" : comp < 70 ? "bad" : comp >= 90 ? "good" : "mid"} style={{ width: `${comp ?? 0}%` }} /></span><b className={comp != null && comp < 70 ? "crit" : comp != null && comp >= 90 ? "good" : ""}>{comp == null ? "–" : `${comp}%`}</b></span></div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                      {byPerson.length > 8 && <button className="sdmore" onClick={() => setShowAllPeople((v) => !v)}>{showAllPeople ? "Show fewer" : `Show all ${byPerson.length}`}</button>}
                    </>
                  )}
                </section>

                {/* priority + attention */}
                <div className="sdcol" style={{ gridColumn: "span 4" }}>
                  <section className="sdcard sdrise" style={{ animationDelay: "370ms" }}>
                    <header><h3>By priority</h3></header>
                    <ul className="sdprio">
                      {byPriority.map((p) => (
                        <li key={p.p}>
                          <span className={`pr ${p.p.toLowerCase()}`}>{p.p}</span>
                          <span className="sdstack2"><i className="late" style={{ flex: p.late }} /><i className="risk" style={{ flex: p.risk }} /><i className="ok" style={{ flex: Math.max(0, p.ok) }} /><i className="done" style={{ flex: p.closed }} /></span>
                          <b className="num">{p.total}</b>
                        </li>
                      ))}
                    </ul>
                  </section>
                  <section className="sdcard sdrise" style={{ animationDelay: "420ms" }}>
                    <header><h3><AlertTriangle size={15} />Needs attention</h3><span>{attention.length ? `top ${attention.length}` : ""}</span></header>
                    {attention.length === 0 ? <p className="sdnone good">Nothing overdue or at risk in this view.</p> : (
                      <ul className="sdatt">
                        {attention.map((r) => (
                          <li key={r.id}>
                            <Link href={`/intake?open=${r.id}`} className="sdattrow">
                              <span className={`dot ${r.sla_status === "overdue" ? "late" : "risk"}`} />
                              <span className="sdattx"><b>{r.subject || r.type_label}</b><em>{r.ref} · {r.assigned_to_label ?? "Unassigned"}</em></span>
                              <span className={`sdattp ${r.sla_status === "overdue" ? "late" : "risk"}`}>{Math.round(r.sla_pct)}%</span>
                              <ChevronRight size={14} />
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </MockupShell>
  );
}

const SLAD_CSS = `
.slad{color:var(--ink);font:400 13px/1.5 var(--font-sans)}
.slad *{box-sizing:border-box}
.slad .num{font-variant-numeric:tabular-nums}
@keyframes sdrise{from{opacity:0;transform:translateY(16px) scale(.99)}to{opacity:1;transform:none}}
.slad .sdrise{opacity:0;animation:sdrise .6s cubic-bezier(.2,.8,.2,1) forwards}
.slad .sdin{animation:sdrise .45s both}

.slad .sdtools{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:4px 0 14px}
.slad .sdseg{position:relative;display:grid;grid-template-columns:repeat(4,auto);padding:4px;border-radius:12px;background:var(--surface-2);height:40px}
.slad .sdseg-pill{position:absolute;top:4px;bottom:4px;left:4px;width:calc((100% - 8px) / 4);border-radius:9px;background:var(--surface);box-shadow:0 1px 3px rgba(0,0,0,.14);transform:translateX(calc(var(--i,0) * 100%));transition:transform .3s cubic-bezier(.3,.8,.2,1)}
.slad .sdseg button{position:relative;z-index:1;padding:0 16px;border:0;background:none;font:500 12.5px var(--font-sans);color:var(--ink-3);cursor:pointer;transition:color .2s;white-space:nowrap}
.slad .sdseg button.on{color:var(--ink);font-weight:650}
.slad .sdsel{display:inline-flex;align-items:center;gap:8px;height:36px;padding:0 10px;border-radius:10px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink-3)}
.slad .sdsel select{border:0;background:none;color:var(--ink);font:inherit;font-size:12.5px;outline:none;min-width:120px}
.slad .sdchip{display:inline-flex;align-items:center;gap:7px;height:32px;padding:0 10px;border-radius:99px;border:0;background:var(--accent-soft);color:var(--accent);font:600 12px var(--font-sans);cursor:pointer}
.slad .sdclear{border:0;background:none;color:var(--accent);font:600 12.5px var(--font-sans);cursor:pointer}
.slad .sdnote{margin-left:auto;font-size:12px;color:var(--ink-3)}

.slad .sdgrid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px;align-items:stretch}
.slad .sdkpis{grid-column:1/-1;display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin:0}
.slad .sdkpis .kcard{padding:14px;gap:12px}
.slad .sdkpis .ks{font-size:11px;color:var(--ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.slad .sdkpis .kn{font-size:24px}
.slad .sdcard{display:flex;flex-direction:column;border-radius:20px;background:var(--surface);box-shadow:0 1px 2px rgba(20,26,40,.04),0 0 0 1px var(--border);padding:16px;min-width:0;transition:box-shadow .25s}
.slad .sdcard:hover{box-shadow:0 18px 34px -26px rgba(20,26,40,.45),0 0 0 1px var(--border-strong)}
.slad .sdcard header{display:flex;align-items:baseline;gap:10px;margin-bottom:10px}
.slad .sdcard h3{margin:0;font-size:14px;font-weight:660;display:flex;align-items:center;gap:6px;text-transform:none;letter-spacing:-.005em;color:var(--ink)}
.slad .sdcard header span{margin-left:auto;font-size:11.5px;color:var(--ink-3)}
.slad .sdchart{position:relative}
.slad .sdnone{margin:8px 0;font-size:12.5px;color:var(--ink-3)}
.slad .sdnone.good{color:var(--good);background:var(--good-soft);padding:10px 12px;border-radius:12px}
.slad .sddonut .sdcenter{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;pointer-events:none}
.slad .sdcenter b{font-size:26px;letter-spacing:-.02em}
.slad .sdcenter span{font-size:11px;color:var(--ink-3)}
.slad .sdlegend{list-style:none;margin:8px 0 0;padding:0;display:grid;grid-template-columns:1fr 1fr;gap:4px 12px}
.slad .sdlegend li{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--ink-2)}
.slad .sdlegend i{width:9px;height:9px;border-radius:3px}
.slad .sdlegend b{margin-left:auto;color:var(--ink)}

.slad .sdtbl{display:block;width:100%;min-width:0;font-size:12.5px}
.slad .sdtrh,.slad .sdtr{display:grid;grid-template-columns:var(--cols);column-gap:10px;align-items:center;margin:0}
.slad .sdtrh{border-bottom:1px solid var(--border);padding:0 10px 8px}
.slad .sdtrh>div{font:600 10px var(--font-sans);letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:left}
.slad .sdtrh>div.r,.slad .sdtd.r{text-align:right}
.slad .sdtbody{display:flex;flex-direction:column;gap:3px;margin-top:6px}
.slad .t-teams{--cols:minmax(0,3fr) minmax(0,2.2fr) minmax(0,.9fr) minmax(0,.9fr) minmax(0,1.3fr)}
.slad .t-people{--cols:minmax(0,2.9fr) minmax(0,2fr) minmax(0,.8fr) minmax(0,1.1fr) minmax(0,.8fr) minmax(0,1fr) minmax(0,1.6fr)}
.slad .sdtr{padding:9px 10px;border-radius:14px;cursor:pointer;opacity:0;animation:sdrise .55s cubic-bezier(.2,.8,.2,1) forwards;outline:none;transition:background .18s,transform .18s}
.slad .sdtd{min-width:0;overflow:hidden}
.slad .sdtr:hover,.slad .sdtr:focus-visible{background:var(--inset);transform:translateX(3px)}
.slad .sdtr.on{background:var(--accent-soft)}
.slad .sdtr:hover .sdinit{transform:scale(1.1) rotate(-4deg)}
.slad .cell{font-size:13px;font-weight:650;color:var(--ink);font-variant-numeric:tabular-nums}
.slad .pctn{font-size:13px;font-weight:700;color:var(--ink-2);font-variant-numeric:tabular-nums}
.slad .pctn.crit{color:var(--crit)}.slad .pctn.good{color:var(--good)}
.slad .sdinit.sq{border-radius:10px}
.slad .sdinit{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;font:700 11px var(--font-sans);background:var(--accent-soft);color:var(--accent);flex:none;transition:transform .25s}
.slad .pcell{display:flex;align-items:center;gap:10px;min-width:0}
.slad .ptx{display:flex;flex-direction:column;min-width:0;line-height:1.25}
.slad .ptx b{font-size:13px;font-weight:650;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.slad .ptx em{font-style:normal;font-size:11px;color:var(--ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.slad .latepill{display:inline-block;min-width:26px;text-align:center;padding:2px 8px;border-radius:99px;background:var(--crit-soft);color:var(--crit);font-weight:700;font-size:12px}
.slad .dimn{color:var(--ink-3);font-weight:500}
.slad .sdmeter{display:flex;align-items:center;justify-content:flex-end;gap:8px}
.slad .sdmeter .mt{flex:1;max-width:60px;height:7px;border-radius:4px;background:var(--surface-2);overflow:hidden}
.slad .sdmeter .mt i{display:block;height:100%;border-radius:4px;background:var(--border-strong);transform-origin:left;animation:sdgrow .9s cubic-bezier(.2,.8,.2,1) both}
.slad .sdmeter .mt i.good{background:var(--c-emr)}.slad .sdmeter .mt i.mid{background:var(--c-amb)}.slad .sdmeter .mt i.bad{background:var(--c-red)}
.slad .sdmeter b{min-width:34px;text-align:right;font-size:12.5px;font-weight:700;color:var(--ink-2);font-variant-numeric:tabular-nums}
.slad .sdmeter b.crit{color:var(--crit)}.slad .sdmeter b.good{color:var(--good)}
.slad .sdstack,.slad .sdstack2{display:flex;height:9px;border-radius:5px;overflow:hidden;background:var(--surface-2);gap:2px}
.slad .sdstack i,.slad .sdstack2 i,.slad .sdbar b{display:block;min-width:0;transform-origin:left;animation:sdgrow .9s cubic-bezier(.2,.8,.2,1) both}
@keyframes sdgrow{from{transform:scaleX(0)}to{transform:none}}
.slad i.late,.slad b.late{background:var(--c-red)}.slad i.risk,.slad b.risk{background:var(--c-amb)}.slad i.ok,.slad b.ok{background:var(--c-emr)}.slad i.done,.slad b.done{background:var(--border-strong)}
.slad .sdbar{display:block;height:9px;border-radius:5px;background:var(--surface-2);overflow:hidden}
.slad .sdbar i{display:flex;height:100%;gap:2px;transition:width .6s}
.slad .sdbar i b{border-radius:3px}
.slad .sdkey{display:flex;gap:6px 14px;flex-wrap:wrap;margin:10px 4px 0;font-size:11px;color:var(--ink-3);align-items:center}
.slad .sdkey i{width:9px;height:9px;border-radius:3px;display:inline-block;margin-right:-6px}
.slad .sdmore{display:block;margin:8px auto 0;padding:6px 16px;border-radius:99px;border:0;background:var(--surface-2);color:var(--accent);font:600 12px var(--font-sans);cursor:pointer}
.slad .sdmore:hover{background:var(--accent-soft)}

.slad .sdcol{display:flex;flex-direction:column;gap:14px}
.slad .sdcol>.sdcard{flex:1}
.slad .sdcard .sdchart{flex:1;display:flex;flex-direction:column;justify-content:center}
.slad .sdcard .sdchart>div{width:100%}
.slad .sdprio{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}
.slad .sdprio li{display:grid;grid-template-columns:62px 1fr 28px;gap:10px;align-items:center}
.slad .pr{font:700 10.5px var(--font-sans);letter-spacing:.04em;text-transform:uppercase;padding:3px 0;border-radius:6px;text-align:center}
.slad .pr.critical{background:var(--crit-soft);color:var(--crit)}.slad .pr.high{background:var(--warn-soft);color:var(--warn)}.slad .pr.medium{background:var(--accent-soft);color:var(--accent)}.slad .pr.low{background:var(--surface-2);color:var(--ink-3)}
.slad .sdprio b{text-align:right;font-size:12.5px}
.slad .sdatt{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px}
.slad .sdattrow{display:flex;align-items:center;gap:10px;padding:8px 6px;border-radius:10px;text-decoration:none;color:var(--ink-3);transition:background .15s,transform .15s}
.slad .sdattrow:hover{background:var(--inset);transform:translateX(3px)}
.slad .sdattrow .dot{width:8px;height:8px;border-radius:50%;flex:none}
.slad .sdattrow .dot.late{background:var(--c-red);animation:sdpulse 1.6s ease-in-out infinite}
.slad .sdattrow .dot.risk{background:var(--c-amb)}
@keyframes sdpulse{50%{box-shadow:0 0 0 5px color-mix(in srgb,var(--c-red) 0%,transparent);opacity:.7}0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--c-red) 40%,transparent)}}
.slad .sdattx{display:flex;flex-direction:column;min-width:0;flex:1}
.slad .sdattx b{font-size:12.5px;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.slad .sdattx em{font-style:normal;font-size:11px;color:var(--ink-3)}
.slad .sdattp{font:700 11px var(--font-sans);padding:2px 7px;border-radius:99px}
.slad .sdattp.late{background:var(--crit-soft);color:var(--crit)}.slad .sdattp.risk{background:var(--warn-soft);color:var(--warn)}
.slad .sdempty{display:flex;flex-direction:column;align-items:center;text-align:center;padding:56px 16px;border:1px dashed var(--border-strong);border-radius:18px}
.slad .sdempty .eic{width:48px;height:48px;border-radius:14px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;margin-bottom:10px}
.slad .sdempty .et{margin:0;font-weight:700;font-size:15px}
.slad .sdempty .ed{margin:3px 0 0;font-size:12.5px;color:var(--ink-2)}
@media (max-width:1400px){
  .slad .sdgrid>*{grid-column:1/-1!important}
  .slad .sdkpis{grid-template-columns:repeat(3,minmax(0,1fr))}
}
@media (prefers-reduced-motion:reduce){.slad *{animation:none!important;transition:none!important}.slad .sdrise,.slad .sdtr{opacity:1!important}}
`;
