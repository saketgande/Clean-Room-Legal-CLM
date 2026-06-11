"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  CheckCircle2,
  Clock,
  Loader2,
  RotateCw,
  XCircle,
} from "lucide-react";
import { jobsApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CenterSpinner,
  EmptyState,
  ErrorState,
  PageHeader,
  StatCard,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { fmtDateTime, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { JobRun, JobStatus } from "@/lib/types";

export default function JobsPage() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busyId, setBusyId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["jobs"],
    queryFn: jobsApi.list,
    refetchInterval: 5000,
  });

  const counts = useMemo(() => {
    const c: Record<JobStatus, number> = {
      queued: 0,
      running: 0,
      succeeded: 0,
      failed: 0,
      cancelled: 0,
    };
    for (const j of data ?? []) c[j.status] += 1;
    return c;
  }, [data]);

  async function act(job: JobRun, kind: "cancel" | "run") {
    setBusyId(job.id);
    try {
      if (kind === "cancel") await jobsApi.cancel(job.id);
      else await jobsApi.run(job.id);
      qc.invalidateQueries({ queryKey: ["jobs"] });
      notify(kind === "cancel" ? "Job cancelled" : "Job re-queued", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Action failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Background jobs"
        description="Async processing — extraction, embeddings, reminders and more."
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard
          label="Queued"
          value={counts.queued}
          tone="amber"
          icon={<Clock className="h-5 w-5" />}
        />
        <StatCard
          label="Running"
          value={counts.running}
          tone="blue"
          icon={<Loader2 className="h-5 w-5" />}
        />
        <StatCard
          label="Succeeded"
          value={counts.succeeded}
          tone="green"
          icon={<CheckCircle2 className="h-5 w-5" />}
        />
        <StatCard
          label="Failed"
          value={counts.failed}
          tone="red"
          icon={<XCircle className="h-5 w-5" />}
        />
        <StatCard
          label="Cancelled"
          value={counts.cancelled}
          tone="slate"
          icon={<XCircle className="h-5 w-5" />}
        />
      </div>

      {isLoading ? (
        <CenterSpinner label="Loading jobs…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={<Activity className="h-6 w-6" />}
          title="No background jobs"
          description="Jobs appear here when contracts are uploaded or AI tasks run."
        />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Job type</TH>
                <TH>Resource</TH>
                <TH>Status</TH>
                <TH>Progress</TH>
                <TH>Attempts</TH>
                <TH>Created</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((job) => {
                const pct = Math.round(
                  Math.max(0, Math.min(1, job.progress)) * 100,
                );
                const canCancel =
                  job.status === "queued" || job.status === "running";
                const canRerun =
                  job.status === "failed" || job.status === "queued";
                return (
                  <TR key={job.id}>
                    <TD className="font-medium text-slate-900">
                      {titleCase(job.job_type)}
                    </TD>
                    <TD>
                      <div className="flex flex-col">
                        <span>{titleCase(job.resource_type)}</span>
                        <span className="font-mono text-xs text-slate-400">
                          {job.resource_id}
                        </span>
                      </div>
                    </TD>
                    <TD>
                      <Badge tone={statusTone(job.status)}>
                        {titleCase(job.status)}
                      </Badge>
                      {job.status === "failed" && job.error_message && (
                        <p
                          title={job.error_message}
                          className="mt-1 max-w-[16rem] truncate text-xs text-red-600"
                        >
                          {job.error_message}
                        </p>
                      )}
                    </TD>
                    <TD>
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-100">
                          <div
                            className="h-full rounded-full bg-brand-600 transition-all"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <span className="text-xs tabular-nums text-slate-500">
                          {pct}%
                        </span>
                      </div>
                    </TD>
                    <TD className="tabular-nums">{job.attempt_count}</TD>
                    <TD>{fmtDateTime(job.created_at)}</TD>
                    <TD className="text-right">
                      {canCancel || canRerun ? (
                        <div className="flex justify-end gap-2">
                          {canCancel && (
                            <Button
                              size="sm"
                              variant="danger"
                              loading={busyId === job.id}
                              onClick={() => act(job, "cancel")}
                            >
                              Cancel
                            </Button>
                          )}
                          {canRerun && (
                            <Button
                              size="sm"
                              variant="outline"
                              loading={busyId === job.id}
                              onClick={() => act(job, "run")}
                            >
                              <RotateCw className="h-3.5 w-3.5" />
                              Re-run
                            </Button>
                          )}
                        </div>
                      ) : (
                        <span className="text-xs text-slate-400">—</span>
                      )}
                    </TD>
                  </TR>
                );
              })}
            </tbody>
          </Table>
        </Card>
      )}
    </div>
  );
}
