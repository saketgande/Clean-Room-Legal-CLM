import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AGREEMENT_FORMS, AgreementWizard, Question } from "./_agreement-forms";
import { fmtDate } from "@/lib/utils";
import { EMPTY_BOX } from "@/lib/summary-edits";
import type { IntakeDraft } from "@/lib/types";

// What a person meets while filling a request form (round 2, bugs 7, 46 and 47): answers that cannot be right are
// refused with a reason, and the line above the form tells the truth about what is still wrong.

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  intakeApi: { forms: vi.fn(), approvalPreview: vi.fn(), create: vi.fn(), ingestAttachment: vi.fn(), mine: vi.fn(), createDraft: vi.fn(), updateDraft: vi.fn(), deleteDraft: vi.fn() },
  contractsApi: { list: vi.fn() },
  partiesApi: { entities: vi.fn(), counterparties: vi.fn(), createCounterparty: vi.fn() },
}));
vi.mock("@/lib/endpoints", () => ({ intakeApi: h.intakeApi, contractsApi: h.contractsApi, partiesApi: h.partiesApi }));
// buildSummary can be made to throw for the "a broken summary never stops filing" test; otherwise it is the real one.
const boom = vi.hoisted(() => ({ on: false }));
vi.mock("@/lib/request-summary", async (orig) => {
  const m = await orig<typeof import("@/lib/request-summary")>();
  return { ...m, buildSummary: (...a: Parameters<typeof m.buildSummary>) => { if (boom.on) throw new Error("boom"); return m.buildSummary(...a); } };
});
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { full_name: "Saket Gande" } }) }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const serverForms = JSON.parse(
  readFileSync(resolve(__dirname, "../../../../../backend/app/intake/agreement_forms.json"), "utf8"),
);

function mount(formKey: string, draft?: IntakeDraft) {
  const def = AGREEMENT_FORMS.find((f) => f.key === formKey)!;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onFiled = vi.fn();
  const onBack = vi.fn();
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  render(
    <QueryClientProvider client={qc}>
      <AgreementWizard def={def} draft={draft} onFiled={onFiled} onBack={onBack} />
    </QueryClientProvider>,
  );
  return { onFiled, onBack };
}

const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const next = () => fireEvent.click(screen.getByRole("button", { name: "Continue" }));
const banner = () => screen.queryByRole("alert");

async function sowStep1(over: { email?: string } = {}) {
  await screen.findByLabelText("Which master agreement is this under?");
  type("Which master agreement is this under?", "c-1");
  type("Their signer: name", "Maya Chen");
  type("Their signer: email", over.email ?? "maya@example.com");
  type("Requesting department", "Procurement");
}

/** Fill row `n` of the deliverables table. */
function fillRow(n: number, name: string, due: string, owners: [string, string] = ["Priya Nair", "Ravi Shah"], share?: string) {
  type(`Row ${n}, Deliverable name`, name);
  type(`Row ${n}, Due date`, due);
  type(`Row ${n}, Their owner`, owners[0]);
  type(`Row ${n}, Our owner`, owners[1]);
  if (share !== undefined) type(`Row ${n}, Share of the fee (%)`, share);
}

beforeEach(() => {
  boom.on = false;
  vi.resetAllMocks();
  h.intakeApi.forms.mockResolvedValue(serverForms);
  h.intakeApi.approvalPreview.mockResolvedValue({ workflow: null, approvers: [] });
  h.intakeApi.mine.mockResolvedValue([]);
  h.contractsApi.list.mockResolvedValue([{ id: "c-1", title: "Acme MSA", counterparty_name: "Acme Ltd", expiration_date: null }]);
  h.partiesApi.counterparties.mockResolvedValue([]);
  h.partiesApi.entities.mockResolvedValue([]);
});

describe("an email that cannot be delivered (bug 7)", () => {
  it("is refused on Continue, and says what to type instead", async () => {
    mount("sow");
    await sowStep1({ email: "maya.example.com" });
    next();
    expect(banner()).toHaveTextContent("Their signer: email: Enter a full email address, like name@company.com.");
    expect(screen.getByText("Enter a full email address, like name@company.com.", { selector: "p" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "The master agreement" })).toBeInTheDocument(); // still on step 1
  });

  it("lets the person on once it is fixed, and the banner goes with it", async () => {
    mount("sow");
    await sowStep1({ email: "maya.example.com" });
    next();
    expect(banner()).not.toBeNull();
    type("Their signer: email", "maya@example.com");
    expect(banner()).toBeNull();
    next();
    expect(await screen.findByRole("heading", { name: "Scope and fees" })).toBeInTheDocument();
  });
});

describe("an end date before the start date (bug 7)", () => {
  async function toStep2() {
    mount("sow");
    await sowStep1();
    next();
    await screen.findByRole("heading", { name: "Scope and fees" });
    type("SoW title", "Phase 2");
    fireEvent.change(screen.getAllByLabelText("Total value of this SoW")[1], { target: { value: "1000" } }); // [0] is the currency
    type("Pricing", "Fixed fee");
    type("Scope of services", "Hosting");
    fillRow(1, "A report", "2026-12-01");
  }

  it("is refused, naming the date it must not come before", async () => {
    await toStep2();
    type("Services start", "2026-11-01");
    type("Services end", "2026-10-01");
    next();
    expect(banner()).toHaveTextContent("Services end: Can't be before services start.");
    expect(screen.getByRole("heading", { name: "Scope and fees" })).toBeInTheDocument();
  });

  it("offers no day before the start in the date picker", async () => {
    await toStep2();
    type("Services start", "2026-11-01");
    expect(screen.getByLabelText("Services end")).toHaveAttribute("min", "2026-11-01");
    expect(screen.getByLabelText("Services start")).not.toHaveAttribute("min");
  });

  it("is accepted on the same day, and moving the start date later brings the complaint back", async () => {
    await toStep2();
    type("Services start", "2026-11-01");
    type("Services end", "2026-11-01");
    type("Services start", "2026-12-01");
    next();
    expect(banner()).toHaveTextContent("Services end: Can't be before services start.");
  });
});

describe("dates on the review step (bug 48)", () => {
  it("read the way the rest of the app writes a date, not as the stored YYYY-MM-DD", async () => {
    mount("sow");
    await sowStep1();
    next();
    await screen.findByRole("heading", { name: "Scope and fees" });
    type("SoW title", "Phase 2");
    type("Services start", "2026-11-01");
    type("Services end", "2027-10-31");
    fireEvent.change(screen.getAllByLabelText("Total value of this SoW")[1], { target: { value: "1000" } });
    type("Pricing", "Fixed fee");
    type("Scope of services", "Hosting");
    fillRow(1, "A report", "2026-12-01");
    next();
    await screen.findByRole("heading", { name: "Risk checks and files" });
    type("Will personal data be shared or processed?", "No");
    type("Is this GxP or other regulated quality work?", "No");
    next();
    await screen.findByRole("heading", { name: "Review and submit" });
    const start = screen.getByText("Services start").closest("div")!;
    expect(start).toHaveTextContent(fmtDate("2026-11-01"));
    expect(start).not.toHaveTextContent("2026-11-01");
    expect(screen.getByText("Services end").closest("div")).toHaveTextContent(fmtDate("2027-10-31"));
  });
});

describe("the 'answers still needed' line (bug 47)", () => {
  it("lists only what is still missing as the person fills the form in", async () => {
    mount("sow");
    await screen.findByLabelText("Which master agreement is this under?");
    next();
    expect(banner()).toHaveTextContent(
      "4 answers still needed: Which master agreement is this under?, Their signer: name, Their signer: email, Requesting department.",
    );

    type("Their signer: name", "Maya Chen");
    expect(banner()).toHaveTextContent("3 answers still needed: Which master agreement is this under?, Their signer: email, Requesting department.");
    expect(banner()).not.toHaveTextContent("Their signer: name");

    type("Which master agreement is this under?", "c-1");
    type("Their signer: email", "maya@example.com");
    expect(banner()).toHaveTextContent("1 answer still needed: Requesting department.");

    type("Requesting department", "Procurement");
    expect(banner()).toBeNull();
  });

  it("marks only the questions that still need an answer", async () => {
    mount("sow");
    await screen.findByLabelText("Their signer: name");
    next();
    expect(screen.getAllByText("Needed before you continue.")).toHaveLength(4);
    type("Their signer: name", "Maya Chen");
    expect(screen.getAllByText("Needed before you continue.")).toHaveLength(3);
  });
});

