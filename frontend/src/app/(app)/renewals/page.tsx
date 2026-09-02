"use client";

// Renewals — upcoming contract renewal/auto-renewal tracking in the new
// mockup style (scoped `.renw`). Deadline urgency (crit/warn/good) is derived
// from days-until-notice-date for undecided events.

import { useId, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, RefreshCw } from "lucide-react";
import { renewalsApi } from "@/lib/endpoints";
import { Modal } from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { RenewalEvent } from "@/lib/types";

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
  const [windowBusy, setWindowBusy] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["renewals"],
    queryFn: () => renewalsApi.list(),
  });
  const rows = data ?? [];
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
                    <td className="ttl">
                      <span className={`bar ${notice?.tone ?? "none"}`} />
                      {r.contract_title ?? "Untitled contract"}
                    </td>
                    <td className="nowrap">{fmtDate(r.expiration_date)}</td>
                    <td className="nowrap">
                      {fmtDate(r.notice_date)}
                      {notice && <span className={`tag ${notice.tone}`}>{notice.label}</span>}
                    </td>
                    <td className="nowrap">{fmtDate(r.renewal_window_starts_at)}</td>
                    <td className="nowrap">
                      <span className={`tag ${DECISION_TONE[r.decision]}`}>
                        {titleCase(r.decision)}
                      </span>
                    </td>
                    <td className="right">
                      <button className="btn sm" onClick={() => setDeciding(r)}>
                        Decide
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

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
.renw tbody td{padding:9px 12px;border-bottom:1px solid var(--border);vertical-align:middle}
.renw tbody tr:last-child td{border-bottom:0}
.renw tbody tr:hover td{background:var(--inset)}
.renw tbody tr.u-crit td{background:var(--crit-soft)}
.renw tbody tr.u-crit:hover td{background:var(--crit-soft)}
.renw td.nowrap{white-space:nowrap}
.renw td.ttl{font-weight:560;color:var(--ink);display:flex;align-items:center;gap:8px}
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
