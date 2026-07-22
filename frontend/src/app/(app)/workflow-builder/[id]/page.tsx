"use client";

// Full-page workflow editor — a visual step "ladder" replacing the old modal.
// [id] is "new" (blank create) or a flow id (edit, loaded via getFlow).
// Left column: settings + criteria + at-a-glance. Right column: the ladder.

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Bell,
  ChevronDown,
  ChevronRight,
  FileText,
  Gavel,
  PenLine,
  Plus,
  ShieldCheck,
  Sparkles,
  Trash2,
  User,
  Users,
  Workflow,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  CenterSpinner,
  ErrorState,
  Field,
  Input,
  Select,
} from "@/components/ui";
import { flowsApi, approvalsApi } from "@/lib/endpoints";
import { titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { ApprovalRoutingRule, Flow, FlowStepType } from "@/lib/types";

const ROLE_OPTIONS = [
  "requester",
  "legal_ops",
  "paralegal",
  "attorney",
  "legal_counsel",
  "gc",
  "cfo",
  "ciso",
  "privacy",
  "procurement",
];

const AGENT_OPTIONS: { label: string; value: string }[] = [
  { label: "NDA agent", value: "nda-agent" },
  { label: "Contract Review agent", value: "contract-review-agent" },
  { label: "Litigation agent", value: "litigation-agent" },
  { label: "Notice mgmt agent", value: "notice-mgmt-agent" },
  { label: "Vendor intake agent", value: "vendor-intake-agent" },
  { label: "Privacy assessment agent", value: "privacy-assessment-agent" },
  { label: "Trademark agent", value: "trademark-agent" },
  { label: "Policy / FAQ agent", value: "faq-agent" },
];


// Node accent + icon per step type. Classes are static so Tailwind keeps them.
const STEP_STYLE: Record<
  FlowStepType,
  { icon: LucideIcon; ring: string; text: string; bg: string }
> = {
  clm_draft: { icon: FileText, ring: "ring-success/40", text: "text-success", bg: "bg-success-subtle" },
  human_task: { icon: User, ring: "ring-info/40", text: "text-info", bg: "bg-info-subtle" },
  ai_task: { icon: Sparkles, ring: "ring-warning/40", text: "text-warning", bg: "bg-warning-subtle" },
  approval: { icon: Gavel, ring: "ring-brand-200", text: "text-brand-600", bg: "bg-brand-50" },
  signature: { icon: PenLine, ring: "ring-info/40", text: "text-info", bg: "bg-info-subtle" },
  counterparty: { icon: Users, ring: "ring-brand-200", text: "text-brand-600", bg: "bg-brand-50" },
  notify: { icon: Bell, ring: "ring-slate-300", text: "text-slate-500", bg: "bg-slate-100" },
};

function str(v: unknown): string {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

// ---- Approval step ↔ Approval Routing link --------------------------------
// An approval step doesn't send to a single approver — it submits the contract
// through the org's Approval Routing chain (value/type rules → DoA gates → NDA
// fast-lane). This surfaces that live so the two systems read as one.
function critSummary(c: Record<string, unknown>): string {
  const p: string[] = [];
  if (c.min_value != null) p.push(`≥ $${Number(c.min_value).toLocaleString()}`);
  if (c.max_value != null) p.push(`≤ $${Number(c.max_value).toLocaleString()}`);
  const ty = c.type ?? c.match_type;
  if (ty) p.push(`type ${String(ty)}`);
  return p.length ? p.join(" · ") : "any contract";
}
function chainSummary(r: ApprovalRoutingRule): string {
  if (r.steps?.length)
    return r.steps
      .map((s) => s.approver_group_name || s.approver_user_name || (s.approver_role ? titleCase(s.approver_role) : "?"))
      .join(" → ");
  return r.approver_role ? titleCase(r.approver_role) : "—";
}

// Route picker for an Approval step: "Auto" (best match by priority) or a
// specific pinned route. Reads the org's active routing rules.
function RoutingRuleSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string | undefined) => void;
}) {
  const { data: rules } = useQuery({ queryKey: ["approval-routing-rules"], queryFn: approvalsApi.routingRules });
  const active = (rules ?? []).filter((r) => r.is_active);
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value || undefined)}>
      <option value="">Auto — best match by priority</option>
      {active.map((r) => (
        <option key={r.id} value={r.id}>
          {r.name} — {critSummary(r.criteria)} → {chainSummary(r)}
        </option>
      ))}
    </Select>
  );
}

