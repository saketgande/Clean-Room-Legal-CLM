"use client";

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Bot, Check, DoorOpen, FileText, Paperclip, PenLine, Plus, RotateCw, Search, ShieldCheck, Trash2, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, CenterSpinner, EmptyState,
  ErrorState, Field, Input, Modal, Select, StatCard, Table, TD, TH, THead,
  TR, Textarea,
} from "@/components/ui";
import { intakeApi, projectsApi, contractsApi, approvalsApi, aiApi, playbooksApi, flowsApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { Markdown } from "@/components/markdown";
import { cn, titleCase } from "@/lib/utils";
import {
  can, POSTURE_LABEL, POSTURE_TONE, PRIORITY_TONE, slaBarColor,
  sortBySla, STATUS_LABEL, STATUS_TONE,
} from "@/lib/intake";
import type {
  ContractResponse, FlowRun, FlowRunStep, FlowSuggestion, IntakeFieldSpec, IntakeRequest, IntakeRequestType, IntakeSlaPosture, IntakeStatus, LitigationAssessment,
} from "@/lib/types";
import { SlaDashboardTab, SlaLegsBar, TeamsTab } from "./_phase1";
import { CopilotChat, PoolOpsTab, SelfServiceTab } from "./_phase2";
import { RulesTab } from "../approvals/_rules-builder";
import { WorkflowPanel } from "./_workflow-panel";

// Keep the queue live: React Query re-fetches on this cadence (paused while the
// tab is backgrounded), so SLA postures advance and triage/escalation changes
// surface without a manual reload. The backend recomputes SLA from `now` on
// every serialize, so each poll reflects real elapsed time.
const LIVE_POLL = { refetchInterval: 15_000 } as const;

// A shared wall-clock that re-renders its consumers every `ms`. Used to make
// the SLA columns tick in real time instead of freezing between polls.
function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

// Elapsed with a live seconds field, so the SLA clock visibly runs.
// ponytail: derives from submitted_at, so it ignores paused time between polls;
// each 15s refetch re-syncs to the backend's pause-accurate posture.
function fmtDurLive(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}h ${pad(m)}m ${pad(sec)}s` : `${m}m ${pad(sec)}s`;
}

// A truthful "it's live" cue: a pulsing dot + how long ago the queue last
// refreshed, ticking every second. Owns its own tick so only this badge
// re-renders, not the whole page.
function LivePulse({ updatedAt }: { updatedAt: number }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const secs = updatedAt ? Math.max(0, Math.round((Date.now() - updatedAt) / 1000)) : 0;
  const ago = secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m`;
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.06em] text-success"
      title="The queue refreshes automatically"
    >
      <span className="relative flex h-1.5 w-1.5">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-75" />
        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success" />
      </span>
      Live · updated {ago} ago
    </span>
  );
}

export default function IntakePage() {
  const { user } = useAuth();
  const isStaff = can(user, "intake:read");
  const isAdmin = can(user, "admin_panel:access");

  // Reference-style tab set — Work · File · Insights, divider-grouped. Inbox and
  // SLA are first-class tabs (not nested view-toggles) so every lens is one
  // click away. Requesters get just the filing three.
  const groups = useMemo<{ id: string; label: string }[][]>(() => {
    if (!isStaff)
      return [[
        { id: "new", label: "New Request" },
        { id: "mywork", label: "My Work" },
      ]];
    return [
      [
        { id: "queue", label: "Inbox" },
        { id: "mywork", label: "My Work" },
      ],
      [
        { id: "new", label: "New Request" },
      ],
      [
        { id: "workflows", label: "Workflows" },
        { id: "sla", label: "SLA" },
        { id: "ops", label: "Operations" },
      ],
    ];
  }, [isStaff]);

  const [section, setSection] = useState(isStaff ? "queue" : "new");
  const [detailId, setDetailId] = useState<string | null>(null);

  const { data: listData, dataUpdatedAt } = useQuery({
    queryKey: ["intake-list"], queryFn: () => intakeApi.list(), enabled: isStaff, ...LIVE_POLL,
  });
  const { data: myWork } = useQuery({
    queryKey: ["intake-mywork"], queryFn: intakeApi.myWork, enabled: isStaff, ...LIVE_POLL,
  });
  // Count-pills on the tabs — the reference's at-a-glance queue signal.
  const openCount = (listData ?? []).filter((r) => r.status !== "closed" && r.status !== "approved").length;
  const onMe = myWork
    ? myWork.awaiting_review.length + myWork.my_tickets.length + myWork.my_tasks.length
    : 0;
  const counts: Record<string, number> = { queue: openCount, mywork: onMe };

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-slate-400">Operations · Legal</p>
          <h1 className="mt-1 text-[22px] font-semibold leading-tight tracking-[-0.02em] text-slate-900">Legal Intake</h1>
          <p className="mt-0.5 text-[13px] text-slate-500">Triage, draft, and resolve every legal request from one queue.</p>
        </div>
        <div className="flex flex-shrink-0 flex-wrap items-center gap-2.5">
          {isStaff && openCount > 0 && (
            <span className="inline-flex items-center gap-1 rounded-md bg-brand-50 px-2.5 py-1 text-[11px] font-medium uppercase tracking-[0.06em] text-brand-700">
              ◆ {openCount} open
            </span>
          )}
          {isStaff && <LivePulse updatedAt={dataUpdatedAt} />}
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
                  "-mb-px inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-[11px] font-medium uppercase tracking-[0.06em] transition-colors",
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
          {section === "queue" && <InboxCockpit onOpen={setDetailId} />}
          {section === "mywork" && <MyWorkView isStaff={isStaff} onOpen={setDetailId} />}
          {section === "new" && <NewRequestTab onFiled={setDetailId} />}
          {section === "sla" && isStaff && <SlaDashboardTab isAdmin={isAdmin} />}
          {section === "workflows" && isStaff && <WorkflowsBuilderTab />}
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
  const now = useNow(1000);
  if (r.status === "closed" || r.status === "approved")
    return <span className="text-xs text-slate-400">—</span>;
  const posture = r.sla_status as IntakeSlaPosture;
  const submitted = r.submitted_at ? new Date(r.submitted_at).getTime() : null;
  const elapsedMs = submitted != null ? now - submitted : null;
  const livePct = elapsedMs != null ? Math.min(100, (elapsedMs / (r.sla_hours * 3_600_000)) * 100) : r.sla_pct;
  return (
    <div className="flex flex-col gap-1">
      {elapsedMs != null && (
        <span className="text-xs tabular-nums text-slate-600">
          {fmtDurLive(elapsedMs)} <span className="text-slate-400">of {r.sla_hours}h</span>
        </span>
      )}
      <div className="flex items-center gap-2">
        <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-200">
          <div
            className="h-full rounded-full transition-[width] duration-1000 ease-linear"
            style={{ width: `${livePct}%`, background: slaBarColor(posture) }}
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
        showRequester && r.sla_status === "overdue" && "bg-danger-subtle",
        showRequester && r.sla_status === "at_risk" && "bg-warning-subtle",
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
      <TD>
        <span className="inline-flex items-center gap-2">
          <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full",
            r.sla_status === "overdue" ? "bg-danger"
              : r.sla_status === "at_risk" ? "bg-warning" : "bg-success")}
            title={`SLA ${r.sla_status}`} />
          <span className="font-mono text-xs text-slate-500">{r.ref}</span>
        </span>
      </TD>
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


// One landing for filing + self-serve: a segmented switch between the
// structured form (reference "route to agent" styling), the copilot chat, and
// the self-service KB. Self-Service is no longer its own tab — it lives here so
// a requester tries to deflect before filing.
const DEPARTMENTS = ["Product", "Engineering", "Sales", "HR", "Finance", "Procurement", "Marketing", "Operations", "Legal", "Executive"];
const URGENCIES = ["Standard", "Priority", "Urgent — deadline this week", "Emergency — deal blocker"];
// Built-in categories that don't map to a configured type — they file as a
// general request (the configured types render first, with dynamic fields).
const BUILTIN_EXTRAS = ["IP Question", "Vendor Due Diligence", "Contract Question", "Legal Question — General", "Other"];

function urgencyToPriority(u: string): string {
  if (u.startsWith("Emergency")) return "Critical";
  if (u.startsWith("Urgent") || u === "Priority") return "High";
  return "Medium";
}

function fileToB64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => { const s = String(r.result); const i = s.indexOf(","); resolve(i >= 0 ? s.slice(i + 1) : s); };
    r.onerror = () => reject(new Error("Could not read file"));
    r.readAsDataURL(file);
  });
}

function NewRequestTab({ onFiled }: { onFiled: (id: string) => void }) {
  const { data: types } = useQuery({ queryKey: ["intake-types"], queryFn: () => intakeApi.listTypes() });
  const [mode, setMode] = useState<"form" | "chat" | "self">("form");
  const [seedDesc, setSeedDesc] = useState("");

  const MODES: { id: "form" | "chat" | "self"; label: string; hint: string }[] = [
    { id: "form", label: "Structured form", hint: "Fast · route to agent" },
    { id: "chat", label: "Copilot chat", hint: "Describe it in a conversation" },
    { id: "self", label: "Self-service", hint: "Resolve it without a ticket" },
  ];

  return (
    <div className="space-y-5">
      {/* segmented switch — merges New Request + Self-Service on one page */}
      <div className="mx-auto grid max-w-3xl grid-cols-3 gap-1.5 rounded-xl border border-slate-200 bg-slate-100 p-1.5">
        {MODES.map((m) => {
          const active = mode === m.id;
          return (
            <button key={m.id} onClick={() => setMode(m.id)}
              className={cn("rounded-lg px-3 py-2 text-center transition-colors",
                active ? "bg-brand-50 shadow-sm ring-1 ring-brand-200" : "hover:bg-slate-200/60")}>
              <div className={cn("text-[13px] font-semibold", active ? "text-brand-700" : "text-slate-600")}>{m.label}</div>
              <div className="mt-0.5 text-[10.5px] text-slate-400">{m.hint}</div>
            </button>
          );
        })}
      </div>

      {mode === "form" && <div className="mx-auto max-w-3xl"><RequestForm types={types ?? []} onFiled={onFiled} initialDesc={seedDesc} /></div>}
      {mode === "chat" && <div className="mx-auto max-w-3xl"><CopilotChat onFiled={onFiled} /></div>}
      {mode === "self" && <SelfServiceTab onFileTopic={(t) => { setSeedDesc(`Re: ${t}\n\n`); setMode("form"); }} />}
    </div>
  );
}

