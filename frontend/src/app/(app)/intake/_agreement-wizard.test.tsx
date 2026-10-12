import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AGREEMENT_FORMS, AgreementWizard } from "./_agreement-forms";
import { fmtDate } from "@/lib/utils";

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

function mount(formKey: string) {
  const def = AGREEMENT_FORMS.find((f) => f.key === formKey)!;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  render(
    <QueryClientProvider client={qc}>
      <AgreementWizard def={def} onFiled={vi.fn()} onBack={vi.fn()} />
    </QueryClientProvider>,
  );
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
    type("Deliverables and who accepts them", "A report");
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
    type("Deliverables and who accepts them", "A report");
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
