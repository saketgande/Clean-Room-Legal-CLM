import { beforeEach, describe, expect, it, vi } from "vitest";
import { contractsApi, workflowsApi } from "./endpoints";
import { apiFetch } from "./api";

// The two calls that restart an interrupted AI review, checked against the routes the server defines
// (backend: POST /contracts/{id}/review/retry and POST /workflows/runs/{id}/retry-review).
vi.mock("./api", () => ({ apiFetch: vi.fn(), apiDownload: vi.fn() }));

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  vi.mocked(apiFetch).mockResolvedValue({});
});

describe("restarting an interrupted review", () => {
  it("restarts a contract's review with a POST to its review/retry route", async () => {
    await contractsApi.retryReview("c-1");
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith("/contracts/c-1/review/retry", { method: "POST", body: {} });
  });

  it("restarts the review a workflow step stopped on with a POST to the run's retry-review route", async () => {
    await workflowsApi.retryReview("r-1");
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith("/workflows/runs/r-1/retry-review", { method: "POST", body: {} });
  });

  it("reads the review state from the same review-status route as before", async () => {
    await contractsApi.reviewStatus("c-1");
    expect(apiFetch).toHaveBeenCalledWith("/contracts/c-1/review-status");
  });
});
