"use client";

import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download, Target } from "lucide-react";
import { trademarksApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CenterSpinner,
  ErrorState,
  Modal,
  PageHeader,
  Select,
  StatCard,
} from "@/components/ui";
import { fmtDate } from "@/lib/utils";
import type { RenewalCalendarEntry } from "@/lib/types";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

// UTC throughout — matches how the rest of the trademarks module treats
// "today" (daysLeft on the Dashboard) so the grid never disagrees with the
// overdue/due-soon badges computed elsewhere from the same field.
function dateKeyUTC(iso: string): string {
  const d = new Date(iso);
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`;
}

function csvEscape(value: string): string {
  if (/[",\n]/.test(value)) return `"${value.replace(/"/g, '""')}"`;
  return value;
}

function daysLeftUTC(iso: string): number {
  const now = new Date();
  const todayUtc = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const due = new Date(iso);
  const dueUtc = Date.UTC(due.getUTCFullYear(), due.getUTCMonth(), due.getUTCDate());
  return Math.round((dueUtc - todayUtc) / 86400000);
}

function DayDetailModal({
  dayKey,
  records,
  onClose,
}: {
  dayKey: string;
  records: RenewalCalendarEntry[];
  onClose: () => void;
}) {
  const [y, m, d] = dayKey.split("-").map(Number);
  const label = new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
  return (
    <Modal open onClose={onClose} title="Renewals due" size="lg">
      <p className="mb-3 text-[13px] text-slate-500">
        {label} · {records.length} trademark{records.length !== 1 ? "s" : ""}
      </p>
      <div className="space-y-2">
        {records.map((r) => (
          <div key={r.id} className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2">
            <div>
              <Link href={`/trademarks?highlight=${encodeURIComponent(r.id)}`} className="font-medium text-slate-900 hover:text-brand-700 hover:underline">
                {r.name}
              </Link>
              <div className="text-[11px] text-slate-500">
                {r.id} · {r.jurisdiction} · Class {r.nice_class || "—"} · filed {fmtDate(r.filed_on)}
              </div>
            </div>
            <Badge tone={r.overdue ? "red" : "amber"}>{r.overdue ? "Overdue" : "Due"}</Badge>
          </div>
        ))}
      </div>
    </Modal>
  );
}

