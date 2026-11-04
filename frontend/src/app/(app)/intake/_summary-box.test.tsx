import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { editorText, ExtraInstructions, LiveSummary, SummaryEditor } from "./_summary-box";
import { EMPTY_BOX } from "@/lib/summary-edits";
import type { SummaryLine } from "@/lib/request-summary";
import type { SummaryBoxState, SummaryOffer } from "@/lib/types";

const line = (over: Partial<SummaryLine> & { id: string; text: string }): SummaryLine => ({
  kind: "sentence", keys: [over.id], name: over.id, required: false, editable: true, ...over,
});
const LINES: SummaryLine[] = [
  line({ id: "law", text: "It is governed by Indian law.", name: "Governing law" }),
  line({ id: "cp", kind: "gap", text: "Counterparty: not chosen yet", required: true, editable: false }),
  line({ id: "needed_by", kind: "gap", text: "Needed by: not given yet", editable: false }),
  line({ id: "note_approvers", text: "Note to approvers: be quick.", editable: false, name: "Note to approvers" }),
];
const OFFER: SummaryOffer = {
  id: "law:governing_law", line: "law", kind: "update", keys: ["governing_law"], label: "Governing law", oldText: "India",
  newText: "England and Wales", patch: { governing_law: "England and Wales" },
};

function editor(box: SummaryBoxState = EMPTY_BOX, over: Partial<Parameters<typeof SummaryEditor>[0]> = {}) {
  const p = { onText: vi.fn(), onCheck: vi.fn(), onAccept: vi.fn(), onDecline: vi.fn(), onUndo: vi.fn() };
  render(<SummaryEditor lines={LINES} box={box} text={editorText(LINES)} announce="" {...p} {...over} />);
  return p;
}

describe("LiveSummary", () => {
  it("is a named section whose gaps are told apart by words, not colour alone (FR-4)", () => {
    // Failure mode: a required gap looking like an optional one to someone who cannot see colour.
    render(<LiveSummary lines={LINES} instructions={0} />);
    expect(screen.getByRole("region", { name: "Your request in words" })).toHaveAttribute("id", "request-in-words");
    expect(screen.getByText(/Counterparty: not chosen yet/)).toHaveTextContent("(required)");
    expect(screen.getByText(/Needed by: not given yet/)).toHaveTextContent("(optional)");
    expect(screen.getByText("Built from your answers. Updates as you answer.")).toBeInTheDocument();
  });

  it("is not a live region (it changes on every keystroke)", () => {
    // Failure mode: a screen reader reading the box out on each key press.
    const { container } = render(<LiveSummary lines={LINES} instructions={0} />);
    expect(container.querySelector("[aria-live]")).toBeNull();
  });

  it("says one line when the summary could not be built, and counts kept instructions (AC-30)", () => {
    // Failure mode: a broken builder leaving a blank card, or hiding that instructions exist.
    const { rerender } = render(<LiveSummary lines={null} instructions={0} />);
    expect(screen.getByText("The summary could not be built. You can still file.")).toBeInTheDocument();
    rerender(<LiveSummary lines={LINES} instructions={2} />);
    expect(screen.getByText("2 extra instructions for Legal — see the Review step.")).toBeInTheDocument();
    rerender(<LiveSummary lines={LINES} instructions={1} />);
    expect(screen.getByText("1 extra instruction for Legal — see the Review step.")).toBeInTheDocument();
  });
});

