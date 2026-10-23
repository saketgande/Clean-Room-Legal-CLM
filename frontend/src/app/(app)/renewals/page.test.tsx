import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import RenewalsPage from "./page";
import type { ContractResponse, RenewalEvent } from "@/lib/types";

// Renewals vs Contracts: the Contracts screen counts running contracts that end within 90 days. Renewals only
// lists contracts that have a renewal record, so it says which of those are missing instead of silently showing 0.

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  renewalsApi: { list: vi.fn(), runWindowCheck: vi.fn() },
  contractsApi: { list: vi.fn(), update: vi.fn() },
}));

vi.mock("@/lib/endpoints", () => ({ renewalsApi: h.renewalsApi, contractsApi: h.contractsApi }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));
vi.mock("next/link", () => ({ default: (p: { href: string; children: ReactNode }) => <a href={p.href}>{p.children}</a> }));

const isoIn = (days: number) => {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + days);
  const p = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

const contract = (over: Partial<ContractResponse>): ContractResponse =>
  ({ id: "c-1", title: "Globex MSA", lifecycle_stage: "active", expiration_date: isoIn(20), ...over }) as ContractResponse;

const renewal = (over: Partial<RenewalEvent>): RenewalEvent =>
  ({
    id: "rn-1", org_id: "o", contract_id: "c-9", contract_title: "Far Away Ltd", contract_version_id: null,
    expiration_date: isoIn(400), notice_date: isoIn(300), renewal_window_starts_at: null, owner_user_id: null,
    decision: "undecided", decision_note: null, next_expiration_date: null, formulas: {}, date_reason: null,
    date_conflict: null, dates_status: "confirmed", metadata_json: {}, created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z", ...over,
  }) as RenewalEvent;

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RenewalsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  h.renewalsApi.list.mockResolvedValue([renewal({})]);
  h.contractsApi.list.mockResolvedValue([]);
});

describe("running contracts that expire soon but have no renewal record", () => {
  it("are named, with how many days are left and a link to the contract", async () => {
    h.contractsApi.list.mockResolvedValue([
      contract({ id: "c-1", title: "Globex MSA", expiration_date: isoIn(20) }),
      contract({ id: "c-2", title: "Initech NDA", expiration_date: isoIn(60) }),
    ]);
    mount();
    const note = await screen.findByRole("note");
    expect(note).toHaveTextContent("2 running contracts expire within 90 days but have no renewal record yet");
    expect(screen.getByRole("link", { name: "Globex MSA" })).toHaveAttribute("href", "/contracts/c-1");
    expect(note).toHaveTextContent("(20d)");
    expect(note).toHaveTextContent("(60d)");
  });

  it("are not mentioned when the contract has a renewal row, is not running, or ends later", async () => {
    h.renewalsApi.list.mockResolvedValue([renewal({ contract_id: "c-1" })]);
    h.contractsApi.list.mockResolvedValue([
      contract({ id: "c-1" }),
      contract({ id: "c-2", lifecycle_stage: "review" }),
      contract({ id: "c-3", expiration_date: isoIn(200) }),
    ]);
    mount();
    await screen.findByText("Far Away Ltd");
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("use the singular when there is one", async () => {
    h.contractsApi.list.mockResolvedValue([contract({})]);
    mount();
    expect(await screen.findByRole("note")).toHaveTextContent("1 running contract expire within 90 days but has no renewal record yet, so it is not listed");
  });
});
