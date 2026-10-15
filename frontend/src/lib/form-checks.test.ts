import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  answerProblem, dateWindow, EMAIL_PROBLEM, findBlanks, longDate, looksLikeEmail, pastDueRows, rowsProblems, shareTotal,
  shown, tidiedRows, tidyCell,
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
