import { describe, expect, it, vi } from "vitest";
import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { ActivityCard, AskAegisCard, CommentsCard, CARDS_CSS, CounterpartyCard, DocumentsCard, PeopleCard } from "./_request-cards";
import type { IntakeDocument, RequestActivity, RequestActivityItem, RequestCounterparty } from "@/lib/types";

const doc = (over: Partial<IntakeDocument> = {}): IntakeDocument => ({
  id: "d1", filename: "msa.pdf", mime_type: "application/pdf", size_bytes: 1, extracted_chars: 10, extraction_quality: null, created_at: null, ...over,
});

const cpItem = (i: number) => ({ id: `c${i}`, title: `Contract ${i}`, contract_type: "msa", stage: "active", value: 5000, currency: "USD", created_at: "2026-03-02T00:00:00Z", ended: false });
const rqItem = (i: number) => ({ id: `r${i}`, ref: `REQ-${i}`, type_label: "NDA", status: "approved", date: "2026-02-01T00:00:00Z" });
const cpData = (nc: number, nr: number, over: Partial<RequestCounterparty> = {}): RequestCounterparty => ({
  name: "Globex", summary: { contracts: nc, active: 2, requests: nr }, note: null,
  contracts: Array.from({ length: nc }, (_, i) => cpItem(i + 1)), requests: Array.from({ length: nr }, (_, i) => rqItem(i + 1)), ...over,
});
const cpProps = { parties: [{ name: "Globex" }], onRetry: vi.fn(), onOpenRequest: vi.fn() };

describe("CounterpartyCard", () => {
  it("shows the name, the summary line and 5 + 5 items with Show more revealing the rest", () => {
    /* Guards the first batch being unbounded, or Show more miscounting what is left. */
    render(<CounterpartyCard status="ready" data={cpData(8, 7)} {...cpProps} />);
    expect(screen.getByText("Globex")).toBeInTheDocument();
    expect(screen.getByText("8 other contracts · 2 active · 7 earlier requests")).toBeInTheDocument();
    expect(screen.getAllByText(/^Contract \d$/)).toHaveLength(5);
    fireEvent.click(screen.getByRole("button", { name: "Show 3 more" }));
    expect(screen.getAllByText(/^Contract \d$/)).toHaveLength(8);
    expect(screen.getAllByText(/^REQ-\d · NDA$/)).toHaveLength(5);
    fireEvent.click(screen.getByRole("button", { name: "Show 2 more" }));
    expect(screen.getAllByText(/^REQ-\d · NDA$/)).toHaveLength(7);
  });
  it("links a contract to its page with stage, type, value and no raw codes", () => {
    /* Guards a dead contract row or raw stage/type codes ("active", "msa") leaking into the DOM. */
    render(<CounterpartyCard status="ready" data={cpData(1, 0)} {...cpProps} />);
    expect(screen.getByRole("link", { name: "Contract 1" })).toHaveAttribute("href", "/contracts/c1");
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Msa")).toBeInTheDocument();
    expect(screen.getByText(/5,000/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\bactive\b(?! )|\bmsa\b|c1\b/);
  });
  it("calls onOpenRequest on a plain click and leaves modified clicks to the browser", () => {
    /* Guards a plain link: /intake only reads ?open= on first load, so the page would not change. */
    const onOpenRequest = vi.fn();
    render(<CounterpartyCard status="ready" data={cpData(0, 1)} {...cpProps} onOpenRequest={onOpenRequest} />);
    const a = screen.getByRole("link", { name: "REQ-1 · NDA" });
    expect(a).toHaveAttribute("href", "/intake?open=r1");
    fireEvent.click(a, { ctrlKey: true });
    expect(onOpenRequest).not.toHaveBeenCalled();
    fireEvent.click(a);
    expect(onOpenRequest).toHaveBeenCalledWith("r1");
  });
  it("says so in one line when one side is empty", () => {
    /* Guards a blank section heading with nothing under it. */
    render(<CounterpartyCard status="ready" data={cpData(2, 0)} {...cpProps} />);
    expect(screen.getByText("No earlier requests you can see")).toBeInTheDocument();
  });
  it("with nothing on file shows only the name and the note, no counts", () => {
    /* Guards "0 other contracts" and empty section headings on a first-time counterparty. */
    render(<CounterpartyCard status="ready" data={cpData(0, 0, { summary: { contracts: 0, active: 0, requests: 0 }, note: "First dealing" })} {...cpProps} />);
    expect(screen.getByText("Globex")).toBeInTheDocument();
    expect(screen.getByText("First dealing")).toBeInTheDocument();
    expect(screen.queryByText("Contracts")).toBeNull();
    expect(screen.queryByText(/other contract/)).toBeNull();
  });
  it("renders nothing while loading or without a name, and a retry line on error", () => {
    /* Guards an empty card on requests with no counterparty, and a failed load blanking the page. */
    const { container, rerender } = render(<CounterpartyCard status="ready" data={cpData(0, 0, { name: null })} {...cpProps} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<CounterpartyCard status="loading" data={undefined} {...cpProps} />);
    expect(container).toBeEmptyDOMElement();
    const onRetry = vi.fn();
    rerender(<CounterpartyCard status="error" data={undefined} {...cpProps} onRetry={onRetry} />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalled();
  });
  it("names all parties only when there is more than one", () => {
    /* Guards a repeated name line for single-party requests. */
    render(<CounterpartyCard status="ready" data={cpData(0, 0)} {...cpProps} parties={[{ name: "A" }, { name: "B" }]} />);
    expect(screen.getByText("All parties: A, B")).toBeInTheDocument();
  });
});

describe("PeopleCard", () => {
  it("offers Reassign only when given, disabled until a new owner is picked", () => {
    /* Guards AC-17: Reassign shown to someone who may not act, or enabled with nothing picked. */
    const onReassign = vi.fn();
    const { rerender } = render(<PeopleCard owner="Priya" requester="Sam" department={null} reassign={null} />);
    expect(screen.queryByRole("button", { name: "Reassign owner" })).toBeNull();
    expect(screen.getByText("owner")).toBeInTheDocument();
    rerender(<PeopleCard owner={null} requester={null} department="Legal" reassign={{ options: [{ id: "u1", name: "Lee" }], value: "", onChange: vi.fn(), onReassign, busy: false }} />);
    expect(screen.getByText("Unassigned")).toBeInTheDocument();
    expect(screen.getByText("Legal")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Pick a new owner…" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reassign owner" })).toBeDisabled();
    rerender(<PeopleCard owner="P" requester="S" department={null} reassign={{ options: [], value: "u1", onChange: vi.fn(), onReassign, busy: false }} />);
    fireEvent.click(screen.getByRole("button", { name: "Reassign owner" }));
    expect(onReassign).toHaveBeenCalledTimes(1);
  });
});