describe("a counterparty made from the form (bug 46)", () => {
  async function openCreate() {
    mount("new_agreement");
    const box = await screen.findByLabelText("Counterparty");
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "Globex" } });
    fireEvent.click(await screen.findByRole("button", { name: /Create .*Globex.* as a new counterparty/ }));
    return screen.findByText(/New counterparty: Globex/);
  }

  it("cannot be created without a jurisdiction and a contact email", async () => {
    h.partiesApi.createCounterparty.mockResolvedValue({ id: "cp-1", name: "Globex", jurisdiction: "India", contact_email: "a@globex.com" });
    await openCreate();
    const create = screen.getByRole("button", { name: "Create counterparty" });
    expect(create).toBeDisabled();
    expect(screen.getByText(/needs a jurisdiction and a contact email/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Jurisdiction"), { target: { value: "India" } });
    expect(create).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Contact email"), { target: { value: "legal.globex.com" } });
    expect(create).toBeDisabled();
    expect(screen.getByText("Enter a full email address, like name@company.com.")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Contact email"), { target: { value: "legal@globex.com" } });
    expect(create).toBeEnabled();
    fireEvent.click(create);
    await waitFor(() =>
      expect(h.partiesApi.createCounterparty).toHaveBeenCalledWith({ name: "Globex", jurisdiction: "India", contact_email: "legal@globex.com" }),
    );
  });
});

// ---- The deliverables table (007) ----------------------------------------------------------------------------
// The Statement of Work asks for its deliverables as a table: a row each, checked by the same rules the server
// applies when the request is filed (lib/form-checks.ts, pinned by one shared cases file).

const ANSWERS = {
  parent_contract_id: "c-1", cp_signer_name: "Maya Chen", cp_signer_email: "maya.chen@example.com", department: "Procurement",
  sow_title: "Data platform migration, phase 2", services_start: "2026-11-01", services_end: "2027-03-31",
  value: "4500000", currency: "INR", pricing: "Fixed fee", scope: "Migrate the data warehouse.", personal_data: "No", gxp: "No",
};
const ROWS = [
  { name: "Migration plan", due: "2026-11-30", their_owner: "Priya Nair", our_owner: "Ravi Shah" },
  { name: "Migrated warehouse", due: "2027-02-15", their_owner: "Priya Nair", our_owner: "Ravi Shah" },
  { name: "Handover pack", due: "2027-03-31", their_owner: "Tom Reyes", our_owner: "Ravi Shah" },
];
const OLD_TEXT = "Plan by Nov, report by Mar, Ravi signs off";
const draftAt = (page: number, values: IntakeDraft["values"]): IntakeDraft => ({
  id: "d-1", form_key: "sow", title: null, values, parent_contract_id: "c-1", page_index: page, visited: page,
  created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
});

/** The Scope and fees step, the way a person reaches it, with the dates and the money answered and no row filled. */
async function toScope(pricing = "Fixed fee") {
  const handles = mount("sow");
  await sowStep1();
  next();
  await screen.findByRole("heading", { name: "Scope and fees" });
  type("SoW title", "Phase 2");
  type("Services start", "2026-11-01");
  type("Services end", "2027-03-31");
  fireEvent.change(screen.getAllByLabelText("Total value of this SoW")[1], { target: { value: "1000" } }); // [0] is the currency
  type("Pricing", pricing);
  type("Scope of services", "Hosting");
  return handles;
}
const add = () => screen.getByRole("button", { name: /Add a deliverable/ });
const reviewOf = async (values: IntakeDraft["values"]) => {
  h.intakeApi.deleteDraft.mockResolvedValue(undefined);
  const handles = mount("sow", draftAt(3, values));
  await screen.findByRole("heading", { name: "Review and submit" });
  return handles;
};

describe("the deliverables question is a table (AC-10)", () => {
  it("has the four columns and one row to start, and no free-text box", async () => {
    await toScope();
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((c) => c.textContent))
      .toEqual(["Row", "Deliverable name", "Due date", "Their owner", "Our owner", "Remove row"]);
    expect(screen.getByLabelText("Row 1, Deliverable name")).toHaveValue("");
    expect(screen.queryByLabelText("Row 2, Deliverable name")).toBeNull();
    expect(screen.queryByLabelText("Deliverables and who accepts them")).toBeNull();
    expect(screen.getByText(/one entry per row in this order \(D1, D2/)).toBeInTheDocument(); // what the answer does
  });

  it("offers no day outside the Services window in the date picker, and says what the window is", async () => {
    await toScope();
    expect(screen.getByLabelText("Row 1, Due date")).toHaveAttribute("min", "2026-11-01");
    expect(screen.getByLabelText("Row 1, Due date")).toHaveAttribute("max", "2027-03-31");
    expect(screen.getByText(/between 1 November 2026 and 31 March 2027 \(Services start to Services end\)/)).toBeInTheDocument();
  });
});

describe("adding and removing rows (AC-11)", () => {
  it("adds and removes rows in order, and the last row cannot be removed", async () => {
    await toScope();
    expect(screen.getByRole("button", { name: "Remove row 1" })).toBeDisabled();
    fireEvent.click(add());
    type("Row 2, Deliverable name", "Second");
    expect(screen.getByRole("button", { name: "Remove row 1" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Remove row 1" }));
    expect(screen.getByLabelText("Row 1, Deliverable name")).toHaveValue("Second");
    expect(screen.queryByLabelText("Row 2, Deliverable name")).toBeNull();
    expect(screen.getByRole("button", { name: "Remove row 1" })).toBeDisabled();
  });

  it("stops at twenty rows, says why, and offers a row again once one is removed", async () => {
    await toScope();
    for (let i = 1; i < 20; i++) fireEvent.click(add());
    expect(screen.getByLabelText("Row 20, Deliverable name")).toBeInTheDocument();
    expect(add()).toBeDisabled();
    expect(screen.getByText("A table holds at most 20 deliverables.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove row 20" }));
    expect(add()).toBeEnabled();
    expect(screen.queryByText("A table holds at most 20 deliverables.")).toBeNull();
  });
});

describe("a row that is not complete (AC-12, AC-20)", () => {
  it("is refused on Continue beside the cell and in the line above the form, naming the row and the column", async () => {
    await toScope();
    fillRow(1, "Report", "2026-12-01");
    type("Row 1, Their owner", "");
    next();
    expect(banner()).toHaveTextContent('"Deliverables", row 1: "Their owner" is required.');
    expect(screen.getByRole("heading", { name: "Scope and fees" })).toBeInTheDocument();
    const cell = screen.getByLabelText("Row 1, Their owner");
    expect(cell).toHaveAttribute("aria-invalid", "true");
    expect(document.getElementById(cell.getAttribute("aria-describedby")!)).toHaveTextContent('"Their owner" is required.');
  });

  it("lists every row and column at fault, and the list shrinks as they are fixed", async () => {
    await toScope();
    fillRow(1, "Report", "2026-12-01");
    fireEvent.click(add());
    type("Row 2, Deliverable name", "Second");
    next();
    expect(banner()).toHaveTextContent('"Deliverables", row 2: "Due date" is required.');
    expect(banner()).toHaveTextContent('"Deliverables", row 2: "Their owner" is required.');
    expect(banner()).toHaveTextContent('"Deliverables", row 2: "Our owner" is required.');
    type("Row 2, Due date", "2026-12-02");
    type("Row 2, Their owner", "Priya Nair");
    expect(banner()).not.toHaveTextContent("Due date");
    type("Row 2, Our owner", "Ravi Shah");
    expect(banner()).toBeNull();
    expect(screen.getByLabelText("Row 2, Our owner")).not.toHaveAttribute("aria-invalid");
  });

  it("refuses a table with no filled row, a due date outside the window, a repeated name and a blank bracket", async () => {
    await toScope();
    next();
    expect(banner()).toHaveTextContent('"Deliverables" needs at least 1 row.');
    fillRow(1, "Report [TBD]", "2026-10-31");
    next();
    expect(banner()).toHaveTextContent('"Deliverables", row 1: "Due date" must be between 1 November 2026 and 31 March 2027 (Services start to Services end).');
    expect(banner()).toHaveTextContent('"Deliverables", row 1: "Deliverable name" contains [TBD], which reads as a blank to fill in later. Reword it without square brackets.');
    fillRow(1, "Report", "2026-12-01");
    fireEvent.click(add());
    fillRow(2, "report ", "2026-12-02");
    next();
    expect(banner()).toHaveTextContent('"Deliverables", rows 1 and 2 have the same "Deliverable name" ("Report"). Give each row its own.');
  });

  it("ignores a row left entirely empty", async () => {
    await toScope();
    fillRow(1, "Report", "2026-12-01");
    fireEvent.click(add());
    next();
    expect(await screen.findByRole("heading", { name: "Risk checks and files" })).toBeInTheDocument();
  });
});

describe("a due date that has already passed (AC-15)", () => {
  it("shows a note naming the row, and does not stop the request", async () => {
    await toScope();
    type("Services start", "2020-01-01");
    type("Services end", "2030-12-31");
    fillRow(1, "Report", "2020-06-01");
    expect(screen.getByText("Row 1: this due date has already passed.")).toBeInTheDocument();
    next();
    expect(await screen.findByRole("heading", { name: "Risk checks and files" })).toBeInTheDocument();
    expect(banner()).toBeNull();
  });

  it("says nothing about a date in the future, or about one outside the window (that has its own error)", async () => {
    await toScope();
    type("Services start", "2020-01-01");
    type("Services end", "2030-12-31");
    fillRow(1, "Report", "2029-06-01");
    expect(screen.queryByText(/already passed/)).toBeNull();
    type("Services start", "2021-01-01");
    type("Row 1, Due date", "2020-06-01");
    expect(screen.queryByText(/already passed/)).toBeNull();
  });
});

describe("the Share column (AC-44, AC-45, AC-46)", () => {
  it("appears for Milestones, hides for any other pricing, and brings the typed shares back", async () => {
    await toScope("Milestones");
    expect(screen.getByRole("columnheader", { name: "Share of the fee (%)" })).toBeInTheDocument();
    fireEvent.click(add());
    fireEvent.click(add());
    fillRow(1, "One", "2026-12-01", undefined, "40");
    fillRow(2, "Two", "2026-12-02", undefined, "35");
    fillRow(3, "Three", "2026-12-03", undefined, "25");

    type("Pricing", "Fixed fee");
    expect(screen.queryByRole("columnheader", { name: "Share of the fee (%)" })).toBeNull();
    expect(screen.queryByLabelText("Row 1, Share of the fee (%)")).toBeNull();
    expect(screen.queryByText(/Total .*% of 100%/)).toBeNull();
    next();
    expect(await screen.findByRole("heading", { name: "Risk checks and files" })).toBeInTheDocument(); // no share is asked for

    fireEvent.click(screen.getByRole("button", { name: /Back/ }));
    type("Pricing", "Milestones");
    expect(screen.getByLabelText("Row 1, Share of the fee (%)")).toHaveValue("40");
    expect(screen.getByLabelText("Row 2, Share of the fee (%)")).toHaveValue("35");
    expect(screen.getByLabelText("Row 3, Share of the fee (%)")).toHaveValue("25");
  });

  it("refuses a row without a share once the pricing is Milestones, naming the row", async () => {
    await toScope("Milestones");
    fillRow(1, "One", "2026-12-01");
    next();
    expect(banner()).toHaveTextContent('"Deliverables", row 1: "Share of the fee (%)" is required.');
  });

  it("keeps a running total as shares are typed, and refuses 99 saying how far short it is", async () => {
    await toScope("Milestones");
    fireEvent.click(add());
    fireEvent.click(add());
    expect(screen.getByText("Total 0% of 100%")).toBeInTheDocument();
    fillRow(1, "One", "2026-12-01", undefined, "40");
    expect(screen.getByText("Total 40% of 100%")).toBeInTheDocument();
    fillRow(2, "Two", "2026-12-02", undefined, "35");
    fillRow(3, "Three", "2026-12-03", undefined, "24");
    expect(screen.getByText("Total 99% of 100%")).toBeInTheDocument();
    next();
    expect(banner()).toHaveTextContent('"Deliverables": the "Share of the fee (%)" column adds up to 99%, 1 short of 100%.');
    expect(screen.getByRole("heading", { name: "Scope and fees" })).toBeInTheDocument();

    type("Row 3, Share of the fee (%)", "26");
    expect(screen.getByText("Total 101% of 100%")).toBeInTheDocument();
    expect(banner()).toHaveTextContent("adds up to 101%, 1 over 100%.");

    type("Row 3, Share of the fee (%)", "25");
    expect(screen.getByText("Total 100% of 100%")).toBeInTheDocument();
    expect(banner()).toBeNull();
    next();
    expect(await screen.findByRole("heading", { name: "Risk checks and files" })).toBeInTheDocument();
  });

  it("refuses a share that is not a number with at most two decimal places, above 0 and at most 100", async () => {
    await toScope("Milestones");
    fillRow(1, "One", "2026-12-01", undefined, "33.333");
    next();
    expect(banner()).toHaveTextContent('"Deliverables", row 1: "Share of the fee (%)" must be a number above 0 and at most 100, with no more than two decimal places.');
    type("Row 1, Share of the fee (%)", "100");
    expect(banner()).toBeNull();
  });
});

describe("the review step (AC-21)", () => {
  it("shows the deliverables as rows, not as raw data, and says the Statement of Work template will be drafted", async () => {
    await reviewOf({ ...ANSWERS, deliverable_rows: ROWS });
    const table = screen.getByRole("table");
    expect(within(table).queryAllByRole("textbox")).toHaveLength(0); // read-only
    expect(within(table).queryAllByRole("button")).toHaveLength(0);
    const first = within(table).getByText("Migration plan").closest("tr")!;
    expect(first).toHaveTextContent(fmtDate("2026-11-30"));
    expect(first).toHaveTextContent("Priya Nair");
    expect(first).toHaveTextContent("Ravi Shah");
    expect(within(table).getByText("Tom Reyes")).toBeInTheDocument();
    expect(within(table).queryByRole("columnheader", { name: "Share of the fee (%)" })).toBeNull();
    expect(document.body).not.toHaveTextContent("[object Object]");
    expect(screen.getByText("Statement of Work template")).toBeInTheDocument();
  });

  it("shows each row's share when the pricing is Milestones, with no trailing zeros", async () => {
    const shares = ["40", "35.5", "24.5"];
    await reviewOf({ ...ANSWERS, pricing: "Milestones", deliverable_rows: ROWS.map((r, i) => ({ ...r, share: shares[i] })) });
    const table = screen.getByRole("table");
    expect(within(table).getByRole("columnheader", { name: "Share of the fee (%)" })).toBeInTheDocument();
    expect(within(table).getByText("Migration plan").closest("tr")).toHaveTextContent("40%");
    expect(within(table).getByText("Migrated warehouse").closest("tr")).toHaveTextContent("35.5%");
  });

  it("leaves out an empty row", async () => {
    await reviewOf({ ...ANSWERS, deliverable_rows: [ROWS[0], {}, ROWS[1]] });
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(3); // header + two
  });
});

describe("filing a Statement of Work (FR-16, FR-34, FR-43)", () => {
  const filed = async () => {
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    await waitFor(() => expect(h.intakeApi.create).toHaveBeenCalled());
    return h.intakeApi.create.mock.calls[0][0] as { field_values: Record<string, unknown> };
  };
  // Braces: a function returned from beforeEach is run as a clean-up, and a mock is a function.
  beforeEach(() => { h.intakeApi.create.mockResolvedValue({ id: "q-1", ref: "REQ-1" }); });

  it("sends the rows under deliverable_rows and never the old free-text key", async () => {
    const { onFiled } = await reviewOf({ ...ANSWERS, deliverables: OLD_TEXT, deliverable_rows: ROWS });
    const { field_values: fv } = await filed();
    expect(fv.deliverable_rows).toEqual(ROWS);
    expect("deliverables" in fv).toBe(false);
    expect(fv.request_form).toBe("sow");
    await waitFor(() => expect(onFiled).toHaveBeenCalledWith("q-1"));
  });

  it("sends no shares unless the pricing is Milestones, even though they were typed into the open form", async () => {
    await reviewOf({ ...ANSWERS, deliverable_rows: ROWS.map((r) => ({ ...r, share: "33.33" })) });
    expect((await filed()).field_values.deliverable_rows).toEqual(ROWS);
  });

  it("sends the shares when the pricing is Milestones", async () => {
    const shares = ["40", "35", "25"];
    await reviewOf({ ...ANSWERS, pricing: "Milestones", deliverable_rows: ROWS.map((r, i) => ({ ...r, share: shares[i] })) });
    expect((await filed()).field_values.deliverable_rows).toEqual(ROWS.map((r, i) => ({ ...r, share: shares[i] })));
  });

  it("keeps the numbering of the table when it sends an empty row, so a filing error names the row on the page", async () => {
    await reviewOf({ ...ANSWERS, deliverable_rows: [ROWS[0], {}, ROWS[1]] });
    expect((await filed()).field_values.deliverable_rows).toEqual([ROWS[0], { name: "", due: "", their_owner: "", our_owner: "" }, ROWS[1]]);
  });

  it("when filing fails, keeps every entry, shows the server's message, and files nothing", async () => {
    const message = '"Deliverables", row 2: "Due date" must be between 1 November 2026 and 31 March 2027 (Services start to Services end).';
    h.intakeApi.create.mockRejectedValue(new Error(message));
    const { onFiled } = await reviewOf({ ...ANSWERS, deliverable_rows: ROWS });
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(message, "error"));
    expect(onFiled).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Back/ }));
    await screen.findByRole("heading", { name: "Risk checks and files" });
    fireEvent.click(screen.getByRole("button", { name: /Back/ }));
    await screen.findByRole("heading", { name: "Scope and fees" });
    expect(screen.getByLabelText("Row 2, Deliverable name")).toHaveValue("Migrated warehouse");
    expect(screen.getByLabelText("Row 3, Their owner")).toHaveValue("Tom Reyes");
  });

  it("is refused at Submit when a draft's table is not valid, whichever step it was saved on", async () => {
    await reviewOf({ ...ANSWERS, deliverable_rows: [{ ...ROWS[0], due: "2027-04-01" }] });
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    expect(await screen.findByRole("heading", { name: "Scope and fees" })).toBeInTheDocument();
    expect(banner()).toHaveTextContent('"Deliverables", row 1: "Due date" must be between');
    expect(h.intakeApi.create).not.toHaveBeenCalled();
  });
});

