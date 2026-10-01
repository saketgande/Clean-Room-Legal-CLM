"use client";

// FR-14 deep-link gate: one guard, mounted once in `(app)/layout.tsx`, keyed
// on `usePathname()` -> `route_path`, covering every current and future
// route (including deep links no menu node points to) by construction —
// instead of a per-page guard that a new page could silently omit.
//
// This is a UX / defense-in-depth layer only (FR-13): the real security
// boundary is the backend's `require_screen_level` dependency (T008 and the
// tranche-1 retrofit). A user blocked here has simply not been granted VIEW
// on this screen per `GET /screen-access/me` (menuApi.myScreenAccess); a
// user who bypasses this guard entirely still can't perform anything the API
// doesn't independently allow.
import { useQuery } from "@tanstack/react-query";
import { usePathname } from "next/navigation";
import { Lock } from "lucide-react";
import { CenterSpinner, EmptyState, PageHeader } from "@/components/ui";
import { menuApi } from "@/lib/endpoints";

// The full 39-screen route-path inventory (plan.md's "Screen catalog"
// table), verbatim. This is a route *inventory* — a list of what pages
// exist — not a nav list and not an access rule; every access decision below
// still comes entirely from the `/screen-access/me` server response. It
// exists only to distinguish "this pathname is not a governed screen at
// all" (allow) from "this is a governed screen the caller has no grant on"
// (block); `GET /screens` can't be used for that distinction here because
// it's `screen_access:read`-gated (admin-only) and would 403 for everyone
// else, which would make the guard fail open.
const ALL_SCREEN_ROUTE_PATHS: readonly string[] = [
  "/",
  "/my-work",
  "/notifications",
  "/search",
  "/prompts",
  "/assistant",
  "/brain",
  "/tabular-reviews",
  "/tabular-reviews/[id]",
  "/playbooks",
  "/playbooks/[id]",
  "/playbooks/build",
  "/contracts",
  "/contracts/[id]",
  "/intake",
  "/delegations",
  "/approvals",
  "/signatures",
  "/obligations",
  "/sla",
  "/notices",
  "/notices/[id]",
  "/renewals",
  "/trademarks",
  "/trademarks/[id]",
  "/trademarks/calendar",
  "/trademarks/intake",
  "/trademarks/extract",
  "/trademarks/integrations",
  "/trademarks/reports",
  "/workflow-builder",
  "/workflow-builder/[id]",
  "/ai-usage",
  "/jobs",
  "/admin",
  "/org-structure",
  "/screen-access",
] as const;

// Segment-wise route match: a `[param]` segment matches exactly one path
// segment ("/contracts/abc-123" matches "/contracts/[id]"; "/contracts" does
// not). Both sides are compared with leading/trailing slashes stripped.
function matchesRoutePath(pathname: string, routePath: string): boolean {
  const a = pathname.split("/").filter(Boolean);
  const b = routePath.split("/").filter(Boolean);
  if (a.length !== b.length) return false;
  return b.every((seg, i) => (seg.startsWith("[") && seg.endsWith("]")) || seg === a[i]);
}

function isKnownScreenRoute(pathname: string): boolean {
  return ALL_SCREEN_ROUTE_PATHS.some((routePath) => matchesRoutePath(pathname, routePath));
}

export function ScreenGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { data, isLoading } = useQuery({
    queryKey: ["screen-access", "me", "all"],
    queryFn: () => menuApi.myScreenAccess(),
  });

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center py-12">
        <CenterSpinner label="Checking access…" />
      </div>
    );
  }

  const path = pathname ?? "";

  // Unknown route (not in the screen catalog) — nothing this feature governs
  // to check, so allow through. A guard that blocked unknown routes would
  // deny things (e.g. a future uncatalogued page, or a 404) this feature
  // never claimed to enforce.
  if (!isKnownScreenRoute(path)) {
    return <>{children}</>;
  }

  // Known screen: the caller has at least VIEW iff `/screen-access/me`
  // includes a matching entry for this pathname (FR-7: included iff >= VIEW).
  const hasAccess = (data?.screens ?? []).some((entry) => matchesRoutePath(path, entry.route_path));

  if (hasAccess) {
    return <>{children}</>;
  }

  return (
    <div className="mx-auto max-w-[1280px] space-y-4">
      <PageHeader title="Access restricted" description="You don't have permission to view this page." />
      <EmptyState
        icon={<Lock className="h-6 w-6" />}
        title="Access restricted"
        description="You don't have permission to view this page. Contact an admin if you need access."
      />
    </div>
  );
}
