"use client";

// Renewals — upcoming contract renewal/auto-renewal tracking in the new
// mockup style (scoped `.renw`). Deadline urgency (crit/warn/good) is derived
// from days-until-notice-date for undecided events.

import { useEffect, useId, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, CalendarClock, CheckCircle2, ChevronDown, Clock, FileText, Gavel, Inbox, RefreshCw, Search } from "lucide-react";
import { contractsApi, intakeApi, renewalsApi } from "@/lib/endpoints";
import { Modal } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/utils";
import { daysToExpiry, expiringWithoutRenewal } from "@/lib/expiry";
import { useToast } from "@/components/toast";
import type { IntakeRequest, RenewalEvent } from "@/lib/types";
import { MockupShell, pageList } from "../intake/_legal-intake";
import {
  EVERGREEN_NOTE, STATUS_TEXT, STATUS_TONE, canConfirm, conflictField, conflictText, evergreenConflict, mustTypeExpiry,
  recomputed,
} from "./dates";
import { conflictChoices } from "../obligations/deadline";

type RenewalDecision = "renew" | "terminate" | "renegotiate";
const DECISIONS: RenewalDecision[] = ["renew", "terminate", "renegotiate"];

type Urgency = "crit" | "warn" | "good";

function daysUntil(iso: string | null): number | null {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  const target = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(iso);
  if (Number.isNaN(target.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - today.getTime()) / 86400000);
}

// Rows already decided are no longer time-pressured; only undecided renewals
// carry urgency off their notice-date deadline.
function noticeUrgency(r: RenewalEvent): { tone: Urgency; label: string } | null {
  if (r.decision !== "undecided") return null;
  const days = daysUntil(r.notice_date);
  if (days === null) return null;
  if (days < 0) return { tone: "crit", label: "Overdue" };
  if (days <= 14) return { tone: "crit", label: `In ${days}d` };
  if (days <= 30) return { tone: "warn", label: `In ${days}d` };
  return { tone: "good", label: `In ${days}d` };
}

const DECISION_TONE: Record<RenewalEvent["decision"], "slate" | "good" | "warn" | "crit"> = {
  undecided: "slate",
  renew: "good",
  renegotiate: "warn",
  terminate: "crit",
};

type Tab = "decide" | "soon" | "done" | "all";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const TONE_VAR = { crit: "var(--c-red)", warn: "var(--c-amb)", good: "var(--c-emr)", none: "var(--border-strong)" } as const;

