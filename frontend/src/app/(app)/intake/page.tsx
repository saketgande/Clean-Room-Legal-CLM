"use client";

import { Fragment, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DoorOpen, FileText, MessageSquare, Plus, Search, Trash2, Users } from "lucide-react";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, CenterSpinner, EmptyState,
  ErrorState, Field, Input, Modal, Select, StatCard, Table, TD, TH, THead,
  TR, Textarea,
} from "@/components/ui";
import { intakeApi, projectsApi, contractsApi, approvalsApi, aiApi, playbooksApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { cn, titleCase } from "@/lib/utils";
import {
  can, humanizeEvent, POSTURE_LABEL, POSTURE_TONE, PRIORITY_TONE, slaBarColor,
  sortBySla, STATUS_LABEL, STATUS_TONE,
} from "@/lib/intake";
import type {
  ContractResponse, IntakeFieldSpec, IntakeRequest, IntakeRequestType, IntakeSlaPosture, IntakeStatus,
} from "@/lib/types";
import { KanbanTab, SlaDashboardTab, SlaLegsBar, TeamsTab } from "./_phase1";
import { AiOpsTab, CopilotChat, PoolOpsTab, SelfServiceTab } from "./_phase2";

export default function IntakePage() {
  const { user } = useAuth();
  const isStaff = can(user, "intake:triage");
  const isAdmin = can(user, "admin_panel:access");

  // Reference-style tab set — Work · File · Insights, divider-grouped. The Wall
  // (absorbed Command) leads. Inbox/Cockpit/Kanban and SLA are first-class tabs
  // (not nested view-toggles) so every lens is one click away, matching the
  // reference app. Requesters get just the filing three.
  const groups = useMemo<{ id: string; label: string }[][]>(() => {
    if (!isStaff)
      return [[
        { id: "new", label: "New Request" },
        { id: "mywork", label: "My Work" },
        { id: "self", label: "Self-Service" },
      ]];
    return [
      [
        { id: "queue", label: "Inbox" },
        { id: "kanban", label: "Kanban" },
        { id: "mywork", label: "My Work" },
      ],
      [
        { id: "new", label: "New Request" },
        { id: "self", label: "Self-Service" },
      ],
      [
        { id: "sla", label: "SLA" },
        { id: "agents", label: "Agents" },
        { id: "ops", label: "Operations" },
      ],
    ];
  }, [isStaff]);

  const [section, setSection] = useState(isStaff ? "queue" : "new");
  const [detailId, setDetailId] = useState<string | null>(null);

  const { data: listData } = useQuery({
    queryKey: ["intake-list"], queryFn: () => intakeApi.list(), enabled: isStaff,
  });
  const { data: myWork } = useQuery({
    queryKey: ["intake-mywork"], queryFn: intakeApi.myWork, enabled: isStaff,
  });
  const awaiting = (listData ?? []).filter((r) => r.status === "awaiting_triage").length;

  // Count-pills on the tabs — the reference's at-a-glance queue signal.
  const openCount = (listData ?? []).filter((r) => r.status !== "closed" && r.status !== "approved").length;
  const onMe = myWork
    ? myWork.awaiting_review.length + myWork.my_tickets.length + myWork.my_tasks.length
    : 0;
  const counts: Record<string, number> = { queue: openCount, mywork: onMe };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-brand-600">
            Operations · Legal · Intake
          </p>
          <h1 className="mt-1.5 text-[26px] font-semibold leading-tight tracking-[-0.01em] text-slate-900">
            Mission control for every legal request —{" "}
            <span className="font-normal italic text-brand-600" style={{ fontFamily: 'Georgia, "Times New Roman", serif' }}>
              triaged, drafted, resolved
            </span>
          </h1>
        </div>
        <div className="flex flex-shrink-0 flex-wrap items-center gap-2">
          {isStaff && awaiting > 0 && (
            <span className="inline-flex items-center gap-1 rounded-md bg-amber-50 px-2.5 py-1 font-mono text-[10px] font-semibold uppercase tracking-wide text-amber-700 ring-1 ring-inset ring-amber-200">
              ◆ {awaiting} awaiting triage
            </span>
          )}
          {isStaff && (
            <span className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wide text-emerald-600">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />AI triage · live
            </span>
          )}
          <Button onClick={() => { setSection("new"); setDetailId(null); }}><Plus className="h-4 w-4" />New request</Button>
        </div>
      </div>

      {/* Grouped tab bar — reference Work · File · Insights · Admin, divider-separated. */}
      <nav aria-label="Intake sections" className="flex flex-wrap items-center border-b border-slate-200">
        {groups.map((grp, gi) => (
          <Fragment key={gi}>
            {gi > 0 && <span aria-hidden className="mx-2 h-4 w-px self-center bg-slate-200" />}
            {grp.map((t) => (
              <button
                key={t.id}
                onClick={() => { setSection(t.id); setDetailId(null); }}
                aria-current={section === t.id ? "page" : undefined}
                className={cn(
                  "-mb-px inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 font-mono text-[11px] font-medium uppercase tracking-[0.09em] transition-colors",
                  section === t.id
                    ? "border-brand-600 text-slate-900"
                    : "border-transparent text-slate-500 hover:text-slate-900",
                )}
              >
                {t.label}
                {counts[t.id] > 0 && (
                  <span className={cn(
                    "rounded-full px-1.5 text-[10px] tabular-nums",
                    section === t.id ? "bg-brand-50 text-brand-700" : "bg-slate-200 text-slate-500",
                  )}>
                    {counts[t.id]}
                  </span>
                )}
              </button>
            ))}
          </Fragment>
        ))}
      </nav>

      {detailId ? (
        <RequestDetailView id={detailId} canTriage={isStaff} onBack={() => setDetailId(null)} />
      ) : (
        <>
          {section === "queue" && <InboxTab onOpen={setDetailId} />}
          {section === "kanban" && <KanbanTab onOpen={setDetailId} />}
          {section === "mywork" && <MyWorkView isStaff={isStaff} onOpen={setDetailId} />}
          {section === "new" && <NewRequestTab onFiled={setDetailId} />}
          {section === "self" && <SelfServiceTab onFileTopic={() => setSection("new")} />}
          {section === "sla" && isStaff && <SlaDashboardTab isAdmin={isAdmin} />}
          {section === "agents" && isStaff && <AiOpsTab />}
          {section === "ops" && isStaff && <OperationsView isAdmin={isAdmin} />}
        </>
      )}
    </div>
  );
}

// ---- shared bits ----------------------------------------------------------

function StatusPill({ r }: { r: IntakeRequest }) {
  return (
    <Badge tone={STATUS_TONE[r.status] as never}>{STATUS_LABEL[r.status]}</Badge>
  );
}

function fmtElapsedHours(h: number): string {
  const totalMin = Math.max(0, Math.round(h * 60));
  const hh = Math.floor(totalMin / 60);
  const mm = totalMin % 60;
  return hh > 0 ? `${hh}h ${mm}m` : `${mm}m`;
}

function SlaCell({ r }: { r: IntakeRequest }) {
  if (r.status === "closed" || r.status === "approved")
    return <span className="text-xs text-slate-400">—</span>;
  const posture = r.sla_status as IntakeSlaPosture;
  const submitted = r.submitted_at ? new Date(r.submitted_at).getTime() : null;
  const elapsedH = submitted != null ? (Date.now() - submitted) / 3_600_000 : null;
  return (
    <div className="flex flex-col gap-1">
      {elapsedH != null && (
        <span className="text-xs tabular-nums text-slate-600">
          {fmtElapsedHours(elapsedH)} <span className="text-slate-400">of {r.sla_hours}h</span>
        </span>
      )}
      <div className="flex items-center gap-2">
        <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-200">
          <div
            className="h-full rounded-full"
            style={{ width: `${Math.min(100, r.sla_pct)}%`, background: slaBarColor(posture) }}
          />
        </div>
        <Badge tone={POSTURE_TONE[posture] as never}>{POSTURE_LABEL[posture]}</Badge>
      </div>
    </div>
  );
}

function RequestRow({ r, onOpen, showStatus = true, selectable = false, checked = false, onToggle, showRequester = false, showDescription = false }: { r: IntakeRequest; onOpen: (id: string) => void; showStatus?: boolean; selectable?: boolean; checked?: boolean; onToggle?: (id: string) => void; showRequester?: boolean; showDescription?: boolean }) {
  return (
    <TR
      className={cn(
        "cursor-pointer transition-colors hover:bg-slate-100/70",
        showRequester && r.sla_status === "overdue" && "bg-red-50/50",
        showRequester && r.sla_status === "at_risk" && "bg-amber-50/40",
      )}
      onClick={() => onOpen(r.id)}
    >
      {selectable && (
        <TD>
          <input type="checkbox" className="accent-brand-600" checked={checked}
            aria-label={`Select ${r.ref}`}
            onClick={(e) => e.stopPropagation()}
            onChange={() => onToggle?.(r.id)} />
        </TD>
      )}
      <TD><span className="font-mono text-xs text-slate-500">{r.ref}</span></TD>
      {showRequester && (
        <TD className="whitespace-nowrap">
          <div className="font-medium text-slate-900">{r.requester_name ?? "—"}</div>
          {r.department && <div className="text-xs text-slate-400">{r.department}</div>}
        </TD>
      )}
      <TD className="whitespace-nowrap font-medium text-slate-900">{r.type_label}</TD>
      {showDescription && (
        <TD className="max-w-[16rem] truncate text-slate-600" title={r.description || undefined}>
          {r.description || <span className="text-slate-400">—</span>}
        </TD>
      )}
      <TD><Badge tone={PRIORITY_TONE[r.priority] as never}>{r.priority}</Badge></TD>
      <TD><SlaCell r={r} /></TD>
      {showStatus && <TD><StatusPill r={r} /></TD>}
      <TD className="text-slate-600">{r.assigned_to_label ?? <span className="text-slate-400">Unassigned</span>}</TD>
    </TR>
  );
}

