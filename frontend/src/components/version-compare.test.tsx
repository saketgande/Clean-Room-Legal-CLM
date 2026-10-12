import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { VersionCompare } from "./version-compare";
import type { ContractVersionResponse, VersionDiffResponse } from "@/lib/types";

// The compare screen: pick two versions of a contract and see which lines were added and removed.
// It needs the diff endpoint only; every other state (loading, error, nothing changed, cut off) is
// shown rather than assumed away.

const h = vi.hoisted(() => ({ contractsApi: { versionDiff: vi.fn() } }));
vi.mock("@/lib/endpoints", () => ({ contractsApi: h.contractsApi }));

const version = (n: number, over: Partial<ContractVersionResponse> = {}) =>
  ({ id: `v-${n}`, version_number: n, source: "upload", text_snapshot_id: `s-${n}`, is_authoritative: false, ...over }) as unknown as ContractVersionResponse;

const v1 = version(1);
const v2 = version(2);
const v3 = version(3, { is_authoritative: true });

const diff = (over: Partial<VersionDiffResponse> = {}): VersionDiffResponse => ({
  base_version_id: "v-2", base_version_number: 2, target_version_id: "v-3", target_version_number: 3,
  added: 1, removed: 1, truncated: false,
  lines: [
    { type: "gap", text: "3 unchanged lines" },
    { type: "context", text: "Liability: capped." },
    { type: "remove", text: "Term: one year." },
    { type: "add", text: "Term: two years." },
  ],
  ...over,
});

function mount(props: Partial<React.ComponentProps<typeof VersionCompare>> = {}) {
  const onClose = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <VersionCompare contractId="c-1" versions={[v1, v2, v3]} current={v3} open onClose={onClose} {...props} />
    </QueryClientProvider>,
  );
  return { onClose };
}

beforeEach(() => {
  vi.resetAllMocks();
  h.contractsApi.versionDiff.mockResolvedValue(diff());
});

