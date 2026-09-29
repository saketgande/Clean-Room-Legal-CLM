"use client";

// Chains tab (feature 004) — lists condition-driven, materialized approval
// chain instances (FR-5) with status/module filters, and opens the full
// per-step breakdown (_chain-detail.tsx) in a modal when a row is selected.

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { GitBranch, ShieldAlert } from "lucide-react";
import { approvalChainsApi } from "@/lib/endpoints";
import type { ChainInstanceStatus, ChainSubjectType } from "@/lib/types";
import {
  Badge,
  CenterSpinner,
  EmptyState,
  ErrorState,
  Modal,
  Select,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { fmtDateTime, statusTone, titleCase } from "@/lib/utils";
import { ChainDetail } from "./_chain-detail";

const STATUS_OPTIONS: ChainInstanceStatus[] = [
  "pending",
  "approved",
  "rejected",
  "cancelled",
];
const MODULE_OPTIONS: ChainSubjectType[] = ["contract", "intake_request"];

export function ChainsTab() {
  const [status, setStatus] = useState<ChainInstanceStatus | "">("");
  const [module, setModule] = useState<ChainSubjectType | "">("");
  const [selected, setSelected] = useState<{ id: string; label: string } | null>(
    null,
  );

  const { data, isLoading, error } = useQuery({
    queryKey: ["approval-chain-instances", { status, module }],
    queryFn: () =>
      approvalChainsApi.instances({
        status: status || undefined,
        module: module || undefined,
      }),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Select
          value={status}
          onChange={(e) => setStatus(e.target.value as ChainInstanceStatus | "")}
          className="w-auto min-w-[9rem]"
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {titleCase(s)}
            </option>
          ))}
        </Select>
        <Select
          value={module}
          onChange={(e) => setModule(e.target.value as ChainSubjectType | "")}
          className="w-auto min-w-[9rem]"
          aria-label="Filter by module"
        >
          <option value="">All modules</option>
          {MODULE_OPTIONS.map((m) => (
            <option key={m} value={m}>
              {m === "contract" ? "Contract" : "Intake request"}
            </option>
          ))}
        </Select>
      </div>

      {isLoading ? (
        <CenterSpinner label="Loading approval chains…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={<GitBranch className="h-6 w-6" />}
          title="No approval chains"
          description="Chains started for new contract or intake-request submissions will appear here."
        />
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Record</TH>
              <TH>Definition</TH>
              <TH>Current step</TH>
              <TH>Status</TH>
              <TH>Blocked</TH>
              <TH>Updated</TH>
            </TR>
          </THead>
          <tbody>
            {(data ?? []).map((inst) => (
              <TR
                key={inst.id}
                className="cursor-pointer"
                onClick={() =>
                  setSelected({ id: inst.id, label: inst.module_record_label })
                }
              >
                <TD className="font-medium text-slate-900">
                  {inst.module_record_label}
                </TD>
                <TD>{inst.definition_name}</TD>
                <TD>{inst.current_step_key ?? "—"}</TD>
                <TD>
                  <Badge tone={statusTone(inst.status)}>
                    {titleCase(inst.status)}
                  </Badge>
                </TD>
                <TD>
                  {inst.is_blocked ? (
                    <Badge tone="red">
                      <ShieldAlert className="h-3 w-3" />
                      Blocked
                    </Badge>
                  ) : (
                    <span className="text-slate-400">—</span>
                  )}
                </TD>
                <TD>{fmtDateTime(inst.updated_at)}</TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}

      <Modal
        open={!!selected}
        onClose={() => setSelected(null)}
        title={selected ? selected.label : "Chain detail"}
        size="xl"
      >
        {selected && <ChainDetail instanceId={selected.id} />}
      </Modal>
    </div>
  );
}
