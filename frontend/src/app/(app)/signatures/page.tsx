"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, PenLine, Plus, Send, Trash2, XCircle } from "lucide-react";
import { contractsApi, signaturesApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CenterSpinner,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Modal,
  PageHeader,
  Select,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { fmtDateTime, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { SignatureRequest } from "@/lib/types";

const TERMINAL = new Set(["completed", "declined", "voided"]);

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

  return (
    <div className="space-y-6">
      <PageHeader
        title="Signatures"
        description="Send contracts for e-signature and track envelope status."
        actions={
          <Button onClick={() => setSendOpen(true)}>
            <Send className="h-4 w-4" />
            Send for signature
          </Button>
        }
      />

      {isLoading ? (
        <CenterSpinner label="Loading signatures…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={<PenLine className="h-6 w-6" />}
          title="No signature requests"
          description="Send a contract for e-signature to track its envelope here."
          action={
            <Button onClick={() => setSendOpen(true)}>
              <Send className="h-4 w-4" />
              Send for signature
            </Button>
          }
        />
      ) : (
        <Card>
          <p className="border-b border-slate-100 px-4 py-3 text-xs text-slate-500">
            Each pending request can be reconciled from its row — use{" "}
            <span className="font-medium text-slate-700">Mark completed</span> or{" "}
            <span className="font-medium text-slate-700">Mark declined</span> to
            record the envelope&apos;s final status.
          </p>
          <Table>
            <THead>
              <tr>
                <TH>Contract</TH>
                <TH>Status</TH>
                <TH>Provider</TH>
                <TH>Sent</TH>
                <TH>Completed</TH>
                <TH className="text-right">Update status</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((req) => (
                <TR key={req.id}>
                  <TD className="font-medium text-slate-900">
                    {titleMap.get(req.contract_id) ?? req.contract_id}
                  </TD>
                  <TD>
                    <Badge tone={statusTone(req.status)}>
                      {titleCase(req.status)}
                    </Badge>
                  </TD>
                  <TD>{titleCase(req.provider)}</TD>
                  <TD>{fmtDateTime(req.sent_at)}</TD>
                  <TD>{fmtDateTime(req.completed_at)}</TD>
                  <TD className="text-right">
                    {TERMINAL.has(req.status) ? (
                      <span className="text-xs text-slate-400">
                        No action needed
                      </span>
                    ) : (
                      <div className="flex justify-end gap-2">
                        <Button
                          size="sm"
                          loading={busyId === req.id}
                          onClick={() => sync(req, true)}
                          aria-label="Mark this signature request completed"
                        >
                          <CheckCircle2 className="h-4 w-4" />
                          Mark completed
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          disabled={busyId === req.id}
                          onClick={() => sync(req, false)}
                          aria-label="Mark this signature request declined"
                        >
                          <XCircle className="h-4 w-4" />
                          Mark declined
                        </Button>
                      </div>
                    )}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <SendModal
        open={sendOpen}
        onClose={() => setSendOpen(false)}
        contracts={contracts ?? []}
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
        <Field label="Contract">
          <Select
            value={contractId}
            onChange={(e) => setContractId(e.target.value)}
          >
            <option value="">Select a contract…</option>
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