// ---- New Request ----------------------------------------------------------

// Two-card chooser — the reference app's "how would you like to file this?"
// router: a fast structured form vs. talking it through with the copilot.
function PathRouter({ onPick }: { onPick: (m: "form" | "chat") => void }) {
  const cardCls =
    "group rounded-xl border border-slate-200 bg-slate-100/40 p-5 text-left transition-colors hover:border-brand-300 hover:bg-brand-50";
  return (
    <div className="max-w-3xl space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">How would you like to file this?</h2>
        <p className="mt-1 text-sm text-slate-500">
          Pick the fast structured form when you know what you need, or talk it through with the
          copilot for a complex or ambiguous matter.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <button onClick={() => onPick("form")} className={cardCls}>
          <div className="flex items-center gap-2">
            <FileText className="h-5 w-5 text-brand-600" />
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-slate-400">Fast path · structured form</span>
          </div>
          <h3 className="mt-2 text-base font-semibold text-slate-900">When you know exactly what you need</h3>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">
            NDA, contract review, privacy / DPA, IP, vendor due-diligence — a quick form the
            reviewer can triage instantly.
          </p>
          <span className="mt-3 inline-block text-xs font-semibold text-brand-600 group-hover:underline">Open the form →</span>
        </button>
        <button onClick={() => onPick("chat")} className={cardCls}>
          <div className="flex items-center gap-2">
            <MessageSquare className="h-5 w-5 text-brand-600" />
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-slate-400">Smart path · guided chat</span>
          </div>
          <h3 className="mt-2 text-base font-semibold text-slate-900">When you&rsquo;re not sure what you need</h3>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">
            Employment, litigation, a novel question — talk it through and the copilot files it to
            the right place.
          </p>
          <span className="mt-3 inline-block text-xs font-semibold text-brand-600 group-hover:underline">Start a conversation →</span>
        </button>
      </div>
      <p className="text-xs text-slate-400">Either path works — you can switch anytime.</p>
    </div>
  );
}

function NewRequestTab({ onFiled }: { onFiled: (id: string) => void }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data: types } = useQuery({ queryKey: ["intake-types"], queryFn: () => intakeApi.listTypes() });
  const [typeId, setTypeId] = useState("");
  const [priority, setPriority] = useState("Medium");
  const [department, setDepartment] = useState("");
  const [description, setDescription] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const type = (types ?? []).find((t) => t.id === typeId);
  const preview = derivePreview(description, type);
  // Reference-style path chooser: pick fast-form vs guided-chat before filing.
  const [chosen, setChosen] = useState<null | "form" | "chat">(null);

  // Client-side gate on required dynamic fields so we never fire a submit the
  // server will 422 with no useful feedback.
  const missingRequired = (type?.fields ?? []).filter(
    (f) => f.required && !String(values[f.key] ?? "").trim(),
  );
  const canSubmit = !!description.trim() && missingRequired.length === 0;

  async function submit() {
    setBusy(true);
    try {
      const r = await intakeApi.create({
        type_label: type ? type.name + " Request" : "General request",
        request_type_id: typeId || null,
        priority, department: department || null, description,
        field_values: Object.keys(values).length ? values : null,
      });
      qc.invalidateQueries({ queryKey: ["intake-mine"] });
      qc.invalidateQueries({ queryKey: ["intake-list"] });
      notify(`Filed ${r.ref} — routed for triage`, "success");
      onFiled(r.id);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Submit failed", "error");
    } finally {
      setBusy(false);
    }
  }

  if (!chosen) return <PathRouter onPick={setChosen} />;

  return (
    <div className="grid max-w-2xl gap-4">
      <button onClick={() => setChosen(null)}
        className="inline-flex w-fit items-center gap-1 font-mono text-[11px] uppercase tracking-[0.09em] text-slate-500 hover:text-slate-900">
        ← Change path
      </button>
      {chosen === "chat" ? <CopilotChat onFiled={onFiled} /> : (
      <Card>
        <CardHeader><CardTitle>What do you need?</CardTitle></CardHeader>
        <CardBody className="space-y-4">
          <Field label="Request type">
            <Select value={typeId} onChange={(e) => { setTypeId(e.target.value); setValues({}); }}>
              <option value="">General question / not sure</option>
              {(types ?? []).map((t) => (
                <option key={t.id} value={t.id}>{t.name}{t.workstream ? ` · ${t.workstream}` : ""}</option>
              ))}
            </Select>
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Priority">
              <Select value={priority} onChange={(e) => setPriority(e.target.value)}>
                {["Low", "Medium", "High", "Critical"].map((p) => <option key={p}>{p}</option>)}
              </Select>
            </Field>
            <Field label="Department">
              <Input value={department} onChange={(e) => setDepartment(e.target.value)} placeholder="e.g. Sales — EMEA" />
            </Field>
          </div>
          {(type?.fields ?? []).map((f) => (
            <DynamicField key={f.key} f={f} value={values[f.key] ?? ""}
              onChange={(v) => setValues((s) => ({ ...s, [f.key]: v }))} />
          ))}
          <Field label="Describe the request" hint="The reviewer reads this to triage — be specific.">
            <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={4}
              placeholder="What do you need, with whom, by when?" />
          </Field>
          {preview && (
            <div className="rounded-lg border border-brand-200 bg-brand-50 px-3 py-2 text-xs text-brand-700">
              Likely routing: <strong>{preview}</strong>
            </div>
          )}
          <div className="flex items-center justify-end gap-3">
            {missingRequired.length > 0 && (
              <span className="text-xs text-amber-600">
                Required: {missingRequired.map((f) => f.label).join(", ")}
              </span>
            )}
            <Button onClick={submit} loading={busy} disabled={!canSubmit}>Submit request</Button>
          </div>
        </CardBody>
      </Card>
      )}
    </div>
  );
}

