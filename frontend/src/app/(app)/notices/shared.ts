/* Shared between the register list and the notice detail page, so a status
   colour or a deadline phrase can't drift between the two views. */

import type { Notice, NoticeDeadlinePosture, NoticeStatus } from "@/lib/types";

export const NOTICE_TYPES = [
  { value: "demand", label: "Demand" },
  { value: "cease_and_desist", label: "Cease and desist" },
  { value: "breach", label: "Breach" },
  { value: "termination", label: "Termination" },
  { value: "statutory", label: "Statutory" },
  { value: "recovery", label: "Recovery" },
  { value: "infringement", label: "Infringement" },
  { value: "other", label: "Other" },
];

export const typeLabel = (v: string) =>
  NOTICE_TYPES.find((t) => t.value === v)?.label ?? v;

type Tone = "slate" | "blue" | "green" | "amber" | "red" | "violet";

export const STATUS_TONE: Record<NoticeStatus, Tone> = {
  draft: "slate",
  open: "blue",
  responded: "green",
  escalated: "red",
  closed: "slate",
};

export const POSTURE_TONE: Record<NoticeDeadlinePosture, Tone> = {
  none: "slate",
  on_track: "green",
  at_risk: "amber",
  overdue: "red",
  met: "green",
};

export const REMINDER_LABEL: Record<"t7" | "t3" | "due" | "overdue", string> = {
  t7: "A week out",
  t3: "Due soon",
  due: "Due today",
  overdue: "Deadline missed",
};

/** Deadline as a human phrase. The raw date is useless at a glance; "3 days
 *  left" / "10 days overdue" is what a lawyer actually scans for. */
export function deadlineLabel(n: Notice): string {
  if (!n.response_due_date) return "No deadline";
  const d = n.days_to_due;
  if (n.deadline_posture === "met") return n.responded_at ? "Answered" : "Closed";
  if (d === null) return n.response_due_date;
  if (d < 0) return `${Math.abs(d)} day${Math.abs(d) === 1 ? "" : "s"} overdue`;
  if (d === 0) return "Due today";
  return `${d} day${d === 1 ? "" : "s"} left`;
}

export const fmtDate = (iso: string | null) =>
  iso
    ? new Date(iso + "T00:00:00").toLocaleDateString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
      })
    : "—";
