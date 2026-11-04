"use client";

import { use, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { trademarksApi } from "@/lib/endpoints";
import {
  Badge,
  Breadcrumbs,
  CenterSpinner,
  ErrorState,
  Field,
  Select,
  Tabs,
} from "@/components/ui";
import { TrademarkComments } from "@/components/TrademarkComments";
import { statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { TrademarkDecision, TrademarkStatus } from "@/lib/types";
import { AuditLogCard } from "./_audit-log-card";
import { OverviewCard } from "./_overview-card";
import { DecisionPanel } from "./_decision-panel";
import { DocumentsPanel } from "./_documents-panel";
import { FilingPanel } from "./_filing-panel";
import { ProgressPanel } from "./_progress-panel";
import { SearchesPanel } from "./_searches-panel";
import { ProgressHeader } from "./_progress-header";

const STATUSES: TrademarkStatus[] = [
  "draft",
  "filed",
  "prosecuting",
  "registered",
  "opposed",
  "renewal_pending",
  "abandoned",
  "lapsed",
  "renewed",
  "examination",
  "published",
  "opposition_period",
  "refused",
  "withdrawn",
];

const DECISION_TONE: Record<TrademarkDecision, "green" | "amber" | "red"> = {
  proceed: "green",
  conditional: "amber",
  reject: "red",
};

export default function TrademarkDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("overview");

  const { data: trademark, isLoading, error } = useQuery({
    queryKey: ["trademark", id],
    queryFn: () => trademarksApi.get(id),
    // The workflow panel moves the case along without touching this query, so
    // keep the step shown in the header current while the case is open.
    refetchInterval: 15_000,
  });

  function openTab(next: string) {
    setTab(next);
    qc.invalidateQueries({ queryKey: ["trademark", id] });
  }

  async function changeStatus(status: TrademarkStatus) {
    setBusy(true);
    try {
      await trademarksApi.update(id, { status });
      qc.invalidateQueries({ queryKey: ["trademark", id] });
      qc.invalidateQueries({ queryKey: ["trademarks"] });
      notify("Status updated", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "error");
    } finally {
      setBusy(false);
    }
  }

  if (isLoading) return <CenterSpinner label="Loading trademark…" />;
  if (error) return <ErrorState error={error} />;
  if (!trademark) return null;

  const trademarkCase = trademark.case;
  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "searches", label: "Searches" },
    ...(trademarkCase
      ? [
          { id: "decision", label: "Decision" },
          { id: "filing", label: "Filing" },
          { id: "documents", label: "Documents" },
        ]
      : []),
    { id: "discussion", label: "Discussion" },
  ];

  return (
    <div className="space-y-4">
      <Breadcrumbs
        items={[
          { label: "Trademarks", href: "/trademarks" },
          { label: trademark.name },
        ]}
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-[20px] font-semibold leading-tight tracking-[-0.01em] text-slate-900">
            {trademark.name}
          </h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Badge tone={statusTone(trademark.status)}>
              {titleCase(trademark.status)}
            </Badge>
            <Badge tone="slate">{titleCase(trademark.trademark_type)}</Badge>
            <Badge tone="slate">{titleCase(trademark.source)}</Badge>
            {trademark.decision && (
              <Badge tone={DECISION_TONE[trademark.decision]}>
                {titleCase(trademark.decision)}
              </Badge>
            )}
          </div>
        </div>
        <Field label="Status" className="w-48">
          <Select
            value={trademark.status}
            disabled={busy}
            onChange={(e) => changeStatus(e.target.value as TrademarkStatus)}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      {trademarkCase && <ProgressHeader trademarkCase={trademarkCase} />}

      <Tabs tabs={tabs} active={tab} onChange={openTab} label="Trademark sections" />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_300px] lg:items-start">
        <div className="min-w-0 space-y-4">
          {tab === "overview" && <OverviewCard trademark={trademark} />}
          {tab === "searches" && <SearchesPanel trademark={trademark} />}
          {tab === "decision" && <DecisionPanel trademark={trademark} />}
          {tab === "filing" && <FilingPanel trademark={trademark} />}
          {tab === "documents" && trademarkCase && <DocumentsPanel requestId={trademarkCase.request_id} />}
          {tab === "discussion" && <TrademarkComments trademarkId={trademark.id} />}
        </div>

        {/* The case's own column, the same on every tab: where the work stands (and the action for the current
            step), then the record of what was done. */}
        <aside className="space-y-4" aria-label="Case progress and audit log">
          {trademarkCase && <ProgressPanel requestId={trademarkCase.request_id} trademarkId={trademark.id} />}
          {/* Each open examination / opposition response is its own workflow run, with its own step card. */}
          {trademark.responses
            .filter((response) => response.run_status !== "complete")
            .map((response) => (
              <ProgressPanel key={response.request_id} requestId={response.request_id} trademarkId={trademark.id} title={response.title} />
            ))}
          <AuditLogCard trademarkId={trademark.id} />
        </aside>
      </div>
    </div>
  );
}
