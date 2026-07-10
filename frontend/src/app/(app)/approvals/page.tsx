"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  GitBranch,
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
import type { ApprovalRequest } from "@/lib/types";

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
        <p className="text-xs text-slate-500">
          The matching routing rule decides the approval chain. The first step is
          notified now; later steps activate automatically as each one approves.
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
  steps: { step_order: number; approver_group_name?: string | null; approver_user_name?: string | null; approver_role: string | null }[];
  approver_role: string | null;
  approver_user_id: string | null;
}): string {
  if (rule.steps && rule.steps.length) {
    return rule.steps
      .slice()
      .sort((a, b) => a.step_order - b.step_order)
      .map(
        (s) =>
          s.approver_group_name ||
          s.approver_user_name ||
          (s.approver_role ? titleCase(s.approver_role) : "?"),
      )
      .join("  →  ");
  }
  if (rule.approver_role) return titleCase(rule.approver_role);
  return rule.approver_user_id ?? "—";
}

function RulesTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [newOpen, setNewOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["approval-routing-rules"],
    queryFn: approvalsApi.routingRules,
  });

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button onClick={() => setNewOpen(true)}>
          <Plus className="h-4 w-4" />
          New rule
        </Button>
      </div>

      {isLoading ? (
        <SkeletonRows rows={4} />
      ) : error ? (
        <ErrorState error={error} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={<GitBranch className="h-6 w-6" />}
          title="No routing rules"
          description="Create a rule to route approvals through an ordered chain of approvers."
          action={
            <Button onClick={() => setNewOpen(true)}>
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
                <TH>Name</TH>
                <TH>Priority</TH>
                <TH>Approval chain</TH>
                <TH>Active</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((rule) => (
                <TR key={rule.id}>
                  <TD className="font-medium text-slate-900">{rule.name}</TD>
                  <TD>{rule.priority}</TD>
                  <TD className="text-slate-700">{chainSummary(rule)}</TD>
                  <TD>
                    <Badge tone={rule.is_active ? "green" : "slate"}>
                      {rule.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <NewRuleModal
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onCreated={() => {
          qc.invalidateQueries({ queryKey: ["approval-routing-rules"] });
          notify("Routing rule created", "success");
          setNewOpen(false);
        }}
      />
    </div>
  );
}

function NewRuleModal({
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
  const [priority, setPriority] = useState("100");
  const [minValue, setMinValue] = useState("");
  const [isActive, setIsActive] = useState(true);
  // Each step holds an encoded target: "group:<id>" or "user:<id>" (or "").
  const [steps, setSteps] = useState<string[]>([""]);
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
    setMinValue("");
    setIsActive(true);
    setSteps([""]);
  }

  function setStep(i: number, value: string) {
    setSteps((prev) => prev.map((s, idx) => (idx === i ? value : s)));
  }
  function addStep() {
    setSteps((prev) => [...prev, ""]);
  }
  function removeStep(i: number) {
    setSteps((prev) => (prev.length === 1 ? prev : prev.filter((_, idx) => idx !== i)));
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
    const chosen = steps.filter(Boolean);
    if (!name.trim() || chosen.length === 0) return;
    setBusy(true);
    try {
      await approvalsApi.createRoutingRule({
        name: name.trim(),
        priority: String(Number(priority) || 0),
        is_active: isActive,
        criteria: minValue ? { min_value: Number(minValue) } : {},
        steps: chosen.map((s) => {
          const [kind, id] = s.split(":");
          return kind === "group"
            ? { approver_group_id: id }
            : { approver_user_id: id };
        }),
      });
      reset();
      onCreated();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Create failed", "error");
    } finally {
      setBusy(false);
    }
  }

  const canSave = !!name.trim() && steps.some(Boolean);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New routing rule"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!canSave}>
            Create rule
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
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Priority" hint="Lower wins when rules overlap.">
            <Input
              type="number"
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
            />
          </Field>
          <Field label="Applies when value ≥" hint="Optional threshold.">
            <Input
              type="number"
              placeholder="Any value"
              value={minValue}
              onChange={(e) => setMinValue(e.target.value)}
            />
          </Field>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-slate-700">Approval steps</span>
            <span className="text-xs text-slate-400">Approved in order, top to bottom</span>
          </div>
          {steps.map((value, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="w-12 shrink-0 text-xs font-medium text-slate-500">
                Step {i + 1}
              </span>
              <Select
                className="flex-1"
                value={value}
                onChange={(e) => setStep(i, e.target.value)}
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
                className="rounded p-1 text-slate-400 hover:text-rose-600 disabled:opacity-30"
                disabled={steps.length === 1}
                onClick={() => removeStep(i)}
                aria-label="Remove step"
              >
                <Trash2 className="h-4 w-4" />
              </button>
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
