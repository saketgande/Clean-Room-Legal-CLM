"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { notificationsApi } from "@/lib/endpoints";
import {
  Scale,
  FileText,
  FolderKanban,
  Search,
  Bot,
  Brain,
  Table2,
  BookMarked,
  Workflow as WorkflowIcon,
  Library,
  ClipboardCheck,
  Signature,
  ListChecks,
  RefreshCw,
  Activity,
  Bell,
  Settings,
  ChevronDown,
  LogOut,
  Menu,
  PanelLeftClose,
  X,
  Stamp,
 Gauge } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { can } from "@/lib/intake";
import { useLayout } from "@/lib/layout";
import { cn, initials } from "@/lib/utils";
import { ThemeToggle } from "@/components/theme-toggle";

const NAV: {
  section: string;
  items: { href: string; label: string; icon: React.ElementType }[];
}[] = [
  {
    section: "Workspace",
    items: [
      { href: "/intake", label: "Legal Intake", icon: Gauge },
      { href: "/", label: "Ask Aegis", icon: Bot },
      { href: "/projects", label: "Projects", icon: FolderKanban },
      { href: "/search", label: "Search", icon: Search },
    ],
  },
  {
    section: "Intelligence",
    items: [
      { href: "/brain", label: "Contract Brain", icon: Brain },
      { href: "/tabular-reviews", label: "Tabular Review", icon: Table2 },
      { href: "/playbooks", label: "Playbooks", icon: BookMarked },
      { href: "/workflows", label: "Prompt Library", icon: Library },
    ],
  },
  {
    section: "Lifecycle",
    items: [
      { href: "/workflow-builder", label: "Workflows", icon: WorkflowIcon },
      { href: "/approvals", label: "Approvals", icon: ClipboardCheck },
      { href: "/signatures", label: "Signatures", icon: Signature },
      { href: "/obligations", label: "Obligations", icon: ListChecks },
      { href: "/renewals", label: "Renewals", icon: RefreshCw },
      { href: "/trademarks", label: "Trademarks", icon: Stamp },
    ],
  },
  // Jobs & Admin live in the sidebar-footer account menu; Notifications lives
  // in the top-right of the header — none belong in the primary nav.
];

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

