import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ContractTypePicker } from "./contract-type-picker";

const h = vi.hoisted(() => ({ notify: vi.fn(), api: { types: vi.fn(), setType: vi.fn() } }));
vi.mock("@/lib/endpoints", () => ({ contractsApi: h.api }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

function mount(props: { current?: string | null; suggested?: string | null } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  render(<QueryClientProvider client={qc}><ContractTypePicker contractId="c-1" current={props.current ?? null} suggested={props.suggested ?? null} /></QueryClientProvider>);
  return invalidate;
}

beforeEach(() => {
  vi.resetAllMocks();
  h.api.types.mockResolvedValue([{ value: "NDA", label: "NDA" }, { value: "MSA", label: "Master services (MSA)" }]);
});

describe("ContractTypePicker", () => {
  it("keeps Save disabled until a type is picked, so a blank type can never be saved", async () => {
    mount();
    await screen.findByRole("option", { name: "NDA" });
    expect(screen.getByRole("button", { name: "Save type" })).toBeDisabled();
  });

  it("saves the chosen value, refreshes the lists and toasts", async () => {
    h.api.setType.mockResolvedValue({ id: "c-1" });
    const invalidate = mount();
    await screen.findByRole("option", { name: "NDA" });
    fireEvent.change(screen.getByLabelText("Contract type"), { target: { value: "MSA" } });
    fireEvent.click(screen.getByRole("button", { name: "Save type" }));
    await waitFor(() => expect(h.api.setType).toHaveBeenCalledWith("c-1", "MSA"));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Contract type saved", "success"));
    const keys = invalidate.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey[0]);
    expect(keys).toEqual(expect.arrayContaining(["contract", "contracts-type-needed", "contracts-list"]));
  });

  it("preselects the suggestion so a stale list entry isn't what gets saved", async () => {
    mount({ suggested: "NDA" });
    await waitFor(() => expect(screen.getByLabelText("Contract type")).toHaveValue("NDA"));
    expect(screen.getByRole("button", { name: "Save type" })).toBeEnabled();
  });

  it("shows the server's message when saving fails", async () => {
    h.api.setType.mockRejectedValue(new Error("Not allowed"));
    mount({ suggested: "NDA" });
    await waitFor(() => expect(screen.getByLabelText("Contract type")).toHaveValue("NDA"));
    fireEvent.click(screen.getByRole("button", { name: "Save type" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Not allowed", "error"));
  });
});
