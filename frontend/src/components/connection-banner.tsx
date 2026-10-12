"use client";

import { useSyncExternalStore } from "react";
import { connectionMonitor } from "@/lib/api";

/**
 * Says so when the server has stopped answering, so a page full of spinners or empty boxes has an
 * explanation. It goes away by itself as soon as the server answers again. Mounted once, at the root, so it also
 * shows on the "Loading workspace…" screen and the login page, which the app frame does not cover.
 */
export function ConnectionBanner() {
  const down = useSyncExternalStore(connectionMonitor.subscribe, connectionMonitor.getSnapshot, connectionMonitor.getServerSnapshot);
  if (!down) return null;
  return (
    <div role="alert" className="fixed inset-x-0 top-0 z-[80] border-b border-amber-200 bg-amber-50 px-4 py-2 text-center text-sm text-amber-900 shadow-sm">
      The server isn&apos;t answering right now. What you see may be out of date and changes may not save. Trying again automatically…
    </div>
  );
}
