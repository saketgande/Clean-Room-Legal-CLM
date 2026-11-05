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

// The Consultancy and DPA answers (008). The questions are the server's; the wizard adds where each sits and what
// it does, so those are what is pinned, along with the rules that decide when each one shows.
describe("the Consultancy questions on the New agreement form (008)", () => {
  const form = server.find((f) => f.key === "new_agreement")!;
  const step = AGREEMENT_FORMS.find((f) => f.key === "new_agreement")!.steps.find((s) => s.title === "Commercial terms")!;
  const q = (key: string) => form.fields.find((f) => f.key === key)!;
  const NEW = ["advice_only", "consultancy_deliverables", "fee_basis", "fee_rate", "key_individuals", "company_contact_name", "company_contact_role"];
  const ours = { agreement_type: "Consultancy", paper: "Our template", fee_basis: "Day rate" }; // a rate basis, so the rate shows too

  it("places all seven after the scope and the payment terms they belong beside, each saying what it does", () => {
    expect(step.fields.filter((k) => NEW.includes(k))).toEqual(
      ["advice_only", "consultancy_deliverables", "fee_basis", "fee_rate", "key_individuals", "company_contact_name", "company_contact_role"]);
    expect(step.fields.indexOf("scope")).toBeLessThan(step.fields.indexOf("advice_only"));
    expect(step.fields.indexOf("advice_only")).toBeLessThan(step.fields.indexOf("consultancy_deliverables"));
    expect(step.fields.indexOf("payment_terms")).toBeLessThan(step.fields.indexOf("fee_basis"));
    expect(step.fields.indexOf("fee_basis")).toBeLessThan(step.fields.indexOf("fee_rate"));
    expect(step.fields.indexOf("company_contact_role")).toBeLessThan(step.fields.indexOf("nonstandard"));
    for (const k of NEW) expect(_layout.UI[k]?.uses, `${k} says what it does`).toBeTruthy();
    expect(_layout.UI.consultancy_deliverables.uses).toMatch(/D1, D2/);
    expect(_layout.UI.key_individuals.uses).toMatch(/written consent/);
  });

  it("shows them only for Consultancy on our template", () => {
    for (const k of NEW) {
      expect(shown(q(k).show, ours), k).toBe(true);
      expect(shown(q(k).show, { ...ours, paper: "Their paper" }), `${k}, their paper`).toBe(false);
      for (const other of ["NDA", "Services (MSA)", "Buying from a vendor", "Software or SaaS", "Selling to a customer", "Something else"]) {
        expect(shown(q(k).show, { ...ours, agreement_type: other }), `${k}, ${other}`).toBe(false);
      }
    }
  });

  it("shows the rate for a rate basis only, and the deliverables unless it is advice only", () => {
    for (const [basis, rate] of [["Fixed fee", false], ["Day rate", true], ["Hourly rate", true], ["", false]] as const) {
      expect(shown(q("fee_rate").show, { ...ours, fee_basis: basis }), basis).toBe(rate);
    }
    const advice = q("advice_only").options![0];
    expect(shown(q("consultancy_deliverables").show, ours)).toBe(true);
    expect(shown(q("consultancy_deliverables").show, { ...ours, advice_only: [advice] })).toBe(false);
    expect(shown(q("consultancy_deliverables").show, { ...ours, advice_only: [] })).toBe(true);
  });

  it("declares the tables and limits the wizard reads", () => {
    const deliverables = q("consultancy_deliverables");
    expect([deliverables.kind, deliverables.min_rows, deliverables.max_rows]).toEqual(["rows", 1, 20]);
    expect(deliverables.columns!.map((c) => [c.key, c.kind])).toEqual([["name", "text"], ["due", "date"]]);
    expect(deliverables.columns!.find((c) => c.key === "due")!.open_end).toBe(true);
    const people = q("key_individuals");
    expect([people.kind, people.min_rows, people.max_rows, people.item]).toEqual(["rows", 1, 10, ["key individual", "key individuals"]]);
    expect(people.columns!.map((c) => c.key)).toEqual(["name"]);
    expect(q("fee_rate")).toMatchObject({ kind: "number", min_exclusive: 0, max_decimals: 2 });
    expect(q("fee_basis").options).toEqual(["Fixed fee", "Day rate", "Hourly rate"]);
    for (const k of ["company_contact_name", "company_contact_role"]) expect(q(k)).toMatchObject({ max_length: 120, no_blanks: true, required: true });
  });

  it("holds the scope and the total value to the new rules for Consultancy on our paper only", () => {
    for (const k of ["scope", "value"]) {
      expect(shown(q(k).rules_when, ours), k).toBe(true);
      expect(shown(q(k).rules_when, { ...ours, paper: "Their paper" }), k).toBe(false);
      expect(shown(q(k).rules_when, { ...ours, agreement_type: "Services (MSA)" }), k).toBe(false);
    }
  });
});

