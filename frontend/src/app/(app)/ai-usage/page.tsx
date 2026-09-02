"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  BarChart3,
  Coins,
  Cpu,
  DollarSign,
} from "lucide-react";
import { aiUsageApi } from "@/lib/endpoints";
import {
  Badge,
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
import type { AiUsageSummary } from "@/lib/types";

const usd = (n: number) =>
  n >= 1
    ? `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : `$${n.toFixed(4)}`;
const num = (n: number) => n.toLocaleString();
const compact = (n: number) =>
  n >= 1_000_000
    ? `${(n / 1_000_000).toFixed(1)}M`
    : n >= 1_000
      ? `${(n / 1_000).toFixed(1)}k`
      : String(n);

const CATEGORY_TONE: Record<string, "blue" | "amber" | "green" | "slate"> = {
  "Raise a request": "blue",
  Redlining: "amber",
  "Ask Aegis / Chat": "green",
  "Analysis & extraction": "slate",
};

const WINDOWS = [7, 30, 90] as const;

export default function AiUsagePage() {
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(30);
  const { data, isLoading, error } = useQuery<AiUsageSummary>({
    queryKey: ["ai-usage", days],
    queryFn: () => aiUsageApi.summary(days),
  });

  const maxDaily = Math.max(1, ...(data?.daily ?? []).map((d) => d.cost));
  const empty = !isLoading && !error && (data?.totals.calls ?? 0) === 0;

  return (
    <div className="space-y-4">
      <PageHeader
        title="AI Usage & Cost"
        description="Every AI operation, priced from the token ledger. Sonnet 4.5 at $3 / $15 per million input / output tokens."
        actions={
          <div className="flex gap-1 rounded-lg border border-slate-200 p-0.5">
            {WINDOWS.map((w) => (
              <button
                key={w}
                onClick={() => setDays(w)}
                className={`rounded-md px-3 py-1 text-xs font-semibold transition ${
                  days === w
                    ? "bg-brand-600 text-white"
                    : "text-slate-500 hover:text-slate-800"
                }`}
              >
                {w}d
              </button>
            ))}
          </div>
        }
      />

      {isLoading ? (
        <CenterSpinner label="Loading AI usage…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : empty ? (
        <EmptyState
          icon={<Activity className="h-6 w-6" />}
          title="No AI activity yet"
          description={`No AI calls were logged in the last ${days} days.`}
        />
      ) : data ? (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard
              label={`Total spend (${days}d)`}
              value={usd(data.totals.cost)}
              tone="green"
              icon={<DollarSign className="h-5 w-5" />}
            />
            <StatCard
              label="AI calls"
              value={num(data.totals.calls)}
              tone="blue"
              icon={<Cpu className="h-5 w-5" />}
            />
            <StatCard
              label="Tokens"
              value={compact(data.totals.total_tokens)}
              tone="slate"
              icon={<Coins className="h-5 w-5" />}
            />
            <StatCard
              label="Avg cost / call"
              value={usd(data.totals.cost_per_call)}
              tone="amber"
              icon={<BarChart3 className="h-5 w-5" />}
            />
          </div>

          {/* Cost by user-facing action */}
          <Card className="p-5">
            <h3 className="mb-3 text-sm font-semibold text-slate-800">
              Cost by action
            </h3>
            <div className="space-y-3">
              {data.by_category.map((c) => {
                const share = data.totals.cost
                  ? (c.cost / data.totals.cost) * 100
                  : 0;
                return (
                  <div key={c.category}>
                    <div className="mb-1 flex items-baseline justify-between text-sm">
                      <span className="font-medium text-slate-700">
                        {c.category}
                      </span>
                      <span className="tabular-nums text-slate-500">
                        {usd(c.cost)}{" "}
                        <span className="text-xs text-slate-400">
                          · {num(c.calls)} calls · {share.toFixed(0)}%
                        </span>
                      </span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                      <div
                        className="h-full rounded-full bg-brand-600"
                        style={{ width: `${Math.max(2, share)}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>

          {/* Daily trend */}
          {data.daily.length > 1 && (
            <Card className="p-5">
              <h3 className="mb-3 text-sm font-semibold text-slate-800">
                Daily spend
              </h3>
              <div className="flex h-28 items-end gap-1">
                {data.daily.map((d) => (
                  <div
                    key={d.date}
                    className="flex h-full flex-1 flex-col items-center justify-end"
                    title={`${d.date} · ${usd(d.cost)} · ${num(d.calls)} calls`}
                  >
                    <div
                      className="w-full rounded-t bg-brand-500/80 transition-all hover:bg-brand-600"
                      style={{
                        height: `${Math.max(2, (d.cost / maxDaily) * 100)}%`,
                      }}
                    />
                  </div>
                ))}
              </div>
              <div className="mt-1 flex justify-between text-[11px] text-slate-400">
                <span>{data.daily[0]?.date}</span>
                <span>{data.daily[data.daily.length - 1]?.date}</span>
              </div>
            </Card>
          )}

          {/* Per-operation breakdown */}
          <Card>
            <Table>
              <THead>
                <tr>
                  <TH>Operation</TH>
                  <TH>Action</TH>
                  <TH className="text-right">Calls</TH>
                  <TH className="text-right">Avg tokens (in / out)</TH>
                  <TH className="text-right">Cost / call</TH>
                  <TH className="text-right">Total cost</TH>
                </tr>
              </THead>
              <tbody>
                {data.by_operation.map((op) => (
                  <TR key={`${op.prompt_key}-${op.model}`}>
                    <TD className="font-medium text-slate-900">
                      {op.label}
                      <span className="block font-mono text-[11px] text-slate-400">
                        {op.prompt_key}
                      </span>
                    </TD>
                    <TD>
                      <Badge tone={CATEGORY_TONE[op.category] ?? "slate"}>
                        {op.category}
                      </Badge>
                    </TD>
                    <TD className="text-right tabular-nums">{num(op.calls)}</TD>
                    <TD className="text-right tabular-nums text-slate-500">
                      {num(op.avg_prompt_tokens)} / {num(op.avg_completion_tokens)}
                    </TD>
                    <TD className="text-right tabular-nums">
                      {usd(op.cost_per_call)}
                    </TD>
                    <TD className="text-right font-semibold tabular-nums text-slate-900">
                      {usd(op.cost)}
                    </TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          </Card>

          <p className="px-1 text-xs text-slate-400">
            Costs are derived from logged token counts at list price and exclude
            prompt-caching and batch discounts, so they are an upper bound on
            actual spend.
          </p>
        </>
      ) : null}
    </div>
  );
}
