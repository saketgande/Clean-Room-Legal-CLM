import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { AGREEMENT_FORMS } from "@/app/(app)/intake/_agreement-forms";
import type { FormValue, RequestFormDef, SummaryBoxState } from "@/lib/types";
import { buildSummary } from "./request-summary";
import {
  EMPTY_BOX, WORD_LIMIT, WORD_WARN, acceptOffer, addPending, checkLine, checkText, declineOffer, differsNote, editLine, finalize,
  matchChoice, norm, parseDate, parseMoney, reconcile, removeInstruction, undoEdits, wordCount, type CheckContext,
} from "./summary-edits";

const forms: RequestFormDef[] = JSON.parse(
  readFileSync(resolve(__dirname, "../../../backend/app/intake/agreement_forms.json"), "utf8"),
);
const formOf = (k: string) => forms.find((f) => f.key === k)!;
const parent = { parentTitle: "Acme MSA 2024" };

function setup(formKey: string, values: Record<string, FormValue>, locale = "en-IN"): CheckContext {
  const def = AGREEMENT_FORMS.find((d) => d.key === formKey)!;
  const form = formOf(formKey);
  return { form, def, values, lines: buildSummary(form, def, values, parent), ctx: parent, locale };
}
/** Type a line and leave it, as a person does. */
const type = (c: CheckContext, id: string, text: string, box: SummaryBoxState = EMPTY_BOX) =>
  checkLine(editLine(box, id, text, c), id, c);
const nda: Record<string, FormValue> = {
  agreement_type: "NDA", nda_kind: "Mutual", entity: "Acme Pharma Ltd", counterparty: "Initech Software Ltd",
  paper: "Our template", nda_term: "2 years", governing_law: "India",
  cp_signer_name: "Ravi Rao", cp_signer_email: "ravi@initech.com",
};
/** A services agreement: asks `scope`, a required free-text answer with a one-word prefix ("Scope: ..."). */
const msa: Record<string, FormValue> = { ...nda, agreement_type: "Services (MSA)", scope: "Review the vendor NDA" };

describe("parseMoney (pinned table)", () => {
  // Failure mode: a loose reading turns "5.000.000" or "fifty lakh" into a wrong amount that gets offered.
  it.each([
    ["₹50 lakh", "5000000", "INR"], ["50L", "5000000", null], ["INR 50,00,000", "5000000", "INR"],
    ["5,000,000", "5000000", null], ["5000000", "5000000", null], ["USD 60,000", "60000", "USD"],
    ["1.5 cr", "15000000", null], ["$2 million", "2000000", "USD"], ["50 lakh rupees", "5000000", "INR"],
    ["1,50,00,000", "15000000", null], ["12,345.50", "12345.5", null],
  ])("%s", (text, amount, currency) => {
    expect(parseMoney(text)).toEqual({ amount, currency });
  });
  it.each(["fifty lakh", "5.000.000", "5 000 000", "5-6 lakh", "approx. 50 lakh", "0", "1e3", "", "USD 5 INR", "1,50", "1,234,56", "12,34", "1,2345"])(
    "%s is not certain", (text) => expect(parseMoney(text)).toBeNull());
});

describe("parseDate (pinned table)", () => {
  // Failure mode: reading 1/12/2026 the wrong way round silently files the wrong date (AC-9).
  it.each([
    ["1/12/2026", "en-IN", "2026-12-01"], ["1/12/2026", "en-US", null], ["15/11/2026", "en-US", "2026-11-15"],
    ["12/31/2026", "en-IN", "2026-12-31"], ["1/12/26", "en-IN", null], ["1/12/26", "en-US", null],
    ["5/5/2026", "en-US", "2026-05-05"], ["2026-11-15", "en-US", "2026-11-15"], ["15 Nov 2026", "en-US", "2026-11-15"],
    ["15th November 2026", "en-IN", "2026-11-15"], ["Nov 15, 2026", "en-IN", "2026-11-15"],
    ["November 15 2026", "en-IN", "2026-11-15"], ["31/02/2026", "en-IN", null],
    ["15 Nov 2026 to 3 Dec 2026", "en-IN", null], ["Nov 2026", "en-IN", null],
  ])("%s (%s)", (text, locale, want) => expect(parseDate(text, locale)).toBe(want));
});