describe("the DPA questions on the Data Processing Agreement form (008)", () => {
  const form = server.find((f) => f.key === "dpa")!;
  const step = AGREEMENT_FORMS.find((f) => f.key === "dpa")!.steps.find((s) => s.title === "The data")!;
  const q = (key: string) => form.fields.find((f) => f.key === key)!;

  it("places the two lists straight after the question that asks for them", () => {
    expect(step.fields).toEqual(["our_role", "processing_purpose", "data_types", "data_subjects", "transfer", "transfer_countries",
      "subprocessors", "subprocessor_rows"]);
    for (const k of ["transfer_countries", "subprocessor_rows"]) expect(_layout.UI[k]?.uses, k).toBeTruthy();
  });

  it("shows the countries for Yes and the sub-processors for Yes, on our template only", () => {
    expect(shown(q("transfer_countries").show, { transfer: "Yes", paper: "Our template" })).toBe(true);
    expect(shown(q("transfer_countries").show, { transfer: "No", paper: "Our template" })).toBe(false);
    expect(shown(q("transfer_countries").show, { transfer: "Yes", paper: "Their paper" })).toBe(false);
    expect(shown(q("transfer_countries").show, { paper: "Our template" })).toBe(false);
    expect(shown(q("subprocessor_rows").show, { subprocessors: "Yes", paper: "Our template" })).toBe(true);
    expect(shown(q("subprocessor_rows").show, { subprocessors: "No", paper: "Our template" })).toBe(false);
    expect(shown(q("subprocessor_rows").show, { subprocessors: "Yes", paper: "Their paper" })).toBe(false);
  });

  it("asks for the kind of data and whose it is as ticks, and keeps an older single choice (legacy_single)", () => {
    for (const k of ["data_types", "data_subjects"]) expect(q(k)).toMatchObject({ kind: "multiselect", required: true, legacy_single: true });
    expect(q("data_types").options).toContain("Health or other special category");
    expect(q("data_subjects").options).toContain("Patients or trial subjects");
  });

  it("holds the purpose to the no-blank-brackets rule for our paper only", () => {
    expect(q("processing_purpose").no_blanks).toBe(true);
    expect(shown(q("processing_purpose").rules_when, { paper: "Our template" })).toBe(true);
    expect(shown(q("processing_purpose").rules_when, { paper: "Their paper" })).toBe(false);
  });

  it("no longer says the answers are for the annex only in general: it names Annex 1 and Annex 3, as the draft now does", () => {
    expect(_layout.UI.processing_purpose.uses).toMatch(/Annex 1/);
    expect(_layout.UI.data_types.uses).toMatch(/Annex 1/);
    expect(_layout.UI.data_subjects.uses).toMatch(/Annex 1/);
    expect(_layout.UI.transfer.uses).toMatch(/Annex 1/);
    expect(_layout.UI.subprocessors.uses).toMatch(/Annex 3/);
    expect(_layout.UI.our_role.uses).toMatch(/Processor: no draft is made from it; attach the other side's DPA/);
    expect(Object.values(_layout.UI).map((u) => u.uses)).not.toContain("DPA annex.");
  });
});

describe("every rule in the Consultancy and DPA forms points at a question that can give that answer", () => {
  for (const key of ["new_agreement", "dpa"]) {
    it(`${key}: show rules, rules_when, column rules and must_be.when name a question of the same form whose options hold their values`, () => {
      const form = server.find((f) => f.key === key)!;
      const byKey = Object.fromEntries(form.fields.map((f) => [f.key, f]));
      const rules = form.fields.flatMap((f) => [
        ...(f.show ?? []), ...(f.rules_when ?? []),
        ...(f.columns ?? []).flatMap((c) => [...(c.show ?? []), ...(c.must_be ? [c.must_be.when] : [])]),
      ]);
      expect(rules.length).toBeGreaterThan(5);
      for (const r of rules) {
        const target = byKey[r.field];
        expect(target, `${r.field} is a question of ${key}`).toBeDefined();
        for (const v of r.in) expect(target.options, `${r.field} has ${v}`).toContain(v);
      }
    });

    it(`${key}: every date-window rule names two date questions of the same form`, () => {
      const form = server.find((f) => f.key === key)!;
      const kind = Object.fromEntries(form.fields.map((f) => [f.key, f.kind]));
      for (const c of form.fields.flatMap((f) => f.columns ?? [])) {
        if (c.within) expect(c.within.map((k) => kind[k]), c.key).toEqual(["date", "date"]);
        if (c.open_end) expect(c.within, `${c.key} has an open end`).toBeDefined();
      }
    });

    it(`${key}: every question that feeds the draft (fills) is shown on a step, and so can be answered`, () => {
      const def = AGREEMENT_FORMS.find((f) => f.key === key)!;
      const placed = def.steps.flatMap((s) => s.fields);
      const fills = server.find((f) => f.key === key)!.fields.filter((f) => f.fills?.length).map((f) => f.key);
      expect(fills.length).toBeGreaterThan(3);
      expect(fills.filter((k) => !placed.includes(k))).toEqual([]);
    });
  }
});
