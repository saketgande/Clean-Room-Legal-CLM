"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, CheckCheck } from "lucide-react";
import { notificationsApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  SkeletonRows,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { cn, fmtRelative, statusTone, titleCase, truncate } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { Notification } from "@/lib/types";

export default function NotificationsPage() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data, isLoading, error } = useQuery({
    queryKey: ["notifications"],
    queryFn: notificationsApi.list,
  });

  const unread = (data ?? []).filter((n) => !n.read_at).length;

  function refresh() {
    qc.invalidateQueries({ queryKey: ["notifications"] });
    qc.invalidateQueries({ queryKey: ["notifications", "unread-count"] });
  }

  async function markRead(n: Notification) {
    if (n.read_at) return;
    // Optimistic: clear the unread state instantly, reconcile after.
    qc.setQueryData<Notification[]>(["notifications"], (old) =>
      (old ?? []).map((x) =>
        x.id === n.id ? { ...x, read_at: new Date().toISOString() } : x,
      ),
    );
    try {
      await notificationsApi.markRead(n.id);
      refresh();
    } catch (e) {
      refresh();
      notify(e instanceof Error ? e.message : "Failed to mark read", "error");
    }
  }

  async function markAll() {
    try {
      await notificationsApi.markAllRead();
      refresh();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed", "error");
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Notifications"
        description="Emails and in-app alerts sent across your organization."
        actions={
          unread > 0 ? (
            <Button variant="outline" onClick={markAll}>
              <CheckCheck className="h-4 w-4" />
              Mark all as read ({unread})
            </Button>
          ) : undefined
        }
      />

      {isLoading ? (
        <SkeletonRows rows={6} />
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
              {(data ?? []).map((n) => {
                const isUnread = !n.read_at;
                return (
                  <TR
                    key={n.id}
                    onClick={() => markRead(n)}
                    className={cn(
                      isUnread && "cursor-pointer bg-brand-50/40",
                    )}
                    title={isUnread ? "Click to mark as read" : undefined}
                  >
                    <TD
                      className={cn(
                        "text-slate-900",
                        isUnread ? "font-semibold" : "font-medium",
                      )}
                    >
                      <span className="flex items-center gap-2">
                        {isUnread && (
                          <span className="h-2 w-2 shrink-0 rounded-full bg-brand-600" />
                        )}
                        {titleCase(n.event_type.replace(/\./g, " "))}
                      </span>
                    </TD>
                    <TD className={cn(isUnread && "font-medium")}>
                      {n.subject ?? "—"}
                    </TD>
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
                );
              })}
            </tbody>
          </Table>
        </Card>
      )}
    </div>
  );
}
