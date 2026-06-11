"use client";

import { useEffect } from "react";
import { reportClientError } from "@/lib/client-logger";

/**
 * Installs global browser-error listeners that forward uncaught errors and
 * unhandled promise rejections to the backend (so they're visible in the VM
 * logs). Mounted once, near the root of the app. Renders nothing.
 */
export function ClientErrorReporter() {
  useEffect(() => {
    const onError = (e: ErrorEvent) => {
      reportClientError({
        kind: "window.error",
        message: e.message || "Uncaught error",
        stack: e.error?.stack,
        url: e.filename || undefined,
      });
    };
    const onRejection = (e: PromiseRejectionEvent) => {
      const reason = e.reason as { message?: string; stack?: string } | undefined;
      reportClientError({
        kind: "unhandledrejection",
        message: reason?.message ?? String(e.reason ?? "Unhandled rejection"),
        stack: reason?.stack,
      });
    };
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);

  return null;
}
