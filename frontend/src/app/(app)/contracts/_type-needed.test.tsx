import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TypeNeededPanel } from "./_type-needed";

const h = vi.hoisted(() => ({ perms: ["*"] as string[], canEdit: true, typeNeeded: vi.fn() }));
vi.mock("@/lib/endpoints", () => ({ contractsApi: { typeNeeded: h.typeNeeded } }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { permissions: h.perms } }) }));
vi.mock("@/lib/screen-access", () => ({ useScreenAccess: () => ({ canEdit: h.canEdit }) }));
vi.mock("@/components/contract-type-picker", () => ({ ContractTypePicker: () => <div data-testid="type-picker" /> }));

const item = { id: "c1", title: "Mutual NDA - Umbrella", counterparty_name: "Umbrella Corp", stage: "active", contract_type: null, suggested_type: null };
const mount = () => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TypeNeededPanel /></QueryClientProvider>);

beforeEach(() => {
  vi.resetAllMocks();
  h.perms = ["*"];
  h.canEdit = true;
  h.typeNeeded.mockResolvedValue({ items: [item], truncated: false });
});

describe("TypeNeededPanel", () => {
  it("renders nothing with zero items, so a clean portfolio shows no empty strip", async () => {
    h.typeNeeded.mockResolvedValue({ items: [], truncated: false });
    const { container } = mount();
    await waitFor(() => expect(h.typeNeeded).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("is collapsed with a count, then lists title link, counterparty, stage and blank type", async () => {
    mount();
    const head = await screen.findByRole("button", { name: /1 contract needs a type/ });
    expect(screen.queryByText("Umbrella Corp")).toBeNull();
    fireEvent.click(head);
    expect(screen.getByRole("link", { name: "Mutual NDA - Umbrella" })).toHaveAttribute("href", "/contracts/c1");
    expect(screen.getByText("Umbrella Corp")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("blank")).toBeInTheDocument();
    expect(screen.getByTestId("type-picker")).toBeInTheDocument();
  });

  it("offers no picker to someone who cannot edit contracts", async () => {
    h.perms = ["contract:read"];
    mount();
    fireEvent.click(await screen.findByRole("button", { name: /need|needs/ }));
    expect(screen.queryByTestId("type-picker")).toBeNull();
    expect(screen.getByText("Ask someone who can edit contracts")).toBeInTheDocument();
  });
});