/** A number that counts up to its value, so the cards feel live. */
function CountUp({ n }: { n: number }) {
  const [v, setV] = useState(0);
  useEffect(() => {
    if (typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { setV(n); return; }
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / 650);
      setV(Math.round(n * (1 - Math.pow(1 - k, 3))));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [n]);
  return <>{v}</>;
}

/** The days left to give notice, drawn as a ring that empties as the deadline nears. */
function Ring({ days, tone }: { days: number | null; tone: Urgency | "none" }) {
  const R = 19;
  const C = 2 * Math.PI * R;
  const frac = days == null ? 0 : Math.max(0.06, Math.min(1, days / 90));
  const [on, setOn] = useState(false);
  useEffect(() => { const t = setTimeout(() => setOn(true), 60); return () => clearTimeout(t); }, []);
  return (
    <span className={`rnring ${tone}`} aria-hidden>
      <svg viewBox="0 0 48 48" width="48" height="48">
        <circle cx="24" cy="24" r={R} className="rt" />
        <circle cx="24" cy="24" r={R} className="rv" stroke={TONE_VAR[tone]} strokeDasharray={C} strokeDashoffset={on ? C * (1 - frac) : C} transform="rotate(-90 24 24)" />
      </svg>
      <b>{days == null ? "–" : days < 0 ? "!" : days}</b>
      <em>{days == null ? "" : days < 0 ? "late" : "days"}</em>
    </span>
  );
}

export default function RenewalsPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const { notify } = useToast();
  const [deciding, setDeciding] = useState<RenewalEvent | null>(null);
  const [confirming, setConfirming] = useState<RenewalEvent | null>(null);
  const [windowBusy, setWindowBusy] = useState(false);
  const [tabSel, setTabSel] = useState<Tab | null>(null);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(10);

  const { data, isLoading, error } = useQuery({
    queryKey: ["renewals"],
    queryFn: () => renewalsApi.list(),
  });
  const rows = useMemo(() => data ?? [], [data]);
  // Running contracts that end within 90 days but have no row here: the Contracts screen counts them.
  const { data: contracts } = useQuery({ queryKey: ["contracts-list"], queryFn: contractsApi.list });
  // The request each contract came from; someone who cannot see requests gets the contract name as the heading.
  const { data: requests } = useQuery({ queryKey: ["intake-requests-all"], queryFn: () => intakeApi.list(), retry: false, staleTime: 60_000 });
  const reqByContract = useMemo(() => {
    const m = new Map<string, IntakeRequest>();
    (requests ?? []).forEach((r) => { if (r.contract_id && !m.has(r.contract_id)) m.set(r.contract_id, r); });
    return m;
  }, [requests]);
  const contractById = useMemo(() => new Map((contracts ?? []).map((c) => [c.id, c])), [contracts]);
  const untracked = expiringWithoutRenewal(contracts ?? [], rows);

  const isSoon = (r: RenewalEvent) => { const u = noticeUrgency(r); return !!u && (u.tone === "crit" || u.tone === "warn"); };
  const undecided = rows.filter((r) => r.decision === "undecided");
  const soon = rows.filter(isSoon);
  const decided = rows.filter((r) => r.decision !== "undecided");
  const tab: Tab = tabSel ?? (undecided.length ? "decide" : "all");
  const pickTab = (t: Tab) => { setTabSel(t); setPage(0); };

  const q = search.trim().toLowerCase();
  const shown = useMemo(() => {
    const base = tab === "decide" ? undecided : tab === "soon" ? soon : tab === "done" ? decided : rows;
    const list = base.filter((r) => !q || `${r.contract_title ?? ""} ${reqByContract.get(r.contract_id)?.subject ?? ""} ${reqByContract.get(r.contract_id)?.ref ?? ""}`.toLowerCase().includes(q));
    // Undecided first, nearest notice deadline first (overdue leads); decided ones after.
    const key = (r: RenewalEvent) => (r.decision === "undecided" ? 0 : 1e9) + (daysUntil(r.notice_date) ?? 9e8);
    return [...list].sort((a, b) => key(a) - key(b));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, tab, q, reqByContract]);
  const pageCount = Math.max(1, Math.ceil(shown.length / pageSize));
  const cur = Math.min(page, pageCount - 1);
  const visible = shown.slice(cur * pageSize, (cur + 1) * pageSize);

  // Expirations over the next twelve months, split by whether anyone has decided yet.
  const byMonth = useMemo(() => {
    const now = new Date();
    const slots = Array.from({ length: 12 }, (_, i) => {
      const d = new Date(now.getFullYear(), now.getMonth() + i, 1);
      return { key: `${d.getFullYear()}-${d.getMonth()}`, label: MONTHS[d.getMonth()], decide: 0, done: 0 };
    });
    for (const r of rows) {
      if (!r.expiration_date) continue;
      const d = new Date(`${r.expiration_date}T00:00:00`);
      const slot = slots.find((s) => s.key === `${d.getFullYear()}-${d.getMonth()}`);
      if (slot) { if (r.decision === "undecided") slot.decide++; else slot.done++; }
    }
    return slots;
  }, [rows]);
  const chartHas = byMonth.some((m) => m.decide || m.done);

  async function runWindowCheck() {
    setWindowBusy(true);
    try {
      const r = await renewalsApi.runWindowCheck();
      qc.invalidateQueries({ queryKey: ["renewals"] });
      qc.invalidateQueries({ queryKey: ["contracts"] });
      notify(
        `${r.contracts_moved_to_renewal_due} contract(s) moved to renewal due`,
        "success",
      );
    } catch (e) {
      notify(e instanceof Error ? e.message : "Window check failed", "error");
    } finally {
      setWindowBusy(false);
    }
  }

  const kcards: { t: Tab; n: number; l: string; sub: string; I: typeof Inbox; c: string }[] = [
    { t: "all", n: rows.length, l: "Renewals", sub: "tracked in all", I: CalendarClock, c: "ind" },
    { t: "decide", n: undecided.length, l: "Need a decision", sub: "nobody has decided", I: Gavel, c: "vio" },
    { t: "soon", n: soon.length, l: "Notice due soon", sub: "within 30 days", I: AlertTriangle, c: "red" },
    { t: "done", n: decided.length, l: "Decided", sub: "renew, renegotiate, end", I: CheckCircle2, c: "emr" },
  ];
  const EMPTY: Record<Tab, { t: string; d: string }> = {
    decide: { t: "Nothing waiting for a decision", d: "Every renewal has been decided." },
    soon: { t: "No notice deadline is close", d: "Nothing needs notice within the next 30 days." },
    done: { t: "No decisions recorded yet", d: "Renewals you decide will appear here." },
    all: { t: "No renewal events", d: "Renewal events are created as contracts approach their expiration window." },
  };

  return (
    <MockupShell
      icon={<RefreshCw />}
      title="Renewals"
      subtitle="watch every expiry and notice deadline, and record what you decided"
      actions={<button className="btn" disabled={windowBusy} onClick={runWindowCheck}><RefreshCw className="sic" />{windowBusy ? "Checking…" : "Run window check"}</button>}
    >
    <div className="renw">
      <style dangerouslySetInnerHTML={{ __html: RENW_CSS }} />
      {rows.length > 0 && (
        <div className="kcards" style={{ gridTemplateColumns: "repeat(4,minmax(0,1fr))", marginTop: 4 }}>
          {kcards.map((k) => (
            <button key={k.t} type="button" className={`kcard kc-${k.c}`} onClick={() => pickTab(k.t)} tabIndex={-1} aria-hidden="true" style={{ cursor: "pointer", padding: 14, gap: 12 }}>
              <span className="kic"><k.I className="ic" aria-hidden /></span>
              <span className="ktx"><span className="kl">{k.l}</span><b className="kn num"><CountUp n={k.n} /></b><span className="ks">{k.sub}</span></span>
            </button>
          ))}
        </div>
      )}

      {untracked.length > 0 && (
        <div className="rnwarn rnin" role="note">
          <span className="wic"><AlertTriangle size={18} /></span>
          <div>
            <b>
              {untracked.length} running contract{untracked.length === 1 ? "" : "s"} expire within 90 days but{" "}
              {untracked.length === 1 ? "has" : "have"} no renewal record yet, so {untracked.length === 1 ? "it is" : "they are"} not
              listed below:
            </b>{" "}
            {untracked.map((c, i) => (
              <span key={c.id}>
                {i > 0 ? ", " : ""}
                <Link href={`/contracts/${c.id}`} style={{ textDecoration: "underline" }}>{c.title}</Link> ({daysToExpiry(c)}d)
              </span>
            ))}
            . A renewal record is created when a contract&apos;s renewal terms have been read.
          </div>
        </div>
      )}

      {rows.length > 0 && chartHas && (
        <section className="rncard rnin" style={{ animationDelay: "120ms" }}>
          <header><h3>Expiries over the next 12 months</h3><span className="rnleg"><i className="d" />Needs a decision<i className="o" />Decided</span></header>
          <ResponsiveContainer width="100%" height={150}>
            <BarChart data={byMonth} margin={{ top: 6, right: 6, left: -22, bottom: 0 }} barCategoryGap="22%">
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 4" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--ink-3)" }} tickLine={false} axisLine={false} />
              <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "var(--ink-3)" }} tickLine={false} axisLine={false} />
              <Tooltip cursor={{ fill: "var(--surface-2)" }} contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 10, fontSize: 12, color: "var(--ink)" }} />
              <Bar dataKey="decide" name="Needs a decision" stackId="a" fill="var(--c-amb)" radius={[0, 0, 0, 0]} animationDuration={900}>
                {byMonth.map((m) => <Cell key={m.key} fill="var(--c-amb)" />)}
              </Bar>
              <Bar dataKey="done" name="Decided" stackId="a" fill="var(--c-emr)" radius={[5, 5, 0, 0]} animationDuration={1200} />
            </BarChart>
          </ResponsiveContainer>
        </section>
      )}

      {rows.length > 0 && (
        <div className="rntools">
          <div className="stabs" role="group" aria-label="Filter renewals" style={{ marginTop: 0 }}>
            {([["decide", "Needs a decision", undecided.length, "vio"], ["soon", "Notice due soon", soon.length, "red"], ["done", "Decided", decided.length, "emr"], ["all", "All", rows.length, "ind"]] as const).map(([t, l, n, c]) => (
              <button key={t} type="button" aria-pressed={tab === t} onClick={() => pickTab(t)} className={`stab${tab === t ? " on" : ""}`} style={{ ["--c" as string]: `var(--c-${c})` }}>
                <i aria-hidden /><span className="tl">{l}</span><span className="tn num">{n}</span>
              </button>
            ))}
          </div>
          <div className="search" style={{ marginLeft: "auto" }}>
            <Search className="ic" />
            <input aria-label="Search renewals" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} placeholder="Search request or contract…" />
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="grid" style={{ padding: "8px 0" }}>
          {[0, 1, 2].map((i) => (
            <div key={i} className="skel" />
          ))}
        </div>
      ) : error ? (
        <div className="rnempty err">
          {error instanceof Error ? error.message : "Couldn't load renewals."}
        </div>
      ) : rows.length === 0 ? (
        <div className="rnempty rnin">
          <div className="eic"><CalendarClock size={22} /></div>
          <p className="et">{EMPTY.all.t}</p>
          <p className="ed">{EMPTY.all.d}</p>
        </div>
      ) : shown.length === 0 ? (
        <div className="rnempty rnin">
          <div className="eic"><CheckCircle2 size={22} /></div>
          <p className="et">{q ? "No renewals match your search" : EMPTY[tab].t}</p>
          <p className="ed">{q ? "Try a different word." : EMPTY[tab].d}</p>
        </div>
      ) : (
        <div key={`${tab}-${cur}`}>
          <ul className="rnlist">
            {visible.map((r, i) => {
              const notice = noticeUrgency(r);
              const rq = reqByContract.get(r.contract_id);
              const ct = contractById.get(r.contract_id);
              const href = rq ? `/intake?open=${rq.id}` : `/contracts/${r.contract_id}`;
              const heading = rq ? rq.subject || rq.type_label : r.contract_title ?? "Untitled contract";
              const days = daysUntil(r.notice_date);
              const exp = daysUntil(r.expiration_date);
              const tone: Urgency | "none" = notice?.tone ?? "none";
              return (
                <li key={r.id} className={`rnrow st-${tone}${tone === "crit" ? " late" : ""}`} style={{ animationDelay: `${Math.min(i, 8) * 50}ms` }}>
                  <div className="rngrid click" onClick={(e) => { if (!(e.target as HTMLElement).closest("a,button")) router.push(href); }}>
                    <Ring days={r.decision === "undecided" ? days : null} tone={r.decision === "undecided" ? tone : "none"} />
                    <div className="rnmain">
                      <div className="rntitle">
                        <Link href={href} className="lnk">{heading}</Link>
                        {rq && <b className="rnref">{rq.ref}</b>}
                      </div>
                      <div className="rnmeta">
                        <Link href={`/contracts/${r.contract_id}`} className="rnchip ct" title="Open the contract"><FileText size={11} /><span className="trunc">{r.contract_title ?? "Contract"}</span></Link>
                        {ct?.counterparty_name && <span className="rnchip"><span className="trunc">{ct.counterparty_name}</span></span>}
                        {r.dates_status && <span className={`rndst ${STATUS_TONE[r.dates_status]}`}>{STATUS_TEXT[r.dates_status]}</span>}
                      </div>
                      {(r.date_conflict || r.date_reason) && <p className="fml">{r.date_conflict ? conflictText(r.date_conflict, fmtDate) : r.date_reason}</p>}
                    </div>
                    <div className="rndates">
                      <div className="rdate"><span>Expires</span><b>{fmtDate(r.expiration_date)}</b>{exp != null && <em>{exp < 0 ? `${-exp}d ago` : `in ${exp}d`}</em>}</div>
                      <div className={`rdate${tone === "crit" ? " late" : ""}`}><span>Notice by</span><b>{fmtDate(r.notice_date)}</b>{notice && <em className={`tg ${notice.tone}`}>{notice.label}</em>}</div>
                      <div className="rdate"><span>Window opens</span><b>{fmtDate(r.renewal_window_starts_at)}</b></div>
                    </div>
                    <div className="rndec"><span className={`tag ${DECISION_TONE[r.decision]}`}>{titleCase(r.decision)}</span></div>
                    <div className="rnact">
                      {r.dates_status && r.dates_status !== "confirmed" && (
                        <button className="btn sm" onClick={() => setConfirming(r)}>{canConfirm(r) ? "Confirm dates" : "Set dates"}</button>
                      )}
                      <button className="btn sm pri" onClick={() => setDeciding(r)}><Gavel size={13} />Decide</button>
                    </div>
                  </div>
                  {(r.formulas.expiration_date || r.formulas.notice_date || r.formulas.window_opens || r.next_expiration_date) && (
                    <details className="rnhow" onClick={(e) => e.stopPropagation()}>
                      <summary>How these dates were counted</summary>
                      {r.formulas.expiration_date && <p className="fml">Expiry: {r.formulas.expiration_date}</p>}
                      {r.formulas.notice_date && <p className="fml">Notice: {r.formulas.notice_date}</p>}
                      {r.formulas.window_opens && <p className="fml">Window: {r.formulas.window_opens}</p>}
                      {r.next_expiration_date && <p className="fml">{r.formulas.next_expiration_date ?? `Next term ends ${fmtDate(r.next_expiration_date)}`} (if not stopped)</p>}
                    </details>
                  )}
                </li>
              );
            })}
          </ul>
          <div className="rnpager">
            <span className="num">{`${cur * pageSize + 1}–${Math.min((cur + 1) * pageSize, shown.length)} of ${shown.length}`}</span>
            <nav className="pager" aria-label="Pagination">
              <button type="button" className="pgb" disabled={cur === 0} onClick={() => setPage(cur - 1)} aria-label="Previous page"><ChevronDown className="ic prev" /></button>
              {pageList(pageCount, cur).map((n, i) => n === -1
                ? <span key={`e${i}`} className="pge">…</span>
                : <button key={n} type="button" className={`pgb${n === cur ? " on" : ""}`} aria-current={n === cur ? "page" : undefined} onClick={() => setPage(n)}>{n + 1}</button>)}
              <button type="button" className="pgb" disabled={cur >= pageCount - 1} onClick={() => setPage(cur + 1)} aria-label="Next page"><ChevronDown className="ic next" /></button>
            </nav>
            <label className="rpp">Rows <select aria-label="Rows per page" value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}>{[5, 10, 25, 50].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
          </div>
        </div>
      )}

      <ConfirmDatesModal
        key={confirming?.id ?? "none"}
        renewal={confirming}
        onClose={() => setConfirming(null)}
        onSaved={(message) => {
          qc.invalidateQueries({ queryKey: ["renewals"] });
          qc.invalidateQueries({ queryKey: ["obligations"] });
          notify(message, "success");
          setConfirming(null);
        }}
      />

      <DecideModal
        renewal={deciding}
        onClose={() => setDeciding(null)}
        onDecided={() => {
          qc.invalidateQueries({ queryKey: ["renewals"] });
          notify("Renewal decision recorded", "success");
          setDeciding(null);
        }}
      />
    </div>
    </MockupShell>
  );
}

