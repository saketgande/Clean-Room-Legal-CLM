import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ChatPanel, ChatTab, type ChatPanelProps } from "./_chat-tab";
import { INITIAL_VIEW, type ChatView } from "@/lib/contract-chat-controller";
import type { ChatItem, ToolStatus } from "@/lib/contract-chat";
import { apiStream } from "@/lib/api";
import { assistantApi } from "@/lib/endpoints";
import type { AssistantMessage, AssistantSession } from "@/lib/types";

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { full_name: "Saket Gande" } }) }));
vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/api")>()), apiStream: vi.fn() }));
vi.mock("@/lib/endpoints", () => ({
  assistantApi: { sessions: vi.fn(), createSession: vi.fn(), messages: vi.fn(), session: vi.fn(), cancelRun: vi.fn(), confirm: vi.fn(), reject: vi.fn() },
}));

const box = () => screen.getByRole("textbox", { name: "Message Aegis about this contract" }) as HTMLInputElement;
const type = (t: string) => fireEvent.change(box(), { target: { value: t } });
const enter = (init: KeyboardEventInit = {}) => fireEvent.keyDown(box(), { key: "Enter", ...init });

// ---- the view on its own ---------------------------------------------------------

const view = (over: Partial<ChatView> = {}): ChatView => ({ ...INITIAL_VIEW, loading: false, ...over });
const item = (id: string, role: ChatItem["role"], text: string, extra: Partial<ChatItem> = {}): ChatItem => ({ id, role, text, ...extra });
const tool = (id: string, name: string, status: ToolStatus): ChatItem => ({ id, role: "tool", text: name, tool: { name, status } });

function panel(over: Partial<ChatView> = {}, props: Partial<ChatPanelProps> = {}) {
  const p: ChatPanelProps = {
    view: view(over), userInitials: "SG",
    onSend: vi.fn(), onStop: vi.fn(), onResolve: vi.fn(), onDismissError: vi.fn(), onConsumeDraft: vi.fn(), onShortcut: vi.fn(),
    ...props,
  };
  const utils = render(<ChatPanel {...p} />);
  return { p, again: (o: Partial<ChatView>) => utils.rerender(<ChatPanel {...p} view={view(o)} />) };
}

const stageCard = {
  confirmationId: "conf-1", assistantRunId: "run-1", toolName: "advance_contract_stage",
  details: { contract: "Acme MSA", move: "Review to Approval", effect: "Creates the approval chain and moves the contract to Approval." },
};