describe("matchChoice", () => {
  const f = (form: string, key: string) => formOf(form).fields.find((x) => x.key === key)!;
  it("reads labels, phrases and synonyms", () => {
    // Failure mode: a spelling the plan froze is not recognised, so an obvious edit becomes an instruction.
    expect(matchChoice(f("new_agreement", "governing_law"), "nda_term_law", "English")).toBe("England and Wales");
    expect(matchChoice(f("new_agreement", "governing_law"), "governing_law", "the UK")).toBe("England and Wales");
    expect(matchChoice(f("new_agreement", "nda_kind"), "nda_kind", "two-way")).toBe("Mutual");
    expect(matchChoice(f("new_agreement", "agreement_type"), "deal", "a services agreement (MSA)")).toBe("Services (MSA)");
    expect(matchChoice(f("new_agreement", "agreement_type"), "deal", "MNDA")).toBe("NDA");
    expect(matchChoice(f("new_agreement", "hosting"), "hosting", "the EU")).toBe("European Union");
  });
  it("never guesses", () => {
    // Failure mode: "French" or an unlisted word picks the nearest option.
    expect(matchChoice(f("new_agreement", "governing_law"), "governing_law", "French")).toBeNull();
    expect(matchChoice(f("new_agreement", "agreement_type"), "deal", "")).toBeNull();
  });
  it("reads durations in words, years, months and days", () => {
    // Failure mode: "36 months" or "three years" is not seen as the "3 years" option; "30 months" wrongly is.
    const t = f("new_agreement", "nda_term");
    expect(matchChoice(t, "nda_term", "3 yrs")).toBe("3 years");
    expect(matchChoice(t, "nda_term", "three years")).toBe("3 years");
    expect(matchChoice(t, "nda_term", "36 months")).toBe("3 years");
    expect(matchChoice(t, "nda_term", "30 months")).toBeNull();
    expect(matchChoice(t, "nda_term", "4 years")).toBeNull();
    expect(matchChoice(f("new_agreement", "payment_terms"), "payment_terms", "forty-five days")).toBe("45 days");
    expect(matchChoice(f("new_agreement", "payment_terms"), "payment_terms", "2 years")).toBeNull();
  });
  it("norm tidies quotes, spaces and one end mark", () => {
    expect(norm("  “Hello”   World. ")).toBe("hello world");
  });
});

