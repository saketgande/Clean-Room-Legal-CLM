import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  answerProblem, dateWindow, EMAIL_PROBLEM, findBlanks, lacksAnswer, longDate, looksLikeEmail, needsDraftAnswers, pastDueRows,
  PROCESSOR_ROLE, rowsProblems, rulesFor, shareTotal, shown, tidiedRows, tidyCell,
} from "./form-checks";
import type { FormRow, RequestFormDef, RequestFormField } from "./types";

// The wizard and the server must refuse and accept the same tables, the same bracketed blanks and the same
// numbers (007, FR-16). Both sides run ONE cases file: backend/tests/fixtures/deliverable_rows_cases.json is
// read by the backend suite (clean_rows) and by this one (rowsProblems, findBlanks, answerProblem). It is read
// with readFileSync, like agreement-forms.test.ts reads the form definitions, so the Vite config needs nothing.
const backend = resolve(__dirname, "../../../backend");
interface RowsCase { id: string; values: Record<string, unknown>; rows: unknown; error: string | null; stored?: FormRow[] }
interface FieldCase { id: string; field: { key: string; label: string; kind: string; no_blanks?: boolean; min_exclusive?: number };
  value: string | number; error: string | null }
const cases: { rows: RowsCase[]; fields: FieldCase[]; blanks: { text: string; blanks: string[] }[] } =
  JSON.parse(readFileSync(resolve(backend, "tests/fixtures/deliverable_rows_cases.json"), "utf8"));
const forms: RequestFormDef[] = JSON.parse(readFileSync(resolve(backend, "app/intake/agreement_forms.json"), "utf8"));
const sow = forms.find((f) => f.key === "sow")!;
const table = sow.fields.find((f) => f.key === "deliverable_rows") as RequestFormField;
const labels = Object.fromEntries(sow.fields.map((f) => [f.key, f.label]));

describe("looksLikeEmail (the same rule as the server)", () => {
  it.each(["maya@example.com", " maya@example.com ", "maya.chen+legal@sub.example.co.in"])("accepts %s", (ok) => {
    expect(looksLikeEmail(ok)).toBe(true);
  });

  it.each(["maya.example.com", "maya@", "@example.com", "maya@example", "ma ya@example.com", "maya@@example.com",
    "maya@exa..mple.com", "maya@example.", "a@b@c.com", "maya@.example.com", ""])("refuses %j", (bad) => {
    expect(looksLikeEmail(bad)).toBe(false);
  });

  it("refuses an address longer than a mailbox can be", () => {
    expect(looksLikeEmail(`${"a".repeat(250)}@example.com`)).toBe(false);
  });
});

describe("answerProblem", () => {
  it("says nothing about an empty answer: whether it is required is another question", () => {
    expect(answerProblem("email", "")).toBeNull();
    expect(answerProblem("email", undefined)).toBeNull();
    expect(answerProblem("date", "  ", { label: "Start date", value: "2026-10-01" })).toBeNull();
  });

  it("names what is wrong with an email with no @", () => {
    expect(answerProblem("email", "maya.example.com")).toBe(EMAIL_PROBLEM);
    expect(answerProblem("email", "maya@example.com")).toBeNull();
  });

  it("refuses an end date before its start date, and says which date", () => {
    const start = { label: "Start date", value: "2026-10-01" };
    expect(answerProblem("date", "2026-01-01", start)).toBe("Can't be before start date.");
    expect(answerProblem("date", "2026-10-01", start)).toBeNull(); // the same day is allowed
    expect(answerProblem("date", "2027-09-30", start)).toBeNull();
  });

  it("does not compare against a start date that is not filled in yet", () => {
    expect(answerProblem("date", "2026-01-01", { label: "Start date", value: "" })).toBeNull();
    expect(answerProblem("date", "2026-01-01", { label: "Start date", value: undefined })).toBeNull();
    expect(answerProblem("date", "2026-01-01")).toBeNull();
  });

  it("does not compare a date that is still half typed", () => {
    expect(answerProblem("date", "2026-1", { label: "Start date", value: "2026-10-01" })).toBeNull();
  });
});

// A share is stored as "40.00"; the wizard keeps what was typed, so compare them without trailing zeros.
const plainShare = (c: string) => (c.includes(".") ? c.replace(/\.?0+$/, "") : c);
const plain = (rows: FormRow[]) => rows.map((r) => ({ ...r, ...(r.share !== undefined ? { share: plainShare(r.share) } : {}) }));

