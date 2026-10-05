"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { notificationsApi } from "@/lib/endpoints";
import { Bell, Menu, X } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { AegisRail } from "./aegis-rail";

import { useLayout } from "@/lib/layout";
import { cn } from "@/lib/utils";

/** Unread in-app notifications — drives the bell badges. Polled lightly so the
 * badge stays fresh without a websocket. */
function useUnreadCount(): number {
  const { user } = useAuth();
  const { data } = useQuery({
    queryKey: ["notifications", "unread-count"],
    queryFn: notificationsApi.unreadCount,
    enabled: !!user,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
  return data?.count ?? 0;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { mobileOpen, setMobileOpen } = useLayout();
  const mobileUnread = useUnreadCount();
  const pathname = usePathname();
  const notifActive = pathname.startsWith("/notifications");
  // The redesigned mockup screens fill the whole content area with their own
  // full-height layout (topbar + body) and manage their own scroll, so the main
  // area is overflow-hidden for them. They share the one AegisRail like every
  // other route — no more per-screen sidebars.
  const mockupRoute =
    pathname === "/intake" ||
    pathname === "/my-work" ||
    pathname === "/contracts" ||
    pathname.startsWith("/contracts/") ||
    pathname === "/brain" ||
    pathname.startsWith("/workflow-builder/");
  const oldFullBleed =
    pathname === "/playbooks/build" ||
    pathname === "/" ||
    pathname.startsWith("/assistant");
  // The old centered, max-width-capped column + breadcrumb strip is legacy
  // chrome — only the pages that haven't been reskinned to the mockup style yet
  // still need it (it gives their old @/components/ui-based layout room to
  // breathe). Every reskinned page manages its own width/padding/background
  // exactly like the mockup screens, so it renders edge-to-edge here — wrapping
  // it in the old box double-pads it and leaves a stray "Home > X" breadcrumb
  // bar that no mockup screen has. Shrink this list as more pages get reskinned.
  const needsOldChrome =
    pathname === "/admin" || pathname.startsWith("/admin/") ||
    pathname === "/jobs" || pathname.startsWith("/jobs/") ||
    pathname === "/notifications" || pathname.startsWith("/notifications/") ||
    pathname.startsWith("/notices/") ||
    pathname.startsWith("/tabular-reviews/") ||
    (pathname.startsWith("/playbooks/") && pathname !== "/playbooks/build");
  const fullBleed = mockupRoute || oldFullBleed || !needsOldChrome;

  return (
    <div className="flex h-screen overflow-hidden bg-slate-50">
      {/* Skip link (WCAG 2.4.1) — visually hidden until keyboard-focused. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[60] focus:rounded focus:bg-brand-600 focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-white focus:shadow-pop"
      >
        Skip to main content
      </a>
      {/* Desktop sidebar — the one shared rail for every route. */}
      <div className="hidden lg:block">
        <AegisRail />
      </div>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
          />
          <div className="absolute left-0 top-0 h-full animate-fade-in">
            <button
              onClick={() => setMobileOpen(false)}
              className="absolute right-2 top-3 z-10 rounded-md p-1 text-slate-400 hover:bg-slate-100"
              aria-label="Close menu"
            >
              <X className="h-4 w-4" />
            </button>
            <AegisRail onNavigate={() => setMobileOpen(false)} />
          </div>
        </div>
      )}

      {/* Main */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Mobile top bar only — on desktop these controls live in the sidebar,
            so the main area runs full-height with no top chrome. */}
        <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-slate-50 px-3 sm:px-6 lg:hidden">
          <button
            onClick={() => setMobileOpen(true)}
            className="rounded-md p-2 text-slate-500 hover:bg-slate-100"
            aria-label="Open menu"
          >
            <Menu className="h-5 w-5" />
          </button>

          <Link
            href="/notifications"
            aria-label="Notifications"
            title="Notifications"
            className={cn(
              "relative flex h-9 w-9 shrink-0 items-center justify-center rounded-md transition-colors",
              notifActive
                ? "bg-brand-50 text-brand-700"
                : "text-slate-500 hover:bg-slate-100 hover:text-slate-900",
            )}
          >
            <Bell className="h-5 w-5" />
            {mobileUnread > 0 && (
              <span className="absolute right-0.5 top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-brand-600 px-1 text-[9px] font-bold text-white">
                {mobileUnread > 99 ? "99+" : mobileUnread}
              </span>
            )}
          </Link>
        </header>

        <main id="main-content" className={cn("flex flex-1 flex-col min-h-0 bg-slate-50", mockupRoute ? "overflow-hidden" : "overflow-y-auto")}>
          {fullBleed ? (
            // Full-height app screens (intake, contract workspace, assistant, etc.)
            // manage their own flex layout and must stay direct flex children.
            // The reskinned list pages (.wfl/.ctl/…) instead center a
            // max-width column with `margin:0 auto`; as a flex item that auto
            // margin collapses them to content width (2 cramped columns), so they
            // need a plain block wrapper to expand to their max-width like the
            // mockup's block-flow `.main` — exactly reproducing screen-01..14.
            mockupRoute || oldFullBleed ? (
              children
            ) : (
              <div className="w-full">{children}</div>
            )
          ) : (
            // Old-chrome pages (admin, jobs, notifications, kit detail pages) run
            // full-width with the same ~24px gutter as the scoped screens — one
            // width policy across the app, no centered max-width column.
            <div className="w-full px-6 py-5">
              {children}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
