import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ClmWorkspace } from "./_clm-workspace";
import { HttpError } from "@/lib/api";
import type { ContractDeviation, ContractResponse, ContractRiskSummary, IssueFixJob, ReviewStatusResponse } from "@/lib/types";

// This file checks the page's WIRING: that the pieces it hosts are connected to each
// other and to the server calls. The chat, the review list and the lifecycle tab have
// their own tests; here they are stood in for so that only the connections show.

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  contractsApi: { get: vi.fn(), comments: vi.fn(), edits: vi.fn(), deviations: vi.fn(), risk: vi.fn(), parties: vi.fn(), revisionRound: vi.fn(), editorConfig: vi.fn(), aiRedline: vi.fn(), draftIssueFixes: vi.fn(), issueFixJob: vi.fn(), acceptEdit: vi.fn(), rejectEdit: vi.fn(), versions: vi.fn(), lifecycleOptions: vi.fn(), reviewStatus: vi.fn(), retryReview: vi.fn() },
  workflowsApi: { runForContract: vi.fn(), lifecycle: vi.fn(), startForContract: vi.fn() },
  playbooksApi: { decideDeviation: vi.fn() },
}));

vi.mock("@/lib/endpoints", () => ({ contractsApi: h.contractsApi, workflowsApi: h.workflowsApi, playbooksApi: h.playbooksApi }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { full_name: "Saket Gande", permissions: ["*"] } }) }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));
vi.mock("@/components/ui", () => ({ CenterSpinner: () => <div>Loading</div>, ErrorState: () => <div>Error</div>, NotFound: () => <div>Not found</div> }));
vi.mock("@/components/contract-document", () => ({
  DOCUMENT_PAGE_CSS: "",
  ContractDocument: (p: { mode?: string; zoom?: number; toolbar?: ReactNode; onSelectComment?: (id: string) => void }) => (
    <div data-testid="document" data-mode={p.mode} data-zoom={String(p.zoom)}>
      {p.toolbar}
      <button onClick={() => p.onSelectComment?.("cm-2")}>document → comment</button>
    </div>
  ),
}));
vi.mock("@/components/revision-round", () => ({ REVISION_CSS: "", RevisionChanges: () => null, RevisionDecisions: () => null }));
vi.mock("@/components/word-editor", () => ({ WORD_EDITOR_CSS: "", WordEditor: () => null }));
vi.mock("@/components/clause-tree", () => ({ CLAUSE_TREE_CSS: "", ClauseTree: () => null }));
vi.mock("@/components/lifecycle-view", () => ({
  LIFECYCLE_STRIP_CSS: "",
  currentStage: () => "review",
  LifecycleView: (p: { trailing?: ReactNode }) => <div data-testid="strip">{p.trailing}</div>,
}));
vi.mock("./_lifecycle-tab", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./_lifecycle-tab")>()),
  LifecycleTab: (p: { onOpenReview: () => void; onOpenComments?: () => void }) => (
    <div data-testid="lifecycle-stub"><button onClick={p.onOpenReview}>lifecycle → review</button><button onClick={p.onOpenComments}>lifecycle → comments</button></div>
  ),
}));
vi.mock("./_comments-tab", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./_comments-tab")>()),
  CommentsTab: (p: { contractId: string; focusId?: string | null; onFocused?: () => void }) => (
    <div data-testid="comments-stub" data-contract={p.contractId} data-focus={p.focusId ?? ""}>
      <button onClick={p.onFocused}>comments focused</button>
    </div>
  ),
}));
vi.mock("./_chat-tab", () => ({
  CHAT_TAB_CSS: "",
  ChatTab: (p: { contractId: string; intro?: ReactNode; banner?: ReactNode; onSettled: () => void; onOpenReview: () => void; onOpenLifecycle: () => void }) => (
    <div data-testid="chat-stub" data-contract={p.contractId}>
      <div data-testid="chat-banner">{p.banner}</div>
      <div data-testid="chat-intro">{p.intro}</div>
      <button onClick={p.onSettled}>chat settled</button>
      <button onClick={p.onOpenReview}>chat → review</button>
      <button onClick={p.onOpenLifecycle}>chat → lifecycle</button>
    </div>
  ),
}));

const dev = (id: string, severity: string, clause: string): ContractDeviation => ({
  id, playbook_run_id: "run-1", playbook_rule_id: null, contract_id: "c-1", severity, clause_type: clause,
  issue: `Issue ${id}`, suggested_fix: null, citation: null, status: "open",
});

const proposedEdit = (id: string, deviationId: string, over: Record<string, unknown> = {}) => ({
  id, contract_id: "c-1", contract_version_id: "v-1", edit_type: "replace", status: "proposed",
  original_text: "thirty days", replacement_text: "sixty days", rationale: null, citation: null,
  playbook_deviation_id: deviationId, locatable: true, ...over,
});

const contract = {
  id: "c-1", title: "Acme MSA", lifecycle_stage: "review", counterparty_name: "Acme Ltd",
  current_authoritative_version_id: "v-1",
} as unknown as ContractResponse;

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <ClmWorkspace id="c-1" />
    </QueryClientProvider>,
  );
  return { invalidate, qc };
}

const fixJob = (over: Partial<IssueFixJob> = {}): IssueFixJob => ({
  job_id: "j-1", status: "running", trigger: "review", total: 2, done: 0, drafted: 0, no_change: 0, failed: 0,
  left_for_later: 0, stopped_early: false, started_at: null, finished_at: null, ...over,
});

const riskOf = (band: ContractRiskSummary["band"], over: Partial<ContractRiskSummary> & Record<string, unknown> = {}) =>
  ({ band, contract_version_id: "v-1", note: null, drivers: [], ...over }) as ContractRiskSummary;

// Only last_review matters to the page; the rest is the Lifecycle tab's.
const reviewStatus = (last_review: ReviewStatusResponse["last_review"]) => ({ contract_id: "c-1", checklist: [], last_review }) as unknown as ReviewStatusResponse;

const tabButton = (name: RegExp) => screen.getByRole("button", { name });
const reviewTab = () => tabButton(/^Review\b/);
const keysOf = (spy: { mock: { calls: unknown[][] } }) => spy.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey);

