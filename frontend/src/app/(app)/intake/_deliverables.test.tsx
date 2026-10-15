import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RequestDeliverables, RowsTable } from "./_deliverables";
import { rowsProblems } from "@/lib/form-checks";
import { fmtDate } from "@/lib/utils";
import type { FormRow, IntakeRequest, RequestFormDef, RequestFormField } from "@/lib/types";

// The deliverables table on its own (labelled cells, add and remove, the cap, errors tied to their cell, the
// running share total, the past-date note, the read-only form), and the block the request page shows for it:
// stored rows, an older request's free text, and the editor that lets an older request get its rows.

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  intakeApi: { forms: vi.fn(), update: vi.fn() },
  workflowsApi: { refreshRun: vi.fn() },
}));
vi.mock("@/lib/endpoints", () => ({ intakeApi: h.intakeApi, workflowsApi: h.workflowsApi }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const forms: RequestFormDef[] = JSON.parse(
  readFileSync(resolve(__dirname, "../../../../../backend/app/intake/agreement_forms.json"), "utf8"),
);
const sow = forms.find((f) => f.key === "sow")!;
const table = sow.fields.find((f) => f.key === "deliverable_rows") as RequestFormField;
const labels = Object.fromEntries(sow.fields.map((f) => [f.key, f.label]));
const window_ = { services_start: "2026-11-01", services_end: "2027-03-31" };

const ROWS: FormRow[] = [
  { name: "Migration plan", due: "2026-11-30", their_owner: "Priya Nair", our_owner: "Ravi Shah" },
  { name: "Migrated warehouse", due: "2027-02-15", their_owner: "Priya Nair", our_owner: "Ravi Shah" },
  { name: "Handover pack", due: "2027-03-31", their_owner: "Tom Reyes", our_owner: "Ravi Shah" },
];

/** The table with its rows in state, the way a form holds them. */
function Editor({ initial = [{}] as FormRow[], values = { ...window_, pricing: "Fixed fee" } as Record<string, unknown>,
  problems = [] as ReturnType<typeof rowsProblems>, today }: {
  initial?: FormRow[]; values?: Record<string, unknown>; problems?: ReturnType<typeof rowsProblems>; today?: string;
}) {
  const [rows, setRows] = useState(initial);
  return <RowsTable field={table} rows={rows} values={values} labels={labels} onChange={setRows} problems={problems} today={today} />;
}
const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

describe("RowsTable as an editor", () => {
  it("labels every cell with its row and column, and heads every column", () => {
    render(<Editor initial={ROWS} />);
    for (const n of [1, 2, 3]) for (const c of ["Deliverable name", "Due date", "Their owner", "Our owner"]) {
      expect(screen.getByLabelText(`Row ${n}, ${c}`)).toBeInTheDocument();
    }
    expect(screen.getByLabelText("Row 2, Deliverable name")).toHaveValue("Migrated warehouse");
    const heads = screen.getAllByRole("columnheader");
    expect(heads.every((th) => th.getAttribute("scope") === "col")).toBe(true);
    expect(screen.getByRole("table", { name: "Deliverables" })).toBeInTheDocument();
  });

  it("is reachable and operable by keyboard: every control is a real, focusable button or field with a name", () => {
    render(<Editor initial={ROWS.slice(0, 2)} />);
    const controls = [
      ...screen.getAllByRole("textbox"),
      screen.getByRole("button", { name: "Add a deliverable" }),
      screen.getByRole("button", { name: "Remove row 1" }),
      screen.getByRole("button", { name: "Remove row 2" }),
    ];
    for (const el of controls) {
      expect(el.getAttribute("tabindex")).not.toBe("-1");
      el.focus();
      expect(document.activeElement).toBe(el);
    }
    // Date cells are native date inputs (no role=textbox), reachable the same way.
    const due = screen.getByLabelText("Row 1, Due date");
    due.focus();
    expect(document.activeElement).toBe(due);
  });

  it("adds a row at the end and removes the one asked for, keeping the others in order", () => {
    render(<Editor initial={ROWS.slice(0, 2)} />);
    fireEvent.click(screen.getByRole("button", { name: "Add a deliverable" }));
    expect(screen.getByLabelText("Row 3, Deliverable name")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "Remove row 1" }));
    expect(screen.getByLabelText("Row 1, Deliverable name")).toHaveValue("Migrated warehouse");
    expect(screen.queryByLabelText("Row 3, Deliverable name")).toBeNull();
  });

  it("will not remove the last row", () => {
    render(<Editor initial={[{}]} />);
    expect(screen.getByRole("button", { name: "Remove row 1" })).toBeDisabled();
  });

  it("will not add past the cap, and says why", () => {
    render(<Editor initial={Array.from({ length: 20 }, () => ({}))} />);
    const addBtn = screen.getByRole("button", { name: "Add a deliverable" });
    expect(addBtn).toBeDisabled();
    expect(screen.getByText("A table holds at most 20 deliverables.")).toBeInTheDocument();
    expect(addBtn).toHaveAccessibleDescription("A table holds at most 20 deliverables.");
  });

  it("ties an error to its own cell: invalid, and described by the message under it", () => {
    const values = { ...window_, pricing: "Fixed fee" };
    const rows = [{ ...ROWS[0], their_owner: "" }, ROWS[1]];
    render(<Editor initial={rows} values={values} problems={rowsProblems(table, rows, values, labels)} />);
    const cell = screen.getByLabelText("Row 1, Their owner");
    expect(cell).toHaveAttribute("aria-invalid", "true");
    const message = document.getElementById(cell.getAttribute("aria-describedby")!);
    expect(message).toHaveTextContent('"Their owner" is required.');
    expect(message).not.toHaveTextContent("row 1"); // the row is where the person already is
    expect(screen.getByLabelText("Row 1, Our owner")).not.toHaveAttribute("aria-invalid");
    expect(screen.getByLabelText("Row 2, Their owner")).not.toHaveAttribute("aria-invalid");
  });

  it("shows a table-wide problem under the table, not beside any cell", () => {
    const values = { ...window_, pricing: "Milestones" };
    const rows = ROWS.map((r, i) => ({ ...r, share: ["40", "35", "24"][i] }));
    render(<Editor initial={rows} values={values} problems={rowsProblems(table, rows, values, labels)} />);
    expect(screen.getByText('"Deliverables": the "Share of the fee (%)" column adds up to 99%, 1 short of 100%.')).toBeInTheDocument();
    expect(screen.getAllByRole("textbox").filter((el) => el.hasAttribute("aria-invalid"))).toHaveLength(0);
  });

  it("limits the date picker to the Services window and says what the window is", () => {
    render(<Editor initial={ROWS.slice(0, 1)} />);
    const due = screen.getByLabelText("Row 1, Due date");
    expect(due).toHaveAttribute("type", "date");
    expect(due).toHaveAttribute("min", "2026-11-01");
    expect(due).toHaveAttribute("max", "2027-03-31");
    expect(due).toHaveAccessibleDescription(/between 1 November 2026 and 31 March 2027 \(Services start to Services end\)/);
  });

  it("has no window to offer, and no hint, while the Services dates are not both given", () => {
    render(<Editor initial={ROWS.slice(0, 1)} values={{ services_start: "2026-11-01" }} />);
    expect(screen.getByLabelText("Row 1, Due date")).not.toHaveAttribute("max");
    expect(screen.queryByText(/must fall between/)).toBeNull();
  });
});