function SidebarNav({
  collapsed,
  onNavigate,
}: {
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();
  const unread = useUnreadCount();
  const { setCollapsed, forceCollapsed } = useLayout();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuTriggerRef = useRef<HTMLButtonElement>(null);

  // Keyboard support for the account menu (WCAG 2.1.1): focus the first item
  // on open; Escape closes and returns focus; arrows move between items.
  useEffect(() => {
    if (!menuOpen) return;
    const items = () =>
      Array.from(
        menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ??
          [],
      );
    items()[0]?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMenuOpen(false);
        menuTriggerRef.current?.focus();
        return;
      }
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      e.preventDefault();
      const els = items();
      if (els.length === 0) return;
      const idx = els.indexOf(document.activeElement as HTMLElement);
      const delta = e.key === "ArrowDown" ? 1 : -1;
      els[(idx + delta + els.length) % els.length]?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const canToggle = !forceCollapsed;
  const toggleCollapsed = () => {
    if (canToggle) setCollapsed(!collapsed);
  };
  const notifActive = pathname.startsWith("/notifications");

  const isActive = (href: string) => {
    if (href === "/")
      return pathname === "/" || pathname.startsWith("/assistant");
    // Legal Intake is the hub — it owns contracts and the retired /command route.
    if (href === "/intake")
      return (
        pathname.startsWith("/intake") ||
        pathname.startsWith("/contracts") ||
        pathname.startsWith("/command")
      );
    return pathname.startsWith(href);
  };

  // Navigate + close the menu / mobile drawer.
  const go = (href: string) => {
    router.push(href);
    setMenuOpen(false);
    onNavigate?.();
  };

  return (
    <div className="flex h-full flex-col">
      {/* Brand */}
      <div
        className={cn(
          "flex h-16 shrink-0 items-center",
          collapsed ? "justify-center px-2" : "justify-between pl-6 pr-3",
        )}
      >
        {collapsed ? (
          canToggle ? (
            <button
              onClick={toggleCollapsed}
              title="Expand sidebar"
              aria-label="Expand sidebar"
              className="flex h-8 w-8 items-center justify-center rounded-md bg-slate-900 text-base font-semibold text-white transition-colors hover:bg-brand-700"
            >
              A
            </button>
          ) : (
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-slate-900 text-base font-semibold text-white">
              A
            </div>
          )
        ) : (
          <>
            <div className="flex items-baseline gap-2">
              <span className="text-[17px] font-semibold tracking-tight text-slate-900">
                Aegis
              </span>
              <span className="text-[9px] font-medium uppercase tracking-[0.12em] text-slate-500">
                Legal
              </span>
            </div>
            {canToggle && (
              <button
                onClick={toggleCollapsed}
                title="Collapse sidebar"
                aria-label="Collapse sidebar"
                className="hidden rounded-md p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 lg:inline-flex"
              >
                <PanelLeftClose className="h-5 w-5" />
              </button>
            )}
          </>
        )}
      </div>

      {/* Search — top of the sidebar */}
      <div className={cn("shrink-0 pb-2", collapsed ? "px-2" : "px-3")}>
        <button
          onClick={() => go("/search")}
          title={collapsed ? "Search" : undefined}
          aria-label="Search"
          className={cn(
            "flex h-9 items-center rounded-md border border-slate-200 bg-slate-100 text-sm text-slate-500 transition-colors hover:border-slate-300 hover:text-slate-700",
            collapsed ? "w-full justify-center" : "w-full gap-2 px-3",
          )}
        >
          <Search className="h-4 w-4 shrink-0" />
          {!collapsed && (
            <>
              <span className="flex-1 truncate text-left">Search…</span>
              <kbd className="rounded border border-slate-200 px-1.5 py-0.5 text-[10px] font-medium text-slate-400">
                ⌘K
              </kbd>
            </>
          )}
        </button>
      </div>

      {/* Primary navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-2">
        {NAV.map((group) => (
          <div key={group.section} className="mb-5">
            {!collapsed && (
              <p className="px-2.5 pb-1.5 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
                {group.section}
              </p>
            )}
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const Icon = item.icon;
                const active = isActive(item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={onNavigate}
                    title={collapsed ? item.label : undefined}
                    className={cn(
                      "relative flex items-center rounded-lg text-[13px] transition-colors",
                      collapsed
                        ? "justify-center px-2 py-2"
                        : "gap-2.5 px-2.5 py-1.5",
                      active
                        ? "bg-slate-100 font-medium text-brand-700 shadow-sm ring-1 ring-slate-200"
                        : "font-normal text-slate-600 hover:bg-slate-100 hover:text-slate-900",
                    )}
                  >
                    {active && !collapsed && (
                      <span className="absolute -left-1.5 top-1/2 h-4 w-[2.5px] -translate-y-1/2 rounded-full bg-brand-600" />
                    )}
                    <Icon
                      className={cn(
                        "h-[17px] w-[17px] shrink-0",
                        active ? "text-brand-600" : "text-slate-400",
                      )}
                    />
                    {!collapsed && item.label}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Notifications — relocated here from the removed top bar */}
      <div className={cn("shrink-0", collapsed ? "px-2 pb-2" : "px-3 pb-2")}>
        <Link
          href="/notifications"
          onClick={onNavigate}
          title={collapsed ? "Notifications" : undefined}
          className={cn(
            "flex items-center rounded-md text-sm transition-colors",
            collapsed ? "justify-center px-2 py-2.5" : "gap-2.5 px-2.5 py-2",
            notifActive
              ? "bg-brand-50 font-medium text-brand-700"
              : "font-normal text-slate-600 hover:bg-slate-100 hover:text-slate-900",
          )}
        >
          <span className="relative shrink-0">
            <Bell
              className={cn(
                "h-[17px] w-[17px]",
                notifActive ? "text-brand-600" : "text-slate-400",
              )}
            />
            {unread > 0 && (
              <span className="absolute -right-1.5 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-brand-600 px-1 text-[9px] font-bold text-white">
                {unread > 99 ? "99+" : unread}
              </span>
            )}
          </span>
          {!collapsed && "Notifications"}
        </Link>
      </div>

      {/* Footer — theme toggle + user info (very bottom of the sidebar) */}
      <div
        className={cn(
          "relative shrink-0 border-t border-slate-200",
          collapsed ? "p-2" : "p-3",
        )}
      >
        {collapsed ? (
          <div className="flex flex-col items-center gap-2">
            <ThemeToggle />
            <button
              ref={menuTriggerRef}
              onClick={() => setMenuOpen((o) => !o)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              title={user?.full_name ?? "Account"}
              aria-label={user?.full_name ?? "Account"}
              className="flex h-9 w-9 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-1"
            >
              {initials(user?.full_name)}
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <button
              ref={menuTriggerRef}
              onClick={() => setMenuOpen((o) => !o)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              className="flex min-w-0 flex-1 items-center gap-2.5 rounded-md px-2 py-1.5 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white">
                {initials(user?.full_name)}
              </div>
              <div className="min-w-0 flex-1 text-left">
                <p className="truncate text-sm font-medium leading-tight text-slate-900">
                  {user?.full_name ?? "User"}
                </p>
                <p className="truncate text-xs leading-tight text-slate-500">
                  {user?.active_role_name ?? user?.roles?.[0] ?? "member"}
                </p>
              </div>
              <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
            </button>
            <ThemeToggle />
          </div>
        )}

        {/* Account menu — opens upward (footer sits at the bottom). */}
        {menuOpen && (
          <>
            <div
              className="fixed inset-0 z-10"
              onClick={() => setMenuOpen(false)}
              aria-hidden="true"
            />
            <div
              ref={menuRef}
              role="menu"
              aria-label="Account"
              className={cn(
                "absolute bottom-full z-20 mb-2 animate-fade-in rounded-md border border-slate-200 bg-slate-100 p-1.5 shadow-pop",
                collapsed ? "left-2 w-60" : "left-3 right-3",
              )}
            >
              <div className="border-b border-slate-200 px-3 py-2">
                <p className="truncate text-sm font-medium text-slate-900">
                  {user?.full_name ?? "User"}
                </p>
                <p className="truncate text-xs text-slate-500">
                  {user?.email}
                </p>
              </div>
              <button
                role="menuitem"
                onClick={() => go("/jobs")}
                className="mt-1 flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-200 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                <Activity className="h-4 w-4 text-slate-400" aria-hidden="true" />
                My Jobs
              </button>
              {can(user, "admin_panel:access") && (
                <button
                  role="menuitem"
                  onClick={() => go("/admin")}
                  className="flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-200 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                >
                  <Settings className="h-4 w-4 text-slate-400" aria-hidden="true" />
                  Admin &amp; settings
                </button>
              )}
              <div className="my-1 border-t border-slate-200" />
              <button
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  void logout();
                }}
                className="flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium text-danger hover:bg-danger-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                <LogOut className="h-4 w-4" />
                Sign out
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { collapsed, mobileOpen, setMobileOpen, forceCollapsed } = useLayout();
  const mobileUnread = useUnreadCount();
  const pathname = usePathname();

  const deskCollapsed = forceCollapsed || collapsed;
  const notifActive = pathname.startsWith("/notifications");
  // Immersive routes fill the whole main area instead of the centered,
  // max-width-capped content column: the playbook builder, the AI assistant
  // workspace, and the contract detail workspace all run edge-to-edge.
  const fullBleed =
    pathname === "/playbooks/build" ||
    pathname === "/" ||
    pathname.startsWith("/assistant") ||
    pathname.startsWith("/contracts/");

  return (
    <div className="flex h-screen overflow-hidden bg-slate-50">
      {/* Skip link (WCAG 2.4.1) — visually hidden until keyboard-focused. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[60] focus:rounded focus:bg-brand-600 focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-white focus:shadow-pop"
      >
        Skip to main content
      </a>
      {/* Desktop sidebar */}
      <aside
        className={cn(
          "hidden shrink-0 flex-col border-r border-slate-200 bg-slate-50 transition-[width] duration-200 lg:flex",
          deskCollapsed ? "w-[4.25rem]" : "w-56",
        )}
      >
        <SidebarNav collapsed={deskCollapsed} />
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="absolute left-0 top-0 flex h-full w-64 animate-fade-in flex-col border-r border-slate-200 bg-slate-50">
            <button
              onClick={() => setMobileOpen(false)}
              className="absolute right-3 top-4 rounded-md p-1 text-slate-400 hover:bg-slate-100"
              aria-label="Close menu"
            >
              <X className="h-4 w-4" />
            </button>
            <SidebarNav
              collapsed={false}
              onNavigate={() => setMobileOpen(false)}
            />
          </aside>
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

        <main id="main-content" className="flex-1 overflow-y-auto bg-slate-50">
          {fullBleed ? (
            children
          ) : (
            <div className="mx-auto max-w-[1400px] px-5 py-4 sm:px-6">
              {children}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
