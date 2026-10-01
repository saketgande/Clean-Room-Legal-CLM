"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileSearch, Plus, ScanLine, Stamp } from "lucide-react";
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
  Modal,
  PageHeader,
  StatCard,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { fmtDate, statusTone, titleCase } from "@/lib/utils";
import type { RenewalCalendarEntry } from "@/lib/types";

const TIMELINE_WINDOW_DAYS = 182; // next 6 months, same as the source module's Dashboard

// Mirrors the source module's daysLeft/statusBadge helpers on its own
// Dashboard (app/page.tsx) — UTC day-diff so "today" doesn't drift with TZ.
function daysLeft(renewalDueOn: string): number {
  const now = new Date();
  const todayUtc = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const due = new Date(renewalDueOn);
  const dueUtc = Date.UTC(due.getFullYear(), due.getMonth(), due.getDate());
  return Math.round((dueUtc - todayUtc) / 86400000);
}

function renewalBadge(left: number): { tone: "red" | "amber" | "blue"; label: string } {
  if (left < 0) return { tone: "red", label: "Overdue" };
  if (left <= 60) return { tone: "amber", label: "Action needed" };
  return { tone: "blue", label: "Upcoming" };
}

export default function TrademarkDashboardPage() {
  const { data: metrics, isLoading: metricsLoading, error: metricsError } = useQuery({
    queryKey: ["trademarks", "dashboard"],
    queryFn: () => trademarksApi.dashboard(),
  });
  const { data: digest, isLoading: digestLoading } = useQuery({
    queryKey: ["trademarks", "reports", "digest"],
    queryFn: () => trademarksApi.digest(),
  });
  const { data: trademarks } = useQuery({
    queryKey: ["trademarks"],
    queryFn: () => trademarksApi.list(),
  });
  // The backend returns every trademark with a computable renewal date in
  // one shot (matching the source module's own unfiltered
  // GET /api/portfolio/renewals) — the Dashboard windows to the next 6
  // months client-side, same as its own Dashboard (app/page.tsx) does.
  const { data: renewals, isLoading: renewalsLoading, error: renewalsError } = useQuery({
    queryKey: ["trademarks", "calendar"],
    queryFn: () => trademarksApi.calendar(),
  });
  const [selected, setSelected] = useState<RenewalCalendarEntry | null>(null);
  const { data: selectedDetail } = useQuery({
    queryKey: ["trademark", selected?.id],
    queryFn: () => trademarksApi.get(selected!.id),
    enabled: !!selected,
  });

  const recent = (trademarks ?? [])
    .slice()
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
    .slice(0, 5);

  // Overdue first (most recently missed first), then soonest-upcoming —
  // same ordering as the source module, capped to 8 rows. Falls back to
  // the nearest upcoming renewals when nothing is due within 6 months.
  const { timeline, nearestUpcomingBeyond6mo } = useMemo(() => {
    const all = renewals?.records ?? [];
    const withinWindow = all.filter((r) => r.overdue || daysLeft(r.renewal_due_on) <= TIMELINE_WINDOW_DAYS);
    const upcomingInWindow = withinWindow.some((r) => !r.overdue);
    const rows = upcomingInWindow || all.every((r) => r.overdue) ? withinWindow : all;
    const overdue = rows.filter((r) => r.overdue).sort((a, b) => b.renewal_due_on.localeCompare(a.renewal_due_on));
    const upcoming = rows.filter((r) => !r.overdue).sort((a, b) => a.renewal_due_on.localeCompare(b.renewal_due_on));
    return {
      timeline: [...overdue, ...upcoming].slice(0, 8),
      nearestUpcomingBeyond6mo: !upcomingInWindow && upcoming.length > 0,
    };
  }, [renewals]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Trademark Suite Dashboard"
        description="Portfolio health at a glance — status mix, upcoming renewals, and the latest activity."
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

      {metricsError ? (
        <ErrorState error={metricsError} />
      ) : (
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
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Renewal timeline — next 6 months</CardTitle>
          </CardHeader>
          <CardBody className={timeline.length ? "p-0" : undefined}>
            {renewalsError ? (
              <ErrorState error={renewalsError} />
            ) : renewalsLoading ? (
              <CenterSpinner label="Loading renewals…" />
            ) : timeline.length === 0 ? (
              <p className="text-[13px] text-slate-500">
                No trademarks with a computable renewal date — nothing to show yet.
              </p>
            ) : (
              <>
                {nearestUpcomingBeyond6mo && (
                  <p className="px-5 pt-3 text-[11.5px] text-slate-500">
                    Nothing is actually due within the next 6 months — showing the nearest upcoming renewals instead.
                  </p>
                )}
                <Table>
                  <THead>
                    <tr>
                      <TH>Trademark</TH>
                      <TH>Jurisdiction</TH>
                      <TH>Renewal due</TH>
                      <TH>Days</TH>
                      <TH>Status</TH>
                    </tr>
                  </THead>
                  <tbody>
                    {timeline.map((r) => {
                      const left = daysLeft(r.renewal_due_on);
                      const badge = renewalBadge(left);
                      return (
                        <TR key={r.id} className="cursor-pointer" onClick={() => setSelected(r)}>
                          <TD className="font-medium text-slate-900">{r.name}</TD>
                          <TD>{r.jurisdiction}</TD>
                          <TD>{fmtDate(r.renewal_due_on)}</TD>
                          <TD className={left < 0 ? "font-semibold text-danger" : left <= 60 ? "font-semibold text-warning" : undefined}>
                            {left < 0 ? `-${Math.abs(left)}` : left}
                          </TD>
                          <TD>
                            <Badge tone={badge.tone}>{badge.label}</Badge>
                          </TD>
                        </TR>
                      );
                    })}
                  </tbody>
                </Table>
              </>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Today's portfolio digest</CardTitle>
          </CardHeader>
          <CardBody>
            {digestLoading ? (
              <CenterSpinner label="Loading digest…" />
            ) : digest ? (
              <p className="whitespace-pre-wrap text-[13px] text-slate-700">{digest.summary}</p>
            ) : (
              <p className="text-[13px] text-slate-500">No digest available yet.</p>
            )}
            <Link href="/trademarks/reports" className="mt-2 inline-block text-[12.5px] font-medium text-brand-700 hover:underline">
              View full reports →
            </Link>
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recently added</CardTitle>
        </CardHeader>
        <CardBody className="space-y-2">
          {recent.length === 0 ? (
            <p className="text-[13px] text-slate-500">No trademarks yet.</p>
          ) : (
            recent.map((t) => (
              <div key={t.id} className="flex items-center justify-between text-[13px]">
                <Link href={`/trademarks/${t.id}`} className="font-medium text-slate-900 hover:text-brand-700 hover:underline">
                  {t.name}
                </Link>
                <div className="flex items-center gap-3 text-slate-500">
                  <Badge tone={statusTone(t.status)}>{titleCase(t.status)}</Badge>
                  <span>{fmtDate(t.created_at)}</span>
                </div>
              </div>
            ))
          )}
          <Link href="/trademarks" className="mt-2 inline-block text-[12.5px] font-medium text-brand-700 hover:underline">
            View all trademarks →
          </Link>
        </CardBody>
      </Card>

      {selected && (
        <Modal open onClose={() => setSelected(null)} title={selected.name} size="md">
          <div className="grid grid-cols-2 gap-4 text-[13px]">
            <DetailRow label="Status" value={titleCase(selectedDetail?.status ?? selected.status)} />
            <DetailRow label="Jurisdiction" value={selected.jurisdiction} />
            <DetailRow label="Nice class" value={selectedDetail?.nice_class ?? "—"} />
            <DetailRow label="Filed on" value={fmtDate(selectedDetail?.filed_on ?? null)} />
            <DetailRow label="Renewal due" value={fmtDate(selected.renewal_due_on)} />
            <DetailRow
              label={daysLeft(selected.renewal_due_on) < 0 ? "Days overdue" : "Days left"}
              value={String(Math.abs(daysLeft(selected.renewal_due_on)))}
            />
          </div>
          {selectedDetail?.description && (
            <div className="mt-4">
              <p className="mb-1 text-xs font-medium uppercase tracking-[0.04em] text-slate-500">
                Description / goods &amp; services
              </p>
              <p className="whitespace-pre-wrap text-[13px] text-slate-700">{selectedDetail.description}</p>
            </div>
          )}
          <Badge tone={renewalBadge(daysLeft(selected.renewal_due_on)).tone} className="mt-4">
            {renewalBadge(daysLeft(selected.renewal_due_on)).label}
          </Badge>
          <p className="mt-4 text-[11px] text-slate-500">
            Renewal due date reflects the record's own filing; not an official registry lookup.
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <Link href={`/trademarks?highlight=${encodeURIComponent(selected.id)}`}>
              <Button variant="outline">View in My trademarks</Button>
            </Link>
            <Link href="/trademarks/calendar">
              <Button>Open renewal calendar</Button>
            </Link>
          </div>
        </Modal>
      )}
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-[0.04em] text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-slate-800">{value}</dd>
    </div>
  );
}
