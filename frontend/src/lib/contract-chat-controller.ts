// The contract chat's conversation with the server, as a plain class so it can be
// tested without React: find or create this contract's chat, send a message, watch
// the answer arrive, reconnect if the connection drops, stop it, and approve or
// cancel the action it asks about.
//
// How answers work (backend assistant/routes.py): sending a message starts the answer
// in a background worker and the response only WATCHES it. Leaving the page does not
// stop it, and a dropped connection can pick up after the last event seen. An action
// that changes something (a stage move) pauses the answer until the person says yes.

import { HttpError, type StreamCallbacks } from "./api";
import { runNoticeFor, type RunNotice } from "./assistant-run";
import { applyEvent, clean, initialChatState, itemsFromMessages, type ChatItem, type ChatState, type Confirmation } from "./contract-chat";
import type { AssistantMessage, AssistantRunSummary, AssistantSession } from "./types";

export interface ChatApi {
  sessions(params: Record<string, unknown>): Promise<AssistantSession[]>;
  createSession(payload: { session_type?: string; title?: string; contract_id?: string }): Promise<AssistantSession>;
  messages(sessionId: string): Promise<AssistantMessage[]>;
  session(sessionId: string): Promise<{
    pending_confirmation: { confirmation_id: string; assistant_run_id: string; tool_name: string; details: Record<string, string> } | null;
    latest_run: AssistantRunSummary | null;
  }>;
  cancelRun(runId: string): Promise<{ run_status?: string } | unknown>;
  confirm(confirmationId: string): Promise<unknown>;
  reject(confirmationId: string, reason?: string): Promise<unknown>;
}

export type StreamFn = (path: string, body: unknown, cb: StreamCallbacks) => Promise<() => void>;

export interface ChatDeps {
  contractId: string;
  api: ChatApi;
  stream: StreamFn;
  /** Called when an answer changed something on this contract (suggestions, stage). */
  onSettled: () => void;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

export interface ChatView {
  loading: boolean;
  items: ChatItem[];
  pending: Confirmation | null;
  streaming: boolean;
  stopping: boolean;
  resolving: boolean;
  error: string | null;
  notice: RunNotice | null;
  /** Text to put back in the box: a message that could not be sent. */
  draft: string | null;
}

export const INITIAL_VIEW: ChatView = {
  loading: true, items: [], pending: null, streaming: false, stopping: false, resolving: false, error: null, notice: null, draft: null,
};

const MAX_RECONNECTS = 5;
const WATCHDOG_EVERY_MS = 4000;
const WATCHDOG_FOR_MS = 300_000;

export class ContractChatController {
  private chat: ChatState = initialChatState;
  private sessionId: string | null = null;
  private lastEventId: string | undefined;
  private reconnects = 0;
  private lastSent: string | null = null;
  private ended = false;
  private abortStream: (() => void) | null = null;
  private dogTimer: unknown = null;
  // Anything started before dispose() (or for another contract) must not touch the view.
  private alive = true;
  private listeners = new Set<() => void>();
  private view: ChatView = INITIAL_VIEW;

  constructor(private deps: ChatDeps) {}

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  getSnapshot = () => this.view;

  dispose() {
    this.alive = false;
    this.stopWatchdog();
    this.abortStream?.();
    this.abortStream = null;
    this.listeners.clear();
  }

  // ---- state -----------------------------------------------------------------

  private commit(patch: Partial<ChatView> = {}) {
    if (!this.alive) return;
    this.view = { ...this.view, items: this.chat.items, pending: this.chat.pending, error: this.chat.error, ...patch };
    for (const l of this.listeners) l();
  }

  private setChat(next: ChatState, patch: Partial<ChatView> = {}) {
    this.chat = next;
    this.commit(patch);
  }

