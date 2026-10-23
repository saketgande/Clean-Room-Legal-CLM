import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, connectionMonitor, HttpError, NO_REPLY_STATUS } from "./api";
import { SERVER_DOWN_MESSAGE } from "./connection";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  connectionMonitor.serverAnswered();
});
afterEach(() => {
  vi.unstubAllGlobals();
  connectionMonitor.serverAnswered();
});

describe("apiFetch when the server does not answer", () => {
  it("says so in plain words instead of leaking the browser's 'Failed to fetch'", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const err = await apiFetch("/contracts").catch((e) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect(err).toMatchObject({ status: NO_REPLY_STATUS, message: SERVER_DOWN_MESSAGE });
  });

  it("switches the connection banner on, and off again when the server answers", async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValueOnce(json(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    await apiFetch("/contracts").catch(() => undefined);
    expect(connectionMonitor.getSnapshot()).toBe(true);
    await expect(apiFetch("/contracts")).resolves.toEqual({ ok: true });
    expect(connectionMonitor.getSnapshot()).toBe(false);
  });

  it("counts an error reply as the server being there", async () => {
    connectionMonitor.serverNotAnswering();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(500, { detail: "Broken" })));
    await expect(apiFetch("/contracts")).rejects.toMatchObject({ status: 500, message: "Broken" });
    expect(connectionMonitor.getSnapshot()).toBe(false);
  });

  it("passes a caller's own abort through untouched and does not blame the server", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("aborted", "AbortError")));
    const err = await apiFetch("/contracts").catch((e) => e);
    expect(err).toMatchObject({ name: "AbortError" });
    expect(connectionMonitor.getSnapshot()).toBe(false);
  });
});

describe("apiFetch refusals", () => {
  it("shows a permission-code refusal in plain words and keeps the server's body for debugging", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(403, { detail: "Missing permission: intake:read" })));
    const err = await apiFetch("/intake/requests").catch((e) => e);
    expect(err).toMatchObject({ status: 403, message: "Your role doesn't allow this. Ask an administrator if you need access.", detail: { detail: "Missing permission: intake:read" } });
  });

  it("passes any other refusal through unchanged", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(403, { detail: "Only this step's assignee or its team can act on it" })));
    await expect(apiFetch("/workflows/runs/x/complete-step")).rejects.toMatchObject({ status: 403, message: "Only this step's assignee or its team can act on it" });
  });
});
