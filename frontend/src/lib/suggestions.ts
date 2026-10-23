// One list of suggestions for a contract.
//
// A change can reach a contract three ways: the playbook review proposes a fix
// for an issue, the assistant proposes an edit when asked in chat, or a person
// proposes a redline. They are all ContractEdits and are decided the same way.
// This file turns the contract's open playbook issues and its edits into what
// the Review tab shows: issues (with the changes made for them) and standalone
// changes (made for no issue).

import type { ContractDeviation, ContractEditResponse } from "./types";

/** ready: can be accepted. stale: made for an older version of the contract.
 *  unplaceable: its wording isn't in the text, so it can only be rejected.
 *  blank: it would write a fill-in blank into the contract, so it can only be rejected. */
export type EditState = "ready" | "stale" | "unplaceable" | "blank";
export type EditOrigin = "playbook" | "assistant" | "manual";

export interface IssueSuggestion {
  deviation: ContractDeviation;
  /** Every change made for this issue, decided or not. */
  edits: ContractEditResponse[];
}

export interface Suggestions {
  issues: IssueSuggestion[];
  /** Undecided changes that fix no open issue. */
  standalone: ContractEditResponse[];
  /** Cards waiting for a decision: open issues plus standalone changes. */
  toDecide: number;
  /** Changes still undecided anywhere, including those inside an issue card. */
  proposedEdits: number;
}

const OPEN_ISSUE = ["open", "needs_review"];

export const isOpenIssue = (d: ContractDeviation) => OPEN_ISSUE.includes(d.status || "open");

/** High and critical risks can only be kept on purpose: the server asks for a reason of at least
 *  this many characters (backend playbooks.service.decision_problem) and a rejected fix does not close them. */
export const MIN_KEEP_REASON = 10;
export const isHighRisk = (d: Pick<ContractDeviation, "severity">) => ["high", "critical"].includes((d.severity || "").trim().toLowerCase());

const severityRank = (severity: string) => {
  const v = (severity || "").toLowerCase();
  if (v === "critical" || v === "high") return 0;
  if (v === "medium") return 1;
  if (v === "low") return 2;
  return 3;
};

export function editState(edit: ContractEditResponse, currentVersionId: string | null | undefined): EditState {
  if (currentVersionId && edit.contract_version_id !== currentVersionId) return "stale";
  if (!edit.locatable) return "unplaceable";
  if (edit.blank) return "blank";
  return "ready";
}

export function editOrigin(edit: ContractEditResponse): EditOrigin {
  if (edit.edit_type === "playbook_redline") return "playbook";
  if (edit.edit_type === "manual") return "manual";
  return "assistant";
}

/** The changes made for an issue. Playbook changes saved before they named their
 *  issue only share its text as their rationale, so those are matched that way. */
export function editsForIssue(d: ContractDeviation, edits: ContractEditResponse[]): ContractEditResponse[] {
  const linked = edits.filter((e) => e.playbook_deviation_id === d.id);
  if (linked.length > 0) return linked;
  return edits.filter((e) => !e.playbook_deviation_id && e.edit_type === "playbook_redline" && e.rationale === d.issue);
}

export function buildSuggestions(input: {
  deviations: ContractDeviation[];
  edits: ContractEditResponse[];
  currentVersionId?: string | null;
}): Suggestions {
  const { edits, currentVersionId } = input;
  // A rejected change made for an older version of the contract says nothing about
  // the issue as it stands now, so the issue is offered a new fix instead of being
  // shown as "change already rejected".
  const counts = (e: ContractEditResponse) => !(e.status === "rejected" && editState(e, currentVersionId) === "stale");
  const issues = input.deviations
    .filter(isOpenIssue)
    .map((deviation, index) => ({ deviation, index }))
    .sort((a, b) => severityRank(a.deviation.severity) - severityRank(b.deviation.severity) || a.index - b.index)
    .map(({ deviation }) => ({ deviation, edits: editsForIssue(deviation, edits).filter(counts) }));
  const inIssue = new Set(issues.flatMap((i) => i.edits.map((e) => e.id)));
  const standalone = edits.filter((e) => e.status === "proposed" && !inIssue.has(e.id));
  return {
    issues,
    standalone,
    toDecide: issues.length + standalone.length,
    proposedEdits: edits.filter((e) => e.status === "proposed").length,
  };
}

/** What the Review header shows: how much has been decided, how much is left, how risky what is left is. */
export interface ReviewProgress {
  /** Suggested changes accepted or rejected, for the contract as it stands now. */
  decided: number;
  /** Decided plus still waiting: the "y" in "x of y decided". */
  total: number;
  /** Cards waiting for a decision: open issues plus standalone changes. */
  toDecide: number;
  /** Open issues by severity (critical counts as high). Issues of another kind are left out. */
  risk: { high: number; medium: number; low: number };
}

/**
 * The server only returns OPEN playbook issues (a decided issue leaves the list), so what has
 * been decided can only be counted from the changes: a change accepted or rejected for the
 * version the contract is on now. Changes made for an older version say nothing about this one
 * (once every change is decided the server makes a new version). An issue closed with "Keep as
 * is" never had a change, so it leaves the count instead of adding to "decided".
 */
export function reviewProgress(suggestions: Suggestions, edits: ContractEditResponse[], currentVersionId?: string | null): ReviewProgress {
  const decided = edits.filter((e) => e.status !== "proposed" && (!currentVersionId || e.contract_version_id === currentVersionId)).length;
  const risk = { high: 0, medium: 0, low: 0 };
  for (const { deviation } of suggestions.issues) {
    const rank = severityRank(deviation.severity);
    if (rank === 0) risk.high++;
    else if (rank === 1) risk.medium++;
    else if (rank === 2) risk.low++;
  }
  return { decided, total: decided + suggestions.toDecide, toDecide: suggestions.toDecide, risk };
}