beforeEach(() => {
  vi.resetAllMocks();
  h.contractsApi.get.mockResolvedValue(contract);
  h.contractsApi.edits.mockResolvedValue([]);
  h.contractsApi.comments.mockResolvedValue([]);
  h.contractsApi.deviations.mockResolvedValue([dev("d1", "high", "payment_terms"), dev("d2", "medium", "liability")]);
  h.contractsApi.risk.mockResolvedValue(null);
  h.contractsApi.parties.mockResolvedValue([]);
  h.contractsApi.revisionRound.mockResolvedValue(null);
  h.contractsApi.editorConfig.mockResolvedValue({ enabled: false });
  h.contractsApi.aiRedline.mockResolvedValue({ edits: 2, summary: null });
  h.contractsApi.issueFixJob.mockResolvedValue(null);
  h.contractsApi.draftIssueFixes.mockResolvedValue({ job: fixJob({ status: "queued" }), reason: null });
  h.contractsApi.versions.mockResolvedValue([{ id: "v-1", version_number: 2 }]);
  // The server still reports days in stage and a target; the page does not show them (see "the page around the contract").
  h.contractsApi.lifecycleOptions.mockResolvedValue({ current_stage: "review", allowed_transitions: [], days_in_stage: 3, stage_sla_days: 7, sla_breached: false });
  h.workflowsApi.runForContract.mockResolvedValue(null);
  h.workflowsApi.lifecycle.mockResolvedValue({});
  h.contractsApi.reviewStatus.mockResolvedValue(reviewStatus({ status: "succeeded", source: "ai", contract_version_id: "v-1" }));
});

describe("the contract page and its chat", () => {
  it("opens on the chat for this contract, and says what the review found", async () => {
    mount();
    const chat = await screen.findByTestId("chat-stub");
    expect(chat).toHaveAttribute("data-contract", "c-1");
    await waitFor(() => expect(screen.getByTestId("chat-intro")).toHaveTextContent("Found 2 open issues (1 high · 1 medium · 0 low)"));
    expect(tabButton(/^Assistant/)).toHaveClass("on");
  });

  it("does not announce a review that never ran", async () => {
    // Guards: a never-reviewed contract has no deviations, so the intro said "I reviewed this draft … it's clean".
    h.contractsApi.reviewStatus.mockResolvedValue(reviewStatus(null));
    h.contractsApi.deviations.mockResolvedValue([]);
    mount();
    const intro = await screen.findByTestId("chat-intro");
    await waitFor(() => expect(intro).toHaveTextContent("No playbook review has run"));
    expect(intro).not.toHaveTextContent("reviewed this draft");
    expect(intro).not.toHaveTextContent("it's clean");
  });

  it("calls a keyword scan a keyword check, not a review", async () => {
    // Guards: when the AI review failed over to the keyword engine, its findings were presented as "I reviewed".
    h.contractsApi.reviewStatus.mockResolvedValue(reviewStatus({ status: "keyword_only", source: "keyword", contract_version_id: "v-1" }));
    h.contractsApi.deviations.mockResolvedValue([]);
    mount();
    const intro = await screen.findByTestId("chat-intro");
    await waitFor(() => expect(intro).toHaveTextContent("only had a keyword check"));
    expect(intro).not.toHaveTextContent("I reviewed");
    expect(intro).not.toHaveTextContent("it's clean");
  });

  it("says a review that didn't finish didn't finish", async () => {
    // Guards: an AI run that never reached "succeeded" must not count as the review.
    h.contractsApi.reviewStatus.mockResolvedValue(reviewStatus({ status: "failed", source: "ai", contract_version_id: "v-1" }));
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-intro")).toHaveTextContent("didn't finish"));
    expect(screen.getByTestId("chat-intro")).not.toHaveTextContent("I reviewed this draft");
  });

  it("says when the review was of an earlier version", async () => {
    // Guards: a review of v-0 was announced as a review of the v-1 now on screen, hiding unchecked changes.
    h.contractsApi.reviewStatus.mockResolvedValue(reviewStatus({ status: "succeeded", source: "ai", contract_version_id: "v-0" }));
    h.contractsApi.deviations.mockResolvedValue([]);
    mount();
    const intro = await screen.findByTestId("chat-intro");
    await waitFor(() => expect(intro).toHaveTextContent("earlier version"));
    expect(intro).not.toHaveTextContent("it's clean");
  });

  it("counts a review of an identical earlier version as a review of this one", async () => {
    // Guards: a signed copy (same words, new version) was announced as "I reviewed an earlier version".
    h.contractsApi.reviewStatus.mockResolvedValue(reviewStatus({ status: "succeeded", source: "ai", contract_version_id: "v-0", on_current_text: true }));
    h.contractsApi.deviations.mockResolvedValue([]);
    mount();
    const intro = await screen.findByTestId("chat-intro");
    await waitFor(() => expect(intro).toHaveTextContent("I reviewed this draft."));
    expect(intro).not.toHaveTextContent("earlier version");
  });

  it("still calls a real clean review of this version clean", async () => {
    // Guards: the honesty checks must not hide a genuine clean AI review of the current version.
    h.contractsApi.deviations.mockResolvedValue([]);
    h.contractsApi.risk.mockResolvedValue(riskOf("low"));
    mount();
    const intro = await screen.findByTestId("chat-intro");
    await waitFor(() => expect(intro).toHaveTextContent("Overall risk is low."));
    expect(intro).toHaveTextContent("I reviewed this draft.");
    expect(intro).toHaveTextContent("it's clean");
  });

  it("says risk is not reviewed when there is no band, instead of a level", async () => {
    // Guards: an unknown band was shown as nothing at all beside "it's clean", which read as safe.
    h.contractsApi.deviations.mockResolvedValue([]);
    h.contractsApi.risk.mockResolvedValue(riskOf("unknown", { note: "No playbook review has run on this text." }));
    mount();
    const intro = await screen.findByTestId("chat-intro");
    await waitFor(() => expect(intro).toHaveTextContent("Risk: not reviewed on this text."));
    expect(intro).not.toHaveTextContent("Overall risk");
    expect(intro).not.toHaveTextContent("it's clean");
  });

  it("does not show a band computed for other text as this text's risk", async () => {
    // Guards: the cached summary lags a new version; a stale "low" must not read as the current risk.
    h.contractsApi.risk.mockResolvedValue(riskOf("low", { contract_version_id: "v-0" }));
    mount();
    const intro = await screen.findByTestId("chat-intro");
    await waitFor(() => expect(intro).toHaveTextContent("Risk: not reviewed on this text."));
    expect(intro).not.toHaveTextContent("Overall risk is low");
  });

  it("shows the band but never a 0-100 score", async () => {
    // Guards: an old cached row still carrying the retired weighted score printed "high (66)".
    h.contractsApi.risk.mockResolvedValue(riskOf("high", { score: 66 }));
    mount();
    const intro = await screen.findByTestId("chat-intro");
    await waitFor(() => expect(intro).toHaveTextContent("Overall risk is high."));
    expect(intro).not.toHaveTextContent("66");
  });

  it("no longer has the old one-shot question box", async () => {
    mount();
    await screen.findByTestId("chat-stub");
    expect(screen.queryByLabelText("Ask the agent to redline or analyse")).toBeNull();
    expect(screen.queryByRole("button", { name: "Summarise risks" })).toBeNull();
  });

  it("keeps the chat alive while another tab is open, only hidden", async () => {
    mount();
    const chat = await screen.findByTestId("chat-stub");
    fireEvent.click(reviewTab());
    expect(screen.getByTestId("chat-stub")).toBe(chat);
    expect(chat.parentElement).toHaveStyle({ display: "none" });
    fireEvent.click(tabButton(/^Assistant/));
    expect(chat.parentElement).toHaveStyle({ display: "contents" });
  });

  it("sends the chat's shortcuts to the right tabs", async () => {
    mount();
    await screen.findByTestId("chat-stub");
    fireEvent.click(screen.getByRole("button", { name: "chat → review" }));
    expect(reviewTab()).toHaveClass("on");
    // The chat is hidden while Review is open, so go back to it before using its other shortcut.
    fireEvent.click(tabButton(/^Assistant/));
    fireEvent.click(screen.getByRole("button", { name: "chat → lifecycle" }));
    expect(screen.getByTestId("lifecycle-stub")).toBeInTheDocument();
    expect(tabButton(/^Lifecycle/)).toHaveClass("on");
  });

  it("refetches what every tab shows when the chat changed something", async () => {
    const { invalidate } = mount();
    await screen.findByTestId("chat-stub");
    invalidate.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "chat settled" }));
    const keys = keysOf(invalidate).map((k) => JSON.stringify(k));
    for (const k of [
      ["contract", "c-1"], ["contract-lifecycle", "c-1"], ["review-status", "c-1"], ["contract-stage-history", "c-1"],
      ["flow-run-contract", "c-1"], ["lifecycle-rows-contract", "c-1"], ["contract-edits", "c-1"], ["contract-devs", "c-1"],
      ["issue-fix-job", "c-1"], // a playbook review run from the chat starts the drafting job
    ])
      expect(keys).toContain(JSON.stringify(k));
  });
});

