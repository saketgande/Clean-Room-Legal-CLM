import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ReviewList, type ReviewListProps } from "./_review-list";
import { buildSuggestions } from "@/lib/suggestions";
import type { ContractDeviation, ContractEditResponse } from "@/lib/types";

const dev = (id: string, over: Partial<ContractDeviation> = {}): ContractDeviation => ({
  id,
  playbook_run_id: "run-1",
  playbook_rule_id: null,
  contract_id: "c-1",
  severity: "high",
  clause_type: "payment_terms",
  issue: `Issue ${id}`,
  suggested_fix: null,
  citation: null,
  status: "open",
  ...over,
});

const edit = (id: string, over: Partial<ContractEditResponse> = {}): ContractEditResponse => ({
  id,
  contract_id: "c-1",
  contract_version_id: "v-1",
  edit_type: "replace",
  status: "proposed",
  original_text: "Net 30.",
  replacement_text: "Net 60.",
  rationale: `Because ${id}`,
  citation: [],
  playbook_deviation_id: null,
  locatable: true,
  ...over,
});

function setup(deviations: ContractDeviation[], edits: ContractEditResponse[], over: Partial<ReviewListProps> = {}) {
  const handlers = { onDecide: vi.fn(), onCloseIssue: vi.fn(), onDraftFix: vi.fn(), onShow: vi.fn() };
  render(
    <ReviewList
      suggestions={buildSuggestions({ deviations, edits, currentVersionId: "v-1" })}
      currentVersionId="v-1"
      busy={false}
      activeEditId={null}
      {...handlers}
      {...over}
    />,
  );
  return handlers;
}

