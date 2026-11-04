import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { aiIsWorking, LifecycleView, stepFacts } from "./lifecycle-view";

// Two screens put this view in a page header: the request overview (the grid of stages, in the
// app's own colours) and the contract page (one slim row, in that page's colours). The slim row is
// drawn only when asked for, so the request overview keeps looking the way it did.

const props = { steps: [], current: "review" as const, comments: [], extras: {} };

describe("LifecycleView in a page header", () => {
  it("draws the grid of stages when only `compact` is set (the request overview)", () => {
    const { container } = render(<LifecycleView compact {...props} />);
    expect(container.querySelector(".lcs-row")).toBeNull();
    expect(container.querySelector(".lcs-strip")).toBeNull();
    expect(screen.getAllByRole("button").length).toBeGreaterThanOrEqual(7);
  });

  it("draws one slim row with a button per stage when `strip` is set too (the contract page)", () => {
    const { container } = render(<LifecycleView compact strip {...props} />);
    expect(container.querySelector(".lcs-row")).not.toBeNull();
    const stages = container.querySelectorAll(".lcs-st");
    expect(stages).toHaveLength(7);
    expect(container.querySelector(".lcs-st.current")).toHaveTextContent("Review");
    expect(container.querySelectorAll(".lcs-st.done")).toHaveLength(2); // intake and drafting are behind it
  });

  it("shows what it is given at the end of the slim row, and nothing when given nothing", () => {
    const { container, rerender } = render(<LifecycleView compact strip trailing={<span>Day 3 of 7</span>} {...props} />);
    expect(container.querySelector(".lcs-trail")).toHaveTextContent("Day 3 of 7");
    rerender(<LifecycleView compact strip {...props} />);
    expect(container.querySelector(".lcs-trail")).toBeNull();
  });

  it("opens a stage's steps when its button is pressed, and closes them when pressed again", () => {
    render(<LifecycleView compact strip {...props} />);
    const review = screen.getByRole("button", { name: /^Review: now/ });
    expect(review).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(review);
    expect(screen.getByRole("button", { name: /^Review: now/ })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: /^Review: now/ }));
    expect(screen.getByRole("button", { name: /^Review: now/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("without `compact`, opens on the current stage", () => {
    render(<LifecycleView {...props} onSelectStep={vi.fn()} />);
    expect(screen.getAllByRole("button").some((b) => b.getAttribute("aria-pressed") === "true")).toBe(true);
  });
});

describe("the stage captions count what happened", () => {
  const stepOf = (idx: number, status: string, stage: string) => ({
    idx, type: "human_task", name: `Step ${idx}`, status, assignee_user_id: null, assignee_label: null, team_id: null,
    team_label: null, note: null, result: null, updated_at: null, stage,
  }) as never;
  const captionOf = (steps: never[], stage: string) => {
    const { container } = render(<LifecycleView compact strip {...props} steps={steps} current="approval" />);
    const li = [...container.querySelectorAll(".lcs-st")].find((x) => x.textContent?.includes(stage));
    return li?.querySelector("button")?.getAttribute("title");
  };

  it("does not call a skipped step done", () => {
    expect(captionOf([stepOf(0, "done", "review"), stepOf(1, "skipped", "review"), stepOf(2, "skipped", "review")], "Review"))
      .toBe("1 of 1 done, 2 skipped");
  });

  it("says so when every step of a stage was skipped", () => {
    expect(captionOf([stepOf(0, "skipped", "review")], "Review")).toBe("All skipped");
  });

  it("keeps the plain wording when nothing was skipped", () => {
    expect(captionOf([stepOf(0, "done", "review"), stepOf(1, "done", "review")], "Review")).toBe("2 of 2 done");
  });
});

// The Active stage's obligation row: a failed extraction is red, says so on the stage without opening it, and offers
// Retry (or Re-read) only to someone who may press it. The button is its own button, never inside the row's.
describe("a failed obligation extraction in the Active stage", () => {
  const retry = { kind: "extract_obligations" as const, label: "Retry", contract_id: "c-1" };
  const failedRow = {
    name: "Obligation extraction failed", kind: "AI", detail: "The AI's answer was cut off. Tried 3 times.", who: "Aegis AI",
    status: "failed" as const, when: null, action: retry,
  };
  const withRow = { ...props, current: "active" as const, extras: { active: [failedRow] } };
  const open = () => fireEvent.click(screen.getByRole("button", { name: /^Active: now/ }));

  it("says Needs attention on the stage without opening it, in the grid and on the slim row", () => {
    const grid = render(<LifecycleView compact {...withRow} />);
    expect(grid.container).toHaveTextContent("Needs attention · Obligation extraction failed");
    grid.unmount();
    const strip = render(<LifecycleView compact strip {...withRow} />);
    const active = [...strip.container.querySelectorAll(".lcs-st")].find((li) => li.textContent?.includes("Active"));
    expect(active).toHaveClass("attn");
    expect(active?.querySelector("button")).toHaveAttribute("title", "Needs attention · Obligation extraction failed");
  });

  it("shows the row red with the plain reason", () => {
    render(<LifecycleView compact {...withRow} />);
    open();
    expect(screen.getByText("Obligation extraction failed")).toBeInTheDocument();
    expect(screen.getByText(/cut off/)).toBeInTheDocument();
    expect(screen.getByText("Failed").closest("li")).toHaveAttribute("data-tone", "failed");
  });

  it("draws no button unless the page passes onAction (a viewer who may not press it)", () => {
    render(<LifecycleView compact {...withRow} />);
    open();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("draws Retry as a sibling of the row's own button, not inside it", () => {
    render(<LifecycleView compact {...withRow} onAction={vi.fn()} />);
    open();
    const button = screen.getByRole("button", { name: "Retry" });
    expect(button.closest("li")?.querySelectorAll("button")).toHaveLength(2);
    expect(button.parentElement?.tagName).toBe("LI");
  });

  it("calls onAction with the row's action and is disabled while it works", async () => {
    let finish: () => void = () => {};
    const onAction = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    render(<LifecycleView compact {...withRow} onAction={onAction} />);
    open();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onAction).toHaveBeenCalledWith(retry);
    expect(await screen.findByRole("button", { name: "Starting…" })).toBeDisabled();
    finish();
    expect(await screen.findByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("shows the server's reason under the list when the press is refused", async () => {
    const onAction = vi.fn(() => Promise.reject(new Error("Obligations were already read for this version.")));
    render(<LifecycleView compact {...withRow} onAction={onAction} />);
    open();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already read for this version");
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("keeps a refusal under the stage it came from, not under whichever stage is opened next", async () => {
    const onAction = vi.fn(() => Promise.reject(new Error("Obligations were already read for this version.")));
    render(<LifecycleView compact {...withRow} onAction={onAction} />);
    open();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Closed:/ }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("puts what needs attention in the stage button's accessible name, which replaces its visible text", () => {
    render(<LifecycleView compact strip {...withRow} />);
    expect(screen.getByRole("button", { name: "Active: now. Needs attention: Obligation extraction failed. Show its steps." }))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Closed: not reached yet. Show its steps." })).toBeInTheDocument();
  });

  it("gives the action button a class the contract page's own button reset cannot strip bare", () => {
    render(<LifecycleView compact {...withRow} onAction={vi.fn()} />);
    open();
    expect(screen.getByRole("button", { name: "Retry" })).toHaveClass("lc-act");
  });

  it("keeps the old look for a row with no problem: no attention caption", () => {
    const calm = { ...withRow, extras: { active: [{ ...failedRow, status: "done" as const, action: null, name: "Obligations tracked" }] } };
    const { container } = render(<LifecycleView compact {...calm} />);
    expect(container).not.toHaveTextContent("Needs attention");
  });
});

describe("aiIsWorking", () => {
  const row = (over: object) => ({ name: "x", kind: "AI", detail: "", who: "", status: "waiting", when: null, ...over }) as never;
  it("is true only while an AI row waits with nothing to press, so the page keeps refetching until it settles", () => {
    expect(aiIsWorking({ active: [row({})] })).toBe(true);
    expect(aiIsWorking({ active: [row({ action: { kind: "extract_obligations", label: "Re-read", contract_id: "c" } })] })).toBe(false);
    expect(aiIsWorking({ active: [row({ status: "failed" })] })).toBe(false);
    expect(aiIsWorking({ active: [row({ kind: "Renewal" })] })).toBe(false);
    expect(aiIsWorking(undefined)).toBe(false);
  });
});

describe("stepFacts", () => {
  const team = { id: "t1", name: "Finance", members: [{ user_id: "u1", name: "Asha", active: true }, { user_id: "u2", name: "Gone", active: false }] } as never;
  const open = (over: object) => ({
    idx: 0, type: "approval", name: "Sign-off", status: "waiting_human", assignee_user_id: null, assignee_label: null,
    team_id: "t1", team_label: "Finance", note: null, result: null, updated_at: null, ...over,
  }) as never;

  it("lets the live approval win over the team, and lists active members only when it waits on a team", () => {
    /* Guards the tiles and the next-step card telling different stories about who holds a step. */
    const live = stepFacts(open({ approval: { approver_label: "Casey", step_order: 1, due_at: "2026-10-01T00:00:00Z", overdue: true } }), [team]);
    expect(live).toMatchObject({ who: "Casey", members: [], overdue: true });
    const waiting = stepFacts(open({}), [team]);
    expect(waiting).toMatchObject({ who: "Finance team", members: ["Asha"], due: null, overdue: false });
  });
});
