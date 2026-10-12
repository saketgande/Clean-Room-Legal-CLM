import { describe, expect, it, vi } from "vitest";
import { ContractChatController, type ChatApi, type StreamFn } from "./contract-chat-controller";
import { HttpError, type StreamCallbacks } from "./api";
import type { AssistantMessage, AssistantSession } from "./types";

const session = (id: string, type = "contract", contractId: string | null = "c-1"): AssistantSession =>
  ({ id, session_type: type, contract_id: contractId }) as AssistantSession;

const message = (id: string, role: "user" | "assistant", content: string, metadata: Record<string, unknown> = {}): AssistantMessage =>
  ({ id, role, content, metadata_json: metadata, session_id: "s-1" }) as AssistantMessage;

function setup(over: { sessions?: AssistantSession[]; messages?: AssistantMessage[] } = {}) {
  const api = {
    sessions: vi.fn().mockResolvedValue(over.sessions ?? []),
    createSession: vi.fn().mockResolvedValue(session("s-1")),
    messages: vi.fn().mockResolvedValue(over.messages ?? []),
    session: vi.fn().mockResolvedValue({ pending_confirmation: null, latest_run: null }),
    cancelRun: vi.fn().mockResolvedValue({}),
    confirm: vi.fn().mockResolvedValue({}),
    reject: vi.fn().mockResolvedValue({}),
  };
  const streams: { path: string; body: unknown; cb: StreamCallbacks; abort: ReturnType<typeof vi.fn> }[] = [];
  const stream: StreamFn = vi.fn(async (path, body, cb) => {
    const abort = vi.fn();
    streams.push({ path, body, cb, abort });
    return abort;
  });
  const timers: { fn: () => void; ms: number; id: number }[] = [];
  let nextTimer = 1;
  const onSettled = vi.fn();
  const controller = new ContractChatController({
    contractId: "c-1",
    api: api as unknown as ChatApi,
    stream,
    onSettled,
    setTimer: (fn, ms) => {
      const id = nextTimer++;
      timers.push({ fn, ms, id });
      return id;
    },
    clearTimer: (id) => {
      const i = timers.findIndex((t) => t.id === id);
      if (i >= 0) timers.splice(i, 1);
    },
  });
  const runTimers = () => timers.splice(0).forEach((t) => t.fn());
  const emit = (i: number, event: string, data: Record<string, unknown> = {}, id?: string) => streams[i].cb.onEvent(event, data, id);
  const view = () => controller.getSnapshot();
  return { controller, api, streams, stream, timers, runTimers, emit, view, onSettled };
}

async function started(t: ReturnType<typeof setup>, text = "Tighten the payment terms", runId = "run-1") {
  await t.controller.send(text);
  t.emit(0, "session_started", { assistant_run_id: runId });
}

