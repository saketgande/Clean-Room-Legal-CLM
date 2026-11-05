import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { AGREEMENT_FORMS } from "@/app/(app)/intake/_agreement-forms";
import type { FormValue, RequestFormDef } from "@/lib/types";
import {
  NAMES, SENTENCE_RULES, SYNONYMS, _rules, answerText, buildSummary, moneyText, summaryText, writtenDate,
} from "./request-summary";

// Same reading of the server's forms as agreement-forms.test.ts: the questions are the server's, only the
// wording lives in request-summary.ts, so a new or changed question must fail here until it has a sentence.
const forms: RequestFormDef[] = JSON.parse(
  readFileSync(resolve(__dirname, "../../../backend/app/intake/agreement_forms.json"), "utf8"),
);
const formOf = (key: string) => forms.find((f) => f.key === key)!;
const defOf = (key: string) => AGREEMENT_FORMS.find((d) => d.key === key)!;
const ctx = { parentTitle: "Acme MSA 2024" };
const lines = (key: string, values: Record<string, FormValue>) => buildSummary(formOf(key), defOf(key), values, ctx);
const sentences = (key: string, values: Record<string, FormValue>) =>
  lines(key, values).filter((l) => l.kind === "sentence").map((l) => l.text);
const COMPANIONS = ["entity_id", "counterparty_id", "currency"];

describe("sentence rules match the server's forms (guard)", () => {
  it("covers all nine forms", () => {
    expect(forms.map((f) => f.key)).toEqual(AGREEMENT_FORMS.map((d) => d.key));
  });

  for (const form of forms) {
    describe(form.key, () => {
      const fieldOf = (k: string) => form.fields.find((f) => f.key === k);
      const mine = SENTENCE_RULES.filter((r) => _rules.applies(r, form));

      it("every question has exactly one sentence rule", () => {
        for (const f of form.fields.filter((x) => !COMPANIONS.includes(x.key))) {
          const solos = mine.filter((r) => r.keys.length === 1 && r.keys[0] === f.key);
          expect(solos.filter((r) => r.forms).length, `${f.key}: explicit rules`).toBeLessThanOrEqual(1);
          expect(solos.filter((r) => !r.forms).length, `${f.key}: shared rules`).toBeLessThanOrEqual(1);
          expect(_rules.soloRule(form.key, f.key), `${f.key} has a sentence`).toBeTruthy();
        }
      });

      it("rules name real questions and options, and every slot is a key", () => {
        for (const r of mine) {
          for (const k of r.keys) expect(fieldOf(k), `${r.id}: ${k}`).toBeTruthy();
          for (const w of r.when ?? []) {
            const f = fieldOf(w.field);
            expect(f, `${r.id} when ${w.field}`).toBeTruthy();
            for (const o of w.in) expect(f!.options, `${r.id} when ${w.field}=${o}`).toContain(o);
          }
          for (const [k, map] of Object.entries(r.phrases ?? {})) {
            expect(r.keys, `${r.id} phrase key ${k}`).toContain(k);
            for (const o of Object.keys(map)) expect(fieldOf(k)!.options, `${r.id} phrase ${k}=${o}`).toContain(o);
          }
          const how = [r.text, r.variants, r.rows].filter(Boolean).length;
          expect(how, `${r.id} has one way of being said`).toBe(1);
          if (r.text) {
            const slots = [...r.text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]);
            expect([...new Set(slots)].sort(), `${r.id} slots`).toEqual([...r.keys].sort());
          }
          if (r.rows) {
            const f = fieldOf(r.keys[0])!;
            expect(r.keys.length).toBe(1);
            expect(f.kind).toBe("rows");
            expect((f.columns ?? []).map((c) => c.key), `${r.id} column`).toContain(r.rows.column);
          }
        }
      });

      it("variants cover every option of their choice question", () => {
        for (const r of mine.filter((x) => x.variants)) {
          expect(r.keys.length).toBe(1);
          expect(Object.keys(r.variants!).sort(), r.id).toEqual([...(fieldOf(r.keys[0])!.options ?? [])].sort());
        }
      });
    });
  }

  it("rules are only for forms that exist; synonyms name real options", () => {
    for (const r of SENTENCE_RULES) for (const f of r.forms ?? []) expect(formOf(f), `${r.id}: ${f}`).toBeTruthy();
    for (const [key, byOption] of Object.entries(SYNONYMS)) {
      const asks = forms.flatMap((f) => f.fields.filter((x) => x.key === key));
      expect(asks.length, `synonyms for ${key}`).toBeGreaterThan(0);
      for (const o of Object.keys(byOption)) expect(asks.some((f) => f.options?.includes(o)), `${key}=${o}`).toBe(true);
    }
    for (const k of Object.keys(NAMES)) {
      const [, key] = k.includes(".") ? k.split(".") : [null, k];
      expect(forms.some((f) => f.fields.some((x) => x.key === key)), `NAMES ${k}`).toBe(true);
    }
  });

  it("no wording gives advice (FR-6)", () => {
    const words: string[] = [];
    for (const r of SENTENCE_RULES) {
      if (r.text) words.push(r.text);
      words.push(...Object.values(r.variants ?? {}));
      for (const m of Object.values(r.phrases ?? {})) words.push(...Object.values(m));
    }
    expect(words.length).toBeGreaterThan(100);
    for (const w of words) expect(w, w).not.toMatch(/\b(should|must|recommend\w*|risk\w*|allowed|advise\w*|illegal)\b/i);
  });
});