describe("comparing two versions", () => {
  it("starts with the version before the current one against the current one, and shows what changed", async () => {
    mount();

    const dialog = await screen.findByRole("dialog", { name: "Compare versions" });
    expect(within(dialog).getByLabelText("From")).toHaveValue("v-2");
    expect(within(dialog).getByLabelText("To")).toHaveValue("v-3");
    await waitFor(() => expect(h.contractsApi.versionDiff).toHaveBeenCalledWith("c-1", "v-2", "v-3", 3));
    expect(await within(dialog).findByText("Term: two years.")).toBeInTheDocument();
    expect(within(dialog).getByText("Term: one year.")).toBeInTheDocument();
    expect(within(dialog).getByText("1 added")).toBeInTheDocument();
    expect(within(dialog).getByText("1 removed")).toBeInTheDocument();
  });

  it("marks added and removed lines so they can be told apart without colour", async () => {
    mount();

    const added = (await screen.findByText("Term: two years.")).closest("li") as HTMLElement;
    const removed = screen.getByText("Term: one year.").closest("li") as HTMLElement;
    const kept = screen.getByText("Liability: capped.").closest("li") as HTMLElement;
    expect(added).toHaveAttribute("data-kind", "add");
    expect(added).toHaveTextContent(/Added/);
    expect(removed).toHaveAttribute("data-kind", "remove");
    expect(removed).toHaveTextContent(/Removed/);
    expect(kept).toHaveAttribute("data-kind", "context");
  });

  it("says how many unchanged lines were left out", async () => {
    mount();

    expect(await screen.findByText(/3 unchanged lines/)).toBeInTheDocument();
  });

  it("compares again when a different version is picked", async () => {
    mount();
    await screen.findByText("Term: two years.");

    fireEvent.change(screen.getByLabelText("From"), { target: { value: "v-1" } });

    await waitFor(() => expect(h.contractsApi.versionDiff).toHaveBeenLastCalledWith("c-1", "v-1", "v-3", 3));
  });

  it("asks for two different versions instead of comparing a version with itself", async () => {
    mount();
    await screen.findByText("Term: two years.");
    h.contractsApi.versionDiff.mockClear();

    fireEvent.change(screen.getByLabelText("From"), { target: { value: "v-3" } });

    expect(await screen.findByText("Pick two different versions to compare.")).toBeInTheDocument();
    expect(h.contractsApi.versionDiff).not.toHaveBeenCalled();
  });

  it("offers only versions whose text has been read", async () => {
    mount({ versions: [v1, version(2, { text_snapshot_id: null }), v3] });

    const from = await screen.findByLabelText("From");
    expect(within(from).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual(["v-3", "v-1"]);
  });

  it("names each version the way the version dropdown does, newest first, and says which is current", async () => {
    mount();

    const to = await screen.findByLabelText("To");
    const labels = within(to).getAllByRole("option").map((o) => o.textContent);
    expect(labels).toEqual(["V3 · Upload · current", "V2 · Upload · earlier", "V1 · Upload · earlier"]);
  });

  it("shows a spinner while the comparison loads", async () => {
    h.contractsApi.versionDiff.mockReturnValue(new Promise(() => {}));
    mount();

    expect(await screen.findByRole("status")).toHaveTextContent("Comparing");
  });

  it("shows why it failed and lets the person try again", async () => {
    h.contractsApi.versionDiff.mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce(diff());
    mount();

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Term: two years.")).toBeInTheDocument();
    expect(h.contractsApi.versionDiff).toHaveBeenCalledTimes(2);
  });

  it("says plainly when the two versions have the same text", async () => {
    h.contractsApi.versionDiff.mockResolvedValue(diff({ added: 0, removed: 0, lines: [] }));
    mount();

    expect(await screen.findByText("No differences")).toBeInTheDocument();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("warns when the comparison was cut off, so a short list is not mistaken for the whole change", async () => {
    h.contractsApi.versionDiff.mockResolvedValue(diff({ truncated: true }));
    mount();

    expect(await screen.findByRole("note")).toHaveTextContent("cut off");
  });

  it("needs two versions with text to compare anything", async () => {
    mount({ versions: [v3] });

    expect(await screen.findByText("This contract has fewer than two versions with readable text.")).toBeInTheDocument();
    expect(h.contractsApi.versionDiff).not.toHaveBeenCalled();
  });

  it("closes from its Close button", async () => {
    const { onClose } = mount();

    fireEvent.click(await screen.findByRole("button", { name: "Close dialog" }));

    expect(onClose).toHaveBeenCalled();
  });

  it("starts again from the defaults each time it is opened, not from what was left on screen", async () => {
    const onClose = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const tree = (open: boolean) => (
      <QueryClientProvider client={qc}>
        <VersionCompare contractId="c-1" versions={[v1, v2, v3]} current={v3} open={open} onClose={onClose} />
      </QueryClientProvider>
    );
    const { rerender } = render(tree(true));
    const dialog = await screen.findByRole("dialog", { name: "Compare versions" });
    fireEvent.change(within(dialog).getByLabelText("To"), { target: { value: "v-1" } });
    fireEvent.change(within(dialog).getByLabelText("From"), { target: { value: "v-1" } });
    expect(await within(dialog).findByText("Pick two different versions to compare.")).toBeInTheDocument();

    rerender(tree(false));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    rerender(tree(true));

    const reopened = await screen.findByRole("dialog", { name: "Compare versions" });
    expect(within(reopened).getByLabelText("From")).toHaveValue("v-2");
    expect(within(reopened).getByLabelText("To")).toHaveValue("v-3");
    expect(within(reopened).queryByText("Pick two different versions to compare.")).not.toBeInTheDocument();
  });

  it("renders nothing, and asks for nothing, while closed", () => {
    mount({ open: false });

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(h.contractsApi.versionDiff).not.toHaveBeenCalled();
  });
});
