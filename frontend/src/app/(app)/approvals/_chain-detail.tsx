"use client";

// Chain instance detail (feature 004) — the FR-5/FR-6/FR-7/FR-12/FR-13/FR-20
// surface. Renders the per-step, per-requirement breakdown of a materialized
// approval chain instance, lets an eligible approver decide, lets an
// authorized admin recalculate, and shows the append-only history.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, History as HistoryIcon, RefreshCw, X } from "lucide-react";
import { approvalChainsApi } from "@/lib/endpoints";
import type { ChainHistoryEntry, ChainRequirementResponse } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  CenterSpinner,
  ErrorState,
  Field,
  Input,
  MessageBar,
  Modal,
  Table,
  TD,
  TH,
  THead,
  TR,
  useConfirm,
} from "@/components/ui";
import { useToast } from "@/components/toast";
import { useAuth } from "@/lib/auth";
import { can } from "@/lib/intake";
import { fmtDateTime, titleCase } from "@/lib/utils";
import { requirementTone } from "@/lib/approval-chains";

export function ChainDetail({ instanceId }: { instanceId: string }) {
  const { user } = useAuth();
  const { notify } = useToast();
  const qc = useQueryClient();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [rejectFor, setRejectFor] = useState<ChainRequirementResponse | null>(
    null,
  );
  const [rejectComment, setRejectComment] = useState("");
  const [recalcReason, setRecalcReason] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["approval-chain-instance", instanceId],
    queryFn: () => approvalChainsApi.instance(instanceId),
  });

  const decideMutation = useMutation({
    mutationFn: ({
      requirementId,
      decision,
      comment,
    }: {
      requirementId: string;
      decision: "approve" | "reject";
      comment?: string | null;
    }) => approvalChainsApi.decide(instanceId, requirementId, { decision, comment }),
  });

  const recalculateMutation = useMutation({
    mutationFn: (reason: string) =>
      approvalChainsApi.recalculate(instanceId, { reason: reason || undefined }),
  });

  async function decide(requirement: ChainRequirementResponse, decision: "approve" | "reject", comment?: string) {
    setBusyId(requirement.id);
    try {
      await decideMutation.mutateAsync({ requirementId: requirement.id, decision, comment });
      qc.invalidateQueries({ queryKey: ["approval-chain-instance", instanceId] });
      qc.invalidateQueries({ queryKey: ["approval-chain-instances"] });
      notify(decision === "approve" ? "Approved" : "Rejected", "success");
      setRejectFor(null);
      setRejectComment("");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Decision failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  async function recalculate() {
    const ok = await confirm({
      title: "Recalculate required approvers",
      message:
        "Re-evaluate this chain's condition rules against the item's current data. Already-recorded decisions are never invalidated, but requirements that no longer apply will stop counting toward completion, and any newly-required approver will be added.",
      confirmLabel: "Recalculate",
      tone: "danger",
    });
    if (!ok) return;
    try {
      await recalculateMutation.mutateAsync(recalcReason.trim());
      qc.invalidateQueries({ queryKey: ["approval-chain-instance", instanceId] });
      qc.invalidateQueries({ queryKey: ["approval-chain-instances"] });
      notify("Required approvers recalculated", "success");
      setRecalcReason("");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Recalculate failed", "error");
    }
  }

  if (isLoading) return <CenterSpinner label="Loading chain…" />;
  if (error) return <ErrorState error={error} />;
  if (!data) return <ErrorState error={new Error("Chain instance not found")} />;

  const canRecalculate = data.can_recalculate && can(user, "approval_chain:recalculate");
  const history: ChainHistoryEntry[] = [...data.history].sort(
    (a, b) => new Date(b.acted_at).getTime() - new Date(a.acted_at).getTime(),
  );
  const showDelegatorColumn = history.some((h) => !!h.delegated_from_label);

  return (
    <div className="space-y-4">
      {data.instance.is_blocked && (
        <MessageBar intent="error">
          {data.blocking_visible && data.blocking.length > 0 ? (
            <>
              This chain is blocked: no eligible approver for{" "}
              {data.blocking.map((b) => b.required_role_name).join(", ")}.
            </>
          ) : (
            <>This step is blocked: a required role has no eligible approver. Ask an administrator.</>
          )}
        </MessageBar>
      )}

      {data.steps.map((step) => (
        <Card key={step.step_id}>
          <CardHeader>
            <div className="flex items-center gap-2">
              <CardTitle>{step.name}</CardTitle>
              <Badge tone={step.approval_mode === "sequential" ? "blue" : "violet"}>
                {step.approval_mode === "sequential" ? "Sequential" : "Parallel"}
              </Badge>
              {step.is_current && <Badge tone="amber">Current</Badge>}
              {step.is_complete && <Badge tone="green">Complete</Badge>}
              {step.is_blocked && <Badge tone="red">Blocked</Badge>}
            </div>
          </CardHeader>
          <CardBody className="space-y-3 p-0">
            <Table>
              <THead>
                <TR>
                  <TH>Seq</TH>
                  <TH>Role</TH>
                  <TH>Status</TH>
                  <TH>Origin</TH>
                  <TH>Explanation</TH>
                  <TH className="text-right">Actions</TH>
                </TR>
              </THead>
              <tbody>
                {step.requirements.map((req) => {
                  const tone = requirementTone(req);
                  const badgeTone = tone === "neutral" ? "slate" : tone;
                  return (
                    <TR key={req.id}>
                      <TD>{req.sequence_order}</TD>
                      <TD className="font-medium text-slate-900">{req.required_role_name}</TD>
                      <TD>
                        <Badge tone={badgeTone}>{titleCase(req.status)}</Badge>
                      </TD>
                      <TD>
                        <Badge tone={req.is_base_requirement ? "slate" : "violet"}>
                          {req.is_base_requirement ? "Base requirement" : "Condition-triggered"}
                        </Badge>
                      </TD>
                      <TD className="max-w-xs whitespace-normal text-slate-500">
                        {req.explanation ?? "—"}
                      </TD>
                      <TD className="text-right">
                        {req.can_decide ? (
                          <div className="flex justify-end gap-2">
                            <Button
                              size="sm"
                              disabled={busyId === req.id || req.blocked_by_sequence}
                              title={
                                req.blocked_by_sequence
                                  ? "An earlier approver on this step has not acted yet"
                                  : undefined
                              }
                              onClick={() => decide(req, "approve")}
                            >
                              <Check className="h-3.5 w-3.5" />
                              Approve
                            </Button>
                            <Button
                              size="sm"
                              variant="danger"
                              disabled={busyId === req.id || req.blocked_by_sequence}
                              title={
                                req.blocked_by_sequence
                                  ? "An earlier approver on this step has not acted yet"
                                  : undefined
                              }
                              onClick={() => setRejectFor(req)}
                            >
                              <X className="h-3.5 w-3.5" />
                              Reject
                            </Button>
                          </div>
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </TD>
                    </TR>
                  );
                })}
              </tbody>
            </Table>
          </CardBody>
        </Card>
      ))}

      {canRecalculate && (
        <Card>
          <CardHeader>
            <CardTitle>Recalculate required approvers</CardTitle>
            <RefreshCw className="h-4 w-4 text-slate-400" />
          </CardHeader>
          <CardBody className="space-y-3">
            <Field label="Reason" hint="Optional, but recorded in the append-only history for this action.">
              <Input
                value={recalcReason}
                onChange={(e) => setRecalcReason(e.target.value)}
                placeholder="e.g. contract value corrected after data-entry error"
              />
            </Field>
            <Button variant="danger" onClick={recalculate}>
              <RefreshCw className="h-3.5 w-3.5" />
              Recalculate required approvers
            </Button>
          </CardBody>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>History</CardTitle>
          <HistoryIcon className="h-4 w-4 text-slate-400" />
        </CardHeader>
        <CardBody className="p-0">
          {history.length === 0 ? (
            <p className="p-5 text-[13px] text-slate-500">No history yet.</p>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Action</TH>
                  <TH>Actor</TH>
                  {showDelegatorColumn && <TH>Delegator</TH>}
                  <TH>Comments</TH>
                  <TH>When</TH>
                </TR>
              </THead>
              <tbody>
                {history.map((h) => (
                  <TR
                    key={h.id}
                    className={h.action === "recalculated" ? "bg-warning-subtle/40" : undefined}
                  >
                    <TD>
                      <Badge tone={h.action === "recalculated" ? "amber" : "slate"}>
                        {h.action === "recalculated" && <AlertTriangle className="h-3 w-3" />}
                        {titleCase(h.action)}
                      </Badge>
                    </TD>
                    <TD>{h.acted_by_label ?? "—"}</TD>
                    {showDelegatorColumn && <TD>{h.delegated_from_label ?? "—"}</TD>}
                    <TD className="max-w-xs whitespace-normal">{h.comments ?? "—"}</TD>
                    <TD>{fmtDateTime(h.acted_at)}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </CardBody>
      </Card>

      <Modal
        open={!!rejectFor}
        onClose={() => {
          setRejectFor(null);
          setRejectComment("");
        }}
        title="Reject requirement"
        size="sm"
        footer={
          <>
            <Button variant="outline" onClick={() => setRejectFor(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              disabled={!rejectComment.trim() || busyId === rejectFor?.id}
              onClick={() => rejectFor && decide(rejectFor, "reject", rejectComment.trim())}
            >
              Reject
            </Button>
          </>
        }
      >
        <Field label="Comment" hint="A comment is required to reject.">
          <Input
            autoFocus
            value={rejectComment}
            onChange={(e) => setRejectComment(e.target.value)}
            placeholder="Why is this being rejected?"
          />
        </Field>
      </Modal>

      {confirmDialog}
    </div>
  );
}
