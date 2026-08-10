"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock } from "lucide-react";
import { trademarksApi } from "@/lib/endpoints";
import {
  Badge,
  Card,
  CenterSpinner,
  EmptyState,
  ErrorState,
  PageHeader,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { fmtDate, statusTone, titleCase } from "@/lib/utils";

export default function TrademarkCalendarPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["trademarks", "calendar"],
    queryFn: () => trademarksApi.calendar(180),
  });

  return (
    <div className="space-y-4">
      <PageHeader
        title="Renewal calendar"
        description="Upcoming trademark renewal deadlines across your portfolio (next 180 days)."
      />

      {isLoading ? (
        <CenterSpinner label="Loading renewal dates…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={<CalendarClock className="h-6 w-6" />}
          title="No upcoming renewals"
          description="Trademarks with a renewal due date within the next 180 days will appear here."
        />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Trademark</TH>
                <TH>Status</TH>
                <TH>Jurisdiction</TH>
                <TH>Renewal due</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((entry) => (
                <TR key={entry.id}>
                  <TD className="font-medium text-slate-900">
                    <Link href={`/trademarks/${entry.id}`} className="hover:text-brand-700 hover:underline">
                      {entry.name}
                    </Link>
                  </TD>
                  <TD>
                    <Badge tone={statusTone(entry.status)}>{titleCase(entry.status)}</Badge>
                  </TD>
                  <TD>{entry.jurisdiction}</TD>
                  <TD>{fmtDate(entry.renewal_due_on)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </div>
  );
}