describe("one filled request per form, in words", () => {
  const entity = { entity: "Aegis Pharma Ltd", counterparty: "Initech Software Ltd" };
  const signer = { cp_signer_name: "Sam Lee", cp_signer_email: "sam@acme.com" };

  it("new agreement: a mutual NDA reads as one sentence and the NDA terms", () => {
    expect(sentences("new_agreement", {
      agreement_type: "NDA", ...entity, cp_signer_name: "Priya Rao", cp_signer_email: "priya@initech.com",
      paper: "Our template", needed_by: "2026-11-15", department: "Commercial",
      nda_kind: "Mutual", nda_term: "3 years", governing_law: "England and Wales", start_date: "2026-12-01",
      personal_data: "No", note_approvers: "Please hurry.",
    })).toEqual([
      "This is a mutual NDA between Aegis Pharma Ltd and Initech Software Ltd, on our template.",
      "Their signer is Priya Rao (priya@initech.com).",
      "Needed by 15 Nov 2026.",
      "Requested by Commercial.",
      "It lasts 3 years and is under English law.",
      "It starts on 1 Dec 2026.",
      "No personal data will be shared or processed.",
      "Note to approvers: Please hurry.",
    ]);
  });

  it("new agreement: a services agreement keeps unanswered questions as gaps", () => {
    const l = lines("new_agreement", {
      agreement_type: "Services (MSA)", ...entity, paper: "Their paper", cp_signer_name: "Priya Rao",
      department: "Finance", value: "5000000", currency: "INR", start_date: "2026-12-01",
      term: "Renews automatically", end_date: "2029-11-30", renewal_term: "1 year", notice_days: "60 days",
      scope: "Managed services.\nOn-site support.", payment_terms: "45 days", personal_data: "Yes", gxp: "No",
    });
    expect(l.filter((x) => x.kind === "sentence").map((x) => x.text)).toEqual([
      "This is a services agreement (MSA) between Aegis Pharma Ltd and Initech Software Ltd, on their paper.",
      "Their signer is Priya Rao.",
      "Requested by Finance.",
      "The total value is INR 50,00,000.",
      "It starts on 1 Dec 2026.",
      "It has automatic renewal.",
      "It ends on 30 Nov 2029.",
      "Each renewal lasts 1 year.",
      "Notice to stop it renewing: 60 days.",
      "Scope: Managed services. On-site support.",
      "Payment is due within 45 days.",
      "Personal data will be shared or processed.",
      "It is not GxP or other regulated quality work.",
    ]);
    expect(l.filter((x) => x.kind === "gap").map((x) => x.text)).toEqual([
      "Their signer's email: not given yet", "Needed by: not given yet",
      "Note to approvers: not given yet",
    ]);
  });

  it("new agreement: consultancy on our paper, with a table and a rate", () => {
    expect(sentences("new_agreement", {
      agreement_type: "Consultancy", ...entity, paper: "Our template", fee_basis: "Day rate", fee_rate: "500",
      currency: "USD", company_contact_name: "Ann", company_contact_role: "Head of R&D",
      key_individuals: [{ name: "Ravi" }], advice_only: [],
      consultancy_deliverables: [{ name: "Report" }, { name: "Slides" }],
    })).toEqual([
      "This is a consultancy agreement between Aegis Pharma Ltd and Initech Software Ltd, on our template.",
      "2 deliverables: Report and Slides.",
      "The consultant is paid a day rate.",
      "The rate is USD 500.",
      "1 key individual: Ravi.",
      "Our contact is Ann (Head of R&D).",
    ]);
  });

  it("statement of work", () => {
    const names = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta"];
    expect(sentences("sow", {
      parent_contract_id: "c1", ...signer, needed_by: "2026-11-20", department: "Procurement",
      sow_title: "Phase 2 build", services_start: "2026-12-01", services_end: "2027-05-31", value: "5000000",
      currency: "INR", pricing: "Milestones", scope: "Build the portal.",
      deliverable_rows: names.map((name) => ({ name, due: "2027-01-01" })), personal_data: "Yes", gxp: "No",
    })).toEqual([
      "It is under the master agreement “Acme MSA 2024”.",
      "Their signer is Sam Lee (sam@acme.com).",
      "Needed by 20 Nov 2026.",
      "Requested by Procurement.",
      "The SoW is called “Phase 2 build”.",
      "Services run from 1 Dec 2026 to 31 May 2027.",
      "The total value of this SoW is INR 50,00,000.",
      "Pricing is by milestones.",
      "Scope: Build the portal.",
      "6 deliverables: Alpha, Beta, Gamma and 3 more.",
      "Personal data will be shared or processed.",
      "It is not GxP or other regulated quality work.",
    ]);
  });

  it("data processing agreement", () => {
    const l = lines("dpa", {
      parent_contract_id: "c1", ...signer, paper: "Our template",
      our_role: "Processor: we act on their instructions", processing_purpose: "Payroll processing.",
      data_types: ["Financial", "Employee / HR"], data_subjects: ["Employees"], transfer: "Yes",
      transfer_countries: [{ country: "France" }, { country: "Germany" }], subprocessors: "No",
    });
    expect(l.filter((x) => x.kind === "sentence").map((x) => x.text)).toEqual([
      "It goes with “Acme MSA 2024”.",
      "Their signer is Sam Lee (sam@acme.com).",
      "It is on our template.",
      "We are the processor (we act on their instructions).",
      "Why the data is processed: Payroll processing.",
      "Personal data involved: Employee / HR and Financial.",
      "Whose data: Employees.",
      "Data will leave India.",
      "2 countries outside India: France and Germany.",
      "They will not use sub-processors.",
    ]);
    expect(l.filter((x) => x.kind === "gap").map((x) => x.id)).toEqual(["needed_by", "note_approvers"]);
  });

  it("amendment", () => {
    expect(sentences("amendment", {
      parent_contract_id: "c1", needed_by: "2026-11-20", what_changes: ["Dates", "Value"], new_value: "6000000",
      currency: "USD", new_end_date: "2028-03-31", change_effective: "2026-12-01", reason: "Scope grew.",
    })).toEqual([
      "It amends “Acme MSA 2024”.",
      "Needed by 20 Nov 2026.",
      "What is changing: Value and Dates.",
      "The new total value is USD 6,000,000.",
      "The new end date is 31 Mar 2028.",
      "The change takes effect on 1 Dec 2026.",
      "Why: Scope grew.",
    ]);
  });

  it("renewal", () => {
    expect(sentences("renewal", {
      parent_contract_id: "c1", renew_terms: "Changed terms", renew_end: "2028-01-31", renew_value: "7500000",
      currency: "INR", reason: "Price review.",
    })).toEqual([
      "It renews “Acme MSA 2024”.",
      "It renews on changed terms.",
      "The new end date is 31 Jan 2028.",
      "The new total value is INR 75,00,000.",
      "Why: Price review.",
    ]);
  });

  it("termination", () => {
    expect(sentences("termination", {
      parent_contract_id: "c1", grounds: "Breach by the other side", termination_date: "2026-12-31",
      clause_breached: "12.3", cure_served: "No", reason: "Late deliveries.",
    })).toEqual([
      "It ends “Acme MSA 2024”.",
      "It is ending because of a breach by the other side.",
      "The termination date is 31 Dec 2026.",
      "The clause breached is 12.3.",
      "The cure period has not run out yet.",
      "Why: Late deliveries.",
    ]);
  });

  it("novation", () => {
    expect(sentences("novation", {
      parent_contract_id: "c1", transferring: "We are", incoming_party: "Globex Ltd", effective_date: "2027-01-01",
      consent: "Not yet", reason: "Group restructure.",
    })).toEqual([
      "It moves “Acme MSA 2024” to a new party.",
      "We are transferring out.",
      "The new party is Globex Ltd.",
      "It takes effect on 1 Jan 2027.",
      "The outgoing party has not agreed in writing yet.",
      "Why: Group restructure.",
    ]);
  });

  it("signed outside the system", () => {
    expect(sentences("regularize", {
      ...entity, date_signed: "2026-06-01", value: "250000", start_date: "2026-06-01", term: "Fixed end date",
      end_date: "2027-05-31", why_outside: "Urgency", prior_approval: "Informally, by email", signed_by_us: "R. Mehta",
    })).toEqual([
      "It is between Aegis Pharma Ltd and Initech Software Ltd.",
      "It was signed on 1 Jun 2026.",
      "The total value is INR 2,50,000.",
      "It starts on 1 Jun 2026.",
      "It has a fixed end date.",
      "It ends on 31 May 2027.",
      "Why it was signed outside the system: Urgency.",
      "Approved at the time: Informally, by email.",
      "Signed for us by R. Mehta.",
    ]);
  });

  it("cancel a request", () => {
    expect(sentences("cancellation", { cancel_request_ref: "REQ-123", cancel_reason: "Duplicate", tell_cp: "No" })).toEqual([
      "Cancel request REQ-123.",
      "Reason: Duplicate.",
      "The counterparty has no draft yet.",
    ]);
  });
});

