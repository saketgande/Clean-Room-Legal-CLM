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
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  const d = dateOnly
    ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
    : new Date(value);
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
  "review",
  "approval",
  "signature",
  "active",
  "closed",
] as const;

type Tone =
  | "slate"
  | "blue"
  | "green"
  | "amber"
  | "red"
  | "violet"
  | "cyan";

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

// ---- Contract naming ------------------------------------------------------
// Titles are seeded from the uploaded filename, so a register listed things
// like "b87f2c89ed253d4d0b7492a6f8b6f4de.jpg" and "leecounty-scan-6pg.pdf" as
// contract names. Anything that still looks like a filename gets replaced by
// what a lawyer would actually call the agreement.

const FILENAME_RE = /\.(docx?|pdf|jpe?g|png|txt|xlsx?|rtf|eml|msg)$/i;
const HEX_BLOB_RE = /^[0-9a-f]{16,}$/i;
const WORD_SEPARATORS_RE = /[_-]+/g;

/** Does this string read as a filename or an opaque blob rather than a name? */
export function looksLikeFilename(title?: string | null): boolean {
  const t = (title ?? "").trim();
  if (!t) return true;
  if (FILENAME_RE.test(t)) return true;
  if (HEX_BLOB_RE.test(t.replace(FILENAME_RE, ""))) return true;
  // "Acme_MSA_Counterparty_Draft" — no spaces but separator-joined words.
  return !t.includes(" ") && WORD_SEPARATORS_RE.test(t);
}

/** The filename tidied into words, for the secondary line. */
export function prettyFilename(title?: string | null): string {
  const t = (title ?? "").trim();
  if (!t) return "";
  return t.replace(FILENAME_RE, "").replace(WORD_SEPARATORS_RE, " ").trim();
}

/**
 * What a row should lead with: "Master Services Agreement — Acme Analytics LLC".
 *
 * A real title is always preferred; this only steps in when the title is still
 * the uploaded filename. Falls back through type, then counterparty, then the
 * tidied filename, so a row never renders as "Untitled".
 */
export function contractDisplayName(c: {
  title?: string | null;
  contract_type?: string | null;
  counterparty_name?: string | null;
}): string {
  if (!looksLikeFilename(c.title)) return (c.title ?? "").trim();
  const type = (c.contract_type ?? "").trim();
  const party = (c.counterparty_name ?? "").trim();
  if (type && party) return `${type} — ${party}`;
  return type || party || prettyFilename(c.title) || "Untitled contract";
}
