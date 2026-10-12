"use client";

import { useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
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
import { jobProgressPercent } from "@/lib/job-progress";
import { fmtDateTime, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { JobRun, JobStatus } from "@/lib/types";

const PAGE_SIZE = 100;
const FILTERS: { id: JobStatus | "all"; label: string }[] = [
  { id: "all", label: "All" },
  { id: "queued", label: "Queued" },
  { id: "running", label: "Running" },
  { id: "succeeded", label: "Succeeded" },
  { id: "failed", label: "Failed" },
  { id: "cancelled", label: "Cancelled" },
];

export default function JobsPage() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busyId, setBusyId] = useState<string | null>(null);

  const [filter, setFilter] = useState<JobStatus | "all">("all");

  // The cards come from the server's own count over every job. Counting the rows on screen
  // (only the newest page) made them wrong whenever more than a page of jobs existed.
  const summary = useQuery({
    queryKey: ["jobs", "summary"],
    queryFn: jobsApi.summary,
    refetchInterval: 5000,
  });
  const counts = summary.data;

  const {
    data: pages,
    isLoading,
    error,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["jobs", "list", filter],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      jobsApi.list({
        status: filter === "all" ? undefined : filter,
        limit: PAGE_SIZE,
        offset: pageParam,
      }),
    getNextPageParam: (last, all) =>
      last.length < PAGE_SIZE ? undefined : all.reduce((n, p) => n + p.length, 0),
    refetchInterval: 5000,
  });
  const data = pages?.pages.flat() ?? [];

  async function act(job: JobRun, kind: "cancel" | "run") {
    setBusyId(job.id);
    try {
      if (kind === "cancel") await jobsApi.cancel(job.id);
      else await jobsApi.run(job.id);
      qc.invalidateQueries({ queryKey: ["jobs"] }); // the cards and every list
      notify(kind === "cancel" ? "Job cancelled" : "Job re-queued", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Action failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Background jobs"
        description="Async processing — extraction, embeddings, reminders and more."
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard
          label="Queued"
          value={counts ? counts.queued : "—"}
          tone="amber"
          icon={<Clock className="h-5 w-5" />}
        />
        <StatCard
          label="Running"
          value={counts ? counts.running : "—"}
          tone="blue"
          icon={<Loader2 className="h-5 w-5" />}
        />
        <StatCard
          label="Succeeded"
          value={counts ? counts.succeeded : "—"}
          tone="green"
          icon={<CheckCircle2 className="h-5 w-5" />}
        />
        <StatCard
          label="Failed"
          value={counts ? counts.failed : "—"}
          tone="red"
          icon={<XCircle className="h-5 w-5" />}
        />
        <StatCard
          label="Cancelled"
          value={counts ? counts.cancelled : "—"}
          tone="slate"
          icon={<XCircle className="h-5 w-5" />}
        />
      </div>

      <div className="flex flex-wrap gap-2" role="group" aria-label="Filter jobs by status">
        {FILTERS.map((f) => (
          <Button
            key={f.id}
            size="sm"
            variant={filter === f.id ? "primary" : "outline"}
            aria-pressed={filter === f.id}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
          </Button>
        ))}
      </div>

      {isLoading ? (
        <CenterSpinner label="Loading jobs…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : data.length === 0 ? (
        <EmptyState
          icon={<Activity className="h-6 w-6" />}
          title={filter === "all" ? "No background jobs" : `No ${filter} jobs`}
          description={
            filter === "all"
              ? "Jobs appear here when contracts are uploaded or AI tasks run."
              : "Choose All to see every job."
          }
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
              {data.map((job) => {
                const pct = jobProgressPercent(job.progress);
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
                          className="mt-1 max-w-[16rem] truncate text-xs text-danger"
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
          {hasNextPage && (
            <div className="flex justify-center border-t border-slate-100 p-3">
              <Button
                size="sm"
                variant="outline"
                loading={isFetchingNextPage}
                onClick={() => fetchNextPage()}
              >
                Show older jobs
              </Button>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