describe("RowsTable: the Share column", () => {
  const withShares = ROWS.map((r, i) => ({ ...r, share: ["40", "35", "25"][i] }));

  it("is shown for Milestones with a decimal keypad, and a running total that updates as shares are typed", () => {
    render(<Editor initial={withShares.slice(0, 2)} values={{ ...window_, pricing: "Milestones" }} />);
    const share = screen.getByLabelText("Row 1, Share of the fee (%)");
    expect(share).toHaveAttribute("inputmode", "decimal");
    expect(screen.getByText("Total 75% of 100%")).toBeInTheDocument();
    type("Row 2, Share of the fee (%)", "60");
    expect(screen.getByText("Total 100% of 100%")).toBeInTheDocument();
    type("Row 2, Share of the fee (%)", "12.5");
    expect(screen.getByText("Total 52.5% of 100%")).toBeInTheDocument();
  });

  it("is hidden for any other pricing, with its total, and the typed shares are still there when it comes back", () => {
    const { rerender } = render(<RowsTable field={table} rows={withShares} values={{ ...window_, pricing: "Fixed fee" }} labels={labels} onChange={() => undefined} />);
    expect(screen.queryByRole("columnheader", { name: "Share of the fee (%)" })).toBeNull();
    expect(screen.queryByLabelText("Row 1, Share of the fee (%)")).toBeNull();
    expect(screen.queryByText(/Total .*% of 100%/)).toBeNull();
    rerender(<RowsTable field={table} rows={withShares} values={{ ...window_, pricing: "Milestones" }} labels={labels} onChange={() => undefined} />);
    expect(screen.getByLabelText("Row 3, Share of the fee (%)")).toHaveValue("25");
  });

  it("changes a cell without losing the hidden share of its row", () => {
    const onChange = vi.fn();
    render(<RowsTable field={table} rows={withShares} values={{ ...window_, pricing: "Fixed fee" }} labels={labels} onChange={onChange} />);
    type("Row 1, Deliverable name", "Renamed");
    expect(onChange).toHaveBeenCalledWith([{ ...withShares[0], name: "Renamed" }, withShares[1], withShares[2]]);
  });
});