describe("DocumentsCard", () => {
  it("collapses to one line with no CLM link when nothing is attached and there is no contract", () => {
    /* Guards FR-9: an empty documents card, or an Open in CLM link to a contract that does not exist. */
    render(<DocumentsCard docs={[]} contractHref={null} contractActions={null} trademark={null} />);
    expect(screen.getByText("No documents attached.")).toBeInTheDocument();
    expect(screen.queryByText(/Open in CLM editor/)).toBeNull();
  });
  it("lists files and links to the contract editor once", () => {
    /* Guards AC-16: the one editor link lives here, pointing at the contract. */
    render(<DocumentsCard docs={[doc(), doc({ id: "d2", filename: "a.docx", mime_type: "application/msword", extracted_chars: 0 })]} contractHref="/contracts/c1" contractActions={null} trademark={null} />);
    expect(screen.getByText("msa.pdf")).toBeInTheDocument();
    expect(screen.getByText("parsed")).toBeInTheDocument();
    expect(screen.getByText("attached")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /Open in CLM editor/ })).toHaveLength(1);
    expect(screen.getByRole("link", { name: /Open in CLM editor/ })).toHaveAttribute("href", "/contracts/c1");
  });
  it("runs the contract and trademark actions with an attachment", () => {
    /* Guards FR-18: each action wired to its own callback, with the attached-file wording. */
    const h = { onIngest: vi.fn(), onDraft: vi.fn(), onContinue: vi.fn() };
    render(<DocumentsCard docs={[]} contractHref={null} contractActions={{ docType: "NDA", hasAttachment: true, busy: false, onIngest: h.onIngest, onDraft: h.onDraft }} trademark={{ busy: false, onContinue: h.onContinue }} />);
    fireEvent.click(screen.getByRole("button", { name: "Use attached as the NDA →" }));
    fireEvent.click(screen.getByRole("button", { name: "Approve & draft fresh" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue as Trademark →" }));
    expect(h.onIngest).toHaveBeenCalled(); expect(h.onDraft).toHaveBeenCalled(); expect(h.onContinue).toHaveBeenCalled();
  });
  it("offers one draft button without an attachment, disabled while busy", () => {
    /* Guards a double-submitted draft while the first is running. */
    render(<DocumentsCard docs={[]} contractHref={null} contractActions={{ docType: "MSA", hasAttachment: false, busy: true, onIngest: vi.fn(), onDraft: vi.fn() }} trademark={null} />);
    expect(screen.getByRole("button", { name: "Approve & draft the MSA →" })).toBeDisabled();
    expect(screen.queryByText(/Use attached/)).toBeNull();
  });
});

describe("AskAegisCard", () => {
  it("shows the three chips and no canned hint sentence while the chat is empty", () => {
    /* Guards FR-8: the removed "Ask about this request — …" filler coming back. */
    const onAsk = vi.fn();
    render(<AskAegisCard scope="portfolio" chat={[]} pending={false} q="" onQ={vi.fn()} onSend={vi.fn()} onAsk={onAsk} />);
    expect(screen.queryByText(/Ask about this request/)).toBeNull();
    expect(screen.getByText("portfolio")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "What are the key risks here?" }));
    expect(onAsk).toHaveBeenCalledWith("What are the key risks here?");
  });
  it("shows the log with source counts and sends on Enter", () => {
    /* Guards the chat log or the Enter-to-send path being lost in the move. */
    const onSend = vi.fn();
    render(<AskAegisCard scope="this contract" chat={[{ role: "you", text: "hi" }, { role: "ai", text: "yo", cites: 2 }]} pending q="x" onQ={vi.fn()} onSend={onSend} onAsk={vi.fn()} />);
    expect(screen.getByText("· 2 sources")).toBeInTheDocument();
    expect(screen.getByText("Thinking…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "What are the key risks here?" })).toBeNull();
    fireEvent.keyDown(screen.getByLabelText("Ask a question"), { key: "Enter" });
    expect(onSend).toHaveBeenCalled();
  });
});

describe("CommentsCard", () => {
  it("collapses to one line without a composer for a viewer who cannot act", () => {
    /* Guards FR-14/FR-9: the empty-state paragraph returning, or a composer shown without canAct. */
    render(<CommentsCard comments={[]} composer={null} />);
    expect(screen.getByText("No comments yet.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Add a comment or question")).toBeNull();
  });
  it("marks a sent-back entry with its step and posts on Enter", () => {
    /* Guards AC-13: the return note losing its step or "sent back" mark; Enter not posting. */
    const onPost = vi.fn();
    render(<CommentsCard comments={[{ actor: "Ana", at: "2026-10-01T10:00:00Z", text: "Fix clause 4", kind: "return", stepLabel: "Review" }, { actor: "Bo", at: "2026-10-02T10:00:00Z", text: "done", kind: "reply", stepLabel: null }]}
      composer={{ value: "hello", onChange: vi.fn(), onPost, posting: false, inputRef: createRef<HTMLInputElement>() }} />);
    expect(screen.getByText("Fix clause 4")).toBeInTheDocument();
    expect(screen.getByText(/on Review/)).toBeInTheDocument();
    expect(screen.getByText(/sent back/)).toBeInTheDocument();
    expect(screen.getByText(/replied to the rejection/)).toBeInTheDocument();
    fireEvent.keyDown(screen.getByLabelText("Add a comment or question"), { key: "Enter" });
    expect(onPost).toHaveBeenCalledTimes(1);
  });
  it("disables Post for blank text", () => {
    /* Guards posting empty comments. */
    render(<CommentsCard comments={[]} composer={{ value: "  ", onChange: vi.fn(), onPost: vi.fn(), posting: false, inputRef: createRef<HTMLInputElement>() }} />);
    expect(screen.getByRole("button", { name: "Post" })).toBeDisabled();
  });
});

const ev = (i: number, over: Partial<RequestActivityItem> = {}): RequestActivityItem => ({
  id: `e${i}`, at: "2026-10-14T10:00:00Z", sentence: `Event ${i} happened`, who: "Sam", source: "request", tone: "plain", system: false, ...over,
});
const act = (items: RequestActivityItem[], truncated = false): RequestActivity => ({ items, truncated });

describe("ActivityCard", () => {
  it("shows a loading line and an error line with Retry, never a blank card", () => {
    /* Guards a failed activity load blanking the card or the page. */
    const onRetry = vi.fn();
    const { rerender } = render(<ActivityCard status="loading" data={undefined} onRetry={onRetry} />);
    expect(screen.getByText("Loading activity…")).toBeInTheDocument();
    rerender(<ActivityCard status="error" data={undefined} onRetry={onRetry} />);
    expect(screen.getByText(/Activity could not be loaded/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
  it("says there is no activity, with no system toggle", () => {
    /* Guards an empty card and a pointless toggle when nothing is hidden. */
    render(<ActivityCard status="ready" data={act([])} onRetry={vi.fn()} />);
    expect(screen.getByText("No activity yet.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /system details/ })).toBeNull();
  });
  it("groups by day, newest day first and newest event first, with who and time", () => {
    /* Guards the order being flipped to oldest-first (the user chose newest first) or events losing their day. */
    render(<ActivityCard status="ready" onRetry={vi.fn()} data={act([
      ev(1, { at: "2026-10-14T15:00:00" }), ev(2, { at: "2026-10-14T09:00:00" }), ev(3, { at: "2026-10-13T09:00:00", who: "Lee" }),
    ])} />);
    const text = document.body.textContent ?? "";
    expect(text.indexOf("Event 1")).toBeLessThan(text.indexOf("Event 2"));
    expect(text.indexOf("Event 2")).toBeLessThan(text.indexOf("Event 3"));
    expect(document.querySelectorAll(".day")).toHaveLength(2);
    expect(screen.getByText("· Lee")).toBeInTheDocument();
  });
  it("shows 20 at a time and Show earlier activity reveals the rest", () => {
    /* Guards a 500-row list rendered at once. */
    render(<ActivityCard status="ready" data={act(Array.from({ length: 25 }, (_, i) => ev(i + 1)))} onRetry={vi.fn()} />);
    expect(document.querySelectorAll(".ev")).toHaveLength(20);
    fireEvent.click(screen.getByRole("button", { name: "Show earlier activity" }));
    expect(document.querySelectorAll(".ev")).toHaveLength(25);
    expect(screen.queryByRole("button", { name: "Show earlier activity" })).toBeNull();
  });
  it("keeps system events hidden until the toggle, and hides them again", () => {
    /* Guards a hidden system event showing by default, and the toggle showing when nothing is hidden. */
    render(<ActivityCard status="ready" data={act([ev(1), ev(2, { system: true }), ev(3, { system: true }), ev(4)])} onRetry={vi.fn()} />);
    expect(screen.queryByText("Event 2 happened")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show system details (2)" }));
    const text = document.body.textContent ?? "";
    expect([1, 2, 3, 4].map((n) => text.indexOf(`Event ${n}`))).toEqual([...[1, 2, 3, 4].map((n) => text.indexOf(`Event ${n}`))].sort((a, b) => a - b));
    expect(screen.getByText("Event 2 happened")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Hide system details" }));
    expect(screen.queryByText("Event 3 happened")).toBeNull();
  });
  it("has no system toggle when no event is hidden, and no ids in the DOM", () => {
    /* Guards a dead toggle and opaque ids leaking into the page. */
    render(<ActivityCard status="ready" data={act([ev(1), ev(2)])} onRetry={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /system details/ })).toBeNull();
    expect(document.body.innerHTML).not.toMatch(/e1|e2/);
  });
  it("tells the reader when older activity is cut off", () => {
    /* Guards silent truncation of a very long history. */
    render(<ActivityCard status="ready" data={act([ev(1)], true)} onRetry={vi.fn()} />);
    expect(screen.getByText("Older activity is not listed here.")).toBeInTheDocument();
  });
});


describe("Counterparty card body class", () => {
  it("does not use a class other pages style as a clipped one-line label", () => {
    /* Guards a long contract title being clipped instead of wrapping: `.li-board .cp` (max-width:160px; overflow:hidden; nowrap) hit this body when it was called `cp`. */
    render(<CounterpartyCard status="ready" data={cpData(1, 1)} {...cpProps} />);
    expect(CARDS_CSS).toMatch(/\.ro \.cpbody\{[^}]*min-width:0/);
    expect(CARDS_CSS).not.toMatch(/\.ro \.cp\{/);
    expect(document.querySelector(".cb.cpbody")).not.toBeNull();
    expect(document.querySelector(".cp")).toBeNull();
  });
});