describe("the shared cases file is really there", () => {
  it("holds every section, so an empty read cannot make the loops below pass for nothing", () => {
    expect(cases.rows.length).toBeGreaterThan(100);
    expect(cases.fields.length).toBeGreaterThan(10);
    expect(cases.blanks.length).toBeGreaterThan(40);
    expect(table.kind).toBe("rows");
  });
});

describe("rowsProblems gives the server's first message for every shared table case", () => {
  for (const c of cases.rows) {
    it(`${c.id}: ${c.error ?? "accepted"}`, () => {
      const problems = rowsProblems(table, c.rows, c.values, labels);
      expect(problems[0]?.message ?? null).toBe(c.error);
      if (c.error === null) {
        expect(problems).toEqual([]);
        // What the server keeps (tidied cells, shown columns, empty rows gone) is what the wizard reads too.
        expect(plain(tidiedRows(table, c.rows, c.values))).toEqual(plain(c.stored!));
      }
    });
  }

  it("lists every problem in the server's order, so the banner can name all of them", () => {
    const rows = [
      { name: "Plan", due: "2026-10-01", their_owner: "", our_owner: "Ravi", share: "50" },
      { name: "plan", due: "2026-11-30", their_owner: "Priya", our_owner: "Ravi", share: "50" },
    ];
    const values = { services_start: "2026-11-01", services_end: "2027-03-31", pricing: "Milestones" };
    expect(rowsProblems(table, rows, values, labels).map((p) => [p.row, p.column, p.message])).toEqual([
      [1, "due", '"Deliverables", row 1: "Due date" must be between 1 November 2026 and 31 March 2027 (Services start to Services end).'],
      [1, "their_owner", '"Deliverables", row 1: "Their owner" is required.'],
      [2, "name", '"Deliverables", rows 1 and 2 have the same "Deliverable name" ("Plan"). Give each row its own.'],
    ]);
  });

  it("puts a row, a column and a message on every problem, and none on a table-wide one", () => {
    const [m3] = rowsProblems(table, [], {}, labels);
    expect(m3).toMatchObject({ row: null, column: null });
    const [m5] = rowsProblems(table, [{ name: "", due: "2026-11-30", their_owner: "a", our_owner: "b" }], {}, labels);
    expect(m5).toMatchObject({ row: 1, column: "name" });
    const total = rowsProblems(table, [{ name: "a", due: "2026-11-30", their_owner: "a", our_owner: "b", share: "40" }], { pricing: "Milestones" }, labels);
    expect(total).toEqual([{ row: null, column: "share", message: '"Deliverables": the "Share of the fee (%)" column adds up to 40%, 60 short of 100%.' }]);
  });

  it("treats a missing answer as no rows, the way the server treats a required answer that is not there", () => {
    expect(rowsProblems(table, undefined, {}, labels)[0].message).toBe('"Deliverables" needs at least 1 row.');
    expect(rowsProblems(table, "", {}, labels)[0].message).toBe('"Deliverables" needs at least 1 row.');
  });
});

describe("findBlanks gives the list the server's find_blanks gives (shared cases)", () => {
  for (const c of cases.blanks) {
    it(JSON.stringify(c.text).slice(0, 60), () => {
      expect(findBlanks(c.text)).toEqual(c.blanks);
    });
  }

  it("stops at twenty different blanks", () => {
    const text = Array.from({ length: 30 }, (_, i) => `[Blank ${String.fromCharCode(65 + (i % 26))}${i}]`).join(" ");
    expect(findBlanks(text)).toHaveLength(20);
  });
});

