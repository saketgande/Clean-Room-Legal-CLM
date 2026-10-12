// What to tell a person when a page's data could not be loaded.
//
// A failed request used to leave a page looking like a healthy empty one ("0 open requests, queue is
// healthy") or stuck on "Loading…" for ever. A failure is its own state: say what could not be loaded
// and why, in words, and let the person try again.

import { HttpError, NO_REPLY_STATUS } from "./api";

export interface LoadErrorText {
  title: string;
  /** What the server said, when that helps; null when the title already says it. */
  detail: string | null;
  /** Trying again can help (the server was busy or unreachable); false when the answer is final. */
  canRetry: boolean;
}

/** `what` reads in a sentence: "the request queue", "this request". */
export function loadErrorText(error: unknown, what: string): LoadErrorText {
  const status = error instanceof HttpError ? error.status : null;
  const message = error instanceof Error && error.message ? error.message : null;
  if (status === 403)
    return { title: `You don't have access to ${what}.`, detail: message, canRetry: false };
  if (status === 404)
    return { title: `Couldn't find ${what}. It may have been removed, or the link is not right.`, detail: null, canRetry: false };
  if (status === NO_REPLY_STATUS)
    return { title: `Couldn't load ${what}: the server isn't answering.`, detail: null, canRetry: true };
  return { title: `Couldn't load ${what}.`, detail: message, canRetry: true };
}
