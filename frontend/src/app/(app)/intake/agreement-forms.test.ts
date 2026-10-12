import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { AGREEMENT_FORMS, _layout, shown } from "./_agreement-forms";

type ServerForm = { key: string; name: string; fields: { key: string }[] };

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
