import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ObligationsPage from "./page";
import type { DateState, Obligation, ObligationSummary } from "@/lib/types";

// The Obligations page: the tiles count every obligation the person may see (from the server's summary, over the same
// query as the list), "Needs a date" is a tile and a filter, and a row with no date says why by the server's date state.

const h = vi.hoisted(() => ({
  perms: ["obligation:read", "obligation:update"] as string[],
  obligationsApi: { list: vi.fn(), summary: vi.fn(), update: vi.fn(), complete: vi.fn(), runReminders: vi.fn() },
  contractsApi: { update: vi.fn() },
  intakeApi: { list: vi.fn() },
}));

vi.mock("@/lib/endpoints", () => ({ obligationsApi: h.obligationsApi, contractsApi: h.contractsApi, intakeApi: h.intakeApi }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { full_name: "Erin", permissions: h.perms } }) }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: vi.fn() }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/obligations", useSearchParams: () => new URLSearchParams() }));
vi.mock("next/link", () => ({ default: (p: { href: string; children: ReactNode; className?: string }) => <a href={p.href} className={p.className}>{p.children}</a> }));

const summary = (over: Partial<ObligationSummary> = {}): ObligationSummary => ({
  total: 111, by_status: { open: 111, due_soon: 0, overdue: 0, completed: 0, cancelled: 0 },
  dates: { dated: 1, waiting_event: 12, needs_person: 2, no_fixed_date: 53, not_read: 43 }, needs_date: 45,
  line: "1 dated, 12 waiting for an event, 45 need a date, 53 no fixed date", ...over,
});

const row = (id: string, date_state: DateState, over: Partial<Obligation> = {}): Obligation => ({
  id, org_id: "o", contract_id: "c-1", contract_version_id: null, contract_title: "Acme MSA", counterparty_name: "Acme",
  owner_user_id: null, owner_name: null, responsible_party: "Supplier", obligation_type: "payment", description: `Duty ${id}`,
  due_date: date_state === "dated" ? "2030-01-15" : null, recurrence: null, status: "open", source_citation: null,
  metadata_json: {}, deadline: null, date_state, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z", ...over,
}) as Obligation;

// The page opens grouped by request; the calendar is the other view.
function mount(view: "requests" | "calendar" = "requests") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const out = render(<QueryClientProvider client={qc}><ObligationsPage /></QueryClientProvider>);
  if (view === "calendar") fireEvent.click(screen.getByRole("tab", { name: /Calendar/ }));
  return out;
}
// Request cards start shut, as they do for a person; this opens them all.
async function mountOpen() {
  const out = mount();
  const heads = await waitFor(() => {
    const found = document.querySelectorAll(".obghd");
    if (!found.length) throw new Error("no request cards yet");
    return [...found];
  });
  heads.forEach((hd) => fireEvent.click(hd));
  return out;
}
const lastListParams = () => h.obligationsApi.list.mock.calls.at(-1)?.[0];
const tile = (name: RegExp) => screen.getByRole("button", { name });

beforeEach(() => {
  vi.resetAllMocks();
  h.perms = ["obligation:read", "obligation:update"];
  h.obligationsApi.list.mockResolvedValue([row("a", "needs_person"), row("b", "waiting_event"), row("c", "not_read")]);
  h.obligationsApi.summary.mockResolvedValue(summary());
  h.intakeApi.list.mockResolvedValue([]);
});

