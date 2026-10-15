import { describe, expect, it } from "vitest";
import {
  canApplyFinding,
  locatePassage,
  missingPartyFields,
  needsAttention,
  severityTone,
  sha256Hex,
  statusTone,
  templatePermissions,
  toBodyText,
  toEditorText,
  wordingProblems,
  type ApplyInput,
} from "./templates";
import type { TemplateStatusState } from "./types";

const FIELDS = [
  { name: "company", label: "Our entity" },
  { name: "counterparty", label: "The other party" },
  { name: "payment_days", label: "Payment days" },
];

describe("toEditorText / toBodyText (FR-62)", () => {
  it("shows a known field under its plain name and turns it back into the code", () => {
    const body = "Between {company} and {counterparty}, pay within {payment_days} days.";
    const editor = toEditorText(body, FIELDS);
    expect(editor).toBe("Between «Our entity» and «The other party», pay within «Payment days» days.");
    expect(editor).not.toContain("{");
    expect(toBodyText(editor, FIELDS)).toBe(body);
  });

  it("round-trips literal braces and unknown codes unchanged, so they stay visible and flagged", () => {
    const body = "Keep {{company}} as text, but {client_name} is not a field, nor {company!r}. {counterparty} signs.";
    const editor = toEditorText(body, FIELDS);
    expect(editor).toBe(
      "Keep {{company}} as text, but {client_name} is not a field, nor {company!r}. «The other party» signs.",
    );
    expect(toBodyText(editor, FIELDS)).toBe(body);
  });

  it("reads a field between escaped braces as a field", () => {
    expect(toEditorText("{{{company}}}", FIELDS)).toBe("{{«Our entity»}}");
    expect(toBodyText("{{«Our entity»}}", FIELDS)).toBe("{{{company}}}");
  });

  it("leaves a «Label» that names no field alone (pasted code becomes a chip only when it is known)", () => {
    expect(toBodyText("«Something else» and «Our entity»", FIELDS)).toBe("«Something else» and {company}");
    // pasting raw wording: running it through toEditorText turns the known code into a chip
    expect(toEditorText("{company} {nope}", FIELDS)).toBe("«Our entity» {nope}");
  });
});

describe("wordingProblems (the cases of the server's check_body)", () => {
  it("accepts wording drafting can fill, in either notation", () => {
    expect(wordingProblems("{company} and {counterparty}; {{literal}}", FIELDS)).toEqual([]);
    expect(wordingProblems("«Our entity» and «The other party»", FIELDS)).toEqual([]);
  });

  it("refuses an empty template", () => {
    const [p] = wordingProblems("  \n ", FIELDS);
    expect(p.kind).toBe("empty");
    expect(p.message).toBe("The template is empty.");
  });

  it("names an unknown placeholder, its line and its place", () => {
    const text = "Line one.\nLine two.\nHello {client_name}, welcome.";
    const [p] = wordingProblems(text, FIELDS);
    expect(p).toMatchObject({ kind: "unknown_field", line: 3, text: "{client_name}" });
    expect(text.slice(p.start, p.end)).toBe("{client_name}");
    expect(p.message).toBe(
      "Unknown placeholder {client_name} (line 3). Use one from the list, or keep it as ordinary text by writing it as {{client_name}}.",
    );
  });

  it("reads a conversion, a format spec, a positional and an empty field as unknown", () => {
    const text = "{company!r} {company:>5} {0} {}";
    expect(wordingProblems(text, FIELDS).map((p) => p.text)).toEqual(["{company!r}", "{company:>5}", "{0}", "{}"]);
  });

  it("reports a brace with no partner, on its line", () => {
    const lone = wordingProblems("Fine.\nThis is { open", FIELDS);
    expect(lone).toHaveLength(1);
    expect(lone[0]).toMatchObject({ kind: "unpaired_brace", line: 2, text: "{" });
    expect(lone[0].message).toBe("The braces don't pair up (line 2). Write a literal brace as {{ or }}.");
    expect(wordingProblems("close } here", FIELDS)[0]).toMatchObject({ kind: "unpaired_brace", text: "}" });
    expect(wordingProblems("{company}}", FIELDS)[0]).toMatchObject({ kind: "unpaired_brace", start: 9 });
    expect(wordingProblems("{ab{company}", FIELDS)).toHaveLength(1); // the lone `{`; {company} itself is fine
  });

  it("lists every problem, not only the first", () => {
    expect(wordingProblems("{a} {company} {b} }", FIELDS).map((p) => p.kind)).toEqual([
      "unknown_field", "unknown_field", "unpaired_brace",
    ]);
  });
});