describe("a saved form that holds the old free-text answer (FR-34, AC-37)", () => {
  it("shows the old text beside an empty table, and does not let the person on until the table is valid", async () => {
    mount("sow", draftAt(1, { ...ANSWERS, deliverables: OLD_TEXT }));
    await screen.findByRole("heading", { name: "Scope and fees" });
    expect(screen.getByText(OLD_TEXT)).toBeInTheDocument();
    expect(screen.getByText("Your earlier free-text answer (kept for reference, not filed)")).toBeInTheDocument();
    expect(screen.getByLabelText("Row 1, Deliverable name")).toHaveValue("");
    next();
    expect(banner()).toHaveTextContent('"Deliverables" needs at least 1 row.');
    expect(screen.getByRole("heading", { name: "Scope and fees" })).toBeInTheDocument();
    fillRow(1, "Migration plan", "2026-11-30");
    next();
    expect(await screen.findByRole("heading", { name: "Risk checks and files" })).toBeInTheDocument();
  });

  it("keeps the old text in the saved draft, so nothing is lost", async () => {
    h.intakeApi.updateDraft.mockResolvedValue({ id: "d-1", updated_at: "2026-10-02T00:00:00Z" });
    mount("sow", draftAt(1, { ...ANSWERS, deliverables: OLD_TEXT }));
    await screen.findByRole("heading", { name: "Scope and fees" });
    fillRow(1, "Migration plan", "2026-11-30");
    fireEvent.click(screen.getByRole("button", { name: "Save as draft" }));
    await waitFor(() => expect(h.intakeApi.updateDraft).toHaveBeenCalled());
    const saved = h.intakeApi.updateDraft.mock.calls[0][1] as { values: Record<string, unknown> };
    expect(saved.values.deliverables).toBe(OLD_TEXT);
    expect(saved.values.deliverable_rows).toEqual([{ name: "Migration plan", due: "2026-11-30", their_owner: "Priya Nair", our_owner: "Ravi Shah" }]);
  });
});

describe("saving a draft before leaving the first page", () => {
  it("sends visited of at least 1, which the server requires (a 0 was refused with a 422)", async () => {
    h.intakeApi.createDraft.mockResolvedValue({ id: "d-9", updated_at: "2026-10-26T00:00:00Z" });
    mount("sow");
    await screen.findByLabelText("Which master agreement is this under?");
    fireEvent.click(screen.getByRole("button", { name: "Save as draft" }));
    await waitFor(() => expect(h.intakeApi.createDraft).toHaveBeenCalled());
    expect((h.intakeApi.createDraft.mock.calls[0][0] as { visited: number }).visited).toBeGreaterThanOrEqual(1);
  });
});