describe("how the lines behave", () => {
  it("a broken rule reaches the caller: buildSummary must not swallow it into an empty list (wizard shows the note)", () => {
    const form = formOf("new_agreement");
    const rule = SENTENCE_RULES.find((r) => r.keys.length === 1 && r.keys[0] === "agreement_type" && _rules.applies(r, form))!;
    const orig = Object.getOwnPropertyDescriptor(rule, "text");
    Object.defineProperty(rule, "text", { configurable: true, get() { throw new Error("broken rule"); } });
    try {
      expect(() => buildSummary(form, defOf("new_agreement"), { agreement_type: "NDA" }, ctx)).toThrow("broken rule");
    } finally {
      if (orig) Object.defineProperty(rule, "text", orig); else delete (rule as { text?: string }).text;
    }
  });

  it("AC-1: an NDA answered alone says so; a half-filled pair falls back to one sentence each", () => {
    expect(sentences("new_agreement", { agreement_type: "NDA", nda_kind: "Mutual" }))
      .toEqual(["This is an NDA.", "It is a mutual NDA."]);
  });

  it("AC-3: an empty form is gaps only, worded by kind, with the required flag", () => {
    const l = lines("new_agreement", {});
    expect(l.every((x) => x.kind === "gap" && !x.editable)).toBe(true);
    expect(l[0]).toMatchObject({ id: "agreement_type", text: "Kind of agreement: not chosen yet", required: true });
    expect(l.find((x) => x.id === "needed_by")).toMatchObject({ text: "Needed by: not given yet", required: false });
    expect(l.find((x) => x.id === "cp_signer_name")?.text).toBe("Their signer's name: not given yet");
    expect(summaryText(l)).toBe("");
  });

  it("AC-4: a question that does not apply is never mentioned, and appears when it does", () => {
    const ids = (type: string) => lines("new_agreement", { agreement_type: type }).map((x) => x.id);
    expect(ids("Services (MSA)")).not.toContain("nda_term");
    expect(ids("Services (MSA)")).not.toContain("nda_kind");
    expect(ids("NDA")).toContain("nda_term");
    expect(ids("NDA")).not.toContain("payment_terms");
  });

  it("names a gap by the form's own wording: the master agreement under a SoW, the agreement elsewhere", () => {
    expect(lines("sow", {}).find((x) => x.id === "parent_contract_id")?.text).toBe("Master agreement: not chosen yet");
    expect(lines("amendment", {}).find((x) => x.id === "parent_contract_id")?.text).toBe("Agreement: not chosen yet");
    expect(lines("sow", {}).find((x) => x.id === "value")?.name).toBe("Total value of this SoW");
  });

  it("AC-5: six deliverables are three names and a count; one row is singular; more than three joins with commas", () => {
    const rows = (n: number) => Array.from({ length: n }, (_, i) => ({ name: `D${i + 1}` }));
    const say = (n: number) => sentences("sow", { deliverable_rows: rows(n) })[0];
    expect(say(6)).toBe("6 deliverables: D1, D2, D3 and 3 more.");
    expect(say(3)).toBe("3 deliverables: D1, D2 and D3.");
    expect(say(1)).toBe("1 deliverable: D1.");
    expect(sentences("sow", { deliverable_rows: [{ name: "" }, {}] })).toEqual([]);
  });

  it("an unticked advice-only box is an answer, not a gap", () => {
    const l = lines("new_agreement", { agreement_type: "Consultancy", paper: "Our template" });
    expect(l.some((x) => x.id === "advice_only")).toBe(false);
    expect(sentences("new_agreement", {
      agreement_type: "Consultancy", paper: "Our template",
      advice_only: ["There are no separate deliverables (advice only)"],
    })).toContain("There are no separate deliverables (advice only).");
    expect(lines("new_agreement", {
      agreement_type: "Consultancy", paper: "Our template",
      advice_only: ["There are no separate deliverables (advice only)"],
    }).some((x) => x.id === "consultancy_deliverables")).toBe(false);
  });

  it("AC-27: the note to approvers is a read-only sentence", () => {
    const n = lines("cancellation", { note_approvers: "Call me." }).find((x) => x.id === "note_approvers")!;
    expect(n).toMatchObject({ kind: "sentence", text: "Note to approvers: Call me.", editable: false });
    expect(lines("cancellation", { cancel_reason: "Duplicate" }).find((x) => x.id === "cancel_reason")?.editable).toBe(true);
  });

  it("summaryText holds the sentences only, one per line", () => {
    const l = lines("cancellation", { cancel_request_ref: "REQ-1", cancel_reason: "Duplicate" });
    expect(summaryText(l)).toBe("Cancel request REQ-1.\nReason: Duplicate.");
  });

  it("a long free-text answer is kept in full", () => {
    const long = "word ".repeat(300).trim();
    expect(sentences("amendment", { reason: long })).toEqual([`Why: ${long}`]);
  });

  it("the picked parent shows by title, or a plain fallback", () => {
    const v = { parent_contract_id: "c9" };
    expect(buildSummary(formOf("amendment"), defOf("amendment"), v, { parentTitle: null })[0].text)
      .toBe("It amends “the chosen agreement”.");
  });

  it("never throws on odd answers; they read as unanswered", () => {
    const odd: FormValue[] = [5, null, undefined, {}, [null], [[]], "  ", NaN] as unknown as FormValue[];
    for (const form of forms) {
      for (const o of odd) {
        const values = Object.fromEntries(form.fields.map((f) => [f.key, o]));
        expect(() => buildSummary(form, defOf(form.key), values, ctx)).not.toThrow();
        expect(sentences(form.key, values).length).toBe(0);
      }
    }
    expect(() => buildSummary(formOf("sow"), defOf("sow"), undefined as never, ctx)).not.toThrow();
  });
});

