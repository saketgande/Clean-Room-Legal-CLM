"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, GitBranch, Plus, Send } from "lucide-react";
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
} from "@/components/ui";
import { fmtDate, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { ApprovalRequest } from "@/lib/types";

export default function ApprovalsPage() {
  const [tab, setTab] = useState("requests");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Approvals"
        description="Route contracts for sign-off and manage approval routing rules."
      />
      <Tabs
        tabs={[
          { id: "requests", label: "Requests" },
          { id: "rules", label: "Routing rules" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "requests" ? <RequestsTab /> : <RulesTab />}
    </div>
  );
}

// ---- Requests ------------------------------------------------------------
function RequestsTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
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
        <CenterSpinner label="Loading approvals…" />
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
                <TH>Status</TH>
                <TH>Approver</TH>
                <TH>Due</TH>
                <TH className="text-right">Actions</TH>
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
                  <TD>
                    {req.approver_role
                      ? titleCase(req.approver_role)
                      : req.approver_user_id ?? "—"}
                  </TD>
                  <TD>{fmtDate(req.due_at)}</TD>
                  <TD className="text-right">
                    {req.status === "pending" ? (
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
  const [approverRole, setApproverRole] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!contractId) return;
    setBusy(true);
    try {
      await approvalsApi.submit({
        contract_id: contractId,
        approver_role: approverRole || undefined,
      });
      setContractId("");
      setApproverRole("");
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
        <Field label="Approver role" hint="Optional — routing rules apply if blank">
          <Input
            placeholder="e.g. legal_counsel"
            value={approverRole}
            onChange={(e) => setApproverRole(e.target.value)}
          />
        </Field>
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
        <CenterSpinner label="Loading routing rules…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={<GitBranch className="h-6 w-6" />}
          title="No routing rules"
          description="Create a rule to automatically route approvals to the right approver."
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
                <TH>Approver role</TH>
                <TH>Active</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((rule) => (
                <TR key={rule.id}>
                  <TD className="font-medium text-slate-900">{rule.name}</TD>
                  <TD>{rule.priority}</TD>
                  <TD>
                    {rule.approver_role
                      ? titleCase(rule.approver_role)
                      : rule.approver_user_id ?? "—"}
                  </TD>
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
  const [approverRole, setApproverRole] = useState("");
  const [approverUserId, setApproverUserId] = useState("");
  const [isActive, setIsActive] = useState(true);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await approvalsApi.createRoutingRule({
        name: name.trim(),
        priority: Number(priority) || 0,
        approver_role: approverRole || undefined,
        approver_user_id: approverUserId || undefined,
        is_active: isActive,
        criteria: {},
      });
      setName("");
      setPriority("100");
      setApproverRole("");
      setApproverUserId("");
      setIsActive(true);
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
      title="New routing rule"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!name.trim()}>
            Create rule
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Priority" hint="Lower numbers are evaluated first.">
          <Input
            type="number"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
          />
        </Field>
        <Field label="Approver role">
          <Input
            placeholder="e.g. legal_counsel"
            value={approverRole}
            onChange={(e) => setApproverRole(e.target.value)}
          />
        </Field>
        <Field label="Approver user ID" hint="Optional — overrides role.">
          <Input
            value={approverUserId}
            onChange={(e) => setApproverUserId(e.target.value)}
          />
        </Field>
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