describe("a clear change is offered, never applied", () => {
  it("AC-8: English law offers India to England and Wales and leaves every answer alone", () => {
    // Failure mode: the edit writes the answer itself instead of waiting for a click.
    const c = setup("new_agreement", nda);
    const r = type(c, "nda_term_law", "It lasts 2 years and is under English law.");
    expect(r).not.toHaveProperty("patch");
    expect(c.values.governing_law).toBe("India");
    expect(r.box.offers).toHaveLength(1);
    expect(r.box.offers[0]).toMatchObject({
      kind: "update", keys: ["governing_law"], label: "Governing law", oldText: "India", newText: "England and Wales",
      patch: { governing_law: "England and Wales" },
    });
    expect(r.announce).toBe("Aegis can update Governing law: India → England and Wales.");
    expect(r.box.instructions).toEqual([]);
  });

  it("accepting returns the only patch and keeps nothing when the change is fully applied", () => {
    // Failure mode: an accepted change also lands in the instructions, so Legal sees it twice.
    const c = setup("new_agreement", nda);
    const r = type(c, "nda_term_law", "It lasts 2 years and is under English law.");
    const a = acceptOffer(r.box, r.box.offers[0].id, c);
    expect(a.patch).toEqual({ governing_law: "England and Wales" });
    expect(a.announce).toBe("Governing law updated to England and Wales.");
    expect(a.box.edits).toEqual({});
    expect(a.box.instructions).toEqual([]);
    expect(a.box.accepted).toEqual([{ key: "governing_law", label: "Governing law", old: "India", new: "England and Wales" }]);
  });

  it("AC-12: term and law give two offers; an unknown law gives one offer and a note", () => {
    // Failure mode: one unreadable slot blocks the clear one, or is silently dropped.
    const c = setup("new_agreement", nda);
    const two = type(c, "nda_term_law", "It lasts 5 years and is under Singapore law.");
    expect(two.box.offers.map((o) => o.keys[0])).toEqual(["nda_term", "governing_law"]);
    const one = type(c, "nda_term_law", "It lasts 5 years and is under French law.");
    expect(one.box.offers).toHaveLength(1);
    expect(one.box.offers[0].patch).toEqual({ nda_term: "5 years" });
    const a = acceptOffer(one.box, one.box.offers[0].id, c);
    expect(a.box.instructions).toEqual([{
      text: "It lasts 5 years and is under French law.",
      differs: [{ key: "governing_law", label: "Governing law", answer: "India" }],
    }]);
    expect(a.announce).toContain("Kept as an extra instruction for Legal.");
  });

  it("AC-9: dates follow the browser's order; a duration in words is read", () => {
    // Failure mode: "1/12/2026" is offered as 12 Jan for an Indian user, or as 1 Dec for a US user.
    const v = { ...nda, needed_by: "2026-11-15" };
    const india = type(setup("new_agreement", v, "en-IN"), "needed_by", "Needed by 1/12/2026.");
    expect(india.box.offers[0].patch).toEqual({ needed_by: "2026-12-01" });
    expect(india.box.offers[0].newText).toBe("1 Dec 2026");
    const us = type(setup("new_agreement", v, "en-US"), "needed_by", "Needed by 1/12/2026.");
    expect(us.box.offers).toEqual([]);
    expect(us.box.instructions).toEqual([{
      text: "Needed by 1/12/2026.", differs: [{ key: "needed_by", label: "Needed by", answer: "15 Nov 2026" }],
    }]);
    const yrs = type(setup("new_agreement", nda), "nda_term_law", "It lasts three years and is under Indian law.");
    expect(yrs.box.offers[0].patch).toEqual({ nda_term: "3 years" });
  });

  it("an end date before the start date is not offered", () => {
    // Failure mode: an offer the form itself would refuse (the question's own check, FR-18).
    const v: Record<string, FormValue> = { ...nda, agreement_type: "Services (MSA)", start_date: "2026-11-01", end_date: "2027-11-01", term: "Fixed end date" };
    const r = type(setup("new_agreement", v), "end_date", "It ends on 1 Oct 2026.");
    expect(r.box.offers).toEqual([]);
    expect(r.box.instructions[0].differs[0]).toMatchObject({ key: "end_date", answer: "1 Nov 2027" });
  });

  it("AC-10: a reworded free-text answer is offered as the new answer, with or without the prefix", () => {
    // Failure mode: free text is treated as uncertain and the person has to retype it in the form.
    const c = setup("new_agreement", msa);
    const a = type(c, "scope", "Scope: Review the new vendor NDA");
    expect(a.box.offers[0]).toMatchObject({ patch: { scope: "Review the new vendor NDA" }, newText: "Review the new vendor NDA" });
    const b = type(c, "scope", "Review the new vendor NDA.");
    expect(b.box.offers[0].patch).toEqual({ scope: "Review the new vendor NDA" });
  });

  it("money: lakh, currency and the shared currency in one combined offer", () => {
    // Failure mode: a named currency is lost, or changes silently without being shown in the offer.
    const v: Record<string, FormValue> = { pricing: "Fixed fee", value: "5000000", currency: "INR", parent_contract_id: "p1" };
    const c = setup("sow", v);
    const lakh = type(c, "value", "The total value of this SoW is ₹60 lakh.");
    expect(lakh.box.offers[0]).toMatchObject({ keys: ["value"], patch: { value: "6000000" }, oldText: "INR 50,00,000", newText: "INR 60,00,000" });
    const usd = type(c, "value", "The total value of this SoW is USD 60,000.");
    expect(usd.box.offers[0]).toMatchObject({ keys: ["value", "currency"], patch: { value: "60000", currency: "USD" } });
    const same = type(c, "value", "The total value of this SoW is 50 lakh.");
    expect(same.box.offers).toEqual([]);
    expect(same.box.instructions).toEqual([]);
  });

  it("a choice line with a sentence per option offers the other option", () => {
    // Failure mode: rewriting the Yes sentence as the No sentence is kept as text instead of flipping the answer.
    const c = setup("sow", { personal_data: "Yes", parent_contract_id: "p1" });
    const r = type(c, "personal_data", "No personal data will be shared or processed.");
    expect(r.box.offers[0]).toMatchObject({ patch: { personal_data: "No" }, oldText: "Yes", newText: "No" });
    const extra = type(c, "personal_data", "Personal data will be shared or processed, mostly staff data.");
    expect(extra.box.offers).toEqual([]);
    expect(extra.box.instructions[0].differs).toEqual([]);
    const other = type(c, "personal_data", "Something about data.");
    expect(other.box.instructions[0].differs[0].key).toBe("personal_data");
  });
});