export default function TrademarkCalendarPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["trademarks", "calendar"],
    queryFn: () => trademarksApi.calendar(),
  });
  const [cursor, setCursor] = useState(() => {
    const now = new Date();
    return { year: now.getUTCFullYear(), month: now.getUTCMonth() };
  });
  const [jurisdiction, setJurisdiction] = useState("All");
  const [selectedDay, setSelectedDay] = useState<string | null>(null);

  const allRecords = data?.records ?? [];

  const jurisdictions = useMemo(() => {
    const set = new Set<string>();
    allRecords.forEach((r) => set.add(r.jurisdiction));
    return Array.from(set).sort();
  }, [allRecords]);

  const filteredRecords = useMemo(
    () => (jurisdiction === "All" ? allRecords : allRecords.filter((r) => r.jurisdiction === jurisdiction)),
    [allRecords, jurisdiction],
  );

  const byDay = useMemo(() => {
    const map = new Map<string, RenewalCalendarEntry[]>();
    for (const r of filteredRecords) {
      const key = dateKeyUTC(r.renewal_due_on);
      const arr = map.get(key) ?? [];
      arr.push(r);
      map.set(key, arr);
    }
    return map;
  }, [filteredRecords]);

  const overdueCount = useMemo(() => filteredRecords.filter((r) => r.overdue).length, [filteredRecords]);
  const dueNext90 = useMemo(
    () => filteredRecords.filter((r) => !r.overdue && daysLeftUTC(r.renewal_due_on) <= 90).length,
    [filteredRecords],
  );

  const { year, month } = cursor;
  const daysInMonth = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  const startOffset = new Date(Date.UTC(year, month, 1)).getUTCDay();
  const now = new Date();
  const isCurrentMonth = now.getUTCFullYear() === year && now.getUTCMonth() === month;

  function dayKey(d: number): string {
    return `${year}-${pad2(month + 1)}-${pad2(d)}`;
  }

  function goToMonth(delta: number) {
    const d = new Date(Date.UTC(year, month + delta, 1));
    setCursor({ year: d.getUTCFullYear(), month: d.getUTCMonth() });
  }

  function jumpToNearestRenewal() {
    if (filteredRecords.length === 0) return;
    const sorted = filteredRecords.slice().sort((a, b) => a.renewal_due_on.localeCompare(b.renewal_due_on));
    const todayIso = new Date().toISOString().slice(0, 10);
    const upcoming = sorted.find((r) => r.renewal_due_on.slice(0, 10) >= todayIso);
    const target = upcoming ?? sorted[sorted.length - 1];
    const d = new Date(target.renewal_due_on);
    setCursor({ year: d.getUTCFullYear(), month: d.getUTCMonth() });
  }

  const monthHasData = filteredRecords.some((r) => dateKeyUTC(r.renewal_due_on).startsWith(`${year}-${pad2(month + 1)}`));

  function handleExport() {
    const header = ["id", "name", "status", "jurisdiction", "nice_class", "filed_on", "renewal_due_on", "overdue"];
    const lines = [header.join(",")];
    for (const r of filteredRecords) {
      lines.push(
        [r.id, r.name, r.status, r.jurisdiction, r.nice_class ?? "", fmtDate(r.filed_on), r.renewal_due_on, r.overdue ? "yes" : "no"]
          .map((v) => csvEscape(String(v)))
          .join(","),
      );
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const suffix = jurisdiction === "All" ? "all" : jurisdiction.toLowerCase().replace(/\s+/g, "-");
    a.href = url;
    a.download = `trademark-renewals-${suffix}-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  const cells: ReactNode[] = [];
  for (let i = 0; i < startOffset; i++) {
    cells.push(<div className="aspect-square rounded-lg bg-slate-50/50" key={`f${i}`} />);
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const key = dayKey(d);
    const dayRecords = byDay.get(key) ?? [];
    const hasOverdue = dayRecords.some((r) => r.overdue);
    const isToday = isCurrentMonth && now.getUTCDate() === d;
    cells.push(
      <div
        key={key}
        className={`flex aspect-square flex-col items-center justify-center gap-1 rounded-lg border text-[13px] ${
          isToday ? "border-brand-500 bg-brand-50" : "border-slate-200"
        }`}
      >
        <div className="text-slate-600">{d}</div>
        {dayRecords.length > 0 && (
          <button
            type="button"
            onClick={() => setSelectedDay(key)}
            title={`${dayRecords.length} renewal(s) due — click for details`}
            className={`flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[11px] font-semibold text-white ${
              hasOverdue ? "bg-danger" : "bg-warning"
            }`}
          >
            {dayRecords.length}
          </button>
        )}
      </div>,
    );
  }

  const selectedRecords = selectedDay ? byDay.get(selectedDay) ?? [] : [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Renewal calendar"
        description="Renewal due dates are computed from the record's own renewal date, or projected as filed on + 10 years (India's statutory renewal term) when only a filing date is known — a projection, not an official registry record."
        actions={
          <Button variant="outline" onClick={handleExport} disabled={isLoading || filteredRecords.length === 0}>
            <Download className="h-4 w-4" />
            Export CSV ({filteredRecords.length})
          </Button>
        }
      />

      {error ? (
        <ErrorState error={error} />
      ) : isLoading ? (
        <CenterSpinner label="Loading renewal data…" />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <StatCard
              label={`Overdue renewals${jurisdiction !== "All" ? ` (${jurisdiction})` : ""}`}
              value={overdueCount}
              tone={overdueCount > 0 ? "red" : "slate"}
            />
            <StatCard label="Due in next 90 days" value={dueNext90} tone={dueNext90 > 0 ? "amber" : "slate"} />
            <StatCard
              label="Records with a usable filing date"
              value={`${data?.total_with_dates ?? 0} of ${(data?.total_with_dates ?? 0) + (data?.skipped_unparseable ?? 0)}`}
            />
          </div>

          <Card className="flex flex-wrap items-center gap-2 p-3">
            <Button variant="outline" size="sm" onClick={() => goToMonth(-1)}>
              <ChevronLeft className="h-4 w-4" />
              Prev
            </Button>
            <div className="min-w-[150px] text-center text-[13.5px] font-semibold text-slate-900">
              {MONTH_NAMES[month]} {year}
            </div>
            <Button variant="outline" size="sm" onClick={() => goToMonth(1)}>
              Next
              <ChevronRight className="h-4 w-4" />
            </Button>
            {!isCurrentMonth && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  const n = new Date();
                  setCursor({ year: n.getUTCFullYear(), month: n.getUTCMonth() });
                }}
              >
                Today
              </Button>
            )}
            {!monthHasData && filteredRecords.length > 0 && (
              <Button size="sm" onClick={jumpToNearestRenewal}>
                <Target className="h-4 w-4" />
                Jump to nearest renewal
              </Button>
            )}
            <Select className="ml-auto max-w-[200px]" value={jurisdiction} onChange={(e) => setJurisdiction(e.target.value)}>
              <option value="All">All jurisdictions</option>
              {jurisdictions.map((j) => (
                <option key={j} value={j}>{j}</option>
              ))}
            </Select>
          </Card>

          <div>
            <div className="mb-1 grid grid-cols-7 gap-1.5 text-center text-[11px] font-medium uppercase tracking-[0.04em] text-slate-500">
              {WEEKDAYS.map((w) => (
                <div key={w}>{w}</div>
              ))}
            </div>
            <div className="grid grid-cols-7 gap-1.5">{cells}</div>
            {!monthHasData && (
              <p className="mt-3 text-[13px] text-slate-500">
                No renewals due in {MONTH_NAMES[month]} {year}
                {jurisdiction !== "All" ? ` for ${jurisdiction}` : ""}. Use "Jump to nearest renewal" above to find
                the closest month with activity.
              </p>
            )}
          </div>
        </>
      )}

      {selectedDay && (
        <DayDetailModal dayKey={selectedDay} records={selectedRecords} onClose={() => setSelectedDay(null)} />
      )}
    </div>
  );
}