describe("answerProblem: the single-field rules (shared cases)", () => {
  // The server words these with the question's label ('Field "SoW title" contains [TBD], ...'); the wizard
  // shows them under the label, so its sentence starts at the verb. Same refusal, same bracket or number.
  const bare = (s: string) => s.replace(/^Field "[^"]*" /, "").replace(/\.$/, "").replace(/^./, (x) => x.toLowerCase());
  for (const c of cases.fields) {
    it(`${c.id}: ${c.error ?? "accepted"}`, () => {
      const problem = answerProblem(c.field.kind, String(c.value), undefined,
        { no_blanks: c.field.no_blanks, min_exclusive: c.field.min_exclusive });
      if (c.error === null) expect(problem).toBeNull();
      else expect(bare(problem ?? "")).toBe(bare(c.error));
    });
  }

  it("leaves an email that cannot be delivered to the email rule, and a good one to the blank rule", () => {
    expect(answerProblem("email", "[name]@example", undefined, { no_blanks: true })).toBe(EMAIL_PROBLEM);
    expect(answerProblem("email", "[name]@example.com", undefined, { no_blanks: true })).toMatch(/^Contains \[name\],/);
  });

  it("only refuses a number that is not above the minimum, not one that is half typed", () => {
    expect(answerProblem("number", "1,000", undefined, { min_exclusive: 0 })).toBeNull();
    expect(answerProblem("number", "abc", undefined, { min_exclusive: 0 })).toBeNull();
    expect(answerProblem("number", "0", undefined, { min_exclusive: 0 })).toBe("Must be more than 0.");
    expect(answerProblem("number", "0", undefined, {})).toBeNull();
  });
});

describe("tidyCell", () => {
  it("collapses and trims ASCII whitespace only", () => {
    expect(tidyCell("  Migration \t plan\r\n")).toBe("Migration plan");
    expect(tidyCell("a\u00a0b")).toBe("a\u00a0b"); // a no-break space is text
    expect(tidyCell("\u00a0a")).toBe("\u00a0a");
    expect(tidyCell(null)).toBe("");
    expect(tidyCell(undefined)).toBe("");
    expect(tidyCell(40)).toBe("40");
  });
});

describe("longDate", () => {
  it("writes a day the way the message and the draft do", () => {
    expect(longDate("2026-11-01")).toBe("1 November 2026");
    expect(longDate("2027-03-31")).toBe("31 March 2027");
    expect(longDate("2028-02-29")).toBe("29 February 2028");
  });
});

describe("shareTotal", () => {
  const rows = (...shares: string[]): FormRow[] => shares.map((share) => ({ name: "x", share }));
  it("adds the shares as they are typed, to two decimals, with no trailing zeros", () => {
    expect(shareTotal(table, rows("40", "35", "24"))).toBe("99");
    expect(shareTotal(table, rows("40", "35", "25"))).toBe("100");
    expect(shareTotal(table, rows("12.5", "37.5", "25"))).toBe("75");
    expect(shareTotal(table, rows("33.33", "33.33", "33.34"))).toBe("100");
    expect(shareTotal(table, rows("49.5", "50"))).toBe("99.5");
    expect(shareTotal(table, rows("0.01"))).toBe("0.01");
  });
  it("counts nothing for a cell that is empty or not yet a share, and 0 for no rows", () => {
    expect(shareTotal(table, rows("40", "", "abc", "33.333", " 10 "))).toBe("50");
    expect(shareTotal(table, [])).toBe("0");
  });
  it("is null for a table with no share column", () => {
    expect(shareTotal({ ...table, columns: table.columns!.filter((c) => c.key !== "share") }, rows("40"))).toBeNull();
  });
});

describe("pastDueRows", () => {
  const rows: FormRow[] = [{ due: "2026-11-30" }, {}, { due: "2026-12-15" }, { due: "2027-01-10" }, { due: "soon" }];
  it("numbers the rows whose due date is before today, counting empty rows, and leaves today and later alone", () => {
    expect(pastDueRows(table, rows, "2026-12-15")).toEqual([1]);
    expect(pastDueRows(table, rows, "2026-12-16")).toEqual([1, 3]);
    expect(pastDueRows(table, rows, "2026-01-01")).toEqual([]);
  });
});

describe("the window and the show rules the table reads", () => {
  it("knows the Services window only when both ends are real days", () => {
    const due = table.columns!.find((c) => c.key === "due")!;
    expect(dateWindow(due, { services_start: "2026-11-01", services_end: "2027-03-31" })).toMatchObject({ start: "2026-11-01", end: "2027-03-31" });
    expect(dateWindow(due, { services_start: "2026-11-01" })).toBeNull();
    expect(dateWindow(due, { services_start: "2026-02-30", services_end: "2027-03-31" })).toBeNull();
  });

  it("shows the Share column for Milestones only", () => {
    const share = table.columns!.find((c) => c.key === "share")!;
    expect(shown(share.show, { pricing: "Milestones" })).toBe(true);
    expect(shown(share.show, { pricing: "Fixed fee" })).toBe(false);
    expect(shown(share.show, {})).toBe(false);
  });
});