describe("opening the chat", () => {
  it("opens empty when this contract has no chat yet, without creating one", async () => {
    const t = setup();
    await t.controller.load();
    expect(t.view()).toMatchObject({ loading: false, items: [], pending: null });
    expect(t.api.sessions).toHaveBeenCalledWith({ contract_id: "c-1" });
    expect(t.api.createSession).not.toHaveBeenCalled();
  });

  it("opens this contract's chat and ignores chats of another kind", async () => {
    const t = setup({
      sessions: [session("g-1", "general"), session("s-9")],
      messages: [message("m1", "user", "Hello"), message("m2", "assistant", "Hi")],
    });
    await t.controller.load();
    expect(t.api.messages).toHaveBeenCalledWith("s-9");
    expect(t.view().items.map((i) => [i.role, i.text])).toEqual([["user", "Hello"], ["assistant", "Hi"]]);
    expect(t.view().loading).toBe(false);
  });

  it("does not open a chat that belongs to a different contract", async () => {
    const t = setup({ sessions: [session("s-2", "contract", "c-2")] });
    await t.controller.load();
    expect(t.api.messages).not.toHaveBeenCalled();
  });

  it("brings back the confirmation the chat was waiting on", async () => {
    const t = setup({ sessions: [session("s-9")] });
    t.api.session.mockResolvedValue({
      pending_confirmation: { confirmation_id: "conf-1", assistant_run_id: "run-1", tool_name: "advance_contract_stage", details: { Contract: "Acme MSA" } },
      latest_run: { id: "run-1", status: "waiting_confirmation", error_message: null, user_message_id: null, has_answer: false },
    });
    await t.controller.load();
    expect(t.view().pending).toEqual({ confirmationId: "conf-1", assistantRunId: "run-1", toolName: "advance_contract_stage", details: { Contract: "Acme MSA" } });
    expect(t.stream).not.toHaveBeenCalled();
  });

  it("explains an answer that did not finish, and offers the question again", async () => {
    const t = setup({ sessions: [session("s-9")], messages: [message("m1", "user", "Tighten clause 5")] });
    t.api.session.mockResolvedValue({
      pending_confirmation: null,
      latest_run: { id: "run-1", status: "failed", error_message: "Provider error", user_message_id: "m1", has_answer: false },
    });
    await t.controller.load();
    expect(t.view().notice).toEqual({ message: "Provider error", retryText: "Tighten clause 5", retryRunId: "run-1" });
  });

  it("says a failed answer once: the red error is dropped when the explanation below the question says the same", async () => {
    const t = setup({ sessions: [session("s-9")] });
    const failed = "Aegis couldn't finish this answer. Please try again (reference abc).";
    t.api.messages.mockResolvedValue([message("m1", "user", "What is the notice period?")]);
    t.api.session.mockResolvedValue({
      pending_confirmation: null,
      latest_run: { id: "run-1", status: "failed", error_message: failed, user_message_id: "m1", has_answer: false },
    });
    await t.controller.send("What is the notice period?");
    t.emit(0, "session_started", { assistant_run_id: "run-1" });
    t.emit(0, "error", { message: failed });
    expect(t.view().error).toBe(failed);
    t.emit(0, "done", {});
    await vi.waitFor(() => expect(t.view().notice).not.toBeNull());
    expect(t.view().notice?.message).toBe(failed);
    expect(t.view().error).toBeNull();
  });

  it("keeps an error that says something the explanation does not", async () => {
    const t = setup({ sessions: [session("s-9")] });
    t.api.messages.mockResolvedValue([message("m1", "user", "Hello")]);
    t.api.session.mockResolvedValue({
      pending_confirmation: null,
      latest_run: { id: "run-1", status: "failed", error_message: "Provider error", user_message_id: "m1", has_answer: false },
    });
    await t.controller.send("Hello");
    t.emit(0, "session_started", { assistant_run_id: "run-1" });
    t.emit(0, "error", { message: "Rate limited, retry in 20s" });
    t.emit(0, "done", {});
    await vi.waitFor(() => expect(t.view().notice).not.toBeNull());
    expect(t.view().error).toBe("Rate limited, retry in 20s");
  });

  it("keeps watching an answer that is still being written", async () => {
    const t = setup({ sessions: [session("s-9")] });
    t.api.session.mockResolvedValue({ pending_confirmation: null, latest_run: { id: "run-7", status: "running", error_message: null, user_message_id: null, has_answer: false } });
    await t.controller.load();
    expect(t.streams[0].path).toBe("/assistant/runs/run-7/events");
    expect(t.streams[0].body).toBeNull();
    expect(t.view().streaming).toBe(true);
  });

  it("says so when the chat can't be opened", async () => {
    const t = setup();
    t.api.sessions.mockRejectedValue(new Error("Forbidden"));
    await t.controller.load();
    expect(t.view()).toMatchObject({ loading: false, error: "Forbidden" });
  });
});

describe("sending", () => {
  it("creates the chat on the first message, for this contract", async () => {
    const t = setup();
    await t.controller.send("Tighten the payment terms");
    expect(t.api.createSession).toHaveBeenCalledWith({ session_type: "contract", contract_id: "c-1", title: "Tighten the payment terms" });
    expect(t.streams[0].path).toBe("/assistant/sessions/s-1/stream");
    expect(t.streams[0].body).toEqual({ message: "Tighten the payment terms", contract_ids: ["c-1"] });
    expect(t.view().items.map((i) => [i.role, i.text])).toEqual([["user", "Tighten the payment terms"]]);
    expect(t.view().streaming).toBe(true);
  });

  it("uses the chat it opened and does not create another", async () => {
    const t = setup({ sessions: [session("s-9")] });
    await t.controller.load();
    await t.controller.send("Hello");
    expect(t.api.createSession).not.toHaveBeenCalled();
    expect(t.streams[0].path).toBe("/assistant/sessions/s-9/stream");
  });

  it("shortens a long first message into a title", async () => {
    const t = setup();
    await t.controller.send("x".repeat(100));
    expect(t.api.createSession.mock.calls[0][0].title).toBe(`${"x".repeat(59)}…`);
  });

  it("does not send nothing, or a second message while one is being answered", async () => {
    const t = setup();
    await t.controller.send("   ");
    expect(t.stream).not.toHaveBeenCalled();
    await t.controller.send("First");
    await t.controller.send("Second");
    expect(t.stream).toHaveBeenCalledTimes(1);
  });

  it("gives the text back if the chat can't be created", async () => {
    const t = setup();
    t.api.createSession.mockRejectedValue(new Error("Not allowed"));
    await t.controller.send("Hello");
    expect(t.view()).toMatchObject({ error: "Not allowed", draft: "Hello", streaming: false });
    expect(t.stream).not.toHaveBeenCalled();
  });
});

