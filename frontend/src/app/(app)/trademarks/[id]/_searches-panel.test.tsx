import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Trademark, TrademarkSearchRun } from "@/lib/types";
import { SearchesPanel } from "./_searches-panel";

// The Searches tab's two actions: "Re-run" starts a new detailed search, "Review" opens a run's risk rating.

const h = vi.hoisted(() => ({ searchRuns: vi.fn() }));
vi.mock("@/lib/endpoints", () => ({ trademarksApi: { searchRuns: h.searchRuns, exportSearchRun: vi.fn(), updateSearchRun: vi.fn() } }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: vi.fn() }) }));
vi.mock("@/app/(app)/trademarks/intake/_search-similar-modal", () => ({
  SearchSimilarModal: () => <div role="dialog">search results</div>,
}));

const trademark = { id: "t-1", name: "ZQ", description: null, trademark_type: "word_mark", jurisdictions: ["IN"] } as unknown as Trademark;
const run = { id: "run-1", purpose: "detailed", risk_level: null, notes: null, created_at: "2026-10-12T10:00:00Z", summary: { high_risk_count: 0, medium_risk_count: 1, low_risk_count: 2 } } as unknown as TrademarkSearchRun;

function mount() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SearchesPanel trademark={trademark} />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.resetAllMocks());

describe("SearchesPanel", () => {
  it("calls the search action Re-run and the rating action Review", async () => {
    h.searchRuns.mockResolvedValue([run]);
    mount();

    expect(await screen.findByRole("button", { name: "Review" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Re-run" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run detailed search" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Rate" })).toBeNull();
  });

  it("Review opens the run's risk form and the same button then reads Close", async () => {
    h.searchRuns.mockResolvedValue([run]);
    mount();

    fireEvent.click(await screen.findByRole("button", { name: "Review" }));
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save rating/ })).toBeInTheDocument();
  });

  it("Re-run starts the search, and is there even before the first run", async () => {
    h.searchRuns.mockResolvedValue([]);
    mount();

    expect(await screen.findByText("Use Re-run to start the risk mapping.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Re-run" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