  private timer(fn: () => void, ms: number): unknown {
    return (this.deps.setTimer ?? ((f, m) => setTimeout(f, m)))(fn, ms);
  }
  private untimer(handle: unknown) {
    (this.deps.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>)))(handle);
  }

  // ---- opening the chat ---------------------------------------------------------

  async load() {
    try {
      const sessions = await this.deps.api.sessions({ contract_id: this.deps.contractId });
      if (!this.alive) return;
      const mine = sessions.find((s) => s.session_type === "contract" && s.contract_id === this.deps.contractId);
      if (!mine) return this.commit({ loading: false });
      this.sessionId = mine.id;
      const messages = await this.deps.api.messages(mine.id);
      if (!this.alive) return;
      this.setChat({ ...this.chat, items: itemsFromMessages(messages) }, { loading: false });
      await this.restore(mine.id, messages, true);
    } catch (e) {
      this.setChat({ ...this.chat, error: e instanceof Error && e.message ? e.message : "Couldn't open the chat." }, { loading: false });
    }
  }

  /** After the chat opens (or an answer ends): show the confirmation it is waiting on,
   *  keep watching an answer still being produced, or explain one that didn't finish. */
  private async restore(sessionId: string, messages: Pick<AssistantMessage, "id" | "content">[], follow: boolean) {
    try {
      const res = await this.deps.api.session(sessionId);
      if (!this.alive || this.sessionId !== sessionId) return;
      const pc = res.pending_confirmation;
      this.setChat({
        ...this.chat,
        pending: pc
          ? { confirmationId: pc.confirmation_id, assistantRunId: pc.assistant_run_id, toolName: pc.tool_name, details: pc.details ?? {} }
          : null,
      });
      const run = res.latest_run;
      if (run?.status === "running" && follow) {
        this.beginTurn(null);
        this.chat = { ...this.chat, runId: run.id };
        this.commit({ streaming: true });
        this.startWatchdog(sessionId);
        await this.watch(run.id);
        return;
      }
      const notice = runNoticeFor(run, messages);
      // The stream's own error and the saved answer's explanation are usually the same sentence: say it once
      // (the explanation carries "Try again"), instead of a grey box and a red box repeating each other.
      if (notice && this.chat.error === notice.message) this.chat = { ...this.chat, error: null };
      this.commit({ notice });
    } catch {
      /* the conversation itself already opened; these are conveniences */
    }
  }

  private async syncFromServer(): Promise<AssistantMessage[] | null> {
    const id = this.sessionId;
    if (!id) return null;
    try {
      const messages = await this.deps.api.messages(id);
      if (!this.alive || this.sessionId !== id) return messages;
      if (messages.length) this.setChat({ ...this.chat, items: itemsFromMessages(messages) });
      return messages;
    } catch {
      return null;
    }
  }

  // ---- sending ---------------------------------------------------------------------

  private beginTurn(text: string | null) {
    this.lastEventId = undefined;
    this.reconnects = 0;
    this.lastSent = text;
    this.ended = false;
    this.chat = clean(this.chat);
  }

  private async ensureSession(firstMessage: string): Promise<string | null> {
    if (this.sessionId) return this.sessionId;
    try {
      const title = firstMessage.length > 60 ? `${firstMessage.slice(0, 59)}…` : firstMessage;
      const created = await this.deps.api.createSession({ session_type: "contract", contract_id: this.deps.contractId, title });
      if (!this.alive) return null;
      this.sessionId = created.id;
      return created.id;
    } catch (e) {
      this.setChat({ ...this.chat, error: e instanceof Error && e.message ? e.message : "Couldn't start the chat." }, { draft: firstMessage });
      return null;
    }
  }

  /** `retryRunId`: this send is "Try again" on that answer, which wrote nothing, so the question is
   *  already in the chat (and stored on the server); it is asked again, not added a second time. */
  async send(raw: string, retryRunId?: string | null) {
    const text = raw.trim();
    if (!text || this.view.streaming || this.view.resolving) return;
    const sessionId = await this.ensureSession(text);
    if (!sessionId || !this.alive) return;
    this.beginTurn(text);
    const lastUser = [...this.chat.items].reverse().find((i) => i.role === "user");
    const alreadyShown = !!retryRunId && lastUser?.text === text;
    this.setChat(
      {
        ...this.chat,
        items: alreadyShown ? this.chat.items : [...this.chat.items, { id: crypto.randomUUID(), role: "user", text }],
        pending: null,
      },
      { streaming: true, notice: null, draft: null },
    );
    this.startWatchdog(sessionId);
    await this.follow(`/assistant/sessions/${sessionId}/stream`, {
      message: text,
      contract_ids: [this.deps.contractId],
      ...(retryRunId ? { retry_run_id: retryRunId } : {}),
    });
  }

  // ---- watching an answer -----------------------------------------------------------

  private watch(runId: string, after?: string) {
    return this.follow(`/assistant/runs/${runId}/events${after ? `?after=${encodeURIComponent(after)}` : ""}`, null);
  }

  private async follow(path: string, body: unknown) {
    this.abortStream?.();
    this.ended = false;
    let aborted = false;
    const abort = await this.deps.stream(path, body, {
      onEvent: (event, data, id) => {
        if (aborted || !this.alive) return;
        if (id) this.lastEventId = id;
        this.handle(event, data);
      },
      onError: (err) => {
        if (!aborted && this.alive) this.streamError(err);
      },
      onClose: () => {
        if (aborted || !this.alive) return;
        const runId = this.chat.runId;
        // The connection dropped but the answer keeps going on the server: carry on after the last event.
        if (!this.ended && runId && this.reconnects < MAX_RECONNECTS) {
          this.reconnects += 1;
          const wait = 1000 * this.reconnects;
          this.timer(() => {
            if (this.alive && !this.ended) void this.watch(runId, this.lastEventId);
          }, wait);
          return;
        }
        this.stopWatchdog();
        this.commit({ streaming: false, stopping: false });
      },
    });
    this.abortStream = () => {
      aborted = true;
      abort();
    };
  }

  private handle(event: string, data: Record<string, unknown>) {
    const next = applyEvent(this.chat, event, data);
    if (event === "confirmation_required") {
      this.stopWatchdog();
      this.setChat(next, { streaming: false });
    } else if (event === "error") {
      this.stopWatchdog();
      this.setChat(next, { streaming: false });
    } else if (event === "done") {
      this.ended = true;
      this.reconnects = 0;
      this.stopWatchdog();
      this.setChat(next, { streaming: false, stopping: false });
      if (next.toolRan) this.deps.onSettled();
      void this.syncFromServer().then((messages) => {
        // The saved conversation has the finished answer and its steps; then show a
        // confirmation it is waiting on, or explain an answer that did not finish.
        if (messages && this.sessionId) void this.restore(this.sessionId, messages, false);
      });
    } else {
      this.setChat(next);
    }
  }

  private streamError(err: Error) {
    // A network drop mid-answer: the answer carries on and onClose reconnects.
    // Only an error the server sent ends the turn here.
    if (!(err instanceof HttpError) && this.chat.runId && !this.ended) return;
    this.ended = true;
    this.stopWatchdog();
    const notStarted = !this.chat.runId && this.lastSent;
    this.setChat(
      { ...this.chat, error: err.message || "Aegis couldn't answer right now." },
      { streaming: false, stopping: false, ...(notStarted ? { draft: this.lastSent } : {}) },
    );
    // Not started (still answering the last message, or rate limited): show what the server really has.
    if (notStarted) void this.syncFromServer();
  }

  // Some networks buffer a stream and it never ends. Ask the server whether the
  // answer is still running, so "Thinking…" always clears and the answer always shows.
  private startWatchdog(sessionId: string) {
    this.stopWatchdog();
    const startedAt = Date.now();
    const tick = () => {
      if (!this.alive) return;
      if (Date.now() - startedAt > WATCHDOG_FOR_MS) {
        this.commit({ streaming: false });
        return;
      }
      this.dogTimer = this.timer(() => void check(), WATCHDOG_EVERY_MS);
    };
    const check = async () => {
      try {
        const res = await this.deps.api.session(sessionId);
        if (!this.alive || this.sessionId !== sessionId || !this.view.streaming) return;
        const status = res.latest_run?.status;
        if (status === "running") return tick();
        if (!this.ended && (status === "waiting_confirmation" || status === "succeeded" || status === "failed" || status === "cancelled" || status === "interrupted")) {
          await this.settleFromServer(sessionId);
          return;
        }
        tick();
      } catch {
        tick();
      }
    };
    tick();
  }

  /** The server says this answer is over, so stop waiting for its stream: show what it saved. */
  private async settleFromServer(sessionId: string) {
    this.ended = true;
    this.stopWatchdog();
    this.abortStream?.();
    this.abortStream = null;
    this.commit({ streaming: false, stopping: false });
    const messages = await this.syncFromServer();
    if (messages) await this.restore(sessionId, messages, false);
    if (this.chat.toolRan) this.deps.onSettled();
  }

  private stopWatchdog() {
    if (this.dogTimer != null) this.untimer(this.dogTimer);
    this.dogTimer = null;
  }

  // ---- stop, confirm, cancel ---------------------------------------------------------------

  async stop() {
    const runId = this.chat.runId;
    if (!runId || this.ended) {
      this.abortStream?.();
      this.commit({ streaming: false });
      return;
    }
    this.commit({ stopping: true });
    try {
      // The worker stops at its next check, saves what it has and sends "done".
      const res = await this.deps.api.cancelRun(runId);
      // When no worker was running the answer (it died, or never started) the server ends it right here and
      // no "done" will come from a worker, so do not wait for one.
      const status = (res as { run_status?: string } | null | undefined)?.run_status;
      if (status && status !== "running" && this.sessionId && !this.ended) await this.settleFromServer(this.sessionId);
    } catch (e) {
      this.setChat({ ...this.chat, error: e instanceof Error && e.message ? e.message : "Couldn't stop the answer." }, { stopping: false });
    }
  }

  async resolve(approve: boolean) {
    const p = this.chat.pending;
    if (!p || this.view.resolving) return;
    this.setChat({ ...this.chat, pending: null, error: null }, { resolving: true });
    try {
      if (approve) {
        await this.deps.api.confirm(p.confirmationId);
        this.beginTurn(null);
        this.chat = { ...this.chat, runId: p.assistantRunId };
        this.commit({ streaming: true, resolving: false });
        if (this.sessionId) this.startWatchdog(this.sessionId);
        await this.follow(`/assistant/runs/${p.assistantRunId}/resume?confirmation_id=${p.confirmationId}`, {});
      } else {
        await this.deps.api.reject(p.confirmationId, "Rejected by user");
        // The server closes the turn with "Cancelled — nothing was changed": show what it saved.
        await this.syncFromServer();
        this.commit({ resolving: false });
      }
    } catch (e) {
      this.setChat({ ...this.chat, error: e instanceof Error && e.message ? e.message : "Couldn't record your answer." }, { streaming: false, resolving: false });
      // For example it expired: the server has closed the turn with an explanation.
      await this.syncFromServer();
    }
  }

  dismissError() {
    this.setChat({ ...this.chat, error: null });
  }

  consumeDraft() {
    this.commit({ draft: null });
  }
}
