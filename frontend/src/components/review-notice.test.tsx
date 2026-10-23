import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ContractReviewNotice, ReviewNoticeView, StepReviewNotice } from "./review-notice";
import { reviewQueryKeys } from "@/lib/review-retry";
import type { AutoReviewState } from "@/lib/types";

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  retryReview: vi.fn(),
  permissions: ["contract:redline", "playbook:run"] as string[],
}));

vi.mock("@/lib/endpoints", () => ({ contractsApi: { retryReview: h.retryReview } }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { full_name: "Legal One", permissions: h.permissions } }) }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const interrupted = (over: Partial<AutoReviewState> = {}): AutoReviewState => ({
  state: "interrupted", reason: "stalled", since: "2026-10-08T07:00:00Z", job_id: "j-1", ...over,
});
const running = (): AutoReviewState => ({ state: "running", reason: null, since: new Date().toISOString(), job_id: "j-1" });

function mount(auto: AutoReviewState | null | undefined, byHand = false) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  const view = render(
    <QueryClientProvider client={qc}>
      <ContractReviewNotice contractId="c-1" auto={auto} byHand={byHand} />
    </QueryClientProvider>,
  );
  return { invalidate, ...view };
}
const keysOf = (spy: { mock: { calls: unknown[][] } }) => spy.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey);

beforeEach(() => {
  vi.resetAllMocks();
  h.permissions = ["contract:redline", "playbook:run"];
});

describe("the review notice", () => {
  it("shows nothing when no review is pending", () => {
    mount(null);
    expect(screen.queryByTestId("review-notice")).toBeNull();
    mount(undefined);
    expect(screen.queryByTestId("review-notice")).toBeNull();
  });

  it("says a review is running, with no button to press", () => {
    mount(running());
    const n = screen.getByRole("status");
    expect(n).toHaveTextContent("AI review in progress");
    expect(n).toHaveTextContent(/Started (just now|\d+m ago)/);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says the review was interrupted, and why, with a Retry button", () => {
    mount(interrupted({ reason: "failed" }));
    const n = screen.getByRole("alert");
    expect(n).toHaveTextContent("The AI review was interrupted");
    expect(n).toHaveTextContent("It stopped with an error before it finished.");
    expect(screen.getByRole("button", { name: "Retry review" })).toBeEnabled();
  });

  it("restarts the review on this contract and refreshes everything it changes", async () => {
    h.retryReview.mockResolvedValue({ status: "queued", job_id: "j-2", workflow_resumed: true });
    const { invalidate } = mount(interrupted());
    fireEvent.click(screen.getByRole("button", { name: "Retry review" }));
    await waitFor(() => expect(h.retryReview).toHaveBeenCalledWith("c-1"));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(expect.stringMatching(/workflow carries on/), "success"));
    expect(keysOf(invalidate)).toEqual(reviewQueryKeys("c-1"));
  });

  it("cannot be pressed twice while the restart is on its way", async () => {
    let resolve!: (v: unknown) => void;
    h.retryReview.mockReturnValue(new Promise((r) => { resolve = r; }));
    mount(interrupted());
    fireEvent.click(screen.getByRole("button", { name: "Retry review" }));
    const busy = await screen.findByRole("button", { name: "Restarting…" });
    expect(busy).toBeDisabled();
    fireEvent.click(busy);
    expect(h.retryReview).toHaveBeenCalledTimes(1);
    resolve({ status: "queued", job_id: "j-2", workflow_resumed: false });
    await waitFor(() => expect(h.notify).toHaveBeenCalled());
  });

  it("tells the truth when a second tab got there first, or the playbook is missing", async () => {
    h.retryReview.mockResolvedValueOnce({ status: "already_running", job_id: "j-2", workflow_resumed: false });
    mount(interrupted());
    fireEvent.click(screen.getByRole("button", { name: "Retry review" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("The review is already running.", "info"));

    h.notify.mockClear();
    h.retryReview.mockResolvedValueOnce({ status: "no_playbook", job_id: null, workflow_resumed: false });
    fireEvent.click(await screen.findByRole("button", { name: "Retry review" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(expect.stringMatching(/No playbook covers/), "error"));
  });

  it("shows the server's refusal and refreshes what is true now", async () => {
    h.retryReview.mockRejectedValue(new Error("Restarting the AI review needs permission to redline and to run playbooks."));
    const { invalidate } = mount(interrupted());
    fireEvent.click(screen.getByRole("button", { name: "Retry review" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Restarting the AI review needs permission to redline and to run playbooks.", "error"));
    expect(keysOf(invalidate)).toContainEqual(["review-status", "c-1"]);
  });

  it("does not offer Retry to someone who may not use it, and says who can", () => {
    h.permissions = ["contract:read"];
    mount(interrupted());
    expect(screen.queryByRole("button", { name: "Retry review" })).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("Ask someone who can redline and run playbooks to retry it.");
  });

  it("offers reviewing by hand where a workflow step is waiting on the person", () => {
    mount(interrupted(), true);
    expect(screen.getByRole("alert")).toHaveTextContent("review the contract yourself and complete the step");
  });
});

describe("the notice as a picture", () => {
  it("draws the button it is given, or the hint when there is none", () => {
    const { rerender } = render(<ReviewNoticeView state="interrupted" title="T" body="B" action={<button>Go</button>} hint="Ask someone." />);
    expect(screen.getByRole("button", { name: "Go" })).toBeInTheDocument();
    expect(screen.queryByText("Ask someone.")).toBeNull();
    rerender(<ReviewNoticeView state="interrupted" title="T" body="B" hint="Ask someone." />);
    expect(screen.getByText("Ask someone.")).toBeInTheDocument();
  });
});

describe("the notice on a workflow step", () => {
  const onRetry = vi.fn();

  it("says the review was interrupted and retries it on a click", () => {
    render(<StepReviewNotice canAct busy={false} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("The AI review was interrupted");
    expect(screen.getByRole("alert")).toHaveTextContent("review the contract yourself and complete the step");
    fireEvent.click(screen.getByRole("button", { name: "Retry review" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("says why it stopped when the contract's review state is known", () => {
    render(<StepReviewNotice auto={interrupted({ reason: "failed" })} canAct busy={false} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("It stopped with an error before it finished.");
  });

  it("does not use a running review's state to say why", () => {
    render(<StepReviewNotice auto={running()} canAct busy={false} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("It stopped before it finished.");
  });

  it("is disabled while the restart is on its way", () => {
    render(<StepReviewNotice canAct busy onRetry={onRetry} />);
    expect(screen.getByRole("button", { name: "Restarting…" })).toBeDisabled();
  });

  it("offers no button to someone who cannot act on the request or lacks the rights", () => {
    const { rerender } = render(<StepReviewNotice canAct={false} busy={false} onRetry={onRetry} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText(/Ask someone who can/)).toBeNull();
    h.permissions = ["intake:read"];
    rerender(<StepReviewNotice canAct busy={false} onRetry={onRetry} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/Ask someone who can redline and run playbooks/)).toBeInTheDocument();
  });
});