describe("deciding a suggestion from the Review tab", () => {
  async function openReviewWith(edits: unknown[], deviations?: ContractDeviation[]) {
    h.contractsApi.edits.mockResolvedValue(edits);
    if (deviations) h.contractsApi.deviations.mockResolvedValue(deviations);
    mount();
    await screen.findByTestId("chat-stub");
    fireEvent.click(reviewTab());
    await screen.findAllByRole("button", { name: "Accept change" });
  }

  it("sends one request for a double click on Accept", async () => {
    let finish: () => void = () => {};
    h.contractsApi.acceptEdit.mockReturnValue(new Promise<void>((resolve) => { finish = resolve; }));
    await openReviewWith([proposedEdit("e1", "d1")]);
    const accept = screen.getByRole("button", { name: "Accept change" });
    fireEvent.click(accept);
    fireEvent.click(accept);
    await waitFor(() => expect(h.contractsApi.acceptEdit).toHaveBeenCalledTimes(1));
    finish();
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Change accepted", "success"));
    expect(h.contractsApi.acceptEdit).toHaveBeenCalledTimes(1);
  });

  it("sends one request for a double click on Reject", async () => {
    h.contractsApi.rejectEdit.mockResolvedValue({});
    await openReviewWith([proposedEdit("e1", "d1")]);
    const reject = screen.getByRole("button", { name: "Reject" });
    fireEvent.click(reject);
    fireEvent.click(reject);
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(expect.stringMatching(/^Change rejected/), "success"));
    expect(h.contractsApi.rejectEdit).toHaveBeenCalledTimes(1);
  });

  it("does not shout when the server says the change was already decided (another tab, or a slow second click)", async () => {
    h.contractsApi.acceptEdit.mockRejectedValue(new HttpError(409, "Only proposed edits can be accepted"));
    await openReviewWith([proposedEdit("e1", "d1")]);
    fireEvent.click(screen.getByRole("button", { name: "Accept change" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("That change was already decided. Showing what is saved.", "info"));
    expect(h.notify).not.toHaveBeenCalledWith(expect.anything(), "error");
  });

  it("still shows any other refusal in the server's words", async () => {
    h.contractsApi.acceptEdit.mockRejectedValue(new HttpError(409, "This change overlaps a change that was already accepted, so both can't be applied. Reject this one."));
    await openReviewWith([proposedEdit("e1", "d1")]);
    fireEvent.click(screen.getByRole("button", { name: "Accept change" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(expect.stringMatching(/overlaps a change/), "error"));
  });

  it("lets the next decision through once the first has finished", async () => {
    h.contractsApi.acceptEdit.mockResolvedValue({});
    await openReviewWith([proposedEdit("e1", "d1"), proposedEdit("e2", "d2")]);
    fireEvent.click(screen.getAllByRole("button", { name: "Accept change" })[0]);
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Change accepted", "success"));
    fireEvent.click(screen.getAllByRole("button", { name: "Accept change" })[0]);
    await waitFor(() => expect(h.contractsApi.acceptEdit).toHaveBeenCalledTimes(2));
  });

  describe("an older playbook change that does not name its issue", () => {
    const legacy = (id: string, issue: string) => proposedEdit(id, "", { playbook_deviation_id: null, edit_type: "playbook_redline", rationale: issue });

    it("closes a medium issue as kept when its fix is rejected", async () => {
      h.contractsApi.rejectEdit.mockResolvedValue({});
      h.playbooksApi.decideDeviation.mockResolvedValue({});
      await openReviewWith([legacy("e2", "Issue d2")]);
      fireEvent.click(screen.getByRole("button", { name: "Reject" }));
      await waitFor(() => expect(h.playbooksApi.decideDeviation).toHaveBeenCalledWith("d2", "waived", "Kept the original wording"));
    });

    it("leaves a high issue open when its fix is rejected, and says so", async () => {
      h.contractsApi.rejectEdit.mockResolvedValue({});
      await openReviewWith([legacy("e1", "Issue d1")]);
      fireEvent.click(screen.getByRole("button", { name: "Reject" }));
      await waitFor(() => expect(h.contractsApi.rejectEdit).toHaveBeenCalled());
      await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Change rejected — the issue stays open until you say why the risk can stay", "success"));
      expect(h.playbooksApi.decideDeviation).not.toHaveBeenCalled();
    });

    it("closes a high issue as fixed when its fix is accepted", async () => {
      h.contractsApi.acceptEdit.mockResolvedValue({});
      h.playbooksApi.decideDeviation.mockResolvedValue({});
      await openReviewWith([legacy("e1", "Issue d1")]);
      fireEvent.click(screen.getAllByRole("button", { name: "Accept change" })[0]);
      await waitFor(() => expect(h.playbooksApi.decideDeviation).toHaveBeenCalledWith("d1", "fixed", "Accepted the proposed change"));
    });
  });

  it("keeps a high risk open after Reject until a reason is given, then saves the reason with it", async () => {
    h.contractsApi.rejectEdit.mockResolvedValue({});
    h.playbooksApi.decideDeviation.mockResolvedValue({});
    await openReviewWith([proposedEdit("e1", "d1")]);
    // The server leaves the issue open; once the page refetches after the click, the card offers to close it.
    h.contractsApi.edits.mockResolvedValue([proposedEdit("e1", "d1", { status: "rejected" })]);
    fireEvent.click(screen.getAllByRole("button", { name: "Reject" })[0]);
    await waitFor(() => expect(h.contractsApi.rejectEdit).toHaveBeenCalled());
    expect(h.playbooksApi.decideDeviation).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Close issue" }));
    fireEvent.change(screen.getByLabelText(/Why can this high risk stay/), { target: { value: "Agreed with the GC on 3 Oct" } });
    fireEvent.click(screen.getByRole("button", { name: "Keep original wording" }));
    await waitFor(() => expect(h.playbooksApi.decideDeviation).toHaveBeenCalledWith("d1", "waived", "Kept the original wording. Agreed with the GC on 3 Oct"));
  });
});

describe("drafting fixes from the Review tab", () => {
  async function openReview() {
    const view = mount();
    await screen.findByTestId("chat-stub");
    fireEvent.click(reviewTab());
    await screen.findByText("Issue d1");
    return view;
  }

  it("starts the background drafting for the issues with no change, not one combined request", async () => {
    await openReview();
    expect(screen.getByRole("status")).toHaveTextContent("2 open issues have no suggested change yet.");
    fireEvent.click(screen.getByRole("button", { name: "Draft fixes for all" }));

    await waitFor(() => expect(h.contractsApi.draftIssueFixes).toHaveBeenCalledWith("c-1"));
    expect(h.contractsApi.aiRedline).not.toHaveBeenCalled();
    expect(await screen.findByText("Drafting suggested changes — 0 of 2 done.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Draft fixes for all" })).toBeNull();
    for (const b of screen.getAllByRole("button", { name: "Draft fix with AI" })) expect(b).toBeDisabled();
    expect(reviewTab()).toHaveClass("on");
  });

  it("takes the button away as soon as it is pressed, so the next press can't start a second job", async () => {
    // (And if one got through anyway, the server hands back the job already running: backend test_issue_fix_drafting.)
    h.contractsApi.draftIssueFixes.mockReturnValue(new Promise(() => {}));
    await openReview();
    fireEvent.click(screen.getByRole("button", { name: "Draft fixes for all" }));
    expect(await screen.findByText("Drafting suggested changes…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Draft fixes for all" })).toBeNull();
    expect(h.contractsApi.draftIssueFixes).toHaveBeenCalledTimes(1);
  });

  it("shows the progress of a job a playbook review started, and shows each change as it lands", async () => {
    h.contractsApi.issueFixJob
      .mockResolvedValueOnce(fixJob({ status: "running", total: 3, done: 0 }))
      .mockResolvedValue(fixJob({ status: "running", total: 3, done: 1 })); // the refetch when Review is opened
    const { invalidate } = mount();
    await screen.findByTestId("chat-stub");
    await waitFor(() => expect(h.contractsApi.issueFixJob).toHaveBeenCalledTimes(1));
    invalidate.mockClear();
    fireEvent.click(reviewTab());
    expect(await screen.findByText("Drafting suggested changes — 1 of 3 done.")).toBeInTheDocument();
    await waitFor(() => expect(keysOf(invalidate)).toEqual(expect.arrayContaining([["contract-edits", "c-1"], ["review-status", "c-1"]])));
  });

  it("asks the server again whenever Review is opened, since a review can finish while another tab is open", async () => {
    const { invalidate } = mount();
    await screen.findByTestId("chat-stub");
    invalidate.mockClear();
    fireEvent.click(reviewTab());
    expect(keysOf(invalidate)).toContainEqual(["issue-fix-job", "c-1"]);
  });

  it("follows the job to its end by polling, and says how it went once", async () => {
    h.contractsApi.issueFixJob
      .mockResolvedValueOnce(fixJob({ total: 2, done: 0 }))
      .mockResolvedValueOnce(fixJob({ total: 2, done: 0 })) // the refetch when Review is opened
      .mockResolvedValue(fixJob({ status: "succeeded", total: 2, done: 2, drafted: 2, finished_at: "2026-10-07T10:00:00Z" }));
    await openReview();
    expect(await screen.findByText("Drafting suggested changes — 0 of 2 done.")).toBeInTheDocument();

    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Drafted 2 suggested changes — accept or reject them below.", "success"), { timeout: 6000 });
    expect(h.notify).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/of 2 done/)).toBeNull();
    expect(h.contractsApi.issueFixJob.mock.calls.length).toBeGreaterThanOrEqual(3);
    // Polling stops once the job is over.
    const calls = h.contractsApi.issueFixJob.mock.calls.length;
    await new Promise((r) => setTimeout(r, 3300));
    expect(h.contractsApi.issueFixJob.mock.calls.length).toBe(calls);
  }, 15000);

  it("tells the person what went wrong when a job they watched did not work", async () => {
    h.contractsApi.issueFixJob.mockResolvedValue(fixJob({ status: "running" }));
    const { qc } = await openReview();
    await screen.findByText("Drafting suggested changes — 0 of 2 done.");
    act(() => {
      qc.setQueryData(["issue-fix-job", "c-1"], fixJob({ status: "failed", drafted: 0, failed: 2, done: 2 }));
    });
    await waitFor(() =>
      expect(h.notify).toHaveBeenCalledWith("Couldn't draft suggested changes automatically. Use “Draft fixes for all” to try again.", "error"),
    );
  });

  it("says nothing about a job that had already finished when the page looked", async () => {
    h.contractsApi.issueFixJob.mockResolvedValue(fixJob({ status: "succeeded", total: 2, done: 2, drafted: 2 }));
    await openReview();
    await waitFor(() => expect(h.contractsApi.issueFixJob).toHaveBeenCalled());
    expect(h.notify).not.toHaveBeenCalled();
    expect(screen.queryByText(/Drafting suggested changes/)).toBeNull();
    expect(screen.getByRole("button", { name: "Draft fixes for all" })).toBeEnabled();
  });

  it("says so when every issue already has a change by the time the server looks", async () => {
    h.contractsApi.draftIssueFixes.mockResolvedValue({ job: null, reason: "nothing_to_draft" });
    const { invalidate } = await openReview();
    invalidate.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Draft fixes for all" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Every open issue already has a suggested change.", "info"));
    expect(keysOf(invalidate)).toEqual(expect.arrayContaining([["contract-edits", "c-1"], ["contract-devs", "c-1"]]));
    expect(screen.getByRole("button", { name: "Draft fixes for all" })).toBeEnabled();
  });

  it("shows the server's reason when the drafting can't be started, and lets the person try again", async () => {
    h.contractsApi.draftIssueFixes.mockRejectedValueOnce(new Error("You don't have permission to redline."));
    await openReview();
    fireEvent.click(screen.getByRole("button", { name: "Draft fixes for all" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("You don't have permission to redline.", "error"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Draft fixes for all" })).toBeEnabled());
  });

  it("does not draft one issue by hand while the job is writing", async () => {
    h.contractsApi.issueFixJob.mockResolvedValue(fixJob({ status: "running", total: 2, done: 0 }));
    await openReview();
    await screen.findByText("Drafting suggested changes — 0 of 2 done.");
    const card = screen.getByText("Issue d2").closest(".rev") as HTMLElement;
    const button = within(card).getByRole("button", { name: "Draft fix with AI" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(h.contractsApi.aiRedline).not.toHaveBeenCalled();
  });

  it("drafts a fix for one issue and ties it to that issue", async () => {
    await openReview();
    const card = screen.getByText("Issue d2").closest(".rev") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "Draft fix with AI" }));
    await waitFor(() => expect(h.contractsApi.aiRedline).toHaveBeenCalledTimes(1));
    const [, instructions, deviationId] = h.contractsApi.aiRedline.mock.calls[0];
    expect(deviationId).toBe("d2");
    expect(instructions).toBe("Fix the liability issue as a tracked change. Issue: Issue d2");
    expect(h.contractsApi.draftIssueFixes).not.toHaveBeenCalled();
    expect(reviewTab()).toHaveClass("on");
  });

  it("says so, in words, while one issue's draft is being written, and holds the buttons", async () => {
    h.contractsApi.aiRedline.mockReturnValue(new Promise(() => {}));
    await openReview();
    const card = screen.getByText("Issue d1").closest(".rev") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "Draft fix with AI" }));
    expect(await screen.findByText("Drafting suggested changes…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Draft fixes for all" })).toBeNull();
    for (const b of screen.getAllByRole("button", { name: "Draft fix with AI" })) expect(b).toBeDisabled();
  });

  it("tells the person when nothing was drafted for an issue, using the server's reason", async () => {
    h.contractsApi.aiRedline.mockResolvedValue({ edits: 0, summary: "The clause already matches the playbook." });
    await openReview();
    const card = screen.getByText("Issue d1").closest(".rev") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "Draft fix with AI" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("The clause already matches the playbook.", "info"));
  });

  it("says how many changes one issue's draft made, and where to decide them", async () => {
    const { invalidate } = await openReview();
    invalidate.mockClear();
    const card = screen.getByText("Issue d1").closest(".rev") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "Draft fix with AI" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Drafted 2 suggested changes — accept or reject them below.", "success"));
    expect(keysOf(invalidate)).toEqual(expect.arrayContaining([["contract-edits", "c-1"], ["review-status", "c-1"]]));
  });

  it("shows the server's reason when one issue's draft fails, and lets the person try again", async () => {
    h.contractsApi.aiRedline.mockRejectedValueOnce(new Error("The AI service is busy."));
    await openReview();
    const card = screen.getByText("Issue d1").closest(".rev") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "Draft fix with AI" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("The AI service is busy.", "error"));
    await waitFor(() => expect(within(card).getByRole("button", { name: "Draft fix with AI" })).toBeEnabled());
  });
});

