import { fmtDate } from "./utils";

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

export interface TrailItem {
  key: string;
  who: string;
  what: string;
  at: string | null;
  /** "you" = the requester, "cp" = a person on the other side or a person's hand-over, "note" = a workflow comment. */
  tone: "you" | "agent" | "cp" | "note";
}

/** The request's Activity list: raised, hand-overs and the notes written on the workflow, oldest first.
 *  Notes used to be left out, so a comment made on the request left no trace in its Activity. */
export function activityTrail(p: {
  requester: string | null;
  raisedAt: string | null;
  handoffs: { id: string; to_label?: string | null; to_holder?: string | null; reason?: string | null; created_at?: string | null; actor_type?: string | null }[];
  comments: { actor_name: string; text: string; at: string; kind: string }[];
}): TrailItem[] {
  const items: TrailItem[] = [
    { key: "raised", who: p.requester ?? "Requester", what: "raised this request", at: p.raisedAt, tone: "you" },
    ...p.handoffs.map((h): TrailItem => ({
      key: `h-${h.id}`, who: h.to_label ?? h.to_holder ?? "Someone", what: h.reason ?? "took custody",
      at: h.created_at ?? null, tone: h.actor_type === "agent" ? "agent" : "cp",
    })),
    ...p.comments.map((c, i): TrailItem => ({
      key: `c-${i}-${c.at}`, who: c.actor_name,
      what: c.kind === "return" ? `sent it back: ${c.text}` : c.kind === "reply" ? `replied to the rejection: ${c.text}` : `wrote: ${c.text}`,
      at: c.at, tone: "note",
    })),
  ];
  const time = (t: string | null) => (t ? Date.parse(t) : Number.NaN);
  return items
    .map((item, order) => ({ item, order, t: time(item.at) }))
    .sort((a, b) => (Number.isNaN(a.t) || Number.isNaN(b.t) ? a.order - b.order : a.t - b.t || a.order - b.order))
    .map((x) => x.item);
}
