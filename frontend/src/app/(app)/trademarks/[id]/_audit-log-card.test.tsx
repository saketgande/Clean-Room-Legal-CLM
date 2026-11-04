import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AUDIT_LOG_PREVIEW, AuditLogCard } from "./_audit-log-card";

// The audit log is a side card on every page of the case: the latest few entries, "More" for the rest.

const h = vi.hoisted(() => ({ activity: vi.fn() }));
vi.mock("@/lib/endpoints", () => ({ trademarksApi: { activity: h.activity } }));

const entry = (n: number, event_type = "trademark.search_run", actor_name: string | null = "Erin Legal") => ({
  id: `e${n}`, event_type, title: `Entry ${n}`, actor_name, actor_user_id: null,
  created_at: new Date().toISOString(), source: "trademark",
});
// Newest first, as the server sends them.
const many = (count: number) => Array.from({ length: count }, (_, i) => entry(count - i));

function mount() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AuditLogCard trademarkId="t-1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.resetAllMocks());

describe("AuditLogCard", () => {
  it("shows the latest five entries and a count of the rest behind More", async () => {
    h.activity.mockResolvedValue(many(8));
    mount();

    expect(await screen.findByText("Entry 8")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(AUDIT_LOG_PREVIEW);
    expect(screen.getByText("Entry 4")).toBeInTheDocument();
    expect(screen.queryByText("Entry 3")).toBeNull();
    expect(screen.getByRole("button", { name: "More (3)" })).toHaveAttribute("aria-expanded", "false");
  });

  it("More opens the rest, and Show less folds it back to five", async () => {
    h.activity.mockResolvedValue(many(8));
    mount();

    fireEvent.click(await screen.findByRole("button", { name: "More (3)" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(8);
    expect(screen.getByText("Entry 1")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show less" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(AUDIT_LOG_PREVIEW);
    expect(screen.queryByText("Entry 1")).toBeNull();
  });

  it("has no More button when everything fits in the first five", async () => {
    h.activity.mockResolvedValue(many(AUDIT_LOG_PREVIEW));
    mount();

    expect(await screen.findByText("Entry 5")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("names who did each entry (the system when nobody) and its category, with the total in the header", async () => {
    h.activity.mockResolvedValue([entry(2, "trademark.decision", null), entry(1, "trademark.filing")]);
    mount();

    const items = within(await screen.findByRole("list")).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("System");
    expect(items[0]).toHaveTextContent("Decision");
    expect(items[1]).toHaveTextContent("Erin Legal");
    expect(items[1]).toHaveTextContent("Filing");
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("says nothing is logged yet when there are no entries", async () => {
    h.activity.mockResolvedValue([]);
    mount();

    expect(await screen.findByText(/Nothing logged yet/)).toBeInTheDocument();
  });
});
