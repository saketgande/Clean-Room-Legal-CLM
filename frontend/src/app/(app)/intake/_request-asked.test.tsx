import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { WhatWasAsked } from "./_request-asked";

const base = {
  sourceTag: "from the form",
  facts: [{ label: "Counterparty", value: "Acme Ltd" }, { label: "Type", value: "SOW" }],
  priority: { value: "high", aiSuggests: null, canApply: false, applying: false, onApply: () => {} },
  description: "Please draft a SOW for Q3.",
  answers: null,
  missingInfo: [],
  degraded: null,
  read: null,
  confidence: null,
};
const show = (over: Partial<Parameters<typeof WhatWasAsked>[0]> = {}) => render(<WhatWasAsked {...base} {...over} />);

describe("WhatWasAsked", () => {
  it("shows the facts, the tag and the description once with no 'form by' attribution", () => {
    /** Guards the removed system suffix coming back under the description (FR-8). */
    const { container } = show({ description: "Draft it\nsecond line" });
    expect(screen.getByText("What was asked")).toBeTruthy();
    expect(screen.getByText("from the form")).toBeTruthy();
    expect(screen.getByText("Acme Ltd")).toBeTruthy();
    expect(container.textContent).not.toMatch(/ by /);
    expect(container.querySelectorAll(".quote")).toHaveLength(1);
  });

  it("omits the description block when empty", () => {
    /** Guards an empty grey quote box (or invented 'No description') for a blank description. */
    const { container } = show({ description: "  " });
    expect(container.querySelector(".quote")).toBeNull();
  });

  it("renders notices only when they apply", () => {
    /** Guards the needs-info and triage-degraded warnings (FR-11) showing without data or vanishing with it. */
    const { container, rerender } = render(<WhatWasAsked {...base} />);
    expect(container.querySelector(".needsinfo")).toBeNull();
    rerender(<WhatWasAsked {...base} missingInfo={["Start date", "Fee"]} degraded={{ reason: "model down" }} />);
    expect(screen.getByText("Start date")).toBeTruthy();
    expect(screen.getByText("Fee")).toBeTruthy();
    expect(screen.getByText("model down")).toBeTruthy();
    rerender(<WhatWasAsked {...base} degraded={{ reason: null }} />);
    expect(screen.getByText(/classified by keyword rules/)).toBeTruthy();
  });

  it("lists only the non-empty Aegis read groups", () => {
    /** Guards empty labelled lists and a lost 'Aegis read' summary (FR-10). */
    show({ read: { summary: "Mostly standard.", mismatches: ["Term differs"], bespoke_asks: [] } });
    expect(screen.getByText("Aegis read")).toBeTruthy();
    expect(screen.getByText("Mostly standard.")).toBeTruthy();
    expect(screen.getByText("Doesn’t match the form")).toBeTruthy();
    expect(screen.getByText("Term differs")).toBeTruthy();
    expect(screen.queryByText("Beyond the template")).toBeNull();
  });

  it("shows confidence only when given", () => {
    /** Guards a confidence bar appearing for form-sourced requests (caller passes null). */
    const { rerender } = render(<WhatWasAsked {...base} />);
    expect(screen.queryByText("AI extraction confidence")).toBeNull();
    rerender(<WhatWasAsked {...base} confidence={{ value: 0.72, complexity: "high" }} />);
    expect(screen.getByText("72%")).toBeTruthy();
    expect(screen.getByText(/high complexity/)).toBeTruthy();
  });

  it("offers the AI priority suggestion, with apply only when allowed", () => {
    /** Guards an apply link for someone who cannot change priority, or a suggestion equal to the value. */
    const onApply = vi.fn();
    const pr = { value: "low", aiSuggests: "high", canApply: false, applying: false, onApply };
    const { rerender } = render(<WhatWasAsked {...base} priority={pr} />);
    expect(screen.getByText(/AI suggests high/)).toBeTruthy();
    expect(screen.queryByText("apply")).toBeNull();
    rerender(<WhatWasAsked {...base} priority={{ ...pr, canApply: true }} />);
    fireEvent.click(screen.getByText("apply"));
    expect(onApply).toHaveBeenCalledOnce();
    rerender(<WhatWasAsked {...base} priority={{ ...pr, aiSuggests: "low", canApply: true }} />);
    expect(screen.queryByText(/AI suggests/)).toBeNull();
  });

  it("renders the answers slot", () => {
    /** Guards losing the answers/deliverables editor entry point (FR-10). */
    show({ answers: <button>Edit deliverables</button> });
    expect(screen.getByText("Edit deliverables")).toBeTruthy();
  });
});
