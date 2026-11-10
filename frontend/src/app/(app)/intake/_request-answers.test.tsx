import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fmtDate } from "@/lib/utils";
import { RequestAnswers } from "./_request-answers";
import type { IntakeRequest, RequestFormDef } from "@/lib/types";

// The answers a Consultancy or DPA draft is made from, on the request page: shown as rows, lists and labelled
// values (never raw data), an older request's single "Several" shown as the earlier answer it is, and the editor
// that lets a person who may change the request enter what the draft is waiting for. The Statement of Work's
// block is 007's and has its own tests; here it only has to be the one that renders.

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  intakeApi: { forms: vi.fn(), update: vi.fn() },
  workflowsApi: { refreshRun: vi.fn() },
}));
vi.mock("@/lib/endpoints", () => ({ intakeApi: h.intakeApi, workflowsApi: h.workflowsApi }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { full_name: "Legal One" } }) }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const FORMS: RequestFormDef[] = JSON.parse(
  readFileSync(resolve(__dirname, "../../../../../backend/app/intake/agreement_forms.json"), "utf8"),
);

const TICK = "There are no separate deliverables (advice only)";
const CONTROLLER = "Controller: we decide how the data is used";
const PROCESSOR = "Processor: we act on their instructions";
const HELD = "Enter these answers on the request first: \"Deliverables\", \"How is the consultant paid?\", \"Consultant's key individuals\", \"Company contact: name\" and \"Company contact: role\". The Engagement Schedule of the Consultancy draft is made from them, and this request does not have them yet. This step carries on by itself once the answers are saved on the request.";
const FREE_TEXT = "File this request on the Data Processing Agreement form first. It came in by email or chat, so it has none of the form's answers.";

// An older Consultancy request: everything it was filed with, none of the answers 008 added.
const OLDER_CONSULTANCY = {
  request_form: "new_agreement", agreement_type: "Consultancy", paper: "Our template", currency: "INR", value: 1800000,
  term: "Fixed end date", start_date: "2026-11-01", end_date: "2027-04-30", scope: "Advise on the quality process.", payment_terms: "30 days",
};
const NEW_CONSULTANCY = {
  consultancy_deliverables: [{ name: "Current-state review", due: "2026-12-15" }, { name: "Process design report", due: "2027-03-15" }],
  fee_basis: "Day rate", fee_rate: 25000,
  key_individuals: [{ name: "Anita Rao" }, { name: "Dev Menon" }],
  company_contact_name: "Ravi Shah", company_contact_role: "Head of Quality",
};
const DPA = {
  request_form: "dpa", paper: "Our template", our_role: CONTROLLER, parent_contract_id: "c-1",
  processing_purpose: "Hosting and support of the clinical-trial site portal.",
  data_types: ["Contact and identity", "Health or other special category"],
  data_subjects: ["Customers", "Patients or trial subjects"],
  transfer: "Yes", transfer_countries: [{ country: "Singapore" }, { country: "Germany" }],
  subprocessors: "Yes",
  subprocessor_rows: [
    { name: "CloudHost Pte Ltd", country: "Singapore", processing: "Hosting of the portal database" },
    { name: "SupportDesk GmbH", country: "Germany", processing: "Second-line support" },
  ],
};
// An older DPA that ticked one choice each and answered No twice: the draft needs nothing more from it.
const OLDER_DPA = {
  request_form: "dpa", paper: "Our template", our_role: CONTROLLER, processing_purpose: "Hosting the site portal.",
  data_types: "Contact and identity", data_subjects: "Customers", transfer: "No", subprocessors: "No",
};

const consultancy = (fv: Record<string, unknown>): IntakeRequest =>
  ({ id: "q-1", ref: "REQ-1", type_label: "New agreement · Consultancy", contract_id: null, draftable_doc_type: "consultancy", field_values: fv }) as unknown as IntakeRequest;
const dpa = (fv: Record<string, unknown>): IntakeRequest =>
  ({ id: "q-2", ref: "REQ-2", type_label: "Data Processing Agreement", contract_id: null, draftable_doc_type: "dpa", field_values: fv }) as unknown as IntakeRequest;