describe("the tiles", () => {
  it("count every obligation from the summary, not the rows on screen", async () => {
    /** Guards the old undercount: tiles were tallied from the 200 rows loaded, so 111 read as however many were on screen. */
    await mountOpen();
    expect(await screen.findByText("111")).toBeInTheDocument();
    expect(screen.getAllByText("Duty a").length).toBe(1); // three rows are listed, yet the Open tile says 111
  });

  it("show the one plain line from the server under them", async () => {
    await mountOpen();
    expect(await screen.findByText("1 dated, 12 waiting for an event, 45 need a date, 53 no fixed date")).toBeInTheDocument();
  });

  it("have a 'Needs a date' tile that filters the list to what it counts, and a second press clears it", async () => {
    /** Guards the queue disagreeing with the tile: the filtered list IS the queue. */
    await mountOpen();
    const needs = await screen.findByRole("button", { name: /Needs a date/ });
    await waitFor(() => expect(within(needs).getByText("45")).toBeInTheDocument());
    expect(needs).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(needs);
    await waitFor(() => expect(lastListParams()).toMatchObject({ date_state: "needs_date" }));
    expect(tile(/Needs a date/)).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(tile(/Needs a date/));
    await waitFor(() => expect(lastListParams()).not.toHaveProperty("date_state"));
  });
});

describe("the Date filter", () => {
  it("sends the chosen state to the server and nothing for 'All dates'", async () => {
    await mountOpen();
    const select = await screen.findByLabelText("Date");
    fireEvent.change(select, { target: { value: "waiting_event" } });
    await waitFor(() => expect(lastListParams()).toMatchObject({ date_state: "waiting_event", limit: 200 }));
    fireEvent.change(select, { target: { value: "" } });
    await waitFor(() => expect(lastListParams()).not.toHaveProperty("date_state"));
  });
});

describe("a row with no date says why", () => {
  it("by the server's state: waiting, needs a date, not read for dates", async () => {
    /** Guards 'On trigger' standing for a duty that needs a person and for a row never read for dates. */
    await mountOpen();
    await screen.findByText("Duty a");
    const rows = within(document.querySelector(".oblist") as HTMLElement);
    expect(rows.getByText("Waiting for event")).toBeInTheDocument();
    expect(rows.getByText("Not read for dates")).toBeInTheDocument();
    expect(rows.getByText(/set the date with Edit/)).toBeInTheDocument();
    expect(rows.getAllByText("Needs a date").length).toBe(1);
    expect(rows.queryByText("On trigger")).toBeNull();
  });

  it("a dated row still shows its date", async () => {
    h.obligationsApi.list.mockResolvedValue([row("d", "dated")]);
    await mountOpen();
    expect((await screen.findAllByText(/2030/)).length).toBeGreaterThan(0); // the row's date, and the card's "Next due"
  });
});

describe("when the counts are not known", () => {
  it("shows a dash, never a 0 that looks like an all-clear, until the summary arrives", async () => {
    /** Guards 'Needs a date 0' reading as 'nothing needs a date' while the counts load or fail. */
    let release: (s: ObligationSummary) => void = () => {};
    h.obligationsApi.summary.mockReturnValue(new Promise<ObligationSummary>((resolve) => { release = resolve; }));
    await mountOpen();
    const needs = await screen.findByRole("button", { name: /Needs a date/ });
    expect(within(needs).getByText("–")).toBeInTheDocument();
    expect(screen.queryByText("0")).toBeNull();
    release(summary());
    await waitFor(() => expect(within(needs).getByText("45")).toBeInTheDocument());
  });

  it("says so when the summary fails, and still shows the list", async () => {
    h.obligationsApi.summary.mockRejectedValue(new Error("boom"));
    await mountOpen();
    expect(await screen.findByText("Duty a")).toBeInTheDocument();
    expect(await screen.findByText(/counts above could not be loaded/)).toBeInTheDocument();
    expect(within(tile(/Needs a date/)).getByText("–")).toBeInTheDocument();
  });
});

describe("a date filter with a finished status", () => {
  it("says the date filters count open obligations only", async () => {
    /** Guards an empty list that looks like nothing is wrong: the Date filter only ever counts open obligations. */
    await mountOpen();
    await screen.findByText("Duty a");
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "needs_date" } });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "completed" } });
    expect(await screen.findByText(/open obligations only/)).toBeInTheDocument();
  });
});

