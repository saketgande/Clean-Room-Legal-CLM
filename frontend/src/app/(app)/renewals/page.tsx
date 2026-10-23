"use client";

// Renewals — upcoming contract renewal/auto-renewal tracking in the new
// mockup style (scoped `.renw`). Deadline urgency (crit/warn/good) is derived
// from days-until-notice-date for undecided events.

import { useId, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, RefreshCw } from "lucide-react";
import { contractsApi, renewalsApi } from "@/lib/endpoints";
import { Modal } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/utils";
import { daysToExpiry, expiringWithoutRenewal } from "@/lib/expiry";
import { useToast } from "@/components/toast";
import type { RenewalEvent } from "@/lib/types";
import {
  EVERGREEN_NOTE, STATUS_TEXT, STATUS_TONE, canConfirm, conflictField, conflictText, evergreenConflict, mustTypeExpiry,
  recomputed,
} from "./dates";
import { conflictChoices } from "../obligations/deadline";

const ic = (p: string) => (
  <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" dangerouslySetInnerHTML={{ __html: p }} />
);

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

export default function RenewalsPage() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [deciding, setDeciding] = useState<RenewalEvent | null>(null);
  const [confirming, setConfirming] = useState<RenewalEvent | null>(null);
  const [windowBusy, setWindowBusy] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["renewals"],
    queryFn: () => renewalsApi.list(),
  });
  const rows = data ?? [];
  // Running contracts that end within 90 days but have no row here: the Contracts screen counts them.
  const { data: contracts } = useQuery({ queryKey: ["contracts-list"], queryFn: contractsApi.list });
  const untracked = expiringWithoutRenewal(contracts ?? [], rows);
  const undecided = rows.filter((r) => r.decision === "undecided").length;
  const dueSoon = rows.filter((r) => {
    const u = noticeUrgency(r);
    return u && (u.tone === "crit" || u.tone === "warn");
  }).length;
  const decided = rows.length - undecided;

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

  return (
    <div className="renw">
      <style dangerouslySetInnerHTML={{ __html: RENW_CSS }} />
      <div className="hd">
        <div>
          <h1>Renewals</h1>
          <p className="sub">Monitor upcoming expirations and record renewal decisions.</p>
        </div>
        <div className="acts">
          <button className="btn" disabled={windowBusy} onClick={runWindowCheck}>
            <RefreshCw className="sic" />
            {windowBusy ? "Checking…" : "Run window check"}
          </button>
        </div>
      </div>

      {rows.length > 0 && (
        <div className="stats">
          <div className="stat">
            <span className="si a">{ic('<path d="M8 2v4M16 2v4M3 10h18"/><rect x="3" y="4" width="18" height="18" rx="2"/>')}</span>
            <div><div className="sv">{rows.length}</div><div className="sl">renewal{rows.length === 1 ? "" : "s"}</div></div>
          </div>
          <div className="stat">
            <span className="si b">{ic('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>')}</span>
            <div><div className="sv">{undecided}</div><div className="sl">undecided</div></div>
          </div>
          <div className="stat">
            <span className="si w">{ic('<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>')}</span>
            <div><div className="sv">{dueSoon}</div><div className="sl">due soon</div></div>
          </div>
          <div className="stat">
            <span className="si g">{ic('<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>')}</span>
            <div><div className="sv">{decided}</div><div className="sl">decided</div></div>
          </div>
        </div>
      )}

      {untracked.length > 0 && (
        <div className="empty" role="note" style={{ marginBottom: 12, textAlign: "left" }}>
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
      )}

      {isLoading ? (
        <div className="tablewrap">
          <div className="grid" style={{ padding: 14 }}>
            {[0, 1, 2].map((i) => (
              <div key={i} className="skel" />
            ))}
          </div>
        </div>
      ) : error ? (
        <div className="empty err">
          {error instanceof Error ? error.message : "Couldn't load renewals."}
        </div>
      ) : rows.length === 0 ? (
        <div className="empty">
          <CalendarClock className="eicon" />
          No renewal events. Renewal events are created as contracts approach their expiration
          window.
        </div>
      ) : (
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Contract</th>
                <th>Expiration</th>
                <th>Notice date</th>
                <th>Window starts</th>
                <th>Decision</th>
                <th className="right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const notice = noticeUrgency(r);
                return (
                  <tr key={r.id} className={notice ? `u-${notice.tone}` : undefined}>
                    <td>
                      <div className="ttl">
                        <span className={`bar ${notice?.tone ?? "none"}`} />
                        <div>
                          {r.contract_title ?? "Untitled contract"}
                          {r.dates_status && (
                            <p className={`dst ${STATUS_TONE[r.dates_status]}`}>{STATUS_TEXT[r.dates_status]}</p>
                          )}
                          {r.date_conflict && <p className="fml">{conflictText(r.date_conflict, fmtDate)}</p>}
                          {r.date_reason && <p className="fml">{r.date_reason}</p>}
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className="nowrap">{fmtDate(r.expiration_date)}</span>
                      {r.formulas.expiration_date && <p className="fml">{r.formulas.expiration_date}</p>}
                      {r.next_expiration_date && (
                        <p className="fml">
                          {r.formulas.next_expiration_date ?? `next term ends ${fmtDate(r.next_expiration_date)}`} (if not
                          stopped)
                        </p>
                      )}
                    </td>
                    <td>
                      <span className="nowrap">
                        {fmtDate(r.notice_date)}
                        {notice && <span className={`tag ${notice.tone}`}>{notice.label}</span>}
                      </span>
                      {r.formulas.notice_date && <p className="fml">{r.formulas.notice_date}</p>}
                    </td>
                    <td>
                      <span className="nowrap">{fmtDate(r.renewal_window_starts_at)}</span>
                      {r.formulas.window_opens && <p className="fml">{r.formulas.window_opens}</p>}
                    </td>
                    <td className="nowrap">
                      <span className={`tag ${DECISION_TONE[r.decision]}`}>
                        {titleCase(r.decision)}
                      </span>
                    </td>
                    <td className="right">
                      <div className="rowacts">
                        {r.dates_status && r.dates_status !== "confirmed" && (
                          <button className="btn sm" onClick={() => setConfirming(r)}>
                            {canConfirm(r) ? "Confirm dates" : "Set dates"}
                          </button>
                        )}
                        <button className="btn sm" onClick={() => setDeciding(r)}>
                          Decide
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
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
.renw{--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--sans:var(--font-sans);--mono:ui-monospace,SFMono-Regular,Menlo,monospace;padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .renw{--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4)}
.renw .hd{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.renw .hd h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.015em}
.renw .hd .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:640px}
.renw .acts{margin-left:auto;display:flex;gap:8px}
.renw .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:20px}
.renw .stat{display:flex;align-items:center;gap:12px;border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);padding:14px 16px}
.renw .si{flex:none;display:grid;place-items:center;width:38px;height:38px;border-radius:10px;background:var(--surface-2);color:var(--ink-2)}
.renw .si.a{background:var(--accent-soft);color:var(--accent)} .renw .si.b{background:var(--accent-soft);color:var(--accent)} .renw .si.w{background:var(--warn-soft);color:var(--warn)} .renw .si.g{background:var(--good-soft);color:var(--good)}
.renw .si .ic{width:19px;height:19px}
.renw .sv{font-size:21px;font-weight:700;letter-spacing:-.02em;line-height:1} .renw .sl{margin-top:3px;font-size:11.5px;color:var(--ink-2)}




.renw .btn.sm{padding:5px 10px;font-size:12px}

.renw .sic{width:14px;height:14px}
.renw .tablewrap{overflow-x:auto}
.renw table{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px;min-width:760px}
.renw thead th{text-align:left;font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);padding:9px 12px;border-bottom:1px solid var(--border);white-space:nowrap}
.renw thead th.right,.renw td.right{text-align:right}
.renw tbody td{padding:9px 12px;border-bottom:1px solid var(--border);vertical-align:top}
.renw tbody tr:last-child td{border-bottom:0}
.renw tbody tr:hover td{background:var(--inset)}
.renw tbody tr.u-crit td{background:var(--crit-soft)}
.renw tbody tr.u-crit:hover td{background:var(--crit-soft)}
.renw td.nowrap{white-space:nowrap}
.renw .ttl{font-weight:560;color:var(--ink);display:flex;align-items:flex-start;gap:8px}
.renw .fml{display:block;margin:2px 0 0;font:400 11px var(--mono);color:var(--ink-3);max-width:280px;white-space:normal}
.renw .dst{margin:2px 0 0;font-size:11px;font-weight:600}
.renw .dst.good{color:var(--good)} .renw .dst.warn{color:var(--warn)} .renw .dst.crit{color:var(--crit)}
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
