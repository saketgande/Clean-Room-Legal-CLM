"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlarmClock,
  Ban,
  Bell,
  CalendarClock,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ClipboardCheck,
  ClipboardList,
  Clock,
  DoorOpen,
  FileText,
  Gauge,
  Inbox,
  Info,
  List,
  ListChecks,
  Lock,
  Package,
  Quote,
  Repeat,
  Search,
  ShieldCheck,
  User2,
  Wallet,
  X,
} from "lucide-react";
import { contractsApi, intakeApi, obligationsApi } from "@/lib/endpoints";
import { fmtDate, titleCase } from "@/lib/utils";
import { MockupShell, pageList } from "../intake/_legal-intake";
import { useToast } from "@/components/toast";
import { useAuth } from "@/lib/auth";
import { canEditObligations, canRunReminders } from "@/lib/obligation-access";
import type { IntakeRequest, Obligation, ObligationStatus } from "@/lib/types";
import {
  DATE_FILTERS, SETTLEABLE, anchorText, conflictChoices, conflictText, dateState, editChanges, expectedTotal, needsEventDate,
  timingDetail, timingOf,
} from "./deadline";

const STATUSES: ObligationStatus[] = [
  "open",
  "due_soon",
  "overdue",
  "completed",
  "cancelled",
];

// Status drives both the list pill and the timing color — reuse the same
// SLA-style language (crit/warn/good) as the rest of the app.
const STATUS_TONE: Record<ObligationStatus, "accent" | "warn" | "crit" | "good" | "muted"> = {
  open: "accent",
  due_soon: "warn",
  overdue: "crit",
  completed: "good",
  cancelled: "muted",
};

type View = "requests" | "calendar";
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const dayKey = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const parseDay = (k: string) => new Date(`${k}T00:00:00`);

/** An icon for what kind of duty this is, so a long list can be scanned by shape. */
function typeIcon(type: string | null | undefined): typeof ListChecks {
  const t = (type ?? "").toLowerCase();
  if (/pay|fee|invoice|price|refund/.test(t)) return Wallet;
  if (/notice|notif/.test(t)) return Bell;
  if (/deliver|service|support|perform/.test(t)) return Package;
  if (/confiden|disclos|nda|secret/.test(t)) return Lock;
  if (/restrict|non-|prohibit|exclusiv/.test(t)) return Ban;
  if (/level|sla|uptime|availab/.test(t)) return Gauge;
  if (/terminat|exit|expir/.test(t)) return DoorOpen;
  if (/audit|report|record|complian/.test(t)) return ClipboardCheck;
  if (/data|privacy|secur|insur|indemn|liab/.test(t)) return ShieldCheck;
  if (/renew|recur/.test(t)) return Repeat;
  return ListChecks;
}

