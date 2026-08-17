"use client";

import { Fragment, useEffect, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlarmClock,
  CheckCircle2,
  ChevronRight,
  ClipboardList,
  Clock,
  ListChecks,
  Quote,
  Repeat,
  User2,
  X,
} from "lucide-react";
import { obligationsApi } from "@/lib/endpoints";
import { fmtDate, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { Obligation, ObligationStatus } from "@/lib/types";

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

/** What the contract says about WHEN this obligation applies. A fixed calendar
 * date is rare; most obligations are conditional ("upon receipt…") or ongoing
 * ("during the term and 12 months after"), which lives in `recurrence`. */
function timing(o: Obligation): {
  label: string;
  sub: string | null;
  tone: "date" | "recurring" | "conditional";
} {
  if (o.due_date) {
    const d = new Date(o.due_date);
    const days = Math.round((d.getTime() - Date.now()) / 86_400_000);
    const rel =
      days < 0 ? `${Math.abs(days)}d overdue` : days === 0 ? "today" : `in ${days}d`;
    return { label: fmtDate(o.due_date), sub: rel, tone: "date" };
  }
  if (o.recurrence) return { label: o.recurrence, sub: "recurring / ongoing", tone: "recurring" };
  return { label: "On trigger", sub: "conditional — no fixed date", tone: "conditional" };
}

export default function ObligationsPage() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState<Obligation | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [remindersBusy, setRemindersBusy] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["obligations", status],
    queryFn: () => obligationsApi.list(status ? { status_filter: status } : {}),
  });
  const obligations = data ?? [];

  const counts: Record<ObligationStatus, number> = {
    open: 0,
    due_soon: 0,
    overdue: 0,
    completed: 0,
    cancelled: 0,
  };
  for (const o of obligations) counts[o.status] += 1;

  async function runReminders() {
    setRemindersBusy(true);
    try {
      const r = await obligationsApi.runReminders();
      qc.invalidateQueries({ queryKey: ["obligations"] });
      notify(
        `${r.reminders_sent} reminders sent · ${r.marked_due_soon} due soon · ${r.marked_overdue} overdue`,
        "success",
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

  return (
    <div className="oblg">
      <style dangerouslySetInnerHTML={{ __html: OBLG_CSS }} />
      <div className="hd">
        <div>
          <h1>Obligations</h1>
          <p className="sub">
            Every commitment extracted from your contracts — who owes it, which agreement it comes
            from, and when it applies.
          </p>
        </div>
        <div className="acts">
          <button className="btn" disabled={remindersBusy} onClick={runReminders}>
            <AlarmClock size={14} />
            {remindersBusy ? "Running…" : "Run reminders"}
          </button>
        </div>
      </div>

      <div className="statcard">
        <div className="stats">
          <StatTile label="Open" value={counts.open} tone="accent" icon={<ListChecks size={16} />} />
          <StatTile label="Due soon" value={counts.due_soon} tone="warn" icon={<Clock size={16} />} />
          <StatTile label="Overdue" value={counts.overdue} tone="crit" icon={<AlarmClock size={16} />} />
          <StatTile label="Completed" value={counts.completed} tone="good" icon={<CheckCircle2 size={16} />} />
          <StatTile label="Cancelled" value={counts.cancelled} tone="muted" icon={<ClipboardList size={16} />} />
        </div>
      </div>

      <div className="filters">
        <label className="fld">
          <span className="flabel">Status</span>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </select>
        </label>
        <p className="hint">Click any row to see the exact contract language it came from.</p>
      </div>

      {isLoading ? (
        <div className="tbl">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="skrow" />
          ))}
        </div>
      ) : error ? (
        <div className="empty err">{error instanceof Error ? error.message : "Couldn't load obligations."}</div>
      ) : obligations.length === 0 ? (
        <div className="empty">
          <ListChecks size={20} />
          <div>
            <p className="et">No obligations found</p>
            <p className="ed">Obligations extracted from active contracts will appear here.</p>
          </div>
        </div>
      ) : (
        <div className="tbl">
          <table>
            <thead>
              <tr>
                <th className="chev"> </th>
                <th>Obligation</th>
                <th>Responsible party</th>
                <th>Contract</th>
                <th>Timing</th>
                <th>Status</th>
                <th className="right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {obligations.map((o) => {
                const t = timing(o);
                const open = expanded === o.id;
                const tone = STATUS_TONE[o.status];
                const quote = (o.source_citation as { quote?: string } | null)?.quote;
                const meta = o.metadata_json as {
                  source_clause_type?: string;
                  confidence?: string;
                } | null;
                return (
                  <Fragment key={o.id}>
                    <tr className={`row${open ? " open" : ""}`} onClick={() => setExpanded(open ? null : o.id)}>
                      <td className="chev">
                        <ChevronRight size={15} className="chevicon" />
                      </td>
                      <td className="obl">
                        {o.obligation_type && <span className="tag">{o.obligation_type}</span>}
                        <p className="desc">{o.description}</p>
                      </td>
                      <td>
                        <div className="party">
                          <User2 size={13} className="dim" />
                          <div>
                            <p className="pname">{o.responsible_party ?? "Unassigned"}</p>
                            {o.owner_name && <p className="psub">tracked by {o.owner_name}</p>}
                          </div>
                        </div>
                      </td>
                      <td>
                        <Link
                          href={`/contracts/${o.contract_id}`}
                          onClick={(e) => e.stopPropagation()}
                          className="clink"
                        >
                          {o.contract_title ?? o.contract_id}
                        </Link>
                        {o.counterparty_name && <p className="psub">with {o.counterparty_name}</p>}
                      </td>
                      <td>
                        <div className="party">
                          {t.tone === "recurring" ? (
                            <Repeat size={13} className="dim" />
                          ) : (
                            <Clock size={13} className="dim" />
                          )}
                          <div>
                            <p className={`pname${t.tone === "date" ? " strong" : ""}`}>{t.label}</p>
                            {t.sub && (
                              <p
                                className="psub"
                                style={{
                                  color:
                                    tone === "crit"
                                      ? "var(--crit)"
                                      : tone === "warn"
                                        ? "var(--warn)"
                                        : undefined,
                                }}
                              >
                                {t.sub}
                              </p>
                            )}
                          </div>
                        </div>
                      </td>
                      <td>
                        <span className={`pill ${tone}`}>{titleCase(o.status)}</span>
                      </td>
                      <td className="right" onClick={(e) => e.stopPropagation()}>
                        <div className="rowacts">
                          {o.status !== "completed" && (
                            <button className="btn sm pri" disabled={busyId === o.id} onClick={() => complete(o)}>
                              {busyId === o.id ? "…" : "Complete"}
                            </button>
                          )}
                          <button className="btn sm" onClick={() => setEditing(o)}>
                            Edit
                          </button>
                        </div>
                      </td>
                    </tr>
                    {open && (
                      <tr className="detail">
                        <td />
                        <td colSpan={6}>
                          <div className="dgrid">
                            <div>
                              <p className="dlabel">
                                <Quote size={13} /> What the contract says
                              </p>
                              {quote ? (
                                <blockquote className="quote">&ldquo;{quote}&rdquo;</blockquote>
                              ) : (
                                <p className="nosrc">No source excerpt was captured for this obligation.</p>
                              )}
                              <p className="descfull">{o.description}</p>
                            </div>
                            <div className="drows">
                              <DetailRow label="Responsible party" value={o.responsible_party} />
                              <DetailRow
                                label="Internal owner"
                                value={o.owner_name ?? "Unassigned — assign one via Edit"}
                              />
                              <DetailRow
                                label="Timing"
                                value={
                                  o.due_date
                                    ? `Due ${fmtDate(o.due_date)}`
                                    : o.recurrence ?? "Conditional — applies on trigger, no fixed date"
                                }
                              />
                              <DetailRow label="Category" value={o.obligation_type} />
                              {meta?.source_clause_type && (
                                <DetailRow label="From clause" value={meta.source_clause_type} />
                              )}
                              {meta?.confidence && (
                                <DetailRow label="Extraction confidence" value={titleCase(meta.confidence)} />
                              )}
                              <Link href={`/contracts/${o.contract_id}`} className="openlink">
                                Open {o.contract_title ?? "contract"} →
                              </Link>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
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
}: {
  label: string;
  value: number;
  tone: "accent" | "warn" | "crit" | "good" | "muted";
  icon: React.ReactNode;
}) {
  return (
    <div className="stat">
      <span className={`sic ${tone}`}>{icon}</span>
      <div>
        <p className="sval">{value}</p>
        <p className="slabel">{label}</p>
      </div>
    </div>
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
      await obligationsApi.update(obligation.id, {
        owner_user_id: ownerUserId || null,
        responsible_party: responsibleParty || null,
        obligation_type: obligationType || null,
        status,
        due_date: dueDate || null,
        recurrence: recurrence || null,
      });
      onSaved();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "error");
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
                value={responsibleParty}
                onChange={(e) => setResponsibleParty(e.target.value)}
              />
              <span className="fhint">Which party owes this — e.g. Receiving Party, both parties</span>
            </label>
            <label className="fld">
              <span className="flabel">Internal owner (user ID)</span>
              <input value={ownerUserId} onChange={(e) => setOwnerUserId(e.target.value)} />
              <span className="fhint">Who on your team tracks it</span>
            </label>
            <label className="fld">
              <span className="flabel">Obligation type</span>
              <input
                placeholder="e.g. payment"
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
              <span className="fhint">Leave blank for conditional / ongoing obligations</span>
            </label>
            <label className="fld">
              <span className="flabel">Recurrence / timing</span>
              <input
                placeholder="e.g. monthly"
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
.oblg{--surface:#fff;--surface-2:#eef1f6;--inset:#f8fafc;--ink:#18213a;--ink-2:#586178;--ink-3:#8a92a6;--border:#e4e8f0;--border-strong:#ccd3e0;--accent:#3b4aa0;--accent-ink:#fff;--accent-soft:#eaecf8;--good:#2f875f;--good-soft:#e4f1ea;--warn:#a9772b;--warn-soft:#f6edd9;--crit:#bb4835;--crit-soft:#f7e4df;--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--pop:0 12px 30px rgba(20,26,40,.16);--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,monospace;padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .oblg{--surface:#141922;--surface-2:#1b2130;--inset:#10141d;--ink:#e8ebf3;--ink-2:#9aa3b8;--ink-3:#6b7488;--border:#242b39;--border-strong:#333c4e;--accent:#8290e6;--accent-ink:#0c0f16;--accent-soft:#1f2740;--good:#5cbf90;--good-soft:#15271f;--warn:#d3a24e;--warn-soft:#2a2213;--crit:#e2705c;--crit-soft:#2c1a17;--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4);--pop:0 14px 34px rgba(0,0,0,.55)}
.oblg .dim{color:var(--ink-3)}
.oblg .hd{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.oblg .hd h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.015em}
.oblg .hd .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:640px}
.oblg .acts{margin-left:auto;display:flex;gap:8px}
.oblg .btn{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border-radius:9px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;font-size:12.5px;cursor:pointer}
.oblg .btn:hover{background:var(--surface-2)}
.oblg .btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
.oblg .btn.pri:hover{filter:brightness(1.06)}
.oblg .btn[disabled]{opacity:.6;pointer-events:none}
.oblg .btn.sm{padding:5px 10px;font-size:11.5px;border-radius:7px}

.oblg .statcard{border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);padding:14px;margin-bottom:14px}
.oblg .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.oblg .stat{display:flex;align-items:center;gap:10px;border:1px solid var(--border);border-radius:10px;background:var(--inset);padding:10px 12px}
.oblg .sic{width:32px;height:32px;flex:none;border-radius:9px;display:grid;place-items:center}
.oblg .sic.accent{background:var(--accent-soft);color:var(--accent)}
.oblg .sic.warn{background:var(--warn-soft);color:var(--warn)}
.oblg .sic.crit{background:var(--crit-soft);color:var(--crit)}
.oblg .sic.good{background:var(--good-soft);color:var(--good)}
.oblg .sic.muted{background:var(--surface-2);color:var(--ink-3)}
.oblg .sval{margin:0;font-size:18px;font-weight:700;letter-spacing:-.01em;line-height:1.1}
.oblg .slabel{margin:2px 0 0;font-size:11.5px;color:var(--ink-2)}

.oblg .filters{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:14px}
.oblg .fld{display:flex;flex-direction:column;gap:4px;font-size:12px}
.oblg .flabel{font-weight:600;color:var(--ink-2);font-size:11.5px}
.oblg .fhint{font-size:11px;color:var(--ink-3)}
.oblg .filters select,.oblg .fld select,.oblg .fld input{border:1px solid var(--border-strong);border-radius:8px;background:var(--surface);color:var(--ink);font:inherit;font-size:12.5px;padding:7px 10px;min-width:180px}
.oblg .filters select:focus,.oblg .fld select:focus,.oblg .fld input:focus{outline:2px solid var(--accent);outline-offset:1px}
.oblg .hint{margin:0 0 0 auto;font-size:12.5px;color:var(--ink-3)}

.oblg .tbl{overflow-x:auto}
.oblg table{width:100%;border-collapse:collapse}
.oblg thead th{text-align:left;font:600 10.5px var(--sans);letter-spacing:.05em;text-transform:uppercase;color:var(--ink-3);padding:10px 14px;border-bottom:1px solid var(--border)}
.oblg thead th.right{text-align:right}
.oblg thead th.chev{width:28px}
.oblg tbody .row{cursor:pointer;border-bottom:1px solid var(--border)}
.oblg tbody .row:hover{background:var(--inset)}
.oblg tbody .row td{padding:11px 14px;vertical-align:top}
.oblg tbody .row.open{background:var(--inset)}
.oblg .chevicon{transition:transform .12s;color:var(--ink-3)}
.oblg .row.open .chevicon{transform:rotate(90deg)}
.oblg td.right{text-align:right}
.oblg .obl{max-width:340px}
.oblg .tag{display:inline-block;margin-bottom:4px;border-radius:6px;background:var(--accent-soft);color:var(--accent);font:600 10.5px var(--sans);letter-spacing:.04em;text-transform:uppercase;padding:2px 7px}
.oblg .desc{margin:0;font-weight:600;font-size:12.5px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.oblg .party{display:flex;align-items:flex-start;gap:6px}
.oblg .pname{margin:0;font-weight:600;font-size:12.5px;color:var(--ink-2)}
.oblg .pname.strong{color:var(--ink)}
.oblg .psub{margin:2px 0 0;font-size:11px;color:var(--ink-3)}
.oblg .clink{font-weight:600;color:var(--accent);text-decoration:none;font-size:12.5px}
.oblg .clink:hover{text-decoration:underline}
.oblg .rowacts{display:flex;justify-content:flex-end;gap:6px}

.oblg .pill{display:inline-block;font:600 10.5px var(--sans);letter-spacing:.02em;padding:3px 9px;border-radius:99px}
.oblg .pill.accent{background:var(--accent-soft);color:var(--accent)}
.oblg .pill.warn{background:var(--warn-soft);color:var(--warn)}
.oblg .pill.crit{background:var(--crit-soft);color:var(--crit)}
.oblg .pill.good{background:var(--good-soft);color:var(--good)}
.oblg .pill.muted{background:var(--surface-2);color:var(--ink-3)}

.oblg tr.detail td{background:var(--inset);padding:16px;border-bottom:1px solid var(--border)}
.oblg .dgrid{display:grid;grid-template-columns:1.4fr 1fr;gap:20px}
.oblg .dlabel{display:flex;align-items:center;gap:6px;margin:0 0 6px;font:600 10.5px var(--sans);letter-spacing:.05em;text-transform:uppercase;color:var(--ink-3)}
.oblg .quote{margin:0;border-left:2px solid var(--accent);background:var(--surface-2);border-radius:0 8px 8px 0;padding:8px 12px;font-size:12.5px;font-style:italic;line-height:1.55;color:var(--ink-2)}
.oblg .nosrc{margin:0;font-size:12.5px;color:var(--ink-3)}
.oblg .descfull{margin:8px 0 0;font-size:12.5px;line-height:1.55;color:var(--ink-2)}
.oblg .drows{display:flex;flex-direction:column;gap:10px;font-size:12.5px}
.oblg .drow{display:flex;gap:10px}
.oblg .dk{width:130px;flex:none;font:700 10.5px var(--sans);letter-spacing:.04em;text-transform:uppercase;color:var(--ink-3)}
.oblg .dv{color:var(--ink-2)}
.oblg .openlink{display:inline-block;padding-top:2px;font-weight:700;font-size:12.5px;color:var(--accent);text-decoration:none}
.oblg .openlink:hover{text-decoration:underline}

.oblg .skrow{height:46px;border-bottom:1px solid var(--border);background:linear-gradient(90deg,var(--surface-2) 25%,var(--inset) 37%,var(--surface-2) 63%);background-size:400% 100%;animation:oblgshimmer 1.4s ease infinite}
.oblg .skrow:last-child{border-bottom:none}
@keyframes oblgshimmer{0%{background-position:100% 0}100%{background-position:0 0}}
.oblg .empty{display:flex;align-items:flex-start;gap:12px;border:1px dashed var(--border-strong);border-radius:12px;background:var(--inset);padding:22px;color:var(--ink-2)}
.oblg .empty.err{border-color:color-mix(in srgb,var(--crit) 40%,var(--border));color:var(--crit)}
.oblg .empty .et{margin:0;font-weight:700;font-size:13px;color:var(--ink)}
.oblg .empty .ed{margin:2px 0 0;font-size:12.5px;color:var(--ink-2)}

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