describe("the starter questions", () => {
  it("are the ones for the stage the contract is at", () => {
    panel({ items: [] }, { starters: ["Summarise the key risks", "What are the key dates and obligations?"] });
    expect(screen.getByRole("button", { name: "What are the key dates and obligations?" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send this for approval" })).toBeNull();
  });
});

describe("ChatPanel: an empty chat", () => {
  it("starts with a welcome and the starter questions, and a starter sends", () => {
    const { p } = panel();
    expect(screen.getByText(/Ask about this contract, or tell me what to change/)).toBeInTheDocument();
    expect(screen.getByText(/accept or reject them in the Review tab/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Send this for approval" }));
    expect(p.onSend).toHaveBeenCalledWith("Send this for approval");
  });

  it("shows what the page passed in place of the default welcome", () => {
    panel({}, { intro: "Acme MSA is in Review with 3 open issues." });
    expect(screen.getByText("Acme MSA is in Review with 3 open issues.")).toBeInTheDocument();
    expect(screen.queryByText(/Ask about this contract, or tell me/)).toBeNull();
  });

  it("says it is opening while it loads, with nothing to click yet", () => {
    panel({ loading: true });
    expect(screen.getByText("Opening the chat…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send this for approval" })).toBeNull();
    expect(screen.queryByText(/Ask about this contract, or tell me/)).toBeNull();
  });

  it("takes the starters away once there is a conversation, and holds them while busy", () => {
    const { again } = panel({ items: [item("1", "user", "Hi")] });
    expect(screen.queryByRole("button", { name: "Send this for approval" })).toBeNull();
    again({ items: [], resolving: true });
    expect(screen.getByRole("button", { name: "Send this for approval" })).toBeDisabled();
  });
});

describe("ChatPanel: the conversation", () => {
  it("shows the question with the person's initials and the answer", () => {
    panel({ items: [item("1", "user", "Is the cap mutual?"), item("2", "assistant", "Yes, clause 9 caps both sides.")] });
    expect(screen.getByText("Is the cap mutual?")).toBeInTheDocument();
    expect(screen.getByText("SG")).toBeInTheDocument();
    expect(screen.getByText("Yes, clause 9 caps both sides.")).toBeInTheDocument();
  });

  it("shows each step by name, running or done", () => {
    panel({ items: [item("1", "user", "Tighten it"), tool("2", "read_contract", "done"), tool("3", "edit_contract", "running"), tool("4", "find_in_contract", "error")] });
    expect(screen.getByText("Read the contract")).toBeInTheDocument();
    expect(screen.getByText("Drafting suggested changes")).toBeInTheDocument();
    expect(screen.getByText("Searching the contract didn't work")).toBeInTheDocument();
  });

  it("shows a step the rules refused as not done, in its own style and not as a failure", () => {
    panel({ items: [item("1", "user", "Send it on"), tool("2", "advance_contract_stage", "refused"), item("3", "assistant", "It is already at Signature.")] });
    const step = screen.getByText("Moving the contract — not done").closest(".cht-step");
    expect(step).toHaveClass("refused");
    expect(step).not.toHaveClass("error");
    expect(screen.queryByText(/didn't work/)).toBeNull();
  });

  it("shows a saved answer's steps and text in the order they happened", () => {
    panel({
      items: [
        item("1", "user", "Review it"),
        item("2", "assistant", "Done.", { blocks: [{ type: "tool", name: "read_contract", status: "done" }, { type: "content", text: "Two clauses need work." }] }),
      ],
    });
    const log = screen.getByRole("log");
    const text = log.textContent ?? "";
    expect(text.indexOf("Read the contract")).toBeGreaterThan(-1);
    expect(text.indexOf("Read the contract")).toBeLessThan(text.indexOf("Two clauses need work."));
  });

  it("shows a note from the system as a quiet line, not as Aegis speaking", () => {
    panel({ items: [item("1", "user", "Hi"), item("2", "system", "Cancelled. Nothing was changed.")] });
    expect(screen.getByText("Cancelled. Nothing was changed.").closest(".msg")).toBeNull();
  });

  it("offers the Review tab after suggestions were drafted, and the Lifecycle tab after a stage move", () => {
    const { p } = panel({
      items: [item("1", "user", "Tighten it"), tool("2", "edit_contract", "done"), item("3", "assistant", "I drafted 2 changes."),
        item("4", "user", "Send it on"), tool("5", "advance_contract_stage", "done"), item("6", "assistant", "Moved.")],
    });
    fireEvent.click(screen.getByRole("button", { name: "Review suggestions" }));
    expect(p.onShortcut).toHaveBeenCalledWith("review");
    fireEvent.click(screen.getByRole("button", { name: "Open lifecycle" }));
    expect(p.onShortcut).toHaveBeenCalledWith("lifecycle");
  });

  it("offers nothing after a step that did not work", () => {
    panel({ items: [item("1", "user", "Tighten it"), tool("2", "edit_contract", "error")] });
    expect(screen.queryByRole("button", { name: "Review suggestions" })).toBeNull();
  });

  it("does not offer a shortcut while that answer is still being written", () => {
    panel({ streaming: true, items: [item("1", "user", "Tighten it"), tool("2", "edit_contract", "done")] });
    expect(screen.queryByRole("button", { name: "Review suggestions" })).toBeNull();
  });
});

describe("ChatPanel: working and stopping", () => {
  it("says Working… until text arrives, and offers Stop instead of Send", () => {
    const { p, again } = panel({ streaming: true, items: [item("1", "user", "Tighten it")] });
    expect(screen.getByText("Working…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(p.onStop).toHaveBeenCalledTimes(1);
    again({ streaming: true, items: [item("1", "user", "Tighten it"), item("2", "assistant", "Partly")] });
    expect(screen.queryByText("Working…")).toBeNull();
  });

  it("cannot press Stop twice", () => {
    panel({ streaming: true, stopping: true, items: [item("1", "user", "x")] });
    expect(screen.getByRole("button", { name: "Stop" })).toBeDisabled();
  });
});

describe("ChatPanel: sending", () => {
  it("sends on Enter, without the extra spaces, and empties the box", () => {
    const { p } = panel();
    type("  Tighten the payment terms  ");
    enter();
    expect(p.onSend).toHaveBeenCalledWith("Tighten the payment terms");
    expect(box().value).toBe("");
  });

  it("sends with the Send button", () => {
    const { p } = panel();
    type("Hello");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(p.onSend).toHaveBeenCalledWith("Hello");
  });

  it("does not send a blank message", () => {
    const { p } = panel();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    type("   ");
    enter();
    expect(p.onSend).not.toHaveBeenCalled();
  });

  it("does not send when Enter confirms a word in an input method", () => {
    const { p } = panel();
    type("こんにちは");
    enter({ isComposing: true });
    expect(p.onSend).not.toHaveBeenCalled();
    expect(box().value).toBe("こんにちは");
  });

  it("does not send a second message while an answer is being written", () => {
    const { p } = panel({ streaming: true, items: [item("1", "user", "x")] });
    type("another");
    enter();
    expect(p.onSend).not.toHaveBeenCalled();
    expect(box().value).toBe("another");
  });

  it("puts back a message that could not be sent, once, without replacing what is being typed", () => {
    const { p, again } = panel();
    type("something newer");
    again({ draft: "my lost message" });
    expect(box().value).toBe("something newer");
    expect(p.onConsumeDraft).toHaveBeenCalledTimes(1);
  });

  it("puts back a lost message into an empty box", () => {
    const { p } = panel({ draft: "my lost message" });
    expect(box().value).toBe("my lost message");
    expect(p.onConsumeDraft).toHaveBeenCalledTimes(1);
  });
});

describe("ChatPanel: the confirmation card", () => {
  it("asks in words, shows what will happen, and reports the choice", () => {
    const { p } = panel({ pending: stageCard, items: [item("1", "user", "Send it for approval")] });
    const card = within(screen.getByRole("group", { name: "Confirm action" }));
    expect(card.getByText("Needs your confirmation")).toBeInTheDocument();
    expect(card.getByText("Move this contract to another stage?")).toBeInTheDocument();
    expect(card.getByText("Contract")).toBeInTheDocument();
    expect(card.getByText("Acme MSA")).toBeInTheDocument();
    expect(card.getByText("Creates the approval chain and moves the contract to Approval.")).toBeInTheDocument();
    fireEvent.click(card.getByRole("button", { name: "Confirm move" }));
    expect(p.onResolve).toHaveBeenLastCalledWith(true);
    fireEvent.click(card.getByRole("button", { name: "Cancel" }));
    expect(p.onResolve).toHaveBeenLastCalledWith(false);
  });

  it("holds the message box until the card is answered", () => {
    panel({ pending: stageCard });
    expect(box()).toBeDisabled();
    expect(box().placeholder).toMatch(/Confirm or cancel the action above/);
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("cannot be answered twice while the answer is being recorded", () => {
    panel({ pending: stageCard, resolving: true });
    expect(screen.getByRole("button", { name: "Confirm move" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });

  it("still asks sensibly for an action it has no special wording for", () => {
    panel({ pending: { ...stageCard, toolName: "add_contract_comment", details: {} } });
    expect(screen.getByText("Confirm: add contract comment?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeInTheDocument();
  });
});

describe("ChatPanel: when something goes wrong", () => {
  it("shows the problem and lets it be dismissed", () => {
    const { p } = panel({ error: "Rate limited. Try again in a minute." });
    expect(screen.getByRole("alert")).toHaveTextContent("Rate limited. Try again in a minute.");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(p.onDismissError).toHaveBeenCalledTimes(1);
  });

  it("explains an answer that did not finish, and can ask the same question again", () => {
    const { p } = panel({ notice: { message: "This answer was cut off before it finished.", retryText: "Summarise the key risks", retryRunId: null }, items: [item("1", "user", "Summarise the key risks")] });
    expect(screen.getByRole("status")).toHaveTextContent("This answer was cut off before it finished.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(p.onSend).toHaveBeenCalledWith("Summarise the key risks");
  });

  it("asks an answer that wrote nothing again as the same stored question, so it is not added to the chat twice", () => {
    const { p } = panel({ notice: { message: "Aegis could not answer.", retryText: "Summarise the key risks", retryRunId: "run-7" }, items: [item("1", "user", "Summarise the key risks")] });
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(p.onSend).toHaveBeenCalledWith("Summarise the key risks", "run-7");
  });

  it("offers no retry when there is nothing to send again, and hides the note while answering", () => {
    const { again } = panel({ notice: { message: "You stopped this answer.", retryText: null, retryRunId: null }, items: [item("1", "user", "x")] });
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    again({ notice: { message: "You stopped this answer.", retryText: null, retryRunId: null }, items: [item("1", "user", "x")], streaming: true });
    expect(screen.queryByRole("status")).toBeNull();
  });
});

// ---- the tab, with a pretend server -----------------------------------------------

const session = (over: Partial<AssistantSession> = {}): AssistantSession => ({
  id: "s-1", org_id: "o-1", session_type: "contract", title: null, contract_id: "c-1", tabular_review_id: null,
  status: "active", created_by_user_id: "u-1", created_at: "2026-10-07T10:00:00Z", updated_at: "2026-10-07T10:00:00Z", ...over,
});
const saved = (id: string, role: "user" | "assistant", content: string): AssistantMessage => ({
  id, session_id: "s-1", org_id: "o-1", role, content, citations: null, metadata_json: {}, created_at: "2026-10-07T10:00:00Z", created_by_user_id: null,
});
const idle = { session: session(), contract_handles: [], pending_confirmation: null, latest_run: null };

type Cb = Parameters<typeof apiStream>[2];
interface Call { path: string; body: unknown; cb: Cb; abort: ReturnType<typeof vi.fn> }

describe("ChatTab", () => {
  const api = vi.mocked(assistantApi);
  const stream = vi.mocked(apiStream);
  let calls: Call[];
  const onSettled = vi.fn();
  const onOpenReview = vi.fn();
  const onOpenLifecycle = vi.fn();

  const mount = () => render(<ChatTab contractId="c-1" onSettled={onSettled} onOpenReview={onOpenReview} onOpenLifecycle={onOpenLifecycle} />);
  const emit = (i: number, event: string, data: Record<string, unknown> = {}, id?: string) =>
    act(async () => {
      calls[i].cb.onEvent(event, data, id);
    });

  beforeEach(() => {
    vi.resetAllMocks();
    calls = [];
    stream.mockImplementation(async (path, body, cb) => {
      const abort = vi.fn();
      calls.push({ path, body, cb, abort });
      return abort;
    });
    api.sessions.mockResolvedValue([]);
    api.messages.mockResolvedValue([]);
    api.session.mockResolvedValue(idle);
    api.createSession.mockResolvedValue(session());
    api.confirm.mockResolvedValue({ confirmation_id: "conf-1", status: "confirmed", assistant_run_id: "run-1", resume_required: true });
    api.reject.mockResolvedValue({ confirmation_id: "conf-1", status: "rejected", assistant_run_id: "run-1", resume_required: false });
    api.cancelRun.mockResolvedValue({ assistant_run_id: "run-1", run_status: "cancelled", cancel_requested: true });
  });

  it("looks for this contract's chat, finds none, and starts one with the first question", async () => {
    mount();
    expect(screen.getByText("Opening the chat…")).toBeInTheDocument();
    await screen.findByText(/Ask about this contract, or tell me what to change/);
    expect(api.sessions).toHaveBeenCalledWith({ contract_id: "c-1" });

    fireEvent.click(screen.getByRole("button", { name: "Summarise the key risks" }));
    await waitFor(() => expect(stream).toHaveBeenCalledTimes(1));
    expect(api.createSession).toHaveBeenCalledWith({ session_type: "contract", contract_id: "c-1", title: "Summarise the key risks" });
    expect(calls[0].path).toBe("/assistant/sessions/s-1/stream");
    expect(calls[0].body).toEqual({ message: "Summarise the key risks", contract_ids: ["c-1"] });
    expect(screen.getByText("Summarise the key risks")).toBeInTheDocument();
    expect(screen.getByText("Working…")).toBeInTheDocument();

    await emit(0, "session_started", { assistant_run_id: "run-1" }, "1");
    await emit(0, "message_delta", { text: "Three " }, "2");
    await emit(0, "message_delta", { text: "risks." }, "3");
    expect(screen.getByText("Three risks.")).toBeInTheDocument();
    await emit(0, "done", {}, "4");
    await waitFor(() => expect(screen.queryByText("Working…")).toBeNull());
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
    // Nothing on the contract changed, so the page is not asked to refetch.
    expect(onSettled).not.toHaveBeenCalled();
  });

  it("a stage move waits for a yes, then continues, refreshes the page and offers the Lifecycle tab", async () => {
    mount();
    await screen.findByText(/Ask about this contract/);
    type("Send this for approval");
    enter();
    await waitFor(() => expect(stream).toHaveBeenCalledTimes(1));

    await emit(0, "session_started", { assistant_run_id: "run-1" });
    await emit(0, "tool_started", { tool_name: "advance_contract_stage", tool_use_id: "t1" });
    await emit(0, "confirmation_required", {
      confirmation_id: "conf-1", assistant_run_id: "run-1", tool_name: "advance_contract_stage",
      details: { contract: "Acme MSA", effect: "Creates the approval chain and moves the contract to Approval." },
    });
    const card = within(screen.getByRole("group", { name: "Confirm action" }));
    expect(card.getByText("Creates the approval chain and moves the contract to Approval.")).toBeInTheDocument();
    expect(screen.queryByText("Working…")).toBeNull();
    expect(api.confirm).not.toHaveBeenCalled();
    expect(onSettled).not.toHaveBeenCalled();

    fireEvent.click(card.getByRole("button", { name: "Confirm move" }));
    await waitFor(() => expect(stream).toHaveBeenCalledTimes(2));
    expect(api.confirm).toHaveBeenCalledWith("conf-1");
    expect(calls[1].path).toBe("/assistant/runs/run-1/resume?confirmation_id=conf-1");
    expect(screen.queryByRole("group", { name: "Confirm action" })).toBeNull();

    await emit(1, "tool_finished", { tool_use_id: "t1" });
    await emit(1, "message_delta", { text: "Moved to Approval." });
    await emit(1, "done", {});
    await waitFor(() => expect(onSettled).toHaveBeenCalledTimes(1));
    expect(screen.getByText("Moved the contract")).toBeInTheDocument();
    expect(screen.getByText("Moved to Approval.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open lifecycle" }));
    expect(onOpenLifecycle).toHaveBeenCalledTimes(1);
  });

  it("Cancel on the card tells the server and changes nothing", async () => {
    mount();
    await screen.findByText(/Ask about this contract/);
    type("Send this for approval");
    enter();
    await waitFor(() => expect(stream).toHaveBeenCalledTimes(1));
    await emit(0, "session_started", { assistant_run_id: "run-1" });
    await emit(0, "confirmation_required", { confirmation_id: "conf-1", assistant_run_id: "run-1", tool_name: "advance_contract_stage", details: {} });

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(api.reject).toHaveBeenCalledWith("conf-1", "Rejected by user"));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Confirm action" })).toBeNull());
    expect(stream).toHaveBeenCalledTimes(1);
    expect(onSettled).not.toHaveBeenCalled();
    expect(box()).not.toBeDisabled();
  });

  it("reopens the saved conversation and the confirmation it was waiting on", async () => {
    api.sessions.mockResolvedValue([session()]);
    api.messages.mockResolvedValue([saved("m1", "user", "Send this for approval"), saved("m2", "assistant", "I need your go-ahead to do that.")]);
    api.session.mockResolvedValue({
      ...idle,
      pending_confirmation: { confirmation_id: "conf-9", assistant_run_id: "run-9", tool_name: "advance_contract_stage", details: { contract: "Acme MSA" }, expires_at: null },
      latest_run: { id: "run-9", status: "waiting_confirmation", error_message: null, user_message_id: "m1", has_answer: true },
    });
    mount();
    await screen.findByText("I need your go-ahead to do that.");
    expect(screen.getByText("Send this for approval")).toBeInTheDocument();
    expect(await screen.findByRole("group", { name: "Confirm action" })).toBeInTheDocument();
    expect(screen.getByText("Acme MSA")).toBeInTheDocument();
    expect(stream).not.toHaveBeenCalled();
  });

  it("keeps watching an answer that was still being written when the page was left", async () => {
    api.sessions.mockResolvedValue([session()]);
    api.messages.mockResolvedValue([saved("m1", "user", "Tighten the payment terms")]);
    api.session.mockResolvedValue({ ...idle, latest_run: { id: "run-3", status: "running", error_message: null, user_message_id: "m1", has_answer: false } });
    mount();
    await waitFor(() => expect(stream).toHaveBeenCalledTimes(1));
    expect(calls[0].path).toBe("/assistant/runs/run-3/events");
    expect(calls[0].body).toBeNull();
    await emit(0, "message_delta", { text: "Drafted two changes." });
    expect(screen.getByText("Drafted two changes.")).toBeInTheDocument();
  });

  it("shows why it could not start, and keeps what was typed", async () => {
    api.createSession.mockRejectedValue(new Error("You can't open a chat on this contract."));
    mount();
    await screen.findByText(/Ask about this contract/);
    type("Tighten the payment terms");
    enter();
    expect(await screen.findByRole("alert")).toHaveTextContent("You can't open a chat on this contract.");
    await waitFor(() => expect(box().value).toBe("Tighten the payment terms"));
    expect(stream).not.toHaveBeenCalled();
  });

  it("says so when the chat itself cannot be opened", async () => {
    api.sessions.mockRejectedValue(new Error("Network down"));
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("Network down");
    expect(screen.queryByText("Opening the chat…")).toBeNull();
  });

  it("stops listening when the tab is closed", async () => {
    const { unmount } = mount();
    await screen.findByText(/Ask about this contract/);
    type("Hello");
    enter();
    await waitFor(() => expect(stream).toHaveBeenCalledTimes(1));
    unmount();
    expect(calls[0].abort).toHaveBeenCalled();
  });

  it("Review suggestions goes to the Review tab", async () => {
    mount();
    await screen.findByText(/Ask about this contract/);
    type("Tighten the payment terms");
    enter();
    await waitFor(() => expect(stream).toHaveBeenCalledTimes(1));
    await emit(0, "session_started", { assistant_run_id: "run-1" });
    await emit(0, "tool_started", { tool_name: "edit_contract", tool_use_id: "t1" });
    await emit(0, "tool_finished", { tool_use_id: "t1" });
    await emit(0, "message_delta", { text: "I drafted 2 changes." });
    await emit(0, "done", {});
    await waitFor(() => expect(onSettled).toHaveBeenCalledTimes(1));
    fireEvent.click(await screen.findByRole("button", { name: "Review suggestions" }));
    expect(onOpenReview).toHaveBeenCalledTimes(1);
  });

  it("shows what the page puts above the conversation", async () => {
    render(<ChatTab contractId="c-1" banner={<div>The AI review was interrupted</div>} onSettled={onSettled} onOpenReview={onOpenReview} onOpenLifecycle={onOpenLifecycle} />);
    expect(await screen.findByText("The AI review was interrupted")).toBeInTheDocument();
    await screen.findByText(/Ask about this contract/);
  });
});

describe("ChatPanel: the banner", () => {
  it("sits above the conversation, in an empty chat and in one that has begun", () => {
    const { again } = panel({}, { banner: <div>Heads up</div> });
    const banner = screen.getByText("Heads up");
    const log = screen.getByRole("log");
    expect(banner.compareDocumentPosition(log) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    again({ items: [item("1", "user", "Hello"), item("2", "assistant", "Hi")] });
    expect(screen.getByText("Heads up")).toBeInTheDocument();
  });

  it("is not there when the page has nothing to say", () => {
    panel();
    expect(document.querySelector(".cht-banner")).toBeNull();
  });
});
