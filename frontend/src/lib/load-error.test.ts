import { describe, expect, it } from "vitest";
import { HttpError, NO_REPLY_STATUS } from "./api";
import { loadErrorText } from "./load-error";

describe("loadErrorText", () => {
  it("says there is no access, with the server's reason, and does not offer to try again", () => {
    const t = loadErrorText(new HttpError(403, "Missing permission: intake:read"), "the request queue");
    expect(t).toEqual({ title: "You don't have access to the request queue.", detail: "Missing permission: intake:read", canRetry: false });
  });

  it("says a missing thing was not found", () => {
    const t = loadErrorText(new HttpError(404, "Not found"), "this request");
    expect(t.title).toMatch(/Couldn't find this request/);
    expect(t.canRetry).toBe(false);
  });

  it("says the server isn't answering when nothing came back", () => {
    const t = loadErrorText(new HttpError(NO_REPLY_STATUS, "Can't reach the server"), "the request queue");
    expect(t.title).toMatch(/server isn't answering/);
    expect(t.canRetry).toBe(true);
  });

  it("gives the message for anything else and allows a retry", () => {
    expect(loadErrorText(new HttpError(500, "Something broke"), "the queue")).toEqual({ title: "Couldn't load the queue.", detail: "Something broke", canRetry: true });
    expect(loadErrorText("odd", "the queue")).toEqual({ title: "Couldn't load the queue.", detail: null, canRetry: true });
  });
});
