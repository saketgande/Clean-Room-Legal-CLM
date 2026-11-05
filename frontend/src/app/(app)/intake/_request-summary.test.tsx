import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { RequestSummarySection } from "./_request-summary";
import { fmtDateTime } from "@/lib/utils";
import type { RequestSummary } from "@/lib/types";

const data: RequestSummary = {
  text: "A mutual NDA with Initech.", at: "2026-10-27T09:00:00Z", by_name: "Asha Rao",
  instructions: [{ text: "Send a copy to procurement", differs: [] },
    { text: "Term is 5 years", differs: [{ key: "term", label: "Term", answer: "3 years" }] }],
};
const ready = (d: RequestSummary | undefined, onRetry = vi.fn()) =>
  render(<RequestSummarySection status="ready" data={d} onRetry={onRetry} />);

describe("RequestSummarySection", () => {
  it("shows the text, numbered instructions, the differs note, writer and time", () => {
    // Failure mode: the requester's words, a conflict note or the writer/time going missing on the request page.
    ready(data);
    expect(screen.getByText("A mutual NDA with Initech.")).toBeInTheDocument();
    expect(screen.getByText("Extra instructions for Legal (2)")).toBeInTheDocument();
    expect(screen.getByText("Send a copy to procurement")).toBeInTheDocument();
    expect(screen.getByText(/This differs from your answer: 3 years/)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`Asha Rao.*${fmtDateTime(data.at!).replace(/[()]/g, "\\$&")}`))).toBeInTheDocument();
  });

  it("renders nothing while loading and when there is neither text nor instructions", () => {
    // Failure mode: an empty "as filed" block on older requests, or a flash while the query loads.
    const a = render(<RequestSummarySection status="loading" data={undefined} onRetry={vi.fn()} />);
    expect(a.container).toBeEmptyDOMElement();
    const b = ready({ text: null, at: null, by_name: null, instructions: [] });
    expect(b.container).toBeEmptyDOMElement();
  });

  it("on error shows the line and a Retry that calls back", () => {
    // Failure mode: a failed summary query leaving no way to try again.
    const onRetry = vi.fn();
    render(<RequestSummarySection status="error" data={undefined} onRetry={onRetry} />);
    expect(screen.getByText(/could not be loaded/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("has no edit controls once filed", () => {
    // Failure mode: an input or button letting someone change the instructions after filing (FR-39).
    ready(data);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
  });
});