describe("an answer arriving", () => {
  it("shows the answer as it is written", async () => {
    const t = setup();
    await started(t);
    t.emit(0, "message_delta", { text: "I drafted " });
    t.emit(0, "message_delta", { text: "one change." });
    expect(t.view().items.at(-1)).toMatchObject({ role: "assistant", text: "I drafted one change." });
  });

  it("replaces it with the saved conversation when it ends", async () => {
    const t = setup();
    await started(t);
    t.emit(0, "message_delta", { text: "Done." });
    t.api.messages.mockResolvedValue([
      message("m1", "user", "Tighten the payment terms"),
      message("m2", "assistant", "Done.", { blocks: [{ type: "tool", name: "edit_contract", status: "done" }, { type: "content", text: "Done." }] }),
    ]);
    t.emit(0, "done");
    await vi.waitFor(() => expect(t.view().items.at(-1)?.blocks).toHaveLength(2));
    expect(t.view().streaming).toBe(false);
  });

  it("tells the page something changed, once, when a tool ran", async () => {
    const t = setup();
    await started(t);
    t.emit(0, "tool_started", { tool_name: "edit_contract", tool_use_id: "a" });
    t.emit(0, "tool_finished", { tool_use_id: "a" });
    t.emit(0, "done");
    await vi.waitFor(() => expect(t.api.messages).toHaveBeenCalled());
    expect(t.onSettled).toHaveBeenCalledTimes(1);
  });

  it("leaves the page alone when only an answer was written", async () => {
    const t = setup();
    await started(t);
    t.emit(0, "message_delta", { text: "It is in Review." });
    t.emit(0, "done");
    await vi.waitFor(() => expect(t.api.messages).toHaveBeenCalled());
    expect(t.onSettled).not.toHaveBeenCalled();
  });

  it("shows a server error and stops waiting", async () => {
    const t = setup();
    await started(t);
    t.emit(0, "error", { message: "Rate limited" });
    expect(t.view()).toMatchObject({ error: "Rate limited", streaming: false });
    t.controller.dismissError();
    expect(t.view().error).toBeNull();
  });
});

describe("a connection that drops", () => {
  it("carries on after the last event it saw", async () => {
    const t = setup();
    await started(t);
    t.emit(0, "message_delta", { text: "Part" }, "5");
    t.streams[0].cb.onClose?.();
    expect(t.view().streaming).toBe(true);
    t.runTimers();
    await vi.waitFor(() => expect(t.streams).toHaveLength(2));
    expect(t.streams[1].path).toBe("/assistant/runs/run-1/events?after=5");
  });

  it("gives up after a few tries and stops waiting", async () => {
    const t = setup();
    await started(t);
    for (let i = 0; i < 5; i++) {
      t.streams[i].cb.onClose?.();
      t.runTimers();
      await vi.waitFor(() => expect(t.streams).toHaveLength(i + 2));
    }
    t.streams[5].cb.onClose?.();
    expect(t.view().streaming).toBe(false);
  });

  it("does not treat a network blip mid-answer as an error", async () => {
    const t = setup();
    await started(t);
    t.streams[0].cb.onError?.(new Error("network"));
    expect(t.view()).toMatchObject({ error: null, streaming: true });
  });

  it("does not reconnect once the answer has ended", async () => {
    const t = setup();
    await started(t);
    t.emit(0, "done");
    t.streams[0].cb.onClose?.();
    t.runTimers();
    expect(t.streams).toHaveLength(1);
  });

  it("returns the text when the server would not start the answer", async () => {
    const t = setup();
    await t.controller.send("Tighten clause 5");
    t.streams[0].cb.onError?.(new HttpError(409, "Aegis is still answering your last message in this chat."));
    expect(t.view()).toMatchObject({ error: "Aegis is still answering your last message in this chat.", draft: "Tighten clause 5", streaming: false });
    t.controller.consumeDraft();
    expect(t.view().draft).toBeNull();
  });
});

