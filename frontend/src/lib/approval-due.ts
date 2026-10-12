import type { ApprovalRequest } from "@/lib/types";
import { fmtDate, fmtDateTime, fmtRelative } from "@/lib/utils";

/**
 * When an approval is due, in words that agree with its "Overdue" pill: the date, plus how late
 * once it is late, and the time of day while it falls today. A bare date next to an "Overdue"
 * pill read as a contradiction for a row due earlier today.
 */
export function dueText(req: Pick<ApprovalRequest, "due_at" | "overdue" | "status">, now = new Date()): string {
  if (!req.due_at) return "—";
  const due = new Date(req.due_at);
  if (Number.isNaN(due.getTime())) return "—";
  if (req.overdue) return `${fmtDate(req.due_at)} · ${fmtRelative(req.due_at)}`;
  if (req.status === "pending" && due.toDateString() === now.toDateString()) {
    return `Today ${fmtDateTime(req.due_at).split(", ").pop()}`;
  }
  return fmtDate(req.due_at);
}
