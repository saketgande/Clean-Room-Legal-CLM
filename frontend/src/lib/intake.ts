import type { IntakeRequest, IntakeSlaPosture, IntakeStatus, UserResponse } from "./types";

// Presentation labels live here, NOT in the wire types (Part 0.19).
export const STATUS_LABEL: Record<IntakeStatus, string> = {
  open: "Open",
  escalated: "Escalated",
  approved: "Approved",
  closed: "Closed",
};

// Badge tones available in ui.tsx: slate | blue | green | amber | red | violet | cyan
export const STATUS_TONE: Record<IntakeStatus, string> = {
  open: "blue",
  escalated: "violet",
  approved: "green",
  closed: "slate",
};

export const POSTURE_LABEL: Record<IntakeSlaPosture, string> = {
  on_track: "On track",
  at_risk: "At risk",
  overdue: "Overdue",
};
export const POSTURE_TONE: Record<IntakeSlaPosture, string> = {
  on_track: "green",
  at_risk: "amber",
  overdue: "red",
};

export const PRIORITY_TONE: Record<string, string> = {
  Critical: "red",
  High: "amber",
  Medium: "slate",
  Low: "slate",
};

// Part 0.24 — respect the RBAC wildcard.
export function can(user: UserResponse | null | undefined, perm: string): boolean {
  return !!user?.permissions?.some((p) => p === perm || p === "*");
}

// humanize an audit action string for the requester-facing latest-update line
export function humanizeEvent(action: string): string {
  const map: Record<string, string> = {
    "intake.created": "Request filed",
    "intake.assigned": "Assigned to a reviewer",
    "intake.handoff": "Moved between reviewers",
    "intake.stage_advanced": "Advanced a stage",
    "intake.sla_breached": "SLA breached",
    "intake.auto_escalated": "Escalated",
    "intake.closed": "Closed",
    "intake.agent_no_match": "Queued for manual triage",
    "intake.approval_blocked": "Approval blocked",
    "intake.promoted": "Promoted to a matter",
    "intake.paused": "Paused — waiting on requester",
    "intake.resumed": "Resumed",
    "intake.routing_rule.fired": "Routing rule applied",
    "intake.approved": "Response approved by legal",
    "intake.rejected": "Sent back for manual handling",
  };
  return map[action] || action.replace("intake.", "").replace(/[._]/g, " ");
}

export function slaBarColor(posture: IntakeSlaPosture): string {
  return { on_track: "var(--good, #0E7A0B)", at_risk: "#9A6700", overdue: "#B10E1C" }[posture];
}

export function sortBySla(a: IntakeRequest, b: IntakeRequest): number {
  return b.sla_pct - a.sla_pct; // most-elapsed first
}