function ApprovalRoutingHint({ pinnedId }: { pinnedId?: string }) {
  const { data: rules } = useQuery({ queryKey: ["approval-routing-rules"], queryFn: approvalsApi.routingRules });
  const active = (rules ?? []).filter((r) => r.is_active);
  const pinned = pinnedId ? active.find((r) => r.id === pinnedId) : undefined;
  return (
    <div className="rounded-md border border-brand-200 bg-brand-50/60 p-3 text-xs">
      <p className="flex items-center gap-1.5 font-medium text-slate-900">
        <ShieldCheck className="h-3.5 w-3.5 text-brand-600" />
        {pinned ? `Runs the “${pinned.name}” route` : "Auto-matches your Approval Routing chain"}
      </p>
      <p className="mt-1 text-slate-600">
        {pinned ? (
          <>
            This step always uses <span className="font-medium text-slate-800">{pinned.name}</span> (
            {chainSummary(pinned)}), regardless of value or type. Delegation-of-Authority gates still apply.
          </>
        ) : (
          <>
            This step submits through the routing rules below — the{" "}
            <span className="font-medium text-slate-800">first route it matches</span> by priority, then
            Delegation-of-Authority gates and the NDA fast-lane. The role above is the fallback when none match.
          </>
        )}
      </p>
      {active.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-brand-200/60 pt-2">
          {active.slice(0, 6).map((r) => {
            const isPinned = r.id === pinnedId;
            return (
              <li
                key={r.id}
                className={`flex items-baseline gap-2 ${pinnedId && !isPinned ? "opacity-40" : ""}`}
              >
                <span className="font-medium text-slate-800">
                  {isPinned && "✓ "}
                  {r.name}
                </span>
                <span className="text-slate-400">{critSummary(r.criteria)}</span>
                <span className="ml-auto truncate font-mono text-[10px] text-slate-500">{chainSummary(r)}</span>
              </li>
            );
          })}
          {active.length > 6 && <li className="text-slate-400">+{active.length - 6} more route{active.length - 6 === 1 ? "" : "s"}…</li>}
        </ul>
      )}
      {rules && active.length === 0 && (
        <p className="mt-2 border-t border-brand-200/60 pt-2 text-slate-500">
          No active routing rules — approvals will use the fallback role above. Add rules to route by value or type.
        </p>
      )}
      <Link href="/approvals" className="mt-2 inline-flex items-center gap-1 font-medium text-brand-700 hover:underline">
        Manage approval routing <ChevronRight className="h-3 w-3" />
      </Link>
    </div>
  );
}

type StepRow = { id?: string; type: FlowStepType; name: string; config: Record<string, unknown> };

export default function WorkflowEditorPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const isNew = id === "new";

  const { data: flow, isLoading, error } = useQuery({
    queryKey: ["flows", id],
    queryFn: () => flowsApi.getFlow(id),
    enabled: !isNew,
  });

  if (!isNew && isLoading) return <CenterSpinner label="Loading workflow…" />;
  if (!isNew && error) return <ErrorState error={error} />;

  return <Editor flow={isNew ? null : flow ?? null} />;
}

