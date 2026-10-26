"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge, Card, CardBody, CenterSpinner, EmptyState, ErrorState } from "@/components/ui";
import { trademarksApi } from "@/lib/endpoints";
import { fmtDateTime } from "@/lib/utils";

type Tone = "violet" | "slate" | "green" | "amber" | "red";

// Every entry is classified by the prefix of its event type so the log reads at a glance.
const CATEGORIES: { match: (eventType: string) => boolean; label: string; tone: Tone }[] = [
  { match: (t) => t === "trademark.decision", label: "Decision", tone: "amber" },
  { match: (t) => t === "trademark.filing", label: "Filing", tone: "green" },
  { match: (t) => t.startsWith("trademark.search"), label: "Search", tone: "violet" },
  { match: (t) => t === "document.added", label: "Document", tone: "slate" },
  { match: (t) => t === "trademark.comment_added", label: "Discussion", tone: "slate" },
  { match: (t) => t === "trademark.status_changed" || t === "trademark.case_opened" || t === "intake.created", label: "Case", tone: "violet" },
  { match: (t) => t.startsWith("flow."), label: "Workflow", tone: "slate" },
];

function categorize(eventType: string): { label: string; tone: Tone } {
  return CATEGORIES.find((c) => c.match(eventType)) ?? { label: "Event", tone: "slate" };
}

/** Audit log of the case: the mark's own events, its Legal Intake request's workflow events and attachments, newest first. */
export function ActivityPanel({ trademarkId }: { trademarkId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["trademark", trademarkId, "activity"],
    queryFn: () => trademarksApi.activity(trademarkId),
  });

  if (isLoading) return <CenterSpinner label="Loading audit log…" />;
  if (error) return <ErrorState error={error} />;
  if (!data || data.length === 0) {
    return (
      <EmptyState
        title="Nothing logged yet"
        description="Case creation, searches, workflow steps, decisions, filings, documents and comments are recorded here."
      />
    );
  }

  return (
    <Card>
      <CardBody className="divide-y divide-slate-200 p-0">
        {data.map((entry) => {
          const category = categorize(entry.event_type);
          return (
            <div key={entry.id} className="flex items-start justify-between gap-3 px-4 py-3 text-[13px]">
              <div className="min-w-0">
                <p className="text-slate-900">{entry.title}</p>
                <p className="mt-0.5 text-xs text-slate-500">
                  {entry.actor_name ?? "System"} · {fmtDateTime(entry.created_at)}
                </p>
              </div>
              <Badge tone={category.tone}>{category.label}</Badge>
            </div>
          );
        })}
      </CardBody>
    </Card>
  );
}
