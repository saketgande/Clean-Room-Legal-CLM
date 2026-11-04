"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge, Card, CardHeader, CardTitle, CenterSpinner, ErrorState } from "@/components/ui";
import { trademarksApi } from "@/lib/endpoints";
import { cn, fmtDateTime, fmtRelative } from "@/lib/utils";

type Tone = "violet" | "slate" | "green" | "amber" | "red";

/** How many entries show before "More". */
export const AUDIT_LOG_PREVIEW = 5;

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

/**
 * The case's audit log, kept in a narrow card at the side of every page of the case: it is a record about
 * the case, not a step of the work. The latest few entries show; "More" opens the rest. It lists the mark's
 * own events, its Legal Intake request's workflow events and attachments, newest first.
 */
export function AuditLogCard({ trademarkId }: { trademarkId: string }) {
  const [showAll, setShowAll] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ["trademark", trademarkId, "activity"],
    queryFn: () => trademarksApi.activity(trademarkId),
  });
  const entries = data ?? [];
  const shown = showAll ? entries : entries.slice(0, AUDIT_LOG_PREVIEW);
  const hidden = entries.length - AUDIT_LOG_PREVIEW;

  return (
    <Card>
      <CardHeader className="px-4 py-3">
        <CardTitle>Audit log</CardTitle>
        {data && <Badge tone="slate">{data.length}</Badge>}
      </CardHeader>

      {isLoading ? (
        <CenterSpinner label="Loading audit log…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : entries.length === 0 ? (
        <p className="px-4 py-3 text-[12.5px] text-slate-500">
          Nothing logged yet. Searches, workflow steps, decisions, filings, documents and comments are recorded here.
        </p>
      ) : (
        <>
          <ul className={cn("divide-y divide-slate-200", showAll && "max-h-[28rem] overflow-y-auto")}>
            {shown.map((entry) => {
              const category = categorize(entry.event_type);
              return (
                <li key={entry.id} className="px-4 py-2.5 text-[12.5px]">
                  <p className="text-slate-900">{entry.title}</p>
                  <p className="mt-1 flex items-center justify-between gap-2 text-[11px] text-slate-500">
                    <span className="min-w-0 truncate" title={fmtDateTime(entry.created_at)}>
                      {entry.actor_name ?? "System"} · {fmtRelative(entry.created_at)}
                    </span>
                    <Badge tone={category.tone}>{category.label}</Badge>
                  </p>
                </li>
              );
            })}
          </ul>
          {hidden > 0 && (
            <button
              type="button"
              aria-expanded={showAll}
              onClick={() => setShowAll((value) => !value)}
              className="w-full rounded-b-xl border-t border-slate-200 px-4 py-2 text-center text-xs font-medium text-brand-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/35"
            >
              {showAll ? "Show less" : `More (${hidden})`}
            </button>
          )}
        </>
      )}
    </Card>
  );
}