function Editor({ flow }: { flow: Flow | null }) {
  const qc = useQueryClient();
  const router = useRouter();
  const { notify } = useToast();

  const [name, setName] = useState(flow?.name ?? "");
  const [description, setDescription] = useState(flow?.description ?? "");
  const [evalOrder, setEvalOrder] = useState(String(flow?.eval_order ?? 100));
  const [enabled, setEnabled] = useState(flow?.enabled ?? true);
  const [matchType, setMatchType] = useState(flow?.criteria.match_type ?? "");
  const [matchPriority, setMatchPriority] = useState(flow?.criteria.match_priority ?? "");
  const [matchKeyword, setMatchKeyword] = useState(flow?.criteria.match_keyword ?? "");
  const [steps, setSteps] = useState<StepRow[]>(
    (flow?.steps ?? []).map((s) => ({ id: s.id, type: s.type, name: s.name, config: s.config })),
  );
  const [busy, setBusy] = useState(false);

  function setStep(i: number, patch: Partial<StepRow>) {
    setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, ...patch } : s)));
  }
  function setConfig(i: number, patch: Record<string, unknown>) {
    setSteps((prev) =>
      prev.map((s, idx) => {
        if (idx !== i) return s;
        const config = { ...s.config, ...patch };
        for (const k of Object.keys(patch)) {
          if (patch[k] === undefined) delete config[k];
        }
        return { ...s, config };
      }),
    );
  }
  function addStep(step: StepRow) {
    setSteps((prev) => [...prev, step]);
  }
  function removeStep(i: number) {
    setSteps((prev) => prev.filter((_, idx) => idx !== i));
  }
  function move(i: number, dir: -1 | 1) {
    setSteps((prev) => {
      const next = [...prev];
      const j = i + dir;
      if (j < 0 || j >= next.length) return prev;
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const body = {
        name: name.trim(),
        description: description.trim() || null,
        enabled,
        eval_order: Number(evalOrder) || 0,
        criteria: {
          match_type: matchType.trim() || null,
          match_priority: matchPriority.trim() || null,
          match_keyword: matchKeyword.trim() || null,
        },
        steps: steps
          .filter((s) => s.name.trim())
          .map((s) => ({
            ...(s.id ? { id: s.id } : {}),
            type: s.type,
            name: s.name.trim(),
            config: s.config ?? {},
          })),
      };
      if (flow) await flowsApi.updateFlow(flow.id, body);
      else await flowsApi.createFlow(body);
      qc.invalidateQueries({ queryKey: ["flows"] });
      notify(flow ? "Workflow updated" : "Workflow created", "success");
      router.push("/workflow-builder");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Save failed", "error");
    } finally {
      setBusy(false);
    }
  }

  // At-a-glance stats.
  const humanCount = steps.filter((s) => s.type === "human_task" || s.type === "approval").length;
  const aiCount = steps.filter((s) => s.type === "ai_task").length;
  const endsInSignature = steps.length > 0 && steps[steps.length - 1].type === "signature";
  const totalSla = steps.reduce((sum, s) => {
    const h = Number(s.config.sla_hours);
    return sum + (Number.isFinite(h) ? h : 0);
  }, 0);

  return (
    <div className="space-y-5">
      {/* Sticky top bar */}
      <div className="sticky top-0 z-10 -mx-4 flex items-center gap-3 border-b border-slate-200 bg-slate-50/90 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
        <Link
          href="/workflow-builder"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-slate-500 transition-colors hover:text-slate-900"
        >
          <ArrowLeft className="h-4 w-4" />
          Workflows
        </Link>
        <h1 className="flex-1 truncate text-[20px] font-semibold leading-tight tracking-[-0.01em] text-slate-900">
          {name.trim() || (flow ? flow.name : "New workflow")}
        </h1>
        <Button variant="outline" onClick={() => router.push("/workflow-builder")}>
          Cancel
        </Button>
        <Button onClick={submit} loading={busy} disabled={!name.trim()}>
          {flow ? "Save changes" : "Create workflow"}
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[360px_1fr]">
        {/* LEFT: settings */}
        <div className="space-y-4 lg:sticky lg:top-20 lg:self-start">
          <Card>
            <CardHeader>
              <CardTitle>Settings</CardTitle>
            </CardHeader>
            <CardBody className="space-y-4">
              <Field label="Name">
                <Input
                  value={name}
                  placeholder="e.g. Standard NDA pipeline"
                  onChange={(e) => setName(e.target.value)}
                />
              </Field>
              <Field label="Description">
                <Input
                  value={description ?? ""}
                  placeholder="Optional"
                  onChange={(e) => setDescription(e.target.value)}
                />
              </Field>
              <Field label="Evaluation order" hint="Lower wins when multiple workflows match.">
                <Input
                  type="number"
                  value={evalOrder}
                  onChange={(e) => setEvalOrder(e.target.value)}
                />
              </Field>
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                />
                Enabled
              </label>
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Match criteria</CardTitle>
            </CardHeader>
            <CardBody className="space-y-3">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="Request type">
                  <Input
                    value={matchType ?? ""}
                    placeholder="Any"
                    onChange={(e) => setMatchType(e.target.value)}
                  />
                </Field>
                <Field label="Priority">
                  <Input
                    value={matchPriority ?? ""}
                    placeholder="Any"
                    onChange={(e) => setMatchPriority(e.target.value)}
                  />
                </Field>
              </div>
              <Field label="Keyword">
                <Input
                  value={matchKeyword ?? ""}
                  placeholder="Any"
                  onChange={(e) => setMatchKeyword(e.target.value)}
                />
              </Field>
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>At a glance</CardTitle>
            </CardHeader>
            <CardBody className="grid grid-cols-2 gap-2">
              <Stat label="Total steps" value={String(steps.length)} />
              <Stat label="Human steps" value={String(humanCount)} />
              <Stat label="AI / agent" value={String(aiCount)} />
              <Stat label="Ends in signature" value={endsInSignature ? "Yes" : "No"} />
              <div className="col-span-2">
                <Stat label="Rough total SLA" value={totalSla > 0 ? formatSla(totalSla) : "—"} />
              </div>
            </CardBody>
          </Card>
        </div>

        {/* RIGHT: the ladder */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-slate-700">
              Steps ({steps.length})
            </span>
            <span className="text-xs text-slate-400">Run in order, top to bottom</span>
          </div>

          {steps.length === 0 ? (
            <Card className="animate-fade-in">
              <CardBody className="flex flex-col items-center gap-3 py-12 text-center">
                <div className="flex h-11 w-11 items-center justify-center rounded-full bg-slate-100 text-slate-400 ring-1 ring-slate-200">
                  <Workflow className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-slate-900">Add your first step</p>
                  <p className="text-xs text-slate-500">
                    Build the ladder from a blank step or a preset.
                  </p>
                </div>
                <AddRow addStep={addStep} />
              </CardBody>
            </Card>
          ) : (
            <div className="space-y-0">
              {steps.map((s, i) => (
                <LadderStep
                  key={s.id ?? i}
                  step={s}
                  index={i}
                  total={steps.length}
                  onPatch={(patch) => setStep(i, patch)}
                  onConfig={(patch) => setConfig(i, patch)}
                  onMove={(dir) => move(i, dir)}
                  onRemove={() => removeStep(i)}
                />
              ))}
            </div>
          )}

          {steps.length > 0 && <AddRow addStep={addStep} />}
        </div>
      </div>
    </div>
  );
}

function formatSla(hours: number): string {
  if (hours >= 24) {
    const days = Math.round((hours / 24) * 10) / 10;
    return `≈ ${days} day${days === 1 ? "" : "s"}`;
  }
  return `≈ ${hours}h`;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
      <div className="text-lg font-semibold text-slate-900 tabular-nums">{value}</div>
      <div className="text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">{label}</div>
    </div>
  );
}

