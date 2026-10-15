"use client";

// Stops the Templates page losing unsaved work by accident (spec 006 FR-61).
//
//   const { ask, dialog } = useLeaveGuard(dirty ? "You have unsaved changes to this template." : null);
//   ...render {dialog} once...
//   if (!(await ask())) return;     // before switching template inside the page
//
// While `message` is set, every way out of the page asks first:
//   - refresh and closing the tab: the browser's own beforeunload prompt (it cannot be given our words);
//   - a click on a same-origin link (the rail, breadcrumbs, any <a href>): intercepted, asked, then followed;
//   - the Back button: a browser cannot be told "not yet", so when the work becomes unsaved the hook pushes
//     one sentinel history entry, {tplGuard: n}, on top of this page. Back then lands on this page's own
//     entry; the hook pushes the sentinel again (so the page and its text stay exactly as they were) and asks.
//     Confirming leaves with history.go(-2), over the sentinel and this page's entry. Saved or discarded work
//     takes the sentinel back out (history.back(), by which time the guard is off and asks nothing), so Back is
//     a plain Back again.
// `ask()` resolves true at once when nothing is unsaved.

import { useCallback, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useConfirm } from "@/components/ui";

// Each time the guard arms it stamps its entry with a new number, so an entry left behind by an earlier visit is
// never mistaken for the one it just pushed.
let armings = 0;

export function useLeaveGuard(message: string | null) {
  const router = useRouter();
  const { confirm, dialog } = useConfirm();
  const armed = message !== null;
  const latest = useRef(message);
  const pushed = useRef(false); // our sentinel entry is on top of the history stack
  const quiet = useRef(0); // popstates our own go(-2) causes: they are not the person pressing Back
  const asking = useRef(false);
  const gone = useRef(false);

  useEffect(() => {
    latest.current = message;
  });
  useEffect(() => {
    gone.current = false;
    return () => {
      gone.current = true;
    };
  }, []);

  const ask = useCallback(async (): Promise<boolean> => {
    if (latest.current === null) return true;
    if (asking.current) return false; // a question is already on screen
    asking.current = true;
    try {
      return await confirm({
        title: "Unsaved work",
        message: latest.current,
        confirmLabel: "Discard and leave",
        tone: "danger",
      });
    } finally {
      asking.current = false;
    }
  }, [confirm]);

  // Refresh and close.
  useEffect(() => {
    if (!armed) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [armed]);

  // Links. Capture phase, so this runs before the router's own click handler and can stop it.
  useEffect(() => {
    if (!armed) return;
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const link = e.target instanceof Element ? e.target.closest<HTMLAnchorElement>("a[href]") : null;
      if (!link || (link.target && link.target !== "_self") || link.hasAttribute("download")) return;
      const url = new URL(link.href, window.location.href);
      if (url.origin !== window.location.origin) return; // a real page load: beforeunload asks
      if (url.pathname + url.search === window.location.pathname + window.location.search) return; // same page
      e.preventDefault();
      e.stopPropagation();
      const href = url.pathname + url.search + url.hash;
      void ask().then((ok) => {
        if (!ok) return;
        // Replace our sentinel instead of stacking a page on it, so Back from the next page does not land on
        // a second copy of this one.
        if (pushed.current) router.replace(href);
        else router.push(href);
      });
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, [armed, ask, router]);

  // The Back button.
  useEffect(() => {
    if (!armed) return;
    const sentinel = { tplGuard: (armings += 1) };
    window.history.pushState(sentinel, "");
    pushed.current = true;
    quiet.current = 0;
    const onPop = () => {
      if (quiet.current > 0) {
        quiet.current -= 1;
        return;
      }
      if (window.history.state?.tplGuard === sentinel.tplGuard) return; // Forward, back onto our own entry
      window.history.pushState(sentinel, ""); // stay: the page and its text are untouched
      void ask().then((ok) => {
        if (!ok) return;
        quiet.current += 1;
        window.history.go(-2); // over the sentinel we just pushed and this page's own entry
      });
    };
    window.addEventListener("popstate", onPop);
    return () => {
      window.removeEventListener("popstate", onPop);
      // Saved or discarded while the page stays: take our entry back out. Leaving the page (unmount) must not.
      // The listener is already gone, so nothing needs to ignore the popstate this causes.
      // ponytail: a new edit made before this back() lands pushes a second sentinel the late popstate undoes,
      // so that one Back is unguarded. Worth a counter only if it is ever seen.
      if (pushed.current && !gone.current) window.history.back();
      pushed.current = false;
    };
  }, [armed, ask]);

  return { ask, dialog };
}