export default function ObligationsPage() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { user } = useAuth();
  // Hints only: the server still decides, and the contract's own rule (owner, creator, update-level share) too.
  const mayEdit = canEditObligations(user?.permissions);
  const mayRunReminders = canRunReminders(user?.permissions);
  const [status, setStatus] = useState("");
  const [dateFilter, setDateFilter] = useState("");
  const [editing, setEditing] = useState<Obligation | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [remindersBusy, setRemindersBusy] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  // Request cards start shut; the ones the person opens.
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const [more, setMore] = useState<Set<string>>(new Set());
  const [view, setView] = useState<View>("requests");
  const [reqFilter, setReqFilter] = useState("");
  const todayDate = useMemo(() => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; }, []);
  const [cursor, setCursor] = useState(() => new Date(todayDate.getFullYear(), todayDate.getMonth(), 1));
  const [dir, setDir] = useState<"prev" | "next">("next");
  const [selected, setSelected] = useState(dayKey(todayDate));
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(10);

  const LIST_LIMIT = 200;
  const { data, isLoading, error } = useQuery({
    queryKey: ["obligations", "list", status, dateFilter],
    queryFn: () => obligationsApi.list({
      limit: LIST_LIMIT, ...(status ? { status_filter: status } : {}), ...(dateFilter ? { date_state: dateFilter } : {}),
    }),
  });
  const obligations = data ?? [];
  // The tiles count every obligation the person may see, not the 200 on screen, from the same query as the list.
  const { data: summary, error: summaryError } = useQuery({ queryKey: ["obligations", "summary"], queryFn: () => obligationsApi.summary() });
  const shownOf = summary ? expectedTotal(summary, status, dateFilter) : null;
  // The request each contract came from: obligations are shown under the request they belong to.
  // A person who cannot see requests gets the contract as the heading instead.
  const { data: requests } = useQuery({ queryKey: ["intake-requests-all"], queryFn: () => intakeApi.list(), retry: false, staleTime: 60_000 });
  const reqByContract = useMemo(() => {
    const m = new Map<string, IntakeRequest>();
    (requests ?? []).forEach((r) => { if (r.contract_id && !m.has(r.contract_id)) m.set(r.contract_id, r); });
    return m;
  }, [requests]);

  // Every obligation sits under the contract it comes from. Rows arrive
  // earliest-due first, so the contract with the most pressing duty leads.
  const q = search.trim().toLowerCase();
  const matches = (o: Obligation) => !q || `${o.description} ${o.contract_title ?? ""} ${o.responsible_party ?? ""} ${o.counterparty_name ?? ""} ${o.obligation_type ?? ""}`.toLowerCase().includes(q);
  const filtered = useMemo(() => obligations.filter(matches), [obligations, q]); // eslint-disable-line react-hooks/exhaustive-deps
  type Group = { id: string; rq?: IntakeRequest; title: string; contracts: { id: string; title: string }[]; counterparty: string | null; items: Obligation[] };
  const groupKeyOf = (o: Obligation) => { const r = reqByContract.get(o.contract_id); return r ? `r:${r.id}` : `c:${o.contract_id}`; };
  const groups = useMemo<Group[]>(() => {
    const m = new Map<string, Group>();
    for (const o of filtered) {
      const k = groupKeyOf(o);
      const rq = reqByContract.get(o.contract_id);
      const g = m.get(k) ?? { id: k, rq, title: rq ? rq.subject || rq.type_label : o.contract_title ?? "Contract", contracts: [], counterparty: o.counterparty_name ?? null, items: [] };
      if (!g.contracts.some((c) => c.id === o.contract_id)) g.contracts.push({ id: o.contract_id, title: o.contract_title ?? "Contract" });
      g.items.push(o);
      m.set(k, g);
    }
    // The request with something overdue first, then the one with the nearest date, then the biggest.
    const next = (g: Group) => g.items.filter((o) => o.due_date && o.status !== "completed" && o.status !== "cancelled").map((o) => o.due_date as string).sort()[0] ?? "9999";
    return [...m.values()].sort((a, b) =>
      Number(b.items.some((o) => o.status === "overdue")) - Number(a.items.some((o) => o.status === "overdue")) ||
      next(a).localeCompare(next(b)) || b.items.length - a.items.length);
  }, [filtered, reqByContract]); // eslint-disable-line react-hooks/exhaustive-deps
  // The calendar: every dated duty by its day.
  const calItems = useMemo(() => (reqFilter ? filtered.filter((o) => groupKeyOf(o) === reqFilter) : filtered), [filtered, reqFilter, reqByContract]); // eslint-disable-line react-hooks/exhaustive-deps
  const byDay = useMemo(() => {
    const m = new Map<string, Obligation[]>();
    for (const o of calItems) if (o.due_date) m.set(o.due_date, [...(m.get(o.due_date) ?? []), o]);
    return m;
  }, [calItems]);
  const undated = calItems.filter((o) => !o.due_date);
  const monthCells = useMemo(() => {
    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    const lead = (first.getDay() + 6) % 7; // Monday first
    return Array.from({ length: 42 }, (_, i) => new Date(first.getFullYear(), first.getMonth(), 1 - lead + i));
  }, [cursor]);
  // An empty month on arrival is a poor first look: open on the next date something falls due.
  const [jumped, setJumped] = useState(false);
  useEffect(() => {
    if (jumped || isLoading || obligations.length === 0) return;
    setJumped(true);
    const keys = [...byDay.keys()].sort();
    const thisMonth = `${todayDate.getFullYear()}-${String(todayDate.getMonth() + 1).padStart(2, "0")}`;
    if (!keys.length || keys.some((k) => k.startsWith(thisMonth))) return;
    const next = keys.find((k) => k >= dayKey(todayDate)) ?? keys[keys.length - 1];
    const d = parseDay(next);
    setCursor(new Date(d.getFullYear(), d.getMonth(), 1));
    setSelected(next);
  }, [jumped, isLoading, obligations.length, byDay, todayDate]);
  const goMonth = (n: number) => { setDir(n < 0 ? "prev" : "next"); setCursor((c) => new Date(c.getFullYear(), c.getMonth() + n, 1)); };
  const goToday = () => { setDir(cursor < todayDate ? "next" : "prev"); setCursor(new Date(todayDate.getFullYear(), todayDate.getMonth(), 1)); setSelected(dayKey(todayDate)); };
  const monthCount = calItems.filter((o) => o.due_date && o.due_date.startsWith(`${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, "0")}`)).length;
  const pageCount = Math.max(1, Math.ceil(groups.length / pageSize));
  const cur = Math.min(page, pageCount - 1);
  const visibleGroups = groups.slice(cur * pageSize, (cur + 1) * pageSize);
  const toggleGroup = (id: string) =>
    setOpened((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // null until the summary arrives (or if it fails): a tile must not read 0, which looks like an all-clear.
  const count = (s: ObligationStatus): number | null => (summary ? summary.by_status[s] : null);

  async function runReminders() {
    setRemindersBusy(true);
    try {
      const r = await obligationsApi.runReminders();
      qc.invalidateQueries({ queryKey: ["obligations"] });
      // A run where emails failed or had no one to go to must not read as a success.
      const problems = [
        r.reminders_failed ? `${r.reminders_failed} failed to send` : "",
        r.reminders_skipped ? `${r.reminders_skipped} had no one to send to` : "",
      ].filter(Boolean);
      notify(
        `${r.reminders_sent} reminders sent · ${r.marked_due_soon} due soon · ${r.marked_overdue} overdue` +
          (problems.length ? ` · ${problems.join(" · ")}` : ""),
        problems.length ? "error" : "success",
      );
    } catch (e) {
      notify(e instanceof Error ? e.message : "Run reminders failed", "error");
    } finally {
      setRemindersBusy(false);
    }
  }

  async function complete(o: Obligation) {
    setBusyId(o.id);
    try {
      await obligationsApi.complete(o.id);
      qc.invalidateQueries({ queryKey: ["obligations"] });
      notify("Obligation completed", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Complete failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  async function patch(o: Obligation, payload: Record<string, unknown>, done: string) {
    setBusyId(o.id);
    try {
      // The version this row was shown at: if someone changed it since, the server refuses (409).
      await obligationsApi.update(o.id, { ...payload, expected_updated_at: o.updated_at });
      qc.invalidateQueries({ queryKey: ["obligations"] });
      notify(done, "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "error");
      qc.invalidateQueries({ queryKey: ["obligations"] }); // show what it is now
    } finally {
      setBusyId(null);
    }
  }

  // Settles a form-vs-text dispute on the contract itself: the date becomes a
  // person-set one and the server recounts every deadline counted from it.
  async function settleAnchor(o: Obligation, field: string, value: string) {
    setBusyId(o.id);
    try {
      await contractsApi.update(o.contract_id, { [field]: value });
      qc.invalidateQueries({ queryKey: ["obligations"] });
      qc.invalidateQueries({ queryKey: ["renewals"] });
      notify("Contract date set — deadlines recounted", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not set the contract date", "error");
    } finally {
      setBusyId(null);
    }
  }

  // One obligation as a row (with its detail panel when open). Used by the list and by the calendar's day panel.
  const renderRow = (o: Obligation, showContract: boolean) => {
    const t = timingOf(o, fmtDate);
    const open = expanded === o.id;
    const tone = STATUS_TONE[o.status];
    const quote = (o.source_citation as { quote?: string } | null)?.quote;
    const dl = o.deadline;
    const state = dateState(o);
    // A computed date waits for a person; a re-count after the anchor
    // moved is accepted the same way.
    const canConfirm = !!dl && !dl.set_by_person && (dl.needs_reconfirm || (!!dl.formula && !dl.confirmed));
    const meta = o.metadata_json as { source_clause_type?: string; confidence?: string } | null;
    const Icon = typeIcon(o.obligation_type);
    return (
      <Fragment key={o.id}>
        <div className={`orow st-${tone}${open ? " open" : ""}`} onClick={() => setExpanded(open ? null : o.id)}>
          <span className={`oic t-${tone}`}><Icon size={18} /></span>
          <div className="obl">
            <p className="desc">{o.description}</p>
            <div className="ometa">
              {o.obligation_type && <span className="tag">{o.obligation_type}</span>}
              {showContract && (() => {
                const rq = reqByContract.get(o.contract_id);
                return rq ? (
                  <Link href={`/intake?open=${rq.id}`} onClick={(e) => e.stopPropagation()} className="ochip ct" title="Open the request">
                    <Inbox size={11} /><span className="trunc">{rq.ref} · {rq.subject || rq.type_label}</span>
                  </Link>
                ) : (
                  <Link href={`/contracts/${o.contract_id}`} onClick={(e) => e.stopPropagation()} className="ochip ct" title="Open the contract">
                    <FileText size={11} /><span className="trunc">{o.contract_title ?? "Contract"}</span>
                  </Link>
                );
              })()}
              <span className="ochip"><User2 size={11} /><span className="trunc">{o.responsible_party ?? "Unassigned"}</span></span>
              {o.owner_name && <span className="psub">tracked by {o.owner_name}</span>}
            </div>
          </div>
          <div className="otime">
            {t.tone === "recurring" ? <Repeat size={14} className="dim" /> : <Clock size={14} className="dim" />}
            <div>
              <p className={`pname${t.tone === "date" ? " strong" : t.tone === "needs" ? " crit" : t.tone === "unread" ? " warn" : ""}`}>{t.label}</p>
              {dl?.formula && <p className="fml">{dl.formula}</p>}
              {state && <p className={`st ${state.tone}`}>{state.text}</p>}
              {t.sub && (
                <p className="psub" style={{ color: tone === "crit" ? "var(--crit)" : tone === "warn" ? "var(--warn)" : undefined }}>{t.sub}</p>
              )}
            </div>
          </div>
          <div className="ostat">
            <span className={`pill ${tone}${o.status === "overdue" ? " beat" : ""}`}>{titleCase(o.status)}</span>
          </div>
          <div className="rowacts" onClick={(e) => e.stopPropagation()}>
            {mayEdit && canConfirm && (
              <button className="btn sm" disabled={busyId === o.id} onClick={() => patch(o, { confirm: true }, "Date confirmed")}>
                {dl?.needs_reconfirm ? "Accept new date" : "Confirm"}
              </button>
            )}
            {mayEdit && o.status !== "completed" && (
              <button className="btn sm pri" disabled={busyId === o.id} onClick={() => complete(o)}>
                {busyId === o.id ? "…" : "Complete"}
              </button>
            )}
            {mayEdit && (
              <button className="btn sm" onClick={() => setEditing(o)}>Edit</button>
            )}
          </div>
        </div>
        {open && (
          <div className="odetail">
            <div className="dgrid">
              <div>
                <p className="dlabel"><Quote size={13} /> What the contract says</p>
                {quote ? (
                  <blockquote className="quote">&ldquo;{quote}&rdquo;</blockquote>
                ) : (
                  <p className="nosrc">No source excerpt was captured for this obligation.</p>
                )}
                <p className="descfull">{o.description}</p>
              </div>
              <div className="drows">
                <DetailRow label="Responsible party" value={o.responsible_party} />
                <DetailRow label="Internal owner" value={o.owner_name ?? "Unassigned — assign one via Edit"} />
                <DetailRow label="Timing" value={timingDetail(o, fmtDate, mayEdit)} />
                {dl?.formula && <DetailRow label="How it was counted" value={dl.formula} />}
                {state && <DetailRow label="Date status" value={state.text} />}
                {dl?.anchor?.conflict ? (
                  <>
                    <DetailRow label="Counts from" value={conflictText(dl.anchor.name, dl.anchor.conflict, fmtDate)} />
                    {mayEdit && SETTLEABLE.has(dl.anchor.name) && (
                      <div className="drow">
                        <span className="dk">Settle it</span>
                        <span className="evt">
                          {conflictChoices(dl.anchor.conflict, fmtDate).map((c) => (
                            <button key={c.value} className="btn sm" disabled={busyId === o.id} onClick={() => settleAnchor(o, dl.anchor!.name, c.value)}>
                              {c.label}
                            </button>
                          ))}
                        </span>
                      </div>
                    )}
                  </>
                ) : (
                  anchorText(dl?.anchor ?? null, fmtDate) && <DetailRow label="Counts from" value={anchorText(dl?.anchor ?? null, fmtDate)} />
                )}
                {dl?.needs_reconfirm && dl.recomputed && (
                  <DetailRow label="Re-counted" value={dl.recomputed.formula ?? dl.recomputed.reason ?? "no date"} />
                )}
                {mayEdit && needsEventDate(o) && (
                  <EventDateForm initial={dl?.anchor_date ?? ""} busy={busyId === o.id} onSave={(day) => patch(o, { anchor_date: day }, "Event date logged — date counted")} />
                )}
                <DetailRow label="Category" value={o.obligation_type} />
                {meta?.source_clause_type && <DetailRow label="From clause" value={meta.source_clause_type} />}
                {meta?.confidence && <DetailRow label="Extraction confidence" value={titleCase(meta.confidence)} />}
                <Link href={`/contracts/${o.contract_id}`} className="openlink">Open {o.contract_title ?? "contract"} →</Link>
              </div>
            </div>
          </div>
        )}
      </Fragment>
    );
  };

  const dayItems = byDay.get(selected) ?? [];
  const selDate = parseDay(selected);
  const selLabel = selected === dayKey(todayDate) ? "Today" : selDate.toLocaleDateString([], { weekday: "long" });

  return (
    <MockupShell
      icon={<ListChecks />}
      title="Obligations"
      subtitle="every commitment extracted from your contracts: who owes it, which agreement it comes from, and when it applies"
      actions={
        mayRunReminders ? (
          <button className="btn" disabled={remindersBusy} onClick={runReminders}>
            <AlarmClock size={14} />
            {remindersBusy ? "Running…" : "Run reminders"}
          </button>
        ) : undefined
      }
    >
    <div className="oblg">
      <style dangerouslySetInnerHTML={{ __html: OBLG_CSS }} />
      {user && !mayEdit && <p className="viewonly">You can view obligations but not change them: your role does not include that.</p>}

      <div className="kcards" style={{ gridTemplateColumns: "repeat(6,minmax(0,1fr))", marginTop: 4 }}>
        <StatTile label="Open" value={count("open")} tone="ind" icon={<ListChecks size={18} />} active={status === "open"} onClick={() => { setStatus(status === "open" ? "" : "open"); setPage(0); }} />
        <StatTile label="Due soon" value={count("due_soon")} tone="amb" icon={<Clock size={18} />} active={status === "due_soon"} onClick={() => { setStatus(status === "due_soon" ? "" : "due_soon"); setPage(0); }} />
        <StatTile label="Overdue" value={count("overdue")} tone="red" icon={<AlarmClock size={18} />} active={status === "overdue"} onClick={() => { setStatus(status === "overdue" ? "" : "overdue"); setPage(0); }} />
        <StatTile
          label="Needs a date"
          value={summary ? summary.needs_date : null}
          tone="vio"
          icon={<CalendarClock size={18} />}
          active={dateFilter === "needs_date"}
          onClick={() => { setDateFilter(dateFilter === "needs_date" ? "" : "needs_date"); setPage(0); }}
        />
        <StatTile label="Completed" value={count("completed")} tone="emr" icon={<CheckCircle2 size={18} />} active={status === "completed"} onClick={() => { setStatus(status === "completed" ? "" : "completed"); setPage(0); }} />
        <StatTile label="Cancelled" value={count("cancelled")} tone="sky" icon={<ClipboardList size={18} />} active={status === "cancelled"} onClick={() => { setStatus(status === "cancelled" ? "" : "cancelled"); setPage(0); }} />
      </div>
      {summary?.line ? <p className="dline"><Info size={14} />{summary.line}</p> : null}
      {summaryError ? <p className="dline err" role="alert">The counts above could not be loaded; the list below is unaffected.</p> : null}

      <div className="obtoolbar">
        <div className="obseg" role="tablist" aria-label="View" style={{ ["--i" as string]: view === "requests" ? 0 : 1 }}>
          <span className="obseg-pill" aria-hidden />
          <button role="tab" aria-selected={view === "requests"} className={view === "requests" ? "on" : ""} onClick={() => setView("requests")}><Inbox size={15} />By request</button>
          <button role="tab" aria-selected={view === "calendar"} className={view === "calendar" ? "on" : ""} onClick={() => setView("calendar")}><CalendarDays size={15} />Calendar</button>
        </div>
        <div className="search">
          <Search className="ic" />
          <input aria-label="Search obligations" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} placeholder="Search duty, contract or party…" />
        </div>
        <label className="fld">
          <span className="flabel">Status</span>
          <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(0); }}>
            <option value="">All statuses</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </select>
        </label>
        <label className="fld">
          <span className="flabel">Date</span>
          <select value={dateFilter} onChange={(e) => { setDateFilter(e.target.value); setPage(0); }}>
            {DATE_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
        {view === "calendar" && (
          <label className="fld">
            <span className="flabel">Request</span>
            <select value={reqFilter} onChange={(e) => setReqFilter(e.target.value)}>
              <option value="">All requests</option>
              {groups.map((g) => <option key={g.id} value={g.id}>{g.rq ? `${g.rq.ref} · ${g.title}` : g.title}</option>)}
            </select>
          </label>
        )}
        <p className="hint">
          {dateFilter && (status === "completed" || status === "cancelled") ? (
            <b>Date filters count open obligations only, so none are shown for {titleCase(status)}. · </b>
          ) : null}
          {obligations.length >= LIST_LIMIT ? (
            <b>Showing {LIST_LIMIT}{shownOf != null ? ` of ${shownOf}` : ""} · </b>
          ) : null}
          {view === "requests"
            ? `${groups.length} request${groups.length === 1 ? "" : "s"} · click a request to open its obligations, then one to see the exact language it came from.`
            : "Click an obligation to see the exact language it came from."}
        </p>
      </div>

      {isLoading ? (
        <div className="oblist">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="skcard" />
          ))}
        </div>
      ) : error ? (
        <div className="obempty err">{error instanceof Error ? error.message : "Couldn't load obligations."}</div>
      ) : filtered.length === 0 ? (
        <div className="obempty aa-in">
          <div className="eic"><ListChecks size={22} /></div>
          <p className="et">{q ? "No obligations match your search" : "No obligations found"}</p>
          <p className="ed">{q ? "Try a different word." : "Obligations extracted from active contracts will appear here."}</p>
        </div>
      ) : view === "calendar" ? (
        <div className="calwrap">
          <div className="calmain">
            <div className="calhd">
              <button className="calnav" onClick={() => goMonth(-1)} aria-label="Previous month"><ChevronLeft size={18} /></button>
              <h2 key={`${cursor.getFullYear()}-${cursor.getMonth()}`} className={`calttl slide-${dir}`}>{MONTHS[cursor.getMonth()]} <span>{cursor.getFullYear()}</span></h2>
              <button className="calnav" onClick={() => goMonth(1)} aria-label="Next month"><ChevronRight size={18} /></button>
              <button className="caltoday" onClick={goToday}>Today</button>
              <span className="calcount">{monthCount} due this month</span>
            </div>
            <div className="calweek">{WEEKDAYS.map((w) => <span key={w}>{w}</span>)}</div>
            <div key={`${cursor.getFullYear()}-${cursor.getMonth()}`} className={`calgrid slide-${dir}`}>
              {monthCells.map((d, i) => {
                const k = dayKey(d);
                const items = byDay.get(k) ?? [];
                const inMonth = d.getMonth() === cursor.getMonth();
                const isToday = k === dayKey(todayDate);
                const worst = items.some((o) => o.status === "overdue") ? "crit" : items.some((o) => o.status === "due_soon") ? "warn" : items.some((o) => o.status === "open") ? "accent" : items.length ? "good" : "";
                return (
                  <button
                    key={k}
                    type="button"
                    className={`calday${inMonth ? "" : " out"}${isToday ? " today" : ""}${k === selected ? " sel" : ""}${items.length ? ` has ${worst}` : ""}`}
                    style={{ animationDelay: `${i * 8}ms` }}
                    onClick={() => { setSelected(k); if (!inMonth) { setDir(d < cursor ? "prev" : "next"); setCursor(new Date(d.getFullYear(), d.getMonth(), 1)); } }}
                    aria-label={`${d.toLocaleDateString([], { day: "numeric", month: "long" })}${items.length ? `, ${items.length} due` : ""}`}
                  >
                    <span className="dnum">{d.getDate()}</span>
                    {items.slice(0, 2).map((o) => {
                      const I = typeIcon(o.obligation_type);
                      return <span key={o.id} className={`dchip t-${STATUS_TONE[o.status]}`}><I size={10} /><span className="trunc">{o.obligation_type ?? o.description}</span></span>;
                    })}
                    {items.length > 2 && <span className="dmore">+{items.length - 2} more</span>}
                  </button>
                );
              })}
            </div>
            <div className="callegend">
              <span><i className="t-crit" />Overdue</span><span><i className="t-warn" />Due soon</span><span><i className="t-accent" />Open</span><span><i className="t-good" />Completed</span>
            </div>
          </div>

          <aside className="calside">
            <div className="agenda">
              <div key={selected} className="agendahd aa-in">
                <span className="agdate"><b>{selDate.getDate()}</b><em>{MONTHS[selDate.getMonth()].slice(0, 3)}</em></span>
                <div>
                  <p className="agday">{selLabel}</p>
                  <p className="agsub">{dayItems.length ? `${dayItems.length} obligation${dayItems.length === 1 ? "" : "s"} due` : "Nothing due"}</p>
                </div>
              </div>
              <div key={`l-${selected}`} className="agendalist">
                {dayItems.length === 0 ? (
                  <p className="agempty"><CalendarDays size={22} />A clear day. Pick another date, or open a day with a coloured mark.</p>
                ) : (
                  dayItems.map((o) => renderRow(o, true))
                )}
              </div>
            </div>
            {undated.length > 0 && (
              <button type="button" className="undated" onClick={() => setView("requests")}>
                <span className="uic"><CalendarClock size={18} /></span>
                <span className="utx"><b>{undated.length} without a fixed date</b><em>Ongoing, conditional or waiting for an event. See them in the list.</em></span>
                <ChevronRight size={16} />
              </button>
            )}
          </aside>
        </div>
      ) : (
        <div key={`${status}-${dateFilter}-${cur}`}>
          <div className="oblist">
            {visibleGroups.map((g, gi) => {
              const shut = !opened.has(g.id);
              const tally = STATUSES.map((st) => [st, g.items.filter((o) => o.status === st).length] as const).filter(([, n]) => n);
              const anyLate = g.items.some((o) => o.status === "overdue");
              const done = g.items.filter((o) => o.status === "completed").length;
              const pct = Math.round((done / g.items.length) * 100);
              const nextDue = g.items.filter((o) => o.due_date && o.status !== "completed" && o.status !== "cancelled").map((o) => o.due_date as string).sort()[0];
              return (
                <section key={g.id} className={`obgroup${anyLate ? " late" : ""}`} style={{ animationDelay: `${Math.min(gi, 8) * 55}ms` }}>
                  <div className="obghd" onClick={() => toggleGroup(g.id)}>
                    <ChevronRight size={16} className={`chevicon${shut ? "" : " down"}`} />
                    <span className={`obgic${g.rq ? "" : " c"}`}>{g.rq ? <Inbox size={17} /> : <FileText size={17} />}</span>
                    <div className="obgnm">
                      <div className="obgtitle">
                        {g.rq ? (
                          <Link href={`/intake?open=${g.rq.id}`} onClick={(e) => e.stopPropagation()} className="clink" title="Open the request">{g.title}</Link>
                        ) : (
                          <Link href={`/contracts/${g.contracts[0].id}`} onClick={(e) => e.stopPropagation()} className="clink">{g.title}</Link>
                        )}
                        {g.rq && <b className="obref">{g.rq.ref}</b>}
                      </div>
                      <div className="ometa">
                        {g.contracts.map((c) => (
                          <Link key={c.id} href={`/contracts/${c.id}`} onClick={(e) => e.stopPropagation()} className="ochip ct" title="Open the contract">
                            <FileText size={11} /><span className="trunc">{c.title}</span>
                          </Link>
                        ))}
                        {g.counterparty && <span className="ochip"><User2 size={11} /><span className="trunc">{g.counterparty}</span></span>}
                        {nextDue && <span className="ochip due"><CalendarClock size={11} />Next due {fmtDate(nextDue)}</span>}
                      </div>
                    </div>
                    <div className="obprog" title={`${done} of ${g.items.length} complete`}>
                      <span className="obbar"><i style={{ width: `${pct}%` }} /></span>
                      <em>{done}/{g.items.length} done</em>
                    </div>
                    <span className="gtally">
                      {tally.map(([st, n]) => (
                        <span key={st} className={`pill ${STATUS_TONE[st]}`}>{n} {titleCase(st)}</span>
                      ))}
                    </span>
                  </div>
                  {!shut && (
                    <div className="obrows">
                      {(more.has(g.id) || g.items.length <= 6 ? g.items : g.items.slice(0, 5)).map((o) => renderRow(o, false))}
                      {g.items.length > 6 && (
                        <button type="button" className="obmore" onClick={() => setMore((prev) => { const n = new Set(prev); if (n.has(g.id)) n.delete(g.id); else n.add(g.id); return n; })}>
                          {more.has(g.id) ? "Show fewer" : `Show all ${g.items.length}`}
                          <ChevronDown size={14} className={more.has(g.id) ? "up" : ""} />
                        </button>
                      )}
                    </div>
                  )}
                </section>
              );
            })}
          </div>
          <div className="obpager">
            <span className="num">{`${cur * pageSize + 1}–${Math.min((cur + 1) * pageSize, groups.length)} of ${groups.length} requests`}</span>
            <nav className="pager" aria-label="Pagination">
              <button type="button" className="pgb" disabled={cur === 0} onClick={() => setPage(cur - 1)} aria-label="Previous page"><ChevronDown className="ic prev" /></button>
              {pageList(pageCount, cur).map((n, i) => n === -1
                ? <span key={`e${i}`} className="pge">…</span>
                : <button key={n} type="button" className={`pgb${n === cur ? " on" : ""}`} aria-current={n === cur ? "page" : undefined} onClick={() => setPage(n)}>{n + 1}</button>)}
              <button type="button" className="pgb" disabled={cur >= pageCount - 1} onClick={() => setPage(cur + 1)} aria-label="Next page"><ChevronDown className="ic next" /></button>
            </nav>
            <label className="rpp">Requests per page <select aria-label="Requests per page" value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}>{[5, 10, 25, 50].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
          </div>
        </div>
      )}

      <EditModal
        obligation={editing}
        onClose={() => setEditing(null)}
        onSaved={() => {
          qc.invalidateQueries({ queryKey: ["obligations"] });
          notify("Obligation updated", "success");
          setEditing(null);
        }}
      />
    </div>
    </MockupShell>
  );
}

/** Logs the day the triggering event happened; the server counts the clause's
 *  period from it and shows the formula. */
function EventDateForm({
  initial,
  busy,
  onSave,
}: {
  initial: string;
  busy: boolean;
  onSave: (day: string) => void;
}) {
  const [day, setDay] = useState(initial);
  return (
    <div className="drow">
      <span className="dk">It happened on</span>
      <span className="evt">
        <input type="date" value={day} onChange={(e) => setDay(e.target.value)} aria-label="Date the trigger happened" />
        <button className="btn sm" disabled={busy || !day || day === initial} onClick={() => onSave(day)}>
          Log date
        </button>
      </span>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="drow">
      <span className="dk">{label}</span>
      <span className="dv">{value ?? "—"}</span>
    </div>
  );
}

function StatTile({
  label,
  value,
  tone,
  icon,
  onClick,
  active = false,
}: {
  label: string;
  value: number | null;
  tone: string;
  icon: React.ReactNode;
  /** A tile that is also a filter: pressing it filters the list to what it counts. */
  onClick?: () => void;
  active?: boolean;
}) {
  return (
    <button type="button" className={`kcard kc-${tone}${active ? " on" : ""}`} aria-pressed={active} onClick={onClick}>
      <span className="kic">{icon}</span>
      <span className="ktx">
        <span className="kl">{label}</span>
        <b className="kn num">{value ?? "–"}</b>
      </span>
    </button>
  );
}

function EditModal({
  obligation,
  onClose,
  onSaved,
}: {
  obligation: Obligation | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify } = useToast();
  const qc = useQueryClient();
  const [ownerUserId, setOwnerUserId] = useState("");
  const [responsibleParty, setResponsibleParty] = useState("");
  const [obligationType, setObligationType] = useState("");
  const [status, setStatus] = useState<ObligationStatus>("open");
  const [dueDate, setDueDate] = useState("");
  const [recurrence, setRecurrence] = useState("");
  const [busy, setBusy] = useState(false);
  const [hydratedFor, setHydratedFor] = useState<string | null>(null);

  if (obligation && hydratedFor !== obligation.id) {
    setOwnerUserId(obligation.owner_user_id ?? "");
    setResponsibleParty(obligation.responsible_party ?? "");
    setObligationType(obligation.obligation_type ?? "");
    setStatus(obligation.status);
    setDueDate(obligation.due_date ?? "");
    setRecurrence(obligation.recurrence ?? "");
    setHydratedFor(obligation.id);
  }
  // Closed: forget the draft, or reopening the same row shows it (and Save writes it) instead of the row.
  if (!obligation && hydratedFor !== null) setHydratedFor(null);

  useEffect(() => {
    if (!obligation) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [obligation, onClose]);

  async function submit() {
    if (!obligation) return;
    setBusy(true);
    try {
      const changes = editChanges(obligation, {
        ownerUserId, responsibleParty, obligationType, status, dueDate, recurrence,
      });
      if (Object.keys(changes).length === 0) {
        onClose();
        return;
      }
      // Only what changed, with the version this form was opened at: a stale form is refused (409)
      // instead of undoing someone else's change, and an unchanged due date is not recorded as typed.
      await obligationsApi.update(obligation.id, { ...changes, expected_updated_at: obligation.updated_at });
      onSaved();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "error");
      qc.invalidateQueries({ queryKey: ["obligations"] });
    } finally {
      setBusy(false);
    }
  }

  if (!obligation) return null;

  return (
    <div className="oblg">
      <div className="ov" onClick={onClose}>
        <div className="mdl" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
          <div className="mhd">
            <h2>Edit obligation</h2>
            <button className="xbtn" aria-label="Close dialog" onClick={onClose}>
              <X size={15} />
            </button>
          </div>
          <div className="mbody">
            <label className="fld">
              <span className="flabel">Responsible party</span>
              <input
                maxLength={255}
                value={responsibleParty}
                onChange={(e) => setResponsibleParty(e.target.value)}
              />
              <span className="fhint">Which party owes this — e.g. Receiving Party, both parties</span>
            </label>
            <label className="fld">
              <span className="flabel">Internal owner (user ID)</span>
              <input maxLength={36} value={ownerUserId} onChange={(e) => setOwnerUserId(e.target.value)} />
              <span className="fhint">Who on your team tracks it</span>
            </label>
            <label className="fld">
              <span className="flabel">Obligation type</span>
              <input
                placeholder="e.g. payment"
                maxLength={160}
                value={obligationType}
                onChange={(e) => setObligationType(e.target.value)}
              />
            </label>
            <label className="fld">
              <span className="flabel">Status</span>
              <select value={status} onChange={(e) => setStatus(e.target.value as ObligationStatus)}>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {titleCase(s)}
                  </option>
                ))}
              </select>
            </label>
            <label className="fld">
              <span className="flabel">Due date</span>
              <input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
              <span className="fhint">
                Changing it records the date as set by you; it is then never recounted.
              </span>
            </label>
            <label className="fld">
              <span className="flabel">Recurrence / timing</span>
              <input
                placeholder="e.g. monthly"
                maxLength={120}
                value={recurrence}
                onChange={(e) => setRecurrence(e.target.value)}
              />
              <span className="fhint">e.g. monthly, during term and 12 months after</span>
            </label>
          </div>
          <div className="mft">
            <button className="btn" onClick={onClose}>
              Cancel
            </button>
            <button className="btn pri" disabled={busy} onClick={submit}>
              {busy ? "Saving…" : "Save changes"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const OBLG_CSS = `
.oblg{--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--pop:0 12px 30px rgba(20,26,40,.16);--sans:var(--font-sans);--mono:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .oblg{--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4);--pop:0 14px 34px rgba(0,0,0,.55)}
.oblg *{box-sizing:border-box}
.oblg .dim{color:var(--ink-3)}
.oblg .trunc{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.oblg .viewonly{margin:0 0 10px;font-size:12.5px;color:var(--ink-2)}
.oblg .kcard{cursor:pointer;padding:14px;gap:12px;text-align:left}
.oblg .kcard.on{box-shadow:0 0 0 2px var(--t)}
.oblg .btn.sm{padding:5px 10px;font-size:11.5px;border-radius:8px}
.oblg .dline{display:flex;align-items:center;gap:8px;margin:12px 2px 0;font-size:12.5px;color:var(--ink-2)}
.oblg .dline.err{color:var(--crit)}
.oblg .dline svg{color:var(--accent);flex:none}

.oblg .obtoolbar{display:flex;align-items:flex-end;gap:12px;flex-wrap:wrap;margin:16px 0 14px}
.oblg .obseg{position:relative;display:grid;grid-template-columns:1fr 1fr;padding:4px;border-radius:12px;background:var(--surface-2);height:40px}
.oblg .obseg-pill{position:absolute;top:4px;bottom:4px;left:4px;width:calc((100% - 8px) / 2);border-radius:9px;background:var(--surface);box-shadow:0 1px 3px rgba(0,0,0,.14);transform:translateX(calc(var(--i,0) * 100%));transition:transform .3s cubic-bezier(.3,.8,.2,1)}
.oblg .obseg button{position:relative;z-index:1;display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:0 16px;border:0;background:none;font:500 13px var(--sans);color:var(--ink-3);cursor:pointer;transition:color .2s}
.oblg .obseg button.on{color:var(--ink);font-weight:650}
.oblg .obgroupby{display:inline-flex;padding:3px;border-radius:10px;background:var(--surface-2);height:36px}
.oblg .obgroupby button{border:0;border-radius:8px;padding:0 12px;background:none;font:500 12.5px var(--sans);color:var(--ink-3);cursor:pointer;transition:background .2s,color .2s}
.oblg .obgroupby button.on{background:var(--surface);color:var(--ink);font-weight:650;box-shadow:0 1px 2px rgba(0,0,0,.12)}
.oblg .fld{display:flex;flex-direction:column;gap:4px;font-size:12px}
.oblg .flabel{font-weight:600;color:var(--ink-3);font-size:10.5px;letter-spacing:.05em;text-transform:uppercase}
.oblg .fhint{font-size:11px;color:var(--ink-3)}
.oblg .obtoolbar select,.oblg .fld select,.oblg .fld input{border:1px solid var(--border-strong);border-radius:10px;background:var(--surface);color:var(--ink);font:inherit;font-size:12.5px;height:36px;padding:0 10px;min-width:150px}
.oblg .obtoolbar select:focus,.oblg .fld select:focus,.oblg .fld input:focus{outline:2px solid var(--accent);outline-offset:1px}
.oblg .hint{margin:0 0 6px auto;font-size:12px;color:var(--ink-3);max-width:380px;text-align:right}
.oblg .hint b{color:var(--ink-2)}

@keyframes obin{from{opacity:0;transform:translateY(14px) scale(.99)}to{opacity:1;transform:none}}
.oblg .aa-in{animation:obin .45s cubic-bezier(.2,.8,.2,1) both}

/* calendar */
.oblg .calwrap{display:grid;grid-template-columns:minmax(0,1fr) 400px;gap:16px;align-items:start}
.oblg .calmain{border-radius:20px;background:var(--surface);box-shadow:0 1px 2px rgba(20,26,40,.04),0 0 0 1px var(--border);padding:16px;overflow:hidden}
.oblg .calhd{display:flex;align-items:center;gap:8px;margin-bottom:12px}
.oblg .calttl{margin:0 8px;font-size:18px;font-weight:700;letter-spacing:-.02em;min-width:170px}
.oblg .calttl span{color:var(--ink-3);font-weight:500}
.oblg .calnav{width:34px;height:34px;border-radius:10px;border:1px solid var(--border);background:var(--surface);color:var(--ink-2);display:grid;place-items:center;cursor:pointer;transition:transform .15s,background .15s}
.oblg .calnav:hover{background:var(--surface-2);transform:scale(1.06)}
.oblg .caltoday{height:34px;padding:0 14px;border-radius:10px;border:1px solid var(--border);background:var(--surface);font:600 12.5px var(--sans);color:var(--ink);cursor:pointer;transition:background .15s}
.oblg .caltoday:hover{background:var(--accent-soft);color:var(--accent)}
.oblg .calcount{margin-left:auto;font-size:12px;font-weight:600;color:var(--ink-2);background:var(--surface-2);padding:4px 12px;border-radius:99px}
.oblg .calweek{display:grid;grid-template-columns:repeat(7,1fr);gap:6px;padding:0 2px 6px;font:600 10.5px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3)}
.oblg .calweek span{text-align:left;padding-left:8px}
.oblg .calgrid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px}
.oblg .calgrid.slide-next,.oblg .calttl.slide-next{animation:calnext .38s cubic-bezier(.2,.8,.2,1) both}
.oblg .calgrid.slide-prev,.oblg .calttl.slide-prev{animation:calprev .38s cubic-bezier(.2,.8,.2,1) both}
@keyframes calnext{from{opacity:0;transform:translateX(28px)}to{opacity:1;transform:none}}
@keyframes calprev{from{opacity:0;transform:translateX(-28px)}to{opacity:1;transform:none}}
.oblg .calday{position:relative;display:flex;flex-direction:column;gap:3px;align-items:stretch;min-height:76px;padding:6px 6px 5px;border-radius:12px;border:1px solid transparent;background:var(--inset);text-align:left;cursor:pointer;font:inherit;color:inherit;opacity:0;animation:obin .4s cubic-bezier(.2,.8,.2,1) forwards;transition:transform .18s,background .18s,box-shadow .18s,border-color .18s}
.oblg .calday.out{opacity:.45!important;background:transparent}
.oblg .calday:hover{transform:translateY(-2px);background:var(--surface-2);box-shadow:0 10px 18px -14px rgba(20,26,40,.5)}
.oblg .calday.sel{border-color:var(--accent);background:var(--accent-soft)}
.oblg .dnum{font:650 12px var(--sans);color:var(--ink-2);padding-left:2px}
.oblg .calday.today .dnum{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;background:var(--accent);color:var(--accent-ink);padding:0;animation:caltoday 2.4s ease-in-out infinite}
@keyframes caltoday{0%,100%{box-shadow:0 0 0 0 color-mix(in srgb,var(--accent) 45%,transparent)}50%{box-shadow:0 0 0 5px color-mix(in srgb,var(--accent) 0%,transparent)}}
.oblg .dchip{display:flex;align-items:center;gap:4px;height:18px;padding:0 6px;border-radius:6px;font:600 10px var(--sans);min-width:0;animation:chippop .4s cubic-bezier(.3,1.4,.4,1) both}
@keyframes chippop{from{opacity:0;transform:scale(.7)}to{opacity:1;transform:none}}
.oblg .dchip.t-accent{background:var(--accent-soft);color:var(--accent)}
.oblg .dchip.t-warn{background:var(--warn-soft);color:var(--warn)}
.oblg .dchip.t-crit{background:var(--crit-soft);color:var(--crit)}
.oblg .dchip.t-good{background:var(--good-soft);color:var(--good)}
.oblg .dchip.t-muted{background:var(--surface-2);color:var(--ink-3)}
.oblg .calday.crit .dchip.t-crit{animation:chippop .4s both,dchipbeat 1.8s 1s ease-in-out infinite}
@keyframes dchipbeat{50%{opacity:.6}}
.oblg .dmore{font:600 10px var(--sans);color:var(--ink-3);padding-left:4px}
.oblg .callegend{display:flex;gap:16px;margin-top:12px;padding-left:4px;font-size:11.5px;color:var(--ink-3)}
.oblg .callegend span{display:inline-flex;align-items:center;gap:6px}
.oblg .callegend i{width:9px;height:9px;border-radius:3px}
.oblg .callegend i.t-crit{background:var(--crit)}.oblg .callegend i.t-warn{background:var(--warn)}.oblg .callegend i.t-accent{background:var(--accent)}.oblg .callegend i.t-good{background:var(--good)}

.oblg .calside{display:flex;flex-direction:column;gap:12px;position:sticky;top:8px}
.oblg .agenda{border-radius:20px;background:var(--surface);box-shadow:0 1px 2px rgba(20,26,40,.04),0 0 0 1px var(--border);padding:14px;max-height:calc(100vh - 140px);overflow-y:auto}
.oblg .agendahd{display:flex;align-items:center;gap:12px;margin-bottom:10px}
.oblg .agdate{width:48px;height:52px;border-radius:14px;background:var(--accent);color:var(--accent-ink);display:flex;flex-direction:column;align-items:center;justify-content:center;line-height:1}
.oblg .agdate b{font-size:20px}.oblg .agdate em{font-style:normal;font-size:10px;text-transform:uppercase;letter-spacing:.06em;opacity:.85;margin-top:2px}
.oblg .agday{margin:0;font-size:15px;font-weight:700}
.oblg .agsub{margin:0;font-size:12px;color:var(--ink-3)}
.oblg .agendalist{display:flex;flex-direction:column;gap:6px}
.oblg .agempty{display:flex;flex-direction:column;align-items:center;gap:8px;text-align:center;padding:26px 12px;color:var(--ink-3);font-size:12.5px;animation:obin .4s both}
.oblg .undated{display:flex;align-items:center;gap:12px;width:100%;padding:12px 14px;border-radius:16px;border:1px dashed var(--border-strong);background:var(--surface);cursor:pointer;text-align:left;color:var(--ink-3);transition:transform .2s,border-color .2s,background .2s}
.oblg .undated:hover{transform:translateY(-2px);border-color:var(--accent);background:var(--accent-soft)}
.oblg .uic{width:36px;height:36px;border-radius:11px;background:var(--warn-soft);color:var(--warn);display:grid;place-items:center;flex:none}
.oblg .utx{display:flex;flex-direction:column;flex:1;min-width:0;font-size:12px}
.oblg .utx b{color:var(--ink);font-size:13px}.oblg .utx em{font-style:normal}

/* list */
.oblg .oblist{display:flex;flex-direction:column;gap:8px}
.oblg .obgroup{border-radius:16px;background:var(--surface);box-shadow:0 1px 2px rgba(20,26,40,.04),0 0 0 1px var(--border);opacity:0;animation:obin .55s cubic-bezier(.2,.8,.2,1) forwards;transition:box-shadow .22s}
.oblg .obgroup:hover{box-shadow:0 16px 30px -24px rgba(20,26,40,.45),0 0 0 1px var(--border-strong)}
.oblg .obgroup.late{animation:obin .55s cubic-bezier(.2,.8,.2,1) forwards,obalert 2.6s 1.2s ease-in-out infinite}
@keyframes obalert{0%,100%{box-shadow:0 1px 2px rgba(20,26,40,.05),0 0 0 1px color-mix(in srgb,var(--crit) 40%,var(--border)),0 0 0 0 color-mix(in srgb,var(--crit) 30%,transparent)}50%{box-shadow:0 1px 2px rgba(20,26,40,.05),0 0 0 1px color-mix(in srgb,var(--crit) 65%,var(--border)),0 0 0 6px color-mix(in srgb,var(--crit) 0%,transparent)}}
.oblg .obghd{display:flex;align-items:center;gap:10px;padding:8px 14px;cursor:pointer;border-radius:16px}
.oblg .obgic{width:32px;height:32px;border-radius:10px;display:grid;place-items:center;background:var(--accent-soft);color:var(--accent);flex:none}
.oblg .obgic.p-overdue{background:var(--crit-soft);color:var(--crit)}.oblg .obgic.p-thisweek{background:var(--warn-soft);color:var(--warn)}.oblg .obgic.p-closed{background:var(--good-soft);color:var(--good)}.oblg .obgic.p-nofixeddate{background:var(--surface-2);color:var(--ink-3)}
.oblg .obgnm{display:flex;flex-direction:column;gap:3px;min-width:0;flex:1}
.oblg .obgtitle{display:flex;align-items:center;gap:8px;min-width:0}
.oblg .obgtitle .clink{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13.5px}
.oblg .obref{flex:none;font:700 10px var(--mono);color:var(--accent);background:var(--accent-soft);padding:2px 8px;border-radius:99px}
.oblg .obprog{display:flex;flex-direction:column;gap:3px;width:104px;flex:none}
.oblg .obbar{height:5px;border-radius:3px;background:var(--surface-2);overflow:hidden}
.oblg .obbar i{display:block;height:100%;border-radius:3px;background:var(--good);transform-origin:left;animation:obgrow .9s cubic-bezier(.2,.8,.2,1) both}
@keyframes obgrow{from{transform:scaleX(0)}}
.oblg .obprog em{font-style:normal;font-size:11px;color:var(--ink-3)}
.oblg .ochip.due{background:var(--warn-soft);color:var(--warn)}
.oblg .ochip.due svg{color:var(--warn)}
.oblg .obgic.c{background:var(--surface-2);color:var(--ink-2)}
.oblg .clink{font-weight:670;color:var(--ink);text-decoration:none;font-size:14px}
.oblg a.clink:hover{color:var(--accent)}
.oblg .gcount{margin-left:auto;font-size:11.5px;font-weight:650;color:var(--ink-2);background:var(--surface-2);padding:3px 10px;border-radius:99px;white-space:nowrap}
.oblg .gtally{display:flex;gap:4px;flex-wrap:wrap}
.oblg .chevicon{transition:transform .18s;color:var(--ink-3);flex:none}
.oblg .chevicon.down{transform:rotate(90deg)}
.oblg .obrows{padding:0 8px 8px;display:flex;flex-direction:column;gap:5px;animation:obin .3s ease both}
.oblg .obmore{align-self:center;display:inline-flex;align-items:center;gap:6px;margin-top:4px;padding:6px 14px;border-radius:99px;border:0;background:var(--surface-2);color:var(--accent);font:600 12px var(--sans);cursor:pointer;transition:background .15s,transform .15s}
.oblg .obmore:hover{background:var(--accent-soft);transform:translateY(-1px)}
.oblg .obmore .up{transform:rotate(180deg)}

/* one obligation */
.oblg .orow{--st:var(--border-strong);position:relative;display:grid;grid-template-columns:34px minmax(0,2.8fr) minmax(150px,1.1fr) 92px auto;gap:12px;align-items:start;padding:8px 12px;border-radius:12px;background:var(--inset);cursor:pointer;transition:background .2s,transform .2s,box-shadow .2s}
.oblg .orow:hover{background:var(--surface-2);transform:translateX(3px)}
.oblg .orow.open{background:var(--surface-2)}
.oblg .orow.st-accent{--st:var(--accent)}.oblg .orow.st-warn{--st:var(--warn)}.oblg .orow.st-crit{--st:var(--crit)}.oblg .orow.st-good{--st:var(--good)}
.oblg .oic{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;transition:transform .25s}
.oblg .orow:hover .oic{transform:rotate(-6deg) scale(1.06)}
.oblg .oic.t-accent{background:var(--accent-soft);color:var(--accent)}.oblg .oic.t-warn{background:var(--warn-soft);color:var(--warn)}.oblg .oic.t-crit{background:var(--crit-soft);color:var(--crit)}.oblg .oic.t-good{background:var(--good-soft);color:var(--good)}.oblg .oic.t-muted{background:var(--surface-2);color:var(--ink-3)}
.oblg .obl{min-width:0}
.oblg .desc{margin:0 0 4px;font-weight:600;font-size:12.5px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.oblg .ometa{display:flex;flex-wrap:wrap;align-items:center;gap:5px}
.oblg .tag{display:inline-block;border-radius:6px;background:var(--accent-soft);color:var(--accent);font:600 10px var(--sans);letter-spacing:.04em;text-transform:uppercase;padding:2px 7px}
.oblg .ochip{display:inline-flex;align-items:center;gap:5px;max-width:200px;height:20px;padding:0 8px;border-radius:99px;font-size:11px;color:var(--ink-2);background:var(--surface);text-decoration:none;white-space:nowrap}
.oblg .ochip svg{color:var(--ink-3);flex:none}
.oblg a.ochip:hover{background:var(--accent-soft);color:var(--accent)}
.oblg .otime{display:flex;align-items:flex-start;gap:7px;min-width:0}
.oblg .pname{margin:0;font-weight:600;font-size:12.5px;color:var(--ink-2)}
.oblg .pname.strong{color:var(--ink)}
.oblg .pname.crit{color:var(--crit)}
.oblg .pname.warn{color:var(--warn)}
.oblg .psub{margin:2px 0 0;font-size:11px;color:var(--ink-3)}
.oblg .fml{margin:2px 0 0;font:11px var(--mono);color:var(--ink-3);max-width:260px}
.oblg .st{margin:2px 0 0;font-size:11px;font-weight:600}
.oblg .st.good{color:var(--good)} .oblg .st.warn{color:var(--warn)} .oblg .st.crit{color:var(--crit)} .oblg .st.muted{color:var(--ink-3)}
.oblg .evt{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.oblg .evt input{border:1px solid var(--border-strong);border-radius:8px;background:var(--surface);color:var(--ink);font:inherit;font-size:12.5px;padding:5px 8px}
.oblg .rowacts{display:flex;justify-content:flex-end;gap:6px;flex-wrap:wrap}
.oblg .btn.pri{font-weight:650}
.calside .orow{grid-template-columns:34px minmax(0,1fr)}
.calside .orow>.otime,.calside .orow>.ostat,.calside .orow>.rowacts{grid-column:2;justify-content:flex-start}

.oblg .pill{display:inline-block;font:600 10.5px var(--sans);letter-spacing:.02em;padding:3px 9px;border-radius:99px;white-space:nowrap}
.oblg .pill.accent{background:var(--accent-soft);color:var(--accent)}
.oblg .pill.warn{background:var(--warn-soft);color:var(--warn)}
.oblg .pill.crit{background:var(--crit-soft);color:var(--crit)}
.oblg .pill.good{background:var(--good-soft);color:var(--good)}
.oblg .pill.muted{background:var(--surface-2);color:var(--ink-3)}
.oblg .pill.beat{animation:obbeat 1.4s ease-in-out infinite}
@keyframes obbeat{50%{opacity:.55;transform:scale(.96)}}

.oblg .odetail{margin:0 0 4px;padding:16px;border-radius:14px;background:var(--surface-2);animation:obin .3s ease both}
.oblg .dgrid{display:grid;grid-template-columns:1.4fr 1fr;gap:20px}
.oblg .dlabel{display:flex;align-items:center;gap:6px;margin:0 0 6px;font:600 10.5px var(--sans);letter-spacing:.05em;text-transform:uppercase;color:var(--ink-3)}
.oblg .quote{margin:0;border-left:2px solid var(--accent);background:var(--surface);border-radius:0 8px 8px 0;padding:8px 12px;font-size:12.5px;font-style:italic;line-height:1.55;color:var(--ink-2)}
.oblg .nosrc{margin:0;font-size:12.5px;color:var(--ink-3)}
.oblg .descfull{margin:8px 0 0;font-size:12.5px;line-height:1.55;color:var(--ink-2)}
.oblg .drows{display:flex;flex-direction:column;gap:10px;font-size:12.5px}
.oblg .drow{display:flex;gap:10px}
.oblg .dk{width:130px;flex:none;font:700 10.5px var(--sans);letter-spacing:.04em;text-transform:uppercase;color:var(--ink-3)}
.oblg .dv{color:var(--ink-2)}
.oblg .openlink{display:inline-block;padding-top:2px;font-weight:700;font-size:12.5px;color:var(--accent);text-decoration:none}
.oblg .openlink:hover{text-decoration:underline}
.calside .dgrid{grid-template-columns:1fr}

.oblg .obpager{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:14px;padding:0 4px;font-size:12.5px;color:var(--ink-2)}
.oblg .skcard{height:64px;border-radius:18px;background:var(--surface-2);animation:obpulse 1.4s ease-in-out infinite}
@keyframes obpulse{50%{opacity:.55}}
.oblg .obempty{display:flex;flex-direction:column;align-items:center;text-align:center;padding:56px 16px;border:1px dashed var(--border-strong);border-radius:18px}
.oblg .obempty.err{color:var(--crit)}
.oblg .obempty .eic{width:48px;height:48px;border-radius:14px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;margin-bottom:10px}
.oblg .obempty .et{margin:0;font-weight:700;font-size:15px;color:var(--ink)}
.oblg .obempty .ed{margin:3px 0 0;font-size:12.5px;color:var(--ink-2)}
@media (max-width:1400px){
  .oblg .calwrap{grid-template-columns:1fr}
  .oblg .calside{position:static}
  .oblg .orow{grid-template-columns:34px minmax(0,1fr);row-gap:6px}
  .oblg .orow>.otime,.oblg .orow>.ostat,.oblg .orow>.rowacts{grid-column:2;justify-content:flex-start}
  .oblg .dgrid{grid-template-columns:1fr}
}
@media (prefers-reduced-motion:reduce){.oblg *{animation:none!important;transition:none!important}.oblg .obgroup,.oblg .calday{opacity:1!important}.oblg .calday.out{opacity:.45!important}}

.oblg .ov{position:fixed;inset:0;z-index:50;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(12,15,22,.45);backdrop-filter:blur(2px)}
.oblg .mdl{width:100%;max-width:480px;max-height:85vh;display:flex;flex-direction:column;border:1px solid var(--border);border-radius:14px;background:var(--surface);box-shadow:var(--pop)}
.oblg .mhd{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid var(--border)}
.oblg .mhd h2{margin:0;font-size:14.5px;font-weight:700}
.oblg .xbtn{display:grid;place-items:center;width:28px;height:28px;border:none;border-radius:7px;background:transparent;color:var(--ink-3);cursor:pointer}
.oblg .xbtn:hover{background:var(--surface-2);color:var(--ink)}
.oblg .mbody{overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:14px}
.oblg .mbody .fld input,.oblg .mbody .fld select{width:100%;min-width:0}
.oblg .mft{display:flex;justify-content:flex-end;gap:8px;padding:12px 16px;border-top:1px solid var(--border)}
`;