describe("RowsTable: a due date that has already passed (FR-12)", () => {
  const values = { services_start: "2026-01-01", services_end: "2027-12-31", pricing: "Fixed fee" };
  const rows = [{ ...ROWS[0], due: "2026-02-01" }, { ...ROWS[1], due: "2026-06-30" }, { ...ROWS[2], due: "2027-03-31" }];

  it("names each row whose date is before today, as a note and not as an error", () => {
    render(<Editor initial={rows} values={values} today="2026-06-30" />);
    expect(screen.getByText("Row 1: this due date has already passed.")).toBeInTheDocument();
    expect(screen.queryByText("Row 2: this due date has already passed.")).toBeNull(); // today is not passed
    expect(screen.queryByText("Row 3: this due date has already passed.")).toBeNull();
    expect(screen.getByLabelText("Row 1, Due date")).not.toHaveAttribute("aria-invalid");
  });

  it("does not add the note to a date that is outside the window and has its own error", () => {
    render(<Editor initial={[{ ...ROWS[0], due: "2025-12-31" }]} values={values} today="2026-06-30" />);
    expect(screen.queryByText(/already passed/)).toBeNull();
  });
});

describe("RowsTable read-only", () => {
  it("shows the rows as text with D-numbers and the date the way the app writes dates: no fields, no buttons", () => {
    render(<RowsTable field={table} rows={ROWS} values={{ ...window_, pricing: "Fixed fee" }} labels={labels} />);
    const t = screen.getByRole("table");
    expect(within(t).queryAllByRole("textbox")).toHaveLength(0);
    expect(within(t).queryAllByRole("button")).toHaveLength(0);
    const rows = within(t).getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(rows[1]).toHaveTextContent("D1");
    expect(rows[1]).toHaveTextContent("Migration plan");
    expect(rows[1]).toHaveTextContent(fmtDate("2026-11-30"));
    expect(rows[1]).toHaveTextContent("Priya Nair");
    expect(rows[3]).toHaveTextContent("D3");
    expect(rows[3]).toHaveTextContent("Tom Reyes");
    expect(within(t).queryByRole("columnheader", { name: "Share of the fee (%)" })).toBeNull();
  });

  it("shows a stored share without its trailing zeros, for Milestones only, and no running total", () => {
    const shares = ["40.00", "35.50", "24.50"];
    render(<RowsTable field={table} rows={ROWS.map((r, i) => ({ ...r, share: shares[i] }))}
      values={{ ...window_, pricing: "Milestones" }} labels={labels} />);
    const rows = screen.getAllByRole("row");
    expect(rows[1]).toHaveTextContent("40%");
    expect(rows[2]).toHaveTextContent("35.5%");
    expect(rows[3]).toHaveTextContent("24.5%");
    expect(screen.queryByText(/Total .*% of 100%/)).toBeNull();
  });
});

