/**
 * Workflows sit under an agreement type (a request form, and for New agreement
 * a kind of agreement) and carry "Chosen when" conditions. A form request only
 * gets a workflow of its type whose conditions all hold — see select_flow in
 * backend/app/workflows/service.py, which this mirrors for display.
 */
import type { RequestFormDef, Workflow, WorkflowCondition } from "@/lib/types";

export interface TypeOption { key: string; label: string; short: string; form: string; agreement_type: string | null }

export function typeOptions(forms: RequestFormDef[] | undefined): TypeOption[] {
  const out: TypeOption[] = [];
  for (const f of forms ?? []) {
    const kinds = f.fields.find((x) => x.key === "agreement_type")?.options;
    if (f.key === "new_agreement" && kinds) {
      for (const k of kinds) out.push({ key: `${f.key}::${k}`, label: `New agreement · ${k}`, short: k, form: f.key, agreement_type: k });
    } else if (f.key !== "cancellation") {
      out.push({ key: `${f.key}::`, label: f.name, short: f.name, form: f.key, agreement_type: null });
    }
  }
  return out;
}

export const typeKey = (w: Pick<Workflow, "criteria">): string | null => {
  const u = w.criteria.used_for?.[0];
  return u ? `${u.form}::${u.agreement_type ?? ""}` : null;
};

export interface Question { field: string; label: string; amount: boolean; date?: boolean; options: string[] }

/** What a workflow of this type may be conditioned on: the choices and the
 * money figure its form asks — for New agreement, only for that kind. */
export function questionsFor(forms: RequestFormDef[] | undefined, form: string, kind: string | null): Question[] {
  const def = forms?.find((f) => f.key === form);
  if (!def) return [];
  const askedFor = (rules: RequestFormDef["fields"][number]["show"]) =>
    !kind || (rules ?? []).every((r) => r.field !== "agreement_type" || r.in.includes(kind));
  const out: Question[] = [];
  for (const f of def.fields) {
    if (f.key === "agreement_type" || !askedFor(f.show)) continue;
    if (f.kind === "number" && ["value", "new_value", "renew_value"].includes(f.key)) {
      // The money figure is the most common condition, so it leads the list.
      if (!out.some((q) => q.field === "value")) out.unshift({ field: "value", label: "Contract value", amount: true, options: [] });
    } else if (f.kind === "date") {
      out.push({ field: f.key, label: f.label, amount: false, date: true, options: [] });
    } else if ((f.kind === "select" || f.kind === "multiselect") && f.key !== "currency" && f.options?.length) {
      out.push({ field: f.key, label: f.label.replace(/\?$/, ""), amount: false, options: f.options });
    }
  }
  return out;
}

export const OP_LABEL: Record<WorkflowCondition["op"], string> = {
  is: "is", is_not: "is not", under: "is under", at_least: "is at least", between: "is between",
  within_days: "is within",
};
export const AMOUNT_OPS: WorkflowCondition["op"][] = ["under", "at_least", "between"];
export const CHOICE_OPS: WorkflowCondition["op"][] = ["is", "is_not"];
export const DATE_OPS: WorkflowCondition["op"][] = ["within_days"];

const money = (cur: string | undefined, n: unknown) =>
  `${cur || "INR"} ${Number(n || 0).toLocaleString(cur && cur !== "INR" ? "en-US" : "en-IN")}`;

export function conditionText(c: WorkflowCondition, label?: string): string {
  const name = (label ?? (c.field === "value" ? "Contract value" : c.field.replace(/_/g, " "))).toLowerCase();
  if (c.op === "within_days") return `${name} is within ${c.value} day${Number(c.value) === 1 ? "" : "s"}`;
  if (c.op === "between") return `${name} is between ${money(c.currency, c.value)} and ${money(c.currency, c.value2)}`;
  if (AMOUNT_OPS.includes(c.op)) return `${name} ${OP_LABEL[c.op]} ${money(c.currency, c.value)}`;
  return `${name} ${OP_LABEL[c.op]} ${c.value}`;
}

export function whenText(conds: WorkflowCondition[] | undefined, labels?: Record<string, string>): string {
  return conds?.length ? conds.map((c) => conditionText(c, labels?.[c.field])).join(" and ") : "always (no conditions)";
}

function range(c: WorkflowCondition): [number, number] {
  if (c.op === "under") return [-Infinity, Number(c.value)];
  if (c.op === "at_least") return [Number(c.value), Infinity];
  return [Number(c.value), Number(c.value2)];
}

/** Could one request meet both sets of conditions? Only a question they both
 * test, with answers that can't both hold, keeps them apart. */
export function overlaps(a: WorkflowCondition[] = [], b: WorkflowCondition[] = []): boolean {
  for (const x of a) for (const y of b) {
    if (x.field !== y.field) continue;
    if (AMOUNT_OPS.includes(x.op) && AMOUNT_OPS.includes(y.op)) {
      if ((x.currency || "INR") !== (y.currency || "INR")) return false;
      const [a0, a1] = range(x), [b0, b1] = range(y);
      if (a1 <= b0 || b1 <= a0) return false;
    } else if ((x.op === "is" && y.op === "is" && x.value !== y.value) || (x.op !== y.op && x.value === y.value)) {
      return false;
    }
  }
  return true;
}