// ---------------------------------------------------------------------------------------------- 008 ----
// The Consultancy and DPA answers (008). Their cases are a SIBLING file, consultancy_dpa_cases.json, read by the
// backend suite (clean_rows and the filing validator) and by this one, so the wizard and filing cannot drift.
// Each case names its form and question; the question is read from agreement_forms.json, never copied here.

interface Case008Rows { id: string; form: string; question: string; values: Record<string, unknown>; rows: unknown;
  error: string | null; stored?: FormRow[] }
interface Case008Field { id: string; form: string; question: string; values: Record<string, unknown>;
  value: string | number; error: string | null }
const cases008: { tables: Case008Rows[]; fields: Case008Field[] } =
  JSON.parse(readFileSync(resolve(backend, "tests/fixtures/consultancy_dpa_cases.json"), "utf8"));
const questionOf = (form: string, key: string): RequestFormField => {
  const f = forms.find((x) => x.key === form)?.fields.find((x) => x.key === key);
  if (!f) throw new Error(`${form}.${key} is not in agreement_forms.json`);
  return f;
};
const labelsOf = (form: string) => Object.fromEntries(forms.find((x) => x.key === form)!.fields.map((f) => [f.key, f.label]));

describe("the 008 shared cases file is really there", () => {
  it("holds both sections, on all four tables, so an empty read cannot make the loops below pass for nothing", () => {
    expect(cases008.tables.length).toBeGreaterThan(200);
    expect(cases008.fields.length).toBeGreaterThan(70);
    expect(new Set(cases008.tables.map((c) => `${c.form}.${c.question}`))).toEqual(new Set([
      "new_agreement.consultancy_deliverables", "new_agreement.key_individuals", "dpa.transfer_countries", "dpa.subprocessor_rows",
    ]));
    expect(new Set(cases008.fields.map((c) => c.question))).toEqual(new Set([
      "scope", "value", "fee_rate", "company_contact_name", "company_contact_role", "processing_purpose",
    ]));
    expect(new Set([...cases008.tables, ...cases008.fields].map((c) => c.id)).size).toBe(cases008.tables.length + cases008.fields.length);
  });
});

describe("rowsProblems gives the server's first message for every 008 table case", () => {
  for (const c of cases008.tables) {
    it(`${c.id}: ${c.error ?? "accepted"}`, () => {
      const field = questionOf(c.form, c.question);
      const problems = rowsProblems(field, c.rows, c.values, labelsOf(c.form));
      expect(problems[0]?.message ?? null).toBe(c.error);
      if (c.error === null) {
        expect(problems).toEqual([]);
        // What the server keeps (tidied cells, empty rows gone) is what the wizard reads too.
        expect(tidiedRows(field, c.rows, c.values)).toEqual(c.stored);
      }
    });
  }
});

describe("answerProblem gives the server's verdict for every 008 single-question case", () => {
  // The server words these with the question's label; the wizard shows them under it, so its sentence starts at
  // the verb. Same refusal, same bracket or number or limit, so compare with the label and full stop stripped.
  const bare = (s: string) => s.replace(/^Field "[^"]*" /, "").replace(/\.$/, "").replace(/^./, (x) => x.toLowerCase());
  for (const c of cases008.fields) {
    it(`${c.id}: ${c.error ?? "accepted"}`, () => {
      const field = questionOf(c.form, c.question);
      // A JSON number is what an API client may send; the wizard always holds text.
      const problem = answerProblem(field.kind, String(c.value), undefined, rulesFor(field, c.values));
      if (c.error === null) expect(problem).toBeNull();
      else expect(bare(problem ?? "")).toBe(bare(c.error));
    });
  }
});