describe("the open row's timing line", () => {
  it("tells a view-only person who has to set a missing date, not to press an Edit they do not have", async () => {
    h.perms = ["obligation:read"];
    h.obligationsApi.list.mockResolvedValue([row("a", "needs_person")]);
    await mountOpen();
    fireEvent.click(await screen.findByText("Duty a"));
    expect(await screen.findByText(/someone who can edit this contract needs to set it/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });
});

describe("a capped list", () => {
  it("says how many there are in all", async () => {
    /** Guards 'Showing 200' looking like everything. */
    h.obligationsApi.list.mockResolvedValue(Array.from({ length: 200 }, (_, i) => row(`r${i}`, "no_fixed_date")));
    h.obligationsApi.summary.mockResolvedValue(summary({ total: 340 }));
    await mountOpen();
    expect(await screen.findByText(/Showing 200 of 340/)).toBeInTheDocument();
  });

  it("says nothing about a cap when the list is complete", async () => {
    await mountOpen();
    await screen.findByText("Duty a");
    expect(screen.queryByText(/Showing 200/)).toBeNull();
  });
});

describe("the calendar", () => {
  const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

  it("puts a dated duty on its day and lists it in the day panel", async () => {
    /** Guards a calendar that shows the month but not what falls due in it. */
    h.obligationsApi.list.mockResolvedValue([row("e", "dated", { due_date: today(), obligation_type: "payment", description: "Pay the licence fee" })]);
    mount("calendar");
    expect(await screen.findByText("Pay the licence fee")).toBeInTheDocument(); // today is selected, so its duties are listed
    expect(screen.getByText("1 obligation due")).toBeInTheDocument();
    expect(screen.getByText("payment", { selector: ".dchip .trunc" })).toBeInTheDocument();
  });

  it("points to duties with no fixed date and opens them under their requests", async () => {
    /** Guards duties without a date vanishing from the calendar view. */
    mount("calendar");
    const note = await screen.findByRole("button", { name: /without a fixed date/ });
    fireEvent.click(note);
    await waitFor(() => expect(document.querySelector(".obghd")).not.toBeNull()); // the request cards, shut until opened
    expect(screen.queryByText("Duty a")).toBeNull();
    expect(screen.getByRole("tab", { name: /By request/ })).toHaveAttribute("aria-selected", "true");
  });

  it("moves between months", async () => {
    mount("calendar");
    const heading = await screen.findByRole("heading", { level: 2 });
    const before = heading.textContent;
    fireEvent.click(screen.getByRole("button", { name: "Next month" }));
    expect((await screen.findByRole("heading", { level: 2 })).textContent).not.toBe(before);
  });
});

describe("grouped under the request", () => {
  const rq = { id: "ir-1", ref: "REQ-7001", subject: "NDA with Acme", type_label: "NDA", contract_id: "c-1", requester_name: "Pat", created_at: "2026-10-01T00:00:00Z", workflow: [] };

  it("heads each card with the request, not the contract, and keeps one request's duties together", async () => {
    /** Guards the old pile: every obligation sat in one place, with nothing saying which request it belonged to. */
    h.intakeApi.list.mockResolvedValue([rq]);
    h.obligationsApi.list.mockResolvedValue([row("a", "needs_person"), row("b", "waiting_event"), row("x", "dated", { contract_id: "c-2", contract_title: "Other MSA" })]);
    await mountOpen();
    const heading = await screen.findByRole("link", { name: "NDA with Acme" });
    expect(heading).toHaveAttribute("href", "/intake?open=ir-1");
    const card = heading.closest(".obgroup") as HTMLElement;
    expect(card).toHaveTextContent("REQ-7001");
    expect(within(card).getByText("Duty a")).toBeInTheDocument();
    expect(within(card).getByText("Duty b")).toBeInTheDocument();
    expect(within(card).queryByText("Duty x")).toBeNull(); // another contract, another card
    expect(screen.getByRole("link", { name: "Acme MSA" })).toBeInTheDocument(); // the contract is a chip
  });
});
