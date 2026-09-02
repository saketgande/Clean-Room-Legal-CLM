"use client";

/* Legal-notice register.
   Sorted by statutory deadline, soonest first — the register's whole job is to
   make "what lapses next" the first thing you see. Deadline posture comes from
   the server on every read (never stored), so a row can't sit showing "on track"
   for a notice that quietly lapsed overnight. */

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowDownLeft,
  ArrowUpRight,
  BellRing,
  CheckCircle2,
  FileWarning,
  Plus,
  Search,
  ShieldAlert,
  Upload,
} from "lucide-react";
import { noticesApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  CenterSpinner,
  ErrorState,
  Field,
  Input,
  Modal,
  Select,
  Textarea,
} from "@/components/ui";
import { useToast } from "@/components/toast";
import type { Notice, NoticeExtraction } from "@/lib/types";
import { NOTICE_TYPES, POSTURE_TONE, STATUS_TONE, deadlineLabel, typeLabel } from "./shared";

export default function NoticesPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const { notify } = useToast();
  const [creating, setCreating] = useState(false);
  const [tile, setTile] = useState<"all" | "overdue" | "open" | "responded">("all");
  const [q, setQ] = useState("");
  const [direction, setDirection] = useState("");

  const listParams = useMemo(() => {
    const p: Record<string, string | boolean> = {};
    if (tile === "overdue") p.overdue_only = true;
    else if (tile !== "all") p.status_filter = tile;
    if (direction) p.direction = direction;
    if (q.trim()) p.q = q.trim();
    return p;
  }, [tile, direction, q]);

  const { data: notices, isLoading, error } = useQuery({
    queryKey: ["notices", listParams],
    queryFn: () => noticesApi.list(listParams),
  });
  const { data: summary } = useQuery({ queryKey: ["notices-summary"], queryFn: noticesApi.summary });

  const [chasing, setChasing] = useState(false);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["notices"] });
    qc.invalidateQueries({ queryKey: ["notices-summary"] });
  };

  /** Manual equivalent of the nightly sweep. Reports what it did rather than
   *  claiming success, since "0 sent" is the normal, correct outcome once
   *  everyone has already been chased today. */
  async function chaseDeadlines() {
    setChasing(true);
    try {
      const r = await noticesApi.runReminders();
      const parts = [`${r.sent} reminder${r.sent === 1 ? "" : "s"} sent`];
      if (r.skipped_no_owner) parts.push(`${r.skipped_no_owner} unassigned`);
      if (r.already_reminded) parts.push(`${r.already_reminded} already chased`);
      notify(parts.join(" · "), r.sent ? "success" : "info");
      refresh();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not run reminders", "error");
    } finally {
      setChasing(false);
    }
  }

  return (
    <div className="notc">
      <style dangerouslySetInnerHTML={{ __html: NOTC_CSS }} />
      <div className="hd">
        <div>
          <h1>Notice Register</h1>
          <p className="sub">Every legal notice sent or received, ranked by the deadline that lapses next.</p>
        </div>
        <div className="acts">
          <button type="button" className="btn" disabled={chasing} onClick={chaseDeadlines}>
            <BellRing className="ic" />
            {chasing ? "Chasing…" : "Chase deadlines"}
          </button>
          <button type="button" className="btn pri" onClick={() => setCreating(true)}>
            <Plus className="ic" />
            Record notice
          </button>
        </div>
      </div>

      <div className="statcard">
      <div className="tiles">
        <button type="button" className={`tile red${tile === "overdue" ? " active" : ""}`} onClick={() => setTile("overdue")}>
          <span className="tic"><ShieldAlert className="ic" /></span>
          <span className="tval">{summary?.overdue ?? 0}</span>
          <span className="tlbl">Overdue</span>
          <span className="thint">Deadline passed, no reply</span>
        </button>
        <button type="button" className={`tile amber${tile === "all" ? " active" : ""}`} onClick={() => setTile("all")}>
          <span className="tic"><AlertTriangle className="ic" /></span>
          <span className="tval">{summary?.at_risk ?? 0}</span>
          <span className="tlbl">At risk</span>
          <span className="thint">Due within 3 days</span>
        </button>
        <button type="button" className={`tile blue${tile === "open" ? " active" : ""}`} onClick={() => setTile("open")}>
          <span className="tic"><FileWarning className="ic" /></span>
          <span className="tval">{summary?.open ?? 0}</span>
          <span className="tlbl">Open</span>
          <span className="thint">Awaiting a response</span>
        </button>
        <button type="button" className={`tile green${tile === "responded" ? " active" : ""}`} onClick={() => setTile("responded")}>
          <span className="tic"><CheckCircle2 className="ic" /></span>
          <span className="tval">{summary?.responded ?? 0}</span>
          <span className="tlbl">Responded</span>
          <span className="thint">Replied, not yet closed</span>
        </button>
      </div>
      </div>

      <div className="tablewrap">
        <div className="toolbar">
          <div className="srch">
            <Search className="sic" />
            <input
              value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Search subject, counterparty, or reference…"
            />
          </div>
          <select className="sel" value={direction} onChange={(e) => setDirection(e.target.value)}>
            <option value="">Both directions</option>
            <option value="received">Received</option>
            <option value="sent">Sent</option>
          </select>
          <select className="sel" value={tile} onChange={(e) => setTile(e.target.value as typeof tile)}>
            <option value="all">All statuses</option>
            <option value="overdue">Overdue only</option>
            <option value="open">Open</option>
            <option value="responded">Responded</option>
          </select>
          <span className="cnt">
            {notices?.length ?? 0} notice{(notices?.length ?? 0) === 1 ? "" : "s"}
          </span>
        </div>

        {isLoading ? (
          <CenterSpinner label="Loading the register…" />
        ) : error ? (
          <ErrorState error={error} />
        ) : !notices?.length ? (
          <div className="empty">No notices yet. Record a notice you&apos;ve received, or draft one you&apos;re about to send.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Ref</th>
                <th>Subject</th>
                <th>Counterparty</th>
                <th>Type</th>
                <th>Deadline</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {notices.map((n) => (
                <tr key={n.id} onClick={() => router.push(`/notices/${n.id}`)}>
                  <td className="nowrap">
                    <span className="refcell">
                      {n.direction === "received" ? (
                        <ArrowDownLeft className="sic sm dir-in" />
                      ) : (
                        <ArrowUpRight className="sic sm dir-out" />
                      )}
                      {n.ref}
                    </span>
                  </td>
                  <td className="ttl" title={n.subject}><span className="ttltext">{n.subject}</span></td>
                  <td className="dim">{n.counterparty_name}</td>
                  <td className="dim">{typeLabel(n.notice_type)}</td>
                  <td className="nowrap"><span className={`tag ${POSTURE_TONE[n.deadline_posture]}`}>{deadlineLabel(n)}</span></td>
                  <td className="nowrap"><span className={`tag ${STATUS_TONE[n.status]}`}>{n.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {creating && (
        <NoticeCreateModal
          onClose={() => setCreating(false)}
          onSaved={(created) => { setCreating(false); refresh(); notify("Notice recorded", "success"); router.push(`/notices/${created.id}`); }}
        />
      )}
    </div>
  );
}

function NoticeCreateModal({ onClose, onSaved }: { onClose: () => void; onSaved: (created: Notice) => void }) {
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [extraction, setExtraction] = useState<NoticeExtraction | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [prefilled, setPrefilled] = useState<Set<string>>(new Set());
  const [form, setForm] = useState({
    subject: "", counterparty_name: "", direction: "received", notice_type: "demand",
    counterparty_ref: "", notice_date: "", received_at: "", response_due_date: "",
    priority: "Medium", description: "",
  });
  const set = (k: keyof typeof form, v: string) => {
    setForm((s) => ({ ...s, [k]: v }));
    // Once a human edits a field it's theirs — drop the "from document" mark so
    // the review highlight only ever flags values the model actually supplied.
    setPrefilled((s) => { const n = new Set(s); n.delete(k); return n; });
  };
  const canSave = form.subject.trim() && form.counterparty_name.trim();

  /** Read the notice and drop its values into the form for review. Nothing is
   *  saved here — the filer still confirms and submits. */
  async function onPickFile(file: File | null) {
    if (!file) return;
    setPendingFile(file);
    setExtracting(true);
    setExtraction(null);
    try {
      const result = await noticesApi.extract(file);
      setExtraction(result);
      // Computed against the current `form`, NOT inside a setForm updater:
      // React defers the updater, so anything collected in there is still empty
      // when read synchronously below — which reported "nothing could be read"
      // over a form it had just filled in.
      const s = result.suggestions ?? {};
      const next = { ...form };
      const applied = new Set<string>();
      for (const k of ["subject", "counterparty_name", "counterparty_ref", "notice_type", "notice_date", "response_due_date"] as const) {
        const v = s[k];
        // Never clobber something the filer already typed.
        if (v && !form[k]) { next[k] = v; applied.add(k); }
      }
      if (s.demanded_action && !form.description) {
        next.description = s.demanded_action;
        applied.add("description");
      }
      setForm(next);
      setPrefilled(applied);
      if (!applied.size) {
        notify(result.message ?? "Nothing could be read from that document", "info");
      }
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not read that document", "error");
      setPendingFile(null);
    } finally {
      setExtracting(false);
    }
  }

  const mark = (k: string) => (prefilled.has(k) ? "ring-1 ring-brand-300 bg-brand-50/40" : "");

  async function save() {
    setBusy(true);
    try {
      // Empty date inputs must go up as null, not "" — the API expects a date
      // or nothing at all.
      const payload: Record<string, unknown> = { ...form };
      for (const k of ["notice_date", "received_at", "response_due_date", "counterparty_ref"]) {
        if (!payload[k]) payload[k] = null;
      }
      const created = await noticesApi.create(payload);
      // Keep the source document on the record. A failure here shouldn't lose
      // the notice the filer just created, so it's reported but not fatal.
      if (pendingFile) {
        try {
          await noticesApi.addDocument(created.id, pendingFile);
        } catch {
          notify("Notice saved, but the document could not be attached", "error");
        }
      }
      onSaved(created);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not record the notice", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} title="Record a notice" size="lg">
      <div className="space-y-4">
        <div className="rounded-lg border border-dashed border-slate-300 p-3">
          <div className="flex flex-wrap items-center gap-3">
            <label className="cursor-pointer">
              <span className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium hover:bg-slate-50">
                <Upload className="h-4 w-4" />
                {pendingFile ? "Choose a different file" : "Upload the notice"}
              </span>
              <input
                type="file" className="hidden"
                accept=".pdf,.docx,.doc,.txt,application/pdf,text/plain"
                onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
              />
            </label>
            {extracting && <span className="text-xs text-slate-500">Reading the document…</span>}
            {pendingFile && !extracting && (
              <span className="truncate text-xs text-slate-500">{pendingFile.name}</span>
            )}
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Optional — we&apos;ll read it and fill in what we can. Everything stays editable,
            and the file is attached to the notice when you save.
          </p>
          {extraction && extraction.source !== "empty" && (
            <p className="mt-2 text-xs">
              <Badge tone={extraction.confidence >= 0.7 ? "green" : "amber"}>
                {Math.round(extraction.confidence * 100)}% confident
              </Badge>{" "}
              <span className="text-slate-500">
                {extraction.source === "heuristic"
                  ? "Read without AI — check every field."
                  : "Highlighted fields came from the document. Confirm the deadline before saving."}
              </span>
            </p>
          )}
          {extraction?.message && (
            <p className="mt-2 text-xs text-warning">{extraction.message}</p>
          )}
        </div>

        <Field label="Subject">
          <Input value={form.subject} onChange={(e) => set("subject", e.target.value)}
            className={mark("subject")}
            placeholder="Demand for payment of outstanding invoices" />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Counterparty">
            <Input value={form.counterparty_name} onChange={(e) => set("counterparty_name", e.target.value)}
              className={mark("counterparty_name")} placeholder="Northwind Traders GmbH" />
          </Field>
          <Field label="Their reference" hint="optional">
            <Input value={form.counterparty_ref} onChange={(e) => set("counterparty_ref", e.target.value)}
              className={mark("counterparty_ref")} placeholder="NT/LEG/2026/118" />
          </Field>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Direction">
            <Select value={form.direction} onChange={(e) => set("direction", e.target.value)}>
              <option value="received">Received</option>
              <option value="sent">Sent</option>
            </Select>
          </Field>
          <Field label="Type">
            <Select value={form.notice_type} onChange={(e) => set("notice_type", e.target.value)} className={mark("notice_type")}>
              {NOTICE_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </Select>
          </Field>
          <Field label="Priority">
            <Select value={form.priority} onChange={(e) => set("priority", e.target.value)}>
              {["Critical", "High", "Medium", "Low"].map((p) => <option key={p}>{p}</option>)}
            </Select>
          </Field>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Date on notice"><Input type="date" value={form.notice_date} onChange={(e) => set("notice_date", e.target.value)} className={mark("notice_date")} /></Field>
          <Field label="Received on"><Input type="date" value={form.received_at} onChange={(e) => set("received_at", e.target.value)} /></Field>
          <Field label="Response due" hint="the statutory clock">
            <Input type="date" value={form.response_due_date} onChange={(e) => set("response_due_date", e.target.value)} className={mark("response_due_date")} />
          </Field>
        </div>
        <Field label="Summary" hint="what it demands, and by when">
          <Textarea rows={3} value={form.description} onChange={(e) => set("description", e.target.value)} className={mark("description")} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={save} loading={busy} disabled={!canSave}>Record notice</Button>
        </div>
      </div>
    </Modal>
  );
}

const NOTC_CSS = `
.notc{--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--sans:var(--font-sans);--mono:ui-monospace,SFMono-Regular,Menlo,monospace;padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .notc{--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4)}
.notc *{box-sizing:border-box}
.notc .dim{color:var(--ink-2)}
.notc .ic{width:14px;height:14px;flex:none}
.notc .sic{width:14px;height:14px;flex:none;color:var(--ink-3)}
.notc .sic.sm{width:13px;height:13px}
.notc .dir-in{color:var(--accent)} .notc .dir-out{color:var(--ink-2)}
.notc .hd{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.notc .hd h1{margin:4px 0 0;font-size:19px;font-weight:680;letter-spacing:-.015em}
.notc .hd .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:640px}
.notc .acts{margin-left:auto;display:flex;gap:8px}





.notc .statcard{border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);padding:14px;margin-bottom:16px}
.notc .tiles{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
@media(min-width:640px){.notc .tiles{grid-template-columns:repeat(4,1fr)}}
.notc .tile{text-align:left;border:1px solid var(--border);border-radius:10px;background:var(--inset);padding:12px 13px;cursor:pointer;transition:.12s;display:grid;grid-template-columns:auto 1fr;grid-template-rows:auto auto;column-gap:10px;font:inherit;color:inherit}
.notc .tile:hover{border-color:var(--border-strong)}
.notc .tile.active{border-color:var(--tone)}
.notc .tile .tic{grid-row:1 / 3;width:32px;height:32px;border-radius:9px;display:grid;place-items:center;background:var(--tone-soft);color:var(--tone)}
.notc .tile .tval{font:700 20px var(--sans);letter-spacing:-.01em;align-self:end}
.notc .tile .tlbl{font:600 10.5px var(--sans);letter-spacing:.04em;text-transform:uppercase;color:var(--ink-2)}
.notc .tile .thint{grid-column:1 / 3;margin-top:2px;font-size:11px;color:var(--ink-3)}
.notc .tile.red{--tone:var(--crit);--tone-soft:var(--crit-soft)}
.notc .tile.amber{--tone:var(--warn);--tone-soft:var(--warn-soft)}
.notc .tile.blue{--tone:var(--accent);--tone-soft:var(--accent-soft)}
.notc .tile.green{--tone:var(--good);--tone-soft:var(--good-soft)}
.notc .toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid var(--border)}
.notc .srch{position:relative;width:280px;max-width:100%}
.notc .srch .sic{position:absolute;left:9px;top:50%;transform:translateY(-50%);pointer-events:none}
.notc .srch input{width:100%;padding:7px 10px 7px 30px;border-radius:8px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-size:12.5px;font-family:var(--sans)}
.notc .srch input:focus{outline:2px solid var(--accent-soft);border-color:var(--accent)}
.notc select.sel{padding:7px 10px;border-radius:8px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-size:12.5px;font-family:var(--sans);max-width:11rem}
.notc select.sel:focus{outline:2px solid var(--accent-soft);border-color:var(--accent)}
.notc .cnt{margin-left:auto;font-size:11.5px;color:var(--ink-3);white-space:nowrap}
.notc table{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px;min-width:760px}
.notc .tablewrap{overflow-x:auto}
.notc thead th{text-align:left;font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);padding:9px 12px;border-bottom:1px solid var(--border);white-space:nowrap}
.notc tbody td{padding:9px 12px;border-bottom:1px solid var(--border);vertical-align:middle}
.notc tbody tr{cursor:pointer}
.notc tbody tr:last-child td{border-bottom:0}
.notc tbody tr:hover td{background:var(--inset)}
.notc td.nowrap{white-space:nowrap}
.notc td.ttl{max-width:20rem}
.notc .ttltext{font-weight:560;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block;max-width:100%;vertical-align:middle}
.notc .refcell{display:inline-flex;align-items:center;gap:6px;font-weight:600;color:var(--ink)}
.notc .tag{display:inline-flex;align-items:center;font:600 10.5px var(--sans);letter-spacing:.02em;padding:2px 8px;border-radius:99px;white-space:nowrap}
.notc .tag.slate{background:var(--surface-2);color:var(--ink-2)}
.notc .tag.blue{background:var(--accent-soft);color:var(--accent)}
.notc .tag.green{background:var(--good-soft);color:var(--good)}
.notc .tag.amber{background:var(--warn-soft);color:var(--warn)}
.notc .tag.red{background:var(--crit-soft);color:var(--crit)}
.notc .tag.violet{background:var(--accent-soft);color:var(--accent)}

`;
