import { fmtDate } from "./utils";
import type { RequestActivityItem, RequestCounterparty } from "./types";

/** Small, testable rules behind the request page's "At a glance" facts and its set-up window. */

/** A new request is set up in the background (AI triage, owner, then the workflow starts). For this long
 *  after filing, "no workflow yet" means "not yet", not "none". */
export const SETUP_WINDOW_MS = 90_000;

export function isBeingSetUp(
  request: { submitted_at?: string | null; created_at?: string | null } | null | undefined,
  run: unknown,
  now: number = Date.now(),
): boolean {
  if (!request || run) return false;
  const filed = Date.parse(request.submitted_at ?? request.created_at ?? "");
  return Number.isFinite(filed) && now - filed >= 0 && now - filed < SETUP_WINDOW_MS;
}

/** What the "You are here" card says before any workflow has run. It used to read "In progress" over "not started",
 *  which contradict each other (round 3): a request someone owns or has drafted is in progress; one nobody owns is not
 *  started, and the note says what it is waiting for. */
export function beforeWorkflow(r: { assigned_to_label?: string | null; contract_id?: string | null }): { title: string; note: string } {
  if (r.assigned_to_label) return { title: "In progress", note: `with ${r.assigned_to_label}` };
  if (r.contract_id) return { title: "In progress", note: "contract drafted" };
  return { title: "Not started", note: "waiting for an owner" };
}

const text = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v.trim() : null);

/** A form's date answer is stored as YYYY-MM-DD, but every other date in the app reads in the person's own
 *  format ("15 Oct 2026"), and the date box they typed it into showed dd/mm/yyyy (round 2, bug 48). */
const day = (v: string | null): string | null => (v && /^\d{4}-\d{2}-\d{2}$/.test(v) ? fmtDate(v) : v);

/** The "Term" fact. The form's own answer ("2-year term", "Renews automatically") is shown when there
 *  is no end date; "open-ended" used to be printed beside a "2-year term" because only dates were read. */
export function termFact(fv: Record<string, unknown>): string | null {
  const start = day(["start_date", "services_start", "change_effective", "effective_date"].map((k) => text(fv[k])).find(Boolean) ?? null);
  const end = day(["end_date", "services_end", "new_end_date", "renew_end", "termination_date"].map((k) => text(fv[k])).find(Boolean) ?? null);
  const term = ["term", "nda_term"].map((k) => text(fv[k])).find(Boolean) ?? null;
  if (start && end) return `${start} → ${end}`;
  if (start || end) {
    if (end) return `${start ?? "—"} → ${end}`;
    return term ? `${start} · ${term}` : `${start} → no end date given`;
  }
  return term;
}

/** The "Approvals" line of the filing form's "Where this request goes" panel. "None on this workflow" used to
 *  be printed while the answer was still loading, and before the contract value (which decides who must
 *  approve) was typed, which made a guess look like a fact. */
export function approvalsText(p: {
  approvers: { step_name: string; approver: string }[];
  fetching: boolean;
  loaded: boolean;
  valueMissing: boolean;
  /** No workflow fits the answers (for example an amount in a currency no workflow condition covers). Who approves
   *  is then decided when a person picks the workflow, so "None on this workflow" would be wrong (round 3). */
  noWorkflow?: boolean;
}): string {
  if (p.approvers.length) return p.approvers.map((a) => `${a.step_name}: ${a.approver}`).join("\n");
  if (p.fetching) return "Working it out…";
  if (!p.loaded) return "Not known yet";
  if (p.valueMissing) return "Depends on the contract value: fill it in to see who approves";
  return p.noWorkflow ? "Decided when a person picks the workflow after filing" : "None on this workflow";
}

/** The Activity list grouped under day headings. Keeps the given (newest-first) order; the label is the local
 *  date ("14 Oct"), with the year when it is not the current one. */
export function groupActivityByDay(items: RequestActivityItem[], now: number = Date.now()): { day: string; items: RequestActivityItem[] }[] {
  const year = new Date(now).getFullYear();
  const out: { key: string; day: string; items: RequestActivityItem[] }[] = [];
  for (const it of items) {
    const d = new Date(it.at);
    const key = Number.isNaN(d.getTime()) ? "" : `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    const last = out[out.length - 1];
    if (last && last.key === key) { last.items.push(it); continue; }
    const day = key ? d.toLocaleDateString("en-GB", { day: "numeric", month: "short", ...(d.getFullYear() === year ? {} : { year: "numeric" }) }) : "Earlier";
    out.push({ key, day, items: [it] });
  }
  return out.map(({ day, items }) => ({ day, items }));
}

/** "5 other contracts · 2 active · 7 earlier requests": parts only when above zero; nothing at all → null. */
export function counterpartySummary(s: RequestCounterparty["summary"]): string | null {
  const parts = [
    s.contracts > 0 ? `${s.contracts} other contract${s.contracts === 1 ? "" : "s"}` : null,
    s.active > 0 ? `${s.active} active` : null,
    s.requests > 0 ? `${s.requests} earlier request${s.requests === 1 ? "" : "s"}` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}
