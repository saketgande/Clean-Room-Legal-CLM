import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CheckPanel, CHECK_POLL_MS, type CheckPanelProps } from "./_check-panel";
import { VersionHistory } from "./_version-history";
import { sha256Hex, toEditorText } from "@/lib/templates";
import type {
  DraftingTemplate,
  DraftingTemplateVersion,
  TemplateCheck,
  TemplateCheckFinding,
  TemplateCheckSummary,
  TemplateMutationResult,
  TemplateStatus,
} from "@/lib/types";

// The Checks tab starts paid AI reviews and shows what they found. Failure modes these tests guard: a check
// that sends the saved text when the editor holds unsaved changes (or the unsaved text when it is clean), an
// unsaved result passed off as the template's own (FR-16), a failure that leaves no way to try again (FR-8),
// an Apply that edits the wrong place or text that has since changed (FR-25), a jump to a passage that is not
// there (FR-24), controls offered to people the server refuses (FR-64), and a check that never updates after
// the person leaves it running (FR-5, FR-19).

const h = vi.hoisted(() => ({
  check: vi.fn(), checks: vi.fn(), checkDetail: vi.fn(), versions: vi.fn(), restore: vi.fn(), notify: vi.fn(),
}));
vi.mock("@/lib/endpoints", () => ({
  draftingTemplatesApi: {
    check: h.check, checks: h.checks, checkDetail: h.checkDetail, versions: h.versions, restore: h.restore,
  },
}));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const FIELDS = [
  { name: "company", label: "Our entity", about: "The company that drafts", sample: "Acme Ltd" },
  { name: "counterparty", label: "The other party", about: "The company we contract with", sample: "Globex" },
];
const BODY = "Fees: the Provider shall invoice annually in advance, for {company}.";
const EDITOR = toEditorText(BODY, FIELDS);
const QUOTE = "invoice annually in advance";
let SHA = "";
beforeAll(async () => {
  SHA = await sha256Hex(BODY);
});

const FINDING: TemplateCheckFinding = {
  index: 0, rule_index: 8, clause_type: "payment_terms", severity: "high",
  issue: "Fees are invoiced a year in advance; the playbook allows at most 20%.",
  quote: QUOTE, quote_template: QUOTE, suggested_fix: "invoice quarterly in arrears", suggested_fix_template: "invoice quarterly in arrears",
  suggestion_is_wording: true, approval_required: true,
};
const summary = (over: Partial<TemplateCheckSummary> = {}): TemplateCheckSummary => ({
  id: "c1", state: "completed", trigger: "saved", unsaved: false, template_version: 3, playbook_name: "SaaS Playbook",
  playbook_version_number: 3, finding_count: 1, highest_severity: "high", text_was_truncated: false, error_code: null,
  error_message: null, requested_by: "Priya Shah", on_behalf: false, created_at: "2026-10-15T08:00:00Z",
  started_at: "2026-10-15T08:00:05Z", finished_at: "2026-10-15T08:01:00Z", ...over,
});
const check = (over: Partial<TemplateCheck> = {}): TemplateCheck => ({
  ...summary(), template_key: "saas", trigger_detail: null, wording_sha256: SHA, playbook_id: "p1", playbook_version_id: "pv3",
  rule_count: 8, findings: [FINDING], summary: null, text_length: 100, read_length: 100, ...over,
});
const status = (over: Partial<TemplateStatus> = {}): TemplateStatus => ({
  state: "has_findings", label: "Has findings", reason: "1 finding, highest High.", finding_count: 1,
  highest_severity: "high", changed: null, error_code: null, results_vary: false, decided_by: summary(),
  last_completed: summary(), active_check: null, ...over,
});
const template = (over: Partial<DraftingTemplate> = {}): DraftingTemplate => ({
  key: "saas", name: "Software / SaaS Agreement", label: "Software / SaaS Agreement", contract_type: "Software / SaaS Agreement",
  binding: { form_key: "saas", form_name: "SaaS", agreement_kind: null, label: "SaaS agreements" },
  playbook: { id: "p1", name: "SaaS Playbook", version_id: "pv3", version_number: 3, pinned: true, available: true },
  shipped: true, active: true, source: "text", version: 3, original_filename: null, updated_at: null, updated_by: null,
  editable: true, status: status(), body: BODY, default_body: BODY, placeholders: FIELDS, fields: [], original_file: null, ...over,
});

