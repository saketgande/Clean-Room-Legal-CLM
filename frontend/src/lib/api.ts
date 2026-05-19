// Core HTTP + SSE client for the Legal CLM backend.
import type { ApiError, TokenResponse } from "./types";
import { demoStream, getMock, isDemo } from "./demo";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

const ACCESS_KEY = "aegis.access_token";

// Refresh tokens are NOT stored client-side any more — they live in an
// HttpOnly cookie set by the backend. The frontend never reads or writes the
// refresh token directly; we just send `credentials: 'include'` on /auth/refresh
// so the cookie is attached automatically. This removes the previous risk where
// any XSS could exfiltrate a 30-day persistent session from localStorage.
//
// Access tokens still live in localStorage because they're sent on every
// request as `Authorization: Bearer ...`. They're short-lived (default 60min)
// and revocable server-side via the access-token jti list, which limits blast
// radius if an XSS does land.
export const tokenStore = {
  get access() {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(ACCESS_KEY);
  },
  set(tokens: { access_token: string }) {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(ACCESS_KEY, tokens.access_token);
  },
  clear() {
    if (typeof window === "undefined") return;
    window.localStorage.removeItem(ACCESS_KEY);
  },
};

export class HttpError extends Error implements ApiError {
  status: number;
  detail?: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

function authHeaders(extra?: HeadersInit): HeadersInit {
  const headers: Record<string, string> = {};
  const token = tokenStore.access;
  if (token) headers.Authorization = `Bearer ${token}`;
  return { ...headers, ...(extra as Record<string, string>) };
}

async function parseError(res: Response): Promise<HttpError> {
  let detail: unknown;
  let message = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    detail = body;
    if (typeof body?.detail === "string") message = body.detail;
    else if (Array.isArray(body?.detail) && body.detail[0]?.msg)
      message = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    else if (typeof body?.message === "string") message = body.message;
  } catch {
    /* non-JSON error body */
  }
  return new HttpError(res.status, message, detail);
}

let refreshing: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  // The refresh token is in an HttpOnly cookie set by /auth/login. We just
  // need to include credentials on this request — the backend reads the
  // cookie, mints a new access token, and rotates the cookie.
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await fetch(`${API_BASE}/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({}),
        });
        if (!res.ok) return false;
        const data = (await res.json()) as TokenResponse;
        tokenStore.set(data);
        return true;
      } catch {
        return false;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  /** Send body as raw FormData (file uploads) instead of JSON. */
  form?: FormData;
  /** Skip auth refresh-retry (used by auth endpoints). */
  noRetry?: boolean;
}

export async function apiFetch<T>(
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const { body, form, noRetry, headers, ...rest } = opts;

  if (isDemo()) {
    await sleep(180);
    return getMock(path, (rest.method as string) ?? "GET", body) as T;
  }

  const doFetch = () => {
    const init: RequestInit = {
      ...rest,
      headers: authHeaders(headers),
      // Include the refresh-token cookie on every request — the backend
      // only reads it on /auth/refresh and /auth/logout, but sending it
      // uniformly avoids special-casing those paths.
      credentials: "include",
    };
    if (form) {
      init.body = form;
    } else if (body !== undefined) {
      init.body = JSON.stringify(body);
      (init.headers as Record<string, string>)["Content-Type"] =
        "application/json";
    }
    return fetch(`${API_BASE}${path}`, init);
  };

  let res = await doFetch();
  if (res.status === 401 && !noRetry) {
    const ok = await tryRefresh();
    if (ok) res = await doFetch();
    else {
      tokenStore.clear();
      if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login"))
        window.location.href = "/login";
    }
  }

  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

/** Trigger a browser download for binary endpoints (file download, XLSX export). */
export async function apiDownload(path: string, fallbackName = "download") {
  if (isDemo()) {
    if (typeof window !== "undefined")
      window.alert(
        "Demo mode: file downloads are disabled. Connect the backend to download real files.",
      );
    return;
  }
  const res = await fetch(`${API_BASE}${path}`, { headers: authHeaders() });
  if (!res.ok) throw await parseError(res);
  const blob = await res.blob();
  const disposition = res.headers.get("content-disposition") ?? "";
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const name = match?.[1] ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export interface StreamCallbacks {
  onEvent: (event: string, data: Record<string, unknown>) => void;
  onError?: (err: Error) => void;
  onClose?: () => void;
}

/**
 * POST a body and consume a text/event-stream response. Returns an abort fn.
 * Used by the assistant streaming + resume endpoints.
 */
export async function apiStream(
  path: string,
  body: unknown,
  cb: StreamCallbacks,
): Promise<() => void> {
  if (isDemo()) {
    return demoStream(cb.onEvent, cb.onClose);
  }

  const controller = new AbortController();
  (async () => {
    try {
      let res = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body ?? {}),
        signal: controller.signal,
      });
      if (res.status === 401) {
        const ok = await tryRefresh();
        if (ok)
          res = await fetch(`${API_BASE}${path}`, {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(body ?? {}),
            signal: controller.signal,
          });
      }
      if (!res.ok || !res.body) {
        cb.onError?.(await parseError(res));
        cb.onClose?.();
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() ?? "";
        for (const chunk of chunks) {
          let eventName = "message";
          const dataLines: string[] = [];
          for (const line of chunk.split("\n")) {
            if (line.startsWith("event:")) eventName = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          if (!dataLines.length) continue;
          const raw = dataLines.join("\n");
          let parsed: Record<string, unknown> = {};
          try {
            parsed = JSON.parse(raw);
          } catch {
            parsed = { raw };
          }
          cb.onEvent(eventName, parsed);
        }
      }
      cb.onClose?.();
    } catch (err) {
      if ((err as Error).name !== "AbortError")
        cb.onError?.(err as Error);
      cb.onClose?.();
    }
  })();
  return () => controller.abort();
}
