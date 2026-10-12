import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SlaDashboardTab } from "./_phase1";

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  intakeApi: { slaOps: vi.fn(), list: vi.fn(), slaLegs: vi.fn(), slaScan: vi.fn() },
}));

vi.mock("@/lib/endpoints", () => ({ intakeApi: h.intakeApi }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const ops = { open_total: 5, at_risk: 1, overdue: 2, breaches_7d: 2, workload: [] };

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SlaDashboardTab isAdmin />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  h.intakeApi.slaOps.mockResolvedValue(ops);
  h.intakeApi.list.mockResolvedValue([]);
});

describe("the SLA tiles", () => {
  it("name the breach figure for what it counts, next to the live overdue figure", async () => {
    mount();
    await screen.findByText("Newly overdue (7d)");
    expect(screen.getByText("Overdue")).toBeInTheDocument();
    expect(screen.queryByText("Breaches (7d)")).toBeNull();
  });
});

describe("Run breach scan", () => {
  it("reports the finished requests it closed as well as the escalations", async () => {
    h.intakeApi.slaScan.mockResolvedValue({ escalated: 1, breached: 1, downgraded: 0, completed: 3 });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Run breach scan" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(
      "Scan: 1 newly overdue, 1 escalated, 3 finished requests closed", "success"));
  });
});
