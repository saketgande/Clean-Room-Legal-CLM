"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { FileSearch, Plus, ScanLine, Stamp } from "lucide-react";
import { trademarksApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CenterSpinner,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
  StatCard,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { fmtDate, statusTone, titleCase } from "@/lib/utils";

export default function TrademarksPage() {
  const [query, setQuery] = useState("");

  const { data: metrics, isLoading: metricsLoading } = useQuery({
    queryKey: ["trademarks", "dashboard"],
    queryFn: () => trademarksApi.dashboard(),
  });
  const { data: trademarks, isLoading, error } = useQuery({
    queryKey: ["trademarks"],
    queryFn: () => trademarksApi.list(),
  });

  const filtered = (trademarks ?? []).filter((t) =>
    t.name.toLowerCase().includes(query.trim().toLowerCase()),
  );

  return (
    <div className="space-y-4">
      <PageHeader
        title="Trademarks"
        description="Portfolio overview, intake, and renewal tracking for your trademark filings."
        actions={
          <>
            <Link href="/trademarks/extract">
              <Button variant="outline">
                <ScanLine className="h-4 w-4" />
                Extract from document
              </Button>
            </Link>
            <Link href="/trademarks/intake">
              <Button>
                <Plus className="h-4 w-4" />
                New intake
              </Button>
            </Link>
          </>
        }
      />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard
          label="Total trademarks"
          value={metricsLoading ? "—" : (metrics?.total_trademarks ?? 0)}
          icon={<Stamp className="h-4 w-4" />}
        />
        <StatCard
          label="Active prosecutions"
          value={metricsLoading ? "—" : (metrics?.active_prosecutions ?? 0)}
          tone="amber"
          icon={<FileSearch className="h-4 w-4" />}
        />
        <StatCard
          label="Renewals due in 90 days"
          value={metricsLoading ? "—" : (metrics?.upcoming_renewals ?? 0)}
          tone="violet"
        />
      </div>

      <Card>
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <h3 className="text-[13px] font-semibold text-slate-900">
            My trademarks
          </h3>
          <Input
            placeholder="Filter by name…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="max-w-xs"
          />
        </div>

        {isLoading ? (
          <CenterSpinner label="Loading trademarks…" />
        ) : error ? (
          <div className="p-5">
            <ErrorState error={error} />
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={<Stamp className="h-6 w-6" />}
              title="No trademarks yet"
              description="Start a new intake or extract records from a filed document to populate your portfolio."
              action={
                <Link href="/trademarks/intake">
                  <Button size="sm">
                    <Plus className="h-4 w-4" />
                    New intake
                  </Button>
                </Link>
              }
            />
          </div>
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Name</TH>
                <TH>Status</TH>
                <TH>Type</TH>
                <TH>Jurisdiction</TH>
                <TH>Nice class</TH>
                <TH>Renewal due</TH>
                <TH>Source</TH>
              </tr>
            </THead>
            <tbody>
              {filtered.map((t) => (
                <TR key={t.id}>
                  <TD className="font-medium text-slate-900">
                    <Link
                      href={`/trademarks/${t.id}`}
                      className="hover:text-brand-700 hover:underline"
                    >
                      {t.name}
                    </Link>
                  </TD>
                  <TD>
                    <Badge tone={statusTone(t.status)}>{titleCase(t.status)}</Badge>
                  </TD>
                  <TD>{titleCase(t.trademark_type)}</TD>
                  <TD>{t.jurisdiction}</TD>
                  <TD>{t.nice_class ?? "—"}</TD>
                  <TD>{fmtDate(t.renewal_due_on)}</TD>
                  <TD>{titleCase(t.source)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
