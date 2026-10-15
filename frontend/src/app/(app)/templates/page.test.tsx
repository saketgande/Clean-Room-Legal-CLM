import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TemplatesPage from "./page";
import type { DraftingTemplate, DraftingTemplateSummary, TemplateCheckResult } from "@/lib/types";

// The Check tab runs the playbook's real AI review on the template. Failure modes these tests guard: a
// button that does nothing, a check that sends the saved text when the editor holds unsaved changes, a
// result that stays on screen as if it described text that has since changed, and an error that leaves the
// person with no way to try again.

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  list: vi.fn(),
  get: vi.fn(),
  check: vi.fn(),
  preview: vi.fn(),
  versions: vi.fn(),
}));

vi.mock("@/lib/endpoints", () => ({
  draftingTemplatesApi: {
    list: h.list, get: h.get, check: h.check, preview: h.preview, versions: h.versions, save: vi.fn(), reset: vi.fn(),
  },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { full_name: "Legal One", permissions: ["playbook:run", "playbook:update"] } }) }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const summary = (over: Partial<DraftingTemplateSummary> = {}): DraftingTemplateSummary => ({
  key: "saas", name: "Software / SaaS Agreement", contract_type: "Software / SaaS Agreement",
  playbook: "Software / SaaS Agreement Playbook", customized: false, version: 0, updated_at: null, updated_by: null, ...over,
});
const template = (over: Partial<DraftingTemplate> = {}): DraftingTemplate => ({
  ...summary(), body: "4.2 The Provider shall invoice annually in advance.", default_body: "x", placeholders: [], ...over,
});
const result = (over: Partial<TemplateCheckResult> = {}): TemplateCheckResult => ({
  key: "saas", name: "Software / SaaS Agreement", checked: "saved", playbook_id: "p1",
  playbook_name: "Software / SaaS Agreement Playbook", playbook_version_id: "v1", rule_count: 8,
  findings: [], summary: null, text_was_truncated: false, ...over,
});
const finding = {
  rule_index: 8, clause_type: "payment_terms", severity: "high" as const,
  issue: "Fees are invoiced a year in advance; the playbook allows at most 20%.",
  quote: "invoice annually in advance", suggested_fix: "Invoice periodically in arrears.", approval_required: true,
};

function mount(tpl: DraftingTemplate = template()) {
  h.list.mockResolvedValue([summary({ playbook: tpl.playbook })]);
  h.get.mockResolvedValue(tpl);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TemplatesPage />
    </QueryClientProvider>,
  );
}

async function openCheckTab() {
  await screen.findByLabelText("Software / SaaS Agreement template text");
  fireEvent.click(screen.getByRole("tab", { name: "Check" }));
}

beforeEach(() => {
  vi.resetAllMocks();
});

describe("the Check tab", () => {
  it("runs the review on the saved template and lists what it found", async () => {
    h.check.mockResolvedValue(result({ findings: [finding] }));
    mount();
    await openCheckTab();
    expect(screen.getByText(/Checks the saved template/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Check against playbook" }));

    await waitFor(() => expect(screen.getByTestId("check-result")).toBeInTheDocument());
    expect(h.check).toHaveBeenCalledWith("saas", undefined);
    expect(screen.getByText("1 deviation found.")).toBeInTheDocument();
    expect(screen.getByText("Payment terms")).toBeInTheDocument();
    expect(screen.getByText("High")).toBeInTheDocument();
    expect(screen.getByText("Needs approval")).toBeInTheDocument();
    expect(screen.getByText(finding.issue)).toBeInTheDocument();
    expect(screen.getByText("invoice annually in advance")).toBeInTheDocument();
    expect(screen.getByText(/Invoice periodically in arrears\./)).toBeInTheDocument();
  });

  it("says plainly when the review found nothing", async () => {
    h.check.mockResolvedValue(result());
    mount();
    await openCheckTab();
    fireEvent.click(screen.getByRole("button", { name: "Check against playbook" }));
    expect(await screen.findByText("No deviations found.")).toBeInTheDocument();
    expect(screen.getByText(/8 rules checked/)).toBeInTheDocument();
  });

  it("checks the text in the editor, not the saved one, when there are unsaved changes", async () => {
    h.check.mockResolvedValue(result({ checked: "unsaved" }));
    mount();
    const area = await screen.findByLabelText("Software / SaaS Agreement template text");
    fireEvent.change(area, { target: { value: "4.2 Fees are invoiced periodically in arrears." } });
    fireEvent.click(screen.getByRole("tab", { name: "Check" }));
    expect(screen.getByText(/including changes you have not saved/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Check against playbook" }));

    await waitFor(() => expect(h.check).toHaveBeenCalled());
    expect(h.check).toHaveBeenCalledWith("saas", "4.2 Fees are invoiced periodically in arrears.");
  });

  it("shows that it is working, and cannot be started twice", async () => {
    let finish: (r: TemplateCheckResult) => void = () => {};
    h.check.mockReturnValue(new Promise<TemplateCheckResult>((res) => { finish = res; }));
    mount();
    await openCheckTab();
    fireEvent.click(screen.getByRole("button", { name: "Check against playbook" }));

    expect(await screen.findByText(/The AI is reading the template/)).toBeInTheDocument();
    const busy = screen.getByRole("button", { name: "Checking…" });
    expect(busy).toBeDisabled();
    fireEvent.click(busy);
    expect(h.check).toHaveBeenCalledTimes(1);

    finish(result());
    expect(await screen.findByText("No deviations found.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check again" })).toBeEnabled();
  });

  it("shows the server's reason when the check cannot run, and lets the person try again", async () => {
    h.check.mockRejectedValueOnce(new Error("The AI review could not run, so the template was not checked."));
    h.check.mockResolvedValueOnce(result());
    mount();
    await openCheckTab();
    fireEvent.click(screen.getByRole("button", { name: "Check against playbook" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The AI review could not run");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("No deviations found.")).toBeInTheDocument();
    expect(h.check).toHaveBeenCalledTimes(2);
  });

  it("cannot be started for a template no playbook reviews", async () => {
    mount(template({ playbook: null }));
    await openCheckTab();
    expect(screen.getByText(/nothing to check it against/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check against playbook" })).toBeDisabled();
  });

  it("warns that a result is out of date once the text changes, and keeps it across tabs", async () => {
    h.check.mockResolvedValue(result({ findings: [finding] }));
    mount();
    await openCheckTab();
    fireEvent.click(screen.getByRole("button", { name: "Check against playbook" }));
    await screen.findByTestId("check-result");
    expect(screen.queryByText(/You changed the text after this check/)).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Software / SaaS Agreement template text"), { target: { value: "new wording" } });
    fireEvent.click(screen.getByRole("tab", { name: "Check" }));

    expect(screen.getByTestId("check-result")).toBeInTheDocument();
    expect(screen.getByText(/You changed the text after this check/)).toBeInTheDocument();
  });

  it("is honest about what it cannot judge", async () => {
    h.check.mockResolvedValue(result({ text_was_truncated: true }));
    mount();
    await openCheckTab();
    fireEvent.click(screen.getByRole("button", { name: "Check against playbook" }));
    await screen.findByTestId("check-result");
    expect(screen.getByText(/cannot judge what a requester picks in the request form/)).toBeInTheDocument();
    expect(screen.getByText(/the end of it was not checked/)).toBeInTheDocument();
  });
});