function AddRow({ addStep }: { addStep: (s: StepRow) => void }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => addStep({ type: "human_task", name: "", config: {} })}
        >
          <User className="h-4 w-4" />
          Human step
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => addStep({ type: "ai_task", name: "", config: {} })}
        >
          <Sparkles className="h-4 w-4" />
          Agent step
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">presets:</span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() =>
            addStep({ type: "human_task", name: "Legal Review", config: { approver_role: "legal_counsel" } })
          }
        >
          <Plus className="h-4 w-4" />
          Legal Review
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() =>
            addStep({ type: "approval", name: "GC Approval", config: { approver_role: "gc", sla_hours: 72 } })
          }
        >
          <Plus className="h-4 w-4" />
          GC Approval
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => addStep({ type: "clm_draft", name: "Draft contract", config: { mode: "template" } })}
        >
          <Plus className="h-4 w-4" />
          Draft contract
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => addStep({ type: "signature", name: "E-signature", config: {} })}
        >
          <Plus className="h-4 w-4" />
          E-signature
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() =>
            addStep({ type: "human_task", name: "Requester Submit", config: { approver_role: "legal_ops" } })
          }
        >
          <Plus className="h-4 w-4" />
          Requester Submit
        </Button>
      </div>
    </div>
  );
}

function RoleSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Select role…</option>
      {ROLE_OPTIONS.map((r) => (
        <option key={r} value={r}>
          {titleCase(r.replace("_", " "))}
        </option>
      ))}
    </Select>
  );
}

function StepControls({
  index,
  total,
  onMove,
  onRemove,
}: {
  index: number;
  total: number;
  onMove: (dir: -1 | 1) => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex shrink-0 items-center">
      <button
        type="button"
        className="rounded p-1 text-slate-400 transition-colors hover:text-slate-700 disabled:opacity-30"
        disabled={index === 0}
        onClick={() => onMove(-1)}
        aria-label="Move step up"
      >
        <ArrowUp className="h-4 w-4" />
      </button>
      <button
        type="button"
        className="rounded p-1 text-slate-400 transition-colors hover:text-slate-700 disabled:opacity-30"
        disabled={index === total - 1}
        onClick={() => onMove(1)}
        aria-label="Move step down"
      >
        <ArrowDown className="h-4 w-4" />
      </button>
      <button
        type="button"
        className="rounded p-1 text-slate-400 transition-colors hover:text-danger"
        onClick={onRemove}
        aria-label="Remove step"
      >
        <Trash2 className="h-4 w-4" />
      </button>
    </div>
  );
}

