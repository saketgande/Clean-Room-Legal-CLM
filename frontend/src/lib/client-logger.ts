// Ships browser errors to the backend (POST /client-logs) so they show up in
// the server's structured logs on the VM (`docker compose logs api`). Best
// effort, never throws, de-duped and capped so a buggy render loop can't flood.
import { API_BASE } from "@/lib/api";

let sent = 0;
const MAX_PER_SESSION = 25;
const seen = new Set<string>();

export function reportClientError(input: {
  message: string;
  stack?: string;
  url?: string;
  kind?: string;
  level?: "error" | "warn";
}): void {
  try {
    if (sent >= MAX_PER_SESSION) return;
    const message = String(input.message ?? "").slice(0, 2000);
    if (!message) return;
    const dedupeKey = `${input.kind ?? ""}:${message.slice(0, 200)}`;
    if (seen.has(dedupeKey)) return;
    seen.add(dedupeKey);
    sent += 1;

    const body = JSON.stringify({
      level: input.level ?? "error",
      message,
      stack: input.stack ? String(input.stack).slice(0, 8000) : undefined,
      url:
        input.url ??
        (typeof window !== "undefined" ? window.location.href : undefined),
      kind: input.kind ?? "error",
    });

    // keepalive lets the report survive a navigation/unload.
    void fetch(`${API_BASE}/client-logs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
    }).catch(() => {
      /* swallow — logging must never break the app */
    });
  } catch {
    /* never throw from the reporter */
  }
}
