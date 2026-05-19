import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function titleCase(s: string | null | undefined): string {
  if (!s) return "";
  return s
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

export function fmtDate(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function fmtDateTime(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtRelative(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value).getTime();
  if (Number.isNaN(d)) return "—";
  const diff = Date.now() - d;
  const mins = Math.round(diff / 60000);
  if (Math.abs(mins) < 1) return "just now";
  if (Math.abs(mins) < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (Math.abs(hrs) < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (Math.abs(days) < 30) return `${days}d ago`;
  return fmtDate(value);
}

export function fmtMoney(
  amount?: number | null,
  currency?: string | null,
): string {
  if (amount === null || amount === undefined) return "—";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency || "USD",
      maximumFractionDigits: 0,
    }).format(amount);
  } catch {
    return `${amount.toLocaleString()} ${currency ?? ""}`.trim();
  }
}

export const STAGE_ORDER = [
  "intake",
  "drafting",
  "ai_review",
  "internal_review",
  "counterparty_review",
  "approval_pending",
  "approved",
  "signature_pending",
  "active",
  "renewal_due",
  "closed",
  "archived",
] as const;

// Mirrors backend app/contracts/lifecycle.py ALLOWED_TRANSITIONS — UI hint
// only; the backend remains the authority and rejects invalid moves.
export const ALLOWED_TRANSITIONS: Record<string, string[]> = {
  intake: ["drafting", "ai_review", "active"],
  drafting: ["ai_review", "internal_review"],
  ai_review: ["internal_review", "counterparty_review"],
  internal_review: ["counterparty_review", "approval_pending"],
  counterparty_review: ["ai_review", "internal_review", "approval_pending"],
  approval_pending: ["approved", "internal_review", "counterparty_review"],
  approved: ["signature_pending", "active"],
  signature_pending: ["active", "approved"],
  active: ["renewal_due", "closed", "archived"],
  renewal_due: ["active", "closed"],
  closed: ["archived"],
  archived: [],
};

export function nextStages(stage: string | null | undefined): string[] {
  return ALLOWED_TRANSITIONS[stage ?? ""] ?? [];
}

type Tone =
  | "slate"
  | "blue"
  | "green"
  | "amber"
  | "red"
  | "violet"
  | "cyan";

export function stageTone(stage?: string | null): Tone {
  switch (stage) {
    case "active":
      return "green";
    case "approved":
    case "signature_pending":
      return "cyan";
    case "approval_pending":
    case "renewal_due":
      return "amber";
    case "closed":
    case "archived":
      return "slate";
    case "ai_review":
    case "internal_review":
    case "counterparty_review":
      return "violet";
    default:
      return "blue";
  }
}

export function riskTone(risk?: string | null): Tone {
  switch ((risk ?? "").toLowerCase()) {
    case "critical":
      return "red";
    case "high":
      return "red";
    case "medium":
      return "amber";
    case "low":
      return "green";
    default:
      return "slate";
  }
}

export function statusTone(status?: string | null): Tone {
  switch ((status ?? "").toLowerCase()) {
    case "active":
    case "approved":
    case "completed":
    case "succeeded":
    case "complete":
    case "published":
    case "valid":
      return "green";
    case "pending":
    case "queued":
    case "running":
    case "draft":
    case "due_soon":
    case "waiting_confirmation":
    case "needs_review":
      return "amber";
    case "rejected":
    case "failed":
    case "declined":
    case "voided":
    case "overdue":
    case "cancelled":
    case "invalid":
      return "red";
    default:
      return "slate";
  }
}

export function initials(name?: string | null): string {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  return (
    (parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")
  ).toUpperCase();
}

export function truncate(s: string | null | undefined, n = 120): string {
  if (!s) return "";
  return s.length > n ? s.slice(0, n).trimEnd() + "…" : s;
}
