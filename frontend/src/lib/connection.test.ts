import { describe, expect, it, vi } from "vitest";
import { ConnectionMonitor, isNetworkFailure } from "./connection";

function setup(probeResult: boolean | (() => boolean) = true) {
  const timers: { fn: () => void; ms: number; id: number }[] = [];
  let next = 1;
  const probe = vi.fn(async () => (typeof probeResult === "function" ? probeResult() : probeResult));
  const monitor = new ConnectionMonitor({
    probe,
    slowAfterMs: 10_000,
    recheckEveryMs: 5_000,
    setTimer: (fn, ms) => {
      const id = next++;
      timers.push({ fn, ms, id });
      return id;
    },
    clearTimer: (h) => {
      const i = timers.findIndex((t) => t.id === h);
      if (i >= 0) timers.splice(i, 1);
    },
  });
  const fire = async (ms: number) => {
    const due = timers.filter((t) => t.ms === ms);
    for (const t of due) {
      timers.splice(timers.indexOf(t), 1);
      t.fn();
    }
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  };
  return { monitor, probe, timers, fire };
}

describe("ConnectionMonitor", () => {
  it("is quiet while everything answers", () => {
    const t = setup();
    const done = t.monitor.requestStarted();
    t.monitor.serverAnswered();
    done();
    expect(t.monitor.getSnapshot()).toBe(false);
    expect(t.timers).toHaveLength(0);
  });

  it("goes down when a request gets no answer at all, and comes back when anything answers", () => {
    const t = setup();
    const seen = vi.fn();
    t.monitor.subscribe(seen);
    t.monitor.serverNotAnswering();
    expect(t.monitor.getSnapshot()).toBe(true);
    t.monitor.serverAnswered();
    expect(t.monitor.getSnapshot()).toBe(false);
    expect(seen).toHaveBeenCalledTimes(2);
  });

  it("does not tell listeners twice about the same state", () => {
    const t = setup();
    const seen = vi.fn();
    t.monitor.subscribe(seen);
    t.monitor.serverNotAnswering();
    t.monitor.serverNotAnswering();
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it("does not blame the server for a slow request when the server still answers the check", async () => {
    const t = setup(true);
    t.monitor.requestStarted();
    await t.fire(10_000);
    expect(t.probe).toHaveBeenCalledTimes(1);
    expect(t.monitor.getSnapshot()).toBe(false);
  });

  it("goes down when a request is slow and the server does not answer the check either", async () => {
    const t = setup(false);
    t.monitor.requestStarted();
    await t.fire(10_000);
    expect(t.monitor.getSnapshot()).toBe(true);
  });

  it("does not check at all for a request that finished in time", async () => {
    const t = setup(false);
    const done = t.monitor.requestStarted();
    done();
    await t.fire(10_000);
    expect(t.probe).not.toHaveBeenCalled();
    expect(t.monitor.getSnapshot()).toBe(false);
  });

  it("keeps asking while down and clears itself once the server is back", async () => {
    let up = false;
    const t = setup(() => up);
    t.monitor.serverNotAnswering();
    await t.fire(5_000);
    expect(t.monitor.getSnapshot()).toBe(true);
    expect(t.timers.some((x) => x.ms === 5_000)).toBe(true);
    up = true;
    await t.fire(5_000);
    expect(t.monitor.getSnapshot()).toBe(false);
    expect(t.timers).toHaveLength(0);
  });

  it("treats a check that throws as no answer", async () => {
    const t = setup(() => {
      throw new Error("boom");
    });
    t.monitor.requestStarted();
    await t.fire(10_000);
    expect(t.monitor.getSnapshot()).toBe(true);
  });
});

describe("isNetworkFailure", () => {
  it("recognises what fetch throws when nothing answered, but not a caller's abort or other errors", () => {
    expect(isNetworkFailure(new TypeError("Failed to fetch"))).toBe(true);
    const abort = new DOMException("aborted", "AbortError");
    expect(isNetworkFailure(abort)).toBe(false);
    expect(isNetworkFailure(new Error("other"))).toBe(false);
    expect(isNetworkFailure("text")).toBe(false);
  });
});