type Over = { canEdit?: boolean; heldNote?: string | null; heldRunId?: string | null };
function mount(r: IntakeRequest, over: Over = {}) {
  const onSaved = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <RequestAnswers request={r} canEdit={over.canEdit ?? false} heldNote={over.heldNote} heldRunId={over.heldRunId} onSaved={onSaved} />
    </QueryClientProvider>,
  );
  return { onSaved, qc, ...view };
}
/** The form definitions have been fetched and rendered into. */
const settled = () => act(async () => { await h.intakeApi.forms.mock.results[0]?.value; });
const loaded = () => screen.findByRole("heading", { name: "Answers for the draft" });
const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const saveButton = () => screen.getByRole("button", { name: "Save answers" });

beforeEach(() => {
  vi.resetAllMocks();
  h.intakeApi.forms.mockResolvedValue(FORMS);
});

describe("a Consultancy request that has its answers (FR-23)", () => {
  it("shows deliverables as rows, key individuals as a list, the rate with its currency and the contact, never as raw data", async () => {
    // Failure mode: a table or a list answer printed as "[object Object]" or JSON, or an editor over answers that exist.
    mount(consultancy({ ...OLDER_CONSULTANCY, ...NEW_CONSULTANCY }), { canEdit: true });
    await loaded();
    const t = screen.getByRole("table", { name: "Deliverables" });
    expect(within(t).getByText("Process design report").closest("tr")).toHaveTextContent("D2");
    expect(within(t).getByText("Process design report").closest("tr")).toHaveTextContent(fmtDate("2027-03-15"));
    expect(within(t).queryAllByRole("textbox")).toHaveLength(0);
    const people = screen.getByRole("list", { name: "Consultant's key individuals" });
    expect(within(people).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Anita Rao", "Dev Menon"]);
    expect(screen.getByText("Day rate")).toBeInTheDocument();
    expect(screen.getByText("INR 25,000")).toBeInTheDocument();
    expect(screen.getByText("Ravi Shah")).toBeInTheDocument();
    expect(screen.getByText("Head of Quality")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save answers" })).toBeNull();
    expect(screen.queryByText(/Not answered yet|object Object|consultancy_deliverables/)).toBeNull();
  });

  it("shows 'advice only' in place of a table when that was ticked, and not as a missing answer", async () => {
    // Failure mode: "Not answered yet" for a deliverables table the request does not need, or the tick lost.
    mount(consultancy({ ...OLDER_CONSULTANCY, ...NEW_CONSULTANCY, consultancy_deliverables: undefined, advice_only: [TICK] }), { canEdit: true });
    await loaded();
    expect(screen.getByRole("list", { name: "Advice only" })).toHaveTextContent(TICK);
    expect(screen.queryByRole("table", { name: "Deliverables" })).toBeNull();
    expect(screen.queryByText("Not answered yet")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save answers" })).toBeNull();
  });

  it("shows a fixed fee without a rate", async () => {
    // Failure mode: a rate row for a fee basis that has none.
    mount(consultancy({ ...OLDER_CONSULTANCY, ...NEW_CONSULTANCY, fee_basis: "Fixed fee", fee_rate: undefined }));
    await loaded();
    expect(screen.getByText("Fixed fee")).toBeInTheDocument();
    expect(screen.queryByText("Rate per day or hour")).toBeNull();
  });
});

describe("an older Consultancy request that lacks them (FR-41, FR-43)", () => {
  it("says each answer is not given yet, with the hold reason, and no editor for someone who may only look", async () => {
    // Failure mode: an editor shown to a person the page does not let change the request (the server would refuse
    // the save), or the reason the draft is held left unsaid.
    mount(consultancy(OLDER_CONSULTANCY), { canEdit: false, heldNote: HELD });
    await loaded();
    expect(screen.getByRole("status")).toHaveTextContent("Enter these answers on the request first");
    expect(screen.getAllByText("Not answered yet")).toHaveLength(5);
    expect(screen.queryByRole("button", { name: "Save answers" })).toBeNull();
    expect(screen.queryByLabelText("Row 1, Deliverable name")).toBeNull();
  });

  it("offers the editor, empty and with the hold reason above it, to someone who may change the request", async () => {
    mount(consultancy(OLDER_CONSULTANCY), { canEdit: true, heldNote: HELD, heldRunId: "run-1" });
    await loaded();
    expect(screen.getByRole("status")).toHaveTextContent("Enter these answers on the request first");
    expect(screen.getByLabelText("Row 1, Deliverable name")).toHaveValue("");
    expect(screen.getByLabelText("Row 1, Name")).toHaveValue("");
    expect(screen.getByLabelText("How is the consultant paid?")).toHaveValue("");
    expect(screen.getByLabelText("Company contact: name")).toHaveValue("");
    expect(screen.getByLabelText("Company contact: role")).toHaveValue("");
    // The rate only exists for a rate basis, and the table only while "advice only" is not ticked.
    expect(screen.queryByLabelText("Rate per day or hour")).toBeNull();
    type("How is the consultant paid?", "Hourly rate");
    expect(screen.getByLabelText("Rate per day or hour")).toHaveValue("");
    expect(screen.getByLabelText("Row 1, Due date")).toHaveAttribute("min", "2026-11-01");
    fireEvent.click(within(screen.getByRole("group", { name: "Advice only" })).getByRole("checkbox"));
    expect(screen.queryByLabelText("Row 1, Deliverable name")).toBeNull();
    fireEvent.click(within(screen.getByRole("group", { name: "Advice only" })).getByRole("checkbox"));
    expect(screen.getByLabelText("Row 1, Deliverable name")).toBeInTheDocument();
  });

  it("refuses to save with answers missing, names them, sends nothing and keeps what was typed", async () => {
    // Failure mode: a partial save that leaves the draft held, or the person's entries thrown away.
    mount(consultancy(OLDER_CONSULTANCY), { canEdit: true, heldRunId: "run-1" });
    await loaded();
    type("Row 1, Deliverable name", "Current-state review");
    fireEvent.click(saveButton());
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("\"Deliverables\", row 1: \"Due date\" is required");
    expect(alert).toHaveTextContent("How is the consultant paid?: Needed before you save.");
    expect(alert).toHaveTextContent("Company contact: name: Needed before you save.");
    expect(h.intakeApi.update).not.toHaveBeenCalled();
    expect(h.workflowsApi.refreshRun).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Row 1, Deliverable name")).toHaveValue("Current-state review");
  });

  it("uses the form's rules: a due date outside the term, a bracketed contact and a rate with too many decimals", async () => {
    // Failure mode: the page accepting what filing refuses, so the person finds out only after a failed save.
    mount(consultancy(OLDER_CONSULTANCY), { canEdit: true });
    await loaded();
    type("Row 1, Deliverable name", "Current-state review");
    type("Row 1, Due date", "2027-05-01");
    type("How is the consultant paid?", "Day rate");
    type("Rate per day or hour", "25.001");
    type("Row 1, Name", "Anita Rao");
    type("Company contact: name", "Ravi Shah");
    type("Company contact: role", "[Role]");
    fireEvent.click(saveButton());
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("\"Deliverables\", row 1: \"Due date\" must be between 1 November 2026 and 30 April 2027 (Start date to End date).");
    expect(alert).toHaveTextContent("Rate per day or hour: Can have at most 2 decimal places.");
    expect(alert).toHaveTextContent("Company contact: role: Contains [Role]");
    expect(h.intakeApi.update).not.toHaveBeenCalled();
  });

  it("saves every stored answer with the new ones, asks the held run to look again, then reports it", async () => {
    // Failure mode: answers saved but the step left held until the next sweep, a stored answer dropped from the
    // whole-replacement update, or the person told before the run was asked.
    const saved = consultancy({ ...OLDER_CONSULTANCY, ...NEW_CONSULTANCY, fee_rate: "25000", consultancy_deliverables: [{ name: "Current-state review", due: "2026-12-15" }] });
    h.intakeApi.update.mockResolvedValue(saved);
    h.workflowsApi.refreshRun.mockResolvedValue({});
    const { onSaved } = mount(consultancy(OLDER_CONSULTANCY), { canEdit: true, heldNote: HELD, heldRunId: "run-1" });
    await loaded();
    type("Row 1, Deliverable name", "  Current-state   review ");
    type("Row 1, Due date", "2026-12-15");
    type("How is the consultant paid?", "Day rate");
    type("Rate per day or hour", "25000");
    type("Row 1, Name", "Anita Rao");
    type("Company contact: name", "Ravi Shah");
    type("Company contact: role", "Head of Quality");
    fireEvent.click(saveButton());

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    expect(h.intakeApi.update).toHaveBeenCalledWith("q-1", {
      field_values: {
        ...OLDER_CONSULTANCY,
        consultancy_deliverables: [{ name: "  Current-state   review ", due: "2026-12-15" }],
        fee_basis: "Day rate", fee_rate: "25000",
        key_individuals: [{ name: "Anita Rao" }],
        company_contact_name: "Ravi Shah", company_contact_role: "Head of Quality",
      },
    });
    expect(h.workflowsApi.refreshRun).toHaveBeenCalledWith("run-1");
    // update, then the run is asked to look again, then the page is told.
    expect(h.intakeApi.update.mock.invocationCallOrder[0]).toBeLessThan(h.workflowsApi.refreshRun.mock.invocationCallOrder[0]);
    expect(h.workflowsApi.refreshRun.mock.invocationCallOrder[0]).toBeLessThan(onSaved.mock.invocationCallOrder[0]);
    expect(h.notify).not.toHaveBeenCalled();
  });

  it("with 'advice only' ticked and a fixed fee, sends neither a table nor a rate", async () => {
    // Failure mode: hidden answers (a table the request no longer has, a rate for a fixed fee) sent back for the server to refuse.
    h.intakeApi.update.mockResolvedValue(consultancy({}));
    mount(consultancy(OLDER_CONSULTANCY), { canEdit: true });
    await loaded();
    fireEvent.click(within(screen.getByRole("group", { name: "Advice only" })).getByRole("checkbox"));
    type("How is the consultant paid?", "Fixed fee");
    type("Row 1, Name", "Anita Rao");
    type("Company contact: name", "Ravi Shah");
    type("Company contact: role", "Head of Quality");
    fireEvent.click(saveButton());
    await waitFor(() => expect(h.intakeApi.update).toHaveBeenCalled());
    expect(h.intakeApi.update).toHaveBeenCalledWith("q-1", {
      field_values: {
        ...OLDER_CONSULTANCY, advice_only: [TICK], fee_basis: "Fixed fee",
        key_individuals: [{ name: "Anita Rao" }],
        company_contact_name: "Ravi Shah", company_contact_role: "Head of Quality",
      },
    });
  });

  it("does not ask a run to look again when nothing was held, and still reports the save", async () => {
    // Failure mode: poking a workflow that is not waiting for these answers.
    h.intakeApi.update.mockResolvedValue(consultancy({}));
    const { onSaved } = mount(consultancy(OLDER_CONSULTANCY), { canEdit: true });
    await loaded();
    fillConsultancy();
    fireEvent.click(saveButton());
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(h.workflowsApi.refreshRun).not.toHaveBeenCalled();
  });

  it("keeps the entries and shows the server's message when it refuses them, without asking the run to look again", async () => {
    // Failure mode: a refused save still poking the workflow, or the typed answers thrown away.
    h.intakeApi.update.mockRejectedValue(new Error("\"Deliverables\", row 1: \"Due date\" must be between 1 November 2026 and 30 April 2027"));
    const { onSaved } = mount(consultancy(OLDER_CONSULTANCY), { canEdit: true, heldRunId: "run-1" });
    await loaded();
    fillConsultancy();
    fireEvent.click(saveButton());
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(expect.stringContaining("row 1: \"Due date\""), "error"));
    expect(h.workflowsApi.refreshRun).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Row 1, Deliverable name")).toHaveValue("Current-state review");
    expect(saveButton()).toBeEnabled();
  });

  it("still reports the save when the run could not be asked to look again, and says it carries on by itself", async () => {
    // Failure mode: answers saved but the person shown a failure, or told nothing about the step.
    h.intakeApi.update.mockResolvedValue(consultancy({}));
    h.workflowsApi.refreshRun.mockRejectedValue(new Error("busy"));
    const { onSaved } = mount(consultancy(OLDER_CONSULTANCY), { canEdit: true, heldRunId: "run-1" });
    await loaded();
    fillConsultancy();
    fireEvent.click(saveButton());
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(h.notify).toHaveBeenCalledWith("Saved. The draft step carries on by itself shortly.", "info");
  });
});

describe("an older request with no 'Whose paper?' answer (P2-1)", () => {
  // Own paper is "Our template, or no such answer": the request is held for these answers, so it can be given them.
  const { paper: _consultancyPaper, ...PAPERLESS_CONSULTANCY } = OLDER_CONSULTANCY;
  const { paper: _dpaPaper, ...PAPERLESS_DPA } = { ...OLDER_DPA, data_subjects: "Several" };

  it("asks a Consultancy request for its five answers, to someone who may only look as well as to an editor", async () => {
    // Failure mode: no editor and no 'Not answered yet' because the Consultancy questions are hidden without a paper answer.
    mount(consultancy(PAPERLESS_CONSULTANCY), { canEdit: false, heldNote: HELD });
    await loaded();
    expect(screen.getAllByText("Not answered yet")).toHaveLength(5);
    expect(screen.queryByRole("button", { name: "Save answers" })).toBeNull();
  });

  it("offers the Consultancy editor and files the answers with 'Our template' as the paper", async () => {
    // Failure mode: the editor missing, or the save refused with 'Field "Whose paper?" is required'.
    h.intakeApi.update.mockResolvedValue(consultancy({}));
    h.workflowsApi.refreshRun.mockResolvedValue({});
    mount(consultancy(PAPERLESS_CONSULTANCY), { canEdit: true, heldNote: HELD, heldRunId: "run-1" });
    await loaded();
    fillConsultancy();
    fireEvent.click(saveButton());
    await waitFor(() => expect(h.workflowsApi.refreshRun).toHaveBeenCalledWith("run-1"));
    expect(h.intakeApi.update).toHaveBeenCalledWith("q-1", {
      field_values: {
        ...PAPERLESS_CONSULTANCY, paper: "Our template",
        consultancy_deliverables: [{ name: "Current-state review", due: "2026-12-15" }], fee_basis: "Fixed fee",
        key_individuals: [{ name: "Anita Rao" }], company_contact_name: "Ravi Shah", company_contact_role: "Head of Quality",
      },
    });
  });

  it("offers the DPA editor and files the answers with 'Our template' as the paper", async () => {
    // Failure mode: a paper-less older DPA left with 'Several' and no way to answer it.
    h.intakeApi.update.mockResolvedValue(dpa({}));
    mount(dpa(PAPERLESS_DPA), { canEdit: true });
    await loaded();
    fireEvent.click(within(screen.getByRole("group", { name: "Whose data?" })).getByRole("checkbox", { name: "Customers" }));
    fireEvent.click(saveButton());
    await waitFor(() => expect(h.intakeApi.update).toHaveBeenCalled());
    expect(h.intakeApi.update).toHaveBeenCalledWith("q-2", {
      field_values: { ...PAPERLESS_DPA, paper: "Our template", data_subjects: ["Customers"] },
    });
  });

  it("leaves a request that says 'Their paper' alone", async () => {
    // Failure mode: the paper-less reading applied to a request that does answer the question.
    const { container } = mount(consultancy({ ...PAPERLESS_CONSULTANCY, paper: "Their paper" }), { canEdit: true });
    await settled();
    expect(container).toBeEmptyDOMElement();
  });
});

function fillConsultancy() {
  type("Row 1, Deliverable name", "Current-state review");
  type("Row 1, Due date", "2026-12-15");
  type("How is the consultant paid?", "Fixed fee");
  type("Row 1, Name", "Anita Rao");
  type("Company contact: name", "Ravi Shah");
  type("Company contact: role", "Head of Quality");
}

describe("a Consultancy request on their paper (FR-12)", () => {
  it("shows nothing: no answer of the template is asked, and nothing is held", async () => {
    // Failure mode: an empty "Answers for the draft" block, or an editor for questions their paper never asks.
    const { container } = mount(consultancy({ ...OLDER_CONSULTANCY, paper: "Their paper" }), { canEdit: true });
    await settled();
    expect(container).toBeEmptyDOMElement();
  });
});

describe("a DPA request (FR-32)", () => {
  it("shows kinds and people as lists, countries as a list and sub-processors as rows", async () => {
    // Failure mode: several ticks run together or shown as raw data, a table shown as JSON.
    mount(dpa(DPA), { canEdit: true });
    await loaded();
    const kinds = screen.getByRole("list", { name: "What kind of personal data?" });
    expect(within(kinds).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Contact and identity", "Health or other special category"]);
    const people = screen.getByRole("list", { name: "Whose data?" });
    expect(within(people).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Customers", "Patients or trial subjects"]);
    const countries = screen.getByRole("list", { name: "Countries outside India" });
    expect(within(countries).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Singapore", "Germany"]);
    const subs = screen.getByRole("table", { name: "Sub-processors" });
    expect(within(subs).getByText("SupportDesk GmbH").closest("tr")).toHaveTextContent("Germany");
    expect(within(subs).getByText("SupportDesk GmbH").closest("tr")).toHaveTextContent("Second-line support");
    expect(screen.getByText("Hosting and support of the clinical-trial site portal.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save answers" })).toBeNull();
    expect(screen.queryByText(/Not answered yet|object Object|subprocessor_rows/)).toBeNull();
  });

  it("needs nothing more from an older request that ticked one choice each and answered No twice", async () => {
    // Failure mode: asking for an editor when the draft would be made at once (AC-47).
    mount(dpa(OLDER_DPA), { canEdit: true });
    await loaded();
    expect(screen.getByRole("list", { name: "Whose data?" })).toHaveTextContent("Customers");
    expect(screen.getByRole("list", { name: "What kind of personal data?" })).toHaveTextContent("Contact and identity");
    expect(screen.queryByRole("button", { name: "Save answers" })).toBeNull();
    expect(screen.queryByText("Not answered yet")).toBeNull();
  });

  it("shows an older 'Several' unchanged as the earlier answer, not as a pick", async () => {
    // Failure mode: "Several" hidden or rewritten (FR-41), or shown as if it were a ticked choice.
    mount(dpa({ ...OLDER_DPA, data_subjects: "Several" }));
    await loaded();
    const earlier = screen.getByText("Earlier answer (kept as filed)").parentElement!.parentElement!;
    expect(earlier).toHaveTextContent("Several");
    expect(screen.queryByRole("list", { name: "Whose data?" })).toBeNull();
  });

  it("with 'Several', asks for a tick beside the earlier answer, and sends the older single choice back as filed", async () => {
    // Failure mode: a "Several" nobody can answer the draft from (the person not asked), or the untouched
    // "Contact and identity" rewritten into a list by an edit that never touched it (FR-41).
    const saved = dpa({});
    h.intakeApi.update.mockResolvedValue(saved);
    h.workflowsApi.refreshRun.mockResolvedValue({});
    const { onSaved } = mount(dpa({ ...OLDER_DPA, data_subjects: "Several" }), { canEdit: true, heldRunId: "run-3" });
    await loaded();
    const people = screen.getByRole("group", { name: "Whose data?" });
    expect(within(people).getAllByRole("checkbox").every((c) => !(c as HTMLInputElement).checked)).toBe(true);
    expect(screen.getByText("Several")).toBeInTheDocument();
    expect(screen.getByText(/Your earlier answer/)).toBeInTheDocument();
    // The valid older single choice is already ticked: nothing to re-enter.
    expect((within(screen.getByRole("group", { name: "What kind of personal data?" })).getByRole("checkbox", { name: "Contact and identity" }) as HTMLInputElement).checked).toBe(true);

    fireEvent.click(saveButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("Whose data?: Needed before you save.");
    expect(h.intakeApi.update).not.toHaveBeenCalled();

    fireEvent.click(within(people).getByRole("checkbox", { name: "Customers" }));
    fireEvent.click(within(people).getByRole("checkbox", { name: "Suppliers" }));
    fireEvent.click(saveButton());
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    expect(h.intakeApi.update).toHaveBeenCalledWith("q-2", {
      field_values: { ...OLDER_DPA, data_subjects: ["Customers", "Suppliers"] },
    });
    expect(h.workflowsApi.refreshRun).toHaveBeenCalledWith("run-3");
  });

  it("asks for the countries and sub-processors a Yes needs, and only for a Yes", async () => {
    // Failure mode: Yes accepted with nothing listed, or tables asked for after a No.
    h.intakeApi.update.mockResolvedValue(dpa({}));
    mount(dpa({ ...OLDER_DPA, transfer: "Yes", subprocessors: "Yes" }), { canEdit: true });
    await loaded();
    // Both tables have a Country column: the countries come first, the sub-processors after.
    const [leaving, hosted] = screen.getAllByLabelText("Row 1, Country");
    expect(leaving).toHaveValue("");
    expect(screen.getByLabelText("Row 1, Sub-processor name")).toHaveValue("");
    fireEvent.change(leaving, { target: { value: "India" } });
    fireEvent.click(saveButton());
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("\"Countries outside India\", row 1: \"Country\" is India. List only the countries outside India.");
    expect(alert).toHaveTextContent("\"Sub-processors\"");
    expect(h.intakeApi.update).not.toHaveBeenCalled();

    fireEvent.change(leaving, { target: { value: "Singapore" } });
    type("Row 1, Sub-processor name", "CloudHost Pte Ltd");
    fireEvent.change(hosted, { target: { value: "Singapore" } });
    type("Row 1, What it does", "Hosting of the portal database");
    fireEvent.click(saveButton());
    await waitFor(() => expect(h.intakeApi.update).toHaveBeenCalled());
    expect(h.intakeApi.update).toHaveBeenCalledWith("q-2", {
      field_values: {
        ...OLDER_DPA, transfer: "Yes", subprocessors: "Yes",
        transfer_countries: [{ country: "Singapore" }],
        subprocessor_rows: [{ name: "CloudHost Pte Ltd", country: "Singapore", processing: "Hosting of the portal database" }],
      },
    });
  });

  it("shows a Processor request's answers with no editor: no draft is made from it, so none are asked for", async () => {
    // Failure mode: asking a person to enter answers for a draft the Processor rule will never make (FR-56).
    mount(dpa({ ...OLDER_DPA, our_role: PROCESSOR, data_subjects: "Several", transfer: "Yes", subprocessors: "Yes" }), { canEdit: true });
    await loaded();
    expect(screen.getByText("Earlier answer (kept as filed)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save answers" })).toBeNull();
    expect(screen.queryByLabelText("Row 1, Country")).toBeNull();
  });

  it("shows nothing for a DPA on their paper that was filed without answers to the template's questions", async () => {
    // Failure mode: an editor on their paper, where their draft is the contract.
    mount(dpa({ ...OLDER_DPA, paper: "Their paper", data_types: undefined, data_subjects: undefined }), { canEdit: true });
    await loaded();
    expect(screen.queryByRole("button", { name: "Save answers" })).toBeNull();
    expect(screen.queryByLabelText("Row 1, Country")).toBeNull();
  });
});

describe("a DPA that came by email or chat (FR-43)", () => {
  it("shows only why no draft is made, and no editor: it has no form to take answers from", async () => {
    // Failure mode: a form-less request offered an editor that would file answers to a form it was never filed on.
    mount(dpa({}), { canEdit: true, heldNote: FREE_TEXT, heldRunId: "run-1" });
    expect(await screen.findByRole("status")).toHaveTextContent(FREE_TEXT);
    expect(screen.queryByRole("heading", { name: "Answers for the draft" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save answers" })).toBeNull();
  });

  it("shows nothing when the draft is not held", async () => {
    // Failure mode: an empty warning on every email request.
    const { container } = mount(dpa({}), { canEdit: true });
    await settled();
    expect(container).toBeEmptyDOMElement();
  });
});

describe("requests of other kinds", () => {
  it("renders the Statement of Work's own block, not this one", async () => {
    // Failure mode: the SOW's deliverables replaced by a Consultancy-style block, or lost.
    const sow = {
      id: "q-3", ref: "REQ-3", contract_id: null, draftable_doc_type: "sow",
      field_values: { request_form: "sow", pricing: "Fixed fee", services_start: "2026-11-01", services_end: "2027-03-31",
        deliverable_rows: [{ name: "Migration plan", due: "2026-11-30", their_owner: "Priya Nair", our_owner: "Ravi Shah" }] },
    } as unknown as IntakeRequest;
    mount(sow);
    expect(await screen.findByRole("heading", { name: "Deliverables" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Deliverables" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Answers for the draft" })).toBeNull();
  });

  it("shows nothing for any other request", async () => {
    // Failure mode: a block on every request page.
    const nda = { id: "q-4", ref: "REQ-4", contract_id: null, draftable_doc_type: "nda", field_values: { request_form: "new_agreement", agreement_type: "NDA" } } as unknown as IntakeRequest;
    const { container } = mount(nda, { canEdit: true });
    await settled();
    expect(container).toBeEmptyDOMElement();
  });
});
