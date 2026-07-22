"use client";

import { Fragment, useMemo, useState } from "react";
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
} from "lucide-react";
import { obligationsApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
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
  SkeletonRows,
} from "@/components/ui";
import { cn, fmtDate, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { Obligation, ObligationStatus } from "@/lib/types";

const STATUSES: ObligationStatus[] = [
  "open",
  "due_soon",
  "overdue",
  "completed",
  "cancelled",
];

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
    <div className="space-y-4">
      <PageHeader
        title="Obligations"
        description="Every commitment extracted from your contracts — who owes it, which agreement it comes from, and when it applies."
        actions={
          <Button variant="outline" loading={remindersBusy} onClick={runReminders}>
            <AlarmClock className="h-4 w-4" />
            Run reminders
          </Button>
        }
      />

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard label="Open" value={counts.open} icon={<ListChecks className="h-5 w-5" />} tone="blue" />
        <StatCard label="Due soon" value={counts.due_soon} icon={<Clock className="h-5 w-5" />} tone="amber" />
        <StatCard label="Overdue" value={counts.overdue} icon={<AlarmClock className="h-5 w-5" />} tone="red" />
        <StatCard label="Completed" value={counts.completed} icon={<CheckCircle2 className="h-5 w-5" />} tone="green" />
        <StatCard label="Cancelled" value={counts.cancelled} icon={<ClipboardList className="h-5 w-5" />} tone="slate" />
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
        <p className="ml-auto self-center text-[13px] text-slate-500">
          Click any row to see the exact contract language it came from.
        </p>
      </Card>

      {isLoading ? (
        <SkeletonRows rows={6} />
      ) : error ? (
        <ErrorState error={error} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={<ListChecks className="h-6 w-6" />}
          title="No obligations found"
          description="Obligations extracted from active contracts will appear here."
        />
      ) : (
        <Card className="overflow-hidden">
          <Table>
            <THead>
              <tr>
                <TH className="w-8"> </TH>
                <TH>Obligation</TH>
                <TH>Responsible party</TH>
                <TH>Contract</TH>
                <TH>Timing</TH>
                <TH>Status</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((o) => {
                const t = timing(o);
                const open = expanded === o.id;
                const quote = (o.source_citation as { quote?: string } | null)?.quote;
                const meta = o.metadata_json as {
                  source_clause_type?: string;
                  confidence?: string;
                } | null;
                return (
                  <Fragment key={o.id}>
                    <TR
                      className="cursor-pointer align-top"
                      onClick={() => setExpanded(open ? null : o.id)}
                    >
                      <TD>
                        <ChevronRight
                          className={cn(
                            "h-4 w-4 text-slate-400 transition-transform",
                            open && "rotate-90",
                          )}
                        />
                      </TD>
                      <TD className="max-w-md">
                        {o.obligation_type && (
                          <span className="mb-1 inline-block rounded bg-brand-50 px-2 py-0.5 text-[11px] font-medium uppercase tracking-[0.06em] text-brand-700">
                            {o.obligation_type}
                          </span>
                        )}
                        <p className="line-clamp-2 font-medium text-slate-900">
                          {o.description}
                        </p>
                      </TD>
                      <TD>
                        <div className="flex items-start gap-1.5">
                          <User2 className="mt-0.5 h-3.5 w-3.5 flex-none text-slate-400" />
                          <div className="min-w-0">
                            <p className="font-medium text-slate-700">
                              {o.responsible_party ?? "Unassigned"}
                            </p>
                            {o.owner_name && (
                              <p className="text-[11px] text-slate-400">
                                tracked by {o.owner_name}
                              </p>
                            )}
                          </div>
                        </div>
                      </TD>
                      <TD>
                        <Link
                          href={`/contracts/${o.contract_id}`}
                          onClick={(e) => e.stopPropagation()}
                          className="font-medium text-brand-700 hover:underline"
                        >
                          {o.contract_title ?? o.contract_id}
                        </Link>
                        {o.counterparty_name && (
                          <p className="text-[11px] text-slate-400">with {o.counterparty_name}</p>
                        )}
                      </TD>
                      <TD>
                        <div className="flex items-start gap-1.5">
                          {t.tone === "recurring" ? (
                            <Repeat className="mt-0.5 h-3.5 w-3.5 flex-none text-slate-400" />
                          ) : (
                            <Clock className="mt-0.5 h-3.5 w-3.5 flex-none text-slate-400" />
                          )}
                          <div className="min-w-0">
                            <p
                              className={cn(
                                "font-medium",
                                t.tone === "date" ? "text-slate-900" : "text-slate-700",
                              )}
                            >
                              {t.label}
                            </p>
                            {t.sub && <p className="text-[11px] text-slate-400">{t.sub}</p>}
                          </div>
                        </div>
                      </TD>
                      <TD>
                        <Badge tone={statusTone(o.status)}>{titleCase(o.status)}</Badge>
                      </TD>
                      <TD className="text-right" onClick={(e) => e.stopPropagation()}>
                        <div className="flex justify-end gap-2">
                          {o.status !== "completed" && (
                            <Button size="sm" loading={busyId === o.id} onClick={() => complete(o)}>
                              Complete
                            </Button>
                          )}
                          <Button size="sm" variant="outline" onClick={() => setEditing(o)}>
                            Edit
                          </Button>
                        </div>
                      </TD>
                    </TR>
                    {open && (
                      <tr key={`${o.id}-detail`} className="bg-slate-50">
                        <td />
                        <td colSpan={6} className="px-4 py-4">
                          <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
                            <div>
                              <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
                                <Quote className="h-3.5 w-3.5" /> What the contract says
                              </p>
                              {quote ? (
                                <blockquote className="rounded-md border-l-2 border-brand-600 bg-slate-100 px-3 py-2 text-[13px] italic leading-relaxed text-slate-700">
                                  “{quote}”
                                </blockquote>
                              ) : (
                                <p className="text-[13px] text-slate-400">
                                  No source excerpt was captured for this obligation.
                                </p>
                              )}
                              <p className="mt-2 text-[13px] leading-relaxed text-slate-600">
                                {o.description}
                              </p>
                            </div>
                            <div className="space-y-2.5 text-[13px]">
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
                                    : o.recurrence ??
                                      "Conditional — applies on trigger, no fixed date"
                                }
                              />
                              <DetailRow label="Category" value={o.obligation_type} />
                              {meta?.source_clause_type && (
                                <DetailRow label="From clause" value={meta.source_clause_type} />
                              )}
                              {meta?.confidence && (
                                <DetailRow
                                  label="Extraction confidence"
                                  value={titleCase(meta.confidence)}
                                />
                              )}
                              <Link
                                href={`/contracts/${o.contract_id}`}
                                className="inline-block pt-1 text-[13px] font-semibold text-brand-700 hover:underline"
                              >
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

function DetailRow({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="flex gap-3">
      <span className="w-32 flex-none text-[11px] font-bold uppercase tracking-wide text-slate-400">
        {label}
      </span>
      <span className="text-slate-700 dark:text-slate-300">{value ?? "—"}</span>
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
        <Field label="Responsible party" hint="Which party owes this — e.g. Receiving Party, both parties">
          <Input value={responsibleParty} onChange={(e) => setResponsibleParty(e.target.value)} />
        </Field>
        <Field label="Internal owner (user ID)" hint="Who on your team tracks it">
          <Input value={ownerUserId} onChange={(e) => setOwnerUserId(e.target.value)} />
        </Field>
        <Field label="Obligation type">
          <Input
            placeholder="e.g. payment"
            value={obligationType}
            onChange={(e) => setObligationType(e.target.value)}
          />
        </Field>
        <Field label="Status">
          <Select value={status} onChange={(e) => setStatus(e.target.value as ObligationStatus)}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Due date" hint="Leave blank for conditional / ongoing obligations">
          <Input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
        </Field>
        <Field label="Recurrence / timing" hint="e.g. monthly, during term and 12 months after">
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
