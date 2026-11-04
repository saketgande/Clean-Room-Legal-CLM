import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Trademark } from "@/lib/types";
import { PostFilingCard } from "./_post-filing-card";

// Post-filing tracking on the Filing tab: the LTE logs updates, and opens a response for an examination report
// or an opposition.

const h = vi.hoisted(() => ({ postFilingUpdate: vi.fn(), openResponse: vi.fn(), notify: vi.fn() }));
vi.mock("@/lib/endpoints", () => ({ trademarksApi: { postFilingUpdate: h.postFilingUpdate, openResponse: h.openResponse } }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const filed = { id: "t-1", name: "ZQ", status: "filed", next_action_on: null, next_action_note: null } as unknown as Trademark;

function mount(trademark: Trademark = filed) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PostFilingCard trademark={trademark} />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.resetAllMocks());

describe("PostFilingCard", () => {
  it("shows the current stage, and cannot log an update until there is a note", () => {
    mount();
    expect(screen.getByText("Post-filing tracking")).toBeInTheDocument();
    expect(screen.getAllByText("Filed").length).toBeGreaterThan(0); // the stage badge (and the select's option)
    expect(screen.getByRole("button", { name: "Log update" })).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText(/What happened/), { target: { value: "Examination report expected" } });
    expect(screen.getByRole("button", { name: "Log update" })).toBeEnabled();
  });

  it("logs an update with the stage and next date it was given, and clears the form after", async () => {
    h.postFilingUpdate.mockResolvedValue(filed);
    mount();

    fireEvent.change(screen.getByPlaceholderText(/What happened/), { target: { value: "Hearing set" } });
    fireEvent.change(screen.getByLabelText("Stage"), { target: { value: "examination" } });
    fireEvent.change(screen.getByLabelText("Next date"), { target: { value: "2026-12-01" } });
    fireEvent.change(screen.getByPlaceholderText(/Hearing, reply/), { target: { value: "Hearing" } });
    fireEvent.click(screen.getByRole("button", { name: "Log update" }));

    await waitFor(() => expect(h.postFilingUpdate).toHaveBeenCalledTimes(1));
    const [id, payload] = h.postFilingUpdate.mock.calls[0];
    expect(id).toBe("t-1");
    expect(payload).toMatchObject({ note: "Hearing set", stage: "examination", next_action_note: "Hearing" });
    expect(payload.next_action_on).toMatch(/^2026-12-01/);
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Update logged", "success"));
    expect(screen.getByPlaceholderText(/What happened/)).toHaveValue("");
  });

  it("keeps the stage and the next date as they are when none is chosen", async () => {
    h.postFilingUpdate.mockResolvedValue(filed);
    mount();

    fireEvent.change(screen.getByPlaceholderText(/What happened/), { target: { value: "Waiting on the office" } });
    fireEvent.click(screen.getByRole("button", { name: "Log update" }));

    await waitFor(() => expect(h.postFilingUpdate).toHaveBeenCalledWith("t-1", { note: "Waiting on the office" }));
  });

  it("can clear the next date that is set", async () => {
    h.postFilingUpdate.mockResolvedValue(filed);
    mount({ ...filed, next_action_on: "2026-12-01T00:00:00Z", next_action_note: "Hearing" } as Trademark);

    expect(screen.getByText(/Next:/)).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/What happened/), { target: { value: "Hearing held" } });
    fireEvent.click(screen.getByLabelText("Clear the current next date"));
    fireEvent.click(screen.getByRole("button", { name: "Log update" }));

    await waitFor(() => expect(h.postFilingUpdate).toHaveBeenCalledWith("t-1", { note: "Hearing held", next_action_on: null }));
  });

  it("opens a response for an examination report or an opposition, with its reference and due date", async () => {
    h.openResponse.mockResolvedValue(filed);
    mount();

    fireEvent.click(screen.getByRole("button", { name: /Log examination report \/ opposition/ }));
    const open = screen.getByRole("button", { name: "Open response workflow" });
    expect(open).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "opposition" } });
    fireEvent.change(screen.getByLabelText("Office reference"), { target: { value: "OP-7" } });
    fireEvent.change(screen.getByPlaceholderText(/What the office or the opponent says/), { target: { value: "Prior mark in class 5" } });
    fireEvent.change(screen.getByLabelText("Response due"), { target: { value: "2026-11-15" } });
    fireEvent.click(open);

    await waitFor(() => expect(h.openResponse).toHaveBeenCalledTimes(1));
    const [, payload] = h.openResponse.mock.calls[0];
    expect(payload).toMatchObject({ kind: "opposition", summary: "Prior mark in class 5", reference: "OP-7" });
    expect(payload.due_on).toMatch(/^2026-11-15/);
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Response workflow opened", "success"));
  });

  it("shows the refusal and keeps what was typed, for someone outside the LTE team", async () => {
    h.postFilingUpdate.mockRejectedValue(new Error("Only the LTE team (or an administrator) can log post-filing work"));
    mount();

    fireEvent.change(screen.getByPlaceholderText(/What happened/), { target: { value: "My update" } });
    fireEvent.click(screen.getByRole("button", { name: "Log update" }));

    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Only the LTE team (or an administrator) can log post-filing work", "error"));
    expect(screen.getByPlaceholderText(/What happened/)).toHaveValue("My update");
  });
});