function SkipWhen({
  config,
  onConfig,
}: {
  config: Record<string, unknown>;
  onConfig: (patch: Record<string, unknown>) => void;
}) {
  const [open, setOpen] = useState(false);
  const skip = (config.skip_when ?? {}) as Record<string, unknown>;

  function setSkip(patch: Record<string, unknown>) {
    const next: Record<string, unknown> = { ...skip };
    for (const [k, v] of Object.entries(patch)) {
      if (v === "" || v === undefined) delete next[k];
      else next[k] = v;
    }
    onConfig({ skip_when: Object.keys(next).length ? next : undefined });
  }

  return (
    <div className="mt-2">
      <button
        type="button"
        className="flex items-center gap-1 text-xs font-medium text-slate-500 transition-colors hover:text-slate-700"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        Skip this step when… (optional)
      </button>
      {/* Height/opacity collapse transition. */}
      <div
        className={`grid transition-all duration-200 ease-out ${
          open ? "mt-2 grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        }`}
      >
        <div className="overflow-hidden">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <Input
              value={str(skip.match_type)}
              placeholder="type"
              onChange={(e) => setSkip({ match_type: e.target.value })}
            />
            <Input
              value={str(skip.match_priority)}
              placeholder="priority"
              onChange={(e) => setSkip({ match_priority: e.target.value })}
            />
            <Input
              value={str(skip.match_keyword)}
              placeholder="keyword"
              onChange={(e) => setSkip({ match_keyword: e.target.value })}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function LadderStep({
  step,
  index,
  total,
  onPatch,
  onConfig,
  onMove,
  onRemove,
}: {
  step: StepRow;
  index: number;
  total: number;
  onPatch: (patch: Partial<StepRow>) => void;
  onConfig: (patch: Record<string, unknown>) => void;
  onMove: (dir: -1 | 1) => void;
  onRemove: () => void;
}) {
  const isToggle = step.type === "human_task" || step.type === "ai_task";
  const isAgent = step.type === "ai_task";
  const style = STEP_STYLE[step.type];
  const Icon = style.icon;
  const isLast = index === total - 1;
  // The approval step is special: it hands off to the whole approval ladder
  // (routing rules → DoA gates → fast-lane), so it gets a solid, sealed node.
  const isApproval = step.type === "approval";

  return (
    <div className="animate-rise-in flex gap-3">
      {/* Node column: numbered circle + connector line */}
      <div className="flex flex-col items-center">
        <div
          className={`group/node relative flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-transform duration-150 hover:-translate-y-0.5 ${
            isApproval
              ? "bg-brand-600 text-white ring-2 ring-brand-200 shadow-sm"
              : `ring-2 ${style.ring} ${style.bg} ${style.text}`
          }`}
          title={isApproval ? "Approval ladder — runs your routing chain" : titleCase(step.type.replace("_", " "))}
        >
          <Icon className="h-4 w-4" />
          {isApproval && (
            <span
              className="absolute -right-1 -top-1 grid h-4 w-4 place-items-center rounded-full bg-warning text-[8px] font-bold leading-none text-white ring-2 ring-slate-50"
              title="Delegates to the approval ladder"
            >
              ◆
            </span>
          )}
        </div>
        {!isLast && <div className="w-px flex-1 bg-slate-200" />}
      </div>

      {/* Step card */}
      <div className="mb-4 flex-1 rounded-md border border-slate-200 bg-slate-50 p-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-slate-400">{index + 1}</span>
          {isToggle ? (
            <div className="inline-flex shrink-0 rounded-md border border-slate-200 bg-slate-100 p-0.5 text-xs font-medium">
              <button
                type="button"
                className={`flex items-center gap-1 rounded px-2 py-1 transition-colors ${
                  !isAgent ? "bg-slate-50 text-slate-900 shadow-sm" : "text-slate-500"
                }`}
                onClick={() => onPatch({ type: "human_task" })}
              >
                <User className="h-3.5 w-3.5" />
                Human
              </button>
              <button
                type="button"
                className={`flex items-center gap-1 rounded px-2 py-1 transition-colors ${
                  isAgent ? "bg-slate-50 text-slate-900 shadow-sm" : "text-slate-500"
                }`}
                onClick={() => onPatch({ type: "ai_task" })}
              >
                <Sparkles className="h-3.5 w-3.5" />
                Agent
              </button>
            </div>
          ) : (
            <span className={`inline-flex items-center gap-1 text-xs font-semibold ${style.text}`}>
              {isApproval && <span className="text-warning">◆</span>}
              {isApproval ? "Approval ladder" : titleCase(step.type.replace("_", " "))}
            </span>
          )}
          <Input
            className="flex-1"
            value={step.name}
            placeholder="Step name"
            onChange={(e) => onPatch({ name: e.target.value })}
          />
          <StepControls index={index} total={total} onMove={onMove} onRemove={onRemove} />
        </div>

        <div className="mt-2">
          {step.type === "approval" && (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <Field
                className="sm:col-span-2"
                label="Approval route"
                hint="Which route this step runs. Auto picks the first matching route by priority; pin one to force it."
              >
                <RoutingRuleSelect
                  value={str(step.config.routing_rule_id)}
                  onChange={(v) => onConfig({ routing_rule_id: v })}
                />
              </Field>
              <Field label="Fallback approver role" hint="Used only when Auto matches no route">
                <RoleSelect
                  value={str(step.config.approver_role)}
                  onChange={(v) => onConfig({ approver_role: v || undefined })}
                />
              </Field>
              <Field label="SLA hours">
                <Input
                  type="number"
                  value={str(step.config.sla_hours)}
                  onChange={(e) =>
                    onConfig({ sla_hours: e.target.value === "" ? undefined : Number(e.target.value) })
                  }
                />
              </Field>
              <div className="sm:col-span-2">
                <ApprovalRoutingHint pinnedId={str(step.config.routing_rule_id) || undefined} />
              </div>
            </div>
          )}
          {step.type === "clm_draft" && (
            <Field label="Mode">
              <Select
                value={str(step.config.mode) || "template"}
                onChange={(e) => onConfig({ mode: e.target.value })}
              >
                <option value="template">Template</option>
                <option value="attachment">Attachment</option>
              </Select>
            </Field>
          )}
          {step.type === "notify" && (
            <Field label="Message">
              <Input
                value={str(step.config.message)}
                placeholder="Notification message"
                onChange={(e) => onConfig({ message: e.target.value || undefined })}
              />
            </Field>
          )}
          {step.type === "ai_task" && (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <Field label="Agent">
                <Select
                  value={str(step.config.agent)}
                  onChange={(e) => onConfig({ agent: e.target.value || undefined })}
                >
                  <option value="">Select agent…</option>
                  {AGENT_OPTIONS.map((a) => (
                    <option key={a.value} value={a.value}>
                      {a.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="SLA hours">
                <Input
                  type="number"
                  value={str(step.config.sla_hours)}
                  onChange={(e) =>
                    onConfig({ sla_hours: e.target.value === "" ? undefined : Number(e.target.value) })
                  }
                />
              </Field>
              <Field label="Escalates to role">
                <RoleSelect
                  value={str(step.config.escalate_role)}
                  onChange={(v) => onConfig({ escalate_role: v || undefined })}
                />
              </Field>
              <Field
                label="Escalate below confidence"
                hint="Below this, a human on the role above decides."
              >
                <Input
                  type="number"
                  step={0.05}
                  min={0}
                  max={1}
                  value={str(step.config.escalate_below_confidence)}
                  onChange={(e) =>
                    onConfig({
                      escalate_below_confidence:
                        e.target.value === "" ? undefined : Number(e.target.value),
                    })
                  }
                />
              </Field>
            </div>
          )}
          {step.type === "human_task" && (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <Field label="Approver role">
                <RoleSelect
                  value={str(step.config.approver_role)}
                  onChange={(v) => onConfig({ approver_role: v || undefined })}
                />
              </Field>
              <Field label="SLA hours">
                <Input
                  type="number"
                  value={str(step.config.sla_hours)}
                  onChange={(e) =>
                    onConfig({ sla_hours: e.target.value === "" ? undefined : Number(e.target.value) })
                  }
                />
              </Field>
            </div>
          )}
          {isToggle && <SkipWhen config={step.config} onConfig={onConfig} />}
        </div>
      </div>
    </div>
  );
}
