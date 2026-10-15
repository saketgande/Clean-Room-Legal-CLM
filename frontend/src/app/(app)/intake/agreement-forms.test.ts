import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { AGREEMENT_FORMS, _layout, shown } from "./_agreement-forms";
import type { RequestFormDef } from "@/lib/types";

type ServerForm = RequestFormDef;

// The wizard renders the server's questions, so labels and options can't drift;
// what CAN drift is the layout: a server question no step shows would be
// required on filing but impossible to answer.
const server: ServerForm[] = JSON.parse(
  readFileSync(resolve(__dirname, "../../../../../backend/app/intake/agreement_forms.json"), "utf8"),
);

describe("request form layout matches the server's forms", () => {
  it("has the same forms, in order, with the same names", () => {
    expect(server.map((f) => [f.key, f.name])).toEqual(AGREEMENT_FORMS.map((d) => [d.key, d.name]));
  });

  for (const def of AGREEMENT_FORMS) {
    it(`${def.key}: every question is placed, and nothing unknown is`, () => {
      const placed = def.steps.flatMap((s) => s.fields);
      const covered = new Set([...placed, ...placed.flatMap((k) => _layout.COMPANION[k] ?? []), "note_approvers"]);
      const keys = server.find((s) => s.key === def.key)!.fields.map((f) => f.key);
      expect(keys.filter((k) => !covered.has(k))).toEqual([]);
      expect(placed.filter((k) => !keys.includes(k) && !_layout.FILE_KEYS.includes(k))).toEqual([]);
      for (const k of placed) expect(_layout.UI[k]?.uses, `${k} says what it does`).toBeTruthy();
    });
  }
});

describe("show-when rules", () => {
  it("needs every rule to match; a multi-select matches on any pick", () => {
    const rules = [{ field: "agreement_type", in: ["Services (MSA)"] }, { field: "term", in: ["Renews automatically"] }];
    expect(shown(rules, { agreement_type: "Services (MSA)", term: "Renews automatically" })).toBe(true);
    expect(shown(rules, { agreement_type: "NDA", term: "Renews automatically" })).toBe(false);
    expect(shown([{ field: "what_changes", in: ["Value"] }], { what_changes: ["Dates", "Value"] })).toBe(true);
    expect(shown(undefined, {})).toBe(true);
  });
});

// The Statement of Work's deliverables are a table (007). Its columns and rules are the server's; what the
// wizard adds is where it sits and what it drives, so those are the things to pin.
describe("the Statement of Work's deliverables table", () => {
  const sow = server.find((f) => f.key === "sow")!;
  const table = sow.fields.find((f) => f.key === "deliverable_rows")!;
  const step = AGREEMENT_FORMS.find((f) => f.key === "sow")!.steps.find((s) => s.title === "Scope and fees")!;

  it("is a table question on the Scope and fees step, says what it does, and replaces the free-text one", () => {
    expect(table.kind).toBe("rows");
    expect(table.required).toBe(true);
    expect([table.min_rows, table.max_rows]).toEqual([1, 20]);
    expect(step.fields).toContain("deliverable_rows");
    expect(step.fields).not.toContain("deliverables");
    expect(_layout.UI.deliverable_rows.uses).toMatch(/one entry per row in this order \(D1, D2/);
    expect(_layout.UI.deliverables).toBeUndefined();
    // The old key survives only as the place older requests kept their text.
    expect(sow.fields.map((f) => f.key)).not.toContain("deliverables");
    expect([table.legacy_key, table.legacy_label]).toEqual(["deliverables", "Deliverables and who accepts them"]);
  });

  it("declares the five columns, and the share column shows only while the pricing is Milestones", () => {
    expect(table.columns!.map((c) => [c.key, c.kind])).toEqual([
      ["name", "text"], ["due", "date"], ["their_owner", "text"], ["our_owner", "text"], ["share", "percent"],
    ]);
    const share = table.columns!.find((c) => c.key === "share")!;
    expect(share.total).toBe(100);
    expect(shown(share.show, { pricing: "Milestones" })).toBe(true);
    for (const other of ["Fixed fee", "Time and materials", ""]) expect(shown(share.show, { pricing: other })).toBe(false);
    expect(table.columns!.filter((c) => c.key !== "share").every((c) => !c.show)).toBe(true);
  });

  it("points every column rule at a question of the same form that exists and means what it says", () => {
    const kind = Object.fromEntries(sow.fields.map((f) => [f.key, f.kind]));
    const due = table.columns!.find((c) => c.key === "due")!;
    expect(due.within!.map((k) => kind[k])).toEqual(["date", "date"]);
    for (const c of table.columns!) for (const r of c.show ?? []) {
      const q = sow.fields.find((f) => f.key === r.field)!;
      expect(q.kind, `${c.key} shows on ${r.field}`).toBe("select");
      for (const v of r.in) expect(q.options, `${r.field} has ${v}`).toContain(v);
    }
  });

  it("refuses blank brackets in the answers that reach the draft, and a total value of zero", () => {
    for (const k of ["sow_title", "scope", "cp_signer_name", "cp_signer_email"]) {
      expect(sow.fields.find((f) => f.key === k)!.no_blanks, k).toBe(true);
    }
    expect(sow.fields.find((f) => f.key === "value")!.min_exclusive).toBe(0);
    expect(table.columns!.filter((c) => c.kind === "text").every((c) => c.no_blanks)).toBe(true);
  });
});
