// Tells the person when the server has stopped answering.
//
// Before this, a frozen or restarting backend showed only spinners and empty grey boxes, sometimes for
// minutes, with nothing saying why. Two things can tell us the server is not answering:
//   1. a request fails outright with a network error (the server is down), or
//   2. a request is still waiting after a while AND a tiny "are you there?" request also gets no answer.
// Either one switches the banner on. It switches off as soon as any request gets a reply from the server
// (any reply, even an error one: an error reply still proves the server is there).
//
// A slow request alone does NOT show the banner: a long answer from the server is normal. Only the
// "are you there?" check failing does.

export interface ConnectionDeps {
  /** Resolves true if the server answered anything at all within a short time. */
  probe: () => Promise<boolean>;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
  /** How long a request may wait before we ask the server whether it is there. */
  slowAfterMs?: number;
  /** While the banner is up, how often to ask again. */
  recheckEveryMs?: number;
}

export const SLOW_AFTER_MS = 10_000;
export const RECHECK_EVERY_MS = 5_000;
export const PROBE_TIMEOUT_MS = 5_000;
export const SERVER_DOWN_MESSAGE =
  "Can't reach the server. Check your connection and try again in a moment.";

export class ConnectionMonitor {
  private down = false;
  private readonly listeners = new Set<() => void>();
  private readonly waiting = new Map<number, unknown>();
  private nextId = 1;
  private recheck: unknown = null;
  private readonly setT: (fn: () => void, ms: number) => unknown;
  private readonly clearT: (handle: unknown) => void;
  private readonly slowAfter: number;
  private readonly recheckEvery: number;

  constructor(private readonly deps: ConnectionDeps) {
    this.setT = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    this.clearT = deps.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
    this.slowAfter = deps.slowAfterMs ?? SLOW_AFTER_MS;
    this.recheckEvery = deps.recheckEveryMs ?? RECHECK_EVERY_MS;
  }

  // React's useSyncExternalStore wants exactly these three.
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  getSnapshot = () => this.down;
  getServerSnapshot = () => false;

  /** A request has started. Call the returned function when it ends, whichever way it ends. */
  requestStarted(): () => void {
    const id = this.nextId++;
    this.waiting.set(id, this.setT(() => void this.askIfStillWaiting(id), this.slowAfter));
    return () => {
      const handle = this.waiting.get(id);
      if (handle !== undefined) this.clearT(handle);
      this.waiting.delete(id);
    };
  }

  /** The server answered something (even an error reply). */
  serverAnswered() {
    this.setDown(false);
  }

  /** A request failed because nothing answered. */
  serverNotAnswering() {
    this.setDown(true);
  }

  private async askIfStillWaiting(id: number) {
    if (!this.waiting.has(id) || this.down) return;
    if (!(await this.safeProbe())) {
      // The request may have finished while we were asking; then the server clearly is there.
      if (this.waiting.has(id)) this.setDown(true);
    }
  }

  private async safeProbe(): Promise<boolean> {
    try {
      return await this.deps.probe();
    } catch {
      return false;
    }
  }

  private setDown(value: boolean) {
    if (value === this.down) return;
    this.down = value;
    if (value) this.scheduleRecheck();
    else this.cancelRecheck();
    this.listeners.forEach((l) => l());
  }

  private scheduleRecheck() {
    this.cancelRecheck();
    this.recheck = this.setT(async () => {
      this.recheck = null;
      if (!this.down) return;
      if (await this.safeProbe()) this.setDown(false);
      else if (this.down) this.scheduleRecheck();
    }, this.recheckEvery);
  }

  private cancelRecheck() {
    if (this.recheck !== null) this.clearT(this.recheck);
    this.recheck = null;
  }
}

/** True for the error `fetch` throws when nothing answered (server down, no network), not for a caller's abort. */
export function isNetworkFailure(err: unknown): boolean {
  if (!(err instanceof Error)) return false;
  if (err.name === "AbortError") return false;
  return err instanceof TypeError;
}
