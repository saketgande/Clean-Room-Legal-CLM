"use client";

// Workflow-engine builder — list org flows. Editing happens on a dedicated
// full-page route (/workflow-builder/[id]); this page is the list + seed.
// Admin-only: workflow definitions are a privileged configuration surface.

import { useState } from "react";
import { Plus, Workflow } from "lucide-react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  SkeletonRows,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { flowsApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import { useAuth } from "@/lib/auth";
import { can } from "@/lib/intake";
import { cn } from "@/lib/utils";
import type { Flow } from "@/lib/types";

// Short label per step type; approval/signature are the "ladder" rungs and get
// emphasised so the tagged approval ladder reads at a glance.
const STEP_SHORT: Record<string, string> = {
  start: "Start",
  ai_task: "AI",
  human_task: "Human",
  clm_draft: "Draft",
  counterparty: "Counterparty",
  approval: "Approval",
  signature: "Signature",
  notify: "Notify",
  end: "End",
};
const isRung = (t: string) => t === "approval" || t === "signature";

function LadderCell({ steps }: { steps: Flow["steps"] }) {
  if (!steps.length) return <span className="text-slate-400">—</span>;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {steps.map((s, i) => (
        <span key={s.id ?? i} className="inline-flex items-center gap-1">
          {i > 0 && <span className="text-slate-300">→</span>}
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-[11px] font-medium",
              isRung(s.type)
                ? "bg-brand-50 text-brand-700 ring-1 ring-brand-200"
                : "bg-slate-100 text-slate-600",
            )}
          >
            {STEP_SHORT[s.type] ?? s.type}
          </span>
        </span>
      ))}
    </div>
  );
}

export default function WorkflowBuilderPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const { notify } = useToast();
  const { user } = useAuth();
  const isAdmin = can(user, "admin_panel:access");
  const [seeding, setSeeding] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["flows"],
    queryFn: flowsApi.listFlows,
    enabled: isAdmin,
  });

  const flows = data ?? [];

  async function seed() {
    setSeeding(true);
    try {
      const res = await flowsApi.seedFlows();
      qc.invalidateQueries({ queryKey: ["flows"] });
      notify(`Seeded ${res.added} default workflow${res.added === 1 ? "" : "s"}`, "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Seed failed", "error");
    } finally {
      setSeeding(false);
    }
  }

  if (!isAdmin) {
    return (
      <div className="space-y-4">
        <PageHeader title="Workflows" description="Automated routing of legal requests through AI tasks, approvals, and signature." />
        <EmptyState title="Access restricted" description="Workflow definitions can only be viewed and edited by administrators." />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Workflows"
        description="Define automated workflows that route legal requests through AI tasks, drafting, approvals, and signature."
      />

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={seed} loading={seeding}>
          Seed defaults
        </Button>
        <Button onClick={() => router.push("/workflow-builder/new")}>
          <Plus className="h-4 w-4" />
          New workflow
        </Button>
      </div>

      {isLoading ? (
        <SkeletonRows rows={4} />
      ) : error ? (
        <ErrorState error={error} />
      ) : flows.length === 0 ? (
        <EmptyState
          icon={<Workflow className="h-6 w-6" />}
          title="No workflows"
          description="Seed the built-in defaults, or create a workflow to automate how requests flow through your team."
          action={
            <Button onClick={seed} loading={seeding}>
              Seed defaults
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Name</TH>
                <TH>Approval ladder</TH>
                <TH>Steps</TH>
                <TH>Enabled</TH>
                <TH></TH>
              </tr>
            </THead>
            <tbody>
              {flows.map((f) => (
                <TR
                  key={f.id}
                  className="cursor-pointer transition-colors hover:bg-slate-100"
                  onClick={() => router.push(`/workflow-builder/${f.id}`)}
                >
                  <TD className="font-medium text-slate-900">
                    <div className="flex items-center gap-2">
                      {f.name}
                      {f.is_builtin && <Badge tone="violet">Built-in</Badge>}
                    </div>
                    {f.description && (
                      <div className="text-xs text-slate-500">{f.description}</div>
                    )}
                  </TD>
                  <TD className="text-slate-700"><LadderCell steps={f.steps} /></TD>
                  <TD>{f.steps.length}</TD>
                  <TD>
                    <Badge tone={f.enabled ? "green" : "slate"}>
                      {f.enabled ? "Enabled" : "Disabled"}
                    </Badge>
                  </TD>
                  <TD>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        router.push(`/workflow-builder/${f.id}`);
                      }}
                    >
                      Edit
                    </Button>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </div>
  );
}
