"use client";

// Workflow-engine builder — list org flows. Editing happens on a dedicated
// full-page route (/workflow-builder/[id]); this page is the list + seed.

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
import type { Flow } from "@/lib/types";

function criteriaSummary(c: Flow["criteria"]): string {
  const parts: string[] = [];
  if (c.match_type) parts.push(`type=${c.match_type}`);
  if (c.match_priority) parts.push(`priority=${c.match_priority}`);
  if (c.match_department) parts.push(`dept=${c.match_department}`);
  if (c.match_keyword) parts.push(`keyword="${c.match_keyword}"`);
  return parts.length ? parts.join(", ") : "Any request";
}

export default function WorkflowBuilderPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const { notify } = useToast();
  const [seeding, setSeeding] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["flows"],
    queryFn: flowsApi.listFlows,
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
                <TH>Criteria</TH>
                <TH>Steps</TH>
                <TH>Order</TH>
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
                  <TD className="text-slate-700">{criteriaSummary(f.criteria)}</TD>
                  <TD>{f.steps.length}</TD>
                  <TD>{f.eval_order}</TD>
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