function DynamicField({ f, value, onChange }: { f: IntakeFieldSpec; value: string; onChange: (v: string) => void }) {
  const label = f.label + (f.required ? " *" : "");
  if (f.kind === "textarea")
    return <Field label={label}><Textarea value={value} onChange={(e) => onChange(e.target.value)} rows={3} /></Field>;
  if (f.kind === "select")
    return (
      <Field label={label}>
        <Select value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">Select…</option>
          {(f.options ?? []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </Select>
      </Field>
    );
  const type = f.kind === "date" ? "date" : f.kind === "number" ? "number" : "text";
  return <Field label={label}><Input type={type} value={value} onChange={(e) => onChange(e.target.value)} /></Field>;
}

function derivePreview(desc: string, type?: IntakeRequestType): string | null {
  const v = desc.toLowerCase();
  if (type) return `${type.name}${type.workstream ? ` · ${type.workstream}` : ""}`;
  if (!v.trim()) return null;
  if (/litig|dispute/.test(v)) return "Litigation → senior counsel";
  if (/privacy|dpa|data protection/.test(v)) return "Privacy / DPA → DPO review";
  if (/nda|non-disclosure/.test(v)) return "NDA → fast lane";
  if (/msa|contract|review/.test(v)) return "Contract review → counsel";
  return "General → triage queue";
}

// ---- My Requests (requester portal) --------------------------------------

function MyRequestsTab({ onOpen }: { onOpen: (id: string) => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-mine"], queryFn: intakeApi.mine });
  if (isLoading) return <CenterSpinner label="Loading your requests…" />;
  if (error) return <ErrorState error={error} />;
  const rows = data ?? [];
  if (!rows.length)
    return <EmptyState icon={<DoorOpen className="h-5 w-5" />} title="No requests yet"
      description="File your first request from the New Request tab." />;
  return (
    <Card>
      <Table>
        <THead><TR><TH>Ref</TH><TH>Request</TH><TH>Priority</TH><TH>SLA</TH><TH>Status</TH><TH>Assignee</TH></TR></THead>
        <tbody>{rows.map((r) => <RequestRow key={r.id} r={r} onOpen={onOpen} />)}</tbody>
      </Table>
    </Card>
  );
}

// ---- Inbox (staff, whole queue) ------------------------------------------

// Inbox filters — mirrors the reference intake app's Inbox chips.
function buildFilters(myId: string | null): { id: string; label: string; match: (r: IntakeRequest) => boolean }[] {
  return [
    { id: "all", label: "All", match: () => true },
    { id: "mine", label: "My Queue", match: (r) => !!myId && r.assigned_to_user_id === myId },
    { id: "overdue", label: "SLA Breached", match: (r) => r.sla_status === "overdue" },
    { id: "at_risk", label: "At risk", match: (r) => r.sla_status === "at_risk" },
    { id: "in_review", label: "In review", match: (r) => r.status === "in_review" },
    { id: "auto", label: "Auto-completed", match: (r) => r.status === "approved" },
    { id: "awaiting", label: "New", match: (r) => r.status === "awaiting_triage" },
  ];
}

function InboxTab({ onOpen }: { onOpen: (id: string) => void }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { user } = useAuth();
  const canTriage = can(user, "intake:triage");
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-list"], queryFn: () => intakeApi.list() });
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  if (isLoading) return <CenterSpinner label="Loading the queue…" />;
  if (error) return <ErrorState error={error} />;
  const rows = data ?? [];
  const FILTERS = buildFilters(user?.id ?? null);
  const total = rows.length;
  const autoResolved = rows.filter((r) => r.status === "approved").length;
  const inFlight = rows.filter((r) => r.status === "in_review" || r.status === "escalated").length;
  const q = search.trim().toLowerCase();
  const activeMatch = (FILTERS.find((f) => f.id === filter) ?? FILTERS[0]).match;
  const shown = rows.filter(activeMatch).filter((r) =>
    !q || `${r.ref} ${r.requester_name ?? ""} ${r.type_label} ${r.description ?? ""}`.toLowerCase().includes(q));
  const overdue = rows.filter((r) => r.sla_status === "overdue" && r.status !== "closed").length;

  const toggle = (id: string) =>
    setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const selectedShown = shown.filter((r) => selected.has(r.id)).map((r) => r.id);
  const allShownSelected = shown.length > 0 && selectedShown.length === shown.length;
  const toggleAll = () =>
    setSelected((s) => {
      const n = new Set(s);
      if (allShownSelected) shown.forEach((r) => n.delete(r.id));
      else shown.forEach((r) => n.add(r.id));
      return n;
    });
  async function bulk(action: "approved" | "manual_close") {
    if (!selectedShown.length) return;
    setBusy(true);
    try {
      const res = await intakeApi.bulkTriage(selectedShown, action);
      const ok = res.results.filter((x) => x.ok).length;
      const failed = res.results.length - ok;
      qc.invalidateQueries({ queryKey: ["intake-list"] });
      qc.invalidateQueries({ queryKey: ["intake-mywork"] });
      setSelected(new Set());
      notify(`${action === "approved" ? "Approved" : "Closed"} ${ok}${failed ? ` · ${failed} failed` : ""}`,
        failed ? "error" : "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Bulk action failed", "error");
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Today's requests" value={String(total)} tone="blue" />
        <StatCard label="Auto-resolved"
          value={String(autoResolved)}
          hint={total ? `${Math.round((autoResolved / total) * 100)}% deflection` : undefined}
          tone="green" />
        <StatCard label="In flight" value={String(inFlight)} tone="slate" />
        <StatCard label="SLA breached" value={String(overdue)} hint="Auto-escalated" tone="red" />
      </div>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <div className="flex flex-1 flex-wrap gap-1.5">
          {FILTERS.map((f) => {
            const n = rows.filter(f.match).length;
            const active = filter === f.id;
            return (
              <button key={f.id} onClick={() => setFilter(f.id)}
                className={cn("inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold transition-colors",
                  active ? "border-brand-600 bg-brand-600 text-white"
                    : "border-slate-200 bg-slate-100 text-slate-600 hover:bg-slate-200")}>
                {f.label}
                <span className={cn("rounded-full px-1.5 text-[10px] tabular-nums",
                  active ? "bg-white/25 text-white" : "bg-slate-200 text-slate-500")}>{n}</span>
              </button>
            );
          })}
        </div>
        <div className="relative sm:w-64">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Search ref, requester, type…" className="h-9 pl-9" />
        </div>
      </div>
      {canTriage && selectedShown.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-brand-200 bg-brand-50 px-3 py-2 text-sm">
          <span className="font-medium text-brand-700">{selectedShown.length} selected</span>
          <Button size="sm" loading={busy} onClick={() => bulk("approved")}>Approve</Button>
          <Button size="sm" variant="outline" loading={busy} onClick={() => bulk("manual_close")}>Close</Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>Clear</Button>
        </div>
      )}
      <Card className="overflow-hidden">
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            {(FILTERS.find((f) => f.id === filter) ?? FILTERS[0]).label}
            <span className="ml-1.5 tabular-nums text-slate-400">{shown.length}{shown.length !== total ? ` of ${total}` : ""}</span>
          </p>
          <span className="hidden font-mono text-[10px] uppercase tracking-wide text-slate-400 sm:inline">
            Status and SLA are independent signals
          </span>
        </div>
        {shown.length === 0 ? (
          <CardBody><EmptyState title="Nothing here"
            description={q ? `No requests match “${search.trim()}”.` : "No requests match this filter."} /></CardBody>
        ) : (
          <Table>
            <THead><TR>
              {canTriage && <TH><input type="checkbox" className="accent-brand-600" aria-label="Select all"
                checked={allShownSelected} onChange={toggleAll} /></TH>}
              <TH>Ref</TH><TH>Requester</TH><TH>Type</TH><TH>Description</TH><TH>Priority</TH><TH>SLA</TH><TH>Status</TH><TH>Assignee</TH>
            </TR></THead>
            <tbody>{shown.map((r) => (
              <RequestRow key={r.id} r={r} onOpen={onOpen} showRequester showDescription
                selectable={canTriage} checked={selected.has(r.id)} onToggle={toggle} />
            ))}</tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}

// ---- My Work (staff) ------------------------------------------------------

function MyWorkTab({ onOpen }: { onOpen: (id: string) => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-mywork"], queryFn: intakeApi.myWork });
  if (isLoading) return <CenterSpinner label="Loading your work…" />;
  if (error) return <ErrorState error={error} />;
  const mw = data!;
  const tickets = [...mw.my_tickets].sort(sortBySla);
  const allClear = !mw.awaiting_review.length && !tickets.length && !mw.my_tasks.length;

  return (
    <div className="space-y-4">
      {allClear && (
        <Card><CardBody>
          <EmptyState icon={<DoorOpen className="h-5 w-5" />} title="Inbox zero 🎉"
            description="Nothing waiting on you. Pick up unassigned work from the Inbox." />
        </CardBody></Card>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Awaiting my review</CardTitle>
            <Badge tone="blue">{String(mw.awaiting_review.length)}</Badge></CardHeader>
          <CardBody className="p-0">
            {mw.awaiting_review.length === 0
              ? <p className="px-4 py-3 text-xs text-slate-400">No agent recommendations waiting on you.</p>
              : <Table><tbody>{mw.awaiting_review.map((r) => <RequestRow key={r.id} r={r} onOpen={onOpen} showStatus={false} />)}</tbody></Table>}
          </CardBody>
        </Card>
        <Card>
          <CardHeader><CardTitle>My tickets</CardTitle><Badge tone="slate">{String(tickets.length)}</Badge></CardHeader>
          <CardBody className="p-0">
            {tickets.length === 0
              ? <p className="px-4 py-3 text-xs text-slate-400">Nothing assigned to you.</p>
              : <Table><tbody>{tickets.map((r) => <RequestRow key={r.id} r={r} onOpen={onOpen} />)}</tbody></Table>}
          </CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader><CardTitle>My tasks</CardTitle></CardHeader>
        <CardBody className="p-0">
          {mw.my_tasks.length === 0
            ? <p className="px-4 py-3 text-xs text-slate-400">No open sub-tasks assigned to you.</p>
            : <Table><tbody>{mw.my_tasks.map((t) => (
              <TR key={t.id}><TD className="font-medium">{t.title}</TD>
                <TD><Badge tone="slate">{titleCase(t.status)}</Badge></TD>
                <TD className="tabular-nums text-slate-500">{t.effort_minutes}m</TD></TR>
            ))}</tbody></Table>}
        </CardBody>
      </Card>
    </div>
  );
}

// ---- Request Types admin --------------------------------------------------

function RequestTypesTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-types-all"], queryFn: () => intakeApi.listTypes(true) });
  const [creating, setCreating] = useState(false);

  async function remove(t: IntakeRequestType) {
    if (!window.confirm(`Delete request type "${t.name}"?`)) return;
    try {
      await intakeApi.deleteType(t.id);
      qc.invalidateQueries({ queryKey: ["intake-types-all"] });
      qc.invalidateQueries({ queryKey: ["intake-types"] });
      notify("Type deleted", "success");
    } catch (e) { notify(e instanceof Error ? e.message : "Delete failed", "error"); }
  }

  if (isLoading) return <CenterSpinner />;
  if (error) return <ErrorState error={error} />;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">No-code request types — fields + stage workflow, live on the New Request form instantly.</p>
        <Button onClick={() => setCreating(true)}><Plus className="h-4 w-4" />New type</Button>
      </div>
      <Card>
        {(data ?? []).length === 0 ? (
          <CardBody><EmptyState title="No request types" description="Create one to give filers a structured form." /></CardBody>
        ) : (
          <Table>
            <THead><TR><TH>Type</TH><TH>Workstream</TH><TH>Fields</TH><TH>Stage workflow</TH><TH></TH></TR></THead>
            <tbody>{(data ?? []).map((t) => (
              <TR key={t.id}>
                <TD className="font-medium">{t.name}{!t.active && <span className="ml-2 text-xs text-slate-400">(inactive)</span>}</TD>
                <TD className="text-slate-500">{t.workstream ?? "—"}</TD>
                <TD className="tabular-nums text-slate-500">{t.fields.length}</TD>
                <TD className="text-xs text-slate-500">{["Submitted", ...(t.stages ?? ["Assigned", "Review"]), "Complete"].join(" → ")}</TD>
                <TD className="text-right">
                  <Button variant="ghost" size="sm" onClick={() => remove(t)}><Trash2 className="h-3.5 w-3.5" />Delete</Button>
                </TD>
              </TR>
            ))}</tbody>
          </Table>
        )}
      </Card>
      {creating && <TypeEditorModal onClose={() => setCreating(false)}
        onSaved={() => { qc.invalidateQueries({ queryKey: ["intake-types-all"] }); qc.invalidateQueries({ queryKey: ["intake-types"] }); setCreating(false); }} />}
    </div>
  );
}

function TypeEditorModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const { notify } = useToast();
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [workstream, setWorkstream] = useState("");
  const [stages, setStages] = useState("");
  const [fields, setFields] = useState<{ key: string; label: string; kind: string; required: boolean }[]>([]);
  const [busy, setBusy] = useState(false);

  const canSave = name.trim() && key.trim();
  async function save() {
    setBusy(true);
    try {
      await intakeApi.createType({
        key: key.trim().toLowerCase(), name: name.trim(),
        workstream: workstream.trim() || null,
        stages: stages.trim() ? stages.split(",").map((s) => s.trim()).filter(Boolean) : null,
        fields: fields.map((f, i) => ({ ...f, sort_order: (i + 1) * 10 })),
      });
      notify("Request type created", "success"); onSaved();
    } catch (e) { notify(e instanceof Error ? e.message : "Create failed", "error"); }
    finally { setBusy(false); }
  }

  return (
    <Modal open onClose={onClose} title="New request type" size="lg">
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name"><Input value={name} onChange={(e) => { setName(e.target.value); if (!key) setKey(e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")); }} placeholder="NDA" /></Field>
          <Field label="Key" hint="lowercase, unique"><Input value={key} onChange={(e) => setKey(e.target.value)} placeholder="nda" /></Field>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Workstream"><Input value={workstream} onChange={(e) => setWorkstream(e.target.value)} placeholder="Commercial" /></Field>
          <Field label="Stages" hint="comma-separated mid-stages (blank = default)"><Input value={stages} onChange={(e) => setStages(e.target.value)} placeholder="draft, review" /></Field>
        </div>
        <div>
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Fields</p>
            <Button variant="outline" size="sm" onClick={() => setFields((s) => [...s, { key: "", label: "", kind: "text", required: false }])}>Add field</Button>
          </div>
          <div className="space-y-2">
            {fields.map((f, i) => (
              <div key={i} className="grid grid-cols-[1fr_1fr_auto_auto_auto] items-center gap-2">
                <Input value={f.label} onChange={(e) => setFields((s) => s.map((x, j) => j === i ? { ...x, label: e.target.value, key: x.key || e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "_") } : x))} placeholder="Label" />
                <Input value={f.key} onChange={(e) => setFields((s) => s.map((x, j) => j === i ? { ...x, key: e.target.value } : x))} placeholder="key" />
                <Select value={f.kind} onChange={(e) => setFields((s) => s.map((x, j) => j === i ? { ...x, kind: e.target.value } : x))}>
                  {["text", "textarea", "select", "date", "number", "boolean"].map((k) => <option key={k}>{k}</option>)}
                </Select>
                <label className="flex items-center gap-1 text-xs"><input type="checkbox" className="accent-brand-600" checked={f.required} onChange={(e) => setFields((s) => s.map((x, j) => j === i ? { ...x, required: e.target.checked } : x))} />req</label>
                <Button variant="ghost" size="sm" onClick={() => setFields((s) => s.filter((_, j) => j !== i))}><Trash2 className="h-3.5 w-3.5" /></Button>
              </div>
            ))}
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={save} loading={busy} disabled={!canSave}>Create type</Button>
        </div>
      </div>
    </Modal>
  );
}

// ---- Request detail (inline, reference-style) -----------------------------

function WorkflowStepper({ steps }: { steps: IntakeRequest["workflow"] }) {
  return (
    <section>
      <p className="mb-2 font-mono text-[11px] uppercase tracking-[0.12em] text-slate-400">Request workflow</p>
      <div className="flex overflow-x-auto rounded-lg border border-slate-200">
        {steps.map((s) => (
          <div key={s.stage}
            className={cn("min-w-[128px] flex-1 border-r border-slate-200 px-3 py-3 text-center last:border-r-0",
              s.active ? "bg-brand-50" : s.done ? "bg-slate-50" : "")}>
            <div className={cn("text-sm leading-none", s.done ? "text-emerald-600" : s.active ? "text-brand-600" : "text-slate-300")}>
              {s.done ? "✓" : s.active ? "⏳" : "○"}
            </div>
            <div className={cn("mt-1.5 text-xs font-medium", s.active || s.done ? "text-slate-900" : "text-slate-400")}>{s.label}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

// The CLM contract lifecycle — once a request has a drafted contract, the ticket
// rides these stages instead of the intake spine.
const CONTRACT_STAGES: { key: string; label: string }[] = [
  { key: "intake", label: "Intake" },
  { key: "drafting", label: "Drafting" },
  { key: "review", label: "Review" },
  { key: "approval", label: "Approval" },
  { key: "signature", label: "Signature" },
  { key: "active", label: "Active" },
  { key: "closed", label: "Closed" },
];

// Which draftable document (if any) a request maps to — mirrors the backend
// resolve_doc_type keyword pass so the "Draft" button shows for the 4 types.
function draftableDocType(r: IntakeRequest): string | null {
  const t = `${r.type_label} ${r.description ?? ""}`.toLowerCase();
  if (/nda|non-disclosure|non disclosure/.test(t)) return "NDA";
  if (/\b(dpa|data processing|data protection|gdpr)\b/.test(t)) return "DPA";
  if (/\b(msa|master service|master services|services agreement|statement of work|sow)\b/.test(t)) return "MSA";
  if (/vendor|supplier|procurement/.test(t)) return "Vendor agreement";
  return null;
}

function goToWorkspace(contractId: string) {
  window.location.href = `/contracts/${contractId}`;
}

// The counterparty this request is about — structured party first, else the
// screening-derived name.
function requestCounterparty(r: IntakeRequest): string | null {
  const parties = (r.parties ?? []) as { name: string; role: string }[];
  const cp = parties.find((p) => p.role === "counterparty") ?? parties[0];
  if (cp?.name) return cp.name;
  const sc = (r.screening ?? null) as { counterparty?: string } | null;
  return (sc?.counterparty || "").trim() || null;
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="font-mono text-[10px] uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-800">{children}</dd>
    </div>
  );
}

function riskTone(band?: string | null): "red" | "amber" | "green" | "slate" {
  const b = (band ?? "").toLowerCase();
  return b === "high" ? "red" : b === "medium" ? "amber" : b === "low" ? "green" : "slate";
}

// Related contracts — prior deals with the same counterparty, and other
// contracts of the same type, so the reviewer has precedent at a glance.
const _TYPE_SYNONYMS: Record<string, string[]> = {
  "nda": ["nda", "non-disclosure", "non disclosure", "confidential"],
  "msa": ["msa", "master service", "services agreement", "sow", "statement of work"],
  "dpa": ["dpa", "data processing", "data protection", "gdpr"],
  "vendor agreement": ["vendor", "supplier", "procurement"],
};

function RelatedContractsCard({ r, contracts }: { r: IntakeRequest; contracts: ContractResponse[] }) {
  const cp = requestCounterparty(r);
  const typeHint = (draftableDocType(r) ?? "").toLowerCase();
  const keywords = _TYPE_SYNONYMS[typeHint] ?? (typeHint ? [typeHint] : []);
  const norm = (s?: string | null) => (s ?? "").trim().toLowerCase();
  const pool = contracts.filter((c) => c.id !== r.contract_id);
  const sameParty = cp
    ? pool.filter((c) => {
        const a = norm(c.counterparty_name);
        const b = norm(cp);
        return !!a && !!b && (a.includes(b) || b.includes(a));
      })
    : [];
  const sameType = keywords.length
    ? pool.filter((c) => {
        const hay = `${norm(c.contract_type)} ${norm(c.title)}`;
        return keywords.some((k) => hay.includes(k)) && !sameParty.includes(c);
      })
    : [];

  const Row = (c: ContractResponse) => (
    <a key={c.id} href={`/contracts/${c.id}`}
      className="flex items-center justify-between gap-2 rounded-md border border-slate-200 px-3 py-2 hover:border-brand-300 hover:bg-brand-50">
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium text-slate-800">{c.title}</span>
        <span className="text-[11px] text-slate-400">
          {c.contract_type ?? "—"}{c.counterparty_name ? ` · ${c.counterparty_name}` : ""} · {titleCase(c.lifecycle_stage)}
        </span>
      </span>
      {c.risk_band && <Badge tone={riskTone(c.risk_band) as never}>{c.risk_band}</Badge>}
    </a>
  );

  return (
    <Card>
      <CardHeader><CardTitle>Related contracts</CardTitle>
        <span className="ml-auto font-mono text-[10px] uppercase tracking-wide text-slate-400">
          {sameParty.length + sameType.length} found
        </span>
      </CardHeader>
      <CardBody className="space-y-3">
        {!sameParty.length && !sameType.length && (
          <p className="text-xs text-slate-400">No prior contracts with {cp ?? "this counterparty"} or of this type.</p>
        )}
        {sameParty.length > 0 && (
          <div>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400">Same counterparty{cp ? ` · ${cp}` : ""}</p>
            <div className="space-y-1.5">{sameParty.slice(0, 5).map(Row)}</div>
          </div>
        )}
        {sameType.length > 0 && (
          <div>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400">Similar {typeHint.toUpperCase()} contracts</p>
            <div className="space-y-1.5">{sameType.slice(0, 5).map(Row)}</div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

// Contract lifecycle stage tracker — replaces the intake WorkflowStepper once a
// contract exists. Fed by the contract's review-status (current stage).
function ContractLifecycleTracker({ contractId }: { contractId: string }) {
  const { data: rs } = useQuery({
    queryKey: ["contract-review", contractId],
    queryFn: () => contractsApi.reviewStatus(contractId),
  });
  const curIdx = rs ? CONTRACT_STAGES.findIndex((s) => s.key === rs.lifecycle_stage) : -1;
  return (
    <section>
      <p className="mb-2 font-mono text-[11px] uppercase tracking-[0.12em] text-slate-400">Contract lifecycle</p>
      <div className="flex overflow-x-auto rounded-lg border border-slate-200">
        {CONTRACT_STAGES.map((s, i) => {
          const done = curIdx > i, active = curIdx === i;
          return (
            <div key={s.key}
              className={cn("min-w-[100px] flex-1 border-r border-slate-200 px-2 py-3 text-center last:border-r-0",
                active ? "bg-brand-50" : done ? "bg-slate-50" : "")}>
              <div className={cn("text-sm leading-none", done ? "text-emerald-600" : active ? "text-brand-600" : "text-slate-300")}>
                {done ? "✓" : active ? "⏳" : "○"}
              </div>
              <div className={cn("mt-1.5 text-xs font-medium", active || done ? "text-slate-900" : "text-slate-400")}>{s.label}</div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// The mission-control "next step" bar, driven by the contract's review-status.
// Simple transitions run inline; anything needing the redline cards, counterparty
// passcode, or signer picker deep-links to the full contract workspace.
function ContractNextStep({ contractId, onRefresh }: { contractId: string; onRefresh: () => void }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data: rs } = useQuery({ queryKey: ["contract-review", contractId], queryFn: () => contractsApi.reviewStatus(contractId) });
  const { data: contract } = useQuery({ queryKey: ["contract-obj", contractId], queryFn: () => contractsApi.get(contractId) });
  const { data: playbooks } = useQuery({ queryKey: ["playbooks"], queryFn: () => playbooksApi.list() });
  const [busy, setBusy] = useState(false);

  function done() {
    qc.invalidateQueries({ queryKey: ["contract-review", contractId] });
    qc.invalidateQueries({ queryKey: ["contract-obj", contractId] });
    onRefresh();
  }
  async function run(fn: () => Promise<unknown>, msg: string) {
    setBusy(true);
    try { await fn(); done(); notify(msg, "success"); }
    catch (e) { notify(e instanceof Error ? e.message : "Action failed", "error"); }
    finally { setBusy(false); }
  }
  async function runRedline() {
    setBusy(true);
    try {
      await contractsApi.computeRisk(contractId);
      const ct = (contract?.contract_type ?? "").toLowerCase();
      const pbType = (p: { contract_type?: string | null }) => (p.contract_type ?? "").toLowerCase();
      const pb = (playbooks ?? []).find((p) => ct && pbType(p) === ct) ?? (playbooks ?? [])[0];
      if (pb) await playbooksApi.createRun(pb.id, { contract_id: contractId, create_redline: true, use_ai: true });
      done();
      notify(pb ? "Redline review run — risk + deviations updated" : "Risk scored (no playbook configured)", "success");
    } catch (e) { notify(e instanceof Error ? e.message : "Redline review failed", "error"); }
    finally { setBusy(false); }
  }
  async function logRevision(file: File) {
    setBusy(true);
    try {
      await contractsApi.logCounterpartyRevision(contractId, file);
      done();
      notify("Counterparty revision logged — re-review runs automatically", "success");
    } catch (e) { notify(e instanceof Error ? e.message : "Upload failed", "error"); }
    finally { setBusy(false); }
  }

  if (!rs) return <Card><CardBody><CenterSpinner /></CardBody></Card>;
  const stage = rs.lifecycle_stage;
  const NA = rs.next_action;

  let primary: React.ReactNode = null;
  if (NA === "run_ai")
    primary = <Button size="sm" loading={busy} onClick={() => run(async () => { await aiApi.rerunMetadata(contractId); await aiApi.rerunClauses(contractId); }, "AI analysis started")}>Run AI analysis</Button>;
  else if (NA === "move_to_drafting")
    primary = <Button size="sm" loading={busy} onClick={() => run(() => contractsApi.transition(contractId, "drafting", { reason: "From intake" }), "Advanced to drafting")}>Advance to drafting</Button>;
  else if (NA === "move_to_review")
    primary = <Button size="sm" loading={busy} onClick={() => run(() => contractsApi.transition(contractId, "review", { reason: "From intake" }), "Advanced to review")}>Advance to review</Button>;
  else if (NA === "submit_approval")
    primary = <Button size="sm" loading={busy} disabled={!rs.ready_for_approval} onClick={() => run(() => approvalsApi.submit({ contract_id: contractId }), "Submitted for approval")}>Submit for approval</Button>;
  else if (NA === "resolve_issues" || NA === "resolve_redlines" || NA === "resolve_comments")
    primary = <Button size="sm" onClick={() => goToWorkspace(contractId)}>Resolve in workspace →</Button>;

  return (
    <Card className="border-2 border-brand-500">
      <CardHeader className="bg-brand-50"><CardTitle>Next step</CardTitle>
        <span className="ml-auto font-mono text-[10px] uppercase tracking-wide text-slate-500">{stage}</span></CardHeader>
      <CardBody className="space-y-3">
        <p className="text-sm text-slate-700">{rs.next_step}</p>
        <div className="flex flex-wrap gap-2">
          {primary}
          {stage === "review" && (
            <Button size="sm" variant="outline" loading={busy} onClick={runRedline}>Run redline review</Button>
          )}
          {(stage === "drafting" || stage === "review") && (
            <Button size="sm" variant="outline" onClick={() => goToWorkspace(contractId)}>Send to counterparty →</Button>
          )}
          {(stage === "review" || stage === "approval") && (
            <label className={cn("inline-flex cursor-pointer items-center rounded-md border border-slate-200 bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-200", busy && "pointer-events-none opacity-60")}>
              {busy ? "Uploading…" : "Log counterparty redlines"}
              <input type="file" className="hidden" accept=".txt,.docx,.pdf,.doc" disabled={busy}
                onChange={(e) => { const f = e.target.files?.[0]; if (f) logRevision(f); e.target.value = ""; }} />
            </label>
          )}
          {stage === "signature" && (
            <Button size="sm" variant="outline" onClick={() => goToWorkspace(contractId)}>Send for signature →</Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => goToWorkspace(contractId)}>Open full workspace →</Button>
        </div>
        {(rs.open_issues > 0 || rs.pending_redlines > 0 || rs.high_severity_issues > 0) && (
          <div className="flex flex-wrap gap-3 text-xs text-slate-500">
            {rs.open_issues > 0 && <span>{rs.open_issues} open issues</span>}
            {rs.pending_redlines > 0 && <span>{rs.pending_redlines} pending redlines</span>}
            {rs.high_severity_issues > 0 && <span className="font-medium text-red-600">{rs.high_severity_issues} high severity</span>}
          </div>
        )}
        {!rs.ready_for_approval && stage === "review" && (
          <p className="text-xs text-slate-400">Approval unlocks once the AI redline review is done and open issues are cleared.</p>
        )}
      </CardBody>
    </Card>
  );
}

function RequestDetailView({ id, onBack, canTriage }: { id: string; onBack: () => void; canTriage: boolean }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data: r, isLoading } = useQuery({ queryKey: ["intake-req", id], queryFn: () => intakeApi.get(id) });
  const { data: handoffs } = useQuery({ queryKey: ["intake-handoffs", id], queryFn: () => intakeApi.handoffs(id) });
  const { data: assignees } = useQuery({ queryKey: ["intake-assignees"], queryFn: intakeApi.assignees, enabled: canTriage });
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list, enabled: canTriage });
  const { data: contracts } = useQuery({ queryKey: ["contracts"], queryFn: contractsApi.list, enabled: canTriage });
  const { data: rec } = useQuery({ queryKey: ["intake-rec", id], queryFn: () => intakeApi.recommendation(id), enabled: canTriage });
  const { data: legs } = useQuery({ queryKey: ["intake-sla", id], queryFn: () => intakeApi.slaLegs(id) });
  const [busy, setBusy] = useState(false);
  const [reassignTo, setReassignTo] = useState("");
  const [snoozeUntil, setSnoozeUntil] = useState("");
  const [promoteKind, setPromoteKind] = useState<"project" | "contract">("project");
  const [promoteTarget, setPromoteTarget] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [showRec, setShowRec] = useState(false);
  const [showLegs, setShowLegs] = useState(false);
  const promoteOptions =
    promoteKind === "project"
      ? (projects ?? []).map((p) => ({ id: p.id, label: p.name }))
      : (contracts ?? []).map((c) => ({ id: c.id, label: c.title }));

  function refresh() {
    ["intake-list", "intake-mywork", "intake-mine"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    qc.invalidateQueries({ queryKey: ["intake-req", id] });
    qc.invalidateQueries({ queryKey: ["intake-handoffs", id] });
    qc.invalidateQueries({ queryKey: ["intake-rec", id] });
    qc.invalidateQueries({ queryKey: ["intake-sla", id] });
  }
  async function act(fn: () => Promise<unknown>, msg: string) {
    setBusy(true);
    try { await fn(); refresh(); notify(msg, "success"); }
    catch (e) { notify(e instanceof Error ? e.message : "Action failed", "error"); }
    finally { setBusy(false); }
  }

  if (isLoading || !r) return <CenterSpinner label="Loading the request…" />;

  const posture = r.sla_status as IntakeSlaPosture;
  const submitted = r.submitted_at ? new Date(r.submitted_at).getTime() : null;
  const elapsedH = submitted != null ? (Date.now() - submitted) / 3_600_000 : null;
  const open = r.status !== "closed" && r.status !== "approved";
  const ai = (r.ai_triage ?? {}) as Record<string, unknown>;
  const accent = posture === "overdue" ? "border-l-red-500" : posture === "at_risk" ? "border-l-amber-500" : "border-l-emerald-500";
  const postureText = posture === "overdue" ? "text-red-600" : posture === "at_risk" ? "text-amber-600" : "text-emerald-600";
  // A short header title — descriptions can be long (pasted text, folded
  // attachments). Take the first line, capped; the full text lives in Overview.
  const firstLine = (r.description || r.type_label).trim().split("\n")[0].trim();
  const shortTitle = firstLine.length > 140 ? `${firstLine.slice(0, 137).trimEnd()}…` : (firstLine || r.type_label);

  return (
    <div className="space-y-5">
      <button onClick={onBack}
        className="inline-flex items-center gap-1 font-mono text-[11px] uppercase tracking-[0.09em] text-slate-500 hover:text-slate-900">
        ← Back to inbox
      </button>

      {/* header + SLA banner */}
      <div className={cn("rounded-xl border border-slate-200 border-l-4 bg-slate-50/50 p-5", accent)}>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-slate-500">{r.ref}</span>
              <Badge tone={PRIORITY_TONE[r.priority] as never}>{r.priority}</Badge>
              <Badge tone="slate">{r.type_label}</Badge>
              <StatusPill r={r} />
              {r.handoff_holder && <Badge tone="violet">{titleCase(r.handoff_holder)} holds</Badge>}
            </div>
            <h2 className="mt-2 line-clamp-2 text-xl font-semibold leading-snug text-slate-900">{shortTitle}</h2>
            <p className="mt-1 text-xs text-slate-500">
              From <span className="text-slate-700">{r.requester_name ?? "—"}</span>
              {r.department ? ` · ${r.department}` : ""}
              {r.submitted_at ? ` · Submitted ${new Date(r.submitted_at).toLocaleString()}` : ""}
              {` · ${r.assigned_to_label ? `Assigned to ${r.assigned_to_label}` : "Unassigned"}`}
            </p>
          </div>
          {open && elapsedH != null && (
            <div className="shrink-0 sm:text-right">
              <p className={cn("font-mono text-[10px] uppercase tracking-wide", postureText)}>{POSTURE_LABEL[posture]}</p>
              <p className="text-2xl font-semibold tabular-nums text-slate-900">{fmtElapsedHours(elapsedH)}</p>
              <p className="text-[11px] text-slate-400">of {r.sla_hours} hrs window</p>
              <div className="mt-1.5 h-1.5 w-40 overflow-hidden rounded-full bg-slate-200 sm:ml-auto">
                <div className="h-full rounded-full" style={{ width: `${Math.min(100, r.sla_pct)}%`, background: slaBarColor(posture) }} />
              </div>
              <p className="mt-1 text-[11px] tabular-nums text-slate-400">{r.sla_pct}% elapsed</p>
            </div>
          )}
        </div>
      </div>

      {/* lifecycle — the intake spine before a contract exists, the contract
          lifecycle once it's been drafted */}
      {r.contract_id ? <ContractLifecycleTracker contractId={r.contract_id} /> : <WorkflowStepper steps={r.workflow} />}

      <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
        {/* ===== main: everything about this request ===== */}
        <div className="space-y-4">
          {/* overview — what it's for + the facts at a glance */}
          <Card>
            <CardHeader><CardTitle>Overview</CardTitle>
              {ai.category != null && <Badge tone="blue">{String(ai.category)}</Badge>}
              {ai.risk_flag != null && <Badge tone={String(ai.risk_flag) === "high" ? "red" : "amber"}>{String(ai.risk_flag)} risk</Badge>}
            </CardHeader>
            <CardBody className="space-y-4">
              <div>
                <p className="mb-1 font-mono text-[10px] uppercase tracking-wide text-slate-400">What they need</p>
                <p className="max-h-44 overflow-y-auto whitespace-pre-wrap text-sm text-slate-700">{r.description || r.type_label}</p>
              </div>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
                <Fact label="Request type">{r.type_label}</Fact>
                <Fact label="Priority"><Badge tone={PRIORITY_TONE[r.priority] as never}>{r.priority}</Badge></Fact>
                <Fact label="Counterparty">{requestCounterparty(r) ?? <span className="text-slate-400">—</span>}</Fact>
                {ai.complexity != null && <Fact label="Complexity">{titleCase(String(ai.complexity))}</Fact>}
                {ai.confidence != null && <Fact label="AI confidence">{Math.round(Number(ai.confidence) * 100)}%</Fact>}
                {r.contract_id && r.contract_title && (
                  <Fact label="Contract"><a className="text-brand-600 hover:underline" href={`/contracts/${r.contract_id}`}>{r.contract_title}</a></Fact>
                )}
              </dl>
              {(r.fired_rules as { summaries?: { name: string; actions: string[] }[] } | null)?.summaries?.length ? (
                <div className="border-t border-slate-100 pt-3">
                  <p className="mb-1 font-mono text-[10px] uppercase tracking-wide text-slate-400">Routing rules fired</p>
                  <ul className="space-y-1 text-xs text-slate-600">
                    {(r.fired_rules as { summaries: { name: string; actions: string[] }[] }).summaries.map((s, i) => (
                      <li key={i}>▸ <b>{s.name}</b> — {s.actions.join(", ")}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </CardBody>
          </Card>

          {/* counterparty / vendor + screening */}
          <Card>
            <CardHeader><CardTitle>Counterparty &amp; screening</CardTitle></CardHeader>
            <CardBody className="space-y-4">
              <PartiesPanel r={r} canTriage={canTriage} onRefreshed={refresh} />
              <ScreeningPanel r={r} canTriage={canTriage} onRefreshed={refresh} />
            </CardBody>
          </Card>

          {/* related / similar contracts */}
          <RelatedContractsCard r={r} contracts={contracts ?? []} />

          {/* attachments */}
          <Card><CardBody><DocumentsPanel requestId={r.id} /></CardBody></Card>

          {/* timeline */}
          <Card>
            <CardHeader><CardTitle>Timeline</CardTitle>
              <span className="ml-auto font-mono text-[10px] uppercase tracking-wide text-slate-400">Hand-off ledger</span></CardHeader>
            <CardBody>
              {(handoffs ?? []).length === 0 ? <p className="text-xs text-slate-400">Still in the intake queue — no hand-offs yet.</p> : (
                <ol className="space-y-2.5">
                  {(handoffs ?? []).map((h) => (
                    <li key={h.id} className="flex gap-2.5 text-xs">
                      <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-brand-400" />
                      <div>
                        <span className="capitalize text-slate-500">{h.from_holder ?? "queue"}</span>
                        <span className="text-slate-400"> → </span>
                        <span className="font-medium capitalize text-slate-900">{h.to_label ?? h.to_holder}</span>
                        {h.reason && <span className="text-slate-400"> · {h.reason}</span>}
                        {h.created_at && <div className="text-[10px] text-slate-400">{new Date(h.created_at).toLocaleString()}</div>}
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </CardBody>
          </Card>

          {/* SLA custody legs — collapsible detail */}
          {legs && legs.legs.length > 0 && (
            <Card>
              <button onClick={() => setShowLegs((v) => !v)} className="flex w-full items-center gap-2 px-4 py-3 text-left">
                <span className="text-sm font-medium text-slate-900">SLA custody legs</span>
                <span className={cn("font-mono text-[10px] uppercase tracking-wide", legs.breached ? "text-red-600" : "text-slate-400")}>{legs.breached ? "breached" : "running"}</span>
                <span className="ml-auto text-xs text-slate-400">{showLegs ? "Hide" : "Show"}</span>
              </button>
              {showLegs && <CardBody className="pt-0"><SlaLegsBar legs={legs.legs} breached={legs.breached} /></CardBody>}
            </Card>
          )}
        </div>

        {/* sidebar */}
        <div className="space-y-4">
          {/* what to do next */}
          {r.contract_id && canTriage && <ContractNextStep contractId={r.contract_id} onRefresh={refresh} />}

          {!r.contract_id && canTriage && open && (
            <Card>
              <CardHeader><CardTitle>Quick actions</CardTitle></CardHeader>
              <CardBody className="space-y-2">
                {r.stage !== "complete" && (
                  <Button className="w-full justify-start" size="sm" variant="outline" loading={busy}
                    onClick={() => act(() => intakeApi.update(id, { stage: nextStage(r) }), "Stage advanced")}>→ Advance stage</Button>
                )}
                <Button className="w-full justify-start" size="sm" variant="outline" loading={busy}
                  onClick={() => act(() => intakeApi.triage(id, { action: "escalate" }), "Escalated")}>⚡ Escalate</Button>
                <Button className="w-full justify-start" size="sm" variant="outline" loading={busy}
                  onClick={() => act(() => intakeApi.triage(id, { action: "manual_close" }), "Closed")}>✓ Mark complete</Button>
              </CardBody>
            </Card>
          )}

          {/* access the contract, or let the agent draft it */}
          {r.contract_id ? (
            <a href={`/contracts/${r.contract_id}`}
              className="flex items-center justify-between rounded-lg border border-brand-200 bg-brand-50 px-4 py-3 text-sm transition-colors hover:bg-brand-100">
              <span className="min-w-0 truncate text-slate-700">Contract: <strong className="text-slate-900">{r.contract_title ?? "linked"}</strong></span>
              <span className="shrink-0 font-semibold text-brand-700">Open →</span>
            </a>
          ) : canTriage && open && draftableDocType(r) ? (
            <div className="rounded-lg border border-dashed border-brand-300 bg-brand-50/50 px-4 py-3 text-sm">
              <p className="mb-2 text-slate-600">No contract yet. Let the agent draft the {draftableDocType(r)} to start the lifecycle, risk scoring and redlines.</p>
              <Button size="sm" loading={busy}
                onClick={() => act(() => intakeApi.draftContract(id), `${draftableDocType(r)} drafted — analysis running`)}>Draft the {draftableDocType(r)} →</Button>
            </div>
          ) : null}

          {/* people */}
          <Card>
            <CardHeader><CardTitle>People</CardTitle></CardHeader>
            <CardBody>
              <dl className="grid gap-3">
                <Fact label="Requester">{r.requester_name ?? "—"}</Fact>
                <Fact label="Department">{r.department ?? "—"}</Fact>
                <Fact label="Assignee">{r.assigned_to_label ?? "Unassigned"}</Fact>
                <Fact label="Holder"><span className="capitalize">{r.handoff_holder ?? "—"}</span></Fact>
                <Fact label="Submitted">{r.submitted_at ? new Date(r.submitted_at).toLocaleString() : "—"}</Fact>
              </dl>
            </CardBody>
          </Card>

          {/* agent recommendation — collapsible, kept out of the way */}
          {rec && (
            <Card className={cn("border", rec.can_auto_send ? "border-brand-300" : "border-amber-300")}>
              <button onClick={() => setShowRec((v) => !v)} className="flex w-full items-center gap-2 px-4 py-3 text-left">
                <span>🤖</span>
                <span className="text-sm font-medium text-slate-900">{titleCase(rec.agent_id.replace(/_/g, " "))}</span>
                <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-bold",
                  rec.confidence >= 0.75 ? "bg-green-100 text-green-700" : "bg-amber-100 text-amber-700")}>{rec.confidence.toFixed(2)}</span>
                <span className="ml-auto text-xs text-slate-400">{showRec ? "Hide" : "Review"}</span>
              </button>
              {showRec && (
                <CardBody className="space-y-3 pt-0">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                    {rec.suggested_action === "approve_and_send" ? "Suggested · approve & send" : "Flagged for human review"}
                  </p>
                  {editing ? (
                    <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={6} />
                  ) : (
                    <div className="whitespace-pre-wrap rounded-md border border-dashed border-slate-300 bg-slate-50 p-3 text-sm">{rec.drafted_response}</div>
                  )}
                  <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Reasoning</p>
                    <p className="text-xs text-slate-600">{rec.reasoning}</p></div>
                  {rec.concerns.length > 0 && (
                    <ul className="space-y-0.5 text-xs text-slate-600">{rec.concerns.map((c, i) => <li key={i}>⚠ {c}</li>)}</ul>
                  )}
                  {canTriage && rec.status === "pending" && (
                    <div className="flex flex-wrap gap-2">
                      {editing ? (
                        <>
                          <Button size="sm" loading={busy}
                            onClick={() => act(async () => { await intakeApi.triage(id, { action: "edited_approved", edited_response: draft }); setEditing(false); }, "Approved")}>Save &amp; approve</Button>
                          <Button size="sm" variant="outline" onClick={() => setEditing(false)}>Cancel</Button>
                        </>
                      ) : (
                        <>
                          <Button size="sm" loading={busy} onClick={() => act(() => intakeApi.triage(id, { action: "approved" }), "Approved")}>Approve</Button>
                          <Button size="sm" variant="outline" onClick={() => { setDraft(rec.drafted_response); setEditing(true); }}>Edit</Button>
                          <Button size="sm" variant="outline" loading={busy} onClick={() => act(() => intakeApi.triage(id, { action: "rejected" }), "Rejected")}>Reject</Button>
                        </>
                      )}
                    </div>
                  )}
                </CardBody>
              )}
            </Card>
          )}

          {canTriage && open && (
            <Card>
              <CardHeader><CardTitle>Route &amp; organize</CardTitle></CardHeader>
              <CardBody className="space-y-3">
                <div className="flex items-end gap-2">
                  <Field label="Reassign to" className="flex-1">
                    <Select value={reassignTo} onChange={(e) => setReassignTo(e.target.value)}>
                      <option value="">Select…</option>
                      {(assignees ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                    </Select>
                  </Field>
                  <Button size="sm" variant="outline" loading={busy} disabled={!reassignTo}
                    onClick={() => act(async () => { await intakeApi.triage(id, { action: "reassigned", assignee_user_id: reassignTo }); setReassignTo(""); }, "Reassigned")}>Go</Button>
                </div>
                <div className="flex items-end gap-2">
                  <Field label="Snooze until" className="flex-1">
                    <Input type="date" value={snoozeUntil} onChange={(e) => setSnoozeUntil(e.target.value)} />
                  </Field>
                  <Button size="sm" variant="outline" loading={busy} disabled={!snoozeUntil}
                    onClick={() => act(async () => { await intakeApi.triage(id, { action: "snoozed", snoozed_until: snoozeUntil }); setSnoozeUntil(""); }, "Snoozed")}>Go</Button>
                </div>
                <div className="flex items-end gap-2">
                  <Field label="Promote to" className="flex-1">
                    <div className="flex gap-1">
                      <Select value={promoteKind} onChange={(e) => { setPromoteKind(e.target.value as "project" | "contract"); setPromoteTarget(""); }}>
                        <option value="project">Project</option><option value="contract">Contract</option>
                      </Select>
                      <Select value={promoteTarget} onChange={(e) => setPromoteTarget(e.target.value)}>
                        <option value="">Select…</option>
                        {promoteOptions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                      </Select>
                    </div>
                  </Field>
                  <Button size="sm" variant="outline" loading={busy} disabled={!promoteTarget}
                    onClick={() => act(async () => { await intakeApi.promote(id, promoteKind, promoteTarget); setPromoteTarget(""); }, `Promoted to ${promoteKind}`)}>Go</Button>
                </div>
                {(r.project_id || r.contract_id) && (
                  <p className="text-xs text-slate-500">Linked to {[r.project_id && "a project", r.contract_id && "a contract"].filter(Boolean).join(" and ")}.</p>
                )}
              </CardBody>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function nextStage(r: IntakeRequest): string {
  const idx = r.workflow.findIndex((s) => s.stage === r.stage);
  return r.workflow[Math.min(idx + 1, r.workflow.length - 1)]?.stage ?? "complete";
}


// ---- screening + documents panels (gap-fill) -------------------------------

const ROLE_LABEL: Record<string, string> = {
  counterparty: "Counterparty", adverse: "Adverse", related: "Related", our_side: "Our side",
};

function PartiesPanel({ r, canTriage, onRefreshed }: { r: IntakeRequest; canTriage: boolean; onRefreshed: () => void }) {
  const { notify } = useToast();
  const parties = (r.parties ?? []) as { name: string; role: string; is_person?: boolean }[];
  const [name, setName] = useState("");
  const [role, setRole] = useState("adverse");
  const [busy, setBusy] = useState(false);

  async function save(next: { name: string; role: string }[]) {
    setBusy(true);
    try { await intakeApi.setParties(r.id, next); onRefreshed(); notify("Parties updated — re-screened", "success"); }
    catch (e) { notify(e instanceof Error ? e.message : "Update failed", "error"); }
    finally { setBusy(false); }
  }

  return (
    <div>
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Parties</p>
      {parties.length === 0 && <p className="text-xs text-slate-400">No parties captured.</p>}
      <ul className="space-y-1 text-xs">
        {parties.map((p, i) => (
          <li key={i} className="flex items-center gap-2">
            <Badge tone={p.role === "adverse" ? "red" : p.role === "counterparty" ? "blue" : "slate"}>{ROLE_LABEL[p.role] ?? p.role}</Badge>
            <span className="font-medium text-slate-700">{p.name}</span>
            {canTriage && (
              <button className="ml-auto text-slate-400 hover:text-red-600" disabled={busy}
                onClick={() => save(parties.filter((_, j) => j !== i))}>remove</button>
            )}
          </li>
        ))}
      </ul>
      {canTriage && (
        <div className="mt-2 flex gap-1.5">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Party name…" className="h-8 text-xs" />
          <Select value={role} onChange={(e) => setRole(e.target.value)} className="h-8 w-28 text-xs">
            {Object.entries(ROLE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </Select>
          <Button size="sm" loading={busy} disabled={!name.trim()}
            onClick={() => { save([...parties, { name: name.trim(), role }]); setName(""); }}>Add</Button>
        </div>
      )}
    </div>
  );
}

function ScreeningPanel({ r, canTriage, onRefreshed }: { r: IntakeRequest; canTriage: boolean; onRefreshed: () => void }) {
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);
  const sc = (r.screening ?? null) as {
    status?: string; note?: string; counterparty?: string;
    sanctions?: { status?: string; party?: string; matches?: { name?: string; programs?: string | null }[]; note?: string };
    conflicts?: { kind: string; severity?: string; party?: string; role_here?: string; via?: string; ref?: string; title?: string }[];
    relationship?: { note?: string };
  } | null;
  const sanTone = (st?: string) => (st === "hit" ? "red" : st === "clear" ? "green" : "amber");
  const conflicts = sc?.conflicts ?? [];
  const high = conflicts.filter((c) => c.severity === "high").length;
  return (
    <div>
      <div className="mb-1 flex items-center gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Screening</p>
        {canTriage && (
          <button className="text-xs text-brand-600 hover:underline disabled:opacity-50" disabled={busy}
            onClick={async () => {
              setBusy(true);
              try { await intakeApi.screen(r.id); onRefreshed(); notify("Screening re-run", "success"); }
              catch (e) { notify(e instanceof Error ? e.message : "Screening failed", "error"); }
              finally { setBusy(false); }
            }}>{busy ? "Running…" : "Re-run"}</button>
        )}
      </div>
      {!sc || sc.status === "skipped" || sc.status === "error" ? (
        <p className="text-xs text-slate-400">{sc?.note ?? "Not screened — no parties captured."}</p>
      ) : (
        <div className="space-y-2 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-slate-500">Sanctions:</span>
            <Badge tone={sanTone(sc.sanctions?.status) as never}>
              {sc.sanctions?.status === "hit" ? "HIT" : sc.sanctions?.status === "clear" ? "Clear" : "Unavailable"}
            </Badge>
            {sc.sanctions?.party && <span className="text-slate-500">{sc.sanctions.party}</span>}
            {(sc.sanctions?.matches ?? []).slice(0, 2).map((m, i) => (
              <span key={i} className="text-red-600">{m.name}{m.programs ? ` · ${m.programs}` : ""}</span>
            ))}
          </div>
          <div>
            <div className="mb-1 flex items-center gap-2">
              <span className="text-slate-500">Conflicts:</span>
              {conflicts.length === 0 ? <span className="text-slate-400">none found</span>
                : high > 0 ? <Badge tone="red">{high} high</Badge> : <Badge tone="amber">{conflicts.length} to review</Badge>}
            </div>
            <ul className="space-y-1">
              {conflicts.slice(0, 6).map((c, i) => (
                <li key={i} className="flex items-start gap-1.5">
                  <Badge tone={c.severity === "high" ? "red" : "amber"}>{c.severity === "high" ? "HIGH" : "REVIEW"}</Badge>
                  <span className="text-slate-600">
                    <span className="font-medium">{c.party}</span>
                    {c.role_here ? ` (${ROLE_LABEL[c.role_here] ?? c.role_here})` : ""} — {c.via}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          {sc.relationship?.note && <p className="text-slate-600"><span className="text-slate-500">Relationship: </span>{sc.relationship.note}</p>}
        </div>
      )}
    </div>
  );
}

function DocumentsPanel({ requestId }: { requestId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data: docs } = useQuery({ queryKey: ["intake-docs", requestId], queryFn: () => intakeApi.documents(requestId) });
  const [busy, setBusy] = useState(false);
  const [openDoc, setOpenDoc] = useState<string | null>(null);

  async function upload(file: File) {
    if (file.size > 3 * 1024 * 1024) { notify("Max 3 MB", "error"); return; }
    setBusy(true);
    try {
      const buf = await file.arrayBuffer();
      let bin = ""; const bytes = new Uint8Array(buf);
      for (let i = 0; i < bytes.length; i += 0x8000)
        bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      await intakeApi.uploadDocument(requestId, {
        filename: file.name, mime_type: file.type || "application/octet-stream",
        content_b64: btoa(bin),
      });
      qc.invalidateQueries({ queryKey: ["intake-docs", requestId] });
      qc.invalidateQueries({ queryKey: ["intake-req", requestId] });
      notify("Attached — text extracted for triage", "success");
    } catch (e) { notify(e instanceof Error ? e.message : "Upload failed", "error"); }
    finally { setBusy(false); }
  }

  return (
    <div>
      <div className="mb-1 flex items-center gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Attachments</p>
        <label className="cursor-pointer text-xs text-brand-600 hover:underline">
          {busy ? "Uploading…" : "Attach"}
          <input type="file" className="hidden" accept=".txt,.docx,.pdf,.doc" disabled={busy}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
        </label>
      </div>
      {(docs ?? []).length === 0 ? (
        <p className="text-xs text-slate-400">No documents attached.</p>
      ) : (
        <ul className="space-y-2 text-xs text-slate-600">
          {(docs ?? []).map((d) => (
            <li key={d.id}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{d.filename}</span>
                <span className="text-slate-400">{Math.round(d.size_bytes / 1024)} KB</span>
                {d.extracted_chars > 0
                  ? <Badge tone="green">text extracted</Badge>
                  : <Badge tone="slate">no text</Badge>}
                {d.extracted_text && (
                  <button className="text-brand-600 hover:underline"
                    onClick={() => setOpenDoc((v) => (v === d.id ? null : d.id))}>
                    {openDoc === d.id ? "Hide text" : "View text"}
                  </button>
                )}
              </div>
              {openDoc === d.id && d.extracted_text && (
                <pre className="mt-1.5 max-h-56 overflow-y-auto whitespace-pre-wrap rounded-md border border-slate-200 bg-slate-50 p-2 font-sans text-[11px] leading-relaxed text-slate-600">
                  {d.extracted_text}
                </pre>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


// ---- consolidated wrappers: My Work + one Operations door -------------------

function MyWorkView({ isStaff, onOpen }: { isStaff: boolean; onOpen: (id: string) => void }) {
  if (!isStaff) return <MyRequestsTab onOpen={onOpen} />;
  return (
    <div className="space-y-6">
      <MyWorkTab onOpen={onOpen} />
      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Filed by me</p>
        <MyRequestsTab onOpen={onOpen} />
      </div>
    </div>
  );
}

function OperationsView({ isAdmin }: { isAdmin: boolean }) {
  // SLA and Agents are now first-class tabs; Smart Routing lives under Approvals → Intake routing.
  const items = [
    { id: "pool", label: "Pool Ops" },
    ...(isAdmin ? [{ id: "teams", label: "Teams" }, { id: "types", label: "Request Types" }] : []),
  ];
  const [sub, setSub] = useState("pool");
  return (
    <div className="space-y-4">
      <div className="flex gap-1 border-b border-slate-200">
        {items.map((it) => (
          <button key={it.id} onClick={() => setSub(it.id)}
            className={cn("-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors",
              sub === it.id ? "border-brand-600 text-slate-900" : "border-transparent text-slate-500 hover:text-slate-900")}>
            {it.label}
          </button>
        ))}
      </div>
      {sub === "pool" && <PoolOpsTab />}
      {sub === "teams" && isAdmin && <TeamsTab isAdmin={isAdmin} />}
      {sub === "types" && isAdmin && <RequestTypesTab />}
    </div>
  );
}
