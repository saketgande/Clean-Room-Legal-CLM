"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, RefreshCw } from "lucide-react";
import { trademarksApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  CenterSpinner,
  ErrorState,
  PageHeader,
  Select,
  StatCard,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";

function csvEscape(value: string): string {
  if (/[",\n]/.test(value)) return `"${value.replace(/"/g, '""')}"`;
  return value;
}

export default function TrademarkReportsPage() {
  const queryClient = useQueryClient();
  const { notify } = useToast();
  const [exportStatus, setExportStatus] = useState("All");
  const [regenerating, setRegenerating] = useState(false);

  const { data: stats, isLoading: statsLoading, error: statsError } = useQuery({
    queryKey: ["trademarks", "reports", "stats"],
    queryFn: () => trademarksApi.portfolioStats(),
  });
  const { data: digest, isLoading: digestLoading, error: digestError } = useQuery({
    queryKey: ["trademarks", "reports", "digest"],
    queryFn: () => trademarksApi.digest(),
  });
  const { data: atRisk, isLoading: atRiskLoading, error: atRiskError } = useQuery({
    queryKey: ["trademarks", "reports", "at-risk"],
    queryFn: () => trademarksApi.atRisk(),
  });
  const { data: allTrademarks, isLoading: listLoading } = useQuery({
    queryKey: ["trademarks"],
    queryFn: () => trademarksApi.list(),
  });

  async function regenerate() {
    setRegenerating(true);
    try {
      await trademarksApi.regenerateDigest();
      queryClient.invalidateQueries({ queryKey: ["trademarks", "reports", "digest"] });
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not regenerate the digest", "error");
    } finally {
      setRegenerating(false);
    }
  }

  const statusOptions = useMemo(() => (stats ? ["All", ...Object.keys(stats.by_status).sort()] : ["All"]), [stats]);
  const filteredForExport = useMemo(
    () => (exportStatus === "All" ? allTrademarks ?? [] : (allTrademarks ?? []).filter((t) => t.status === exportStatus)),
    [allTrademarks, exportStatus],
  );

  function handleExport() {
    const header = ["id", "name", "status", "jurisdiction", "nice_class", "filed_on"];
    const lines = [header.join(",")];
    for (const t of filteredForExport) {
      lines.push(
        [t.id, t.name, t.status, t.jurisdiction, t.nice_class ?? "", t.filed_on ?? ""]
          .map((v) => csvEscape(String(v)))
          .join(","),
      );
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const suffix = exportStatus === "All" ? "all" : exportStatus.toLowerCase().replace(/\s+/g, "-");
    a.href = url;
    a.download = `trademark-portfolio-${suffix}-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Reports"
        description="Portfolio analytics generated from your live trademark data."
        actions={
          <Button variant="outline" size="sm" onClick={regenerate} loading={regenerating}>
            <RefreshCw className="h-4 w-4" />
            Regenerate digest
          </Button>
        }
      />

      {statsLoading ? (
        <CenterSpinner label="Loading portfolio stats…" />
      ) : statsError ? (
        <ErrorState error={statsError} />
      ) : stats ? (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <StatCard label="Total trademarks" value={stats.total} />
            <StatCard label="Filed in last 30 days" value={stats.filed_last_30d} tone="amber" />
            <StatCard label="Renewals due in 90 days" value={stats.upcoming_renewals_90d} tone="violet" />
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>By status</CardTitle>
              </CardHeader>
              <CardBody className="p-0">
                <Table>
                  <THead>
                    <tr>
                      <TH>Status</TH>
                      <TH>Count</TH>
                    </tr>
                  </THead>
                  <tbody>
                    {Object.entries(stats.by_status).map(([status, count]) => (
                      <TR key={status}>
                        <TD className="font-medium text-slate-900">{titleCase(status)}</TD>
                        <TD>{count}</TD>
                      </TR>
                    ))}
                  </tbody>
                </Table>
              </CardBody>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>By jurisdiction</CardTitle>
              </CardHeader>
              <CardBody className="p-0">
                <Table>
                  <THead>
                    <tr>
                      <TH>Jurisdiction</TH>
                      <TH>Count</TH>
                    </tr>
                  </THead>
                  <tbody>
                    {Object.entries(stats.by_jurisdiction).map(([jurisdiction, count]) => (
                      <TR key={jurisdiction}>
                        <TD className="font-medium text-slate-900">{jurisdiction}</TD>
                        <TD>{count}</TD>
                      </TR>
                    ))}
                  </tbody>
                </Table>
              </CardBody>
            </Card>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>By NICE class</CardTitle>
              </CardHeader>
              <CardBody className="p-0">
                <Table>
                  <THead>
                    <tr>
                      <TH>Class</TH>
                      <TH>Count</TH>
                    </tr>
                  </THead>
                  <tbody>
                    {Object.entries(stats.by_nice_class).map(([niceClass, count]) => (
                      <TR key={niceClass}>
                        <TD className="font-medium text-slate-900">{niceClass}</TD>
                        <TD>{count}</TD>
                      </TR>
                    ))}
                  </tbody>
                </Table>
              </CardBody>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Renewal &amp; risk pipeline</CardTitle>
              </CardHeader>
              <CardBody className={atRisk?.length ? "p-0" : undefined}>
                {atRiskLoading ? (
                  <CenterSpinner label="Loading…" />
                ) : atRiskError ? (
                  <ErrorState error={atRiskError} />
                ) : !atRisk || atRisk.length === 0 ? (
                  <p className="p-5 text-[13px] text-slate-500">
                    Nothing in "Renewal pending" or "Lapsed" status right now — no immediate action needed.
                  </p>
                ) : (
                  <div className="divide-y divide-slate-200">
                    {atRisk.map((t) => (
                      <div key={t.id} className="flex items-center justify-between px-5 py-2.5">
                        <div>
                          <Link href={`/trademarks?highlight=${encodeURIComponent(t.id)}`} className="text-[13px] font-medium text-slate-900 hover:text-brand-700 hover:underline">
                            {t.name}
                          </Link>
                          <div className="text-[11px] text-slate-500">{t.id} · {t.jurisdiction}</div>
                        </div>
                        <Badge tone={t.status === "lapsed" ? "red" : "amber"}>{titleCase(t.status)}</Badge>
                      </div>
                    ))}
                  </div>
                )}
                <p className="px-5 pb-4 pt-3 text-[11px] text-slate-500">
                  Sourced directly from the trademarks table's status field — no opposition, cancellation or
                  infringement tracking exists in this build, so that data isn't shown rather than invented.
                </p>
              </CardBody>
            </Card>
          </div>
        </>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Portfolio digest</CardTitle>
        </CardHeader>
        <CardBody>
          {digestLoading ? (
            <CenterSpinner label="Loading digest…" />
          ) : digestError ? (
            <ErrorState error={digestError} />
          ) : digest ? (
            <div className="space-y-1">
              <p className="text-[13px] text-slate-500">{digest.digest_date}</p>
              <p className="whitespace-pre-wrap text-[13px] text-slate-900">{digest.summary}</p>
            </div>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Compliance and export</CardTitle>
        </CardHeader>
        <CardBody className="flex flex-wrap items-center gap-3">
          <Select className="max-w-xs" value={exportStatus} onChange={(e) => setExportStatus(e.target.value)}>
            {statusOptions.map((s) => (
              <option key={s} value={s}>{s === "All" ? "All trademarks" : titleCase(s)}</option>
            ))}
          </Select>
          <Button onClick={handleExport} disabled={listLoading || filteredForExport.length === 0}>
            <Download className="h-4 w-4" />
            Export CSV ({filteredForExport.length})
          </Button>
          {stats && (
            <span className="text-[13px] text-slate-500">
              Registered: <b className="text-slate-900">{stats.total > 0 ? Math.round(((stats.by_status["registered"] ?? 0) / stats.total) * 100) : 0}%</b> of portfolio
              {" · "}Renewal pending: <b className="text-warning">{stats.renewal_pending}</b>
              {" · "}Lapsed: <b className="text-danger">{stats.lapsed}</b>
            </span>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