describe("the page around the contract", () => {
  it("has no header Save button: the text is saved from the document's own Save, and the old one saved nothing (round 2, bug 10)", async () => {
    // Guards: the header "Save" only showed a "Draft saved" toast. Nothing was saved, and people trusted it.
    mount();
    await screen.findByTestId("chat-stub");
    await screen.findByTitle("The version on screen");
    expect(screen.queryByRole("button", { name: /^Save$/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /save/i })).toBeNull();
    expect(h.notify).not.toHaveBeenCalledWith("Draft saved", "success");
  });

  it("shows which version is on screen", async () => {
    mount();
    await screen.findByTestId("chat-stub");
    expect(await screen.findByTitle("The version on screen")).toHaveTextContent("V2");
  });

  it.each([
    ["on time", { days_in_stage: 3, stage_sla_days: 7, sla_breached: false }],
    ["overdue", { days_in_stage: 9, stage_sla_days: 7, sla_breached: true }],
  ])("does not show how many days the contract has been in its stage (%s)", async (_label, days) => {
    h.contractsApi.lifecycleOptions.mockResolvedValue({ current_stage: "review", allowed_transitions: [], ...days });
    mount();
    const strip = await screen.findByTestId("strip");
    await screen.findByTitle("The version on screen");
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); }); // let every answer from the server land first
    expect(screen.queryByText(/Day \d|Entered today|overdue|last day/)).toBeNull();
    expect(strip).toBeEmptyDOMElement(); // nothing at the right end of the stage row unless the contract is flagged
  });

  it("still flags a contract that is due for renewal or archived, at the right end of the stage row", async () => {
    h.contractsApi.get.mockResolvedValue({ ...contract, renewal_due: true, archived: true });
    mount();
    const strip = await screen.findByTestId("strip");
    expect(await within(strip).findByText("Renewal due")).toBeInTheDocument();
    expect(within(strip).getByText("Archived")).toBeInTheDocument();
  });

  it("says how much of the review is decided, from the changes already decided", async () => {
    h.contractsApi.edits.mockResolvedValue([proposedEdit("e0", "dx", { status: "accepted" }), proposedEdit("e1", "d1"), proposedEdit("e2", "d2")]);
    mount();
    const summary = await screen.findByRole("group", { name: "Review progress" });
    // 1 decided (e0) + 2 still to decide (the two open issues, each already carrying a change)
    await waitFor(() => expect(summary).toHaveTextContent("1 of 3 decided"));
    expect(summary).toHaveTextContent("1 high");
    expect(summary).toHaveTextContent("1 medium");
    expect(summary).not.toHaveTextContent("low");
  });

  it("says there is nothing to decide when the review is clean", async () => {
    h.contractsApi.deviations.mockResolvedValue([]);
    mount();
    const summary = await screen.findByRole("group", { name: "Review progress" });
    await waitFor(() => expect(summary).toHaveTextContent("Nothing to decide"));
  });

  it("has no Start workflow button in the page bar: starting one is done in the Lifecycle tab", async () => {
    mount();
    await screen.findByTestId("chat-stub");
    await screen.findByTitle("The version on screen");
    expect(screen.queryByRole("button", { name: /start workflow/i })).toBeNull();
    expect(h.workflowsApi.startForContract).not.toHaveBeenCalled();
  });

  it("says there is no workflow, and the chip opens the Lifecycle tab", async () => {
    mount();
    const chip = await screen.findByRole("button", { name: "No workflow" });
    expect(screen.queryByTestId("lifecycle-stub")).toBeNull();
    fireEvent.click(chip);
    expect(await screen.findByTestId("lifecycle-stub")).toBeVisible();
    expect(h.workflowsApi.startForContract).not.toHaveBeenCalled();
  });

  it("shows the workflow's name instead, once there is one", async () => {
    h.workflowsApi.runForContract.mockResolvedValue({ id: "run-1", flow_name: "Standard contract review", status: "waiting", current_index: 0, steps: [], comments: [] });
    mount();
    expect(await screen.findByTitle("The workflow driving this contract")).toHaveTextContent("Standard contract review");
    expect(screen.queryByRole("button", { name: "No workflow" })).toBeNull();
  });

  it("puts a count on the Review tab only while something is waiting", async () => {
    mount();
    await screen.findByTestId("chat-stub");
    await waitFor(() => expect(reviewTab()).toHaveTextContent("2"));
  });
});

