"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, PenLine, Plus, Send, Trash2, XCircle } from "lucide-react";
import { contractsApi, signaturesApi } from "@/lib/endpoints";
import { Button, Field, Input, Modal, Select } from "@/components/ui";
import { fmtDateTime, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { SignatureRequest } from "@/lib/types";

const ic = (p: string) => (
  <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" dangerouslySetInnerHTML={{ __html: p }} />
);

const TERMINAL = new Set(["completed", "declined", "voided"]);
const STATUS_CLASS: Record<string, string> = {
  completed: "good",
  sent: "warn",
  delivered: "warn",
  draft: "dim",
  declined: "bad",
  voided: "bad",
};

export default function SignaturesPage() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [sendOpen, setSendOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["signatures"],
    queryFn: signaturesApi.list,
  });
  const { data: contracts } = useQuery({
    queryKey: ["contracts"],
    queryFn: contractsApi.list,
  });

  const titleMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of contracts ?? []) m.set(c.id, c.title);
    return m;
  }, [contracts]);

  async function sync(req: SignatureRequest, completed: boolean) {
    setBusyId(req.id);
    try {
      await signaturesApi.sync(req.id, completed, !completed);
      qc.invalidateQueries({ queryKey: ["signatures"] });
      notify(
        completed ? "Marked as completed" : "Marked as declined",
        "success",
      );
    } catch (e) {
      notify(e instanceof Error ? e.message : "Sync failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  const rows = data ?? [];
  const completed = rows.filter((r) => r.status === "completed").length;
  const awaiting = rows.filter((r) => r.status === "sent" || r.status === "delivered").length;
  const declined = rows.filter((r) => r.status === "declined" || r.status === "voided").length;

  return (
    <div className="sigl">
      <style dangerouslySetInnerHTML={{ __html: SIGL_CSS }} />
      <div className="hd">
        <div>
          <h1>Signatures</h1>
          <p className="sub">Send contracts for e-signature and track envelope status.</p>
        </div>
        <div className="acts">
          <button className="btn pri" onClick={() => setSendOpen(true)}>
            <Send className="h-4 w-4" />
            Send for signature
          </button>
        </div>
      </div>

      {rows.length > 0 && (
        <div className="stats">
          <div className="stat">
            <span className="si a">{ic('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>')}</span>
            <div><div className="sv">{rows.length}</div><div className="sl">envelope{rows.length === 1 ? "" : "s"}</div></div>
          </div>
          <div className="stat">
            <span className="si g">{ic('<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>')}</span>
            <div><div className="sv">{completed}</div><div className="sl">completed</div></div>
          </div>
          <div className="stat">
            <span className="si w">{ic('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>')}</span>
            <div><div className="sv">{awaiting}</div><div className="sl">awaiting</div></div>
          </div>
          <div className="stat">
            <span className="si c">{ic('<circle cx="12" cy="12" r="9"/><path d="M15 9l-6 6M9 9l6 6"/>')}</span>
            <div><div className="sv">{declined}</div><div className="sl">declined / voided</div></div>
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="empty">Loading signatures…</div>
      ) : error ? (
        <div className="empty err">
          {error instanceof Error ? error.message : "Couldn't load signatures."}
        </div>
      ) : rows.length === 0 ? (
        <div className="empty">
          <PenLine className="h-6 w-6" />
          <div className="etitle">No signature requests</div>
          <div className="edesc">Send a contract for e-signature to track its envelope here.</div>
          <button className="btn pri" onClick={() => setSendOpen(true)}>
            <Send className="h-4 w-4" />
            Send for signature
          </button>
        </div>
      ) : (
        <div className="sigtablewrap">
          <p className="hint">
            Each pending request can be reconciled from its row — use{" "}
            <strong>Mark completed</strong> or <strong>Mark declined</strong> to
            record the envelope&apos;s final status.
          </p>
          <table className="st">
            <thead>
              <tr>
                <th>Contract</th>
                <th>Status</th>
                <th>Provider</th>
                <th>Sent</th>
                <th>Completed</th>
                <th className="c-right">Update status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((req) => (
                <tr key={req.id}>
                  <td className="c-strong">
                    {titleMap.get(req.contract_id) ?? req.contract_id}
                  </td>
                  <td>
                    <span className={`sstat ${STATUS_CLASS[req.status] ?? "dim"}`}>
                      {titleCase(req.status)}
                    </span>
                  </td>
                  <td>{titleCase(req.provider)}</td>
                  <td>{fmtDateTime(req.sent_at)}</td>
                  <td>{fmtDateTime(req.completed_at)}</td>
                  <td className="c-right">
                    {TERMINAL.has(req.status) ? (
                      <span className="dim">No action needed</span>
                    ) : (
                      <div className="rowacts">
                        <button
                          className="btn sm"
                          disabled={busyId === req.id}
                          onClick={() => sync(req, true)}
                          aria-label="Mark this signature request completed"
                        >
                          <CheckCircle2 className="h-4 w-4" />
                          {busyId === req.id ? "Working…" : "Mark completed"}
                        </button>
                        <button
                          className="btn sm danger"
                          disabled={busyId === req.id}
                          onClick={() => sync(req, false)}
                          aria-label="Mark this signature request declined"
                        >
                          <XCircle className="h-4 w-4" />
                          Mark declined
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <SendModal
        open={sendOpen}
        onClose={() => setSendOpen(false)}
        // Only contracts that have cleared approval (reached the signature stage)
        // can be sent — filtering here stops the pick-then-409 dead end.
        contracts={(contracts ?? [])
          .filter((c) => c.lifecycle_stage === "signature")
          .map((c) => ({ id: c.id, title: c.title }))}
        onSent={() => {
          qc.invalidateQueries({ queryKey: ["signatures"] });
          notify("Sent for signature", "success");
          setSendOpen(false);
        }}
      />
    </div>
  );
}

interface RecipientRow {
  name: string;
  email: string;
  role: string;
}

function SendModal({
  open,
  onClose,
  contracts,
  onSent,
}: {
  open: boolean;
  onClose: () => void;
  contracts: { id: string; title: string }[];
  onSent: () => void;
}) {
  const { notify } = useToast();
  const [contractId, setContractId] = useState("");
  const [overrideLifecycle, setOverrideLifecycle] = useState(false);
  const [recipients, setRecipients] = useState<RecipientRow[]>([
    { name: "", email: "", role: "" },
  ]);
  const [busy, setBusy] = useState(false);

  function setRow(i: number, patch: Partial<RecipientRow>) {
    setRecipients((rows) =>
      rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)),
    );
  }
  function addRow() {
    setRecipients((rows) => [...rows, { name: "", email: "", role: "" }]);
  }
  function removeRow(i: number) {
    setRecipients((rows) =>
      rows.length > 1 ? rows.filter((_, idx) => idx !== i) : rows,
    );
  }

  const valid =
    !!contractId &&
    recipients.every((r) => r.name.trim() && r.email.trim());

  async function submit() {
    if (!valid) return;
    setBusy(true);
    try {
      await signaturesApi.send({
        contract_id: contractId,
        override_lifecycle: overrideLifecycle,
        recipients: recipients.map((r) => ({
          name: r.name.trim(),
          email: r.email.trim(),
          role: r.role.trim() || null,
        })),
      });
      setContractId("");
      setOverrideLifecycle(false);
      setRecipients([{ name: "", email: "", role: "" }]);
      onSent();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Send failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Send for signature"
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!valid}>
            Send
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Contract" hint="Only contracts that have completed approval appear here.">
          <Select
            value={contractId}
            onChange={(e) => setContractId(e.target.value)}
          >
            <option value="">
              {contracts.length ? "Select a contract…" : "No contracts are ready for signature yet"}
            </option>
            {contracts.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </Select>
        </Field>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="block text-xs font-medium text-slate-700">
              Recipients
            </label>
            <Button size="sm" variant="outline" onClick={addRow}>
              <Plus className="h-3.5 w-3.5" />
              Add recipient
            </Button>
          </div>
          <div className="space-y-2">
            {recipients.map((r, i) => (
              <div key={i} className="flex items-end gap-2">
                <Field label={i === 0 ? "Name" : undefined} className="flex-1">
                  <Input
                    placeholder="Full name"
                    value={r.name}
                    onChange={(e) => setRow(i, { name: e.target.value })}
                  />
                </Field>
                <Field label={i === 0 ? "Email" : undefined} className="flex-1">
                  <Input
                    type="email"
                    placeholder="email@example.com"
                    value={r.email}
                    onChange={(e) => setRow(i, { email: e.target.value })}
                  />
                </Field>
                <Field label={i === 0 ? "Role" : undefined} className="w-36">
                  <Input
                    placeholder="signer"
                    value={r.role}
                    onChange={(e) => setRow(i, { role: e.target.value })}
                  />
                </Field>
                <Button
                  size="icon"
                  variant="ghost"
                  disabled={recipients.length === 1}
                  onClick={() => removeRow(i)}
                  aria-label="Remove recipient"
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))}
          </div>
        </div>

        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
            checked={overrideLifecycle}
            onChange={(e) => setOverrideLifecycle(e.target.checked)}
          />
          Override lifecycle stage requirement
        </label>
      </div>
    </Modal>
  );
}

const SIGL_CSS = `
.sigl{--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--sans:var(--font-sans);--mono:ui-monospace,SFMono-Regular,Menlo,monospace;padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .sigl{--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4)}
.sigl .dim{color:var(--ink-3)}
.sigl .hd{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.sigl .hd h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.015em} .sigl .hd .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:640px}
.sigl .acts{margin-left:auto;display:flex;gap:8px}
.sigl .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:20px}
.sigl .stat{display:flex;align-items:center;gap:12px;border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);padding:14px 16px}
.sigl .si{flex:none;display:grid;place-items:center;width:38px;height:38px;border-radius:10px;background:var(--surface-2);color:var(--ink-2)}
.sigl .si.a{background:var(--accent-soft);color:var(--accent)} .sigl .si.g{background:var(--good-soft);color:var(--good)} .sigl .si.w{background:var(--warn-soft);color:var(--warn)} .sigl .si.c{background:var(--crit-soft);color:var(--crit)}
.sigl .si .ic{width:19px;height:19px}
.sigl .sv{font-size:21px;font-weight:700;letter-spacing:-.02em;line-height:1} .sigl .sl{margin-top:3px;font-size:11.5px;color:var(--ink-2)}
    
.sigl .btn.sm{padding:5px 10px;font-size:11.5px;border-radius:7px} .sigl .btn.danger{border-color:color-mix(in srgb,var(--crit) 45%,var(--border-strong));color:var(--crit)} .sigl .btn.danger:hover{background:color-mix(in srgb,var(--crit) 10%,var(--surface))}
 
.sigl .empty .etitle{font-weight:660;font-size:14px;color:var(--ink)} .sigl .empty .edesc{max-width:420px;color:var(--ink-2)} .sigl .empty .btn{margin-top:6px}
.sigl .hint{margin:0 0 10px;font-size:12px;color:var(--ink-3)} .sigl .hint strong{color:var(--ink-2);font-weight:600}
.sigl .sigtablewrap{overflow-x:auto}
.sigl table.st{min-width:820px}
.sigl table.st{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px}
.sigl table.st thead th{text-align:left;font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);padding:10px 12px;border-bottom:1px solid var(--border);background:var(--inset);white-space:nowrap}
.sigl table.st thead th:first-child{border-top-left-radius:8px} .sigl table.st thead th:last-child{border-top-right-radius:8px}
.sigl table.st tbody td{padding:11px 12px;border-bottom:1px solid var(--border);vertical-align:middle}
.sigl table.st tbody tr:last-child td{border-bottom:0}
.sigl table.st .c-strong{font-weight:600;color:var(--ink)}
.sigl table.st .c-right{text-align:right}
.sigl .rowacts{display:flex;justify-content:flex-end;gap:8px}
.sigl .sstat{font:600 11.5px var(--sans);white-space:nowrap} .sigl .sstat.good{color:var(--good)} .sigl .sstat.warn{color:var(--warn)} .sigl .sstat.bad{color:var(--crit)} .sigl .sstat.dim{color:var(--ink-3)}
`;