function RequestForm({ types, onFiled, initialDesc }: { types: IntakeRequestType[]; onFiled: (id: string) => void; initialDesc: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { user } = useAuth();
  const [name, setName] = useState(user?.full_name ?? "");
  const [department, setDepartment] = useState("Product");
  const [urgency, setUrgency] = useState("Standard");
  const [typeSel, setTypeSel] = useState<string>("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [description, setDescription] = useState(initialDesc);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);

  // A configured request-type from the DB carries dynamic fields + a stage
  // ladder; the built-in extras carry only their label and file as general.
  const gridItems = [
    ...types.map((t) => ({ key: t.id, label: t.name, type: t as IntakeRequestType | null })),
    ...BUILTIN_EXTRAS.map((label) => ({ key: label, label, type: null as IntakeRequestType | null })),
  ];
  const selected = gridItems.find((g) => g.key === typeSel) ?? null;
  const selType = selected?.type ?? null;

  const missingRequired = (selType?.fields ?? []).filter((f) => f.required && !String(values[f.key] ?? "").trim());
  const preview = derivePreview(description, selType ?? undefined);
  const canSubmit = !!name.trim() && (description.trim().length >= 10 || !!file) && missingRequired.length === 0;

  async function submit() {
    setBusy(true);
    try {
      const r = await intakeApi.create({
        type_label: selType ? selType.name + " Request" : (selected ? selected.label : "General request"),
        request_type_id: selType?.id ?? null,
        priority: urgencyToPriority(urgency),
        department: department || null,
        requester_name: name.trim() || null,
        description,
        field_values: Object.keys(values).length ? values : null,
      });
      if (file) {
        const content_b64 = await fileToB64(file);
        await intakeApi.uploadDocument(r.id, { filename: file.name, mime_type: file.type || "application/octet-stream", content_b64 });
        await intakeApi.ingestAttachment(r.id);
      }
      qc.invalidateQueries({ queryKey: ["intake-mine"] });
      qc.invalidateQueries({ queryKey: ["intake-list"] });
      notify(`Filed ${r.ref} — routed for triage`, "success");
      onFiled(r.id);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Submit failed", "error");
    } finally { setBusy(false); }
  }

  return (
    <Card>
      <CardBody className="space-y-5">
        <Field label="Your name *">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Jane Smith" />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Department *">
            <Select value={department} onChange={(e) => setDepartment(e.target.value)}>
              {DEPARTMENTS.map((d) => <option key={d}>{d}</option>)}
            </Select>
          </Field>
          <Field label="Urgency">
            <Select value={urgency} onChange={(e) => setUrgency(e.target.value)}>
              {URGENCIES.map((u) => <option key={u}>{u}</option>)}
            </Select>
          </Field>
        </div>

        <Field label="Request type *">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {gridItems.map((g) => {
              const active = typeSel === g.key;
              return (
                <button key={g.key} type="button"
                  onClick={() => { setTypeSel(active ? "" : g.key); setValues({}); }}
                  className={cn("rounded-lg border px-3 py-2.5 text-center text-[12px] font-medium transition-colors",
                    active ? "border-brand-400 bg-brand-50 text-brand-700 ring-1 ring-brand-200"
                      : "border-slate-200 bg-slate-100 text-slate-600 hover:border-slate-300 hover:bg-slate-200/50")}>
                  {g.type && <span className="mr-1 text-brand-500">▣</span>}{g.label}
                </button>
              );
            })}
          </div>
          {selType && (selType.stages?.length ?? 0) > 0 && (
            <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-[9.5px] uppercase tracking-[0.08em] text-slate-400">Workflow</span>
              {(selType.stages ?? []).map((s, i) => (
                <span key={i} className="rounded bg-slate-100 px-2 py-0.5 font-mono text-[10px] text-slate-500">{i + 1}. {titleCase(s)}</span>
              ))}
            </div>
          )}
        </Field>

        {(selType?.fields ?? []).map((f) => (
          <DynamicField key={f.key} f={f} value={values[f.key] ?? ""}
            onChange={(v) => setValues((s) => ({ ...s, [f.key]: v }))} />
        ))}

        <Field label="Describe your request *" hint="Be specific — regex + Claude triage and agent routing use this.">
          <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={5}
            placeholder="E.g. Mutual NDA for discussions with Acme Corp — 2-year term, Delaware law." />
        </Field>

        <Field label="Attach a document" hint="Word (.docx), text (.txt), or PDF — e.g. an NDA / MSA to review. The agent reads the extracted text. (Scanned / image-only PDFs can't be read — paste the text instead.)">
          <div className="flex items-center gap-3">
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-dashed border-slate-300 bg-slate-100 px-3.5 py-2 text-[11px] font-medium uppercase tracking-[0.06em] text-brand-700 hover:border-brand-400">
              <Paperclip className="h-3.5 w-3.5" /> Choose file
              <input type="file" accept=".docx,.txt,.text,.md,.pdf,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="hidden" />
            </label>
            <span className="text-[11px] text-slate-400">Max 3 MB</span>
          </div>
          {file && (
            <div className="mt-2 flex items-center justify-between gap-2 rounded-md border-l-2 border-l-success bg-success-subtle/40 px-3 py-2 text-[12px]">
              <span className="truncate text-slate-700">📄 {file.name} <span className="text-slate-400">· {(file.size / 1024).toFixed(0)} KB</span></span>
              <button type="button" onClick={() => setFile(null)} className="shrink-0 text-slate-400 hover:text-danger">✕</button>
            </div>
          )}
        </Field>

        {preview && (
          <div className="rounded-md border border-brand-200 bg-brand-50 px-3 py-2 text-xs text-brand-700">
            Likely routing: <strong>{preview}</strong>
          </div>
        )}

        <div className="flex items-center gap-3">
          <Button onClick={submit} loading={busy} disabled={!canSubmit}>→ Submit · Route to agent</Button>
          {missingRequired.length > 0 && (
            <span className="text-xs text-warning">Required: {missingRequired.map((f) => f.label).join(", ")}</span>
          )}
        </div>

        <div className="rounded-md border-l-2 border-l-brand-600 bg-slate-100/50 px-3.5 py-2.5 text-[11.5px] leading-relaxed text-slate-500">
          <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.08em] text-brand-600">Flow</span>
          <p className="mt-0.5">On submit: the request is saved, screened, and routed → it lands in the intake queue open for a reviewer to pick up and start a workflow.</p>
        </div>
      </CardBody>
    </Card>
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
    { id: "open", label: "Open", match: (r) => r.status === "open" },
    { id: "escalated", label: "Escalated", match: (r) => r.status === "escalated" },
    { id: "approved", label: "Approved", match: (r) => r.status === "approved" },
  ];
}

// ---- Operations Cockpit (Inbox landing) -----------------------------------
const INBOX_PAGE = 60;

// Inbox — the reference "Legal Mission Control" list: a KPI strip, filter chips,
// and ONE dense full-width table (ID · Requester · Type · Description · Priority
// · SLA · Status · Assignee). Click a row → the dispatch-desk detail. No side
// cards, no rail — the queue is the page, so a filed ticket is easy to find.
function InboxCockpit({ onOpen }: { onOpen: (id: string) => void }) {
  const { user } = useAuth();
  const now = useNow(1000); // tick the SLA clocks every second
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-list"], queryFn: () => intakeApi.list(), ...LIVE_POLL });
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [visibleCount, setVisibleCount] = useState(INBOX_PAGE);
  useEffect(() => setVisibleCount(INBOX_PAGE), [filter, search]);

  const rows = useMemo(() => data ?? [], [data]);
  const open = rows.filter((r) => r.status !== "closed" && r.status !== "approved");
  const kpi = {
    open: rows.filter((r) => r.status === "open").length,
    unassigned: rows.filter((r) => r.status !== "closed" && !r.assigned_to_user_id).length,
    escalated: rows.filter((r) => r.status === "escalated").length,
    breached: rows.filter((r) => r.sla_status === "overdue" && r.status !== "closed").length,
    atRisk: rows.filter((r) => r.sla_status === "at_risk" && r.status !== "closed").length,
    approved: rows.length ? Math.round((rows.filter((r) => r.status === "approved").length / rows.length) * 100) : 0,
  };

  const FILTERS = buildFilters(user?.id ?? null);
  const q = search.trim().toLowerCase();
  const activeMatch = (FILTERS.find((f) => f.id === filter) ?? FILTERS[0]).match;
  // Three-tier queue order so the list reads top-to-bottom as work moves through
  // it: (0) open on top — a just-filed ticket lands where it's seen; (1) active
  // work in the middle (escalated), most SLA pressure first; (2) finished tickets
  // sink to the bottom, most-recently-closed first.
  const created = (r: IntakeRequest) => r.created_at ?? r.submitted_at ?? "";
  const doneAt = (r: IntakeRequest) => r.closed_at ?? created(r);
  const tier = (r: IntakeRequest) =>
    r.status === "open" ? 0 : r.status === "approved" || r.status === "closed" ? 2 : 1;
  const shown = [...rows.filter(activeMatch).filter((r) =>
    !q || `${r.ref} ${r.requester_name ?? ""} ${r.type_label} ${r.description ?? ""}`.toLowerCase().includes(q))]
    .sort((a, b) => {
      const ta = tier(a), tb = tier(b);
      if (ta !== tb) return ta - tb;
      if (ta === 0) return created(b).localeCompare(created(a)); // open: newest first
      if (ta === 2) return doneAt(b).localeCompare(doneAt(a));   // done: most recently closed first
      return sortBySla(a, b);                                     // active: SLA pressure
    });
  const page = shown.slice(0, visibleCount);

  if (isLoading) return <CenterSpinner label="Loading the queue…" />;
  if (error) return <ErrorState error={error} />;

  return (
    <div className="space-y-4">
      {/* KPI strip */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <StatCard label="Open" value={String(kpi.open)} hint={`${kpi.unassigned} unassigned`} tone="blue" />
        <StatCard label="Escalated" value={String(kpi.escalated)} hint="needs senior review" tone="slate" />
        <StatCard label="SLA breached" value={String(kpi.breached)} hint={`${kpi.atRisk} at risk`} tone="red" />
        <StatCard label="Approved" value={`${kpi.approved}%`} hint="of total" tone="green" />
        <StatCard label="In queue" value={String(open.length)} hint="active total" tone="slate" />
      </div>

      {/* filter chips + search */}
      <div className="flex flex-wrap items-center gap-1.5">
        {FILTERS.map((f) => {
          const n = rows.filter(f.match).length;
          const active = filter === f.id;
          return (
            <button key={f.id} onClick={() => setFilter(f.id)}
              className={cn("inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 font-mono text-[10.5px] font-medium uppercase tracking-[0.06em] transition-colors",
                active ? "border-brand-200 bg-brand-50 text-brand-700" : "border-slate-200 bg-slate-100 text-slate-500 hover:text-slate-800")}>
              {f.label}<span className="tabular-nums text-slate-400">{n}</span>
            </button>
          );
        })}
        <div className="relative ml-auto w-56">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search id / requester / text…" className="h-8 pl-8 text-[13px]" />
        </div>
      </div>

      {/* the queue table — the whole page */}
      <Card className="overflow-hidden">
        {shown.length === 0 ? (
          <CardBody><EmptyState title="Nothing here" description={q ? `No requests match “${search.trim()}”.` : "No requests match this filter."} /></CardBody>
        ) : (
          <div className="overflow-x-auto"><div className="min-w-[920px]">
            <Table>
              <THead><TR>
                <TH>ID</TH><TH>Requester</TH><TH>Type</TH><TH>Description</TH><TH>Priority</TH><TH>SLA</TH><TH>Status</TH><TH>Assignee</TH>
              </TR></THead>
              <tbody>{page.map((r) => {
                const dotCls = r.sla_status === "overdue" ? "bg-danger" : r.sla_status === "at_risk" ? "bg-warning" : "bg-success";
                const desc = (r.description ?? "").split("\n")[0].trim() || r.type_label;
                const tint = r.sla_status === "overdue" ? "bg-danger-subtle/40" : r.sla_status === "at_risk" ? "bg-warning-subtle/40" : "";
                return (
                  <TR key={r.id} className={cn("cursor-pointer", tint)} onClick={() => onOpen(r.id)}>
                    <TD className="whitespace-nowrap">
                      <span className="inline-flex items-center gap-2">
                        <span className={cn("h-1.5 w-1.5 rounded-full", dotCls)} />
                        <span className="font-mono text-xs font-semibold text-slate-500">{r.ref}</span>
                      </span>
                    </TD>
                    <TD className="whitespace-nowrap">
                      <div className="font-medium text-slate-800">{r.requester_name ?? "—"}</div>
                      <div className="text-[10px] text-slate-400">{r.department ?? ""}</div>
                    </TD>
                    <TD className="whitespace-nowrap"><Badge tone="violet">{r.type_label}</Badge></TD>
                    <TD className="max-w-[13rem] truncate text-slate-600" title={desc}>
                      {desc}
                    </TD>
                    <TD><Badge tone={PRIORITY_TONE[r.priority] as never}>{r.priority}</Badge></TD>
                    <TD className="whitespace-nowrap">{(() => {
                      const submitted = r.submitted_at ? new Date(r.submitted_at).getTime() : null;
                      if (r.status === "closed" || r.status === "approved" || submitted == null) return <span className="text-xs text-slate-400">—</span>;
                      const elapsedMs = now - submitted;
                      const livePct = Math.min(100, (elapsedMs / (r.sla_hours * 3_600_000)) * 100);
                      const c = r.sla_status === "overdue" ? "text-danger" : r.sla_status === "at_risk" ? "text-warning" : "text-slate-500";
                      return (
                        <div className="flex items-center gap-1.5">
                          <span className={cn("w-[4.75rem] shrink-0 text-right font-mono text-[10.5px] tabular-nums", c)}>{fmtDurLive(elapsedMs)}</span>
                          <span className="h-1.5 w-12 shrink-0 overflow-hidden rounded-full bg-slate-200"><span className="block h-full rounded-full transition-[width] duration-1000 ease-linear" style={{ width: `${livePct}%`, background: slaBarColor(r.sla_status as IntakeSlaPosture) }} /></span>
                          <span className="text-[9.5px] text-slate-400">of {r.sla_hours}h</span>
                        </div>
                      );
                    })()}</TD>
                    <TD><StatusPill r={r} /></TD>
                    <TD className="whitespace-nowrap text-[11px] text-slate-600">{r.assigned_to_label ?? <span className="text-slate-400">Unassigned</span>}</TD>
                  </TR>
                );
              })}</tbody>
            </Table>
            {shown.length > visibleCount && (
              <button onClick={() => setVisibleCount((c) => c + INBOX_PAGE)}
                className="w-full border-t border-slate-100 py-3 text-center font-mono text-[10.5px] font-medium uppercase tracking-[0.1em] text-brand-700 hover:bg-slate-50">
                ▾ Show more · {shown.length - visibleCount} remaining
              </button>
            )}
          </div></div>
        )}
      </Card>
    </div>
  );
}


// ---- My Work (staff) ------------------------------------------------------

function MyWorkTab({ onOpen }: { onOpen: (id: string) => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-mywork"], queryFn: intakeApi.myWork, ...LIVE_POLL });
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
              ? <p className="px-4 py-3 text-xs text-slate-400">Nothing waiting on your review.</p>
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

// The five derived Tier-0 gates (mirror backend app/intake/gates.py). Labels are
// display-only; the server is the source of truth for what a gate forces.
const GATE_CATALOG = [
  { key: "exclusivity", label: "Exclusivity / exclusive licence" },
  { key: "clinical", label: "Clinical-trial agreement" },
  { key: "sensitive_data", label: "Sensitive personal data" },
  { key: "regulated_marketing", label: "Regulated / therapeutic claim" },
  { key: "litigation", label: "Litigation / breach-termination" },
] as const;

// RAG per rung — dot colour + name emphasis. "planned" = a preview rung (the
// ladder before it's been started), rendered as a hollow dot.
const RUNG_UI: Record<string, { dot: string; name: string }> = {
  approved: { dot: "bg-success", name: "text-slate-500" },
  pending: { dot: "bg-warning animate-pulse", name: "text-slate-900 font-semibold" },
  waiting: { dot: "bg-slate-300", name: "text-slate-400" },
  rejected: { dot: "bg-danger", name: "text-danger line-through" },
  cancelled: { dot: "bg-slate-300", name: "text-slate-400 line-through" },
  planned: { dot: "border border-slate-400", name: "text-slate-500" },
};
const RUNG_LABEL: Record<string, string> = {
  approved: "Approved", pending: "Awaiting", waiting: "Waiting",
  rejected: "Rejected", cancelled: "Skipped", planned: "Planned",
};

// Workflows tab — the approval-ladder builder (reuses the routing-rule builder)
// plus the read-only Tier-0 gate catalogue that always forces its own rungs.
function WorkflowsBuilderTab() {
  return (
    <div className="space-y-5">
      <div>
        <p className="text-[11px] font-medium uppercase tracking-[0.06em] text-slate-400">
          <span className="text-brand-600">◎</span> Approval ladders · workflow builder
        </p>
        <p className="mt-1.5 max-w-2xl text-sm text-slate-500">
          Define the ordered approver chain a request runs through. The rule&rsquo;s criteria (value / type / risk) pick
          the ladder; each step is a group, role, or person, activated in turn. Tier-0 gates below always force their
          own senior rung on top — no matter which ladder matches.
        </p>
      </div>
      <RulesTab />
      <Card>
        <CardHeader>
          <CardTitle>Tier-0 hard gates</CardTitle>
          <span className="ml-auto text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">always applied</span>
        </CardHeader>
        <CardBody className="space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {GATE_CATALOG.map((g) => <Badge key={g.key} tone="amber">{g.label}</Badge>)}
          </div>
          <p className="text-xs text-slate-400">
            An AI classifier fires these from the request text/fields and forces a senior rung regardless of the ladder
            above. A reviewer can add or remove any gate per request from its Approval-ladder card.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}

// The rule-driven approval ladder, always visible: Tier-0 gate chips (add/remove
// overrides) over a RAG rung strip. Before submission the rungs are a preview
// (planned); after, they show live RAG status with inline Approve / Reject.
function ApprovalLadderCard({ r, canTriage, onRefreshed }: {
  r: IntakeRequest; canTriage: boolean; onRefreshed: () => void;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);
  const [addKey, setAddKey] = useState("");
  const [reassign, setReassign] = useState<"delegate" | "escalate" | null>(null);
  const [reassignTo, setReassignTo] = useState("");
  const { data: chain } = useQuery({ queryKey: ["intake-chain", r.id], queryFn: () => intakeApi.approvalChain(r.id) });
  const { data: assignees } = useQuery({ queryKey: ["intake-assignees"], queryFn: intakeApi.assignees });

  const gates = r.gates ?? { detected: [], overrides: [], effective: [], effective_keys: [] };
  const effectiveKeys = new Set(gates.effective_keys);
  const addable = GATE_CATALOG.filter((g) => !effectiveKeys.has(g.key));
  const rungs = (chain ?? []).filter((x) => x.status !== "cancelled");
  const started = rungs.some((x) => x.status !== "planned");
  const pending = rungs.find((x) => x.status === "pending");
  const closed = r.status === "closed" || r.status === "approved";

  async function run(fn: () => Promise<unknown>, msg: string) {
    setBusy(true);
    try {
      await fn();
      qc.invalidateQueries({ queryKey: ["intake-chain", r.id] });
      onRefreshed();
      notify(msg, "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Action failed", "error");
    } finally {
      setBusy(false);
    }
  }

  function decide(decision: "approve" | "reject") {
    if (!pending?.approval_request_id) return;
    let comment: string | undefined;
    if (decision === "reject") {
      comment = window.prompt("Reason for rejection (required):") ?? "";
      if (!comment.trim()) return;
    }
    return run(
      () => approvalsApi.decide(pending.approval_request_id as string, decision, comment),
      decision === "approve" ? "Step approved" : "Sent back for review",
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Approval ladder</CardTitle>
        <span className="ml-auto text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">
          {closed ? "Closed" : started ? "Running" : "Preview"}
        </span>
      </CardHeader>
      <CardBody className="space-y-4">
        {/* Tier-0 gates */}
        <div>
          <p className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">Mandatory gates</p>
          {gates.effective.length === 0 ? (
            <p className="text-xs text-slate-400">No hard gates — the ladder follows the value / type routing rules.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {gates.effective.map((g) => (
                <Badge key={g.key} tone="amber">
                  {g.label} → {g.approver_group}
                  {canTriage && !closed && (
                    <button disabled={busy} title="Remove gate"
                      onClick={() => run(() => intakeApi.overrideGate(r.id, { gate_key: g.key, action: "remove", reason: "removed by reviewer" }), "Gate removed")}
                      className="ml-0.5 hover:text-warning disabled:opacity-50">×</button>
                  )}
                </Badge>
              ))}
            </div>
          )}
          {canTriage && !closed && addable.length > 0 && (
            <div className="mt-2 flex items-center gap-2">
              <Select value={addKey} onChange={(e) => setAddKey(e.target.value)}>
                <option value="">Add a gate…</option>
                {addable.map((g) => <option key={g.key} value={g.key}>{g.label}</option>)}
              </Select>
              <Button size="sm" variant="ghost" loading={busy} disabled={!addKey}
                onClick={() => run(() => intakeApi.overrideGate(r.id, { gate_key: addKey, action: "add", reason: "added by reviewer" }).then(() => setAddKey("")), "Gate added")}>
                Add
              </Button>
            </div>
          )}
        </div>

        {/* RAG rung strip — always shown (preview before submit, live after) */}
        <div>
          <p className="mb-2 text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">
            Governance ladder{!started && !closed ? " · preview" : ""}
          </p>
          {rungs.length === 0 ? (
            <p className="text-xs text-slate-400">No approvers resolved — set a routing rule or add a gate.</p>
          ) : (
            <div className="flex flex-wrap items-center gap-x-1 gap-y-2">
              {rungs.map((rung, i) => {
                const ui = RUNG_UI[rung.status] ?? RUNG_UI.waiting;
                return (
                  <Fragment key={rung.approval_request_id ?? `p${rung.step_order}`}>
                    <span className="inline-flex items-center gap-1.5"
                      title={`${rung.step_order}. ${rung.approver_label} — ${RUNG_LABEL[rung.status] ?? rung.status}${(rung.needed ?? 1) > 1 ? ` · needs all ${rung.needed}` : ""}`}>
                      <span className={cn("h-2.5 w-2.5 shrink-0 rounded-full", ui.dot)} />
                      <span className={cn("text-[11px]", ui.name)}>{rung.approver_label}</span>
                      {(rung.needed ?? 1) > 1 && (rung.status === "pending" || rung.status === "approved") && (
                        <span className={cn("rounded px-1 text-[9px] font-semibold tabular-nums",
                          (rung.approvals ?? 0) >= (rung.needed ?? 1) ? "bg-success-subtle text-success" : "bg-warning-subtle text-warning")}>
                          {rung.approvals ?? 0}/{rung.needed}
                        </span>
                      )}
                    </span>
                    {i < rungs.length - 1 && <span className="px-1 text-slate-300">—</span>}
                  </Fragment>
                );
              })}
            </div>
          )}

          {/* actions */}
          {!closed && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {!started && canTriage && (
                <Button size="sm" loading={busy}
                  onClick={() => run(() => intakeApi.submitForApproval(r.id), "Submitted for approval")}>
                  Submit for approval
                </Button>
              )}
              {started && pending && canTriage && (reassign ? (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[11px] text-slate-500">{reassign === "delegate" ? "Delegate this step to" : "Escalate this step to"}</span>
                  <Select value={reassignTo} onChange={(e) => setReassignTo(e.target.value)} className="h-8 w-48 text-[13px]">
                    <option value="">Select a person…</option>
                    {(assignees ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                  </Select>
                  <Button size="sm" loading={busy} disabled={!reassignTo}
                    onClick={() => run(() => approvalsApi.reassign(pending.approval_request_id as string, reassignTo, reassign), reassign === "delegate" ? "Delegated" : "Escalated").then(() => { setReassign(null); setReassignTo(""); })}>Go</Button>
                  <Button size="sm" variant="ghost" onClick={() => { setReassign(null); setReassignTo(""); }}>Cancel</Button>
                </div>
              ) : (
                <>
                  <Button size="sm" loading={busy} onClick={() => decide("approve")}>✓ Approve step</Button>
                  <Button size="sm" variant="outline" loading={busy} onClick={() => decide("reject")}>✕ Reject</Button>
                  <Button size="sm" variant="ghost" onClick={() => setReassign("delegate")}>Delegate</Button>
                  <Button size="sm" variant="ghost" onClick={() => setReassign("escalate")}>Escalate</Button>
                  <span className="text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">
                    step {pending.step_order} · {pending.approver_label}{(pending.needed ?? 1) > 1 ? ` · ${pending.approvals ?? 0} of ${pending.needed} signed` : ""}
                  </span>
                </>
              ))}
              {started && !pending && (
                <span className="text-[10px] font-medium uppercase tracking-[0.06em] text-success">Ladder complete</span>
              )}
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

function WorkflowStepper({ steps }: { steps: IntakeRequest["workflow"] }) {
  return (
    <section>
      <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-400">Request workflow</p>
      <div className="flex overflow-x-auto rounded-md border border-slate-200">
        {steps.map((s) => (
          <div key={s.stage}
            className={cn("min-w-[128px] flex-1 border-r border-slate-200 px-3 py-3 text-center last:border-r-0",
              s.active ? "bg-brand-50" : s.done ? "bg-slate-50" : "")}>
            <div className={cn("text-sm leading-none", s.done ? "text-success" : s.active ? "text-brand-600" : "text-slate-300")}>
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

const _NEXT_STAGE: Record<string, string> = {
  intake: "drafting", drafting: "review", review: "approval", approval: "signature", signature: "active",
};

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
      <dt className="text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">{label}</dt>
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
        <span className="ml-auto text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">
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
      <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-400">Contract lifecycle</p>
      <div className="flex overflow-x-auto rounded-md border border-slate-200">
        {CONTRACT_STAGES.map((s, i) => {
          const done = curIdx > i, active = curIdx === i;
          return (
            <div key={s.key}
              className={cn("min-w-[100px] flex-1 border-r border-slate-200 px-2 py-3 text-center last:border-r-0",
                active ? "bg-brand-50" : done ? "bg-slate-50" : "")}>
              <div className={cn("text-sm leading-none", done ? "text-success" : active ? "text-brand-600" : "text-slate-300")}>
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
    <Card className="border-2 border-brand-600">
      <CardHeader className="bg-brand-50"><CardTitle>Next step</CardTitle>
        <span className="ml-auto text-[10px] font-medium uppercase tracking-[0.06em] text-slate-500">{stage}</span></CardHeader>
      <CardBody className="space-y-3">
        <p className="text-sm text-slate-700">{rs.next_step}</p>

        {stage === "review" && (
          <div className="rounded-md border border-slate-200 bg-slate-50 p-2.5">
            <p className="mb-2 text-xs text-slate-500">
              {rs.high_severity_issues > 0
                ? `${rs.high_severity_issues} high-severity issue${rs.high_severity_issues > 1 ? "s" : ""} — recommend a lawyer reviews before it moves on.`
                : "No high-severity issues — safe to move it forward yourself."}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant={rs.high_severity_issues > 0 ? undefined : "outline"} loading={busy}
                onClick={() => run(() => approvalsApi.submit({ contract_id: contractId }), "Sent to legal — approval requested")}>Send to legal</Button>
              {_NEXT_STAGE[stage] && (
                <Button size="sm" variant={rs.high_severity_issues > 0 ? "outline" : undefined} loading={busy}
                  onClick={() => run(() => contractsApi.transition(contractId, _NEXT_STAGE[stage] as never, { reason: "Advanced from review" }), `Advanced to ${_NEXT_STAGE[stage]}`)}>Advance to next step</Button>
              )}
            </div>
          </div>
        )}

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
            {rs.high_severity_issues > 0 && <span className="font-medium text-danger">{rs.high_severity_issues} high severity</span>}
          </div>
        )}
        {!rs.ready_for_approval && stage === "review" && (
          <p className="text-xs text-slate-400">Approval unlocks once the AI redline review is done and open issues are cleared.</p>
        )}
      </CardBody>
    </Card>
  );
}

// The AI analysis surfaced on the ticket once a contract exists: weighted risk
// + the playbook deviations, severity-ranked, so a reviewer can act without
// leaving the ticket.
function AiAnalysisCard({ contractId }: { contractId: string }) {
  const { data: risk } = useQuery({ queryKey: ["contract-risk", contractId], queryFn: () => contractsApi.risk(contractId) });
  const { data: devs } = useQuery({ queryKey: ["contract-devs", contractId], queryFn: () => contractsApi.deviations(contractId) });
  const { data: plain, isLoading: plainLoading } = useQuery({ queryKey: ["contract-plain", contractId], queryFn: () => contractsApi.plainSummary(contractId), staleTime: 5 * 60 * 1000 });
  const rs = (risk ?? null) as { score?: number | null; band?: string | null; note?: string } | null;
  const deviations = devs ?? [];
  const sev = (s: string) => (s || "").toLowerCase();
  const counts = {
    high: deviations.filter((d) => sev(d.severity) === "high" || sev(d.severity) === "critical").length,
    medium: deviations.filter((d) => sev(d.severity) === "medium").length,
    low: deviations.filter((d) => sev(d.severity) === "low").length,
  };
  return (
    <Card>
      <CardHeader><CardTitle>AI analysis</CardTitle>
        {rs?.score != null
          ? <Badge tone={riskTone(rs.band) as never}>{(rs.band ?? "risk")} · {rs.score}</Badge>
          : <span className="ml-auto text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">running…</span>}
      </CardHeader>
      <CardBody className="space-y-3">
        {/* Plain-English summary — readable to non-lawyers, front and centre. */}
        <div className="rounded-lg border border-brand-200 bg-brand-50/40 p-3">
          <p className="mb-1.5 font-mono text-[9px] font-semibold uppercase tracking-[0.1em] text-brand-600">In plain English</p>
          {plainLoading
            ? <p className="text-[13px] text-slate-400">Summarising the review…</p>
            : <div className="prose-sm text-[13px] leading-relaxed text-slate-700 [&_p]:my-1.5 [&_strong]:text-slate-900 [&_ul]:my-1.5 [&_ul]:space-y-1 [&_ul]:pl-4"><Markdown>{plain?.summary || "No summary available yet."}</Markdown></div>}
        </div>

        <div className="flex flex-wrap items-center gap-3 text-xs">
          <span className="font-medium text-danger">{counts.high} high</span>
          <span className="text-warning">{counts.medium} medium</span>
          <span className="text-slate-500">{counts.low} low</span>
          {deviations.length === 0 && (
            <span className="text-slate-400">No open deviations — clean, or the review is still running.</span>
          )}
        </div>

        {/* The clause-by-clause legal detail — tucked away for whoever wants it. */}
        {deviations.length > 0 && (
          <details className="rounded-md border border-slate-200">
            <summary className="cursor-pointer list-none px-3 py-2 text-[12px] font-medium text-slate-600 hover:text-slate-900">▸ Clause-by-clause detail ({deviations.length})</summary>
            <div className="space-y-2 px-3 pb-3">
              {deviations.slice(0, 12).map((d) => (
                <div key={d.id} className="rounded-md border border-slate-200 p-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={riskTone(d.severity) as never}>{d.severity}</Badge>
                    <span className="text-xs font-medium text-slate-800">{titleCase(d.clause_type)}</span>
                  </div>
                  <p className="mt-1 text-xs text-slate-600">{d.issue}</p>
                  {d.suggested_fix && <p className="mt-1 text-[11px] text-slate-400">Fix: {d.suggested_fix}</p>}
                </div>
              ))}
              {deviations.length > 12 && (
                <p className="text-xs text-slate-400">+{deviations.length - 12} more — open the workspace for the full set.</p>
              )}
            </div>
          </details>
        )}
      </CardBody>
    </Card>
  );
}

// Per-step type metadata for the governance ladder — the icon + plain-English
// status so a reviewer can tell an AI step from a human task from an approval,
// and whether a step is done, running, or waiting on them.
const STEP_META: Record<string, { icon: LucideIcon; label: string; wait: string; running: string }> = {
  ai_task: { icon: Bot, label: "AI step", wait: "Needs your review", running: "Agent is working…" },
  human_task: { icon: Users, label: "Human task", wait: "Waiting on you", running: "In progress" },
  approval: { icon: ShieldCheck, label: "Approval", wait: "Awaiting sign-off", running: "Awaiting sign-off" },
  signature: { icon: PenLine, label: "Signature", wait: "Out for signature", running: "Out for signature" },
  clm_draft: { icon: FileText, label: "Draft", wait: "Awaiting document", running: "Drafting…" },
  counterparty: { icon: Users, label: "Counterparty", wait: "With counterparty", running: "With counterparty" },
  notify: { icon: Bell, label: "Notify", wait: "Notifying", running: "Notifying" },
};
function stepMeta(type: string) { return STEP_META[type] ?? STEP_META.human_task; }
// Plain-English "what is this and what do I do" for the current step — the piece
// that makes a bare step name ("Cross-functional Assessment") actionable.
function stepGuidance(s: FlowRunStep): string {
  const waiting = s.status.startsWith("waiting");
  switch (s.type) {
    case "human_task":
      return `A manual checkpoint you handle off-system. Do the “${s.name}” work — loop in the relevant team and gather what's needed — then click “Complete this step” to record it and move the workflow on.`;
    case "ai_task":
      return waiting
        ? "The agent flagged this for your review — its output is on the ticket. Check it, then complete the step to move on."
        : "The agent runs this automatically. No action needed — it advances on its own when done.";
    case "approval":
      return "Waiting for the approver to sign off. It advances on its own once approved — use “Check approval status” to refresh, or complete it here if you're recording the decision manually.";
    case "signature":
      return "The document is out for e-signature. It advances once every signer has signed — use “Check signature status” to refresh.";
    case "clm_draft":
      return waiting ? "Waiting for the document to be drafted or uploaded before this step can advance." : "The agent is drafting the document — no action needed.";
    case "counterparty":
      return "With the counterparty for their review or input; advance once you hear back.";
    case "notify":
      return "Sending a notification — no action needed; it advances automatically.";
    default:
      return "Click “Complete this step” to advance the workflow.";
  }
}

// The actual litigation findings an AI step produced — the extracted deadlines
// (with statutory source) plus the hold / outside-counsel / settlement flags.
function AiFindingsLitigation({ a }: { a: LitigationAssessment }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-100 px-2.5 py-2">
      <p className="mb-1 font-mono text-[9px] font-semibold uppercase tracking-[0.1em] text-slate-400">Findings · {a.matter_type}</p>
      <div className="flex flex-wrap gap-1.5">
        {a.legal_hold_required && <Badge tone="red">Legal hold required</Badge>}
        {a.outside_counsel_likely && <Badge tone="amber">Outside counsel likely</Badge>}
        {a.settlement_posture && a.settlement_posture !== "none" && <Badge tone="violet">Settlement {a.settlement_posture}</Badge>}
      </div>
      {a.statutory_deadlines?.length ? (
        <ul className="mt-1.5 space-y-1">
          {a.statutory_deadlines.map((d, i) => (
            <li key={i} className="flex flex-wrap items-baseline gap-1.5 text-[12px]">
              <span className="font-medium text-slate-800">{d.what}</span>
              {d.date && <span className="font-mono text-[11px] font-semibold text-danger">{d.date}</span>}
              {d.source && <span className="text-[10.5px] text-slate-400">· {d.source}</span>}
            </li>
          ))}
        </ul>
      ) : <p className="mt-1 text-[11px] text-slate-400">No dated deadlines extracted.</p>}
    </div>
  );
}

// The playbook deviations an AI review found — counts + the top few, inline.
function AiFindingsDeviations({ contractId }: { contractId: string }) {
  const { data: devs } = useQuery({ queryKey: ["contract-devs", contractId], queryFn: () => contractsApi.deviations(contractId) });
  const deviations = devs ?? [];
  const sev = (s: string) => (s || "").toLowerCase();
  const hi = deviations.filter((d) => sev(d.severity) === "high" || sev(d.severity) === "critical").length;
  const md = deviations.filter((d) => sev(d.severity) === "medium").length;
  const lo = deviations.filter((d) => sev(d.severity) === "low").length;
  return (
    <div className="rounded-md border border-slate-200 bg-slate-100 px-2.5 py-2">
      <p className="mb-1 font-mono text-[9px] font-semibold uppercase tracking-[0.1em] text-slate-400">Playbook deviations</p>
      {deviations.length === 0 ? (
        <p className="text-[11px] text-slate-400">No open deviations — clean, or the review is still running.</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3 text-[11px]">
            <span className="font-medium text-danger">{hi} high</span>
            <span className="text-warning">{md} medium</span>
            <span className="text-slate-500">{lo} low</span>
          </div>
          <ul className="mt-1.5 space-y-1">
            {deviations.slice(0, 3).map((d) => (
              <li key={d.id} className="text-[12px]">
                <span className={cn("mr-1.5 rounded px-1 py-0.5 text-[9px] font-semibold uppercase",
                  sev(d.severity) === "high" || sev(d.severity) === "critical" ? "bg-danger-subtle text-danger" : sev(d.severity) === "medium" ? "bg-warning-subtle text-warning" : "bg-slate-200 text-slate-500")}>{d.severity}</span>
                <span className="text-slate-700">{titleCase(d.clause_type)}</span> — <span className="text-slate-500">{d.issue}</span>
              </li>
            ))}
          </ul>
          {deviations.length > 3 && <p className="mt-1 text-[11px] text-slate-400">+{deviations.length - 3} more below.</p>}
        </>
      )}
    </div>
  );
}

// Dynamic step read-out — shows the information that matters for the SELECTED
// step at its current state: an AI step surfaces the agent's output + findings,
// a draft step the document, a human task the guidance; done steps show what
// they produced, pending steps say they haven't run.
function StepInfo({ step, r, state }: { step: FlowRunStep; r: IntakeRequest; state: "done" | "current" | "pending" }) {
  const res = (step.result ?? {}) as Record<string, unknown>;
  const conf = typeof res.confidence === "number" ? Math.round((res.confidence as number) * 100) : null;
  const la = (r.ai_triage as Record<string, unknown> | null)?.litigation_assessment as LitigationAssessment | undefined;

  const agentOutput = step.type === "ai_task" && (res.agent != null || res.category != null || conf != null) ? (
    <div className="rounded-md border border-slate-200 bg-slate-100 px-2.5 py-2">
      <p className="mb-1 font-mono text-[9px] font-semibold uppercase tracking-[0.1em] text-slate-400">Agent output</p>
      <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
        {res.agent != null && <Badge tone="violet">{titleCase(String(res.agent).replace(/[-_]/g, " "))}</Badge>}
        {res.category != null && <span className="text-slate-700">{String(res.category)}</span>}
        {conf != null && <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-semibold", conf >= 75 ? "bg-success-subtle text-success" : "bg-warning-subtle text-warning")}>{conf}% confidence</span>}
      </div>
    </div>
  ) : null;
  const findings = step.type === "ai_task" ? (la ? <AiFindingsLitigation a={la} /> : r.contract_id ? <AiFindingsDeviations contractId={r.contract_id} /> : null) : null;

  const statusLine =
    state === "done" ? <p className="text-[12px] font-medium text-success">✓ Completed</p>
      : state === "pending" ? <p className="text-[12px] text-slate-400">Pending — runs once the earlier steps finish.</p>
        : <p className="text-[12.5px] leading-relaxed text-slate-600">{stepGuidance(step)}</p>;
  const note = step.note ? <p className="text-[11px] text-slate-400">{step.note}</p> : null;
  const link = step.type === "clm_draft" && r.contract_id
    ? <a href={`/contracts/${r.contract_id}`} className="inline-block text-[12px] font-medium text-brand-700 hover:underline">Open the drafted document →</a>
    : step.type === "ai_task" && r.contract_id
      ? <a href={`/contracts/${r.contract_id}`} className="inline-block text-[12px] font-medium text-brand-700 hover:underline">Open the full analysis →</a>
      : null;

  return <div className="space-y-2">{agentOutput}{findings}{statusLine}{note}{link}</div>;
}

// Litigation Intake Agent read-out — the matter facts a triaging lawyer needs,
// surfaced when the request classifies as litigation.
function LitigationCard({ a }: { a: LitigationAssessment }) {
  return (
    <Card className="border-l-2 border-l-brand-600 p-5">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.08em] text-brand-600">§ Litigation Intake · {a.matter_type}</p>
        {a.legal_hold_required && <Badge tone="red">Legal hold required</Badge>}
        {a.outside_counsel_likely && <Badge tone="amber">Outside counsel likely</Badge>}
        {a.settlement_posture && a.settlement_posture !== "none" && <Badge tone="violet">Settlement {a.settlement_posture}</Badge>}
      </div>
      <p className="text-[13px] leading-relaxed text-slate-700">{a.summary}</p>
      {a.statutory_deadlines.length > 0 && (
        <div className="mt-3 border-t border-slate-100 pt-3">
          <p className="mb-1.5 font-mono text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-400">Deadlines</p>
          <ul className="space-y-1 text-[12.5px] text-slate-600">
            {a.statutory_deadlines.map((d, i) => (
              <li key={i} className="flex flex-wrap items-baseline gap-1.5">
                <span className="font-medium text-slate-800">{d.what}</span>
                {d.date && <span className="font-mono text-[11px] font-semibold text-danger">{d.date}</span>}
                {d.source && <span className="text-[11px] text-slate-400">· {d.source}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {a.key_parties.length > 0 && (
        <div className="mt-3 border-t border-slate-100 pt-3">
          <p className="mb-1 font-mono text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-400">Parties</p>
          <p className="text-[12.5px] text-slate-600">{a.key_parties.join(" · ")}</p>
        </div>
      )}
    </Card>
  );
}

function RequestDetailView({ id, onBack, canTriage, inPane = false }: { id: string; onBack: () => void; canTriage: boolean; inPane?: boolean }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { user } = useAuth();
  const { data: r, isLoading } = useQuery({ queryKey: ["intake-req", id], queryFn: () => intakeApi.get(id) });
  const { data: handoffs } = useQuery({ queryKey: ["intake-handoffs", id], queryFn: () => intakeApi.handoffs(id) });
  const { data: assignees } = useQuery({ queryKey: ["intake-assignees"], queryFn: intakeApi.assignees, enabled: canTriage });
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list, enabled: canTriage });
  const { data: contracts } = useQuery({ queryKey: ["contracts"], queryFn: contractsApi.list, enabled: canTriage });
  const { data: legs } = useQuery({ queryKey: ["intake-sla", id], queryFn: () => intakeApi.slaLegs(id) });
  const { data: ticketDocs } = useQuery({ queryKey: ["intake-docs", id], queryFn: () => intakeApi.documents(id) });
  const { data: flowRun } = useQuery({
    queryKey: ["flow-run", id],
    queryFn: () => flowsApi.runForRequest(id),
    // Poll while something is actively working so the ladder animates live — a
    // mid-beat step (running) or a subsystem in progress (waiting_job). Idle
    // runs (waiting on a human / complete) don't poll.
    refetchInterval: (q) => {
      const run = q.state.data as FlowRun | null | undefined;
      if (!run) return false;
      const working = (run.steps ?? []).some((s) => s.status === "running" || s.status === "waiting_job");
      return run.status === "running" || working ? 1500 : false;
    },
  });
  const hasRun = !!flowRun && ["running", "waiting", "complete"].includes(flowRun.status);
  const hasAttachment = (ticketDocs ?? []).some((d) => d.extracted_chars > 0);
  const [busy, setBusy] = useState(false);
  const [reassignTo, setReassignTo] = useState("");
  const [snoozeUntil, setSnoozeUntil] = useState("");
  const [promoteKind, setPromoteKind] = useState<"project" | "contract">("project");
  const [promoteTarget, setPromoteTarget] = useState("");
  const [showLegs, setShowLegs] = useState(false);
  const [tab, setTab] = useState("overview");
  const promoteOptions =
    promoteKind === "project"
      ? (projects ?? []).map((p) => ({ id: p.id, label: p.name }))
      : (contracts ?? []).map((c) => ({ id: c.id, label: c.title }));

  function refresh() {
    ["intake-list", "intake-mywork", "intake-mine"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    qc.invalidateQueries({ queryKey: ["intake-req", id] });
    qc.invalidateQueries({ queryKey: ["intake-handoffs", id] });
    qc.invalidateQueries({ queryKey: ["intake-sla", id] });
    qc.invalidateQueries({ queryKey: ["flow-run", id] });
  }
  async function act(fn: () => Promise<unknown>, msg: string) {
    setBusy(true);
    try { await fn(); refresh(); notify(msg, "success"); }
    catch (e) { notify(e instanceof Error ? e.message : "Action failed", "error"); }
    finally { setBusy(false); }
  }

  // Which ladder step the detail box shows — defaults to and follows the current
  // step; a click on any step overrides it. (Hook must precede the early return.)
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  useEffect(() => { setSelectedIdx(null); }, [flowRun?.current_index]);

  // Pump a mid-beat step: a run parked in "running" is an ai_task showing its
  // "Agent is working…" animation with the agent not yet run. Hold ~1s so the
  // beat is visible, then resume the executor (refresh) so the agent runs for
  // real and the flow advances. Guarded to fire once per step; deliberately not
  // cancelled on unmount, so navigating away mid-beat still completes it.
  const pumpedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!flowRun || flowRun.status !== "running") return;
    const key = `${flowRun.id}:${flowRun.current_index}`;
    if (pumpedRef.current === key) return;
    pumpedRef.current = key;
    const runId = flowRun.id;
    setTimeout(() => {
      flowsApi.refreshRun(runId).finally(() => qc.invalidateQueries({ queryKey: ["flow-run", id] }));
    }, 1000);
  }, [flowRun, id, qc]);

  if (isLoading || !r) return <CenterSpinner label="Loading the request…" />;

  const posture = r.sla_status as IntakeSlaPosture;
  const submitted = r.submitted_at ? new Date(r.submitted_at).getTime() : null;
  const elapsedH = submitted != null ? (Date.now() - submitted) / 3_600_000 : null;
  const open = r.status !== "closed" && r.status !== "approved";
  const ai = (r.ai_triage ?? {}) as Record<string, unknown>;
  const la = ai.litigation_assessment as LitigationAssessment | undefined;
  const accent = posture === "overdue" ? "border-l-danger" : posture === "at_risk" ? "border-l-warning" : "border-l-success";
  const postureText = posture === "overdue" ? "text-danger" : posture === "at_risk" ? "text-warning" : "text-success";
  // A short header title — descriptions can be long (pasted text, folded
  // attachments). Take the first line, capped; the full text lives in Overview.
  const firstLine = (r.description || r.type_label).trim().split("\n")[0].trim();
  const shortTitle = firstLine.length > 140 ? `${firstLine.slice(0, 137).trimEnd()}…` : (firstLine || r.type_label);

  // Dispatch-desk detail (reference "Legal Mission Control" layout, in the light
  // theme): a single scrolling page — header + SLA meter, dispatch banner,
  // governance ladder, request brief, AI analysis, assignment, custody legs, and
  // a tamper-evident timeline. A render helper draws the mono-caps section heads.
  const youCreated = !!user && r.requester_user_id === user.id;
  // A running governance ladder IS direction — count it as dispatched even if no
  // owner is assigned yet, so the banner doesn't contradict the ladder below it.
  const dispatched = hasRun || !!r.assigned_to_user_id || r.status !== "open";
  const owner = r.assigned_to_label;
  const flowSteps = flowRun?.steps ?? [];
  const current = flowRun ? flowSteps[flowRun.current_index] : undefined;
  const doneCount = flowSteps.filter((s) => s.status === "done" || s.status === "complete").length;
  // Click any ladder step to inspect it; defaults to (and follows) the current step.
  const selIdx = selectedIdx ?? flowRun?.current_index ?? 0;
  const selStep = flowSteps[selIdx];
  const selState: "done" | "current" | "pending" = !selStep ? "pending"
    : (selStep.status === "done" || selStep.status === "complete") ? "done"
      : (selStep.idx === flowRun?.current_index && flowRun?.status !== "complete") ? "current"
        : "pending";
  const head = (label: string, note?: React.ReactNode) => (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">{label}</span>
      {note && <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-slate-400">{note}</span>}
    </div>
  );

  return (
    <div className="space-y-3">
      {!inPane && (
        <button onClick={onBack}
          className="inline-flex items-center gap-1 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500 hover:text-slate-900">
          ← Back to inbox
        </button>
      )}

      {/* ===== header + SLA meter ===== */}
      <Card className={cn("border-l-[3px] p-5", accent)}>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-[11px] text-slate-500">{r.ref}</span>
              <Badge tone={PRIORITY_TONE[r.priority] as never}>{r.priority}</Badge>
              <Badge tone="blue">{r.type_label}</Badge>
              <StatusPill r={r} />
              {r.handoff_holder && <Badge tone="violet">{titleCase(r.handoff_holder)} holds</Badge>}
              {youCreated && <Badge tone="slate">You created</Badge>}
            </div>
            <h2 className="mt-2 line-clamp-2 text-[19px] font-semibold leading-snug text-slate-900">{shortTitle}</h2>
            <p className="mt-1.5 text-xs text-slate-500">
              From <span className="text-slate-700">{r.requester_name ?? "—"}</span>
              {r.department ? ` · ${r.department}` : ""}
              {r.submitted_at ? ` · Submitted ${new Date(r.submitted_at).toLocaleString()}` : ""}
              {owner ? <> · Owner <span className="text-slate-700">{owner}</span></> : ""}
            </p>
          </div>
          {open && elapsedH != null && (
            <div className="shrink-0 sm:text-right">
              <p className={cn("font-mono text-[10px] font-semibold uppercase tracking-[0.1em]", postureText)}>SLA {POSTURE_LABEL[posture]}</p>
              <p className="text-2xl font-semibold tabular-nums text-slate-900">{fmtElapsedHours(elapsedH)}</p>
              <p className="text-[11px] text-slate-400">of {r.sla_hours} hrs window</p>
              <div className="mt-1.5 h-1.5 w-40 overflow-hidden rounded-full bg-slate-200 sm:ml-auto">
                <div className="h-full rounded-full" style={{ width: `${Math.min(100, r.sla_pct)}%`, background: slaBarColor(posture) }} />
              </div>
              <p className="mt-1 text-[11px] tabular-nums text-slate-400">{r.sla_pct}% elapsed</p>
            </div>
          )}
        </div>
      </Card>

      {/* ===== dispatch banner ===== */}
      {canTriage && (
        <div className={cn("flex items-start gap-2.5 rounded-xl border border-l-[3px] px-4 py-3 text-[13px]",
          dispatched ? "border-success/30 border-l-success bg-success-subtle" : "border-warning/30 border-l-warning bg-warning-subtle")}>
          <span className={cn("mt-0.5 font-semibold", dispatched ? "text-success" : "text-warning")}>{dispatched ? "✓" : "◆"}</span>
          <div>
            <p className={cn("font-mono text-[10px] font-semibold uppercase tracking-[0.1em]", dispatched ? "text-success" : "text-warning")}>
              {dispatched ? "Dispatched → in the working queue" : "Not dispatched yet"}
            </p>
            <p className="mt-0.5 text-slate-600">
              {dispatched
                ? owner ? <>Assigned to <span className="font-medium text-slate-900">{owner}</span> — it now appears in their queue.</> : "A governance ladder is running on this request."
                : "This request has no direction — start a governance ladder or assign an owner below."}
            </p>
          </div>
        </div>
      )}

      {/* ===== governance ladder ===== */}
      <Card className="p-5">
        {hasRun && flowRun ? (
          <>
            {head(`Governance Ladder · ${flowRun.flow_name}`,
              <>Step {Math.min(flowRun.current_index + 1, flowSteps.length)} of {flowSteps.length} · <span className={flowRun.status === "complete" ? "text-success" : "text-slate-500"}>{titleCase(flowRun.status)}</span></>)}
            <div className="mb-4 h-1.5 overflow-hidden rounded-full bg-slate-200">
              <div className="h-full rounded-full bg-brand-600 transition-all duration-500" style={{ width: `${flowSteps.length ? Math.round((doneCount / flowSteps.length) * 100) : 0}%` }} />
            </div>
            <div className="flex items-start overflow-x-auto pb-1">
              {flowSteps.map((s, i) => {
                const m = stepMeta(s.type);
                const done = s.status === "done" || s.status === "complete";
                const active = s.idx === flowRun.current_index && flowRun.status !== "complete";
                // Two distinct live states: "working" = a subsystem is actively
                // running (ai_task mid-beat, approval/signature in flight) → spin;
                // "needsYou" = the step is parked waiting on the user → attention pulse.
                const working = s.status === "running" || s.status === "waiting_job";
                const needsYou = s.status === "waiting_human";
                const StepIcon = working ? RotateCw : m.icon;
                const isSel = s.idx === selIdx;
                return (
                  <Fragment key={s.idx}>
                    <button type="button" onClick={() => setSelectedIdx(s.idx)}
                      className={cn("flex min-w-[86px] max-w-[116px] shrink-0 flex-col items-center gap-1.5 rounded-lg px-1 py-1.5 text-center transition hover:bg-slate-50", isSel && "bg-slate-100 ring-1 ring-slate-200")}>
                      <span className={cn("grid h-9 w-9 place-items-center rounded-full ring-2 transition",
                        done ? "bg-success-subtle text-success ring-success/40"
                          : active ? cn("bg-brand-50 text-brand-700 ring-brand-300", (working || needsYou) && "animate-pulse")
                            : "bg-slate-100 text-slate-400 ring-slate-200")}>
                        {done ? <Check className="h-4 w-4" /> : <StepIcon className={cn("h-4 w-4", working && "animate-spin")} />}
                      </span>
                      <span className={cn("text-[11px] leading-tight", active ? "font-semibold text-slate-900" : done ? "text-slate-600" : "text-slate-400")}>{s.name}</span>
                      <span className={cn("font-mono text-[8.5px] uppercase tracking-wide",
                        done ? "text-success" : active ? (needsYou ? "text-warning" : "text-brand-600") : "text-slate-400")}>
                        {done ? "Done" : active ? (needsYou ? m.wait : m.running) : m.label}
                      </span>
                    </button>
                    {i < flowSteps.length - 1 && <span className={cn("mt-4 h-0.5 min-w-[12px] flex-1 rounded", done ? "bg-success/40" : "bg-slate-200")} />}
                  </Fragment>
                );
              })}
            </div>
            {selStep && (
              <div className="mt-4 flex items-start gap-2.5 rounded-lg border border-slate-200 bg-slate-50 p-3">
                <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-brand-50 text-brand-700">
                  {(() => { const I = stepMeta(selStep.type).icon; return <I className="h-3.5 w-3.5" />; })()}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-slate-400">{selState === "current" ? "Now" : selState === "done" ? "Done" : `Step ${selStep.idx + 1}`}</span>
                    <span className="text-[13px] font-semibold text-slate-900">{selStep.name}</span>
                    <Badge tone={selState === "done" ? "green" : selState === "current" ? (selStep.status.startsWith("waiting") ? "amber" : "blue") : "slate"}>{stepMeta(selStep.type).label}</Badge>
                  </div>
                  <div className="mt-1"><StepInfo step={selStep} r={r} state={selState} /></div>
                </div>
              </div>
            )}
            {flowRun.status === "complete" && (
              <div className="mt-4 flex items-center gap-2 rounded-lg border border-success/30 bg-success-subtle px-3 py-2 text-[13px] text-success">
                <Check className="h-4 w-4" /> Workflow complete — every step is done.
              </div>
            )}
            <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
              {current?.status === "waiting_human" && (
                <Button size="sm" loading={busy} onClick={() => act(() => flowsApi.completeStep(flowRun.id), `“${current.name}” completed`)}><Check className="h-4 w-4" /> Complete this step</Button>
              )}
              {current && (current.type === "approval" || current.type === "signature") && (
                <Button size="sm" variant="outline" loading={busy} onClick={() => act(() => flowsApi.refreshRun(flowRun.id), "Refreshed")}>Check {current.type} status</Button>
              )}
              {r.contract_id && (
                <a href={`/contracts/${r.contract_id}`} className="ml-auto inline-flex items-center gap-1 text-[13px] font-semibold text-brand-700 hover:underline">Open the drafted contract →</a>
              )}
              {flowRun.error && <p className="text-xs text-danger">{flowRun.error}</p>}
            </div>
          </>
        ) : r.contract_id ? (
          <>
            <ContractLifecycleTracker contractId={r.contract_id} />
            <div className="mt-3"><WorkflowPanel requestId={id} suggestion={r.ai_triage?.flow_suggestion as FlowSuggestion | undefined} /></div>
          </>
        ) : (
          <>
            {head("Request Workflow")}
            <WorkflowStepper steps={r.workflow} />
            <div className="mt-3"><WorkflowPanel requestId={id} suggestion={r.ai_triage?.flow_suggestion as FlowSuggestion | undefined} /></div>
          </>
        )}
      </Card>

      {la && <LitigationCard a={la} />}

      {/* ===== request brief ===== */}
      <Card className="p-5">
        {head("Request Brief", <>{ai.category != null && <span className="text-brand-600">{String(ai.category)}</span>}{ai.risk_flag != null && <span className={cn("ml-2", String(ai.risk_flag) === "high" ? "text-danger" : "text-warning")}>{String(ai.risk_flag)} risk</span>}</>)}
        <p className="max-h-48 overflow-y-auto whitespace-pre-wrap text-sm text-slate-700">{r.description || r.type_label}</p>
        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-slate-100 pt-4 sm:grid-cols-4">
          <Fact label="Counterparty">{requestCounterparty(r) ?? <span className="text-slate-400">—</span>}</Fact>
          <Fact label="Department">{r.department ?? "—"}</Fact>
          {ai.confidence != null && <Fact label="AI confidence">{Math.round(Number(ai.confidence) * 100)}%</Fact>}
          {ai.complexity != null && <Fact label="Complexity">{titleCase(String(ai.complexity))}</Fact>}
          {r.contract_id && r.contract_title && <Fact label="Contract"><a className="text-brand-700 hover:underline" href={`/contracts/${r.contract_id}`}>{r.contract_title}</a></Fact>}
        </dl>
        {(r.fired_rules as { summaries?: { name: string; actions: string[] }[] } | null)?.summaries?.length ? (
          <div className="mt-3 border-t border-slate-100 pt-3">
            <p className="mb-1 font-mono text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-400">Routing rules fired</p>
            <ul className="space-y-1 text-xs text-slate-600">
              {(r.fired_rules as { summaries: { name: string; actions: string[] }[] }).summaries.map((s, i) => <li key={i}>▸ <b>{s.name}</b> — {s.actions.join(", ")}</li>)}
            </ul>
          </div>
        ) : null}
        {/* contract access / draft */}
        {r.contract_id ? (
          <a href={`/contracts/${r.contract_id}`} className="mt-4 flex items-center justify-between rounded-lg border border-brand-200 bg-brand-50 px-4 py-2.5 text-sm hover:bg-brand-100">
            <span className="min-w-0 truncate text-slate-700">Contract: <strong className="text-slate-900">{r.contract_title ?? "linked"}</strong></span>
            <span className="shrink-0 font-semibold text-brand-700">Open →</span>
          </a>
        ) : canTriage && open && draftableDocType(r) ? (
          <div className="mt-4 rounded-lg border border-dashed border-brand-300 bg-brand-50/50 px-4 py-3 text-sm">
            {hasAttachment ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="mr-1 text-slate-600">Attached document ready:</span>
                <Button size="sm" loading={busy} onClick={() => act(() => intakeApi.ingestAttachment(id), "Using the attachment — review running")}>Use attached as the contract →</Button>
                <Button size="sm" variant="outline" loading={busy} onClick={() => act(() => intakeApi.draftContract(id), `${draftableDocType(r)} drafted — review running`)}>Approve &amp; draft fresh</Button>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <span className="mr-1 text-slate-600">Approve to auto-draft the {draftableDocType(r)}:</span>
                <Button size="sm" loading={busy} onClick={() => act(() => intakeApi.draftContract(id), `${draftableDocType(r)} drafted — review running`)}>Approve &amp; draft the {draftableDocType(r)} →</Button>
              </div>
            )}
          </div>
        ) : null}
      </Card>

      {/* ===== AI analysis ===== */}
      {r.contract_id && <Card className="p-5">{head("Playbook Deviations · Risk")}<AiAnalysisCard contractId={r.contract_id} /></Card>}

      {/* ===== counterparty & screening + documents ===== */}
      <Card className="p-5">{head("Counterparty & Screening")}
        <div className="space-y-4"><PartiesPanel r={r} canTriage={canTriage} onRefreshed={refresh} /><ScreeningPanel r={r} canTriage={canTriage} onRefreshed={refresh} /></div>
      </Card>
      <Card className="p-5">{head("Documents")}<DocumentsPanel requestId={r.id} /></Card>

      {/* ===== assignment & dispatch ===== */}
      {canTriage && open && (
        <Card className="p-5">
          {head("Assignment · Direction & Ownership", owner ? <>Owned by <span className="text-slate-600">{owner}</span></> : "Unassigned")}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="flex items-end gap-2">
              <Field label="Reassign owner" className="flex-1">
                <Select value={reassignTo} onChange={(e) => setReassignTo(e.target.value)}>
                  <option value="">Select…</option>{(assignees ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </Select>
              </Field>
              <Button size="sm" variant="outline" loading={busy} disabled={!reassignTo} onClick={() => act(async () => { await intakeApi.triage(id, { action: "reassigned", assignee_user_id: reassignTo }); setReassignTo(""); }, "Reassigned")}>Go</Button>
            </div>
            <div className="flex items-end gap-2">
              <Field label="Snooze until" className="flex-1"><Input type="date" value={snoozeUntil} onChange={(e) => setSnoozeUntil(e.target.value)} /></Field>
              <Button size="sm" variant="outline" loading={busy} disabled={!snoozeUntil} onClick={() => act(async () => { await intakeApi.triage(id, { action: "snoozed", snoozed_until: snoozeUntil }); setSnoozeUntil(""); }, "Snoozed")}>Go</Button>
            </div>
            <div className="flex items-end gap-2 sm:col-span-2">
              <Field label="Promote to" className="flex-1">
                <div className="flex gap-1">
                  <Select value={promoteKind} onChange={(e) => { setPromoteKind(e.target.value as "project" | "contract"); setPromoteTarget(""); }}><option value="project">Project</option><option value="contract">Contract</option></Select>
                  <Select value={promoteTarget} onChange={(e) => setPromoteTarget(e.target.value)}><option value="">Select…</option>{promoteOptions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}</Select>
                </div>
              </Field>
              <Button size="sm" variant="outline" loading={busy} disabled={!promoteTarget} onClick={() => act(async () => { await intakeApi.promote(id, promoteKind, promoteTarget); setPromoteTarget(""); }, `Promoted to ${promoteKind}`)}>Go</Button>
            </div>
          </div>
          <div className="mt-3 flex flex-wrap gap-2 border-t border-slate-100 pt-3">
            {r.status !== "escalated" && <Button size="sm" variant="outline" loading={busy} onClick={() => act(() => intakeApi.triage(id, { action: "escalate" }), "Escalated")}>⚡ Escalate</Button>}
            <Button size="sm" variant="outline" loading={busy} onClick={() => act(() => intakeApi.triage(id, { action: "manual_close" }), "Closed")}>✓ Mark complete</Button>
          </div>
        </Card>
      )}

      <RelatedContractsCard r={r} contracts={contracts ?? []} />

      {/* ===== SLA custody legs ===== */}
      {legs && legs.legs.length > 0 && (
        <Card className="p-5">
          {head("SLA Custody Legs", <span className={legs.breached ? "text-danger" : ""}>{legs.breached ? "Window breached" : "Clock running"} · {fmtElapsedHours(elapsedH ?? 0)} elapsed</span>)}
          <SlaLegsBar legs={legs.legs} breached={legs.breached} />
          <p className="mt-2 text-[11px] text-slate-400">Legs derive from the hand-off ledger — every baton pass starts a new clock segment. One window, no resets.</p>
        </Card>
      )}

      {/* ===== timeline ===== */}
      <Card className="p-5">
        {head(`Timeline (${(handoffs ?? []).length})`, "Chain-sealed · tamper-evident")}
        {(handoffs ?? []).length === 0 ? <p className="text-xs text-slate-400">Still in the intake queue — no hand-offs yet.</p> : (
          <ol className="space-y-3">
            {(handoffs ?? []).map((h) => (
              <li key={h.id} className="flex gap-3 text-xs">
                <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-brand-400" />
                <div className="min-w-0">
                  <span className="capitalize text-slate-500">{h.from_holder ?? "queue"}</span>
                  <span className="text-slate-400"> → </span>
                  <span className="font-medium capitalize text-slate-900">{h.to_label ?? h.to_holder}</span>
                  {h.reason && <span className="text-slate-400"> · {h.reason}</span>}
                  <div className="mt-0.5 flex items-center gap-2 text-[10px] text-slate-400">
                    {h.created_at && <span>{new Date(h.created_at).toLocaleString()}</span>}
                    <span className="font-mono">#{String(h.id).slice(0, 8)}</span>
                  </div>
                </div>
              </li>
            ))}
          </ol>
        )}
      </Card>
    </div>
  );
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
              <button className="ml-auto text-slate-400 hover:text-danger" disabled={busy}
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
              <span key={i} className="text-danger">{m.name}{m.programs ? ` · ${m.programs}` : ""}</span>
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
    <div className="space-y-4">
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
