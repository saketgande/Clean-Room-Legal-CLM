import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Trademark } from "@/lib/types";
import { DecisionPanel } from "./_decision-panel";

// The decision already on record is told inside the "Record a decision" card, not in a card of its own.

vi.mock("@/lib/endpoints", () => ({ trademarksApi: { recordDecision: vi.fn() } }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: vi.fn() }) }));

const base = {
  id: "t-1", name: "ZQ", decision: null, decision_conditions: null, decided_at: null,
  case: { request_id: "q-1", step_name: "Final decision", assignee_name: "Legal Two" },
} as unknown as Trademark;

function mount(trademark: Trademark) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DecisionPanel trademark={trademark} />
    </QueryClientProvider>,
  );
}

describe("DecisionPanel", () => {
  it("says there is no decision yet in the same card, with no separate 'Current decision' card", () => {
    mount(base);
    expect(screen.getByRole("heading", { name: "Record a decision" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Current decision" })).toBeNull();
    expect(screen.getByText("No decision recorded yet")).toBeInTheDocument();
  });

  it("shows the decision on record and when it was made inside the card, and the conditions once, in the form", () => {
    mount({ ...base, decision: "conditional", decision_conditions: "Add a disclaimer for NEURO", decided_at: "2026-10-12T10:00:00Z" } as Trademark);
    expect(screen.getByText("Conditional", { selector: "span" })).toBeInTheDocument();
    // The conditions are the form's own pre-filled box; they are not repeated above it.
    expect(screen.getAllByText(/Add a disclaimer for NEURO/)).toHaveLength(1);
    expect(screen.getByText(/Current decision/)).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { name: /decision/i })).toHaveLength(1);
  });
});
