import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AGREEMENT_FORMS, AgreementWizard } from "./_agreement-forms";
import { fmtDate } from "@/lib/utils";
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
