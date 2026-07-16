"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  GitBranch,
  History,
  Pencil,
  Plus,
  Send,
  Trash2,
  Users,
} from "lucide-react";
import { approvalsApi, contractsApi } from "@/lib/endpoints";
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
  Tabs,
  SkeletonRows,
} from "@/components/ui";
import { fmtDate, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import { useAuth } from "@/lib/auth";
import type {
  ApprovalRequest,
  ApprovalRoutingRule,
  RoutingPreviewStep,
} from "@/lib/types";

export default function ApprovalsPage() {
  const [tab, setTab] = useState("requests");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Approvals"
        description="Route contracts through a multi-step sign-off chain and manage approver groups."
      />
      <Tabs
        tabs={[
          { id: "requests", label: "Requests" },
          { id: "rules", label: "Routing rules" },
          { id: "groups", label: "Approver groups" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "requests" ? (
        <RequestsTab />
      ) : tab === "rules" ? (
        <RulesTab />
      ) : (
        <GroupsTab />
      )}
    </div>
  );
}

// ---- Requests ------------------------------------------------------------
function RequestsTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { user } = useAuth();
  const [submitOpen, setSubmitOpen] = useState(false);
  const [rejectFor, setRejectFor] = useState<ApprovalRequest | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["approvals"],
    queryFn: approvalsApi.list,
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
  const userRoles = useMemo(
    () => new Set([...(user?.roles ?? []), user?.active_role_name ?? ""].filter(Boolean)),
    [user],
  );

  function canDecide(req: ApprovalRequest) {
    // Prefer the server's verdict (it knows approver-group membership).
    if (req.can_decide !== undefined) return req.can_decide;
    if (!user || req.requested_by_user_id === user.id) return false;
    if (userRoles.has("admin")) return true;
    if (req.approver_user_id && req.approver_user_id === user.id) return true;
    return !!req.approver_role && userRoles.has(req.approver_role);
  }

  async function decide(
    req: ApprovalRequest,
    decision: "approve" | "reject",
    comment?: string,
  ) {
    setBusyId(req.id);
    try {
      await approvalsApi.decide(req.id, decision, comment);
      qc.invalidateQueries({ queryKey: ["approvals"] });
      notify(`Approval ${decision === "approve" ? "approved" : "rejected"}`, "success");
      setRejectFor(null);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Decision failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button onClick={() => setSubmitOpen(true)}>
          <Send className="h-4 w-4" />
          Submit for approval
        </Button>
      </div>

      {isLoading ? (
        <SkeletonRows rows={5} />
      ) : error ? (
        <ErrorState error={error} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={<CheckCircle2 className="h-6 w-6" />}
          title="No approval requests"
          description="Submit a contract for approval to start the sign-off process."
          action={
            <Button onClick={() => setSubmitOpen(true)}>
              <Send className="h-4 w-4" />
              Submit for approval
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Contract</TH>
                <TH>Step</TH>
                <TH>Status</TH>
                <TH>Approver</TH>
                <TH>Due</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <tbody>
              {[...(data ?? [])]
                .sort((a, b) => Number(b.overdue ?? false) - Number(a.overdue ?? false))
                .map((req) => (
                <TR key={req.id}>
                  <TD className="font-medium">
                    <Link
                      href={`/contracts/${req.contract_id}`}
                      className="text-brand-700 hover:underline"
                    >
                      {titleMap.get(req.contract_id) ?? req.contract_id}
                    </Link>
                  </TD>
                  <TD>{req.step_order ? `Step ${req.step_order}` : "—"}</TD>
                  <TD>
                    <span className="flex items-center gap-1.5">
                      <Badge tone={statusTone(req.status)}>{titleCase(req.status)}</Badge>
                      {req.overdue && <Badge tone="red">Overdue</Badge>}
                    </span>
                  </TD>
                  <TD>
                    {req.approver_role
                      ? titleCase(req.approver_role)
                      : req.approver_group_id
                        ? "Group"
                        : req.approver_user_id ?? "—"}
                  </TD>
                  <TD className={req.overdue ? "font-semibold text-rose-600" : undefined}>
                    {fmtDate(req.due_at)}
                  </TD>
                  <TD className="text-right">
                    {req.status === "pending" && canDecide(req) ? (
                      <div className="flex justify-end gap-2">
                        <Button
                          size="sm"
                          loading={busyId === req.id}
                          onClick={() => decide(req, "approve")}
                        >
                          Approve
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          disabled={busyId === req.id}
                          onClick={() => setRejectFor(req)}
                        >
                          Reject
                        </Button>
                      </div>
                    ) : (
                      <span className="text-xs text-slate-400">—</span>
                    )}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <SubmitModal
        open={submitOpen}
        onClose={() => setSubmitOpen(false)}
        contracts={contracts ?? []}
        onSubmitted={() => {
          qc.invalidateQueries({ queryKey: ["approvals"] });
          notify("Submitted for approval", "success");
          setSubmitOpen(false);
        }}
      />

      <RejectModal
        request={rejectFor}
        onClose={() => setRejectFor(null)}
        busy={busyId === rejectFor?.id}
        onReject={(comment) => rejectFor && decide(rejectFor, "reject", comment)}
      />
    </div>
  );
}

// Collapse a resolved chain into ordered stage groups (steps sharing a stage
// run in parallel).
function groupByStage(
  chain: RoutingPreviewStep[],
): { stage: number; steps: RoutingPreviewStep[] }[] {
  const groups: { stage: number; steps: RoutingPreviewStep[] }[] = [];
  for (const step of chain) {
    const stage = step.stage ?? step.step_order;
    const last = groups[groups.length - 1];
    if (last && last.stage === stage) last.steps.push(step);
    else groups.push({ stage, steps: [step] });
  }
  return groups;
}

function StepPill({ step }: { step: RoutingPreviewStep }) {
  const conditional = !!step.condition && step.condition.length > 0;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 ${
        step.skipped ? "border-dashed border-slate-300" : "border-slate-200 bg-slate-50"
      }`}
    >
      <span
        className={`flex h-5 w-5 items-center justify-center rounded text-[10px] font-bold tabular-nums ${
          step.skipped ? "bg-slate-200 text-slate-500" : "bg-indigo-600 text-white"
        }`}
      >
        {step.stage ?? step.step_order}
      </span>
      <span
        className={`text-xs font-semibold ${
          step.skipped ? "text-slate-400 line-through" : "text-slate-800"
        }`}
      >
        {step.approver_label}
      </span>
      {step.mode === "all" && !step.skipped && <Badge tone="slate">all</Badge>}
      {conditional && !step.skipped && (
        <span className="text-[9px] font-semibold uppercase text-amber-600" title="Conditional step">
          if
        </span>
      )}
      {step.skipped && (
        <span className="text-[9px] font-medium uppercase text-slate-400">skipped</span>
      )}
    </span>
  );
}

// Dry-run visualization: the chain a contract would get before it's submitted.
function ChainPreview({ contractId }: { contractId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["routing-preview", contractId],
    queryFn: () => approvalsApi.routingPreview(contractId),
    enabled: !!contractId,
  });

  if (!contractId) return null;
  if (isLoading)
    return <p className="text-xs text-slate-400">Resolving the chain…</p>;
  if (error) return <ErrorState error={error} />;
  if (!data) return null;

  if (data.fast_lane_reason) {
    return (
      <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-xs text-emerald-800">
        <span className="font-semibold">⚡ Fast-lane</span> — skips approval,
        straight to signature. {data.fast_lane_reason}.
      </div>
    );
  }
  if (data.chain.length === 0) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-800">
        No routing rule matches — this falls back to a manually-chosen approver.
      </div>
    );
  }

  const shadowed = data.matched_rules.filter((r) => !r.used);
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500">
        <GitBranch className="h-3.5 w-3.5" />
        Chain preview
        <span className="text-slate-400">
          · {data.compose ? "composed from" : "best of"}{" "}
          {data.matched_rules.length} matched rule
          {data.matched_rules.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {groupByStage(data.chain).map((grp, gi) => (
          <div key={grp.stage} className="flex items-center gap-1.5">
            {gi > 0 && <span className="text-slate-300">→</span>}
            {grp.steps.length > 1 ? (
              <div className="flex flex-col gap-1 rounded-lg border border-dashed border-slate-300 p-1">
                <span className="px-1 text-[9px] font-medium uppercase tracking-wide text-slate-400">
                  parallel · all clear
                </span>
                {grp.steps.map((step) => (
                  <StepPill key={step.step_order} step={step} />
                ))}
              </div>
            ) : (
              <StepPill step={grp.steps[0]} />
            )}
          </div>
        ))}
      </div>
      {shadowed.length > 0 && (
        <p className="text-[11px] text-slate-400">
          Already covered by an earlier step:{" "}
          {shadowed.map((r) => r.name).join(", ")}
        </p>
      )}
    </div>
  );
}

function SubmitModal({
  open,
  onClose,
  contracts,
  onSubmitted,
}: {
  open: boolean;
  onClose: () => void;
  contracts: { id: string; title: string }[];
  onSubmitted: () => void;
}) {
  const { notify } = useToast();
  const [contractId, setContractId] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!contractId) return;
    setBusy(true);
    try {
      await approvalsApi.submit({ contract_id: contractId });
      setContractId("");
      onSubmitted();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Submit failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Submit for approval"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!contractId}>
            Submit
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Contract">
          <Select value={contractId} onChange={(e) => setContractId(e.target.value)}>
            <option value="">Select a contract…</option>
            {contracts.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </Select>
        </Field>
        <ChainPreview contractId={contractId} />
        <p className="text-xs text-slate-500">
          Every matching routing rule contributes to the chain above. The first
          step is notified now; later steps activate automatically as each one
          approves.
        </p>
      </div>
    </Modal>
  );
}

function RejectModal({
  request,
  onClose,
  onReject,
  busy,
}: {
  request: ApprovalRequest | null;
  onClose: () => void;
  onReject: (comment: string) => void;
  busy: boolean;
}) {
  const [comment, setComment] = useState("");

  useEffect(() => {
    setComment("");
  }, [request?.id]);

  return (
    <Modal
      open={!!request}
      onClose={onClose}
      title="Reject approval"
      size="sm"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="danger"
            loading={busy}
            disabled={!comment.trim()}
            onClick={() => onReject(comment.trim())}
          >
            Reject
          </Button>
        </>
      }
    >
      <Field label="Reason" hint="A comment is required to reject.">
        <Input
          autoFocus
          placeholder="Why is this being rejected?"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
      </Field>
    </Modal>
  );
}

// ---- Routing rules -------------------------------------------------------
function chainSummary(rule: {
  steps: {
    step_order: number;
    stage?: number | null;
    condition?: Array<Record<string, unknown>> | null;
    approver_group_name?: string | null;
    approver_user_name?: string | null;
    approver_role: string | null;
  }[];
  approver_role: string | null;
  approver_user_id: string | null;
}): string {
  if (rule.steps && rule.steps.length) {
    const label = (s: (typeof rule.steps)[number]) =>
      (s.approver_group_name ||
        s.approver_user_name ||
        (s.approver_role ? titleCase(s.approver_role) : "?")) +
      (s.condition && s.condition.length ? "?" : "");
    // Group by stage; steps in the same stage are parallel (joined with ∥).
    const sorted = [...rule.steps].sort(
      (a, b) => (a.stage ?? a.step_order) - (b.stage ?? b.step_order) || a.step_order - b.step_order,
    );
    const stages: { stage: number; labels: string[] }[] = [];
    for (const s of sorted) {
      const st = s.stage ?? s.step_order;
      const last = stages[stages.length - 1];
      if (last && last.stage === st) last.labels.push(label(s));
      else stages.push({ stage: st, labels: [label(s)] });
    }
    return stages.map((g) => g.labels.join("  ∥  ")).join("  →  ");
  }
  if (rule.approver_role) return titleCase(rule.approver_role);
  return rule.approver_user_id ?? "—";
}

// One-line summary of a rule's WHEN conditions for the rules table.
function criteriaSummary(criteria: Record<string, unknown> | null): string {
  const conds = criteriaToConditions(criteria);
  if (!conds.length) return "Any contract";
  const fieldLabel = (f: string) => COND_FIELDS.find((x) => x.v === f)?.label ?? f;
  const opLabel = (f: string, op: string) =>
    opsFor(f).find((o) => o.v === op)?.label ?? op;
  return conds
    .map((c) =>
      `${fieldLabel(c.field)} ${opLabel(c.field, c.op)} ${c.op === "exists" ? "" : c.value}`.trim(),
    )
    .join(" · ");
}

function RoutingAuditPanel() {
  const { data } = useQuery({
    queryKey: ["routing-rule-audit"],
    queryFn: approvalsApi.routingRuleAudit,
  });
  const entries = data ?? [];
  if (!entries.length) return null;
  const verb: Record<string, string> = {
    "routing_rule.created": "created",
    "routing_rule.updated": "edited",
    "routing_rule.reordered": "reordered the rules",
    "routing_rule.deleted": "deleted",
  };
  return (
    <Card>
      <div className="flex items-center gap-2 border-b border-slate-100 px-5 py-3">
        <History className="h-4 w-4 text-slate-400" />
        <span className="text-sm font-semibold text-slate-800">Routing audit trail</span>
      </div>
      <ul className="divide-y divide-slate-100">
        {entries.slice(0, 8).map((e) => {
          const ruleName =
            (e.after as { name?: string } | null)?.name ??
            (e.before as { name?: string } | null)?.name;
          return (
            <li
              key={e.id}
              className="flex items-center justify-between gap-3 px-5 py-2.5 text-sm"
            >
              <span className="text-slate-700">
                <b className="font-semibold">{e.actor_name}</b> {verb[e.action] ?? e.action}
                {ruleName && e.action !== "routing_rule.reordered" ? (
                  <span className="text-slate-500"> “{ruleName}”</span>
                ) : null}
              </span>
              <span className="shrink-0 text-xs text-slate-400">{fmtDate(e.created_at)}</span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function RulesTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [modalOpen, setModalOpen] = useState(false);
  const [editRule, setEditRule] = useState<ApprovalRoutingRule | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ApprovalRoutingRule | null>(null);
  const [reordering, setReordering] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["approval-routing-rules"],
    queryFn: approvalsApi.routingRules,
  });
  const rules = useMemo(
    () =>
      [...(data ?? [])].sort(
        (a, b) => (Number(a.priority) || 0) - (Number(b.priority) || 0),
      ),
    [data],
  );

  function invalidate() {
    qc.invalidateQueries({ queryKey: ["approval-routing-rules"] });
    qc.invalidateQueries({ queryKey: ["routing-rule-audit"] });
  }

  function payloadFrom(
    rule: ApprovalRoutingRule,
    overrides: { is_active?: boolean } = {},
  ) {
    return {
      name: rule.name,
      priority: rule.priority,
      is_active: overrides.is_active ?? rule.is_active,
      criteria: rule.criteria,
      steps: rule.steps.map((s) => ({
        approver_group_id: s.approver_group_id ?? undefined,
        approver_user_id: s.approver_user_id ?? undefined,
        approver_role: s.approver_role ?? undefined,
        mode: s.mode,
      })),
      approver_role: rule.approver_role ?? undefined,
      approver_user_id: rule.approver_user_id ?? undefined,
    };
  }

  async function toggleActive(rule: ApprovalRoutingRule) {
    try {
      await approvalsApi.updateRoutingRule(
        rule.id,
        payloadFrom(rule, { is_active: !rule.is_active }),
      );
      invalidate();
      notify(rule.is_active ? "Rule deactivated" : "Rule activated", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "error");
    }
  }

  async function moveRule(i: number, dir: -1 | 1) {
    const j = i + dir;
    if (j < 0 || j >= rules.length || reordering) return;
    const next = [...rules];
    [next[i], next[j]] = [next[j], next[i]];
    setReordering(true);
    try {
      await approvalsApi.reorderRoutingRules(next.map((r) => r.id));
      invalidate();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Reorder failed", "error");
    } finally {
      setReordering(false);
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    try {
      await approvalsApi.deleteRoutingRule(deleteTarget.id);
      invalidate();
      notify("Rule deleted", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Delete failed", "error");
    } finally {
      setDeleteTarget(null);
    }
  }

  const openCreate = () => {
    setEditRule(null);
    setModalOpen(true);
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4" />
          New rule
        </Button>
      </div>

      {isLoading ? (
        <SkeletonRows rows={4} />
      ) : error ? (
        <ErrorState error={error} />
      ) : rules.length === 0 ? (
        <EmptyState
          icon={<GitBranch className="h-6 w-6" />}
          title="No routing rules"
          description="Create a rule to route approvals through an ordered chain of approvers."
          action={
            <Button onClick={openCreate}>
              <Plus className="h-4 w-4" />
              New rule
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Order</TH>
                <TH>Name</TH>
                <TH>Applies when</TH>
                <TH>Approval chain</TH>
                <TH>Active</TH>
                <TH>Actions</TH>
              </tr>
            </THead>
            <tbody>
              {rules.map((rule, i) => (
                <TR key={rule.id}>
                  <TD>
                    <div className="flex flex-col">
                      <button
                        type="button"
                        className="rounded p-0.5 text-slate-400 hover:text-slate-700 disabled:opacity-30"
                        disabled={i === 0 || reordering}
                        onClick={() => moveRule(i, -1)}
                        aria-label="Higher priority"
                      >
                        <ArrowUp className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        className="rounded p-0.5 text-slate-400 hover:text-slate-700 disabled:opacity-30"
                        disabled={i === rules.length - 1 || reordering}
                        onClick={() => moveRule(i, 1)}
                        aria-label="Lower priority"
                      >
                        <ArrowDown className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </TD>
                  <TD className="font-medium text-slate-900">
                    {rule.name}
                    <div className="text-xs font-normal text-slate-400">
                      priority {rule.priority}
                    </div>
                  </TD>
                  <TD className="max-w-[220px] text-xs text-slate-600">
                    {criteriaSummary(rule.criteria)}
                  </TD>
                  <TD className="text-slate-700">{chainSummary(rule)}</TD>
                  <TD>
                    <button
                      type="button"
                      onClick={() => toggleActive(rule)}
                      title="Toggle active"
                      className="cursor-pointer"
                    >
                      <Badge tone={rule.is_active ? "green" : "slate"}>
                        {rule.is_active ? "Active" : "Inactive"}
                      </Badge>
                    </button>
                  </TD>
                  <TD>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        className="rounded p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                        onClick={() => {
                          setEditRule(rule);
                          setModalOpen(true);
                        }}
                        aria-label="Edit rule"
                      >
                        <Pencil className="h-4 w-4" />
                      </button>
                      <button
                        type="button"
                        className="rounded p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600"
                        onClick={() => setDeleteTarget(rule)}
                        aria-label="Delete rule"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <RoutingAuditPanel />

      <RuleModal
        open={modalOpen}
        rule={editRule}
        onClose={() => setModalOpen(false)}
        onSaved={(verb) => {
          invalidate();
          notify(`Routing rule ${verb}`, "success");
          setModalOpen(false);
        }}
      />

      <Modal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title="Delete routing rule"
        size="sm"
        footer={
          <>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={confirmDelete}>
              Delete rule
            </Button>
          </>
        }
      >
        <p className="text-sm text-slate-600">
          Delete <b className="text-slate-900">{deleteTarget?.name}</b>? It stops routing new
          contracts immediately. Approvals already in flight are unaffected, and the change is
          recorded in the audit trail.
        </p>
      </Modal>
    </div>
  );
}

// ---- WHEN→THEN condition builder ----------------------------------------
type Condition = { field: string; op: string; value: string };

const COND_FIELDS: { v: string; label: string; numeric?: boolean }[] = [
  { v: "value_amount", label: "Contract value", numeric: true },
  { v: "contract_type", label: "Contract type" },
  { v: "risk_band", label: "Risk band" },
  { v: "risk_score", label: "Risk score", numeric: true },
  { v: "counterparty_name", label: "Counterparty" },
  { v: "jurisdiction", label: "Jurisdiction" },
  { v: "currency", label: "Currency" },
];
const NUMERIC_OPS = [
  { v: "gte", label: "≥" },
  { v: "lte", label: "≤" },
  { v: "gt", label: ">" },
  { v: "lt", label: "<" },
  { v: "eq", label: "=" },
  { v: "ne", label: "≠" },
];
const STRING_OPS = [
  { v: "eq", label: "is" },
  { v: "ne", label: "is not" },
  { v: "in", label: "is any of" },
  { v: "contains", label: "contains" },
  { v: "exists", label: "is set" },
];
const isNumericField = (f: string) =>
  COND_FIELDS.find((c) => c.v === f)?.numeric ?? false;
const opsFor = (f: string) => (isNumericField(f) ? NUMERIC_OPS : STRING_OPS);

function buildCriteria(conditions: Condition[]): Record<string, unknown> {
  const conds = conditions
    .filter((c) => c.field && (c.op === "exists" || c.value.trim() !== ""))
    .map((c) => {
      let value: unknown = c.value.trim();
      if (c.op === "exists") value = null;
      else if (c.op === "in")
        value = c.value.split(",").map((s) => s.trim()).filter(Boolean);
      else if (isNumericField(c.field)) value = Number(c.value);
      return { field: c.field, op: c.op, value };
    });
  return conds.length ? { conditions: conds } : {};
}

// Live "does this match?" tester against a hypothetical contract — Design 2's
// trust affordance, so an admin sees a rule fire before saving it.
function RuleTester({ criteria }: { criteria: Record<string, unknown> }) {
  const [value, setValue] = useState("500000");
  const [type, setType] = useState("dpa");
  const [risk, setRisk] = useState("high");
  const sample = {
    value_amount: Number(value) || 0,
    contract_type: type,
    risk_band: risk,
    risk_score: risk === "high" ? 80 : risk === "critical" ? 95 : 40,
  };
  const { data } = useQuery({
    queryKey: ["rule-preview", JSON.stringify(criteria), JSON.stringify(sample)],
    queryFn: () => approvalsApi.previewCriteria(criteria, sample),
  });
  const hasConds = Array.isArray(
    (criteria as { conditions?: unknown[] }).conditions,
  );
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
          Test against a sample
        </span>
        {hasConds &&
          (data?.matches ? (
            <Badge tone="green">✓ matches</Badge>
          ) : (
            <Badge tone="red">✗ no match</Badge>
          ))}
      </div>
      <div className="grid grid-cols-3 gap-2">
        <Input
          type="number"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-label="Sample value"
        />
        <Select value={type} onChange={(e) => setType(e.target.value)} aria-label="Sample type">
          <option value="nda">NDA</option>
          <option value="dpa">DPA</option>
          <option value="msa">MSA</option>
          <option value="saas">SaaS</option>
        </Select>
        <Select value={risk} onChange={(e) => setRisk(e.target.value)} aria-label="Sample risk">
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
          <option value="critical">Critical</option>
        </Select>
      </div>
      {!hasConds && (
        <p className="mt-2 text-[11px] text-slate-400">
          No conditions yet — this rule matches every contract.
        </p>
      )}
    </div>
  );
}

function ConditionBuilder({
  conditions,
  setConditions,
}: {
  conditions: Condition[];
  setConditions: (fn: (prev: Condition[]) => Condition[]) => void;
}) {
  function setCond(i: number, patch: Partial<Condition>) {
    setConditions((prev) =>
      prev.map((c, idx) => {
        if (idx !== i) return c;
        const next = { ...c, ...patch };
        // Keep op valid when the field's type changes.
        if (patch.field && !opsFor(patch.field).some((o) => o.v === next.op)) {
          next.op = opsFor(patch.field)[0].v;
        }
        return next;
      }),
    );
  }
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-700">
          Applies when{" "}
          <span className="font-normal text-slate-400">(all conditions match)</span>
        </span>
        <button
          type="button"
          className="text-xs font-medium text-indigo-600 hover:text-indigo-700"
          onClick={() =>
            setConditions((prev) => [
              ...prev,
              { field: "value_amount", op: "gte", value: "" },
            ])
          }
        >
          + Condition
        </button>
      </div>
      {conditions.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-200 px-3 py-2 text-xs text-slate-400">
          No conditions — this rule matches every contract (a catch-all).
        </p>
      ) : (
        conditions.map((c, i) => (
          <div key={i} className="flex items-center gap-2">
            <Select
              className="min-w-0 flex-1"
              value={c.field}
              onChange={(e) => setCond(i, { field: e.target.value })}
              aria-label="Field"
            >
              {COND_FIELDS.map((f) => (
                <option key={f.v} value={f.v}>
                  {f.label}
                </option>
              ))}
            </Select>
            <Select
              className="w-20 shrink-0"
              value={c.op}
              onChange={(e) => setCond(i, { op: e.target.value })}
              aria-label="Operator"
            >
              {opsFor(c.field).map((o) => (
                <option key={o.v} value={o.v}>
                  {o.label}
                </option>
              ))}
            </Select>
            <Input
              className="min-w-0 flex-1"
              placeholder={c.op === "exists" ? "—" : c.op === "in" ? "a, b, c" : "value"}
              value={c.value}
              disabled={c.op === "exists"}
              onChange={(e) => setCond(i, { value: e.target.value })}
              aria-label="Value"
            />
            <button
              type="button"
              className="rounded p-1 text-slate-400 hover:text-rose-600"
              onClick={() => setConditions((prev) => prev.filter((_, idx) => idx !== i))}
              aria-label="Remove condition"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))
      )}
    </div>
  );
}

// Parse a stored rule's criteria back into editable WHEN→THEN rows — including
// legacy dict criteria (min_value / contract_type / risk_band) so editing an
// old rule doesn't silently drop its conditions.
function criteriaToConditions(criteria: Record<string, unknown> | null): Condition[] {
  if (!criteria) return [];
  const conds = (criteria as { conditions?: unknown }).conditions;
  if (Array.isArray(conds)) {
    return conds.map((c) => {
      const cc = c as { field?: string; op?: string; value?: unknown };
      return {
        field: String(cc.field ?? "value_amount"),
        op: String(cc.op ?? "eq"),
        value: Array.isArray(cc.value)
          ? cc.value.join(", ")
          : cc.value == null
            ? ""
            : String(cc.value),
      };
    });
  }
  const out: Condition[] = [];
  const c = criteria as Record<string, unknown>;
  if ("min_value" in c) out.push({ field: "value_amount", op: "gte", value: String(c.min_value) });
  if ("max_value" in c) out.push({ field: "value_amount", op: "lte", value: String(c.max_value) });
  const ct = c.contract_type ?? c.contract_types;
  if (ct != null)
    out.push({
      field: "contract_type",
      op: Array.isArray(ct) ? "in" : "eq",
      value: Array.isArray(ct) ? ct.join(", ") : String(ct),
    });
  const rb = c.risk_band ?? c.risk_bands;
  if (rb != null)
    out.push({
      field: "risk_band",
      op: Array.isArray(rb) ? "in" : "eq",
      value: Array.isArray(rb) ? rb.join(", ") : String(rb),
    });
  return out;
}

// A step being authored: an approver target, whether it runs in parallel with
// the step above it, and an optional "only if" condition.
type StepDraft = { target: string; parallel: boolean; cond: Condition | null };

function condToPayload(c: Condition): Record<string, unknown> {
  let value: unknown = c.value.trim();
  if (c.op === "exists") value = null;
  else if (c.op === "in") value = c.value.split(",").map((s) => s.trim()).filter(Boolean);
  else if (isNumericField(c.field)) value = Number(c.value);
  return { field: c.field, op: c.op, value };
}

function stepsToDrafts(steps: ApprovalRoutingRule["steps"]): StepDraft[] {
  if (!steps.length) return [{ target: "", parallel: false, cond: null }];
  const sorted = [...steps].sort(
    (a, b) =>
      (a.stage ?? a.step_order) - (b.stage ?? b.step_order) ||
      a.step_order - b.step_order,
  );
  return sorted.map((s, i) => {
    const target = s.approver_group_id
      ? `group:${s.approver_group_id}`
      : s.approver_user_id
        ? `user:${s.approver_user_id}`
        : "";
    const prev = sorted[i - 1];
    const parallel =
      i > 0 && (s.stage ?? s.step_order) === (prev.stage ?? prev.step_order);
    const first = Array.isArray(s.condition) && s.condition.length ? s.condition[0] : null;
    const cond: Condition | null = first
      ? {
          field: String((first as { field?: string }).field ?? "value_amount"),
          op: String((first as { op?: string }).op ?? "eq"),
          value:
            (first as { value?: unknown }).value == null
              ? ""
              : Array.isArray((first as { value?: unknown }).value)
                ? ((first as { value?: unknown[] }).value ?? []).join(", ")
                : String((first as { value?: unknown }).value),
        }
      : null;
    return { target, parallel, cond };
  });
}

function RuleModal({
  open,
  onClose,
  onSaved,
  rule,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (verb: string) => void;
  rule?: ApprovalRoutingRule | null;
}) {
  const { notify } = useToast();
  const [name, setName] = useState("");
  const [priority, setPriority] = useState("100");
  // WHEN→THEN conditions. Empty = the rule matches every contract.
  const [conditions, setConditions] = useState<Condition[]>([]);
  const [isActive, setIsActive] = useState(true);
  const [steps, setSteps] = useState<StepDraft[]>([
    { target: "", parallel: false, cond: null },
  ]);
  const [busy, setBusy] = useState(false);

  const { data: groups } = useQuery({
    queryKey: ["approval-groups"],
    queryFn: approvalsApi.groups,
    enabled: open,
  });
  const { data: people } = useQuery({
    queryKey: ["eligible-approvers"],
    queryFn: approvalsApi.eligibleApprovers,
    enabled: open,
  });

  function reset() {
    setName("");
    setPriority("100");
    setConditions([]);
    setIsActive(true);
    setSteps([{ target: "", parallel: false, cond: null }]);
  }

  // Load the rule's values when editing; clear for a fresh create.
  useEffect(() => {
    if (!open) return;
    if (rule) {
      setName(rule.name);
      setPriority(rule.priority);
      setConditions(criteriaToConditions(rule.criteria));
      setIsActive(rule.is_active);
      setSteps(stepsToDrafts(rule.steps));
    } else {
      reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, rule?.id]);

  const criteria = buildCriteria(conditions);

  function patchStep(i: number, patch: Partial<StepDraft>) {
    setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, ...patch } : s)));
  }
  function addStep() {
    setSteps((prev) => [...prev, { target: "", parallel: false, cond: null }]);
  }
  function removeStep(i: number) {
    setSteps((prev) =>
      prev.length === 1
        ? prev
        : prev
            .filter((_, idx) => idx !== i)
            // the first step can never be "parallel with previous"
            .map((s, idx) => (idx === 0 ? { ...s, parallel: false } : s)),
    );
  }
  function move(i: number, dir: -1 | 1) {
    setSteps((prev) => {
      const next = [...prev];
      const j = i + dir;
      if (j < 0 || j >= next.length) return prev;
      [next[i], next[j]] = [next[j], next[i]];
      if (next[0]) next[0] = { ...next[0], parallel: false };
      return next;
    });
  }

  async function submit() {
    const chosen = steps.filter((s) => s.target);
    if (!name.trim() || chosen.length === 0) return;
    setBusy(true);
    // Turn the parallel flags into stage numbers: a step that isn't parallel
    // with the one above it starts a new stage.
    let stage = 0;
    const payloadSteps = chosen.map((s, i) => {
      if (i === 0 || !s.parallel) stage += 1;
      const [kind, id] = s.target.split(":");
      const base =
        kind === "group" ? { approver_group_id: id } : { approver_user_id: id };
      const cond =
        s.cond && (s.cond.op === "exists" || s.cond.value.trim() !== "")
          ? [condToPayload(s.cond)]
          : null;
      return { ...base, stage, condition: cond };
    });
    const body = {
      name: name.trim(),
      priority: String(Number(priority) || 0),
      is_active: isActive,
      criteria,
      steps: payloadSteps,
    };
    try {
      if (rule) {
        await approvalsApi.updateRoutingRule(rule.id, body);
        onSaved("updated");
      } else {
        await approvalsApi.createRoutingRule(body);
        reset();
        onSaved("created");
      }
    } catch (e) {
      notify(e instanceof Error ? e.message : "Save failed", "error");
    } finally {
      setBusy(false);
    }
  }

  const canSave = !!name.trim() && steps.some((s) => s.target);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={rule ? "Edit routing rule" : "New routing rule"}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!canSave}>
            {rule ? "Save changes" : "Create rule"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name">
          <Input
            value={name}
            placeholder="e.g. Standard contract chain"
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="Priority" hint="Lower wins when rules overlap.">
          <Input
            type="number"
            className="sm:max-w-[140px]"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
          />
        </Field>

        <ConditionBuilder conditions={conditions} setConditions={setConditions} />
        <RuleTester criteria={criteria} />

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-slate-700">Approval steps</span>
            <span className="text-xs text-slate-400">
              Sequential top-to-bottom; mark ∥ to run in parallel
            </span>
          </div>
          {steps.map((s, i) => (
            <div key={i} className="rounded-lg border border-slate-200 bg-slate-50 p-2">
              <div className="flex items-center gap-2">
                <span className="w-12 shrink-0 text-xs font-medium text-slate-500">
                  {i > 0 && s.parallel ? "∥ with" : `Step ${i + 1}`}
                </span>
                <Select
                  className="min-w-0 flex-1"
                  value={s.target}
                  onChange={(e) => patchStep(i, { target: e.target.value })}
                >
                  <option value="">Select approver…</option>
                  <optgroup label="Groups">
                    {(groups ?? []).map((g) => (
                      <option key={g.id} value={`group:${g.id}`}>
                        {g.name}
                        {g.members.length ? ` (${g.members.length})` : " (no members)"}
                      </option>
                    ))}
                  </optgroup>
                  <optgroup label="People">
                    {(people ?? []).map((u) => (
                      <option key={u.id} value={`user:${u.id}`}>
                        {u.full_name} — {u.email}
                      </option>
                    ))}
                  </optgroup>
                </Select>
                <button
                  type="button"
                  className="rounded p-1 text-slate-400 hover:text-slate-700 disabled:opacity-30"
                  disabled={i === 0}
                  onClick={() => move(i, -1)}
                  aria-label="Move step up"
                >
                  <ArrowUp className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  className="rounded p-1 text-slate-400 hover:text-slate-700 disabled:opacity-30"
                  disabled={i === steps.length - 1}
                  onClick={() => move(i, 1)}
                  aria-label="Move step down"
                >
                  <ArrowDown className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  className="rounded p-1 text-slate-400 hover:text-red-600 disabled:opacity-30"
                  disabled={steps.length === 1}
                  onClick={() => removeStep(i)}
                  aria-label="Remove step"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 pl-14 text-xs">
                {i > 0 && (
                  <label className="flex items-center gap-1.5 text-slate-600">
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                      checked={s.parallel}
                      onChange={(e) => patchStep(i, { parallel: e.target.checked })}
                    />
                    Run in parallel with step above
                  </label>
                )}
                <label className="flex items-center gap-1.5 text-slate-600">
                  <input
                    type="checkbox"
                    className="h-3.5 w-3.5 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                    checked={!!s.cond}
                    onChange={(e) =>
                      patchStep(i, {
                        cond: e.target.checked
                          ? { field: "value_amount", op: "gt", value: "" }
                          : null,
                      })
                    }
                  />
                  Only if…
                </label>
                {s.cond && (
                  <div className="flex items-center gap-1.5">
                    <Select
                      className="w-36"
                      value={s.cond.field}
                      onChange={(e) =>
                        patchStep(i, {
                          cond: {
                            ...s.cond!,
                            field: e.target.value,
                            op: opsFor(e.target.value).some((o) => o.v === s.cond!.op)
                              ? s.cond!.op
                              : opsFor(e.target.value)[0].v,
                          },
                        })
                      }
                    >
                      {COND_FIELDS.map((f) => (
                        <option key={f.v} value={f.v}>
                          {f.label}
                        </option>
                      ))}
                    </Select>
                    <Select
                      className="w-16"
                      value={s.cond.op}
                      onChange={(e) => patchStep(i, { cond: { ...s.cond!, op: e.target.value } })}
                    >
                      {opsFor(s.cond.field).map((o) => (
                        <option key={o.v} value={o.v}>
                          {o.label}
                        </option>
                      ))}
                    </Select>
                    <Input
                      className="w-28"
                      placeholder="value"
                      value={s.cond.value}
                      disabled={s.cond.op === "exists"}
                      onChange={(e) => patchStep(i, { cond: { ...s.cond!, value: e.target.value } })}
                    />
                  </div>
                )}
              </div>
            </div>
          ))}
          <Button variant="outline" size="sm" onClick={addStep}>
            <Plus className="h-4 w-4" />
            Add step
          </Button>
        </div>

        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
            checked={isActive}
            onChange={(e) => setIsActive(e.target.checked)}
          />
          Active
        </label>
      </div>
    </Modal>
  );
}

// ---- Approver groups -----------------------------------------------------
function GroupsTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [newOpen, setNewOpen] = useState(false);
  const [membersFor, setMembersFor] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["approval-groups"],
    queryFn: approvalsApi.groups,
  });

  const activeGroup = (data ?? []).find((g) => g.id === membersFor) ?? null;

  function refresh() {
    qc.invalidateQueries({ queryKey: ["approval-groups"] });
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button onClick={() => setNewOpen(true)}>
          <Plus className="h-4 w-4" />
          New group
        </Button>
      </div>

      {isLoading ? (
        <CenterSpinner label="Loading groups…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={<Users className="h-6 w-6" />}
          title="No approver groups"
          description="Create a group (e.g. Legal Counsel) and add the people who can approve for it."
          action={
            <Button onClick={() => setNewOpen(true)}>
              <Plus className="h-4 w-4" />
              New group
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Group</TH>
                <TH>Members</TH>
                <TH>Active</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((g) => (
                <TR key={g.id}>
                  <TD>
                    <div className="font-medium text-slate-900">{g.name}</div>
                    {g.description ? (
                      <div className="text-xs text-slate-500">{g.description}</div>
                    ) : null}
                  </TD>
                  <TD className="text-slate-700">
                    {g.members.length === 0 ? (
                      <span className="text-xs text-amber-600">No members yet</span>
                    ) : (
                      g.members.map((m) => m.full_name).join(", ")
                    )}
                  </TD>
                  <TD>
                    <Badge tone={g.is_active ? "green" : "slate"}>
                      {g.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </TD>
                  <TD className="text-right">
                    <Button size="sm" variant="outline" onClick={() => setMembersFor(g.id)}>
                      <Users className="h-4 w-4" />
                      Members
                    </Button>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <NewGroupModal
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onCreated={() => {
          refresh();
          notify("Group created", "success");
          setNewOpen(false);
        }}
      />
      <ManageMembersModal
        group={activeGroup}
        onClose={() => setMembersFor(null)}
        onSaved={() => {
          refresh();
          notify("Members updated", "success");
          setMembersFor(null);
        }}
      />
    </div>
  );
}

function NewGroupModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { notify } = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await approvalsApi.createGroup({
        name: name.trim(),
        description: description.trim() || undefined,
      });
      setName("");
      setDescription("");
      onCreated();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Create failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New approver group"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!name.trim()}>
            Create group
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name">
          <Input
            placeholder="e.g. Legal Counsel"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="Description" hint="Optional.">
          <Input value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

function ManageMembersModal({
  group,
  onClose,
  onSaved,
}: {
  group: { id: string; name: string; members: { id: string }[] } | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify } = useToast();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const { data: people } = useQuery({
    queryKey: ["eligible-approvers"],
    queryFn: approvalsApi.eligibleApprovers,
    enabled: !!group,
  });

  useEffect(() => {
    setSelected(new Set((group?.members ?? []).map((m) => m.id)));
  }, [group?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function save() {
    if (!group) return;
    setBusy(true);
    try {
      await approvalsApi.setGroupMembers(group.id, [...selected]);
      onSaved();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Save failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={!!group}
      onClose={onClose}
      title={group ? `Members — ${group.name}` : "Members"}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} loading={busy}>
            Save members
          </Button>
        </>
      }
    >
      <div className="space-y-1 max-h-80 overflow-y-auto">
        {(people ?? []).length === 0 ? (
          <p className="text-sm text-slate-500">No active users to add yet.</p>
        ) : (
          (people ?? []).map((u) => (
            <label
              key={u.id}
              className="flex items-center gap-3 rounded-md px-2 py-2 hover:bg-slate-50"
            >
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                checked={selected.has(u.id)}
                onChange={() => toggle(u.id)}
              />
              <span className="text-sm text-slate-800">{u.full_name}</span>
              <span className="text-xs text-slate-400">{u.email}</span>
            </label>
          ))
        )}
      </div>
    </Modal>
  );
}