describe("SummaryEditor", () => {
  it("is ONE labelled textbox holding the sentences, with gaps and the note listed under it as text", () => {
    // Failure mode: a field per sentence coming back, or the read-only note / gaps landing inside the editable text.
    editor();
    const t = screen.getByRole("textbox", { name: "Your request in words" });
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    expect(t).toHaveValue("It is governed by Indian law.");
    expect(t).toHaveAttribute("rows", "6");
    expect(screen.getByText("Edit it in Note to approvers below.")).toBeInTheDocument();
    expect(screen.getByText(/Counterparty: not chosen yet/)).toHaveTextContent("(required)");
    expect(screen.getByText(/Nothing changes until you click/)).toBeInTheDocument();
  });

  it("shows exactly what was typed and checks only on blur", () => {
    // Failure mode: checking on every keystroke, or the box snapping back to the generated text under the cursor.
    const p = editor(EMPTY_BOX, { text: "English law" });
    const t = screen.getByRole("textbox", { name: "Your request in words" });
    expect(t).toHaveValue("English law");
    fireEvent.change(t, { target: { value: "x" } });
    expect(p.onText).toHaveBeenCalledWith("x");
    expect(p.onCheck).not.toHaveBeenCalled();
    fireEvent.keyDown(t, { key: "Enter" });
    expect(p.onCheck).not.toHaveBeenCalled(); // Enter is a new line, not a check
    fireEvent.blur(t);
    expect(p.onCheck).toHaveBeenCalledTimes(1);
  });

  it("lists each offer under the box in its own group, and its buttons call back", () => {
    // Failure mode: an offer that a keyboard or screen-reader user cannot reach or cannot tell apart from another.
    const p = editor({ ...EMPTY_BOX, offers: [OFFER, { ...OFFER, id: "term:nda_term", label: "Term", oldText: "2 years", newText: "5 years" }] });
    const groups = screen.getAllByRole("group", { name: "Update the answer?" });
    expect(groups).toHaveLength(2);
    expect(groups[0]).toHaveTextContent("Governing law: India → England and Wales");
    expect(screen.getByRole("textbox", { name: "Your request in words" })).toHaveAttribute("aria-describedby", "riw-offers");
    fireEvent.click(within(groups[0]).getByRole("button", { name: "Update answer" }));
    expect(p.onAccept).toHaveBeenCalledWith("law:governing_law");
    fireEvent.click(within(groups[1]).getByRole("button", { name: "Keep my answer" }));
    expect(p.onDecline).toHaveBeenCalledWith("term:nda_term");
  });

  it("calls a clearing offer 'Clear the answer?' with a Clear answer button", () => {
    // Failure mode: a deletion offer worded as an update.
    editor({ ...EMPTY_BOX, offers: [{ ...OFFER, kind: "clear", newText: "no answer", patch: { governing_law: "" } }] });
    expect(screen.getByRole("group", { name: "Clear the answer?" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear answer" })).toBeInTheDocument();
  });

  it("has one polite status region that carries the announcement (AC-30)", () => {
    // Failure mode: offers appearing silently for a screen-reader user.
    editor(EMPTY_BOX, { announce: "Aegis can update Governing law: India → England and Wales." });
    const s = screen.getByRole("status");
    expect(s).toHaveAttribute("aria-live", "polite");
    expect(s).toHaveTextContent("Aegis can update Governing law");
  });

  it("disables Undo my edits until the text differs from the generated sentences, then calls back (FR-13)", () => {
    // Failure mode: an Undo that does nothing, or one that cannot be reached once an edit exists.
    const p = editor();
    expect(screen.getByRole("button", { name: "Undo my edits" })).toBeDisabled();
    document.body.innerHTML = "";
    const q = editor(EMPTY_BOX, { text: "something else" });
    fireEvent.click(screen.getByRole("button", { name: "Undo my edits" }));
    expect(q.onUndo).toHaveBeenCalled();
    expect(p.onUndo).not.toHaveBeenCalled();
  });
});

describe("ExtraInstructions", () => {
  const props = { items: [], pending: "", words: 0, attachHint: "attach them as a file", onPending: vi.fn(), onAdd: vi.fn(), onRemove: vi.fn() };

  it("says what an empty list means and lets them add a line", () => {
    // Failure mode: an unlabeled box, or Add firing with nothing typed.
    const p = { ...props, onPending: vi.fn(), onAdd: vi.fn() };
    const { rerender } = render(<ExtraInstructions {...p} />);
    expect(screen.getByText("None yet. Edits you don't apply to an answer land here.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Anything else for Legal?" }), { target: { value: "cc procurement" } });
    expect(p.onPending).toHaveBeenCalledWith("cc procurement");
    rerender(<ExtraInstructions {...p} pending="cc procurement" />);
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(p.onAdd).toHaveBeenCalled();
    expect(screen.getByText("0 of about 2,000 words")).toBeInTheDocument();
  });

  it("lists each instruction in full with its note and a numbered Remove", () => {
    // Failure mode: an instruction cut short, a lost note, or two Remove buttons nobody can tell apart.
    const onRemove = vi.fn();
    const long = "word ".repeat(50).trim();
    render(<ExtraInstructions {...props} onRemove={onRemove} items={[
      { text: long, differs: [] },
      { text: "Use English law", differs: [{ key: "governing_law", label: "Governing law", answer: "India" }] },
    ]} />);
    const list = screen.getByRole("list", { name: "Extra instructions for Legal" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(list).toHaveTextContent(long);
    expect(screen.getByText("This differs from your answer: India")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove instruction 2" }));
    expect(onRemove).toHaveBeenCalledWith(1);
  });

  it("warns near the limit, refuses over it, and names the way out", () => {
    // Failure mode: the limit surprising the person only at Submit, or a message with no way forward.
    const { rerender } = render(<ExtraInstructions {...props} words={1799} />);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/words left/)).toBeNull();
    rerender(<ExtraInstructions {...props} words={1850} />);
    expect(screen.getByText("About 150 words left before the limit.")).toBeInTheDocument();
    rerender(<ExtraInstructions {...props} words={2001} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Over the limit of about 2,000 words. Shorten them, or attach them as a file.");
    // Failure mode: a form with no file question telling the person to "shorten them, or shorten them".
    rerender(<ExtraInstructions {...props} words={2001} attachHint={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Over the limit of about 2,000 words. Shorten them — this form has no place to attach a file.");
    expect(screen.getByRole("alert")).not.toHaveTextContent("Shorten them, or shorten them");
  });
});