const onApply = vi.fn();
const onShow = vi.fn();
const onChanged = vi.fn();

function panelProps(over: Partial<CheckPanelProps> = {}): CheckPanelProps {
  return {
    template: template(), status: status(), editorText: EDITOR, dirty: false, canRun: true, canChange: true,
    onApply, onShowInTemplate: onShow, onChecksChanged: onChanged, ...over,
  };
}
function mount(over: Partial<CheckPanelProps> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (p: Partial<CheckPanelProps>) => (
    <QueryClientProvider client={qc}>
      <CheckPanel {...panelProps(p)} />
    </QueryClientProvider>
  );
  const view = render(ui(over));
  return { ...view, rerenderWith: (p: Partial<CheckPanelProps>) => view.rerender(ui(p)) };
}
/** Lets the async sha256 of the saved wording land, so "Apply is not offered" is asserted after it could have been. */
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 30)); });

beforeEach(() => {
  vi.resetAllMocks();
  h.checks.mockResolvedValue([check()]);
});
afterEach(() => {
  vi.useRealTimers();
});

describe("what the page must say (FR-10) and what it shows about the last check", () => {
  it("states the three things a check is not, the status in words, and when and against what it last ran", async () => {
    mount();
    expect(screen.getByText(/can report different findings on identical wording/)).toBeInTheDocument();
    expect(screen.getByText(/Passes means the latest review found nothing\. It is not a guarantee about any contract/)).toBeInTheDocument();
    expect(screen.getByText(/filled in with example details, as a draft would read\. It does not judge the answers a requester picks/)).toBeInTheDocument();
    expect(screen.getByText("Status: Has findings. 1 finding, highest High.")).toBeInTheDocument();
    expect(screen.getByText(/Last completed check: finished .*, against SaaS Playbook version 3, of version 3\./)).toBeInTheDocument();
    await screen.findByRole("table");
  });

  it("says plainly when nothing has been checked yet", async () => {
    h.checks.mockResolvedValue([]);
    mount({ status: status({ state: "never_checked", label: "Never checked", reason: "No check has been made.", decided_by: null, last_completed: null }) });
    expect(screen.getByText("No check has finished yet.")).toBeInTheDocument();
    expect(await screen.findByText("No checks have been recorded for this template yet.")).toBeInTheDocument();
  });

  it("explains results vary with how many checks found findings and how many found none (FR-11)", async () => {
    h.checks.mockResolvedValue([
      check({ id: "c1" }),
      check({ id: "c2", finding_count: 1 }),
      check({ id: "c3", finding_count: 0, findings: [], highest_severity: null }),
      check({ id: "c4", unsaved: true, finding_count: 0, findings: [] }), // unsaved checks never count
      check({ id: "c5", wording_sha256: "older", finding_count: 0, findings: [] }), // other wording
    ]);
    mount({ status: status({ results_vary: true }) });
    expect(
      await screen.findByText("Identical wording has given different results: 2 checks found findings, 1 found none."),
    ).toBeInTheDocument();
  });

  it("shows no results-vary note when the status does not carry the flag", async () => {
    mount();
    await screen.findByRole("table");
    expect(screen.queryByText(/Identical wording has given different results/)).toBeNull();
  });
});