describe("the 008 table rules, one by one", () => {
  const countries = questionOf("dpa", "transfer_countries");
  const subs = questionOf("dpa", "subprocessor_rows");
  const due = questionOf("new_agreement", "consultancy_deliverables");
  const dpaLabels = labelsOf("dpa");
  const naLabels = labelsOf("new_agreement");

  it("lists every problem of a table in the server's order, so the banner can name all of them", () => {
    const rows = [{ country: "India" }, { country: "Singapore" }, { country: "singapore " }];
    expect(rowsProblems(countries, rows, { transfer: "Yes" }, dpaLabels).map((p) => [p.row, p.column, p.message])).toEqual([
      [1, "country", '"Countries outside India", row 1: "Country" is India. List only the countries outside India.'],
      [3, "country", '"Countries outside India", rows 2 and 3 have the same "Country" ("Singapore"). Give each row its own.'],
    ]);
  });

  it("asks a sub-processor's country to be India only while data does not leave India", () => {
    const rows = [{ name: "CloudHost Pte Ltd", country: "Singapore", processing: "Hosting" }];
    expect(rowsProblems(subs, rows, { transfer: "Yes" }, dpaLabels)).toEqual([]);
    expect(rowsProblems(subs, rows, {}, dpaLabels)).toEqual([]);
    expect(rowsProblems(subs, rows, { transfer: "No" }, dpaLabels)[0]).toMatchObject({ row: 1, column: "country",
      message: '"Sub-processors", row 1: "Country" is "Singapore", but the answer to "Will data leave India?" is No. Enter India, or change that answer.' });
  });

  it("checks the due dates against the start only while there is no end date, and says so", () => {
    const rows = [{ name: "Report", due: "2026-10-31" }];
    expect(rowsProblems(due, rows, { start_date: "2026-11-01" }, naLabels)[0].message)
      .toBe('"Deliverables", row 1: "Due date" must be on or after 1 November 2026 (Start date).');
    expect(rowsProblems(due, [{ name: "Report", due: "2030-01-01" }], { start_date: "2026-11-01" }, naLabels)).toEqual([]);
    // With an end the whole window applies, as in 007.
    expect(rowsProblems(due, [{ name: "Report", due: "2030-01-01" }], { start_date: "2026-11-01", end_date: "2027-04-30" }, naLabels)[0].message)
      .toBe('"Deliverables", row 1: "Due date" must be between 1 November 2026 and 30 April 2027 (Start date to End date).');
  });
});

describe("dateWindow with an open end", () => {
  const due = questionOf("new_agreement", "consultancy_deliverables").columns!.find((c) => c.key === "due")!;
  const sowDue = table.columns!.find((c) => c.key === "due")!;

  it("is the start alone while the end answer is not a day, and no window while the start is not", () => {
    expect(dateWindow(due, { start_date: "2026-11-01", end_date: "2027-04-30" })).toEqual({
      start: "2026-11-01", end: "2027-04-30", startKey: "start_date", endKey: "end_date" });
    for (const end of [undefined, null, "", "n/a", "2027-02-30"]) {
      expect(dateWindow(due, { start_date: "2026-11-01", end_date: end })).toEqual({
        start: "2026-11-01", end: null, startKey: "start_date", endKey: "end_date" });
    }
    expect(dateWindow(due, { end_date: "2027-04-30" })).toBeNull();
    expect(dateWindow(due, { start_date: "soon", end_date: "2027-04-30" })).toBeNull();
    expect(dateWindow(due, {})).toBeNull();
  });

  it("leaves 007's window alone: with no open end, a missing end day is no window at all", () => {
    expect(dateWindow(sowDue, { services_start: "2026-11-01" })).toBeNull();
  });
});

describe("shown with `not`", () => {
  const rules = [{ field: "advice_only", in: ["There are no separate deliverables (advice only)"], not: true }];
  it("matches when the answer is none of the values: unanswered, empty, or something else", () => {
    expect(shown(rules, {})).toBe(true);
    expect(shown(rules, { advice_only: [] })).toBe(true);
    expect(shown(rules, { advice_only: ["Something else"] })).toBe(true);
  });
  it("does not match when the answer holds one of them, in a list or alone", () => {
    expect(shown(rules, { advice_only: ["There are no separate deliverables (advice only)"] })).toBe(false);
    expect(shown(rules, { advice_only: "There are no separate deliverables (advice only)" })).toBe(false);
  });
  it("is still needed together with the other rules", () => {
    expect(shown([{ field: "a", in: ["x"] }, ...rules], { a: "y" })).toBe(false);
    expect(shown([{ field: "a", in: ["x"] }, ...rules], { a: "x" })).toBe(true);
  });
  it("hides the Consultancy deliverables table for advice only and for any other kind or paper", () => {
    const t = questionOf("new_agreement", "consultancy_deliverables");
    const base = { agreement_type: "Consultancy", paper: "Our template" };
    expect(shown(t.show, base)).toBe(true);
    expect(shown(t.show, { ...base, advice_only: ["There are no separate deliverables (advice only)"] })).toBe(false);
    expect(shown(t.show, { ...base, paper: "Their paper" })).toBe(false);
    expect(shown(t.show, { ...base, agreement_type: "Services (MSA)" })).toBe(false);
  });
});