describe("missingPartyFields (FR-35)", () => {
  it("lists the party the wording has no field for", () => {
    expect(missingPartyFields("{company} agrees with {counterparty}.", FIELDS)).toEqual([]);
    expect(missingPartyFields("«Our entity» agrees.", FIELDS)).toEqual(["counterparty"]);
    expect(missingPartyFields("No parties here.", FIELDS)).toEqual(["company", "counterparty"]);
  });

  it("does not count a literal {{company}} as a field", () => {
    expect(missingPartyFields("{{company}} {{counterparty}}", FIELDS)).toEqual(["company", "counterparty"]);
  });
});

describe("locatePassage (FR-24)", () => {
  const text = "Payment is due in 30 days.\nLiability is capped.\nPayment is due in 30 days.";

  it("finds nothing for an absent or blank passage", () => {
    expect(locatePassage(text, "Termination for cause")).toEqual({ start: -1, end: -1, count: 0 });
    expect(locatePassage(text, "")).toEqual({ start: -1, end: -1, count: 0 });
    expect(locatePassage(text, null)).toEqual({ start: -1, end: -1, count: 0 });
    expect(locatePassage(text, "  ")).toEqual({ start: -1, end: -1, count: 0 });
  });

  it("locates a passage that occurs once", () => {
    const r = locatePassage(text, "Liability is capped.");
    expect(r.count).toBe(1);
    expect(text.slice(r.start, r.end)).toBe("Liability is capped.");
  });

  it("counts every occurrence and points at the first", () => {
    const r = locatePassage(text, "Payment is due in 30 days.");
    expect(r).toEqual({ start: 0, end: 26, count: 2 });
  });

  it("counts overlapping matches, so an ambiguous passage is never taken as unique", () => {
    expect(locatePassage("aaa", "aa").count).toBe(2);
  });

  it("falls back to matching across different whitespace", () => {
    const r = locatePassage("The  Supplier\nshall   indemnify the Customer.", "The Supplier shall indemnify");
    expect(r.count).toBe(1);
    expect(r.start).toBe(0);
    expect(r.end).toBe("The  Supplier\nshall   indemnify".length);
  });

  it("matches regex characters in the passage literally", () => {
    expect(locatePassage("Fees (excl. VAT) apply.", "(excl.  VAT)").count).toBe(1);
    expect(locatePassage("Fees apply.", "F.es").count).toBe(0);
  });

  it("prefers the exact match when there is one", () => {
    expect(locatePassage("a b  a  b", "a  b")).toEqual({ start: 5, end: 9, count: 1 });
  });
});

