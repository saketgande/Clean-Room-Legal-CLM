"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Scale,
  LayoutDashboard,
  FileText,
  FolderKanban,
  Search,
  Bot,
  Brain,
  Table2,
  BookMarked,
  Workflow as WorkflowIcon,
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
  PanelLeftOpen,
  X,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
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
      { href: "/", label: "AI Assistant", icon: Bot },
      { href: "/contract-hub", label: "Contract Hub", icon: LayoutDashboard },
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
      { href: "/workflows", label: "Workflows", icon: WorkflowIcon },
    ],
  },
  {
    section: "Lifecycle",
    items: [
      { href: "/approvals", label: "Approvals", icon: ClipboardCheck },
      { href: "/signatures", label: "Signatures", icon: Signature },
      { href: "/obligations", label: "Obligations", icon: ListChecks },
      { href: "/renewals", label: "Renewals", icon: RefreshCw },
    ],
  },
  // Jobs & Admin live in the sidebar-footer account menu; Notifications lives
  // in the top-right of the header — none belong in the primary nav.
];

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
  const [menuOpen, setMenuOpen] = useState(false);

  const isActive = (href: string) => {
    if (href === "/")
      return pathname === "/" || pathname.startsWith("/assistant");
    if (href === "/contract-hub")
      return (
        pathname.startsWith("/contract-hub") ||
        pathname.startsWith("/contracts")
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
          collapsed ? "justify-center px-2" : "px-6",
        )}
      >
        {collapsed ? (
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 font-serif text-base font-medium text-white">
            A
          </div>
        ) : (
          <div className="flex items-baseline gap-2">
            <span className="font-serif text-[21px] font-medium tracking-tight text-slate-900">
              Aegis
            </span>
            <span className="text-[9px] font-semibold uppercase tracking-[0.2em] text-slate-400">
              Legal
            </span>
          </div>
        )}
      </div>

      {/* Search — top of the sidebar */}
      <div className={cn("shrink-0 pb-2", collapsed ? "px-2" : "px-3")}>
        <button
          onClick={() => go("/search")}
          title={collapsed ? "Search" : undefined}
          aria-label="Search"
          className={cn(
            "flex h-9 items-center rounded-lg border border-slate-200 bg-slate-100 text-sm text-slate-400 transition-colors hover:border-slate-300 hover:text-slate-600",
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
              <p className="px-2.5 pb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">
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
                      "relative flex items-center rounded-lg text-sm transition-colors",
                      collapsed
                        ? "justify-center px-2 py-2.5"
                        : "gap-2.5 px-2.5 py-2",
                      active
                        ? "bg-brand-50 font-semibold text-brand-700"
                        : "font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900",
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
              onClick={() => setMenuOpen((o) => !o)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              title={user?.full_name ?? "Account"}
              className="flex h-9 w-9 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white"
            >
              {initials(user?.full_name)}
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <button
              onClick={() => setMenuOpen((o) => !o)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-2 py-1.5 hover:bg-slate-100"
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
            />
            <div
              className={cn(
                "absolute bottom-full z-20 mb-2 animate-fade-in rounded-xl border border-slate-200 bg-slate-100 p-1.5 shadow-pop",
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
                onClick={() => go("/jobs")}
                className="mt-1 flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-200 hover:text-slate-900"
              >
                <Activity className="h-4 w-4 text-slate-400" />
                My Jobs
              </button>
              <button
                onClick={() => go("/admin")}
                className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-200 hover:text-slate-900"
              >
                <Settings className="h-4 w-4 text-slate-400" />
                Admin &amp; settings
              </button>
              <div className="my-1 border-t border-slate-200" />
              <button
                onClick={() => {
                  setMenuOpen(false);
                  void logout();
                }}
                className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium text-red-600 hover:bg-red-500/10"
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
  const {
    collapsed,
    setCollapsed,
    mobileOpen,
    setMobileOpen,
    forceCollapsed,
  } = useLayout();
  const pathname = usePathname();

  const deskCollapsed = forceCollapsed || collapsed;
  const notifActive = pathname.startsWith("/notifications");

  return (
    <div className="flex h-screen overflow-hidden bg-slate-50">
      {/* Desktop sidebar */}
      <aside
        className={cn(
          "hidden shrink-0 flex-col border-r border-slate-200 bg-slate-50 transition-[width] duration-200 lg:flex",
          deskCollapsed ? "w-[4.25rem]" : "w-60",
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
        <header className="flex h-16 shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-slate-50 px-3 sm:px-6">
          <div className="flex min-w-0 flex-1 items-center gap-2">
            {/* Mobile hamburger */}
            <button
              onClick={() => setMobileOpen(true)}
              className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 lg:hidden"
              aria-label="Open menu"
            >
              <Menu className="h-5 w-5" />
            </button>
            {/* Desktop collapse toggle */}
            <button
              onClick={() => setCollapsed(!collapsed)}
              disabled={forceCollapsed}
              title={
                forceCollapsed
                  ? "Expanded nav is hidden in this workspace"
                  : collapsed
                    ? "Expand sidebar"
                    : "Collapse sidebar"
              }
              className="hidden rounded-lg p-2 text-slate-500 hover:bg-slate-100 disabled:opacity-40 lg:inline-flex"
              aria-label="Toggle sidebar"
            >
              {deskCollapsed ? (
                <PanelLeftOpen className="h-5 w-5" />
              ) : (
                <PanelLeftClose className="h-5 w-5" />
              )}
            </button>
          </div>

          {/* Notifications — top-right */}
          <Link
            href="/notifications"
            aria-label="Notifications"
            title="Notifications"
            className={cn(
              "relative flex h-9 w-9 shrink-0 items-center justify-center rounded-lg transition-colors",
              notifActive
                ? "bg-brand-50 text-brand-700"
                : "text-slate-500 hover:bg-slate-100 hover:text-slate-900",
            )}
          >
            <Bell className="h-5 w-5" />
          </Link>
        </header>

        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-7xl px-4 py-4 sm:px-6 sm:py-6">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