describe("the Comments tab", () => {
  const cm = (id: string, resolved: boolean) => ({ id, contract_id: "c-1", resolved, body: `Comment ${id}`, author_name: "Priya", visibility: "internal", author_kind: "user", anchor: null });
  const commentsTab = () => tabButton(/^Comments/);

  it("is a tab of its own, with the comments for this contract", async () => {
    mount();
    expect(await screen.findByTestId("comments-stub")).toHaveAttribute("data-contract", "c-1");
    expect(commentsTab()).not.toHaveClass("on");
    fireEvent.click(commentsTab());
    expect(commentsTab()).toHaveClass("on");
    expect(screen.getByTestId("comments-stub").parentElement).toHaveStyle({ display: "contents" });
  });

  it("is hidden, not thrown away, while another tab is open, so a half-written comment is still there", async () => {
    mount();
    const stub = await screen.findByTestId("comments-stub");
    expect(stub.parentElement).toHaveStyle({ display: "none" });
    fireEvent.click(commentsTab());
    fireEvent.click(reviewTab());
    expect(screen.getByTestId("comments-stub")).toBe(stub);
    expect(stub.parentElement).toHaveStyle({ display: "none" });
  });

  it("counts only the comments still open, and shows nothing when there are none", async () => {
    h.contractsApi.comments.mockResolvedValue([cm("cm-1", false), cm("cm-2", false), cm("cm-3", true)]);
    mount();
    await screen.findByTestId("chat-stub");
    await waitFor(() => expect(commentsTab()).toHaveTextContent("2"));
  });

  it("has no count when every comment is resolved", async () => {
    h.contractsApi.comments.mockResolvedValue([cm("cm-1", true)]);
    mount();
    await screen.findByTestId("chat-stub");
    await waitFor(() => expect(h.contractsApi.comments).toHaveBeenCalledWith("c-1"));
    expect(commentsTab().textContent).toBe("Comments");
  });

  it("opens with the comment in focus when text that carries one is clicked, or one is written, in the contract", async () => {
    mount();
    await screen.findByTestId("chat-stub");
    fireEvent.click(screen.getByRole("button", { name: "document → comment" }));
    expect(commentsTab()).toHaveClass("on");
    expect(screen.getByTestId("comments-stub")).toHaveAttribute("data-focus", "cm-2");
    // Once the tab has brought it into view, the page forgets it, so reopening the tab does not jump again.
    fireEvent.click(screen.getByRole("button", { name: "comments focused" }));
    expect(screen.getByTestId("comments-stub")).toHaveAttribute("data-focus", "");
  });

  it("is where the Lifecycle tab's \"comments still to resolve\" line leads", async () => {
    mount();
    await screen.findByTestId("chat-stub");
    fireEvent.click(tabButton(/^Lifecycle/));
    fireEvent.click(screen.getByRole("button", { name: "lifecycle → comments" }));
    expect(commentsTab()).toHaveClass("on");
  });

  it("asks for the comments of the contract once, sharing the request with the document and the tab", async () => {
    mount();
    await screen.findByTestId("chat-stub");
    await waitFor(() => expect(h.contractsApi.comments).toHaveBeenCalledTimes(1));
  });
});

