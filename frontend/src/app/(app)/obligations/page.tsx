"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlarmClock,
  CheckCircle2,
  ClipboardList,
  Clock,
  ListChecks,
} from "lucide-react";
import { contractsApi, obligationsApi } from "@/lib/endpoints";
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
  StatCard,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { fmtDate, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { Obligation, ObligationStatus } from "@/lib/types";

const STATUSES: ObligationStatus[] = [
  "open",
  "due_soon",
  "overdue",
  "completed",
  "cancelled",
];

export default function ObligationsPage() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState<Obligation | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [remindersBusy, setRemindersBusy] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["obligations", status],
    queryFn: () =>
      obligationsApi.list(status ? { status_filter: status } : {}),
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

  const counts = useMemo(() => {
    const c: Record<ObligationStatus, number> = {
      open: 0,
      due_soon: 0,
      overdue: 0,
      completed: 0,
      cancelled: 0,
    };
    for (const o of data ?? []) c[o.status] += 1;
    return c;
  }, [data]);

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
    <div className="space-y-6">
      <PageHeader
        title="Obligations"
        description="Track contractual commitments, deadlines and recurring duties."
        actions={
          <Button
            variant="outline"
            loading={remindersBusy}
            onClick={runReminders}
          >
            <AlarmClock className="h-4 w-4" />
            Run reminders
          </Button>
        }
      />

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard
          label="Open"
          value={counts.open}
          icon={<ListChecks className="h-5 w-5" />}
          tone="blue"
        />
        <StatCard
          label="Due soon"
          value={counts.due_soon}
          icon={<Clock className="h-5 w-5" />}
          tone="amber"
        />
        <StatCard
          label="Overdue"
          value={counts.overdue}
          icon={<AlarmClock className="h-5 w-5" />}
          tone="red"
        />
        <StatCard
          label="Completed"
          value={counts.completed}
          icon={<CheckCircle2 className="h-5 w-5" />}
          tone="green"
        />
        <StatCard
          label="Cancelled"
          value={counts.cancelled}
          icon={<ClipboardList className="h-5 w-5" />}
          tone="slate"
        />
      </div>

      <Card className="flex flex-wrap items-end gap-3 p-4">
        <Field label="Status" className="w-48">
          <Select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </Select>
        </Field>
      </Card>

      {isLoading ? (
        <CenterSpinner label="Loading obligations…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={<ListChecks className="h-6 w-6" />}
          title="No obligations found"
          description="Obligations extracted from active contracts will appear here."
        />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Description</TH>
                <TH>Contract</TH>
                <TH>Type</TH>
                <TH>Due</TH>
                <TH>Recurrence</TH>
                <TH>Status</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((o) => (
                <TR key={o.id}>
                  <TD className="max-w-sm font-medium text-slate-900">
                    {o.description}
                  </TD>
                  <TD>{titleMap.get(o.contract_id) ?? o.contract_id}</TD>
                  <TD>{o.obligation_type ? titleCase(o.obligation_type) : "—"}</TD>
                  <TD>{fmtDate(o.due_date)}</TD>
                  <TD>{o.recurrence ? titleCase(o.recurrence) : "—"}</TD>
                  <TD>
                    <Badge tone={statusTone(o.status)}>
                      {titleCase(o.status)}
                    </Badge>
                  </TD>
                  <TD className="text-right">
                    <div className="flex justify-end gap-2">
                      {o.status !== "completed" && (
                        <Button
                          size="sm"
                          loading={busyId === o.id}
                          onClick={() => complete(o)}
                        >
                          Complete
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setEditing(o)}
                      >
                        Edit
                      </Button>
                    </div>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
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

  return (
    <Modal
      open={!!obligation}
      onClose={onClose}
      title="Edit obligation"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy}>
            Save changes
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Owner user ID">
          <Input
            value={ownerUserId}
            onChange={(e) => setOwnerUserId(e.target.value)}
          />
        </Field>
        <Field label="Responsible party">
          <Input
            value={responsibleParty}
            onChange={(e) => setResponsibleParty(e.target.value)}
          />
        </Field>
        <Field label="Obligation type">
          <Input
            placeholder="e.g. payment"
            value={obligationType}
            onChange={(e) => setObligationType(e.target.value)}
          />
        </Field>
        <Field label="Status">
          <Select
            value={status}
            onChange={(e) => setStatus(e.target.value as ObligationStatus)}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Due date">
          <Input
            type="date"
            value={dueDate}
            onChange={(e) => setDueDate(e.target.value)}
          />
        </Field>
        <Field label="Recurrence">
          <Input
            placeholder="e.g. monthly"
            value={recurrence}
            onChange={(e) => setRecurrence(e.target.value)}
          />
        </Field>
      </div>
    </Modal>
  );
}