/** Accept the calculated dates (or their re-count), or type your own. A typed
 *  expiry recounts the notice deadline on the server and becomes the
 *  contract's expiry as a person-set date. */
function ConfirmDatesModal({
  renewal,
  onClose,
  onSaved,
}: {
  renewal: RenewalEvent | null;
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const { notify } = useToast();
  const [expiry, setExpiry] = useState("");
  const [noticeBy, setNoticeBy] = useState("");
  const [busy, setBusy] = useState(false);
  const expiryId = useId();
  const noticeId = useId();
  const recount = renewal ? recomputed(renewal) : null;
  // No expiry to accept (or two that disagree): the person must type one.
  const mustType = !!renewal && mustTypeExpiry(renewal);
  const conflict = renewal?.date_conflict ?? null;

  // A form-vs-text dispute is settled on the contract: the picked date becomes
  // a person-set contract date and the server recounts the renewal from it.
  async function settle(value: string) {
    if (!renewal) return;
    setBusy(true);
    try {
      await contractsApi.update(renewal.contract_id, { [conflictField(renewal)]: value });
      onSaved("Contract date set — renewal dates recounted");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not set the contract date", "error");
    } finally {
      setBusy(false);
    }
  }

  async function noEndDate() {
    if (!renewal) return;
    setBusy(true);
    try {
      await renewalsApi.confirmDates(renewal.id, { no_end_date: true });
      onSaved("Set to run until terminated");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not save", "error");
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    if (!renewal || (mustType && !expiry)) return;
    setBusy(true);
    try {
      await renewalsApi.confirmDates(renewal.id, {
        ...(expiry ? { expiration_date: expiry } : {}),
        ...(noticeBy ? { notice_date: noticeBy } : {}),
      });
      onSaved("Renewal dates confirmed");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not confirm the dates", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={!!renewal}
      onClose={onClose}
      title="Renewal dates"
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn pri" disabled={busy || (mustType && !expiry)} onClick={submit}>
            {busy ? "Saving…" : expiry || noticeBy ? "Save my dates" : "Confirm these dates"}
          </button>
        </>
      }
    >
      {renewal && (
        <div className="mbody">
          {renewal.dates_status && (
            <p className={`dst ${STATUS_TONE[renewal.dates_status]}`}>{STATUS_TEXT[renewal.dates_status]}</p>
          )}
          {conflict && <p className="note">{conflictText(conflict, fmtDate)}.</p>}
          {conflict && evergreenConflict(renewal) && <p className="note">{EVERGREEN_NOTE}</p>}
          {conflict && (
            <div className="rowacts pick">
              {conflictChoices(conflict, fmtDate).map((c) => (
                <button key={c.value} className="btn sm" disabled={busy} onClick={() => settle(c.value)}>
                  {c.label}
                </button>
              ))}
              {evergreenConflict(renewal) && (
                <button className="btn sm" disabled={busy} onClick={noEndDate}>
                  Runs until terminated
                </button>
              )}
            </div>
          )}
          {renewal.date_reason && <p className="note">{renewal.date_reason}</p>}
          <dl className="dates">
            <dt>Expiry</dt>
            <dd>
              {fmtDate(renewal.expiration_date)}
              {renewal.formulas.expiration_date && <span className="fml">{renewal.formulas.expiration_date}</span>}
            </dd>
            <dt>Notice by</dt>
            <dd>
              {fmtDate(renewal.notice_date)}
              {renewal.formulas.notice_date && <span className="fml">{renewal.formulas.notice_date}</span>}
            </dd>
            <dt>Window opens</dt>
            <dd>
              {fmtDate(renewal.renewal_window_starts_at)}
              {renewal.formulas.window_opens && <span className="fml">{renewal.formulas.window_opens}</span>}
            </dd>
          </dl>
          {recount && (
            <div className="note">
              <strong>Re-counted after a contract date changed</strong> — confirming accepts these:
              <span className="fml">{recount.formulas?.expiration_date ?? `expires ${fmtDate(recount.expiration_date ?? null)}`}</span>
              {(recount.formulas?.notice_date || recount.notice_date) && (
                <span className="fml">{recount.formulas?.notice_date ?? `notice by ${fmtDate(recount.notice_date ?? null)}`}</span>
              )}
            </div>
          )}
          <label className="lbl" htmlFor={expiryId}>
            Expiry {mustType ? "(required)" : "(only to change it)"}
          </label>
          {mustType && (
            <span className="hint">There is no calculated expiry to accept — enter the date the contract ends.</span>
          )}
          <input id={expiryId} type="date" className="sel" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
          <label className="lbl" htmlFor={noticeId}>
            Notice deadline (only to change it)
          </label>
          <input id={noticeId} type="date" className="sel" value={noticeBy} onChange={(e) => setNoticeBy(e.target.value)} />
          <span className="hint">A deadline on a weekend is not moved later — act by the day shown.</span>
        </div>
      )}
    </Modal>
  );
}

function DecideModal({
  renewal,
  onClose,
  onDecided,
}: {
  renewal: RenewalEvent | null;
  onClose: () => void;
  onDecided: () => void;
}) {
  const { notify } = useToast();
  const [decision, setDecision] = useState<RenewalDecision>("renew");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const decisionId = useId();
  const noteId = useId();

  // Advisory AI suggestion, grounded in the contract's facts on file.
  const { data: rec, isLoading: recLoading } = useQuery({
    queryKey: ["renewal-rec", renewal?.id],
    queryFn: () => renewalsApi.recommendation(renewal!.id),
    enabled: !!renewal,
    staleTime: 5 * 60_000,
  });

  async function submit() {
    if (!renewal) return;
    setBusy(true);
    try {
      await renewalsApi.decide(renewal.id, decision, note.trim() || undefined);
      setDecision("renew");
      setNote("");
      onDecided();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Decision failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={!!renewal}
      onClose={onClose}
      title="Record renewal decision"
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn pri" disabled={busy} onClick={submit}>
            {busy ? "Saving…" : "Save decision"}
          </button>
        </>
      }
    >
      <div className="mbody">
        {renewal && (recLoading || rec) && (
          <div className="rec">
            {recLoading ? (
              <div className="recload">✦ Aegis is analysing this contract…</div>
            ) : rec ? (
              <>
                <div className="rechd">
                  <span className="reclabel">✦ Aegis suggests</span>
                  <span className={`recpill ${rec.decision}`}>{titleCase(rec.decision)}</span>
                  <span className="recconf">
                    {rec.confidence} confidence{rec.generated ? "" : " · heuristic"}
                  </span>
                </div>
                <p className="recwhy">{rec.rationale}</p>
                {decision !== rec.decision && (
                  <button className="btn sm rcuse" onClick={() => setDecision(rec.decision)}>
                    Use this suggestion
                  </button>
                )}
              </>
            ) : null}
          </div>
        )}
        <label className="lbl" htmlFor={decisionId}>
          Decision
        </label>
        <select
          id={decisionId}
          className="sel"
          value={decision}
          onChange={(e) => setDecision(e.target.value as RenewalDecision)}
        >
          {DECISIONS.map((d) => (
            <option key={d} value={d}>
              {titleCase(d)}
            </option>
          ))}
        </select>

        <label className="lbl" htmlFor={noteId}>
          Note
        </label>
        <span className="hint">Optional context for this decision.</span>
        <textarea
          id={noteId}
          className="txt"
          rows={4}
          placeholder="Add a note…"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </div>
    </Modal>
  );
}

const RENW_CSS = `
.renw{--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--sans:var(--font-sans);--mono:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .renw{--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4)}
.renw *{box-sizing:border-box}
.renw .btn.sm{padding:6px 11px;font-size:12px;display:inline-flex;align-items:center;gap:6px}
.renw .sic{width:14px;height:14px}
.renw .trunc{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.renw .kcard .ks{font-size:11px;color:var(--ink-3)}
@keyframes rnin{from{opacity:0;transform:translateY(14px) scale(.99)}to{opacity:1;transform:none}}
.renw .rnin{animation:rnin .5s cubic-bezier(.2,.8,.2,1) both}
.renw .rnwarn{display:flex;gap:12px;align-items:flex-start;margin:14px 0 0;padding:12px 16px;border-radius:16px;border:1px solid color-mix(in srgb,var(--warn) 35%,var(--border));background:var(--warn-soft);font-size:12.5px;color:var(--ink-2)}
.renw .rnwarn b{color:var(--ink)}
.renw .wic{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;background:var(--warn);color:#fff;flex:none}
.renw .rncard{margin-top:14px;border-radius:20px;background:var(--surface);box-shadow:0 1px 2px rgba(20,26,40,.04),0 0 0 1px var(--border);padding:14px 16px 8px}
.renw .rncard header{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.renw .rncard h3{margin:0;font-size:14px;font-weight:660;text-transform:none;letter-spacing:-.005em;color:var(--ink)}
.renw .rnleg{margin-left:auto;display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--ink-3)}
.renw .rnleg i{width:9px;height:9px;border-radius:3px;margin-left:10px}.renw .rnleg i.d{background:var(--c-amb)}.renw .rnleg i.o{background:var(--c-emr)}
.renw .rntools{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:14px 0}
.renw .rnlist{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.renw .rnrow{--st:var(--border-strong);position:relative;border-radius:16px;background:var(--surface);box-shadow:0 1px 2px rgba(20,26,40,.04),0 0 0 1px var(--border);opacity:0;animation:rnin .55s cubic-bezier(.2,.8,.2,1) forwards;transition:transform .22s cubic-bezier(.2,.8,.2,1),box-shadow .22s}
.renw .rnrow.st-crit{--st:var(--crit)}.renw .rnrow.st-warn{--st:var(--warn)}.renw .rnrow.st-good{--st:var(--good)}
.renw .rnrow:hover{transform:translateY(-2px);box-shadow:0 16px 30px -22px rgba(20,26,40,.45),0 0 0 1px var(--border-strong)}
.renw .rnrow.late{animation:rnin .55s cubic-bezier(.2,.8,.2,1) forwards,rnalert 2.6s 1.2s ease-in-out infinite}
@keyframes rnalert{0%,100%{box-shadow:0 1px 2px rgba(20,26,40,.05),0 0 0 1px color-mix(in srgb,var(--crit) 40%,var(--border)),0 0 0 0 color-mix(in srgb,var(--crit) 30%,transparent)}50%{box-shadow:0 1px 2px rgba(20,26,40,.05),0 0 0 1px color-mix(in srgb,var(--crit) 65%,var(--border)),0 0 0 6px color-mix(in srgb,var(--crit) 0%,transparent)}}
.renw .rngrid{display:grid;grid-template-columns:48px minmax(0,2.4fr) minmax(0,2.6fr) 96px auto;gap:16px;align-items:center;padding:12px 16px 12px 20px;border-radius:16px}
.renw .rngrid.click{cursor:pointer}
.renw .rngrid.click:hover .lnk{color:var(--accent)}
.renw .rnring{position:relative;width:48px;height:48px;display:grid;place-items:center}
.renw .rnring svg{position:absolute;inset:0}
.renw .rnring .rt{fill:none;stroke:var(--surface-2);stroke-width:4}
.renw .rnring .rv{fill:none;stroke-width:4;stroke-linecap:round;transition:stroke-dashoffset 1.1s cubic-bezier(.2,.8,.2,1)}
.renw .rnring b{position:relative;font-size:13px;font-weight:700;line-height:1;margin-top:-5px}
.renw .rnring em{position:absolute;bottom:9px;font-style:normal;font-size:8.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--ink-3)}
.renw .rnring.crit b{color:var(--crit)}.renw .rnring.crit{animation:rnpulse 1.8s ease-in-out infinite}
@keyframes rnpulse{50%{transform:scale(1.07)}}
.renw .rnmain{min-width:0;display:flex;flex-direction:column;gap:5px}
.renw .rntitle{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:670;min-width:0;letter-spacing:-.01em}
.renw .rntitle .lnk{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.renw .lnk{color:var(--ink);text-decoration:none}
.renw .rnref{flex:none;font:700 10px var(--mono);color:var(--accent);background:var(--accent-soft);padding:1px 7px;border-radius:99px}
.renw .rnmeta{display:flex;align-items:center;gap:5px;min-width:0;overflow:hidden}
.renw .rnchip{display:inline-flex;align-items:center;gap:5px;flex:0 1 auto;min-width:0;max-width:200px;height:20px;padding:0 8px;border-radius:99px;font-size:11px;color:var(--ink-2);background:var(--surface-2);text-decoration:none;white-space:nowrap}
.renw .rnchip svg{color:var(--ink-3);flex:none}
.renw a.rnchip:hover{background:var(--accent-soft);color:var(--accent)}
.renw .rndst{font-size:11px;font-weight:600;white-space:nowrap}
.renw .rndst.good{color:var(--good)}.renw .rndst.warn{color:var(--warn)}.renw .rndst.crit{color:var(--crit)}
.renw .rndates{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.renw .rdate{display:flex;flex-direction:column;min-width:0;gap:1px}
.renw .rdate span{font:600 9.5px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3)}
.renw .rdate b{font-size:12.5px;font-weight:650;color:var(--ink);white-space:nowrap}
.renw .rdate em{font-style:normal;font-size:11px;color:var(--ink-3)}
.renw .rdate.late b{color:var(--crit)}
.renw .rdate em.tg{font-weight:700}.renw .rdate em.tg.crit{color:var(--crit)}.renw .rdate em.tg.warn{color:var(--warn)}.renw .rdate em.tg.good{color:var(--good)}
.renw .rndec .tag{margin-left:0}
.renw .rnact{display:flex;align-items:center;justify-content:flex-end;gap:6px;white-space:nowrap}
.renw .rnhow{margin:0 16px 10px 84px;font-size:11.5px;color:var(--ink-3)}
.renw .rnhow summary{cursor:pointer;font-weight:600;color:var(--accent);width:max-content}
.renw .rnhow .fml{margin:3px 0 0;max-width:none}
.renw .rnpager{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:14px;padding:0 4px;font-size:12.5px;color:var(--ink-2)}
.renw .rnempty{display:flex;flex-direction:column;align-items:center;text-align:center;padding:56px 16px;border:1px dashed var(--border-strong);border-radius:18px;margin-top:14px}
.renw .rnempty.err{color:var(--crit)}
.renw .rnempty .eic{width:48px;height:48px;border-radius:14px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;margin-bottom:10px}
.renw .rnempty .et{margin:0;font-weight:700;font-size:15px;color:var(--ink)}
.renw .rnempty .ed{margin:3px 0 0;font-size:12.5px;color:var(--ink-2)}
@media (max-width:1400px){
  .renw .rngrid{grid-template-columns:48px minmax(0,1fr);row-gap:10px}
  .renw .rngrid>.rndates,.renw .rngrid>.rndec,.renw .rngrid>.rnact{grid-column:2}
  .renw .rnact{justify-content:flex-start}
  .renw .rnhow{margin-left:20px}
}
@media (prefers-reduced-motion:reduce){.renw *{animation:none!important;transition:none!important}.renw .rnrow{opacity:1!important}}
.renw .rowacts{display:flex;justify-content:flex-end;gap:6px}
.renw .rowacts.pick{justify-content:flex-start;flex-wrap:wrap}
.renw .note{margin:0;font-size:12.5px;color:var(--ink-2)}
.renw .dates{display:grid;grid-template-columns:auto 1fr;gap:6px 12px;margin:4px 0;font-size:12.5px}
.renw .dates dt{font-weight:600;color:var(--ink-2)}
.renw .dates dd{margin:0}
.renw .bar{width:3px;align-self:stretch;min-height:16px;border-radius:99px;background:var(--border-strong);flex-shrink:0}
.renw .bar.crit{background:var(--crit)} .renw .bar.warn{background:var(--warn)} .renw .bar.good{background:var(--good)}
.renw .tag{display:inline-flex;align-items:center;font:600 10.5px var(--sans);letter-spacing:.02em;padding:2px 8px;border-radius:99px;margin-left:8px;white-space:nowrap}
.renw .tag.slate{background:var(--surface-2);color:var(--ink-2)}
.renw .tag.good{background:var(--good-soft);color:var(--good)}
.renw .tag.warn{background:var(--warn-soft);color:var(--warn)}
.renw .tag.crit{background:var(--crit-soft);color:var(--crit)}


.renw .eicon{width:22px;height:22px;color:var(--ink-3)}
.renw .grid{display:flex;flex-direction:column;gap:8px}
.renw .skel{height:34px;border-radius:8px;background:var(--surface-2);animation:renwpulse 1.4s ease-in-out infinite}
@keyframes renwpulse{50%{opacity:.55}}
.renw .mbody{display:flex;flex-direction:column;gap:8px}
.renw .rec{border:1px solid var(--accent-soft);background:var(--accent-soft);border-radius:11px;padding:11px 12px;margin-bottom:6px;display:flex;flex-direction:column;gap:7px}
.renw .recload{font-size:12.5px;color:var(--accent);font-weight:600}
.renw .rechd{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.renw .reclabel{font:700 11px var(--sans);letter-spacing:.02em;color:var(--accent)}
.renw .recpill{font:700 10.5px var(--sans);text-transform:uppercase;letter-spacing:.03em;padding:2px 9px;border-radius:99px;background:var(--surface);color:var(--accent)}
.renw .recpill.renew{color:var(--good)} .renw .recpill.renegotiate{color:var(--warn)} .renw .recpill.terminate{color:var(--crit)}
.renw .recconf{font-size:11px;color:var(--ink-3);text-transform:capitalize}
.renw .recwhy{margin:0;font-size:12.5px;line-height:1.5;color:var(--ink)}
.renw .rcuse{align-self:flex-start;background:var(--surface)}
.renw .lbl{font:600 11.5px var(--sans);color:var(--ink-2)}
.renw .hint{font-size:11.5px;color:var(--ink-3);margin-top:-4px}
.renw .sel,.renw .txt{width:100%;border-radius:9px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font:13px var(--sans);padding:7px 10px}
.renw .sel:focus,.renw .txt:focus{outline:2px solid var(--accent-soft);border-color:var(--accent)}
.renw .txt{resize:vertical}
`;