describe("how the contract is shown", () => {
  async function openPage() {
    mount();
    await screen.findByTestId("chat-stub");
    return await screen.findByTestId("document");
  }

  it("starts on Markup, and switches to Final and Original without deciding anything", async () => {
    const doc = await openPage();
    expect(doc).toHaveAttribute("data-mode", "markup");
    fireEvent.click(screen.getByRole("button", { name: "Final" }));
    expect(screen.getByTestId("document")).toHaveAttribute("data-mode", "final");
    expect(screen.getByRole("button", { name: "Final" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Markup" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByRole("button", { name: "Original" }));
    expect(screen.getByTestId("document")).toHaveAttribute("data-mode", "original");
    expect(h.contractsApi.acceptEdit).not.toHaveBeenCalled();
    expect(h.contractsApi.rejectEdit).not.toHaveBeenCalled();
  });

  it("zooms in and out in steps of ten percent, and stops at the ends", async () => {
    await openPage();
    expect(screen.getByText("100%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(screen.getByText("110%")).toBeInTheDocument();
    expect(screen.getByTestId("document")).toHaveAttribute("data-zoom", "1.1");
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(screen.getByText("80%")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zoom out" })).toBeDisabled();
  });

  it("has nothing to step through when there are no suggested changes", async () => {
    await openPage();
    expect(screen.getByRole("button", { name: "Next suggestion" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Previous suggestion" })).toBeDisabled();
  });

  describe("with suggested changes", () => {
    beforeEach(() => {
      h.contractsApi.edits.mockResolvedValue([proposedEdit("e1", "d1"), proposedEdit("e2", "d2")]);
    });
    // The document draws each change as a span with this id; here the stand-in does not, so they are added.
    const markChanges = () =>
      document.querySelector(".docpane")!.insertAdjacentHTML("beforeend", '<span id="edit-e1" data-st="proposed"></span><span id="edit-e2" data-st="proposed"></span>');
    const focusedCard = () => document.querySelector(".rev.focus");

    it("steps to the next and previous change, opens Review on it, and wraps around", async () => {
      await openPage();
      await waitFor(() => expect(screen.getByRole("button", { name: "Next suggestion" })).toBeEnabled());
      markChanges();
      fireEvent.click(screen.getByRole("button", { name: "Next suggestion" }));
      expect(reviewTab()).toHaveClass("on");
      await screen.findByText("Issue d1");
      expect(focusedCard()).toHaveTextContent("Issue d1");
      fireEvent.click(screen.getByRole("button", { name: "Next suggestion" }));
      expect(focusedCard()).toHaveTextContent("Issue d2");
      fireEvent.click(screen.getByRole("button", { name: "Next suggestion" }));
      expect(focusedCard()).toHaveTextContent("Issue d1");
      fireEvent.click(screen.getByRole("button", { name: "Previous suggestion" }));
      expect(focusedCard()).toHaveTextContent("Issue d2");
    });

    it("says so when no change is marked in the page", async () => {
      await openPage();
      await waitFor(() => expect(screen.getByRole("button", { name: "Next suggestion" })).toBeEnabled());
      fireEvent.click(screen.getByRole("button", { name: "Next suggestion" }));
      expect(h.notify).toHaveBeenCalledWith("No suggested changes are marked in the page.", "info");
    });
  });
});

describe("the tabs used less often", () => {
  async function open() {
    mount();
    await screen.findByTestId("chat-stub");
  }
  const more = () => screen.getByRole("button", { name: /^More/ });

  it("keeps Clauses, Sources and Parties behind one More button", async () => {
    await open();
    expect(screen.queryByRole("button", { name: /^Parties/ })).toBeNull();
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(more());
    const items = within(screen.getByRole("menu")).getAllByRole("menuitem").map((i) => i.textContent);
    expect(items).toEqual(["Clauses", "Sources", "Parties"]);
    expect(more()).toHaveAttribute("aria-expanded", "true");
  });

  it("opens the chosen tab, closes the menu, and names the tab on the button", async () => {
    await open();
    fireEvent.click(more());
    fireEvent.click(screen.getByRole("menuitem", { name: "Parties" }));
    expect(screen.queryByRole("menu")).toBeNull();
    expect(await screen.findByText("New counterparty")).toBeInTheDocument();
    const current = screen.getByRole("button", { name: /^Parties/ });
    expect(current).toHaveClass("on");
    expect(current).toHaveAttribute("aria-haspopup", "menu");
    expect(tabButton(/^Assistant/)).not.toHaveClass("on");
  });

  it("opens Sources and shows what the agent reads", async () => {
    await open();
    fireEvent.click(more());
    fireEvent.click(screen.getByRole("menuitem", { name: "Sources" }));
    expect(await screen.findByText(/The agent reads the live document text/)).toBeInTheDocument();
  });

  it("explains in Sources why a risk stays high after every suggestion was decided", async () => {
    // Round 1 W5: "2 of 2 decided" and green ticks beside "risk is high" read as a contradiction.
    h.contractsApi.risk.mockResolvedValue(riskOf("high"));
    await open();
    fireEvent.click(more());
    fireEvent.click(screen.getByRole("menuitem", { name: "Sources" }));
    expect(await screen.findByText(/Overall risk is high\. It comes from the playbook review of this text\. An issue you decided to keep as it is still counts/)).toBeInTheDocument();
    expect(screen.getByText(/only a fix that goes into the text lowers it/)).toBeInTheDocument();
  });

  it("says why the risk is not known in Sources, never a level", async () => {
    // Guards: Sources described the retired per-clause AI score whatever the band was.
    h.contractsApi.risk.mockResolvedValue(riskOf("unknown", { note: "The text changed since the last review." }));
    await open();
    fireEvent.click(more());
    fireEvent.click(screen.getByRole("menuitem", { name: "Sources" }));
    expect(await screen.findByText(/Not reviewed — needs a person\. The text changed since the last review\./)).toBeInTheDocument();
  });

  it("closes on Escape and on a click elsewhere, without changing the tab", async () => {
    await open();
    fireEvent.click(more());
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(more());
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(tabButton(/^Assistant/)).toHaveClass("on");
  });

  it("shows how many parties there are, in the menu", async () => {
    h.contractsApi.parties.mockResolvedValue([{ id: "p1", name: "Acme Ltd", party_type: "counterparty", contact_email: null }]);
    await open();
    fireEvent.click(more());
    await waitFor(() => expect(screen.getByRole("menuitem", { name: /^Parties/ })).toHaveTextContent("1"));
  });

  it("leaves the chat alive while a less-used tab is open", async () => {
    await open();
    const chat = screen.getByTestId("chat-stub");
    fireEvent.click(more());
    fireEvent.click(screen.getByRole("menuitem", { name: "Sources" }));
    expect(screen.getByTestId("chat-stub")).toBe(chat);
    expect(chat.parentElement).toHaveStyle({ display: "none" });
  });
});

describe("the AI review that was asked for", () => {
  const withAuto = (auto: unknown) =>
    ({ ...reviewStatus({ status: "succeeded", source: "ai", contract_version_id: "v-1" }), auto_review: auto }) as unknown as ReviewStatusResponse;
  const interrupted = { state: "interrupted", reason: "failed", since: null, job_id: "j-1" };
  const running = () => ({ state: "running", reason: null, since: new Date().toISOString(), job_id: "j-1" });

  it("tells the chat the review was interrupted, and runs it again from there", async () => {
    h.contractsApi.reviewStatus.mockResolvedValue(withAuto(interrupted));
    h.contractsApi.retryReview.mockResolvedValue({ status: "queued", job_id: "j-2", workflow_resumed: false });
    const { invalidate } = mount();
    const banner = await screen.findByTestId("chat-banner");
    await waitFor(() => expect(within(banner).getByRole("alert")).toHaveTextContent("The AI review was interrupted"));
    expect(screen.getByTestId("chat-intro")).toHaveTextContent("interrupted");
    fireEvent.click(within(banner).getByRole("button", { name: "Retry review" }));
    await waitFor(() => expect(h.contractsApi.retryReview).toHaveBeenCalledWith("c-1"));
    await waitFor(() => expect(keysOf(invalidate)).toContainEqual(["review-status", "c-1"]));
  });

  it("tells the chat a review is running, rather than that none has been run", async () => {
    h.contractsApi.reviewStatus.mockResolvedValue(withAuto(running()));
    mount();
    const banner = await screen.findByTestId("chat-banner");
    await waitFor(() => expect(within(banner).getByRole("status")).toHaveTextContent("AI review in progress"));
    expect(screen.getByTestId("chat-intro")).toHaveTextContent("reviewing this draft against the playbook now");
    expect(screen.getByTestId("chat-intro")).not.toHaveTextContent("No playbook review has run");
  });

  it("tells the chat a re-check of accepted changes only lists findings and promises no suggestions", async () => {
    h.contractsApi.reviewStatus.mockResolvedValue(withAuto({ ...running(), after: "accepted_edits" }));
    mount();
    const banner = await screen.findByTestId("chat-banner");
    await waitFor(() => expect(within(banner).getByRole("status")).toHaveTextContent("Re-checking your changes"));
    const intro = screen.getByTestId("chat-intro");
    expect(intro).toHaveTextContent("re-checking your changes against the playbook now");
    expect(intro).not.toHaveTextContent("suggested changes appear");
  });

  it("puts nothing above the chat when no review is pending", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-intro")).toHaveTextContent("I reviewed this draft."));
    expect(within(screen.getByTestId("chat-banner")).queryByTestId("review-notice")).toBeNull();
  });

  it("keeps asking while the review runs, fetches what it wrote when it ends, then stops asking", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      h.contractsApi.reviewStatus.mockResolvedValue(withAuto(running()));
      const { invalidate } = mount();
      await waitFor(() => expect(h.contractsApi.reviewStatus).toHaveBeenCalledTimes(1));
      await act(async () => { await vi.advanceTimersByTimeAsync(5_100); });
      await waitFor(() => expect(h.contractsApi.reviewStatus.mock.calls.length).toBeGreaterThanOrEqual(2));

      // The review ends.
      h.contractsApi.reviewStatus.mockResolvedValue(withAuto(null));
      invalidate.mockClear();
      await act(async () => { await vi.advanceTimersByTimeAsync(5_100); });
      await waitFor(() => expect(keysOf(invalidate)).toContainEqual(["contract-risk", "c-1"]));
      for (const k of [["contract-edits", "c-1"], ["contract-devs", "c-1"], ["flow-run-contract", "c-1"], ["issue-fix-job", "c-1"]]) {
        expect(keysOf(invalidate)).toContainEqual(k);
      }

      // And it stops asking.
      await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
      const settled = h.contractsApi.reviewStatus.mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
      expect(h.contractsApi.reviewStatus.mock.calls.length).toBe(settled);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not ask again while the review is interrupted: nothing changes until someone retries", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      h.contractsApi.reviewStatus.mockResolvedValue(withAuto(interrupted));
      mount();
      await waitFor(() => expect(h.contractsApi.reviewStatus).toHaveBeenCalledTimes(1));
      await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
      expect(h.contractsApi.reviewStatus).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