describe("answerProblem: the 008 rules in their server order", () => {
  it("counts a text answer after tidying, in code points, and puts length before brackets", () => {
    const rules = { max_length: 5, no_blanks: true };
    expect(answerProblem("text", "  ab   cd  ", undefined, rules)).toBeNull(); // "ab cd" is 5
    expect(answerProblem("text", "abcdef", undefined, rules)).toBe("Is longer than 5 characters.");
    expect(answerProblem("text", "[TBD] more", undefined, rules)).toBe("Is longer than 5 characters.");
    expect(answerProblem("text", "[TBD]", undefined, rules)).toMatch(/^Contains \[TBD\],/);
    expect(answerProblem("text", "😀😀😀😀😀", undefined, rules)).toBeNull(); // 5 code points, 10 UTF-16 units
    expect(answerProblem("text", "ab\u00a0cde", undefined, rules)).toBe("Is longer than 5 characters."); // a no-break space is text
  });

  it("with max_decimals: not a plain number, then above the floor, then decimal places", () => {
    const rules = { min_exclusive: 0, max_decimals: 2 };
    for (const bad of ["1e3", ".5", "5.", "+25", "abc", "1.2.3", "２５", "25 000"]) {
      expect(answerProblem("number", bad, undefined, rules), bad).toBe("Must be a number.");
    }
    expect(answerProblem("number", "-0.005", undefined, rules)).toBe("Must be more than 0.");
    expect(answerProblem("number", "0", undefined, rules)).toBe("Must be more than 0.");
    expect(answerProblem("number", "25.001", undefined, rules)).toBe("Can have at most 2 decimal places.");
    expect(answerProblem("number", "3,500.125", undefined, rules)).toBe("Can have at most 2 decimal places.");
    for (const ok of ["25000", "25000.50", "1,500.25", " 25000 ", "0.01"]) expect(answerProblem("number", ok, undefined, rules), ok).toBeNull();
  });

  it("without max_decimals a half-typed number is still left alone (007)", () => {
    expect(answerProblem("number", "1e3", undefined, { min_exclusive: 0 })).toBeNull();
    expect(answerProblem("number", "25.001", undefined, { min_exclusive: 0 })).toBeNull();
  });
});

describe("rulesFor", () => {
  const scope = questionOf("new_agreement", "scope");
  const value = questionOf("new_agreement", "value");
  const rate = questionOf("new_agreement", "fee_rate");
  const purpose = questionOf("dpa", "processing_purpose");

  it("gives a question's rules only while its rules_when matches", () => {
    const ours = { agreement_type: "Consultancy", paper: "Our template" };
    expect(rulesFor(scope, ours)).toEqual({ no_blanks: true });
    expect(rulesFor(value, ours)).toEqual({ min_exclusive: 0 });
    expect(rulesFor(scope, { ...ours, paper: "Their paper" })).toEqual({});
    expect(rulesFor(scope, { ...ours, agreement_type: "Services (MSA)" })).toEqual({});
    expect(rulesFor(value, { ...ours, agreement_type: "Buying from a vendor" })).toEqual({});
    expect(rulesFor(purpose, { paper: "Our template" })).toEqual({ no_blanks: true });
    expect(rulesFor(purpose, { paper: "Their paper" })).toEqual({});
  });

  it("gives all of a question's rules when it has no rules_when", () => {
    expect(rulesFor(rate, {})).toEqual({ min_exclusive: 0, max_decimals: 2 });
    expect(rulesFor(questionOf("new_agreement", "company_contact_name"), {})).toEqual({ no_blanks: true, max_length: 120 });
    expect(rulesFor(questionOf("new_agreement", "paper"), {})).toEqual({});
  });
});