describe("while the form's questions load, and when they cannot (FR-17, AC-20)", () => {
  it("shows no table and no questions until the form has loaded", async () => {
    let resolveForms: (v: unknown) => void = () => undefined;
    h.intakeApi.forms.mockReturnValue(new Promise((r) => { resolveForms = r; }));
    mount("sow");
    expect(screen.getByText("Loading the form…")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    resolveForms(serverForms);
    expect(await screen.findByLabelText("Which master agreement is this under?")).toBeInTheDocument();
  });

  it("shows the form's error state when it cannot be loaded, with Try again and a way back", async () => {
    h.intakeApi.forms.mockRejectedValueOnce(new Error("The server is busy"));
    const { onBack } = mount("sow");
    expect(await screen.findByText("Couldn't load the form.")).toBeInTheDocument();
    expect(screen.getByText("The server is busy")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText("This form isn't available.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "All request types" }));
    expect(onBack).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Which master agreement is this under?")).toBeInTheDocument();
    expect(h.intakeApi.forms).toHaveBeenCalledTimes(2);
  });
});

describe("the other single-field rules (FR-14, FR-15)", () => {
  it("refuses a blank bracket in the title and a total value of zero, beside the question", async () => {
    mount("sow", draftAt(1, { ...ANSWERS, sow_title: "Migration [TBD] phase", value: "0", deliverable_rows: ROWS }));
    await screen.findByRole("heading", { name: "Scope and fees" });
    next();
    expect(banner()).toHaveTextContent("SoW title: Contains [TBD], which reads as a blank to fill in later. Reword it without square brackets.");
    expect(banner()).toHaveTextContent("Total value of this SoW: Must be more than 0.");
    type("SoW title", "Migration (phase 1)");
    fireEvent.change(screen.getAllByLabelText("Total value of this SoW")[1], { target: { value: "1" } });
    expect(banner()).toBeNull();
  });
});

describe("the form definitions the wizard reads", () => {
  it("only the signer email is an email question, and every date-order rule points at a date question of the same form", () => {
    for (const form of serverForms as { key: string; fields: { key: string; kind: string; not_before?: string }[] }[]) {
      const kinds = Object.fromEntries(form.fields.map((f) => [f.key, f.kind]));
      for (const f of form.fields) {
        if (f.key.endsWith("_email")) expect(f.kind).toBe("email");
        if (f.not_before) expect([f.kind, kinds[f.not_before]]).toEqual(["date", "date"]);
      }
    }
    expect(within(document.body).queryByRole("alert")).toBeNull();
  });
});

// ---- Consultancy and DPA (008) -------------------------------------------------------------------------------
// The Consultancy form asks for its deliverables, fee, key individuals and company contact; the DPA asks for what
// kind of data and whose, the countries it goes to and the sub-processors. Each is checked by the rules the server
// applies when the request is filed (lib/form-checks.ts, pinned by one shared cases file).

const draftOf = (form: string, page: number, values: IntakeDraft["values"]): IntakeDraft => ({
  id: "d-2", form_key: form, title: null, values, parent_contract_id: form === "dpa" ? "c-1" : null, page_index: page, visited: page,
  created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
});

const ADVICE = "There are no separate deliverables (advice only)";
const CONSULT: IntakeDraft["values"] = {
  agreement_type: "Consultancy", entity: "Acme Ltd", entity_id: "e-1", counterparty: "Northwind Ltd", counterparty_id: "cp-1",
  cp_signer_name: "Maya Chen", cp_signer_email: "maya@example.com", paper: "Our template",
  needed_by: "2026-11-15", department: "Quality",
  value: "1800000", currency: "INR", start_date: "2026-11-01", term: "Fixed end date", end_date: "2027-04-30",
  scope: "Advise on the design of the new quality-management process.", payment_terms: "45 days",
  consultancy_deliverables: [{ name: "Current-state review", due: "2026-12-15" }, { name: "Process design report", due: "2027-03-15" }],
  fee_basis: "Fixed fee", key_individuals: [{ name: "Anita Rao" }, { name: "Dev Menon" }],
  company_contact_name: "Ravi Shah", company_contact_role: "Head of Quality", personal_data: "No", gxp: "No",
};
const CONTROLLER = "Controller: we decide how the data is used";
const PROCESSOR = "Processor: we act on their instructions";
const DPA: IntakeDraft["values"] = {
  parent_contract_id: "c-1", cp_signer_name: "Maya Chen", cp_signer_email: "maya@example.com", paper: "Our template",
  our_role: CONTROLLER, processing_purpose: "Hosting and support of the clinical-trial site portal.",
  data_types: ["Contact and identity", "Health or other special category"], data_subjects: ["Customers", "Patients or trial subjects"],
  transfer: "No", subprocessors: "No",
};

const NEW_QUESTIONS = ["How is the consultant paid?", "Company contact: name", "Company contact: role"];
const termsOf = async (over: IntakeDraft["values"] = {}) => {
  const handles = mount("new_agreement", draftOf("new_agreement", 1, { ...CONSULT, ...over }));
  await screen.findByRole("heading", { name: "Commercial terms" });
  return handles;
};
const dataOf = async (over: IntakeDraft["values"] = {}) => {
  const handles = mount("dpa", draftOf("dpa", 1, { ...DPA, ...over }));
  await screen.findByRole("heading", { name: "The data" });
  return handles;
};
const tableOf = (name: string) => screen.getByRole("table", { name });
const cellIn = (table: string, label: string) => within(tableOf(table)).getByLabelText(label);
const typeIn = (table: string, label: string, value: string) => fireEvent.change(cellIn(table, label), { target: { value } });

describe("the Consultancy questions show for Consultancy on our template only (AC-16)", () => {
  it("asks for the fee, the deliverables, the key individuals and the company contact", async () => {
    await termsOf();
    for (const label of NEW_QUESTIONS) expect(screen.getByLabelText(label)).toBeInTheDocument();
    expect(screen.getByLabelText(ADVICE)).not.toBeChecked();
    expect(tableOf("Deliverables")).toBeInTheDocument();
    expect(tableOf("Consultant's key individuals")).toBeInTheDocument();
    expect(screen.queryByLabelText("Rate per day or hour")).toBeNull(); // a fixed fee has no rate
    expect(screen.getByText(/Deliverables and timetable in the Engagement Schedule/)).toBeInTheDocument(); // what the answer does
  });

  it("asks for none of it on their paper", async () => {
    await termsOf({ paper: "Their paper" });
    for (const label of [...NEW_QUESTIONS, ADVICE, "Rate per day or hour"]) expect(screen.queryByLabelText(label)).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it.each(["Services (MSA)", "Buying from a vendor", "Software or SaaS", "Selling to a customer", "Something else"])("asks for none of it for %s", async (kind) => {
    await termsOf({ agreement_type: kind, hosting: "India", buying: "Goods" });
    for (const label of [...NEW_QUESTIONS, ADVICE]) expect(screen.queryByLabelText(label)).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("lets a complete step go on", async () => {
    await termsOf();
    next();
    expect(await screen.findByRole("heading", { name: "Risk checks and files" })).toBeInTheDocument();
    expect(banner()).toBeNull();
  });
});

describe("the Consultancy deliverables table (AC-17, AC-18)", () => {
  it("refuses an incomplete row beside the cell and in the line above the form, naming the row and the column", async () => {
    await termsOf({ consultancy_deliverables: [{ name: "Report", due: "" }] });
    next();
    expect(banner()).toHaveTextContent('"Deliverables", row 1: "Due date" is required.');
    expect(screen.getByRole("heading", { name: "Commercial terms" })).toBeInTheDocument();
    const cell = cellIn("Deliverables", "Row 1, Due date");
    expect(cell).toHaveAttribute("aria-invalid", "true");
    expect(document.getElementById(cell.getAttribute("aria-describedby")!.split(" ")[0])).toHaveTextContent('"Due date" is required.');
  });

  it("refuses a blank bracket, a repeated name and a date before the start, all listed", async () => {
    await termsOf({ end_date: "", term: "No end date (evergreen)" });
    typeIn("Deliverables", "Row 1, Deliverable name", "Report [TBD]");
    typeIn("Deliverables", "Row 1, Due date", "2026-10-31");
    typeIn("Deliverables", "Row 2, Deliverable name", "report");
    typeIn("Deliverables", "Row 2, Due date", "2026-12-01");
    fireEvent.click(within(tableOf("Deliverables")).getByRole("button", { name: "Remove row 2" }));
    fireEvent.click(screen.getByRole("button", { name: "Add a deliverable" }));
    typeIn("Deliverables", "Row 2, Deliverable name", "Report [TBD]");
    typeIn("Deliverables", "Row 2, Due date", "2026-12-01");
    next();
    expect(banner()).toHaveTextContent('"Deliverables", row 1: "Due date" must be on or after 1 November 2026 (Start date).');
    expect(banner()).toHaveTextContent('"Deliverables", row 1: "Deliverable name" contains [TBD], which reads as a blank to fill in later.');
    expect(banner()).toHaveTextContent('"Deliverables", rows 1 and 2 have the same "Deliverable name" ("Report [TBD]").');
  });

  it("stops at twenty rows and says why, then offers a row again once one is removed", async () => {
    await termsOf();
    const add = () => screen.getByRole("button", { name: "Add a deliverable" });
    for (let i = 2; i < 20; i++) fireEvent.click(add());
    expect(cellIn("Deliverables", "Row 20, Deliverable name")).toBeInTheDocument();
    expect(add()).toBeDisabled();
    expect(screen.getByText("A table holds at most 20 deliverables.")).toBeInTheDocument();
    fireEvent.click(within(tableOf("Deliverables")).getByRole("button", { name: "Remove row 20" }));
    expect(add()).toBeEnabled();
  });

  it("offers only the days from the start to the end, and says what they are", async () => {
    await termsOf();
    const due = cellIn("Deliverables", "Row 1, Due date");
    expect([due.getAttribute("min"), due.getAttribute("max")]).toEqual(["2026-11-01", "2027-04-30"]);
    expect(screen.getByText(/between 1 November 2026 and 30 April 2027 \(Start date to End date\)/)).toBeInTheDocument();
  });

  it("with no end date checks the start alone, and an end date typed earlier no longer counts", async () => {
    await termsOf({ consultancy_deliverables: [{ name: "Report", due: "2030-01-01" }] }); // beyond the typed end 2027-04-30
    next();
    expect(banner()).toHaveTextContent('"Deliverables", row 1: "Due date" must be between 1 November 2026 and 30 April 2027');
    type("Term", "No end date (evergreen)");
    expect(screen.queryByLabelText("End date")).toBeNull();
    expect(banner()).toBeNull();
    const due = cellIn("Deliverables", "Row 1, Due date");
    expect(due).toHaveAttribute("min", "2026-11-01");
    expect(due).not.toHaveAttribute("max");
    expect(screen.getByText("Due dates must be on or after 1 November 2026 (Start date).")).toBeInTheDocument();
    next();
    expect(await screen.findByRole("heading", { name: "Risk checks and files" })).toBeInTheDocument();
  });

  it("flags the row again when the end date is moved in front of it", async () => {
    await termsOf();
    next();
    expect(await screen.findByRole("heading", { name: "Risk checks and files" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Back/ }));
    await screen.findByRole("heading", { name: "Commercial terms" });
    type("End date", "2027-02-01");
    next();
    expect(banner()).toHaveTextContent('"Deliverables", row 2: "Due date" must be between 1 November 2026 and 1 February 2027 (Start date to End date).');
    expect(cellIn("Deliverables", "Row 2, Due date")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("heading", { name: "Commercial terms" })).toBeInTheDocument();
  });

  it("notes a due date that has already passed, and does not stop the request", async () => {
    await termsOf({ start_date: "2020-01-01", end_date: "2030-12-31", consultancy_deliverables: [{ name: "Report", due: "2020-06-01" }] });
    expect(screen.getByText("Row 1: this due date has already passed.")).toBeInTheDocument();
    next();
    expect(await screen.findByRole("heading", { name: "Risk checks and files" })).toBeInTheDocument();
  });
});

describe("advice only (AC-19)", () => {
  it("hides the deliverables table when ticked, brings the rows back when unticked, and does not need them", async () => {
    await termsOf({ consultancy_deliverables: [{ name: "Current-state review", due: "2026-12-15" }, { name: "", due: "" }] });
    fireEvent.click(screen.getByLabelText(ADVICE));
    expect(screen.queryByRole("table", { name: "Deliverables" })).toBeNull();
    fireEvent.click(screen.getByLabelText(ADVICE));
    expect(cellIn("Deliverables", "Row 1, Deliverable name")).toHaveValue("Current-state review");
    fireEvent.click(screen.getByLabelText(ADVICE));
    next();
    expect(await screen.findByRole("heading", { name: "Risk checks and files" })).toBeInTheDocument(); // no rows asked for
  });

  it("needs a table once it is not advice only", async () => {
    await termsOf({ consultancy_deliverables: [{}] });
    next();
    expect(banner()).toHaveTextContent('"Deliverables" needs at least 1 row.');
  });
});

describe("how the consultant is paid (AC-20)", () => {
  it("asks for a rate only for a day or hourly rate, and brings a typed rate back", async () => {
    await termsOf();
    type("How is the consultant paid?", "Day rate");
    type("Rate per day or hour", "25000");
    type("How is the consultant paid?", "Fixed fee");
    expect(screen.queryByLabelText("Rate per day or hour")).toBeNull();
    type("How is the consultant paid?", "Hourly rate");
    expect(screen.getByLabelText("Rate per day or hour")).toHaveValue("25000");
    expect(screen.getByText(/The day or hourly rate in the Fees entry/)).toBeInTheDocument();
  });

  it("will not go on without the rate for a rate basis, naming it", async () => {
    await termsOf({ fee_basis: "Day rate" });
    next();
    expect(banner()).toHaveTextContent("1 answer still needed: Rate per day or hour.");
    type("Rate per day or hour", "25000");
    expect(banner()).toBeNull();
  });

  it.each([
    ["25.001", "Rate per day or hour: Can have at most 2 decimal places."],
    ["3,500.125", "Rate per day or hour: Can have at most 2 decimal places."],
    ["1e3", "Rate per day or hour: Must be a number."],
    [".5", "Rate per day or hour: Must be a number."],
    ["5.", "Rate per day or hour: Must be a number."],
    ["+25", "Rate per day or hour: Must be a number."],
    ["２５０００", "Rate per day or hour: Must be a number."],
    ["0", "Rate per day or hour: Must be more than 0."],
    ["-5", "Rate per day or hour: Must be more than 0."],
  ])("refuses a rate of %s", async (rate, message) => {
    await termsOf({ fee_basis: "Day rate", fee_rate: rate });
    next();
    expect(banner()).toHaveTextContent(message);
    expect(screen.getByRole("heading", { name: "Commercial terms" })).toBeInTheDocument();
  });

  it.each(["25000", "25000.50", "1,500.25", " 25000 "])("accepts a rate of %j", async (rate) => {
    await termsOf({ fee_basis: "Day rate", fee_rate: rate });
    next();
    expect(await screen.findByRole("heading", { name: "Risk checks and files" })).toBeInTheDocument();
  });

  it("types a rate with the decimal keypad, not a number box that would drop a comma", async () => {
    await termsOf({ fee_basis: "Day rate" });
    const rate = screen.getByLabelText("Rate per day or hour");
    expect(rate).toHaveAttribute("type", "text");
    expect(rate).toHaveAttribute("inputmode", "decimal");
  });
});

describe("key individuals and the company contact (AC-22, AC-23, AC-24)", () => {
  it("needs at least one key individual, refuses a repeat and a blank bracket, and lists every problem", async () => {
    await termsOf({ key_individuals: [{}] });
    next();
    expect(banner()).toHaveTextContent('"Consultant\'s key individuals" needs at least 1 row.');
    typeIn("Consultant's key individuals", "Row 1, Name", "Anita Rao");
    fireEvent.click(screen.getByRole("button", { name: "Add a key individual" }));
    typeIn("Consultant's key individuals", "Row 2, Name", "anita  rao");
    next();
    expect(banner()).toHaveTextContent('"Consultant\'s key individuals", rows 1 and 2 have the same "Name" ("Anita Rao"). Give each row its own.');
    typeIn("Consultant's key individuals", "Row 2, Name", "[Name]");
    next();
    expect(banner()).toHaveTextContent('"Consultant\'s key individuals", row 2: "Name" contains [Name], which reads as a blank');
  });

  it("stops at ten key individuals and says why", async () => {
    await termsOf({ key_individuals: Array.from({ length: 10 }, (_, i) => ({ name: `Person ${i}` })) });
    expect(screen.getByRole("button", { name: "Add a key individual" })).toBeDisabled();
    expect(screen.getByText("A table holds at most 10 key individuals.")).toBeInTheDocument();
  });

  it("refuses a contact name or role of more than 120 characters, or in brackets, beside the question", async () => {
    await termsOf({ company_contact_name: "n".repeat(121), company_contact_role: "[Role]" });
    next();
    expect(banner()).toHaveTextContent("Company contact: name: Is longer than 120 characters.");
    expect(banner()).toHaveTextContent("Company contact: role: Contains [Role], which reads as a blank to fill in later.");
    type("Company contact: name", "n".repeat(120));
    type("Company contact: role", "Head of Quality (acting)");
    expect(banner()).toBeNull();
  });

  it("needs both halves of the company contact", async () => {
    await termsOf({ company_contact_name: "", company_contact_role: "" });
    next();
    expect(banner()).toHaveTextContent("2 answers still needed: Company contact: name, Company contact: role.");
  });
});

describe("the scope and the total value hold the new rules for Consultancy on our paper only (AC-21)", () => {
  it("refuses a blank bracket in the scope and a total value of zero", async () => {
    await termsOf({ scope: "Advise on [TBD] processes", value: "0" });
    next();
    expect(banner()).toHaveTextContent("Scope of services: Contains [TBD], which reads as a blank to fill in later.");
    expect(banner()).toHaveTextContent("Total contract value: Must be more than 0.");
  });

  it.each([["Services (MSA)", "Our template"], ["Consultancy", "Their paper"]])("keeps accepting both for %s on %s paper", async (kind, paper) => {
    await termsOf({ agreement_type: kind, paper, scope: "Advise on [TBD] processes", value: "0" });
    next();
    expect(await screen.findByRole("heading", { name: "Risk checks and files" })).toBeInTheDocument();
  });
});

describe("the review step for a Consultancy request (AC-27)", () => {
  beforeEach(() => {
    h.intakeApi.deleteDraft.mockResolvedValue(undefined);
    h.intakeApi.create.mockResolvedValue({ id: "q-1", ref: "REQ-1" });
  });
  const reviewConsult = async (over: IntakeDraft["values"] = {}) => {
    const handles = mount("new_agreement", draftOf("new_agreement", 3, { ...CONSULT, ...over }));
    await screen.findByRole("heading", { name: "Review and submit" });
    return handles;
  };
  const filed = async () => {
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    await waitFor(() => expect(h.intakeApi.create).toHaveBeenCalled());
    return h.intakeApi.create.mock.calls[0][0] as { field_values: Record<string, unknown> };
  };

  it("says the Consultancy Agreement template will be drafted, shows the deliverables as rows and the individuals as a list", async () => {
    await reviewConsult();
    expect(screen.getByText("Consultancy Agreement template")).toBeInTheDocument();
    const rows = within(tableOf("Deliverables")).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("D1");
    expect(rows[1]).toHaveTextContent("Current-state review");
    expect(rows[2]).toHaveTextContent(fmtDate("2027-03-15"));
    const list = screen.getByRole("list", { name: "Consultant's key individuals" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Anita Rao", "Dev Menon"]);
    expect(screen.queryByRole("table", { name: "Consultant's key individuals" })).toBeNull();
    expect(screen.getByText("Fixed fee")).toBeInTheDocument();
    expect(screen.getByText("Ravi Shah")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("[object Object]");
  });

  it("shows the rate with the currency of the total value", async () => {
    await reviewConsult({ fee_basis: "Day rate", fee_rate: "25000", currency: "USD" });
    const rate = screen.getByText("Rate per day or hour").closest("div")!;
    expect(rate).toHaveTextContent("USD 25,000");
  });

  it("shows advice only instead of a table", async () => {
    await reviewConsult({ advice_only: [ADVICE] });
    expect(screen.getByText(ADVICE)).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Deliverables" })).toBeNull();
  });

  it("files the answers as entered, with the rate only for a rate basis and no table for advice only", async () => {
    await reviewConsult({ fee_basis: "Day rate", fee_rate: "25000" });
    const day = (await filed()).field_values;
    expect(day).toMatchObject({ request_form: "new_agreement", fee_basis: "Day rate", fee_rate: "25000",
      key_individuals: [{ name: "Anita Rao" }, { name: "Dev Menon" }], company_contact_name: "Ravi Shah", company_contact_role: "Head of Quality" });
    expect(day.consultancy_deliverables).toEqual(CONSULT.consultancy_deliverables);
  });

  it("does not file a rate typed earlier once the fee is fixed, or rows once it is advice only", async () => {
    await reviewConsult({ fee_basis: "Fixed fee", fee_rate: "25000", advice_only: [ADVICE] });
    const fv = (await filed()).field_values;
    expect("fee_rate" in fv).toBe(false);
    expect("consultancy_deliverables" in fv).toBe(false);
    expect(fv.advice_only).toEqual([ADVICE]);
    expect(fv.fee_basis).toBe("Fixed fee");
  });

  it("when filing fails, keeps every entry, shows the server's message, and files nothing", async () => {
    const message = '"Deliverables", row 2: "Due date" must be between 1 November 2026 and 30 April 2027 (Start date to End date).';
    h.intakeApi.create.mockRejectedValue(new Error(message));
    const { onFiled } = await reviewConsult();
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(message, "error"));
    expect(onFiled).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Back/ }));
    await screen.findByRole("heading", { name: "Risk checks and files" });
    fireEvent.click(screen.getByRole("button", { name: /Back/ }));
    await screen.findByRole("heading", { name: "Commercial terms" });
    expect(cellIn("Deliverables", "Row 2, Deliverable name")).toHaveValue("Process design report");
    expect(cellIn("Consultant's key individuals", "Row 2, Name")).toHaveValue("Dev Menon");
  });

  it("is refused at Submit when a saved form's table is no longer valid", async () => {
    await reviewConsult({ company_contact_name: "[Name]" });
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    expect(await screen.findByRole("heading", { name: "Commercial terms" })).toBeInTheDocument();
    expect(banner()).toHaveTextContent("Company contact: name: Contains [Name]");
    expect(h.intakeApi.create).not.toHaveBeenCalled();
  });
});

describe("a Consultancy form saved before these questions existed (AC-50)", () => {
  it("shows the new questions empty and required, and does not let the person on", async () => {
    const {
      consultancy_deliverables: _a, fee_basis: _b, key_individuals: _c, company_contact_name: _d, company_contact_role: _e, ...older
    } = CONSULT;
    mount("new_agreement", draftOf("new_agreement", 1, older));
    await screen.findByRole("heading", { name: "Commercial terms" });
    expect(screen.getByLabelText("How is the consultant paid?")).toHaveValue("");
    expect(cellIn("Deliverables", "Row 1, Deliverable name")).toHaveValue("");
    expect(cellIn("Consultant's key individuals", "Row 1, Name")).toHaveValue("");
    next();
    expect(banner()).toHaveTextContent("3 answers still needed: How is the consultant paid?, Company contact: name, Company contact: role.");
    expect(banner()).toHaveTextContent('"Deliverables" needs at least 1 row.');
    expect(banner()).toHaveTextContent('"Consultant\'s key individuals" needs at least 1 row.');
    expect(screen.getByRole("heading", { name: "Commercial terms" })).toBeInTheDocument();
  });
});

// ---- The DPA ----------------------------------------------------------------------------------------------------

describe("what kind of data and whose (AC-28, AC-29)", () => {
  it("asks for both as ticks, with the options of the form", async () => {
    await dataOf({ data_types: [], data_subjects: [] });
    const types = screen.getByRole("group", { name: "What kind of personal data?" });
    expect(within(types).getAllByRole("checkbox").map((c) => c.getAttribute("aria-label") ?? c.parentElement!.textContent))
      .toEqual(["Contact and identity", "Employee / HR", "Health or other special category", "Financial"]);
    const subjects = screen.getByRole("group", { name: "Whose data?" });
    expect(within(subjects).getAllByRole("checkbox")).toHaveLength(4);
    expect(screen.getByText(/Types of personal data and the special-category entry in Annex 1 of the DPA/)).toBeInTheDocument();
  });

  it("refuses none ticked, naming both, and lets the person on once each has a tick", async () => {
    await dataOf({ data_types: [], data_subjects: [] });
    next();
    expect(banner()).toHaveTextContent("2 answers still needed: What kind of personal data?, Whose data?.");
    fireEvent.click(within(screen.getByRole("group", { name: "What kind of personal data?" })).getByLabelText("Financial"));
    expect(banner()).toHaveTextContent("1 answer still needed: Whose data?.");
    fireEvent.click(within(screen.getByRole("group", { name: "Whose data?" })).getByLabelText("Customers"));
    fireEvent.click(within(screen.getByRole("group", { name: "Whose data?" })).getByLabelText("Suppliers"));
    expect(banner()).toBeNull();
    next();
    expect(await screen.findByRole("heading", { name: "Files" })).toBeInTheDocument();
  });

  it("takes a tick back off, and needs one again when the last is gone", async () => {
    await dataOf({ data_subjects: ["Customers"] });
    next();
    expect(await screen.findByRole("heading", { name: "Files" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Back/ }));
    await screen.findByRole("heading", { name: "The data" });
    fireEvent.click(within(screen.getByRole("group", { name: "Whose data?" })).getByLabelText("Customers"));
    next();
    expect(banner()).toHaveTextContent("1 answer still needed: Whose data?.");
  });
});

describe("the countries and the sub-processors (AC-30 to AC-33)", () => {
  it("asks for neither while the answers are No, and for each once its answer is Yes", async () => {
    await dataOf();
    expect(screen.queryByRole("table")).toBeNull();
    type("Will data leave India?", "Yes");
    expect(tableOf("Countries outside India")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Sub-processors" })).toBeNull();
    type("Will they use sub-processors?", "Yes");
    expect(tableOf("Sub-processors")).toBeInTheDocument();
    expect(screen.getByText(/Processing locations in Annex 1 of the DPA, after India/)).toBeInTheDocument();
  });

  it("keeps the entries when the answer goes to No and back to Yes", async () => {
    await dataOf({ transfer: "Yes", subprocessors: "Yes",
      transfer_countries: [{ country: "Singapore" }, { country: "Germany" }],
      subprocessor_rows: [{ name: "CloudHost Pte Ltd", country: "Singapore", processing: "Hosting of the portal database" }] });
    type("Will data leave India?", "No");
    type("Will they use sub-processors?", "No");
    expect(screen.queryByRole("table")).toBeNull();
    type("Will data leave India?", "Yes");
    type("Will they use sub-processors?", "Yes");
    expect(cellIn("Countries outside India", "Row 2, Country")).toHaveValue("Germany");
    expect(cellIn("Sub-processors", "Row 1, Sub-processor name")).toHaveValue("CloudHost Pte Ltd");
  });

  it("needs at least one country and one sub-processor for a Yes", async () => {
    await dataOf({ transfer: "Yes", subprocessors: "Yes" });
    next();
    expect(banner()).toHaveTextContent('"Countries outside India" needs at least 1 row.');
    expect(banner()).toHaveTextContent('"Sub-processors" needs at least 1 row.');
  });

  it("refuses India in the countries list, a repeat and a blank bracket", async () => {
    await dataOf({ transfer: "Yes", transfer_countries: [{ country: "India" }, { country: "Singapore" }, { country: "singapore" }, { country: "[Country]" }] });
    next();
    expect(banner()).toHaveTextContent('"Countries outside India", row 1: "Country" is India. List only the countries outside India.');
    expect(banner()).toHaveTextContent('"Countries outside India", rows 2 and 3 have the same "Country" ("Singapore").');
    expect(banner()).toHaveTextContent('"Countries outside India", row 4: "Country" contains [Country]');
    expect(cellIn("Countries outside India", "Row 1, Country")).toHaveAttribute("aria-invalid", "true");
  });

  it("asks a sub-processor's country to be India when the data does not leave India, and says what to change", async () => {
    await dataOf({ transfer: "No", subprocessors: "Yes",
      subprocessor_rows: [{ name: "CloudHost Pte Ltd", country: "Singapore", processing: "Hosting" }] });
    next();
    expect(banner()).toHaveTextContent('"Sub-processors", row 1: "Country" is "Singapore", but the answer to "Will data leave India?" is No. Enter India, or change that answer.');
    typeIn("Sub-processors", "Row 1, Country", "India"); // either way out clears it
    expect(banner()).toBeNull();
    typeIn("Sub-processors", "Row 1, Country", "Singapore");
    expect(banner()).toHaveTextContent("Enter India, or change that answer.");
    type("Will data leave India?", "Yes");
    expect(banner()).toBeNull();
  });

  it("accepts a sub-processor in Singapore once the data does leave India, and one in India either way", async () => {
    await dataOf({ transfer: "Yes", subprocessors: "Yes",
      transfer_countries: [{ country: "Singapore" }],
      subprocessor_rows: [{ name: "CloudHost Pte Ltd", country: "Singapore", processing: "Hosting" }, { name: "Mumbai Hosting", country: " india ", processing: "Support" }] });
    next();
    expect(await screen.findByRole("heading", { name: "Files" })).toBeInTheDocument();
  });

  it("numbers the sub-processors 1, 2 on the review step and lists the countries", async () => {
    h.intakeApi.deleteDraft.mockResolvedValue(undefined);
    mount("dpa", draftOf("dpa", 3, { ...DPA, transfer: "Yes", subprocessors: "Yes", transfer_countries: [{ country: "Singapore" }, { country: "Germany" }],
      subprocessor_rows: [{ name: "CloudHost Pte Ltd", country: "Singapore", processing: "Hosting" }, { name: "SupportDesk GmbH", country: "Germany", processing: "Support" }] }));
    await screen.findByRole("heading", { name: "Review and submit" });
    const list = screen.getByRole("list", { name: "Countries outside India" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Singapore", "Germany"]);
    const rows = within(tableOf("Sub-processors")).getAllByRole("row");
    expect(rows[1].querySelector("td")!.textContent).toBe("1");
    expect(rows[2]).toHaveTextContent("SupportDesk GmbH");
    expect(screen.getByText("Contact and identity, Health or other special category")).toBeInTheDocument();
    expect(screen.getByText("DPA template")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("[object Object]");
  });
});

describe("the draft a DPA gets, said on the review step (AC-36)", () => {
  const reviewDpa = async (over: IntakeDraft["values"]) => {
    mount("dpa", draftOf("dpa", 3, { ...DPA, ...over }));
    await screen.findByRole("heading", { name: "Review and submit" });
  };
  it("is the DPA template when we are the Controller", async () => {
    await reviewDpa({});
    expect(screen.getByText("DPA template")).toBeInTheDocument();
  });
  it("says no draft is made from our template when we are the Processor, and to attach the other side's DPA", async () => {
    await reviewDpa({ our_role: PROCESSOR });
    expect(screen.getByText("No draft from our template: attach the other side's DPA")).toBeInTheDocument();
    expect(screen.queryByText("DPA template")).toBeNull();
  });
  it("is their draft on their paper, whatever the role", async () => {
    await reviewDpa({ our_role: PROCESSOR, paper: "Their paper" });
    expect(screen.getByText("Their draft becomes the contract; the AI review runs on it")).toBeInTheDocument();
  });
  it("no longer says the answers only fill a DPA annex: it names what each one does", async () => {
    await dataOf();
    expect(screen.getByText(/Controller: drafted from our DPA template\. Processor: no draft is made from it; attach the other side's DPA/)).toBeInTheDocument();
    expect(screen.getByText("Nature and purpose of the Processing in Annex 1 of the DPA.")).toBeInTheDocument();
  });
});

describe("a saved DPA form that holds the old 'Several' answers (AC-50)", () => {
  const OLD = { data_types: "Several of these", data_subjects: "Several" };

  it("opens with the ticks empty and the earlier answers beside them, labelled as not filed", async () => {
    await dataOf(OLD);
    for (const name of ["What kind of personal data?", "Whose data?"]) {
      for (const box of within(screen.getByRole("group", { name })).getAllByRole("checkbox")) expect(box).not.toBeChecked();
    }
    expect(screen.getByText("Several of these")).toBeInTheDocument();
    expect(screen.getByText("Several")).toBeInTheDocument();
    expect(screen.getAllByText("Your earlier answer (kept for reference, not filed)")).toHaveLength(2);
  });

  it("cannot be submitted until each has a tick, and keeps the earlier answer visible while the person ticks", async () => {
    await dataOf(OLD);
    next();
    expect(banner()).toHaveTextContent("2 answers still needed: What kind of personal data?, Whose data?.");
    fireEvent.click(within(screen.getByRole("group", { name: "Whose data?" })).getByLabelText("Customers"));
    expect(screen.getByText("Several")).toBeInTheDocument();
    expect(banner()).toHaveTextContent("1 answer still needed: What kind of personal data?.");
    fireEvent.click(within(screen.getByRole("group", { name: "What kind of personal data?" })).getByLabelText("Financial"));
    expect(banner()).toBeNull();
    next();
    expect(await screen.findByRole("heading", { name: "Files" })).toBeInTheDocument();
  });

  it("is refused at Submit when the form is saved on the review step with 'Several'", async () => {
    mount("dpa", draftOf("dpa", 3, { ...DPA, ...OLD }));
    await screen.findByRole("heading", { name: "Review and submit" });
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    expect(await screen.findByRole("heading", { name: "The data" })).toBeInTheDocument();
    expect(banner()).toHaveTextContent("What kind of personal data?");
    expect(h.intakeApi.create).not.toHaveBeenCalled();
  });

  it("files the ticks as a list", async () => {
    h.intakeApi.deleteDraft.mockResolvedValue(undefined);
    h.intakeApi.create.mockResolvedValue({ id: "q-2", ref: "REQ-2" });
    mount("dpa", draftOf("dpa", 3, DPA));
    await screen.findByRole("heading", { name: "Review and submit" });
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    await waitFor(() => expect(h.intakeApi.create).toHaveBeenCalled());
    const fv = (h.intakeApi.create.mock.calls[0][0] as { field_values: Record<string, unknown> }).field_values;
    expect(fv.data_types).toEqual(["Contact and identity", "Health or other special category"]);
    expect(fv.data_subjects).toEqual(["Customers", "Patients or trial subjects"]);
    expect("transfer_countries" in fv).toBe(false); // transfer is No, so the countries are not asked or filed
    expect("subprocessor_rows" in fv).toBe(false);
  });
});

describe("the DPA purpose on our paper (AC-34)", () => {
  it("refuses a blank bracket in the purpose, in the line above the form", async () => {
    await dataOf({ processing_purpose: "Hosting of [TBD] systems" });
    next();
    expect(banner()).toHaveTextContent("Why is the data processed?: Contains [TBD], which reads as a blank to fill in later.");
  });
  it("accepts round brackets in it", async () => {
    await dataOf({ processing_purpose: "Hosting of (phase 1) systems" });
    next();
    expect(await screen.findByRole("heading", { name: "Files" })).toBeInTheDocument();
  });
});

describe("while the Consultancy and DPA questions load, and when they cannot (AC-26)", () => {
  it.each(["new_agreement", "dpa"])("%s: shows no table and no question until the form has loaded, and an error with Try again when it cannot", async (key) => {
    h.intakeApi.forms.mockRejectedValueOnce(new Error("The server is busy"));
    mount(key, draftOf(key, 1, key === "dpa" ? DPA : CONSULT));
    expect(screen.getByText("Loading the form…")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(await screen.findByText("Couldn't load the form.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: key === "dpa" ? "The data" : "Commercial terms" })).toBeInTheDocument();
  });
});

// ---- Helpers for an NDA wizard ---------------------------------------------------------------------

const NDA_ANSWERS: IntakeDraft["values"] = { ...CONSULT, agreement_type: "NDA", nda_kind: "Mutual", nda_term: "2 years", governing_law: "India" };
const ndaWizard = async (page = 1, over: IntakeDraft["values"] = {}) => {
  const handles = mount("new_agreement", draftOf("new_agreement", page, { ...NDA_ANSWERS, ...over }));
  await screen.findByText("Step " + (page + 1) + " of 4", { exact: false });
  return handles;
};

// ---- The request in words (013) ------------------------------------------------------------------------------
// A live plain-language box in the aside, an editable copy on the review step, and extra instructions. Nothing
// about it may stop filing except being over the word limit.

describe("the request in words (FR-1, FR-2, FR-9 to FR-13, FR-20, FR-29, FR-32, FR-34 to FR-36, FR-38)", () => {
  const LAW = "It lasts 2 years and is under Indian law.";
  beforeEach(() => {
    h.intakeApi.deleteDraft.mockResolvedValue(undefined);
    h.intakeApi.create.mockResolvedValue({ id: "q-1", ref: "REQ-1" });
    h.intakeApi.updateDraft.mockResolvedValue({ id: "d-2", updated_at: "2026-10-02T00:00:00Z" });
  });
  const review = () => ndaWizard(3);
  // One free-text box for the whole summary: reword the law sentence inside it, then leave the box.
  const box = () => screen.getByRole("textbox", { name: "Your request in words" }) as HTMLTextAreaElement;
  const editLaw = (text: string) => {
    fireEvent.change(box(), { target: { value: box().value.replace(LAW, text) } });
    fireEvent.blur(box());
  };
  const file = async () => {
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    await waitFor(() => expect(h.intakeApi.create).toHaveBeenCalled());
    return h.intakeApi.create.mock.calls[0][0] as { summary: { text: string | null; instructions: unknown[]; accepted: unknown[] };
      field_values: Record<string, unknown> };
  };

  it("sits in the aside on the first step, above the routing, and follows the answers (FR-1, FR-10)", async () => {
    // Failure mode: the box missing on the first step, or going stale after an answer changes.
    await ndaWizard(0);
    const aside = document.querySelector("aside")!;
    const heads = Array.from(aside.children).map((c) => c.textContent ?? "");
    expect(heads[0]).toMatch(/^Your request in words/);
    expect(heads[1]).toMatch(/^What these answers do/);
    expect(heads[2]).toMatch(/^Where this request goes/);
    const box = within(screen.getByRole("region", { name: "Your request in words" }));
    expect(box.getByText("Needed by 15 Nov 2026.")).toBeInTheDocument();
    type("Needed by", "2026-12-01");
    expect(box.getByText("Needed by 1 Dec 2026.")).toBeInTheDocument();
    expect(box.queryByText("Needed by 15 Nov 2026.")).toBeNull();
  });

  it("has a link to the box for small screens, only where the box is in the aside (FR-10)", async () => {
    // Failure mode: on a phone the box sits far below the form with no way to reach it.
    await ndaWizard(0);
    expect(screen.getByRole("link", { name: "Read your request in words" })).toHaveAttribute("href", "#request-in-words");
  });

  it("on the review step, shows the editor AND the answers list, in the planned order (AC-6, FR-12, FR-38)", async () => {
    // Failure mode: the editor replacing the answer list, or the note to approvers landing before the editor.
    await review();
    const at = (el: HTMLElement) => el;
    const name = at(screen.getByLabelText("Name this request"));
    const editor = at(screen.getByRole("textbox", { name: "Your request in words" }));
    const answers = at(screen.getByText("Counterparty", { selector: "dt" }));
    const note = at(screen.getByLabelText("Note to approvers (optional)"));
    const extra = at(screen.getByRole("heading", { name: /Extra instructions for Legal/ }));
    const order = [name, editor, answers, note, extra];
    for (let i = 1; i < order.length; i++) {
      expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    expect(document.querySelector("aside")!.textContent).not.toMatch(/^Your request in words/m);
    expect(screen.queryByRole("region", { name: "Your request in words" })).toBeNull();
  });

  it("accepting an offer changes the answer the same way a control does: the routing is asked again (AC-13, FR-20)", async () => {
    // Failure mode: the answer changing silently on typing, or changing without routing and the answers list following.
    await review();
    editLaw("It lasts 2 years and is under English law.");
    const group = await screen.findByRole("group", { name: "Update the answer?" });
    expect(group).toHaveTextContent("Governing law: India → England and Wales");
    expect(screen.getByText("India", { selector: "dd" })).toBeInTheDocument(); // nothing changed yet
    fireEvent.click(within(group).getByRole("button", { name: "Update answer" }));
    await waitFor(() => expect(h.intakeApi.approvalPreview).toHaveBeenLastCalledWith(
      expect.objectContaining({ field_values: expect.objectContaining({ governing_law: "England and Wales" }) })));
    expect(screen.getByText("England and Wales", { selector: "dd" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Update the answer?" })).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("Governing law updated to England and Wales.");
    const body = await file();
    expect(body.field_values.governing_law).toBe("England and Wales");
    expect(body.summary.accepted).toEqual([{ key: "governing_law", label: "Governing law", old: "India", new: "England and Wales" }]);
    expect(body.summary.text).toContain("English");
  });

  it("files with the sentences as text, and a pending instruction it was never asked to add (FR-34)", async () => {
    // Failure mode: text typed in 'Anything else' and not yet added being dropped at Submit.
    await review();
    fireEvent.change(screen.getByLabelText("Anything else for Legal?"), { target: { value: "Send a copy to procurement" } });
    const body = await file();
    expect(body.summary.text).toContain("This is a mutual NDA between Acme Ltd and Northwind Ltd, on our template.");
    expect(body.summary.text).not.toContain("not given yet");
    expect(body.summary.instructions).toEqual([{ text: "Send a copy to procurement", differs: [] }]);
    expect(body.summary.accepted).toEqual([]);
  });

  it("Submit with an offer open files nothing, says so, and focuses the offer; once answered it files (AC-24, FR-34)", async () => {
    // Failure mode: an unanswered offer silently becoming an instruction, or Submit filing past a change the person never settled.
    await review();
    editLaw("It lasts 2 years and is under English law.");
    const group = await screen.findByRole("group", { name: "Update the answer?" });
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    expect(await screen.findByText("You have 1 change waiting — update or keep each one, then submit.")).toBeInTheDocument();
    expect(h.intakeApi.create).not.toHaveBeenCalled();
    await waitFor(() => expect(within(group).getByRole("button", { name: "Update answer" })).toHaveFocus());
    fireEvent.click(within(group).getByRole("button", { name: "Keep my answer" }));
    const body = await file();
    expect(body.field_values.governing_law).toBe("India");
    expect(body.summary.instructions).toEqual([{
      text: "It lasts 2 years and is under English law.",
      differs: [expect.objectContaining({ key: "governing_law", answer: "India" })],
    }]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("a summary that cannot be built still files, with text null (AC-2, FR-9)", async () => {
    // Failure mode: a bug in the wording rules turning every request into one that cannot be filed.
    boom.on = true;
    await review();
    expect(screen.queryByRole("textbox", { name: "Your request in words" })).toBeNull();
    const body = await file();
    expect(body.summary.text).toBeNull();
    expect(body.field_values.request_form).toBe("new_agreement");
  });

  it("over the word limit is refused with the way out, nothing is cut and nothing is sent (AC-22, FR-32)", async () => {
    // Failure mode: a long instruction being silently truncated, or sent to be refused by the server only.
    await review();
    const long = Array.from({ length: 2001 }, (_, i) => `w${i}`).join(" ");
    fireEvent.change(screen.getByLabelText("Anything else for Legal?"), { target: { value: long } });
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    expect(await screen.findByText(/over the limit of about 2,000/)).toHaveTextContent(
      "Your extra instructions are 2,001 words, over the limit of about 2,000. Shorten them, or attach them as a file under Supporting documents on the “Risk checks and files” step.");
    expect(h.intakeApi.create).not.toHaveBeenCalled();
    expect(screen.getByRole("list", { name: "Extra instructions for Legal" })).toHaveTextContent(long);
  });

  // Forms with no file question: the way out is shortening only. Restored drafts carry the over-long instruction.
  const LONG = { text: Array.from({ length: 2001 }, (_, i) => `w${i}`).join(" "), differs: [] };
  const overLimit = async (form: string, page: number, values: IntakeDraft["values"]) => {
    mount(form, { ...draftOf(form, page, values), summary_box: { ...EMPTY_BOX, instructions: [LONG] } });
    if (form === "regularize") { // the signed agreement is required and a draft cannot hold a file: add it on step 1, then go to Review
      await screen.findByText("Step 1 of 4", { exact: false });
      const input = document.querySelector<HTMLInputElement>('input[type="file"]')!;
      fireEvent.change(input, { target: { files: [new File(["x"], "signed.pdf", { type: "application/pdf" })] } });
      fireEvent.click(screen.getByRole("button", { name: /Review and submit/ }));
    }
    fireEvent.click(await screen.findByRole("button", { name: "Submit request" }));
    return screen.findByText(/over the limit of about 2,000/);
  };

  it("Signed outside the system over the limit says to shorten, never to attach (DEC-2)", async () => {
    // Failure mode: telling the person to attach a file on a form that has no place to attach one.
    const note = await overLimit("regularize", 0, { entity: "Acme India Pvt Ltd", entity_id: "e-1", counterparty: "Northwind Ltd", counterparty_id: "cp-1",
      date_signed: "2026-09-01", value: "100000", currency: "INR", start_date: "2026-09-01", term: "No end date (evergreen)",
      why_outside: "Urgency", prior_approval: "Yes, in full", signed_by_us: "Yes" });
    expect(note).toHaveTextContent("over the limit of about 2,000. Shorten them — this form has no place to attach a file.");
    expect(note).not.toHaveTextContent("Shorten them, or shorten them");
    expect(note).not.toHaveTextContent("Supporting documents");
    expect(h.intakeApi.create).not.toHaveBeenCalled();
  });

  it("Cancel a request over the limit says to shorten, never to attach (DEC-2)", async () => {
    // Failure mode: the attach-a-file advice shown on the other form with no file question.
    const note = await overLimit("cancellation", 2, { cancel_request_ref: "REQ-1", cancel_reason: "No longer needed", tell_cp: "No" });
    expect(note).toHaveTextContent("over the limit of about 2,000. Shorten them — this form has no place to attach a file.");
    expect(note).not.toHaveTextContent("Shorten them, or shorten them");
    expect(note).not.toHaveTextContent("Supporting documents");
    expect(h.intakeApi.create).not.toHaveBeenCalled();
  });

  it("exactly at the limit files (AC-22)", async () => {
    // Failure mode: an off-by-one refusing the largest allowed instruction.
    await review();
    fireEvent.change(screen.getByLabelText("Anything else for Legal?"), { target: { value: Array.from({ length: 2000 }, () => "w").join(" ") } });
    await file();
  });

  it("Save as draft sends the box, and resuming shows an instruction and an open offer (AC-25, FR-36)", async () => {
    // Failure mode: edits, offers and instructions lost when the person leaves and comes back.
    await review();
    editLaw("It lasts 2 years and is under English law.");
    await screen.findByRole("group", { name: "Update the answer?" });
    fireEvent.click(screen.getByRole("button", { name: "Save as draft" }));
    await waitFor(() => expect(h.intakeApi.updateDraft).toHaveBeenCalled());
    const saved = h.intakeApi.updateDraft.mock.calls[0][1].summary_box;
    expect(saved.offers).toHaveLength(1);
    expect(saved.edits).toBeDefined();
    document.body.innerHTML = "";
    mount("new_agreement", { ...draftOf("new_agreement", 3, NDA_ANSWERS),
      summary_box: { ...saved, instructions: [{ text: "Send a copy to procurement", differs: [] }] } });
    await screen.findByRole("heading", { name: "Review and submit" });
    expect(await screen.findByRole("group", { name: "Update the answer?" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Extra instructions for Legal" })).toHaveTextContent("Send a copy to procurement");
    // Failure mode: a resumed draft showing the generated text and hiding what the person had typed.
    expect(box().value).toContain("English law");
  });

  it("a resumed draft whose answer has since changed does not offer the old change (FR-36, FR-23)", async () => {
    // Failure mode: an offer from last week updating an answer that is now something else.
    await review();
    editLaw("It lasts 2 years and is under English law.");
    await screen.findByRole("group", { name: "Update the answer?" });
    fireEvent.click(screen.getByRole("button", { name: "Save as draft" }));
    await waitFor(() => expect(h.intakeApi.updateDraft).toHaveBeenCalled());
    const saved = h.intakeApi.updateDraft.mock.calls[0][1].summary_box;
    document.body.innerHTML = "";
    mount("new_agreement", { ...draftOf("new_agreement", 3, { ...NDA_ANSWERS, governing_law: "Singapore" }), summary_box: saved });
    await screen.findByRole("heading", { name: "Review and submit" });
    await waitFor(() => expect(screen.queryByRole("group", { name: "Update the answer?" })).toBeNull());
    expect(screen.getByRole("list", { name: "Extra instructions for Legal" })).toHaveTextContent("English law");
  });

  it("the note to approvers stays an answer and never becomes an instruction (FR-38)", async () => {
    // Failure mode: the note being copied into the extra instructions, or filed twice.
    await review();
    fireEvent.change(screen.getByLabelText("Note to approvers (optional)"), { target: { value: "Two lines beat a paragraph." } });
    const body = await file();
    expect(body.field_values.note_approvers).toBe("Two lines beat a paragraph.");
    expect(body.summary.instructions).toEqual([]);
    expect(body.summary.text).toContain("Note to approvers: Two lines beat a paragraph.");
  });

  it("Add moves the typed line into the list and Remove takes it out (FR-29)", async () => {
    // Failure mode: an instruction that cannot be taken back before filing.
    await review();
    fireEvent.change(screen.getByLabelText("Anything else for Legal?"), { target: { value: "Cc procurement" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByRole("list", { name: "Extra instructions for Legal" })).toHaveTextContent("Cc procurement");
    expect(screen.getByLabelText("Anything else for Legal?")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "Remove instruction 1" }));
    expect(screen.queryByRole("list", { name: "Extra instructions for Legal" })).toBeNull();
  });

  it("the box shows the number of kept instructions on the other steps (FR-10)", async () => {
    // Failure mode: instructions written on the review step being forgotten when the person goes back.
    await review();
    fireEvent.change(screen.getByLabelText("Anything else for Legal?"), { target: { value: "Cc procurement" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.click(screen.getByRole("button", { name: /Back/ }));
    expect(await screen.findByText("1 extra instruction for Legal — see the Review step.")).toBeInTheDocument();
  });
});

describe("payment options follow the agreement type (FR-3)", () => {
  const field = {
    key: "payment_terms", label: "Payment terms", kind: "select", required: true,
    options: ["30 days", "45 days", "60 days", "90 days"],
    options_when: [
      { when: [{ field: "agreement_type", in: ["Services (MSA)"] }], options: ["45 days", "60 days"] },
      { when: [{ field: "agreement_type", in: ["Buying from a vendor"] }], options: ["45 days", "60 days", "90 days"] },
    ],
  } as unknown as Parameters<typeof Question>[0]["field"];
  const choices = (type: string) => {
    const { unmount } = render(
      <Question k="payment_terms" label="Payment terms" req field={field}
        values={{ agreement_type: type }} labels={{}} set={() => undefined} />,
    );
    const got = within(screen.getByRole("combobox", { name: "Payment terms" })).getAllByRole("option").map((o) => o.textContent);
    unmount();
    return got;
  };

  // Failure mode: the select lists every period, so a person picks "30 days" for an MSA and the playbook rejects it.
  it("offers 45/60 for an MSA and 45/60/90 for a vendor", () => {
    expect(choices("Services (MSA)")).toEqual(["Select…", "45 days", "60 days"]);
    expect(choices("Buying from a vendor")).toEqual(["Select…", "45 days", "60 days", "90 days"]);
  });
});