describe("writing helpers", () => {
  it("writtenDate uses fixed English months", () => {
    expect(writtenDate("2026-11-15")).toBe("15 Nov 2026");
    expect(writtenDate("2026-01-05")).toBe("5 Jan 2026");
  });

  it("moneyText keeps the wizard's rendering exactly", () => {
    expect(moneyText("INR", "5000000")).toBe("INR 50,00,000");
    expect(moneyText(undefined, "1,50,000")).toBe("INR 1,50,000");
    expect(moneyText("USD", "60000")).toBe("USD 60,000");
    expect(moneyText("EUR", "0")).toBe("");
    expect(moneyText("INR", undefined)).toBe("");
    expect(moneyText("INR", "abc")).toBe("");
  });

  it("answerText gives the answer as words, or no answer", () => {
    const f = formOf("new_agreement");
    expect(answerText(f, "governing_law", { agreement_type: "NDA", governing_law: "India" }, ctx)).toBe("India");
    expect(answerText(f, "governing_law", { agreement_type: "NDA" }, ctx)).toBe("no answer");
    expect(answerText(f, "governing_law", { agreement_type: "Consultancy", governing_law: "India" }, ctx)).toBe("no answer");
    expect(answerText(f, "value", { agreement_type: "Consultancy", value: "60000", currency: "USD" }, ctx)).toBe("USD 60,000");
    expect(answerText(formOf("dpa"), "transfer_countries", { transfer: "Yes", paper: "Our template",
      transfer_countries: [{ country: "France" }] }, ctx)).toBe("1 country outside India: France");
  });
});