describe("canApplyFinding truth table (FR-25)", () => {
  const editorText = "Intro.\nInvoices are paid within «Payment days» days.\nOutro.";
  const base: ApplyInput = {
    check: { state: "completed", unsaved: false, template_version: 4, wording_sha256: "sha-4" },
    finding: {
      suggestion_is_wording: true,
      quote: "Invoices are paid within 45 days.",
      quote_template: "Invoices are paid within {payment_days} days.",
      suggested_fix: "Invoices are paid within 30 days.",
      suggested_fix_template: "Invoices are paid within 30 days.",
    },
    editorText,
    currentVersion: 4,
    currentSha: "sha-4",
    editable: true,
    canChange: true,
    fields: FIELDS,
  };
  const withFinding = (f: Partial<ApplyInput["finding"]>): ApplyInput => ({ ...base, finding: { ...base.finding, ...f } });
  const withCheck = (c: Partial<ApplyInput["check"]>): ApplyInput => ({ ...base, check: { ...base.check, ...c } });

  it("is offered when every condition holds, finding the quote through its {codes}", () => {
    expect(canApplyFinding(base)).toBe(true);
  });

  it("is never offered for a Word-based template", () => {
    expect(canApplyFinding({ ...base, editable: false })).toBe(false);
  });

  it("is not offered to someone who may not change templates", () => {
    expect(canApplyFinding({ ...base, canChange: false })).toBe(false);
  });

  it("is not offered for advice, or when the review gave no wording", () => {
    expect(canApplyFinding(withFinding({ suggestion_is_wording: false }))).toBe(false);
    expect(canApplyFinding(withFinding({ suggested_fix: null, suggested_fix_template: null }))).toBe(false);
  });

  it("is not offered from a check of an older version or of other wording", () => {
    expect(canApplyFinding(withCheck({ template_version: 3 }))).toBe(false);
    expect(canApplyFinding(withCheck({ wording_sha256: "sha-3" }))).toBe(false);
  });

  it("is not offered from a check that has not completed", () => {
    for (const state of ["queued", "running", "failed", "superseded"] as const) {
      expect(canApplyFinding(withCheck({ state }))).toBe(false);
    }
  });

  it("is offered from an unsaved check made against the loaded version, whatever the saved sha", () => {
    expect(canApplyFinding(withCheck({ unsaved: true, wording_sha256: "sha-of-editor-text" }))).toBe(true);
    expect(canApplyFinding(withCheck({ unsaved: true, template_version: 3 }))).toBe(false);
  });

  it("is not offered when the passage is gone from the editor", () => {
    expect(canApplyFinding({ ...base, editorText: "Intro.\nOutro." })).toBe(false);
  });

  it("is not offered when the passage occurs twice", () => {
    expect(canApplyFinding({ ...base, editorText: `${editorText}\n${editorText}` })).toBe(false);
  });

  it("falls back to the quote itself when the review gave no {code} form", () => {
    const f = withFinding({ quote_template: null, quote: "Outro." });
    expect(canApplyFinding(f)).toBe(true);
    expect(canApplyFinding(withFinding({ quote_template: null, quote: null }))).toBe(false);
  });
});

describe("templatePermissions (FR-64)", () => {
  it("lets a legal reviewer view and run but not change", () => {
    expect(templatePermissions({ permissions: ["playbook:read", "playbook:run"] })).toEqual({
      canView: true, canRun: true, canChange: false,
    });
  });

  it("lets an editor change", () => {
    expect(templatePermissions({ permissions: ["playbook:read", "playbook:run", "playbook:update"] }).canChange).toBe(true);
  });

  it("honours the wildcard", () => {
    expect(templatePermissions({ permissions: ["*"] })).toEqual({ canView: true, canRun: true, canChange: true });
  });

  it("gives a signed-out or permission-less user nothing (unlike can(), which says yes to anyone)", () => {
    const none = { canView: false, canRun: false, canChange: false };
    expect(templatePermissions(null)).toEqual(none);
    expect(templatePermissions(undefined)).toEqual(none);
    expect(templatePermissions({ permissions: [] })).toEqual(none);
    expect(templatePermissions({ permissions: ["contract:read"] })).toEqual(none);
  });
});

describe("needsAttention / tones (FR-63, FR-65)", () => {
  it("flags exactly the five statuses the filter narrows to", () => {
    const all: TemplateStatusState[] = [
      "passes", "has_findings", "checking", "out_of_date", "partly_checked", "couldnt_check", "never_checked", "no_playbook",
    ];
    expect(all.filter(needsAttention)).toEqual([
      "has_findings", "out_of_date", "partly_checked", "couldnt_check", "never_checked",
    ]);
  });

  it("gives every status a tone, with findings red only when serious", () => {
    expect(statusTone("passes")).toBe("green");
    expect(statusTone("has_findings", "critical")).toBe("red");
    expect(statusTone("has_findings", "high")).toBe("red");
    expect(statusTone("has_findings", "medium")).toBe("amber");
    expect(statusTone("has_findings", null)).toBe("amber");
    expect(statusTone("couldnt_check")).toBe("red");
    expect(statusTone("checking")).toBe("blue");
    expect(statusTone("out_of_date")).toBe("amber");
    expect(statusTone("never_checked")).toBe("slate");
    expect(statusTone("no_playbook")).toBe("slate");
    expect(severityTone("low")).toBe("blue");
    expect(severityTone("critical")).toBe("red");
  });
});

describe("sha256Hex", () => {
  it("is the hex digest the server stores as wording_sha256", async () => {
    expect(await sha256Hex("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });
});