describe("stopping", () => {
  it("asks the server to stop the answer that is running", async () => {
    const t = setup();
    await started(t);
    await t.controller.stop();
    expect(t.api.cancelRun).toHaveBeenCalledWith("run-1");
    expect(t.view().stopping).toBe(true);
  });

  it("just stops listening when the answer never started", async () => {
    const t = setup();
    await t.controller.send("Hello");
    await t.controller.stop();
    expect(t.api.cancelRun).not.toHaveBeenCalled();
    expect(t.streams[0].abort).toHaveBeenCalled();
    expect(t.view().streaming).toBe(false);
  });

  it("stops waiting at once when the server ended the answer itself because no worker was running it", async () => {
    const t = setup();
    await started(t);
    t.api.cancelRun.mockResolvedValue({ assistant_run_id: "run-1", run_status: "cancelled", cancel_requested: true });
    t.api.messages.mockResolvedValue([message("m-1", "user", "Tighten the payment terms")]);
    await t.controller.stop();
    expect(t.view()).toMatchObject({ streaming: false, stopping: false });
    expect(t.streams[0].abort).toHaveBeenCalled();
  });

  it("keeps waiting for the answer's own end when a worker is still running it", async () => {
    const t = setup();
    await started(t);
    t.api.cancelRun.mockResolvedValue({ assistant_run_id: "run-1", run_status: "running", cancel_requested: true });
    await t.controller.stop();
    expect(t.view()).toMatchObject({ streaming: true, stopping: true });
    expect(t.streams[0].abort).not.toHaveBeenCalled();
  });

  it("says so when it can't be stopped", async () => {
    const t = setup();
    await started(t);
    t.api.cancelRun.mockRejectedValue(new Error("Try again"));
    await t.controller.stop();
    expect(t.view()).toMatchObject({ error: "Try again", stopping: false });
  });
});

describe("trying again", () => {
  it("asks the stored question again without adding a second copy of it to the chat", async () => {
    const t = setup();
    await t.controller.send("What is the notice period?");
    t.emit(0, "done", {});
    await t.controller.send("What is the notice period?", "run-1");
    expect(t.streams[1].body).toEqual({ message: "What is the notice period?", contract_ids: ["c-1"], retry_run_id: "run-1" });
    expect(t.view().items.filter((i) => i.role === "user")).toHaveLength(1);
  });

  it("adds the question again when a different one is sent, even if a retry id is passed", async () => {
    const t = setup();
    await t.controller.send("First question");
    t.emit(0, "done", {});
    await t.controller.send("Second question", "run-1");
    expect(t.view().items.filter((i) => i.role === "user").map((i) => i.text)).toEqual(["First question", "Second question"]);
  });

  it("sends a plain new question without a retry id", async () => {
    const t = setup();
    await t.controller.send("Hello");
    expect(t.streams[0].body).not.toHaveProperty("retry_run_id");
  });
});