describe("Check now (FR-16)", () => {
  it("checks the saved wording when the editor is clean: sends no body", async () => {
    h.check.mockResolvedValue(check({ id: "n1", state: "queued", findings: [], finding_count: null }));
    h.checkDetail.mockResolvedValue(check({ id: "n1", state: "running" }));
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Check now (1 paid AI review)" }));
    await waitFor(() => expect(h.check).toHaveBeenCalledWith("saas", undefined));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(h.notify).toHaveBeenCalledWith("Check started. It runs in the background, so you can leave this page.", "info");
  });

  it("checks the text in the editor when it has unsaved changes: sends it with the codes back, and labels the result as unsaved", async () => {
    const unsaved = check({ id: "u1", unsaved: true, state: "completed", wording_sha256: "not-saved" });
    h.check.mockResolvedValue({ ...unsaved, state: "queued", findings: [], finding_count: null });
    h.checkDetail.mockResolvedValue(unsaved);
    mount({ dirty: true, editorText: "Fees: invoice quarterly in arrears, for «Our entity»." });

    expect(screen.getByText(/including changes you have not saved/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Check the unsaved wording (1 paid AI review)" }));

    await waitFor(() => expect(h.check).toHaveBeenCalledWith("saas", "Fees: invoice quarterly in arrears, for {company}."));
    expect(await screen.findByText("Unsaved wording")).toBeInTheDocument();
    expect(within(screen.getByTestId("check-result")).getByText(/doesn't change the template's status/)).toBeInTheDocument();
    expect(h.checkDetail).toHaveBeenCalledWith("saas", "u1");
  });

  it("shows the server's reason when a check cannot be started", async () => {
    h.check.mockRejectedValue(new Error("A check of this template is already waiting to start. Try again once it has started."));
    mount();
    fireEvent.click(screen.getByRole("button", { name: /Check now/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already waiting to start");
    expect(screen.getByRole("button", { name: /Check now/ })).toBeEnabled();
  });

  it("cannot be started for a template no playbook reviews (FR-9)", async () => {
    mount({
      template: template({ playbook: null }),
      status: status({ state: "no_playbook", label: "No playbook", reason: "No published playbook covers it.", decided_by: null, last_completed: null }),
    });
    expect(screen.getByText(/nothing to check it against/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Check now/ })).toBeDisabled();
    await settle();
  });

  it("offers a person who may not run reviews no check, and says why (FR-64)", async () => {
    mount({ canRun: false, status: status({ state: "couldnt_check", label: "Couldn't check", reason: "The AI review was unavailable." }) });
    expect(screen.queryByRole("button", { name: /Check now/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    expect(screen.getByText("Running a check needs the permission to run playbook reviews.")).toBeInTheDocument();
    await screen.findByRole("table"); // statuses, findings and history stay visible
  });
});

describe("Couldn't check (FR-8)", () => {
  const failed = () =>
    status({
      state: "couldnt_check", label: "Couldn't check", reason: "The AI review was unavailable, so the wording was not checked.",
      error_code: "ai_unavailable",
    });

  it("gives the reason, keeps the earlier result viewable, and Try again checks the saved wording", async () => {
    h.check.mockResolvedValue(check({ id: "r1", state: "queued", findings: [], finding_count: null }));
    h.checkDetail.mockResolvedValue(check({ id: "r1", state: "running" }));
    mount({ status: failed() });

    expect(screen.getByText(/The AI review was unavailable, so the wording was not checked/)).toBeInTheDocument();
    expect(await screen.findByTestId("check-result")).toHaveTextContent("Payment terms"); // the earlier completed result
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(h.check).toHaveBeenCalledWith("saas", undefined));
  });

  it("is not offered when the last check did not fail", async () => {
    mount();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    await settle();
  });
});

describe("a finding (FR-23)", () => {
  it("shows the clause, severity in words, approval, issue, quoted passage and suggested wording", async () => {
    mount();
    const card = (await screen.findByTestId("check-result")).querySelector("li") as HTMLElement;
    expect(within(card).getByText("Payment terms")).toBeInTheDocument();
    expect(within(card).getByText("High")).toBeInTheDocument();
    expect(within(card).getByText("Needs approval")).toBeInTheDocument();
    expect(within(card).getByText(FINDING.issue)).toBeInTheDocument();
    expect(within(card).getByText(QUOTE)).toBeInTheDocument();
    expect(within(card).getByText("invoice quarterly in arrears")).toBeInTheDocument();
  });

  it("says plainly when the review found nothing, and when it could only read part of the template", async () => {
    h.checks.mockResolvedValue([check({ findings: [], finding_count: 0, highest_severity: null, text_was_truncated: true, text_length: 90000, read_length: 60000 })]);
    mount();
    const result = await screen.findByTestId("check-result");
    expect(result).toHaveTextContent("No findings.");
    expect(result).toHaveTextContent("longer than the review can read, so the end of it was not checked");
    expect(result).toHaveTextContent("60,000 of 90,000 characters");
  });

  it("does not show the model's free-text summary, only the findings and their count", async () => {
    // Failure mode: the summary is the model thinking aloud and can contradict the findings (it
    // talked about three indemnification deviations while the one finding was service levels), so
    // showing it presents a contradictory AI summary as if it were the result.
    const SUMMARY = "Three deviations are flagged on indemnification, though on closer reading there is none.";
    h.checks.mockResolvedValue([check({ summary: SUMMARY })]);
    mount();
    const result = await screen.findByTestId("check-result");
    expect(result).toHaveTextContent("1 finding.");
    expect(screen.queryByText(SUMMARY)).toBeNull();
  });
});

describe("Show in template (FR-24)", () => {
  it("passes the located range to the page", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Show in template" }));
    const start = EDITOR.indexOf(QUOTE);
    expect(onShow).toHaveBeenCalledWith({ start, end: start + QUOTE.length });
  });

  it("is disabled, and says so, when the passage is not in the current wording", async () => {
    mount({ editorText: "Fees are now invoiced quarterly." });
    expect(await screen.findByRole("button", { name: "Show in template" })).toBeDisabled();
    expect(screen.getByText("Couldn't locate this passage in the current wording")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply suggestion" })).toBeNull();
  });

  it("finds the passage even when the wording differs only in whitespace", async () => {
    mount({ editorText: "Fees: the Provider shall invoice  annually\nin advance." });
    fireEvent.click(await screen.findByRole("button", { name: "Show in template" }));
    expect(onShow).toHaveBeenCalledTimes(1);
  });
});

describe("Apply suggestion (FR-25)", () => {
  it("is offered once and replaces exactly the passage, naming the finding it came from", async () => {
    mount();
    const apply = await screen.findByRole("button", { name: "Apply suggestion" });
    expect(screen.getAllByRole("button", { name: "Apply suggestion" })).toHaveLength(1);
    fireEvent.click(apply);
    const start = EDITOR.indexOf(QUOTE);
    expect(onApply).toHaveBeenCalledWith(
      { start, end: start + QUOTE.length }, "invoice quarterly in arrears", { check_id: "c1", finding_index: 0 },
    );
  });

  it("is not offered when the passage appears twice, and says why", async () => {
    mount({ editorText: `${EDITOR} Also: ${QUOTE}.` });
    await screen.findByRole("button", { name: "Show in template" });
    await settle();
    expect(screen.queryByRole("button", { name: "Apply suggestion" })).toBeNull();
    expect(screen.getByText(/appears 2 times in the wording, so the suggestion can't be applied automatically/)).toBeInTheDocument();
    expect(screen.getByText("invoice quarterly in arrears")).toBeInTheDocument(); // still shown, for reading
  });

  it("is not offered once it has been applied, until the page discards it", async () => {
    const view = mount({ appliedFindings: [{ check_id: "c1", finding_index: 0 }] });
    await screen.findByRole("button", { name: "Show in template" });
    await settle();
    expect(screen.queryByRole("button", { name: "Apply suggestion" })).toBeNull();
    expect(screen.getByText(/Applied in the editor\. It is not saved yet/)).toBeInTheDocument();
    view.rerenderWith({ appliedFindings: [] }); // Discard
    expect(await screen.findByRole("button", { name: "Apply suggestion" })).toBeInTheDocument();
  });

  it("is not offered to someone who may not change templates", async () => {
    mount({ canChange: false });
    await screen.findByRole("button", { name: "Show in template" });
    await settle();
    expect(screen.queryByRole("button", { name: "Apply suggestion" })).toBeNull();
  });

  it("is never offered for a template made from a Word file, which still shows the suggestion", async () => {
    mount({ template: template({ editable: false, source: "docx" }) });
    await screen.findByRole("button", { name: "Show in template" });
    await settle();
    expect(screen.queryByRole("button", { name: "Apply suggestion" })).toBeNull();
    expect(screen.getByText("invoice quarterly in arrears")).toBeInTheDocument();
    expect(screen.getByText(/Make this change in Word/)).toBeInTheDocument();
  });

  it("is not offered for advice that is not wording", async () => {
    h.checks.mockResolvedValue([check({ findings: [{ ...FINDING, suggestion_is_wording: false }] })]);
    mount();
    await screen.findByRole("button", { name: "Show in template" });
    await settle();
    expect(screen.queryByRole("button", { name: "Apply suggestion" })).toBeNull();
    expect(screen.getByText(/Suggested change \(advice\):/)).toBeInTheDocument();
  });

  it("is not offered for a check of older wording, and the check is labelled with its version", async () => {
    h.checks.mockResolvedValue([check({ id: "old", template_version: 2 })]);
    mount({ status: status({ decided_by: summary({ id: "old", template_version: 2 }) }) });
    expect(await screen.findByText("Older wording (version 2)")).toBeInTheDocument();
    await settle();
    expect(screen.queryByRole("button", { name: "Apply suggestion" })).toBeNull();
    expect(screen.getByText(/older wording, so its suggestion can't be applied/)).toBeInTheDocument();
  });

  it("is not offered when the saved wording changed under a check of the same version number", async () => {
    h.checks.mockResolvedValue([check({ wording_sha256: "some-other-wording" })]);
    mount();
    await screen.findByRole("button", { name: "Show in template" });
    await settle();
    expect(screen.queryByRole("button", { name: "Apply suggestion" })).toBeNull();
  });

  it("is not offered for someone else's unsaved check", async () => {
    h.checks.mockResolvedValue([check({ id: "theirs", unsaved: true })]);
    mount({ status: status({ decided_by: summary({ id: "theirs", unsaved: true }) }) });
    await screen.findByText("Unsaved wording");
    await settle();
    expect(screen.queryByRole("button", { name: "Apply suggestion" })).toBeNull();
  });
});

describe("check history (FR-18)", () => {
  it("lists when, who, why, which wording, which playbook version and the outcome of every check", async () => {
    h.checks.mockResolvedValue([
      check({ id: "c1" }),
      check({
        id: "c2", trigger: "playbook_published", requested_by: "Priya Shah", on_behalf: true, template_version: 2,
        playbook_version_number: 2, finding_count: 0, findings: [], highest_severity: null,
      }),
      check({
        id: "c3", trigger: "manual", state: "failed", requested_by: null, error_code: "cost_cap",
        error_message: "The organisation's AI spending limit was reached.", findings: [], finding_count: null, highest_severity: null,
      }),
    ]);
    mount();
    const table = await screen.findByRole("table", { name: "Recorded checks" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(4); // header + 3
    expect(within(rows[1]).getByText("Priya Shah")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Saved")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Version 3")).toBeInTheDocument();
    expect(within(rows[1]).getByText("1 finding")).toBeInTheDocument();
    expect(within(rows[2]).getByText("System, for Priya Shah")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Playbook published")).toBeInTheDocument();
    expect(within(rows[2]).getByText(/SaaS Playbook \(version 2\)/)).toBeInTheDocument();
    expect(within(rows[2]).getByText("No findings")).toBeInTheDocument();
    expect(within(rows[3]).getByText("System")).toBeInTheDocument();
    expect(within(rows[3]).getByText("Couldn't check")).toBeInTheDocument();
    expect(within(rows[3]).getByText(/spending limit was reached/)).toBeInTheDocument();
  });

  it("opens a row's findings, labelled with the older wording it covered", async () => {
    h.checks.mockResolvedValue([
      check({ id: "c1" }),
      check({
        id: "c2", template_version: 2, findings: [{ ...FINDING, clause_type: "liability", issue: "Liability is uncapped." }],
      }),
    ]);
    mount();
    await screen.findByRole("table");
    expect(screen.queryByText("Liability is uncapped.")).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: /^Show the check from/ })[1]);
    expect(await screen.findByText("Liability is uncapped.")).toBeInTheDocument();
    expect(screen.getByText("Older wording (version 2)")).toBeInTheDocument();
  });

  it("shows a failed check's reason instead of findings", async () => {
    h.checks.mockResolvedValue([
      check({ id: "f1", state: "failed", error_code: "ai_unavailable", error_message: "The AI review was unavailable.", findings: [], finding_count: null }),
    ]);
    mount({ status: status({ decided_by: null, last_completed: null }) });
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: /^Show the check from/ }));
    expect(await screen.findByText(/This check could not finish\. The AI review was unavailable\./)).toBeInTheDocument();
  });

  it("offers a retry when the history cannot be loaded", async () => {
    h.checks.mockRejectedValueOnce(new Error("boom")).mockResolvedValue([check()]);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });
});

describe("a check in progress (FR-5, FR-19)", () => {
  it("polls checkDetail every 3 s while it is queued or running, keeps the earlier result, and refreshes when it finishes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const running = check({ id: "a1", state: "running", findings: [], finding_count: null, finished_at: null });
    const done = check({ id: "a1", state: "completed", finding_count: 0, findings: [], highest_severity: null });
    h.checkDetail.mockResolvedValueOnce(running).mockResolvedValue(done);
    mount({ status: status({ state: "checking", label: "Checking", reason: "A check is running.", active_check: summary({ id: "a1", state: "running" }) }) });

    expect(await screen.findByText(/A check of the saved wording is in progress/)).toBeInTheDocument();
    expect(screen.getByTestId("check-result")).toHaveTextContent("Payment terms"); // the earlier result stays
    expect(h.checkDetail).toHaveBeenCalledTimes(1);
    expect(h.checkDetail).toHaveBeenCalledWith("saas", "a1");
    expect(onChanged).not.toHaveBeenCalled();

    await act(async () => { await vi.advanceTimersByTimeAsync(CHECK_POLL_MS); });
    await waitFor(() => expect(h.checkDetail).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/A check of the saved wording is in progress/)).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(CHECK_POLL_MS * 2); });
    expect(h.checkDetail).toHaveBeenCalledTimes(2); // stops polling once it is done
  });
});

describe("version history (FR-57, FR-58)", () => {
  const version = (over: Partial<DraftingTemplateVersion>): DraftingTemplateVersion => ({
    version: 1, note: null, source: "text", is_default: false, body: BODY, created_at: "2026-10-10T09:00:00Z", created_by: "Priya Shah",
    original_file: null, applied_findings: [], restored_from_version: null, ocr_confirmed_by: null, ocr_confirmed_at: null, checks: [], ...over,
  });
  const onRestored = vi.fn();

  function mountHistory(versions: DraftingTemplateVersion[], p: { canChange?: boolean; dirty?: boolean; tpl?: DraftingTemplate } = {}) {
    h.versions.mockResolvedValue(versions);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <VersionHistory template={p.tpl ?? template()} canChange={p.canChange ?? true} dirty={p.dirty} onRestored={onRestored} />
      </QueryClientProvider>,
    );
  }
  const all = [
    version({ version: 3, note: "Longer payment terms", checks: [summary({ id: "k1" }), summary({ id: "k2", finding_count: 0, highest_severity: null })] }),
    version({
      version: 2, source: "docx", original_file: { id: "f1", filename: "MSA.docx", mime_type: "x", size_bytes: 1 },
      applied_findings: [{ check_id: "c1", finding_index: 0, clause_type: "payment_terms", severity: "high" }],
    }),
    version({
      version: 1, source: "scan", ocr_confirmed_by: "Dev Patel", ocr_confirmed_at: "2026-10-09T10:00:00Z",
      original_file: { id: "f2", filename: "scan.pdf", mime_type: "x", size_bytes: 1 }, restored_from_version: 0,
    }),
    version({ version: 0, source: "default", is_default: true, created_at: null, created_by: null }),
  ];

  it("shows each version's number, source, author, note, applied suggestions and check outcomes", async () => {
    mountHistory(all);
    const list = await screen.findByRole("list", { name: "Versions" });
    expect(within(list).getByText("Version 3")).toBeInTheDocument();
    expect(within(list).getByText("Current")).toBeInTheDocument();
    expect(within(list).getByText("Longer payment terms")).toBeInTheDocument();
    expect(within(list).getByText("Typed text")).toBeInTheDocument();
    expect(within(list).getByText("Word file · MSA.docx")).toBeInTheDocument();
    expect(within(list).getByText(/Applied 1 suggestion from checks: Payment terms \(High\)/)).toBeInTheDocument();
    expect(within(list).getByText(/Scan · scan\.pdf · text confirmed by Dev Patel at /)).toBeInTheDocument();
    expect(within(list).getByText("Loaded back from the shipped wording.")).toBeInTheDocument();
    expect(within(list).getByText("Shipped wording", { selector: "b" })).toBeInTheDocument();
    const chips = within(screen.getByRole("list", { name: "Checks of Version 3" })).getAllByRole("listitem");
    expect(chips.map((c) => c.textContent)).toEqual(["1 finding · playbook version 3", "No findings · playbook version 3"]);
  });

  it("reads a version's wording, with fields as chips, without changing anything", async () => {
    mountHistory(all);
    fireEvent.click((await screen.findByText("Version 2")).closest("button") as HTMLElement);
    const wording = screen.getByLabelText("Wording of Version 2");
    expect(wording).toHaveTextContent("Fees: the Provider shall invoice annually in advance, for «Our entity».");
    expect(wording).not.toHaveTextContent("{company}");
  });

  it("loads an earlier version back only after a confirm that mentions the paid review", async () => {
    const restored: TemplateMutationResult = { template: template({ version: 4 }), changed: true, check: summary({ id: "new" }) };
    h.restore.mockResolvedValue(restored);
    mountHistory(all, { dirty: true });
    const buttons = await screen.findAllByRole("button", { name: "Load this version back" });
    expect(buttons).toHaveLength(3); // not for the current version

    fireEvent.click(buttons[0]); // version 2
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Loading it back starts one paid AI review of that wording");
    expect(dialog).toHaveTextContent("unsaved changes in the editor will be lost");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(h.restore).not.toHaveBeenCalled();

    fireEvent.click(screen.getAllByRole("button", { name: "Load this version back" })[0]);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Load back and start the check" }));
    await waitFor(() => expect(onRestored).toHaveBeenCalledWith(restored));
    expect(h.restore).toHaveBeenCalledWith("saas", 2, { expected_version: 3 });
  });

  it("does not offer loading back to someone who may not change templates (FR-64)", async () => {
    mountHistory(all, { canChange: false });
    await screen.findByRole("list", { name: "Versions" });
    expect(screen.queryByRole("button", { name: "Load this version back" })).toBeNull();
  });

  it("shows the server's reason when loading back is refused", async () => {
    h.restore.mockRejectedValue(new Error("Someone saved version 4 after you opened it."));
    mountHistory(all);
    fireEvent.click((await screen.findAllByRole("button", { name: "Load this version back" }))[0]);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Load back and start the check" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Someone saved version 4 after you opened it.", "error"));
    expect(onRestored).not.toHaveBeenCalled();
  });
});
