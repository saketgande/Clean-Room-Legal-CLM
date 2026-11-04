import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { isClearanceRequest } from "@/lib/clearance-case";
import { OpenRequest } from "./_open-request";

// A trademark clearance is worked on its case page in Trademarks; every other request keeps the request
// page. If the case can't be found the request page still opens, so a link never dead-ends.

const h = vi.hoisted(() => ({
  replace: vi.fn(),
  get: vi.fn(),
  byRequest: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: h.replace, push: vi.fn() }) }));
vi.mock("@/lib/endpoints", () => ({ intakeApi: { get: h.get }, trademarksApi: { byRequest: h.byRequest } }));
vi.mock("./_request-overview", () => ({ RequestOverview: ({ id }: { id: string }) => <p>request page {id}</p> }));

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <OpenRequest id="q-1" canManage onBack={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.resetAllMocks());

describe("isClearanceRequest", () => {
  it("is true only for a trademark marker answered yes: the clearance's or a response's", () => {
    expect(isClearanceRequest({ tm_clearance: true, mark: "ZQ" })).toBe(true);
    expect(isClearanceRequest({ tm_response: true, tm_response_for: "t-1" })).toBe(true);
    for (const answers of [null, undefined, {}, { tm_clearance: false }, { tm_response: "yes" }, { tm_clearance: "yes" }, { request_form: "nda" }]) {
      expect(isClearanceRequest(answers)).toBe(false);
    }
  });
});

describe("opening a request", () => {
  it("goes to the trademark's case page for a clearance, replacing the history entry", async () => {
    h.get.mockResolvedValue({ id: "q-1", field_values: { tm_clearance: true } });
    h.byRequest.mockResolvedValue({ trademark_id: "tm-9" });
    mount();

    await waitFor(() => expect(h.replace).toHaveBeenCalledWith("/trademarks/tm-9"));
    expect(h.byRequest).toHaveBeenCalledWith("q-1");
    expect(screen.queryByText("request page q-1")).toBeNull();
  });

  it("shows the request page for any other request, and never asks Trademarks about it", async () => {
    h.get.mockResolvedValue({ id: "q-1", field_values: { request_form: "nda" } });
    mount();

    expect(await screen.findByText("request page q-1")).toBeInTheDocument();
    expect(h.byRequest).not.toHaveBeenCalled();
    expect(h.replace).not.toHaveBeenCalled();
  });

  it("falls back to the request page when the clearance's case can't be opened", async () => {
    h.get.mockResolvedValue({ id: "q-1", field_values: { tm_clearance: true } });
    h.byRequest.mockRejectedValue(new Error("No trademark case for this request"));
    mount();

    expect(await screen.findByText("request page q-1")).toBeInTheDocument();
    expect(h.replace).not.toHaveBeenCalled();
  });
});