describe("an action that needs a yes", () => {
  const pause = async (t: ReturnType<typeof setup>) => {
    await started(t, "Send this for approval");
    t.emit(0, "confirmation_required", {
      confirmation_id: "conf-1", assistant_run_id: "run-1", tool_name: "advance_contract_stage",
      details: { Contract: "Acme MSA", Move: "Review to Approval" },
    });
  };

  it("pauses and shows what will happen", async () => {
    const t = setup();
    await pause(t);
    expect(t.view().pending?.details).toEqual({ Contract: "Acme MSA", Move: "Review to Approval" });
    expect(t.view().streaming).toBe(false);
  });

  it("confirms, then carries on with the same answer", async () => {
    const t = setup();
    await pause(t);
    await t.controller.resolve(true);
    expect(t.api.confirm).toHaveBeenCalledWith("conf-1");
    expect(t.streams[1].path).toBe("/assistant/runs/run-1/resume?confirmation_id=conf-1");
    expect(t.streams[1].body).toEqual({});
    expect(t.view()).toMatchObject({ pending: null, streaming: true, resolving: false });
  });

  it("tells the page what changed once the confirmed action has run", async () => {
    const t = setup();
    await pause(t);
    await t.controller.resolve(true);
    t.emit(1, "tool_started", { tool_name: "advance_contract_stage", tool_use_id: "z" });
    t.emit(1, "tool_finished", { tool_use_id: "z" });
    t.emit(1, "done");
    await vi.waitFor(() => expect(t.onSettled).toHaveBeenCalledTimes(1));
  });

  it("cancels without running anything, and shows what the server saved", async () => {
    const t = setup();
    await pause(t);
    t.api.messages.mockResolvedValue([message("m1", "assistant", "Cancelled — nothing was changed.")]);
    await t.controller.resolve(false);
    expect(t.api.reject).toHaveBeenCalledWith("conf-1", "Rejected by user");
    expect(t.api.confirm).not.toHaveBeenCalled();
    expect(t.stream).toHaveBeenCalledTimes(1);
    expect(t.view().pending).toBeNull();
    expect(t.view().items.at(-1)?.text).toBe("Cancelled — nothing was changed.");
  });

  it("says so when it has expired, and shows what the server saved", async () => {
    const t = setup();
    await pause(t);
    t.api.confirm.mockRejectedValue(new Error("This confirmation has expired"));
    t.api.messages.mockResolvedValue([message("m1", "assistant", "The action expired, so nothing was changed.")]);
    await t.controller.resolve(true);
    expect(t.view().error).toBe("This confirmation has expired");
    expect(t.view().items.at(-1)?.text).toMatch(/expired/);
    expect(t.view()).toMatchObject({ streaming: false, resolving: false });
  });

  it("does nothing with no confirmation waiting, and only once", async () => {
    const t = setup();
    await t.controller.resolve(true);
    expect(t.api.confirm).not.toHaveBeenCalled();
    await pause(t);
    await Promise.all([t.controller.resolve(true), t.controller.resolve(true)]);
    expect(t.api.confirm).toHaveBeenCalledTimes(1);
  });

  it("does not let a new message through while a confirmation is being recorded", async () => {
    const t = setup();
    await pause(t);
    const resolving = t.controller.resolve(true);
    await t.controller.send("Something else");
    await resolving;
    expect(t.stream).toHaveBeenCalledTimes(2); // the first send, then the resume. Not a third.
  });
});

describe("an answer that never finishes arriving", () => {
  it("ends the wait when the server says the answer is over", async () => {
    const t = setup();
    await started(t);
    t.api.session.mockResolvedValue({ pending_confirmation: null, latest_run: { id: "run-1", status: "succeeded", error_message: null, user_message_id: null, has_answer: true } });
    t.api.messages.mockResolvedValue([message("m1", "assistant", "All done.")]);
    t.runTimers();
    await vi.waitFor(() => expect(t.view().streaming).toBe(false));
    expect(t.view().items.at(-1)?.text).toBe("All done.");
    expect(t.streams[0].abort).toHaveBeenCalled();
  });

  it("keeps waiting while the server says it is still running", async () => {
    const t = setup();
    await started(t);
    t.api.session.mockResolvedValue({ pending_confirmation: null, latest_run: { id: "run-1", status: "running", error_message: null, user_message_id: null, has_answer: false } });
    t.runTimers();
    await vi.waitFor(() => expect(t.api.session).toHaveBeenCalled());
    expect(t.view().streaming).toBe(true);
    expect(t.timers.length).toBeGreaterThan(0); // and checks again
  });

  it("shows a confirmation the answer paused on if the stream never said so", async () => {
    const t = setup();
    await started(t);
    t.api.session.mockResolvedValue({
      pending_confirmation: { confirmation_id: "conf-2", assistant_run_id: "run-1", tool_name: "advance_contract_stage", details: {} },
      latest_run: { id: "run-1", status: "waiting_confirmation", error_message: null, user_message_id: null, has_answer: false },
    });
    t.runTimers();
    await vi.waitFor(() => expect(t.view().pending?.confirmationId).toBe("conf-2"));
    expect(t.view().streaming).toBe(false);
  });
});

describe("leaving the page", () => {
  it("stops listening and ignores anything that arrives later", async () => {
    const t = setup();
    await started(t);
    const listener = vi.fn();
    t.controller.subscribe(listener);
    t.controller.dispose();
    t.emit(0, "message_delta", { text: "late" });
    t.streams[0].cb.onClose?.();
    expect(t.streams[0].abort).toHaveBeenCalled();
    expect(t.view().items.map((i) => i.text)).toEqual(["Tighten the payment terms"]);
    expect(listener).not.toHaveBeenCalled();
  });

  it("tells whoever is listening about each change, until they stop", async () => {
    const t = setup();
    const listener = vi.fn();
    const stop = t.controller.subscribe(listener);
    await t.controller.load();
    expect(listener).toHaveBeenCalled();
    listener.mockClear();
    stop();
    await t.controller.send("Hello");
    expect(listener).not.toHaveBeenCalled();
  });
});
