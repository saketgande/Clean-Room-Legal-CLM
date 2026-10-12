import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ContractDocument } from "./contract-document";
import type { ContractResponse, ContractVersionResponse, ReviewStatusResponse } from "@/lib/types";

// What editing the text means once approval has started (round 2, bug 17), and how the version picker
// names its versions (bug 19). Saving always makes a NEW version that nobody has approved.

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  contractsApi: {
    get: vi.fn(),
    reviewStatus: vi.fn(),
    versions: vi.fn(),
    comments: vi.fn(),
    versionText: vi.fn(),
    versionDiff: vi.fn(),
    updateText: vi.fn(),
    exportDocx: vi.fn(),
    downloadVersion: vi.fn(),
    proposeEdit: vi.fn(),
    addComment: vi.fn(),
  },
}));
vi.mock("@/lib/endpoints", () => ({ contractsApi: h.contractsApi }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const version = (n: number, over: Partial<ContractVersionResponse> = {}) =>
  ({ id: `v-${n}`, version_number: n, source: "upload", text_snapshot_id: `s-${n}`, is_authoritative: false, ...over }) as unknown as ContractVersionResponse;

const contractAt = (stage: string) =>
  ({ id: "c-1", title: "Acme MSA", lifecycle_stage: stage, current_authoritative_version_id: "v-2" }) as unknown as ContractResponse;

const status = (version_approval: ReviewStatusResponse["version_approval"]) =>
  ({ contract_id: "c-1", checklist: [], version_approval }) as unknown as ReviewStatusResponse;

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ContractDocument contractId="c-1" />
    </QueryClientProvider>,
  );
}

async function openEditor() {
  fireEvent.click(await screen.findByRole("button", { name: /Edit/ }));
  await screen.findByLabelText("Edit contract text");
}

beforeEach(() => {
  vi.resetAllMocks();
  h.contractsApi.get.mockResolvedValue(contractAt("review"));
  h.contractsApi.reviewStatus.mockResolvedValue(status("none"));
  h.contractsApi.versions.mockResolvedValue([version(1), version(2, { is_authoritative: true })]);
  h.contractsApi.comments.mockResolvedValue([]);
  h.contractsApi.versionText.mockResolvedValue({ text: "The Supplier shall deliver the goods in thirty days.", extraction_method: "docx" });
  h.contractsApi.updateText.mockResolvedValue({});
  h.contractsApi.versionDiff.mockResolvedValue({
    base_version_id: "v-1", base_version_number: 1, target_version_id: "v-2", target_version_number: 2,
    added: 0, removed: 0, truncated: false, lines: [],
  });
});

describe("editing the text of a contract that is past approval", () => {
  it("says nothing extra while the contract is still being reviewed", async () => {
    mount();
    await openEditor();
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("warns, before anything is typed, that a contract at Signature gets a new version nobody approved", async () => {
    h.contractsApi.get.mockResolvedValue(contractAt("signature"));
    h.contractsApi.reviewStatus.mockResolvedValue(status("approved"));
    mount();
    await openEditor();
    const note = await screen.findByRole("note");
    expect(note).toHaveTextContent("Saving makes a new version that nobody has approved");
    expect(note).toHaveTextContent("cannot be sent for signature until it goes through approval again");
  });

  it("warns a contract still with the approvers that they are deciding on text that is about to change", async () => {
    h.contractsApi.get.mockResolvedValue(contractAt("approval"));
    h.contractsApi.reviewStatus.mockResolvedValue(status("pending"));
    mount();
    await openEditor();
    expect(await screen.findByRole("note")).toHaveTextContent("This contract is with the approvers");
  });

  it("only shows the warning while editing, and nothing is saved by opening the editor", async () => {
    h.contractsApi.get.mockResolvedValue(contractAt("signature"));
    h.contractsApi.reviewStatus.mockResolvedValue(status("approved"));
    mount();
    await screen.findByRole("button", { name: /Edit/ });
    expect(screen.queryByRole("note")).toBeNull();
    await openEditor();
    expect(h.contractsApi.updateText).not.toHaveBeenCalled();
  });

  it("tells the person the saved version has not been approved, in the toast", async () => {
    h.contractsApi.get.mockResolvedValue(contractAt("signature"));
    h.contractsApi.reviewStatus.mockResolvedValue(status("approved"));
    mount();
    await openEditor();
    await screen.findByRole("note");
    fireEvent.change(screen.getByLabelText("Edit contract text"), { target: { value: "The Supplier shall deliver the goods in sixty days." } });
    fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));
    await waitFor(() => expect(h.contractsApi.updateText).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Saved as a new version. It has not been approved.", "success"));
  });

  it("keeps the plain toast when the contract is still in review", async () => {
    mount();
    await openEditor();
    fireEvent.change(screen.getByLabelText("Edit contract text"), { target: { value: "The Supplier shall deliver the goods in sixty days." } });
    fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Saved as a new version", "success"));
  });
});

describe("the version picker", () => {
  it("says which version is in use, which is older, and which was never applied, instead of bare source names", async () => {
    h.contractsApi.versions.mockResolvedValue([version(1), version(2, { is_authoritative: true }), version(3, { source: "assistant_edit" })]);
    mount();
    const picker = await screen.findByTitle(/marked current is the text in use/);
    const labels = Array.from(picker.querySelectorAll("option")).map((o) => o.textContent ?? "");
    expect(labels).toHaveLength(3);
    expect(labels[0]).toMatch(/V3.*not in use/); // newest first: a proposal that was never applied
    expect(labels[1]).toMatch(/V2.*current/);
    expect(labels[2]).toMatch(/V1.*earlier/);
  });
});


describe("comparing versions from the document", () => {
  it("has a Compare versions button beside the version dropdown when there are two versions to compare", async () => {
    mount();

    fireEvent.click(await screen.findByRole("button", { name: "Compare versions" }));

    const dialog = await screen.findByRole("dialog", { name: "Compare versions" });
    expect(dialog).toBeInTheDocument();
    await waitFor(() => expect(h.contractsApi.versionDiff).toHaveBeenCalledWith("c-1", "v-1", "v-2", 3));
  });

  it("has no Compare versions button when there is only one version", async () => {
    h.contractsApi.versions.mockResolvedValue([version(1, { is_authoritative: true })]);
    mount();

    await screen.findByRole("button", { name: /Edit/ });
    expect(screen.queryByRole("button", { name: "Compare versions" })).toBeNull();
  });
});