// ------------------------------------------------------------ the request page ----

const request = (fv: Record<string, unknown>): IntakeRequest => ({
  id: "q-1", ref: "REQ-1", source: "form", requester_user_id: "u-9", requester_name: "Asha", department: "Procurement",
  type_label: "Statement of Work Request", subject: "SoW", description: "SoW", contract_id: null,
  field_values: { request_form: "sow", pricing: "Fixed fee", ...window_, ...fv },
} as unknown as IntakeRequest);
const OLD_TEXT = "Plan by Nov, report by Mar, Ravi signs off";

function mount(r: IntakeRequest, over: { canEdit?: boolean; heldNote?: string | null; heldRunId?: string | null } = {}) {
  const onSaved = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <RequestDeliverables request={r} canEdit={over.canEdit ?? false} heldNote={over.heldNote} heldRunId={over.heldRunId} onSaved={onSaved} />
    </QueryClientProvider>,
  );
  return { onSaved, qc, ...view };
}
const rowsIn = (fv: FormRow[]) => ({ deliverable_rows: fv });
const loaded = () => screen.findByRole("heading", { name: "Deliverables" });

beforeEach(() => {
  vi.resetAllMocks();
  h.intakeApi.forms.mockResolvedValue(forms);
});

describe("RequestDeliverables: a request with rows", () => {
  it("shows them as rows with D-numbers, and no editor even for someone who may edit", async () => {
    mount(request(rowsIn(ROWS)), { canEdit: true });
    await loaded();
    const t = screen.getByRole("table");
    expect(within(t).getAllByRole("row")).toHaveLength(4);
    expect(within(t).getByText("Handover pack").closest("tr")).toHaveTextContent("D3");
    expect(screen.queryByRole("button", { name: "Save deliverables" })).toBeNull();
    expect(screen.queryByText("Earlier free-text answer")).toBeNull();
  });

  it("shows each row's share when the pricing is Milestones", async () => {
    mount(request({ pricing: "Milestones", ...rowsIn(ROWS.map((r, i) => ({ ...r, share: ["40.00", "35.00", "25.00"][i] }))) }));
    await loaded();
    expect(screen.getByText("Migration plan").closest("tr")).toHaveTextContent("40%");
  });

  it("shows the earlier free text beside the rows, when a request has both", async () => {
    mount(request({ ...rowsIn(ROWS), deliverables: OLD_TEXT }));
    await loaded();
    expect(screen.getByText(OLD_TEXT)).toBeInTheDocument();
    expect(screen.getByText("Earlier free-text answer")).toBeInTheDocument();
  });

  it("renders nothing for a request whose form has no deliverables table", async () => {
    const { container } = mount(request({ request_form: "nda" }), { canEdit: true });
    await waitFor(() => expect(h.intakeApi.forms).toHaveBeenCalled());
    await act(async () => { await h.intakeApi.forms.mock.results[0].value; });
    expect(container).toBeEmptyDOMElement();
  });
});

