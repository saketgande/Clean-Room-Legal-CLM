import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PartiesRegisterTab } from "./_parties-register";

// A new counterparty must say where it is and how to reach it (round 2, bug 46); an older one can still be edited.

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  partiesApi: { entities: vi.fn(), counterparties: vi.fn(), createCounterparty: vi.fn(), updateCounterparty: vi.fn(), createEntity: vi.fn(), updateEntity: vi.fn() },
}));
vi.mock("@/lib/endpoints", () => ({ partiesApi: h.partiesApi }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const old = { id: "cp-old", name: "Old Ltd", jurisdiction: null, address: null, contact_email: null, active: true, source: "manual", external_ref: null, created_at: "", updated_at: "" };

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><PartiesRegisterTab /></QueryClientProvider>);
}

beforeEach(() => {
  vi.resetAllMocks();
  h.partiesApi.entities.mockResolvedValue([]);
  h.partiesApi.counterparties.mockResolvedValue([old]);
  h.partiesApi.createCounterparty.mockResolvedValue({ ...old, id: "cp-new" });
  h.partiesApi.updateCounterparty.mockResolvedValue(old);
});

const dialog = () => within(screen.getByRole("dialog"));

describe("adding a counterparty in the register", () => {
  it("needs a jurisdiction and a contact email, and says so", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: /New counterparty/ }));
    const add = dialog().getByRole("button", { name: "Add counterparty" });
    fireEvent.change(dialog().getByLabelText(/Legal name/), { target: { value: "Globex Ltd" } });
    expect(dialog().getByText(/Jurisdiction \(required\)/)).toBeInTheDocument();
    expect(dialog().getByText(/Contact email \(required\)/)).toBeInTheDocument();
    expect(add).toBeDisabled();

    fireEvent.change(dialog().getByLabelText(/Jurisdiction/), { target: { value: "India" } });
    expect(add).toBeDisabled();
    fireEvent.change(dialog().getByLabelText(/Contact email/), { target: { value: "legal.globex.com" } });
    expect(add).toBeDisabled();
    expect(dialog().getByText("Enter a full email address, like name@company.com.")).toBeInTheDocument();

    fireEvent.change(dialog().getByLabelText(/Contact email/), { target: { value: "legal@globex.com" } });
    expect(add).toBeEnabled();
    fireEvent.click(add);
    await waitFor(() => expect(h.partiesApi.createCounterparty).toHaveBeenCalledWith(
      expect.objectContaining({ name: "Globex Ltd", jurisdiction: "India", contact_email: "legal@globex.com" })));
  });

  it("does not ask the same of a legal entity of ours", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: /New entity/ }));
    fireEvent.change(dialog().getByLabelText(/Registered legal name/), { target: { value: "Us Ltd" } });
    expect(dialog().getByRole("button", { name: "Add legal entity" })).toBeEnabled();
    expect(dialog().queryByText(/\(required\)/)).toBeNull();
  });
});

describe("editing a counterparty that was made before the rule", () => {
  it("can still be saved without them, but an address that is typed must look like one", async () => {
    mount();
    const row = (await screen.findByText("Old Ltd")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: /Edit/ }));
    const save = dialog().getByRole("button", { name: "Save changes" });
    expect(dialog().queryByText(/\(required\)/)).toBeNull();
    expect(save).toBeEnabled();
    fireEvent.change(dialog().getByLabelText(/Contact email/), { target: { value: "nope" } });
    expect(save).toBeDisabled();
    fireEvent.change(dialog().getByLabelText(/Contact email/), { target: { value: "" } });
    expect(save).toBeEnabled();
  });
});
