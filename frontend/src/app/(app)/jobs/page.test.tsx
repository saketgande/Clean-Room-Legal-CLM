import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import JobsPage from "./page";
import type { JobCounts, JobRun } from "@/lib/types";

// The Background Jobs page: the cards count EVERY job (the server counts them), the list can be filtered
// and paged, and a job's progress bar shows the percent the worker wrote.

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  jobsApi: { list: vi.fn(), summary: vi.fn(), cancel: vi.fn(), run: vi.fn() },
}));

vi.mock("@/lib/endpoints", () => ({ jobsApi: h.jobsApi }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const job = (over: Partial<JobRun> = {}): JobRun => ({
  id: "j-1", org_id: "o", job_type: "clause_extraction", resource_type: "contract", resource_id: "c-1",
  idempotency_key: null, status: "cancelled", progress: 0, started_at: null, finished_at: null,
  error_message: null, error_stack: null, attempt_count: 1, celery_task_id: null, metadata_json: {},
  created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z", ...over,
});

const counts = (over: Partial<JobCounts> = {}): JobCounts => ({
  queued: 0, running: 0, succeeded: 0, failed: 0, cancelled: 0, total: 0, ...over,
});

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <JobsPage />
    </QueryClientProvider>,
  );
}

const card = (label: string) => screen.getByText(label, { selector: "p" }).parentElement as HTMLElement;

beforeEach(() => {
  vi.resetAllMocks();
  h.jobsApi.summary.mockResolvedValue(counts());
  h.jobsApi.list.mockResolvedValue([]);
});

describe("the cards", () => {
  it("show the server's count over every job, not the rows on screen", async () => {
    // One page of 100 cancelled jobs is on screen, but older ones succeeded and failed.
    h.jobsApi.summary.mockResolvedValue(counts({ succeeded: 6, failed: 4, cancelled: 120, total: 130 }));
    h.jobsApi.list.mockResolvedValue(Array.from({ length: 100 }, (_, i) => job({ id: `j-${i}`, resource_id: `c-${i}` })));
    mount();
    await waitFor(() => expect(card("Succeeded")).toHaveTextContent("6"));
    expect(card("Failed")).toHaveTextContent("4");
    expect(card("Cancelled")).toHaveTextContent("120");
  });

  it("show a dash, not a false zero, until the count has arrived", () => {
    h.jobsApi.summary.mockReturnValue(new Promise(() => {}));
    mount();
    expect(card("Succeeded")).toHaveTextContent("—");
    expect(card("Succeeded")).not.toHaveTextContent("0");
  });
});

describe("the list", () => {
  it("asks for the first page and offers older jobs only when a full page came back", async () => {
    h.jobsApi.list.mockResolvedValueOnce(Array.from({ length: 100 }, (_, i) => job({ id: `j-${i}`, resource_id: `c-${i}` })));
    h.jobsApi.list.mockResolvedValueOnce([job({ id: "j-old", resource_id: "c-old", status: "succeeded" })]);
    mount();
    const more = await screen.findByRole("button", { name: "Show older jobs" });
    expect(h.jobsApi.list).toHaveBeenLastCalledWith({ status: undefined, limit: 100, offset: 0 });
    fireEvent.click(more);
    await waitFor(() => expect(h.jobsApi.list).toHaveBeenLastCalledWith({ status: undefined, limit: 100, offset: 100 }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Show older jobs" })).toBeNull());
    expect(screen.getByText("c-old")).toBeInTheDocument();
  });

  it("does not offer older jobs when everything fit on one page", async () => {
    h.jobsApi.list.mockResolvedValue([job()]);
    mount();
    await screen.findByText("c-1");
    expect(screen.queryByRole("button", { name: "Show older jobs" })).toBeNull();
  });

  it("filters by status on the server and says so when none match", async () => {
    h.jobsApi.list.mockImplementation(async (p: { status?: string }) => (p.status === "failed" ? [] : [job()]));
    mount();
    await screen.findByText("c-1");
    fireEvent.click(within(screen.getByRole("group", { name: "Filter jobs by status" })).getByRole("button", { name: "Failed" }));
    await screen.findByText("No failed jobs");
    expect(h.jobsApi.list).toHaveBeenLastCalledWith({ status: "failed", limit: 100, offset: 0 });
  });

  it("says so when there are no jobs at all", async () => {
    mount();
    await screen.findByText("No background jobs");
  });
});

describe("a job's progress bar", () => {
  it("shows the percent the worker wrote: 5 is 5%, not finished", async () => {
    h.jobsApi.list.mockResolvedValue([job({ status: "running", progress: 5 })]);
    mount();
    await screen.findByText("5%");
    expect(screen.queryByText("100%")).toBeNull();
  });
});