describe("lacksAnswer", () => {
  const types = questionOf("dpa", "data_types");
  const bases = questionOf("new_agreement", "fee_basis");
  const rows = questionOf("dpa", "transfer_countries");

  it("is true for missing, empty, and blank answers", () => {
    for (const v of [undefined, null, "", "  ", [], [""], ["  "]]) expect(lacksAnswer(bases, v), JSON.stringify(v)).toBe(true);
    expect(lacksAnswer(rows, [{ country: "" }, {}])).toBe(true);
  });
  it("is true for an older single choice of a multi-tick question that is no longer an option", () => {
    expect(lacksAnswer(types, "Several of these")).toBe(true);
    expect(lacksAnswer(types, "Several")).toBe(true);
    expect(lacksAnswer(types, "Financial")).toBe(false); // an older single choice that still is one
    expect(lacksAnswer(types, ["Financial", "Employee / HR"])).toBe(false);
  });
  it("is false for an answer that is there", () => {
    expect(lacksAnswer(bases, "Day rate")).toBe(false);
    expect(lacksAnswer(questionOf("new_agreement", "fee_rate"), 25000)).toBe(false);
    expect(lacksAnswer(rows, [{ country: "Singapore" }])).toBe(false);
  });
});

describe("needsDraftAnswers", () => {
  const na = forms.find((f) => f.key === "new_agreement")!;
  const dpa = forms.find((f) => f.key === "dpa")!;
  const keys = (f: RequestFormField[]) => f.map((x) => x.key);
  const consultancy = { request_form: "new_agreement", agreement_type: "Consultancy", paper: "Our template" };
  const complete = { ...consultancy, scope: "Advise.", fee_basis: "Fixed fee", key_individuals: [{ name: "Anita Rao" }],
    company_contact_name: "Ravi Shah", company_contact_role: "Head of Quality",
    consultancy_deliverables: [{ name: "Report", due: "2026-12-15" }] };

  it("names the five answers an older Consultancy request lacks", () => {
    expect(keys(needsDraftAnswers(na, { ...consultancy, scope: "Advise.", value: "1000" }))).toEqual([
      "consultancy_deliverables", "fee_basis", "key_individuals", "company_contact_name", "company_contact_role"]);
  });
  it("is empty for a complete request, and for advice only without the table", () => {
    expect(needsDraftAnswers(na, complete)).toEqual([]);
    const { consultancy_deliverables: _drop, ...rest } = complete;
    expect(keys(needsDraftAnswers(na, rest))).toEqual(["consultancy_deliverables"]);
    expect(needsDraftAnswers(na, { ...rest, advice_only: ["There are no separate deliverables (advice only)"] })).toEqual([]);
  });
  it("asks for the rate once a rate basis is chosen, and not for Fixed fee", () => {
    expect(keys(needsDraftAnswers(na, { ...complete, fee_basis: "Day rate" }))).toEqual(["fee_rate"]);
    expect(needsDraftAnswers(na, { ...complete, fee_basis: "Day rate", fee_rate: "25000" })).toEqual([]);
  });
  it("is empty for their paper and for the kinds of agreement that ask nothing of this", () => {
    expect(needsDraftAnswers(na, { request_form: "new_agreement", agreement_type: "Consultancy", paper: "Their paper" })).toEqual([]);
    expect(needsDraftAnswers(na, { request_form: "new_agreement", agreement_type: "Services (MSA)", paper: "Our template" })).toEqual([]);
  });

  const controller = { request_form: "dpa", paper: "Our template", our_role: "Controller: we decide how the data is used",
    processing_purpose: "Hosting.", data_types: ["Financial"], data_subjects: ["Customers"], transfer: "No", subprocessors: "No" };
  it("DPA: nothing for a complete one, 'Whose data?' for Several, the tables for Yes", () => {
    expect(needsDraftAnswers(dpa, controller)).toEqual([]);
    expect(keys(needsDraftAnswers(dpa, { ...controller, data_subjects: "Several" }))).toEqual(["data_subjects"]);
    expect(keys(needsDraftAnswers(dpa, { ...controller, transfer: "Yes", subprocessors: "Yes" }))).toEqual(["transfer_countries", "subprocessor_rows"]);
  });
  it("DPA: nothing when we are the Processor or it is their paper, whatever is missing", () => {
    expect(PROCESSOR_ROLE).toBe(dpa.fields.find((f) => f.key === "our_role")!.options![1]);
    expect(needsDraftAnswers(dpa, { ...controller, our_role: PROCESSOR_ROLE, data_subjects: "Several", transfer: "Yes" })).toEqual([]);
    expect(needsDraftAnswers(dpa, { ...controller, paper: "Their paper", data_subjects: "Several" })).toEqual([]);
  });
});
