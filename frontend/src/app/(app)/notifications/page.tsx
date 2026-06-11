"use client";

import { useQuery } from "@tanstack/react-query";
import { Bell } from "lucide-react";
import { notificationsApi } from "@/lib/endpoints";
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
import { fmtRelative, statusTone, titleCase, truncate } from "@/lib/utils";

export default function NotificationsPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["notifications"],
    queryFn: notificationsApi.list,
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Notifications"
        description="Emails and in-app alerts sent across your organization."
      />

      {isLoading ? (
        <CenterSpinner label="Loading notifications…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={<Bell className="h-6 w-6" />}
          title="No notifications"
          description="Notifications appear here as approvals, reminders and signature updates are sent."
        />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Event</TH>
                <TH>Subject</TH>
                <TH>Body</TH>
                <TH>Channel</TH>
                <TH>Status</TH>
                <TH>Created</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((n) => (
                <TR key={n.id}>
                  <TD className="font-medium text-slate-900">
                    {titleCase(n.event_type)}
                  </TD>
                  <TD>{n.subject ?? "—"}</TD>
                  <TD className="max-w-md text-slate-500">
                    {n.body ? truncate(n.body, 100) : "—"}
                  </TD>
                  <TD>
                    <Badge tone="slate">{titleCase(n.channel)}</Badge>
                  </TD>
                  <TD>
                    <Badge tone={statusTone(n.status)}>
                      {titleCase(n.status)}
                    </Badge>
                  </TD>
                  <TD>{fmtRelative(n.created_at)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </div>
  );
}