describe("never guess", () => {
  const sow = (): CheckContext => setup("sow", { pricing: "Fixed fee", value: "5000000", currency: "INR", parent_contract_id: "p1" });

  it("R1: a hedged amount is never offered, on the template path or the scan path", () => {
    // Failure mode: "around 70 lakh" is filed as exactly INR 70,00,000 because only the template path refused hedges.
    const c = sow();
    expect(type(c, "value", "The total value of this SoW is around 70 lakh.").box.offers).toEqual([]);
    const scan = type(c, "value", "Around ₹70 lakh is what this SoW will cost.");
    expect(scan.box.offers).toEqual([]);
    expect(scan.box.instructions[0].differs[0].key).toBe("value");
    expect(type(c, "value", "This SoW costs ₹70 lakh at most.").box.offers).toEqual([]);
    expect(type(c, "value", "This SoW costs ₹70 lakh in total.").box.offers).toHaveLength(1);
  });

  it("R3: a sentence that holds the answer but turns it round keeps the differs note", () => {
    // Failure mode: "No personal data ... apart from names" contains the Yes sentence, so the contradiction was dropped silently.
    const c = setup("sow", { personal_data: "Yes", parent_contract_id: "p1" });
    const r = type(c, "personal_data", "No personal data will be shared or processed, apart from names.");
    expect(r.box.offers).toEqual([]);
    expect(r.box.instructions[0].differs[0].key).toBe("personal_data");
  });

  it("R4: a declined offer does not come back when the line is blurred again", () => {
    // Failure mode: rebuilding the check with declined: [] re-offers the change the person just refused.
    const c = setup("new_agreement", nda);
    const r = type(c, "nda_term_law", "It lasts 5 years and is under Singapore law.");
    const d = declineOffer(r.box, r.box.offers[0].id, c);
    expect(d.box.offers.map((o) => o.keys[0])).toEqual(["governing_law"]);
    const again = checkLine(d.box, "nda_term_law", c);
    expect(again.box.offers.map((o) => o.keys[0])).toEqual(["governing_law"]);
    expect(again.box.edits.nda_term_law.check?.declined).toEqual(["nda_term"]);
  });

  it("R5: a negated option is not offered as that option", () => {
    // Failure mode: "not under English law" is offered as governing law England and Wales.
    const c = setup("new_agreement", nda);
    const r = type(c, "nda_term_law", "Not under English law, and it lasts 2 years.");
    expect(r.box.offers.flatMap((o) => o.keys)).not.toContain("governing_law");
    expect(r.box.instructions[0]?.differs.map((d) => d.key)).toContain("governing_law");
  });

  it("AC-11: a new party is never a pick; kept as an instruction naming the current party", () => {
    // Failure mode: an edit swaps the register pick, or creates a counterparty from a sentence (FR-22).
    const c = setup("novation", { parent_contract_id: "p1", incoming_party: "Initech Software Ltd" });
    const r = type(c, "incoming_party", "The new party is Globex Corp.");
    expect(r.box.offers).toEqual([]);
    expect(r.box.instructions).toEqual([{
      text: "The new party is Globex Corp.",
      differs: [{ key: "incoming_party", label: "Who takes over", answer: "Initech Software Ltd" }],
    }]);
    expect(r.announce).toBe("Kept as an extra instruction for Legal.");
  });

  it("AC-15: the counterparty and our entity are never in a patch", () => {
    // Failure mode: a deal sentence reworded with new company names produces entity/counterparty patches.
    const c = setup("new_agreement", nda);
    const r = type(c, "deal_nda", "This is a one-way NDA between Acme Pharma Ltd and Globex Ltd, on their paper.");
    const patches = r.box.offers.flatMap((o) => Object.keys(o.patch));
    expect(patches.sort()).toEqual(["nda_kind", "paper"]);
    expect(r.box.offers.every((o) => !("counterparty" in o.patch) && !("entity" in o.patch))).toBe(true);
    const a = acceptOffer(r.box, r.box.offers[0].id, c);
    const b = acceptOffer(a.box, a.box.offers[0].id, c);
    expect(b.box.instructions[0].differs).toEqual([{ key: "counterparty", label: "Counterparty", answer: "Initech Software Ltd" }]);
  });

  it("a table summary is never applied", () => {
    // Failure mode: "6 deliverables" edited to "7 deliverables" invents or drops rows (D6).
    const rows = [1, 2, 3].map((n) => ({ name: `Item ${n}`, due: "2026-12-01", share: "" }));
    const c = setup("sow", { deliverable_rows: rows, parent_contract_id: "p1" });
    const line = c.lines.find((l) => l.keys[0] === "deliverable_rows")!;
    const r = type(c, line.id, "4 deliverables: A, B, C and D.");
    expect(r.box.offers).toEqual([]);
    expect(r.box.instructions[0].differs[0]).toMatchObject({ key: "deliverable_rows", answer: "3 deliverables: Item 1, Item 2 and Item 3" });
  });

  it("an unrecognised rewording is an instruction without offers", () => {
    // Failure mode: a sentence that does not fit the template still changes an answer.
    const c = setup("new_agreement", nda);
    const r = type(c, "nda_term_law", "Please make this one run for a good long while.");
    expect(r.box.offers).toEqual([]);
    expect(r.box.instructions[0].text).toBe("Please make this one run for a good long while.");
    expect(r.box.instructions[0].differs.map((d) => d.key).sort()).toEqual(["governing_law", "nda_term"]);
  });

  it("the note to approvers and gap lines cannot be edited", () => {
    // Failure mode: a declined edit turns the note into an instruction (FR-38).
    const c = setup("sow", { note_approvers: "Please hurry", parent_contract_id: "p1" });
    const note = c.lines.find((l) => l.id === "note_approvers")!;
    expect(editLine(EMPTY_BOX, note.id, "changed", c)).toBe(EMPTY_BOX);
    const gap = c.lines.find((l) => l.kind === "gap")!;
    expect(editLine(EMPTY_BOX, gap.id, "changed", c)).toBe(EMPTY_BOX);
  });

  it("an edit equal to the line is dropped", () => {
    // Failure mode: clicking in and out of a line files an empty instruction.
    const c = setup("new_agreement", msa);
    const r = type(c, "scope", "scope: review the vendor nda.");
    expect(r.box).toEqual(EMPTY_BOX);
  });
});

