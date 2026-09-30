"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus, Share2 } from "lucide-react";
import { delegationsApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  CenterSpinner,
  EmptyState,
  ErrorState,
  PageHeader,
  Table,
  TD,
  TH,
  THead,
  TR,
  Tabs,
  useConfirm,
} from "@/components/ui";
import { fmtDate, statusTone, titleCase } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import { can } from "@/lib/intake";
import { useToast } from "@/components/toast";
import type { DelegationResponse } from "@/lib/types";
import { DelegationModal } from "./_delegation-modal";

export default function DelegationsPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState("mine");
  const [modalOpen, setModalOpen] = useState(false);
  const qc = useQueryClient();
  const { notify } = useToast();

  if (!can(user, "delegation:manage")) {
    return (
      <div className="mx-auto max-w-[1280px] space-y-4">
        <PageHeader
          title="Delegations"
          description="Delegate your role/org-unit eligibility to a colleague for a bounded window."
        />
        <EmptyState
          icon={<Lock className="h-6 w-6" />}
          title="Access restricted"
          description="You don't have permission to manage delegations. Contact an admin if you need access."
        />
      </div>
    );
  }

  const isAdmin = can(user, "admin_panel:access");

  const tabs = [
    { id: "mine", label: "Delegated by me" },
    { id: "received", label: "Delegated to me" },
    ...(isAdmin ? [{ id: "all", label: "All delegations" }] : []),
  ];

  function refresh() {
    qc.invalidateQueries({ queryKey: ["delegations"] });
  }

  return (
    <div className="mx-auto max-w-[1280px] space-y-4">
      <PageHeader
        title="Delegations"
        description="Delegate your role/org-unit eligibility to a colleague for a bounded window."
        actions={
          <Button onClick={() => setModalOpen(true)}>
            <Plus className="h-4 w-4" />
            New delegation
          </Button>
        }
      />
      <Tabs tabs={tabs} active={tab} onChange={setTab} />
      {tab === "mine" && (
        <DelegationsTable
          direction="mine"
          allowRevoke
          emptyTitle="No delegations created"
          emptyDescription="Delegations you create appear here — you can revoke them any time before their end date."
          onChanged={refresh}
        />
      )}
      {tab === "received" && (
        <DelegationsTable
          direction="received"
          allowRevoke={false}
          emptyTitle="No delegations received"
          emptyDescription="Delegations a colleague creates in your favor appear here. You cannot revoke a delegation made to you."
          onChanged={refresh}
        />
      )}
      {tab === "all" && isAdmin && (
        <DelegationsTable
          direction="all"
          allowRevoke
          emptyTitle="No delegations in your organization"
          emptyDescription="Every active or past delegation in your organization appears here."
          onChanged={refresh}
        />
      )}

      <DelegationModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={() => {
          refresh();
          setModalOpen(false);
          notify("Delegation created", "success");
        }}
      />
    </div>
  );
}

function DelegationsTable({
  direction,
  allowRevoke,
  emptyTitle,
  emptyDescription,
  onChanged,
}: {
  direction: "mine" | "received" | "all";
  // Whether this view is even eligible to show a Revoke control at all.
  // "Delegated to me" passes false so the delegate genuinely has no revoke
  // control rendered anywhere, per AC-23/FR-18 — not merely hidden/disabled
  // per-row, but structurally absent from this view's table.
  allowRevoke: boolean;
  emptyTitle: string;
  emptyDescription: string;
  onChanged: () => void;
}) {
  const { notify } = useToast();
  const { confirm, dialog } = useConfirm();
  const [busyId, setBusyId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["delegations", direction],
    queryFn: () => delegationsApi.list(direction),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => delegationsApi.revoke(id),
  });

  async function revoke(d: DelegationResponse) {
    const ok = await confirm({
      title: "Revoke delegation",
      message: `Revoke the delegation to ${d.delegate_label}? This stops granting access immediately.`,
      confirmLabel: "Revoke",
      tone: "danger",
    });
    if (!ok) return;
    setBusyId(d.id);
    try {
      await revokeMutation.mutateAsync(d.id);
      onChanged();
      notify("Delegation revoked", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Revoke failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {direction === "mine"
            ? "Delegated by me"
            : direction === "received"
              ? "Delegated to me"
              : "All delegations"}
        </CardTitle>
        <Share2 className="h-4 w-4 text-slate-400" />
      </CardHeader>
      <CardBody className="p-0">
        {isLoading ? (
          <CenterSpinner label="Loading delegations…" />
        ) : error ? (
          <div className="p-5">
            <ErrorState error={error} />
          </div>
        ) : (data ?? []).length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={<Share2 className="h-6 w-6" />}
              title={emptyTitle}
              description={emptyDescription}
            />
          </div>
        ) : (
          <Table>
            <THead>
              <tr>
                {direction === "all" && <TH>Delegator</TH>}
                <TH>Delegate</TH>
                <TH>Role</TH>
                <TH>Org unit</TH>
                <TH>Window</TH>
                <TH>Status</TH>
                {allowRevoke && <TH className="text-right">Actions</TH>}
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((d) => (
                <TR key={d.id}>
                  {direction === "all" && (
                    <TD className="font-medium text-slate-900">
                      {d.delegator_label}
                    </TD>
                  )}
                  <TD className="font-medium text-slate-900">
                    {d.delegate_label}
                  </TD>
                  <TD>{d.role_name ? titleCase(d.role_name) : "All roles held"}</TD>
                  <TD>{d.org_unit_name ?? "All org units held"}</TD>
                  <TD className="text-slate-600">
                    {fmtDate(d.start_date)} – {fmtDate(d.end_date)}
                  </TD>
                  <TD>
                    <Badge tone={statusTone(d.status)}>
                      {titleCase(d.status)}
                    </Badge>
                    {d.is_active && d.status === "active" && (
                      <Badge tone="green" className="ml-1">
                        Active now
                      </Badge>
                    )}
                  </TD>
                  {allowRevoke && (
                    <TD className="text-right">
                      {d.can_revoke ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          loading={busyId === d.id}
                          onClick={() => revoke(d)}
                        >
                          Revoke
                        </Button>
                      ) : (
                        <span className="text-xs text-slate-400">—</span>
                      )}
                    </TD>
                  )}
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </CardBody>
      {dialog}
    </Card>
  );
}