describe("RequestDeliverables: an older request with only free text (FR-31, FR-33)", () => {
  it("shows the earlier answer unchanged and labelled, and offers no editor to someone who may not edit", async () => {
    mount(request({ deliverables: OLD_TEXT }));
    await loaded();
    expect(screen.getByText("Earlier free-text answer")).toBeInTheDocument();
    expect(screen.getByText("(Deliverables and who accepts them)")).toBeInTheDocument();
    expect(screen.getByText(OLD_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save deliverables" })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("shows the editor beside the old text to someone who may edit", async () => {
    mount(request({ deliverables: OLD_TEXT }), { canEdit: true });
    await loaded();
    expect(screen.getByText(OLD_TEXT)).toBeInTheDocument();
    expect(screen.getByLabelText("Row 1, Deliverable name")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Save deliverables" })).toBeInTheDocument();
  });

  it("shows the reason a draft is held, as a warning", async () => {
    mount(request({ deliverables: OLD_TEXT }), { canEdit: true, heldNote: "Enter the deliverables as rows first." });
    await loaded();
    expect(screen.getByRole("status")).toHaveTextContent("Enter the deliverables as rows first.");
  });

  it("asks for the Share column too when the pricing is Milestones", async () => {
    mount(request({ deliverables: OLD_TEXT, pricing: "Milestones" }), { canEdit: true });
    await loaded();
    expect(screen.getByLabelText("Row 1, Share of the fee (%)")).toBeInTheDocument();
    expect(screen.getByText("Total 0% of 100%")).toBeInTheDocument();
  });

  const fillOne = () => {
    type("Row 1, Deliverable name", "Migration plan");
    type("Row 1, Due date", "2026-11-30");
    type("Row 1, Their owner", "Priya Nair");
    type("Row 1, Our owner", "Ravi Shah");
  };

  it("does not save rows that break the rules, and says which row and column", async () => {
    mount(request({ deliverables: OLD_TEXT }), { canEdit: true });
    await loaded();
    type("Row 1, Deliverable name", "Migration plan");
    fireEvent.click(screen.getByRole("button", { name: "Save deliverables" }));
    expect(h.intakeApi.update).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent('"Deliverables", row 1: "Due date" is required.');
    expect(screen.getByLabelText("Row 1, Due date")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Row 1, Deliverable name")).toHaveValue("Migration plan");
  });

  it("saves every stored answer back with the rows added, then refreshes the held run, then reports it", async () => {
    const saved = request({ deliverables: OLD_TEXT, ...rowsIn([ROWS[0]]) });
    h.intakeApi.update.mockResolvedValue(saved);
    h.workflowsApi.refreshRun.mockResolvedValue({ id: "run-9" });
    const r = request({ deliverables: OLD_TEXT });
    const { onSaved, qc } = mount(r, { canEdit: true, heldRunId: "run-9", heldNote: "held" });
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    await loaded();
    fillOne();
    fireEvent.click(screen.getByRole("button", { name: "Save deliverables" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    expect(h.intakeApi.update).toHaveBeenCalledWith("q-1", {
      field_values: { ...r.field_values, deliverable_rows: [{ name: "Migration plan", due: "2026-11-30", their_owner: "Priya Nair", our_owner: "Ravi Shah" }] },
    });
    expect(h.workflowsApi.refreshRun).toHaveBeenCalledWith("run-9");
    expect(h.intakeApi.update.mock.invocationCallOrder[0]).toBeLessThan(h.workflowsApi.refreshRun.mock.invocationCallOrder[0]);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["flow-run", "q-1"] });
  });

  it("does not touch the workflow when no step is held", async () => {
    h.intakeApi.update.mockResolvedValue(request({ deliverables: OLD_TEXT, ...rowsIn([ROWS[0]]) }));
    const { onSaved } = mount(request({ deliverables: OLD_TEXT }), { canEdit: true });
    await loaded();
    fillOne();
    fireEvent.click(screen.getByRole("button", { name: "Save deliverables" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(h.workflowsApi.refreshRun).not.toHaveBeenCalled();
  });

  it("still reports the save when the refresh fails: the rows are saved and the step carries on by itself", async () => {
    h.intakeApi.update.mockResolvedValue(request({ deliverables: OLD_TEXT, ...rowsIn([ROWS[0]]) }));
    h.workflowsApi.refreshRun.mockRejectedValue(new Error("busy"));
    const { onSaved } = mount(request({ deliverables: OLD_TEXT }), { canEdit: true, heldRunId: "run-9" });
    await loaded();
    fillOne();
    fireEvent.click(screen.getByRole("button", { name: "Save deliverables" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(h.notify).toHaveBeenCalledWith(expect.stringContaining("carries on by itself"), "info");
  });

  it("keeps the rows as typed when the server refuses, and shows its message", async () => {
    const message = '"Deliverables", row 1: "Due date" must be between 1 November 2026 and 31 March 2027 (Services start to Services end).';
    h.intakeApi.update.mockRejectedValue(new Error(message));
    const { onSaved } = mount(request({ deliverables: OLD_TEXT }), { canEdit: true, heldRunId: "run-9" });
    await loaded();
    fillOne();
    fireEvent.click(screen.getByRole("button", { name: "Save deliverables" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(message, "error"));
    expect(onSaved).not.toHaveBeenCalled();
    expect(h.workflowsApi.refreshRun).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Row 1, Deliverable name")).toHaveValue("Migration plan");
    expect(screen.getByLabelText("Row 1, Our owner")).toHaveValue("Ravi Shah");
    expect(screen.getByRole("button", { name: "Save deliverables" })).toBeEnabled();
  });
});

describe("RequestDeliverables: nothing entered at all", () => {
  it("says so to someone who may not edit, instead of showing an empty box", async () => {
    mount(request({}));
    await loaded();
    expect(screen.getByText("No deliverables have been entered.")).toBeInTheDocument();
  });
});

// ------------------------------------------------- the 008 tables (Consultancy and DPA) ----
// The same component serves four more tables. What each question declares (its noun, its reference prefix, one
// column or three, an open-ended window) is what changes; the cases below pin each of those, and 007's stay as
// they were above.

const newAgreement = forms.find((f) => f.key === "new_agreement")!;
const dpaForm = forms.find((f) => f.key === "dpa")!;
const questionIn = (def: RequestFormDef, key: string) => def.fields.find((f) => f.key === key) as RequestFormField;
const labelsIn = (def: RequestFormDef) => Object.fromEntries(def.fields.map((f) => [f.key, f.label]));
const dueTable = questionIn(newAgreement, "consultancy_deliverables");
const peopleTable = questionIn(newAgreement, "key_individuals");
const countriesTable = questionIn(dpaForm, "transfer_countries");
const subsTable = questionIn(dpaForm, "subprocessor_rows");

function EditorOf({ field, def, initial = [{}] as FormRow[], values = {} as Record<string, unknown>, today }: {
  field: RequestFormField; def: RequestFormDef; initial?: FormRow[]; values?: Record<string, unknown>; today?: string;
}) {
  const [rows, setRows] = useState(initial);
  return <RowsTable field={field} rows={rows} values={values} labels={labelsIn(def)} onChange={setRows} today={today}
    problems={rowsProblems(field, rows, values, labelsIn(def))} />;
}

describe("RowsTable: the noun a table names for its rows", () => {
  it("says 'Add a key individual' and 'at most 10 key individuals' for the key individuals", () => {
    render(<EditorOf field={peopleTable} def={newAgreement} initial={Array.from({ length: 10 }, (_, i) => ({ name: `Person ${i}` }))} />);
    const addBtn = screen.getByRole("button", { name: "Add a key individual" });
    expect(addBtn).toBeDisabled();
    expect(screen.getByText("A table holds at most 10 key individuals.")).toBeInTheDocument();
    expect(addBtn).toHaveAccessibleDescription("A table holds at most 10 key individuals.");
  });

  it("says 'Add a country', 'Add a sub-processor' and 'at most 20 sub-processors'", () => {
    const { unmount } = render(<EditorOf field={countriesTable} def={dpaForm} />);
    expect(screen.getByRole("button", { name: "Add a country" })).toBeEnabled();
    unmount();
    render(<EditorOf field={subsTable} def={dpaForm} initial={Array.from({ length: 20 }, () => ({}))} />);
    expect(screen.getByRole("button", { name: "Add a sub-processor" })).toBeDisabled();
    expect(screen.getByText("A table holds at most 20 sub-processors.")).toBeInTheDocument();
  });

  it("keeps the Statement of Work's words when the question names none", () => {
    render(<Editor initial={[{}]} />);
    expect(screen.getByRole("button", { name: "Add a deliverable" })).toBeInTheDocument();
  });

  it("adds and removes a key individual in order, with labelled cells", () => {
    render(<EditorOf field={peopleTable} def={newAgreement} initial={[{ name: "Anita Rao" }]} />);
    expect(within(screen.getByRole("table")).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Row", "Name", "Remove row"]);
    fireEvent.click(screen.getByRole("button", { name: "Add a key individual" }));
    type("Row 2, Name", "Dev Menon");
    fireEvent.click(screen.getByRole("button", { name: "Remove row 1" }));
    expect(screen.getByLabelText("Row 1, Name")).toHaveValue("Dev Menon");
    expect(screen.getByRole("button", { name: "Remove row 1" })).toBeDisabled();
  });
});

describe("RowsTable read-only: the reference column and the one-column list", () => {
  it("numbers the Consultancy deliverables D1, D2 like the Statement of Work", () => {
    render(<RowsTable field={dueTable} rows={[{ name: "Review", due: "2026-12-15" }, { name: "Report", due: "2027-03-15" }]}
      values={{ start_date: "2026-11-01", end_date: "2027-04-30" }} labels={labelsIn(newAgreement)} />);
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("D1");
    expect(rows[2]).toHaveTextContent("D2");
    expect(rows[2]).toHaveTextContent(fmtDate("2027-03-15"));
  });

  it("numbers the sub-processors 1, 2 with no prefix, and shows all three columns", () => {
    expect(subsTable.ref_prefix).toBe("");
    render(<RowsTable field={subsTable} labels={labelsIn(dpaForm)} values={{ transfer: "Yes" }}
      rows={[{ name: "CloudHost Pte Ltd", country: "Singapore", processing: "Hosting" }, { name: "SupportDesk GmbH", country: "Germany", processing: "Support" }]} />);
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(within(rows[0]).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Ref", "Sub-processor name", "Country", "What it does"]);
    expect(rows[1].querySelector("td")!.textContent).toBe("1");
    expect(rows[2].querySelector("td")!.textContent).toBe("2");
    expect(rows[2]).toHaveTextContent("Germany");
  });

  it("reads a one-column table as a list named for the question, not as a grid", () => {
    render(<RowsTable field={peopleTable} rows={[{ name: "Anita Rao" }, { name: "Dev Menon" }]} values={{}} labels={labelsIn(newAgreement)} />);
    expect(screen.queryByRole("table")).toBeNull();
    const list = screen.getByRole("list", { name: "Consultant's key individuals" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Anita Rao", "Dev Menon"]);
  });

  it("lists the countries the same way, and a dash for a cell that is somehow empty", () => {
    render(<RowsTable field={countriesTable} rows={[{ country: "Singapore" }, { country: "" }]} values={{ transfer: "Yes" }} labels={labelsIn(dpaForm)} />);
    const list = screen.getByRole("list", { name: "Countries outside India" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Singapore", "—"]);
  });

  it("is still a grid with a header when it is the editor, even with one column", () => {
    render(<EditorOf field={countriesTable} def={dpaForm} initial={[{ country: "Singapore" }]} values={{ transfer: "Yes" }} />);
    expect(screen.getByRole("table", { name: "Countries outside India" })).toBeInTheDocument();
    expect(screen.queryByRole("list")).toBeNull();
  });
});

describe("RowsTable: a due date window with no end date", () => {
  const start = { start_date: "2026-11-01" };
  const rows = (due: string): FormRow[] => [{ name: "Report", due }];

  it("offers no latest day and says dates must be on or after the start", () => {
    render(<EditorOf field={dueTable} def={newAgreement} initial={rows("2030-01-01")} values={start} />);
    const input = screen.getByLabelText("Row 1, Due date");
    expect(input).toHaveAttribute("min", "2026-11-01");
    expect(input).not.toHaveAttribute("max");
    expect(input).toHaveAccessibleDescription("Due dates must be on or after 1 November 2026 (Start date).");
    expect(input).not.toHaveAttribute("aria-invalid");
  });

  it("flags a date before the start beside the cell, with the open-ended wording", () => {
    render(<EditorOf field={dueTable} def={newAgreement} initial={rows("2026-10-31")} values={start} />);
    const cell = screen.getByLabelText("Row 1, Due date");
    expect(cell).toHaveAttribute("aria-invalid", "true");
    expect(document.getElementById(cell.getAttribute("aria-describedby")!.split(" ")[0])).toHaveTextContent('"Due date" must be on or after 1 November 2026 (Start date).');
  });

  it("says nothing about a window while there is no start, and uses both ends once there are two", () => {
    const { unmount } = render(<EditorOf field={dueTable} def={newAgreement} initial={rows("2030-01-01")} values={{ end_date: "2027-04-30" }} />);
    expect(screen.queryByText(/Due dates must/)).toBeNull();
    expect(screen.getByLabelText("Row 1, Due date")).not.toHaveAttribute("min");
    unmount();
    render(<EditorOf field={dueTable} def={newAgreement} initial={rows("2026-12-15")} values={{ ...start, end_date: "2027-04-30" }} />);
    const input = screen.getByLabelText("Row 1, Due date");
    expect(input).toHaveAttribute("max", "2027-04-30");
    expect(input).toHaveAccessibleDescription(/between 1 November 2026 and 30 April 2027 \(Start date to End date\)/);
  });

  it("notes a date that has passed but not a date before the start, which has its own error", () => {
    const { unmount } = render(<EditorOf field={dueTable} def={newAgreement} initial={rows("2026-12-01")} values={start} today="2027-01-01" />);
    expect(screen.getByText("Row 1: this due date has already passed.")).toBeInTheDocument();
    unmount();
    render(<EditorOf field={dueTable} def={newAgreement} initial={rows("2026-10-31")} values={start} today="2027-01-01" />);
    expect(screen.queryByText(/already passed/)).toBeNull();
  });
});

describe("RowsTable: the cell rules of the DPA tables", () => {
  it("flags India in the countries list beside the cell, in the server's words", () => {
    render(<EditorOf field={countriesTable} def={dpaForm} initial={[{ country: "India" }]} values={{ transfer: "Yes" }} />);
    const cell = screen.getByLabelText("Row 1, Country");
    expect(cell).toHaveAttribute("aria-invalid", "true");
    expect(document.getElementById(cell.getAttribute("aria-describedby")!)).toHaveTextContent('"Country" is India. List only the countries outside India.');
  });

  it("flags a sub-processor country other than India once the answer is that data does not leave India", () => {
    const row = [{ name: "CloudHost Pte Ltd", country: "Singapore", processing: "Hosting" }];
    const { unmount } = render(<EditorOf field={subsTable} def={dpaForm} initial={row} values={{ transfer: "No" }} />);
    const cell = screen.getByLabelText("Row 1, Country");
    expect(cell).toHaveAttribute("aria-invalid", "true");
    expect(document.getElementById(cell.getAttribute("aria-describedby")!)).toHaveTextContent(
      '"Country" is "Singapore", but the answer to "Will data leave India?" is No. Enter India, or change that answer.');
    unmount();
    render(<EditorOf field={subsTable} def={dpaForm} initial={row} values={{ transfer: "Yes" }} />);
    expect(screen.getByLabelText("Row 1, Country")).not.toHaveAttribute("aria-invalid");
  });
});