describe("declining and deleting", () => {
  it("AC-14: declining keeps the edit as an instruction and the answer unchanged", () => {
    // Failure mode: a declined edit vanishes, so Legal never sees what the requester asked for (FR-21).
    const c = setup("new_agreement", nda);
    const r = type(c, "nda_term_law", "It lasts 2 years and is under English law.");
    const d = declineOffer(r.box, r.box.offers[0].id, c);
    expect(d.announce).toBe("Your answer is kept. Your edit is kept as an extra instruction.");
    expect(d.box.edits).toEqual({});
    expect(d.box.accepted).toEqual([]);
    expect(d.box.instructions).toEqual([{
      text: "It lasts 2 years and is under English law.",
      differs: [{ key: "governing_law", label: "Governing law", answer: "India" }],
    }]);
  });

  it("AC-17: deleting an optional line offers to clear the answer", () => {
    // Failure mode: an emptied line silently clears, or silently does nothing.
    const c = setup("new_agreement", { ...nda, needed_by: "2026-11-15" });
    const r = type(c, "needed_by", "  ");
    expect(r.box.offers[0]).toMatchObject({ kind: "clear", keys: ["needed_by"], oldText: "15 Nov 2026", newText: "no answer", patch: { needed_by: "" } });
    const a = acceptOffer(r.box, r.box.offers[0].id, c);
    expect(a.patch).toEqual({ needed_by: "" });
    expect(a.box.instructions).toEqual([]);
    expect(a.box.edits).toEqual({});
  });

  it("AC-17: deleting a required line brings it back and keeps nothing", () => {
    // Failure mode: a required answer is cleared from the summary (FR-24).
    const c = setup("new_agreement", msa);
    const r = type(c, "scope", "");
    expect(r.box).toEqual(EMPTY_BOX);
    expect(r.announce).toBe("Scope of services is required, so its line can't be removed. The line is back.");
  });

  it("a declined clear is kept as a 'Remove this line' instruction", () => {
    // Failure mode: a deletion has no words of its own and would be lost (D7).
    const c = setup("new_agreement", { ...nda, needed_by: "2026-11-15" });
    const r = type(c, "needed_by", "");
    const d = declineOffer(r.box, r.box.offers[0].id, c);
    expect(d.box.instructions).toEqual([{
      text: "Remove this line: “Needed by 15 Nov 2026.”",
      differs: [{ key: "needed_by", label: "Needed by", answer: "15 Nov 2026" }],
    }]);
  });

  it("deleting a register-pick line never offers to clear it", () => {
    // Failure mode: clearing the name leaves a stale entity id behind (FR-22); here the pick is required, so the line returns.
    const c = setup("novation", { parent_contract_id: "p1", incoming_party: "Initech Software Ltd" });
    const r = type(c, "incoming_party", "");
    expect(r.box.offers).toEqual([]);
    expect(r.box).toEqual(EMPTY_BOX);
  });
});