describe("ReviewList", () => {
  it("says so when there is nothing to decide", () => {
    setup([], [edit("e1", { status: "accepted" })]);
    expect(screen.getByText(/Nothing waiting for your decision/)).toBeInTheDocument();
  });

  it("gives a chat suggestion the same Accept and Reject as a playbook fix", () => {
    const h = setup([], [edit("e1")]);
    fireEvent.click(screen.getByRole("button", { name: "Accept change" }));
    expect(h.onDecide).toHaveBeenLastCalledWith(expect.objectContaining({ id: "e1" }), true);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(h.onDecide).toHaveBeenLastCalledWith(expect.objectContaining({ id: "e1" }), false);
    expect(screen.getByText("AI suggestion")).toBeInTheDocument();
    expect(screen.getByText("Net 60.")).toBeInTheDocument();
  });

  it("decides a fix for an issue together with that issue", () => {
    const d = dev("d1");
    const h = setup([d], [edit("e1", { edit_type: "playbook_redline", playbook_deviation_id: "d1" })]);
    fireEvent.click(screen.getByRole("button", { name: "Accept change" }));
    expect(h.onDecide).toHaveBeenCalledWith(expect.objectContaining({ id: "e1" }), true, d);
    expect(screen.getByText("Issue d1")).toBeInTheDocument();
  });

  it("offers a fix or keeping the wording for an issue with no change", () => {
    const d = dev("d1", { severity: "medium" });
    const h = setup([d], []);
    fireEvent.click(screen.getByRole("button", { name: "Draft fix with AI" }));
    expect(h.onDraftFix).toHaveBeenCalledWith(d);
    fireEvent.click(screen.getByRole("button", { name: "Keep as is" }));
    expect(h.onCloseIssue).toHaveBeenCalledWith(d, false);
  });

  it("asks why before a high risk is kept as is, and saves the reason with it", () => {
    const d = dev("d1", { severity: "high" });
    const h = setup([d], []);
    fireEvent.click(screen.getByRole("button", { name: "Keep as is" }));
    expect(h.onCloseIssue).not.toHaveBeenCalled();
    const confirm = screen.getByRole("button", { name: "Keep original wording" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Why can this high risk stay/), { target: { value: "short" } });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Why can this high risk stay/), { target: { value: "  Customer standard term, agreed with GC  " } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    expect(h.onCloseIssue).toHaveBeenCalledWith(d, false, "Customer standard term, agreed with GC");
  });

  it("treats a critical risk like a high one, and lets the person back out", () => {
    const d = dev("d1", { severity: "critical" });
    const h = setup([d], []);
    fireEvent.click(screen.getByRole("button", { name: "Keep as is" }));
    expect(screen.getByLabelText(/Why can this critical risk stay/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText(/Why can this critical risk stay/)).toBeNull();
    expect(screen.getByRole("button", { name: "Keep as is" })).toBeInTheDocument();
    expect(h.onCloseIssue).not.toHaveBeenCalled();
  });

  it("keeps a high risk open after its fix was rejected, until a reason is given", () => {
    const d = dev("d1", { severity: "high" });
    const h = setup([d], [edit("e1", { status: "rejected", playbook_deviation_id: "d1" })]);
    expect(screen.getByText(/Change already rejected/)).toHaveTextContent(/stays open until you say why/);
    fireEvent.click(screen.getByRole("button", { name: "Close issue" }));
    expect(h.onCloseIssue).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/Why can this high risk stay/), { target: { value: "Accepted by the CFO on 3 Oct" } });
    fireEvent.click(screen.getByRole("button", { name: "Keep original wording" }));
    expect(h.onCloseIssue).toHaveBeenCalledWith(d, false, "Accepted by the CFO on 3 Oct");
  });

  it("needs no reason to close a high risk whose fix was accepted", () => {
    const d = dev("d1", { severity: "high" });
    const h = setup([d], [edit("e1", { status: "accepted", playbook_deviation_id: "d1" })]);
    fireEvent.click(screen.getByRole("button", { name: "Close issue" }));
    expect(h.onCloseIssue).toHaveBeenCalledWith(d, true);
  });

  it("closes an issue whose change was already decided", () => {
    const d = dev("d1", { severity: "medium" });
    const h = setup([d], [edit("e1", { status: "accepted", playbook_deviation_id: "d1" })]);
    expect(screen.getByText(/Change already accepted/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close issue" }));
    expect(h.onCloseIssue).toHaveBeenCalledWith(d, true);
  });

  it("offers a new fix once the out-of-date change for an issue has been rejected", () => {
    const d = dev("d1");
    const h = setup([d], [edit("e1", { status: "rejected", contract_version_id: "v-0", playbook_deviation_id: "d1" })]);
    expect(screen.queryByText(/Change already/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Draft fix with AI" }));
    expect(h.onDraftFix).toHaveBeenCalledWith(d);
  });

  it("lets an out-of-date change be rejected but not accepted", () => {
    const h = setup([], [edit("e1", { contract_version_id: "v-0" })]);
    expect(screen.getByText(/Out of date/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept change" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Show in document" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(h.onDecide).toHaveBeenCalledWith(expect.objectContaining({ id: "e1" }), false);
  });

  it("lets a change that would write a blank into the contract be rejected but not accepted", () => {
    const h = setup([], [edit("e1", { replacement_text: "Ends on [INSERT CORRECT EXPIRY DATE].", blank: "[INSERT CORRECT EXPIRY DATE]" })]);
    expect(screen.getByText(/It would write a blank into the contract \(\[INSERT CORRECT EXPIRY DATE\]\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept change" })).toBeNull();
    expect(screen.getByRole("button", { name: "Show in document" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(h.onDecide).toHaveBeenCalledWith(expect.objectContaining({ id: "e1" }), false);
  });

  it("lets a change the text can't place be rejected but not accepted", () => {
    setup([], [edit("e1", { locatable: false })]);
    expect(screen.getByText(/Can't be placed/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept change" })).toBeNull();
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
  });

  it("shows a change in the document", () => {
    const h = setup([], [edit("e1")]);
    fireEvent.click(screen.getByRole("button", { name: "Show in document" }));
    expect(h.onShow).toHaveBeenCalledWith("e1");
  });

  it("blocks every decision while one is being saved", () => {
    setup([dev("d1")], [edit("e1")], { busy: true });
    for (const b of screen.getAllByRole("button", { name: /Accept change|Reject|Draft fix|Keep as is/ })) expect(b).toBeDisabled();
  });

  it("marks the card of the change picked in the document", () => {
    setup([], [edit("e1"), edit("e2")], { activeEditId: "e2" });
    const cards = document.querySelectorAll(".rev");
    expect(cards[0].classList.contains("focus")).toBe(false);
    expect(cards[1].classList.contains("focus")).toBe(true);
  });

  it("explains when accepted changes take effect", () => {
    setup([], [edit("e1")]);
    expect(screen.getByText(/once every suggestion here has been decided/)).toBeInTheDocument();
  });

  it("keeps an issue's own card apart from a standalone card", () => {
    setup([dev("d1")], [edit("e1", { playbook_deviation_id: "d1" }), edit("e2")]);
    const cards = Array.from(document.querySelectorAll<HTMLElement>(".rev"));
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByText("Issue d1")).toBeInTheDocument();
    expect(within(cards[1]).getByText("Suggested change")).toBeInTheDocument();
  });
});

describe("ReviewList: what was decided", () => {
  it("lists accepted and rejected changes when nothing is left to decide", () => {
    setup([], [], { decided: [edit("e1", { status: "accepted", rationale: "Governing law" }), edit("e2", { status: "rejected", rationale: "Payment terms" })] });
    expect(screen.getByText(/Nothing waiting for your decision/)).toBeInTheDocument();
    expect(screen.getByText("Decided (2)")).toBeInTheDocument();
    expect(screen.getByText("Accepted")).toBeInTheDocument();
    expect(screen.getByText("Rejected")).toBeInTheDocument();
    expect(screen.getByText("Governing law")).toBeInTheDocument();
    expect(screen.getByText("Payment terms")).toBeInTheDocument();
  });

  it("keeps the list under the cards while other changes still wait for a decision", () => {
    setup([], [edit("e1")], { decided: [edit("e0", { status: "accepted", rationale: "Done earlier" })] });
    expect(screen.getByRole("button", { name: "Accept change" })).toBeInTheDocument();
    expect(screen.getByText("Decided (1)")).toBeInTheDocument();
  });

  it("says when a decision was made on an earlier version of the contract", () => {
    setup([], [], { decided: [edit("e0", { status: "rejected", contract_version_id: "v-0" })] });
    expect(screen.getByText(/Decided on an earlier version/)).toBeInTheDocument();
  });

  it("shows nothing extra when nothing was decided", () => {
    setup([], [], { decided: [] });
    expect(screen.queryByText(/Decided \(/)).toBeNull();
  });
});

describe("ReviewList: draft fixes for every issue at once", () => {
  it("offers it when open issues have no suggested change, and says how many", () => {
    const onDraftAll = vi.fn();
    setup([dev("d1"), dev("d2"), dev("d3")], [edit("e1", { playbook_deviation_id: "d3" })], { unfixedCount: 2, onDraftAll });
    expect(screen.getByRole("status")).toHaveTextContent("2 open issues have no suggested change yet.");
    fireEvent.click(screen.getByRole("button", { name: "Draft fixes for all" }));
    expect(onDraftAll).toHaveBeenCalledTimes(1);
  });

  it("uses the singular for one issue", () => {
    setup([dev("d1")], [], { unfixedCount: 1, onDraftAll: vi.fn() });
    expect(screen.getByRole("status")).toHaveTextContent("1 open issue has no suggested change yet.");
  });

  it("is not shown when every issue already has a change", () => {
    setup([dev("d1")], [edit("e1", { playbook_deviation_id: "d1" })], { unfixedCount: 0, onDraftAll: vi.fn() });
    expect(screen.queryByRole("button", { name: "Draft fixes for all" })).toBeNull();
  });

  it("is not shown without a handler", () => {
    setup([dev("d1")], [], { unfixedCount: 1 });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("cannot be pressed while something else is saving", () => {
    setup([dev("d1")], [], { unfixedCount: 1, onDraftAll: vi.fn(), busy: true });
    expect(screen.getByRole("button", { name: "Draft fixes for all" })).toBeDisabled();
  });

  it("says a draft is being written, in place of the button", () => {
    setup([dev("d1")], [], { unfixedCount: 1, onDraftAll: vi.fn(), drafting: true, busy: true });
    expect(screen.getByRole("status")).toHaveTextContent("Drafting suggested changes…");
    expect(screen.queryByRole("button", { name: "Draft fixes for all" })).toBeNull();
  });
});

describe("ReviewList: while the background job writes the changes", () => {
  it("says how far it has got, and offers no second way to start it", () => {
    setup([dev("d1"), dev("d2"), dev("d3")], [], { unfixedCount: 3, onDraftAll: vi.fn(), drafting: true, draftProgress: { done: 1, total: 3 } });
    expect(screen.getByRole("status")).toHaveTextContent("Drafting suggested changes — 1 of 3 done.");
    expect(screen.queryByRole("button", { name: "Draft fixes for all" })).toBeNull();
  });

  it("does not show a count before the job has one", () => {
    setup([dev("d1")], [], { unfixedCount: 1, onDraftAll: vi.fn(), drafting: true, draftProgress: { done: 0, total: 0 } });
    expect(screen.getByRole("status")).toHaveTextContent("Drafting suggested changes…");
    expect(screen.getByRole("status")).not.toHaveTextContent(/of \d+ done/);
  });

  it("holds every issue's own draft button, since the job could be on that issue", () => {
    const h = setup([dev("d1"), dev("d2")], [], { unfixedCount: 2, onDraftAll: vi.fn(), drafting: true, draftProgress: { done: 0, total: 2 } });
    for (const b of screen.getAllByRole("button", { name: "Draft fix with AI" })) {
      expect(b).toBeDisabled();
      fireEvent.click(b);
    }
    expect(h.onDraftFix).not.toHaveBeenCalled();
  });

  it("still lets a person keep the original wording of an issue meanwhile", () => {
    const h = setup([dev("d1", { severity: "low" })], [], { unfixedCount: 1, onDraftAll: vi.fn(), drafting: true, draftProgress: { done: 0, total: 1 } });
    fireEvent.click(screen.getByRole("button", { name: "Keep as is" }));
    expect(h.onCloseIssue).toHaveBeenCalledWith(expect.objectContaining({ id: "d1" }), false);
  });

  it("keeps deciding the changes that are already there", () => {
    const h = setup([dev("d1"), dev("d2")], [edit("e1", { playbook_deviation_id: "d1" })], { unfixedCount: 1, onDraftAll: vi.fn(), drafting: true, draftProgress: { done: 0, total: 1 } });
    fireEvent.click(screen.getByRole("button", { name: "Accept change" }));
    expect(h.onDecide).toHaveBeenCalledWith(expect.objectContaining({ id: "e1" }), true, expect.objectContaining({ id: "d1" }));
  });

  it("explains, when idle, what the remaining issues are waiting for", () => {
    setup([dev("d1")], [], { unfixedCount: 1, onDraftAll: vi.fn() });
    expect(screen.getByRole("status")).toHaveTextContent("Aegis can write a suggested change for each one. You still accept or reject it below.");
  });

  it("enables the issue's own draft button again once the job is over", () => {
    setup([dev("d1")], [], { unfixedCount: 1, onDraftAll: vi.fn(), drafting: false, draftProgress: null });
    expect(screen.getByRole("button", { name: "Draft fix with AI" })).toBeEnabled();
  });
});
