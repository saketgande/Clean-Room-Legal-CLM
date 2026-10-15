import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { WordingEditor, type FindingRef } from "./_wording-editor";
import { toEditorText } from "@/lib/templates";
import type { DraftingTemplate, TemplateMutationResult, TemplateStatus } from "@/lib/types";

// The Wording tab edits text the person sees in plain names and saves it back as str.format wording. Failure
// modes these tests guard: raw {codes} leaking into the editor (FR-62), a save that overwrites a colleague's
// newer version (FR-59), a save with no warning that it starts a paid review (FR-13), wording the server will
// refuse being sent anyway (FR-34), drafts that silently stop naming a party (FR-35), a jump that selects the
// wrong text (FR-24), and change controls offered to people the server will refuse (FR-64).

const h = vi.hoisted(() => ({ save: vi.fn(), get: vi.fn(), notify: vi.fn() }));
vi.mock("@/lib/endpoints", () => ({ draftingTemplatesApi: { save: h.save, get: h.get } }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const FIELDS = [
  { name: "company", label: "Our entity", about: "The company that drafts", sample: "Acme Ltd" },
  { name: "counterparty", label: "The other party", about: "The company we contract with", sample: "Globex" },
  { name: "payment_days", label: "Payment days", about: "Days to pay an invoice", sample: "30" },
];
const BODY = "Between {company} and {counterparty}, pay within {payment_days} days.";
const STATUS = {
  state: "passes", label: "Passes", reason: "No findings.", finding_count: 0, highest_severity: null, changed: null,
  error_code: null, results_vary: false, decided_by: null, last_completed: null, active_check: null,
} as TemplateStatus;

const template = (over: Partial<DraftingTemplate> = {}): DraftingTemplate => ({
  key: "saas", name: "Software / SaaS Agreement", label: "Software / SaaS Agreement", contract_type: "Software / SaaS Agreement",
  binding: { form_key: "saas", form_name: "SaaS", agreement_kind: null, label: "SaaS agreements" },
  playbook: { id: "p1", name: "SaaS Playbook", version_id: "pv3", version_number: 3, pinned: true, available: true },
  shipped: true, active: true, source: "text", version: 3, original_filename: null, updated_at: null, updated_by: null,
  editable: true, status: STATUS, body: BODY, default_body: BODY, placeholders: FIELDS, fields: [], original_file: null,
  ...over,
});
const result = (tpl: DraftingTemplate, over: Partial<TemplateMutationResult> = {}): TemplateMutationResult => ({
  template: tpl, changed: true, check: null, ...over,
});

const spies = {
  onSaved: vi.fn(),
  onTemplateRefreshed: vi.fn(),
  onApplied: vi.fn(),
  onText: vi.fn(),
};

/** The page holds the editor text; this stands in for it. */
function Harness({
  tpl = template(),
  canChange = true,
  initial,
  applied = [],
  jumpTo = null,
}: {
  tpl?: DraftingTemplate;
  canChange?: boolean;
  initial?: string;
  applied?: FindingRef[];
  jumpTo?: { start: number; end: number } | null;
}) {
  const [text, setText] = useState(initial ?? toEditorText(tpl.body, tpl.placeholders));
  return (
    <WordingEditor
      template={tpl}
      canChange={canChange}
      editorText={text}
      onEditorTextChange={(t) => { spies.onText(t); setText(t); }}
      appliedFindings={applied}
      onAppliedFindingsChange={spies.onApplied}
      jumpTo={jumpTo}
      onSaved={spies.onSaved}
      onTemplateRefreshed={spies.onTemplateRefreshed}
    />
  );
}

const area = () => screen.getByLabelText("Software / SaaS Agreement template text") as HTMLTextAreaElement;
const edit = (value: string) => fireEvent.change(area(), { target: { value } });
const dialog = () => screen.getByRole("dialog");

beforeEach(() => {
  vi.resetAllMocks();
});

describe("fill-in fields under their plain names (FR-62)", () => {
  it("shows chips, never the raw code, with a mirror of the chips behind the text", () => {
    const { container } = render(<Harness />);
    expect(area().value).toBe("Between «Our entity» and «The other party», pay within «Payment days» days.");
    expect(area().value).not.toContain("{");
    const mirror = container.querySelector('[aria-hidden="true"]') as HTMLElement;
    // Same characters as the textarea, or the colours would not line up with the text on top of them.
    expect(mirror.textContent).toBe(area().value);
    expect(mirror.querySelectorAll("span")).toHaveLength(3);
  });

  it("lists every field with what it fills and an example, and Insert puts it at the cursor", () => {
    render(<Harness />);
    const list = screen.getByRole("complementary", { name: "Fill-in fields" });
    expect(within(list).getByText("Days to pay an invoice")).toBeInTheDocument();
    expect(within(list).getByText("e.g. Globex")).toBeInTheDocument();

    area().setSelectionRange(0, 0);
    fireEvent.click(screen.getByRole("button", { name: "Insert Payment days" }));
    expect(area().value.startsWith("«Payment days»Between")).toBe(true);
  });

  it("turns known {codes} in pasted wording into chips and leaves unknown ones visible", () => {
    render(<Harness initial="" />);
    fireEvent.paste(area(), { clipboardData: { getData: () => "Pay {company} {nope}" } });
    expect(area().value).toBe("Pay «Our entity» {nope}");
  });
});

describe("wording problems with their fixes (FR-34)", () => {
  it("names the problem, blocks saving, and offers to keep it as ordinary text", () => {
    render(<Harness initial="Dear {client_name}, welcome. «Our entity» «The other party»" />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Unknown placeholder {client_name} (line 1). Use one from the list, or keep it as ordinary text by writing it as {{client_name}}.",
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Keep as ordinary text" }));
    expect(area().value).toBe("Dear {{client_name}}, welcome. «Our entity» «The other party»");
    expect(screen.queryByText(/Unknown placeholder/)).toBeNull();
  });

  it("offers to use one of our fields instead", () => {
    render(<Harness initial="Dear {client_name}, welcome. «Our entity»" />);
    fireEvent.change(screen.getByLabelText("Use a field for {client_name}"), { target: { value: "counterparty" } });
    expect(area().value).toBe("Dear «The other party», welcome. «Our entity»");
  });

  it("refuses to save an unpaired brace and offers to double it", () => {
    render(<Harness initial="Fee {oops «Our entity» «The other party»" />);
    expect(screen.getByRole("alert")).toHaveTextContent("The braces don't pair up (line 1)");
    fireEvent.click(screen.getByRole("button", { name: "Keep as ordinary text" }));
    expect(area().value).toBe("Fee {{oops «Our entity» «The other party»");
  });
});

describe("a wording with no field for a party (FR-35)", () => {
  it("warns, and saves only after the person ticks that it is intended", async () => {
    h.save.mockResolvedValue(result(template({ version: 4, body: "Pay within {payment_days} days." })));
    render(<Harness />);
    edit("Pay within «Payment days» days.");

    expect(screen.getByRole("status")).toHaveTextContent(
      "This wording has no field for our entity or the other party, so drafts will not name them. Confirm that this is intended to save it.",
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(within(dialog()).getByRole("button", { name: "Save and start the check" }));

    await waitFor(() => expect(h.save).toHaveBeenCalled());
    expect(h.save.mock.calls[0][1]).toMatchObject({ acknowledge_missing_parties: true });
  });

  it("does not ask when both parties are named", () => {
    render(<Harness />);
    edit("«Our entity» agrees with «The other party» to pay within 30 days.");
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });
});

describe("saving (FR-13, FR-59)", () => {
  it("says a paid review starts, sends nothing until confirmed, then sends the version it loaded and the suggestions applied", async () => {
    const saved = template({ version: 4, body: "Between {company} and {counterparty}, pay within 45 days." });
    h.save.mockResolvedValue(result(saved, { check: { id: "c9" } as TemplateMutationResult["check"] }));
    const applied: FindingRef[] = [{ check_id: "c1", finding_index: 0 }];
    render(<Harness applied={applied} />);
    edit("Between «Our entity» and «The other party», pay within 45 days.");
    fireEvent.change(screen.getByLabelText("What changed? (optional)"), { target: { value: "  longer terms " } });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(dialog()).toHaveTextContent("Saving starts one paid AI review of the new wording");
    fireEvent.click(within(dialog()).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(h.save).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(within(dialog()).getByRole("button", { name: "Save and start the check" }));
    await waitFor(() => expect(spies.onSaved).toHaveBeenCalled());

    expect(h.save).toHaveBeenCalledWith("saas", {
      body: "Between {company} and {counterparty}, pay within 45 days.", // codes again, not chips
      note: "longer terms",
      expected_version: 3,
      applied_findings: applied,
      acknowledge_missing_parties: false,
    });
    expect(spies.onApplied).toHaveBeenCalledWith([]);
    expect(area().value).toBe("Between «Our entity» and «The other party», pay within 45 days.");
    expect(h.notify).toHaveBeenCalledWith("Template saved. New drafts use it, and a check has started.", "success");
    expect(spies.onSaved.mock.calls[0][0].template.version).toBe(4);
  });

  it("checks the next save against the version this save made, so a person's own save is never a conflict", async () => {
    h.save.mockResolvedValueOnce(result(template({ version: 4, body: "{company} and {counterparty}, 45 days." })));
    h.save.mockResolvedValueOnce(result(template({ version: 5 })));
    render(<Harness />);
    for (const text of ["«Our entity» and «The other party», 45 days.", "«Our entity» and «The other party», 60 days."]) {
      edit(text);
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      fireEvent.click(within(dialog()).getByRole("button", { name: "Save and start the check" }));
      await waitFor(() => expect(spies.onSaved).toHaveBeenCalledTimes(text.includes("45") ? 1 : 2));
    }
    expect(h.save.mock.calls.map((c) => c[1].expected_version)).toEqual([3, 4]);
  });

  it("does not promise a review when no playbook reviews the template", () => {
    render(<Harness tpl={template({ playbook: null })} />);
    edit("«Our entity» and «The other party» agree.");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(dialog()).toHaveTextContent("No playbook reviews this template, so no check will run.");
    expect(dialog()).not.toHaveTextContent("paid AI review");
    expect(screen.queryByText("Saving starts one paid AI review of the new wording.")).toBeNull();
  });

  it("shows the server's reason when a save is refused, and keeps the text", async () => {
    h.save.mockRejectedValue(new Error("Unknown placeholder {x} (line 2)."));
    render(<Harness />);
    edit("«Our entity» «The other party» changed");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(within(dialog()).getByRole("button", { name: "Save and start the check" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Unknown placeholder {x} (line 2).");
    expect(area().value).toBe("«Our entity» «The other party» changed");
    expect(spies.onSaved).not.toHaveBeenCalled();
  });

  it("Discard changes brings back the saved wording and forgets applied suggestions", () => {
    render(<Harness applied={[{ check_id: "c1", finding_index: 0 }]} />);
    edit("something else entirely");
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(area().value).toBe("Between «Our entity» and «The other party», pay within «Payment days» days.");
    expect(spies.onApplied).toHaveBeenCalledWith([]);
    expect(screen.getByRole("button", { name: "Discard changes" })).toBeDisabled();
  });
});

describe("a colleague saved first (FR-59)", () => {
  const MESSAGE =
    "Priya Shah saved version 4 of this template at 2026-10-15 09:12 UTC after you opened it. Your text is still in the editor: compare it with theirs, then save again.";
  const theirs = () => template({ version: 4, body: "Theirs: {company} and {counterparty}." });

  async function conflict() {
    h.save.mockRejectedValueOnce(Object.assign(new Error(MESSAGE), { status: 409 }));
    h.get.mockResolvedValue(theirs());
    render(<Harness />);
    edit("Mine: «Our entity» and «The other party».");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(within(dialog()).getByRole("button", { name: "Save and start the check" }));
    await screen.findByText(MESSAGE);
  }

  it("names who saved, keeps the person's text, and shows both versions side by side", async () => {
    await conflict();
    expect(area().value).toBe("Mine: «Our entity» and «The other party».");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Compare with their version" }));
    expect(within(screen.getByRole("region", { name: "Your text (not saved)" })).getByText(/Mine:/)).toBeInTheDocument();
    const their = screen.getByRole("region", { name: "Their version (version 4)" });
    expect(their).toHaveTextContent("Theirs: «Our entity» and «The other party».");
  });

  it("Load theirs replaces the editor text and tells the page to adopt their version", async () => {
    await conflict();
    fireEvent.click(screen.getByRole("button", { name: "Load theirs into the editor" }));
    expect(area().value).toBe("Theirs: «Our entity» and «The other party».");
    expect(spies.onTemplateRefreshed).toHaveBeenCalledWith(expect.objectContaining({ version: 4 }));
    expect(spies.onApplied).toHaveBeenCalledWith([]);
    expect(screen.queryByText(MESSAGE)).toBeNull();
  });

  it("Keep mine keeps the text, and the next save is checked against their version", async () => {
    await conflict();
    fireEvent.click(screen.getByRole("button", { name: "Keep mine" }));
    expect(area().value).toBe("Mine: «Our entity» and «The other party».");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();

    h.save.mockResolvedValueOnce(result(template({ version: 5 })));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(within(dialog()).getByRole("button", { name: "Save and start the check" }));
    await waitFor(() => expect(h.save).toHaveBeenCalledTimes(2));
    expect(h.save.mock.calls[1][1]).toMatchObject({ expected_version: 4 });
  });
});

describe("jump to a passage (FR-24)", () => {
  it("selects the range in the editor and highlights it", () => {
    const { container } = render(<Harness jumpTo={{ start: 8, end: 20 }} />);
    expect(area().selectionStart).toBe(8);
    expect(area().selectionEnd).toBe(20);
    const mark = container.querySelector("mark") as HTMLElement;
    expect(mark.textContent).toBe("«Our entity»");
  });

  it("drops the highlight once the text changes, because its offsets no longer mean anything", () => {
    const { container } = render(<Harness jumpTo={{ start: 8, end: 20 }} />);
    edit("Between «Our entity» and «The other party», pay within 60 days.");
    expect(container.querySelector("mark")).toBeNull();
  });
});

describe("who may change it (FR-64) and Word-based templates (FR-40)", () => {
  it("is read-only without the permission: no save bar, no Insert, no problem fixes", () => {
    render(<Harness canChange={false} />);
    expect(area().readOnly).toBe(true);
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Discard changes" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Insert / })).toBeNull();
    expect(screen.getByText("Days to pay an invoice")).toBeInTheDocument();
  });

  it("shows a template made from a Word file as read-only wording, and says how to change it", () => {
    render(<Harness tpl={template({ editable: false, source: "docx" })} />);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent(
      "This template was made from a Word file, so its wording changes only by uploading a replacement: download it, edit it in Word, then upload it.",
    );
    expect(screen.getByRole("region", { name: "Software / SaaS Agreement wording (read only)" })).toHaveTextContent(
      "Between «Our entity» and «The other party»",
    );
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });
});