describe("reconcile, undo, finalize, counts", () => {
  it("AC-16: a form change withdraws the offer and the instruction notes the new answer", () => {
    // Failure mode: an offer for 'India' is accepted after the person changed the law on the form (FR-23).
    const c = setup("new_agreement", nda);
    const r = type(c, "nda_term_law", "It lasts 3 years and is under Indian law.");
    expect(r.box.offers).toHaveLength(1);
    const c2 = setup("new_agreement", { ...nda, nda_term: "5 years" });
    const out = reconcile(r.box, c2);
    expect(out.offers).toEqual([]);
    expect(out.edits).toEqual({});
    expect(out.instructions).toEqual([{
      text: "It lasts 3 years and is under Indian law.",
      differs: [{ key: "nda_term", label: "How long it lasts", answer: "5 years" }],
    }]);
  });

  it("an offer is withdrawn when its question stops applying", () => {
    // Failure mode: the box offers to change an answer the form no longer asks.
    const c = setup("new_agreement", nda);
    const r = type(c, "nda_term_law", "It lasts 3 years and is under Indian law.");
    const c2 = setup("new_agreement", { ...nda, agreement_type: "Services (MSA)" });
    const out = reconcile(r.box, c2);
    expect(out.offers).toEqual([]);
    expect(out.instructions[0].differs[0]).toEqual({ key: "nda_term", label: "How long it lasts", answer: "no answer" });
  });

  it("an unchecked edit whose line changed underneath it is kept at once; a valid one is left alone", () => {
    // Failure mode: a stale edit is later checked against a different sentence and offers a wrong change.
    const c = setup("new_agreement", msa);
    const typed = editLine(EMPTY_BOX, "scope", "Scope: something else", c);
    expect(reconcile(typed, c)).toBe(typed);
    const c2 = setup("new_agreement", { ...msa, scope: "Review the supplier NDA" });
    const out = reconcile(typed, c2);
    expect(out.edits).toEqual({});
    expect(out.instructions[0]).toMatchObject({ text: "Scope: something else", differs: [{ key: "scope", answer: "Review the supplier NDA" }] });
  });

  it("an edit whose line has gone is resolved, not left hanging", () => {
    // Failure mode: an edit on a line that disappeared stays in the draft forever.
    const c = setup("new_agreement", nda);
    const typed = editLine(EMPTY_BOX, "nda_term_law", "Lasts a while", c);
    const out = reconcile(typed, setup("new_agreement", { ...nda, agreement_type: "Services (MSA)" }));
    expect(out.edits).toEqual({});
    expect(out.instructions[0].text).toBe("Lasts a while");
  });

  it("AC-7: undo clears edits and open offers but keeps instructions and accepted answers", () => {
    // Failure mode: Undo my edits throws away what the person already added or applied (FR-13).
    const c = setup("new_agreement", nda);
    let box = addPending({ ...EMPTY_BOX, pending: "Copy procurement" });
    box = { ...box, accepted: [{ key: "k", label: "K", old: "a", new: "b" }] };
    const r = type(c, "nda_term_law", "It lasts 2 years and is under English law.", box);
    const u = undoEdits(r.box);
    expect(u.edits).toEqual({});
    expect(u.offers).toEqual([]);
    expect(u.instructions).toHaveLength(1);
    expect(u.accepted).toHaveLength(1);
  });

  it("AC-24: finalize checks unchecked edits, declines open offers and keeps pending text, without blocking", () => {
    // Failure mode: Submit loses or blocks on an open offer or an unchecked line (FR-34).
    const c = setup("new_agreement", { ...msa, value: "1000000", currency: "INR" });
    const open = type(c, "value", "The total value is ₹50 lakh.");
    const unchecked = editLine(open.box, "scope", "Scope: a different scope", c);
    const fin = finalize({ ...unchecked, pending: "  Also send to procurement  " }, c);
    expect(fin.offers).toEqual([]);
    expect(fin.edits).toEqual({});
    expect(fin.pending).toBe("");
    expect(fin.accepted).toEqual([]);
    expect(fin.instructions.map((i) => i.text)).toEqual([
      "The total value is ₹50 lakh.", "Scope: a different scope", "Also send to procurement",
    ]);
    expect(finalize(fin, c)).toEqual(fin);
  });

  it("AC-22: wordCount counts instructions and pending, not notes", () => {
    // Failure mode: the browser and the server count differently, so Submit passes and filing is refused.
    const box: SummaryBoxState = {
      ...EMPTY_BOX, pending: "one two  three\nfour",
      instructions: [{ text: "a b c", differs: [{ key: "k", label: "K", answer: "many many words here" }] }],
    };
    expect(wordCount(box)).toBe(7);
    expect(wordCount(EMPTY_BOX)).toBe(0);
    expect(WORD_LIMIT).toBe(2000);
    expect(WORD_WARN).toBe(1800);
  });

  it("addPending ignores blank text and removeInstruction removes one", () => {
    // Failure mode: a blank click adds an empty instruction; removing one removes another.
    expect(addPending({ ...EMPTY_BOX, pending: "   " }).instructions).toEqual([]);
    const two = { ...EMPTY_BOX, instructions: [{ text: "a", differs: [] }, { text: "b", differs: [] }] };
    expect(removeInstruction(two, 0).instructions).toEqual([{ text: "b", differs: [] }]);
  });

  it("differsNote words one and several answers", () => {
    // Failure mode: the note wording drifts from the plan the request page shows.
    const d = (answer: string) => ({ key: "k", label: "K", answer });
    expect(differsNote([])).toBeNull();
    expect(differsNote([d("India")])).toBe("This differs from your answer: India");
    expect(differsNote([d("India"), d("2 years")])).toBe("This differs from your answers: India; 2 years");
  });
});

