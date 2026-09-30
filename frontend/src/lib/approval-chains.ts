// Pure, dependency-free approval-chain helpers — no React, no HTTP.
//
// Why this exists: the `_chain-detail.tsx` surface (FR-5/FR-6/FR-7/FR-12/
// FR-13/FR-20) needs to render condition explanations as readable text, map a
// requirement's status to a UI tone, compute a step's decided/total progress
// (counting only requirements that still count toward completion), and find
// the sequence position of the next actionable approver on a sequential step.
//
// NOTE on types: `ConditionOperator`/`ConditionExpression`/
// `ChainRequirementResponse`/`ChainInstanceStep` are expected to land in
// `src/lib/types.ts` via a concurrent task (T007). As of writing this file,
// T007 had not yet landed those types, so the local shapes below mirror the
// exact frozen interfaces from plan.md's "Frontend TypeScript models" section
// verbatim; swap the import for the real ones once T007 lands (the shapes are
// structurally identical, so no other code here should need to change).

export type ConditionOperator = "gt" | "lt" | "eq" | "in" | "contains";

export interface ConditionExpression {
  field: string;
  operator: ConditionOperator;
  value: string | number | boolean | Array<string | number | boolean> | null;
}

export type ChainRequirementStatus = "pending" | "approved" | "rejected" | "cancelled";

export type ChainApprovalMode = "sequential" | "parallel";

export interface ChainConditionExplanationLike {
  rule_id: string;
  field: string;
  operator: ConditionOperator;
  value: string | number | boolean | Array<string | number | boolean> | null;
  actual: string | number | boolean | null;
  text: string;
}

export interface ChainRequirementResponseLike {
  id: string;
  sequence_order: number;
  is_base_requirement: boolean;
  triggered_by_rule_ids: string[];
  condition_explanations: ChainConditionExplanationLike[];
  explanation: string | null;
  status: ChainRequirementStatus;
  counts_toward_completion: boolean;
  superseded_at: string | null;
}

export interface ChainInstanceStepLike {
  step_id: string;
  approval_mode: ChainApprovalMode;
  requirements: ChainRequirementResponseLike[];
}

// Structural aliases so call sites can pass the real generated types (from
// `src/lib/types.ts`, once T007 lands) directly — those interfaces are
// supersets/structural matches of the *Like shapes above.
export type ChainRequirementResponse = ChainRequirementResponseLike;
export type ChainInstanceStep = ChainInstanceStepLike;

// Human-readable labels for the fixed five FR-2 comparison operators.
export const OPERATOR_LABELS: Record<ConditionOperator, string> = {
  gt: "greater than",
  lt: "less than",
  eq: "equal to",
  in: "is one of",
  contains: "contains",
};

// Formats a numeric value with thousands separators; leaves non-numbers as-is.
function formatValue(value: string | number | boolean | Array<string | number | boolean> | null): string {
  if (value === null) return "null";
  if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString("en-US") : String(value);
  if (Array.isArray(value)) return value.map((v) => formatValue(v)).join(", ");
  return String(value);
}

// Renders a single condition expression as readable text, e.g.
// `{field: "contract_value", operator: "gt", value: 1000000}` ->
// "contract_value greater than 1,000,000" (AC-6's worked example).
export function formatConditionText(expr: ConditionExpression): string {
  const label = OPERATOR_LABELS[expr.operator] ?? expr.operator;
  return `${expr.field} ${label} ${formatValue(expr.value)}`;
}

// Maps a materialized requirement's status to a UI status tone. Cancelled
// requirements (e.g. superseded by a recalculation, per the edge-case in
// spec.md) are neutral rather than red/green/amber since they no longer
// count toward anything.
export function requirementTone(
  r: ChainRequirementResponseLike,
): "green" | "amber" | "red" | "neutral" {
  switch (r.status) {
    case "approved":
      return "green";
    case "rejected":
      return "red";
    case "pending":
      return "amber";
    case "cancelled":
      return "neutral";
    default:
      return "neutral";
  }
}

// A requirement counts toward step-completion tracking only while it still
// counts toward completion (per FR-8/recalculation semantics: a requirement
// superseded by a later recalculation is not counted) and has not been
// superseded.
function isCountedRequirement(r: ChainRequirementResponseLike): boolean {
  return r.counts_toward_completion && !r.superseded_at;
}

function isTerminalStatus(status: ChainRequirementStatus): boolean {
  return status === "approved" || status === "rejected";
}

// Counts how many of a step's still-counted requirements have received a
// terminal (approved/rejected) decision, out of how many are counted at all.
export function stepProgress(step: ChainInstanceStepLike): { decided: number; total: number } {
  const counted = step.requirements.filter(isCountedRequirement);
  const decided = counted.filter((r) => isTerminalStatus(r.status)).length;
  return { decided, total: counted.length };
}

// For a sequential step, returns the lowest `sequence_order` among still-
// pending, counting requirements — i.e. who must act next. Returns null for
// a parallel step (FR-14: no ordering restriction applies) or when nothing
// is pending.
export function nextActionableSequence(step: ChainInstanceStepLike): number | null {
  if (step.approval_mode !== "sequential") return null;

  const pending = step.requirements.filter(
    (r) => isCountedRequirement(r) && r.status === "pending",
  );
  if (pending.length === 0) return null;

  return pending.reduce(
    (min, r) => (r.sequence_order < min ? r.sequence_order : min),
    pending[0].sequence_order,
  );
}