describe("checkText: the one free-text box is aligned to the sentences", () => {
  const gen = (c: CheckContext) => c.lines.filter((l) => l.editable).map((l) => l.text);
  const LAWLINE = "It lasts 2 years and is under Indian law.";

  it("unchanged text, or lines in the same words with extra blank lines, offers nothing", () => {
    // Failure mode: a blur on untouched text producing offers or instructions.
    const c = setup("new_agreement", nda);
    const r = checkText(EMPTY_BOX, gen(c).join("\n\n"), c);
    expect(r.box).toEqual(EMPTY_BOX);
    expect(r.open).toBe(false);
  });

  it("one sentence edited gives that sentence's offer and nothing else", () => {
    // Failure mode: an edit in the box not reaching the per-line logic, or touching other answers.
    const c = setup("new_agreement", nda);
    const text = gen(c).join("\n").replace(LAWLINE, "It lasts 2 years and is under English law.");
    const r = checkText(EMPTY_BOX, text, c);
    expect(r.box.offers).toHaveLength(1);
    expect(r.box.offers[0]).toMatchObject({ line: "nda_term_law", keys: ["governing_law"], newText: "England and Wales" });
    expect(r.box.instructions).toEqual([]);
    expect(r.open).toBe(true);
    expect(c.values.governing_law).toBe("India");
  });

  it("reordered lines still pair with their sentences", () => {
    // Failure mode: pairing by position, so moving a sentence turns every line into a change.
    const c = setup("new_agreement", nda);
    const lines = gen(c).map((l) => (l === LAWLINE ? "It lasts 2 years and is under English law." : l)).reverse();
    const r = checkText(EMPTY_BOX, lines.join("\n"), c);
    expect(r.box.offers.map((o) => o.newText)).toEqual(["England and Wales"]);
    expect(r.box.instructions).toEqual([]);
  });

  it("a line that merges two sentences is an instruction, with no offer and no answer cleared", () => {
    // Failure mode: guessing which sentence a merged line belongs to, or clearing the two sentences it swallowed.
    const c = setup("new_agreement", nda);
    const ls = gen(c);
    const merged = `${LAWLINE.replace("Indian", "English").replace(/\.$/, "")} and ${ls[0].charAt(0).toLowerCase()}${ls[0].slice(1)}`;
    const r = checkText(EMPTY_BOX, [merged, ...ls.slice(2)].join("\n"), c);
    expect(r.box.offers).toEqual([]);
    expect(r.box.instructions.map((i) => i.text)).toEqual([merged]);
  });

  it("a deleted optional sentence offers to clear its answer; a deleted required one is put back", () => {
    // Failure mode: a missing line silently clearing an answer, or removing a required one.
    const c = setup("new_agreement", { ...msa, needed_by: "2026-11-15" });
    const ls = gen(c);
    const neededLine = c.lines.find((l) => l.id === "needed_by")!.text;
    const gone = checkText(EMPTY_BOX, ls.filter((l) => l !== neededLine).join("\n"), c);
    expect(gone.box.offers[0]).toMatchObject({ kind: "clear", keys: ["needed_by"], newText: "no answer" });
    const scope = c.lines.find((l) => l.id === "scope")!.text;
    const req = checkText(EMPTY_BOX, ls.filter((l) => l !== scope).join("\n"), c);
    expect(req.box.offers).toEqual([]);
    expect(req.announce).toContain("Scope of services is required");
  });

  it("a brand-new line is an extra instruction, and the sentences stay as they were", () => {
    // Failure mode: new words being thrown away, or forced onto the nearest answer.
    const c = setup("new_agreement", nda);
    const r = checkText(EMPTY_BOX, [...gen(c), "Please send a copy to procurement."].join("\n"), c);
    expect(r.box.instructions).toEqual([{ text: "Please send a copy to procurement.", differs: [] }]);
    expect(r.box.offers).toEqual([]);
    expect(r.open).toBe(false);
  });

  it("a rewritten Scope line is an Update-answer offer, not an extra instruction (one-word prefix)", () => {
    // Failure mode (2026-10-27): a rewritten free-text answer shared too few words with its sentence, so it became an instruction.
    const c = setup("new_agreement", { ...msa, scope: "this is a test" });
    const text = gen(c).map((l) => (l === "Scope: this is a test" ? "Scope: evaluating a joint analytics project" : l)).join("\n");
    const r = checkText(EMPTY_BOX, text, c);
    expect(r.box.offers).toHaveLength(1);
    expect(r.box.offers[0]).toMatchObject({ line: "scope", keys: ["scope"], oldText: "this is a test", newText: "evaluating a joint analytics project" });
    expect(r.box.instructions).toEqual([]);
    expect(r.open).toBe(true);
  });

  it("another multi-word prefix ('Non-standard terms asked for:') pairs the same way", () => {
    // Failure mode: only 'Scope:' being fixed while other free-text sentences still turn into instructions.
    const c = setup("new_agreement", { ...nda, agreement_type: "Selling to a customer", nonstandard: "Net 90 payment" });
    const line = c.lines.find((l) => l.id === "nonstandard")!.text;
    const r = checkText(EMPTY_BOX, gen(c).map((l) => (l === line ? "Non-standard terms asked for: pay within 30 days, no cap" : l)).join("\n"), c);
    expect(r.box.offers).toHaveLength(1);
    expect(r.box.offers[0]).toMatchObject({ line: "nonstandard", newText: "pay within 30 days, no cap" });
    expect(r.box.instructions).toEqual([]);
  });

  it("a start claimed by two typed lines is not paired by prefix (never guess)", () => {
    // Failure mode: two lines starting 'Scope:' both overwriting one answer, or the second silently lost.
    const c = setup("new_agreement", msa);
    const ls = gen(c).filter((l) => !l.startsWith("Scope:"));
    const r = checkText(EMPTY_BOX, [...ls, "Scope: first idea", "Scope: second idea"].join("\n"), c);
    expect(r.box.offers.filter((o) => o.line === "scope")).toEqual([]);
    expect(r.box.instructions.map((i) => i.text)).toEqual(expect.arrayContaining(["Scope: first idea", "Scope: second idea"]));
  });

  it("checking again while offers are open does not repeat an offer or an instruction, nor revive a declined one", () => {
    // Failure mode: each blur adding the same instruction, or bringing back an offer the person declined.
    const c = setup("new_agreement", nda);
    const text = [...gen(c).map((l) => (l === LAWLINE ? "It lasts 5 years and is under English law." : l)), "Copy procurement"].join("\n");
    const one = checkText(EMPTY_BOX, text, c);
    expect(one.box.offers).toHaveLength(2);
    const two = checkText(one.box, text, c);
    expect(two.box.offers).toHaveLength(2);
    expect(two.box.instructions).toHaveLength(1);
    const declined = declineOffer(two.box, two.box.offers[0].id, c).box;
    const three = checkText(declined, text, c);
    expect(three.box.offers).toHaveLength(1);
    expect(three.box.instructions).toHaveLength(1);
  });
});
