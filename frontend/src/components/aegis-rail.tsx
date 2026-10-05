"use client";

// The one shared sidebar for the whole app (the mockup rail). Every route renders
// this via AppShell — no more per-screen sidebars. Self-contained indigo theme
// (scoped `.aerail`, dark-mode via the app's `.dark` class). Nav is complete so
// nothing is unreachable; items gate on the same permissions as before.

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth";
import { initials } from "@/lib/utils";
import { menuApi } from "@/lib/endpoints";
import type { MenuNode } from "@/lib/types";

const svg = (p: string) => <svg className="ic" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: p }} />;

type Item = { href: string; label: string; icon: string; perm?: string; children?: Item[] };
const GROUPS: { sec: string; items: Item[] }[] = [
  { sec: "Workspace", items: [
    { href: "/intake", label: "Legal Intake", icon: '<path d="M3 7l9 6 9-6"/><rect x="3" y="5" width="18" height="14" rx="2"/>' },
    { href: "/my-work", label: "My Work", icon: '<path d="M9 11l3 3 8-8"/><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"/>' },
    { href: "/notifications", label: "Notifications", icon: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>' },
    { href: "/contracts", label: "Contracts", icon: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>' },
    { href: "/matters", label: "Matters", icon: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>' },
    { href: "/search", label: "Search", icon: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>' },
    { href: "/trademarks", label: "Trademark Suite", icon: '<path d="M12 15a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"/><path d="M8.5 13.5 6 21l6-2 6 2-2.5-7.5"/>', children: [
      { href: "/trademarks/dashboard", label: "Dashboard", icon: "" },
      { href: "/trademarks/intake", label: "New intake", icon: "" },
      { href: "/trademarks/extract", label: "Document extraction", icon: "" },
      { href: "/trademarks", label: "My trademarks", icon: "" },
      { href: "/trademarks/calendar", label: "Renewal calendar", icon: "" },
      { href: "/trademarks/reports", label: "Reports", icon: "" },
      { href: "/trademarks/integrations", label: "Integration status", icon: "" },
    ] },
  ] },
  { sec: "Intelligence", items: [
    { href: "/", label: "Ask Aegis", icon: '<rect x="3" y="4" width="18" height="14" rx="2"/><path d="M8 21h8M12 18v3"/><circle cx="9" cy="11" r="1"/><circle cx="15" cy="11" r="1"/>' },
    { href: "/brain", label: "Contract Brain", icon: '<path d="M12 5a3 3 0 0 0-6 0 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 0M12 5a3 3 0 0 1 6 0 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 0M12 5v14"/>' },
    { href: "/tabular-reviews", label: "Tabular Review", icon: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/>' },
    { href: "/playbooks", label: "Playbooks", icon: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>' },
    { href: "/prompts", label: "Prompt Library", icon: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>' },
  ] },
  { sec: "Lifecycle", items: [
    { href: "/approvals", label: "Approvals", icon: '<path d="M9 11l3 3 8-8"/><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"/>' },
    { href: "/signatures", label: "Signatures", icon: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>' },
    { href: "/obligations", label: "Obligations", icon: '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>' },
    { href: "/sla", label: "SLA", icon: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>' },
    { href: "/notices", label: "Notices", icon: '<path d="M10.3 3.9 2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>', perm: "notice:read" },
    { href: "/renewals", label: "Renewals", icon: '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v5h-5"/>' },
  ] },
  { sec: "Admin", items: [
    { href: "/ai-usage", label: "AI Usage & Cost", icon: '<path d="M3 3v18h18"/><path d="M7 15l3-4 3 3 5-7"/>', perm: "admin_panel:access" },
    { href: "/jobs", label: "Background Jobs", icon: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>', perm: "admin_panel:access" },
    { href: "/workflow-builder", label: "Workflows", icon: '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="12" r="2.5"/><path d="M8.5 6H14a2 2 0 0 1 2 2v2M8.5 18H14a2 2 0 0 0 2-2v-2"/>', perm: "admin_panel:access" },
    { href: "/admin", label: "Roles & teams", icon: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>', perm: "admin_panel:access" },
  ] },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/contracts") return pathname === "/contracts" || pathname.startsWith("/contracts/");
  if (href === "/workflow-builder") return pathname.startsWith("/workflow-builder");
  // "/trademarks" is both a nav item and the parent prefix of every sibling
  // Trademark Suite route (/trademarks/intake, /trademarks/reports, ...) —
  // an exact match here keeps it from lighting up alongside those siblings.
  if (href === "/trademarks") return pathname === "/trademarks";
  return pathname === href || pathname.startsWith(href + "/");
}

function NavGroupItem({ it, pathname, onNavigate }: { it: Item; pathname: string; onNavigate?: () => void }) {
  const childActive = it.children?.some((c) => isActive(pathname, c.href)) ?? false;
  const [open, setOpen] = useState(childActive);
  if (!it.children) {
    return <Link href={it.href} onClick={onNavigate} className={isActive(pathname, it.href) ? "on" : ""}>{svg(it.icon)}<span>{it.label}</span></Link>;
  }
  return (
    <div className="grp">
      <button type="button" className={`gh ${childActive ? "on" : ""}`} onClick={() => setOpen((v) => !v)}>
        {svg(it.icon)}<span>{it.label}</span>
        <svg className={`chev ${open ? "open" : ""}`} viewBox="0 0 24 24"><path d="M9 6l6 6-6 6" stroke="currentColor" strokeWidth="1.7" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
      {open && (
        <div className="children">
          {it.children.map((c) => (
            <Link key={c.href} href={c.href} onClick={onNavigate} className={isActive(pathname, c.href) ? "on" : ""}>{c.label}</Link>
          ))}
        </div>
      )}
    </div>
  );
}

export function AegisRail({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { user, logout, switchRole } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const [switching, setSwitching] = useState<string | null>(null);
  const meRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const h = (e: MouseEvent) => { if (meRef.current && !meRef.current.contains(e.target as Node)) setMenuOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [menuOpen]);
  const roles = user?.roles ?? [];
  async function pickRole(r: string) {
    if (r === user?.active_role_name || switching) return;
    setSwitching(r);
    try {
      await switchRole(undefined, r);
      // The access token carries the active role, so reload to pick up a fresh
      // token + role-scoped menu/permissions everywhere.
      window.location.reload();
    } catch {
      setSwitching(null);
    }
  }
  const name = user?.full_name ?? user?.email ?? "You";
  const role = user?.active_role_name ?? "Legal";

  const { data } = useQuery({
    queryKey: ["menu-tree"],
    queryFn: () => menuApi.tree(),
    staleTime: 60_000,
  });

  function renderNode(n: MenuNode, depth = 0): ReactNode {
    if (n.menu_type === "group") {
      return (
        <div key={n.id}>
          <div className="sec" style={depth ? { paddingLeft: depth * 8 } : undefined}>{n.label}</div>
          {n.children.map((c) => renderNode(c, depth + 1))}
        </div>
      );
    }
    return (
      <Link key={n.id} href={n.route_path ?? "#"} onClick={onNavigate} className={isActive(pathname, n.route_path ?? "") ? "on" : ""}>{svg(n.icon ?? "")}<span>{n.label}</span></Link>
    );
  }

  return (
    <aside className="aerail">
      <style dangerouslySetInnerHTML={{ __html: RAIL_CSS }} />
      <div className="brand"><div className="mark">⚖</div><b>Aegis</b></div>
      <nav className="nav">
        {GROUPS.map((g) => {
          const items = g.items.filter((it) => !it.perm || can(user, it.perm));
          if (!items.length) return null;
          return (
            <div key={g.sec}>
              <div className="sec">{g.sec}</div>
              {items.map((it) => (
                <NavGroupItem key={it.href} it={it} pathname={pathname} onNavigate={onNavigate} />
              ))}
            </div>
          );
        })}
      </nav>
      <div className="me"><div className="av">{initials(name)}</div><div className="mi"><div className="nm">{name}</div><div className="rl">{role}</div></div></div>
        {(data?.nodes ?? []).map((n) => renderNode(n))}
      </nav>
      <div className="me" ref={meRef}>
        {menuOpen && (
          <div className="menu" role="menu" aria-label="Account">
            <div className="mh">{user?.email}</div>
            {roles.length > 0 && (
              <>
                <div className="ms">Switch role</div>
                {roles.map((r) => (
                  <button key={r} type="button" role="menuitemradio" aria-checked={r === user?.active_role_name} className={r === user?.active_role_name ? "it on" : "it"} onClick={() => void pickRole(r)} disabled={!!switching}>
                    <span>{r}</span>
                    {r === user?.active_role_name ? <span className="ck">✓</span> : switching === r ? <span className="ck">…</span> : null}
                  </button>
                ))}
                <div className="sep" />
              </>
            )}
            <button type="button" role="menuitem" className="it out" onClick={() => { setMenuOpen(false); void logout(); }}>
              <svg className="ic" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>' }} />
              <span>Sign out</span>
            </button>
          </div>
        )}
        <button type="button" className="mebtn" onClick={() => setMenuOpen((o) => !o)} aria-haspopup="menu" aria-expanded={menuOpen} title="Account">
          <div className="av">{initials(name)}</div>
          <div className="mi"><div className="nm">{name}</div><div className="rl">{role}</div></div>
          <svg className="ic car" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: '<path d="M6 15l6-6 6 6"/>' }} />
        </button>
      </div>
    </aside>
  );
}

const RAIL_CSS = `
.aerail{--accent-ink:#fff;--sans:var(--font-sans);width:212px;flex:none;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;min-height:0;height:100%;font:400 13px/1.5 var(--sans);color:var(--ink)}
.dark .aerail{--accent-ink:#0c0f16;--accent-soft:#1f2740}
.aerail .ic{width:16px;height:16px;flex:none;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.aerail .brand{display:flex;align-items:center;gap:9px;padding:14px 16px 12px;border-bottom:1px solid var(--border)}
.aerail .brand .mark{width:26px;height:26px;border-radius:8px;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;font-weight:700}
.aerail .brand b{font-size:15px;letter-spacing:-.02em}
.aerail .nav{padding:8px;display:flex;flex-direction:column;gap:1px;overflow:auto;flex:1}
.aerail .nav .sec{padding:11px 8px 4px;font:600 10px/1.4 var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.aerail .nav a{display:flex;align-items:center;gap:10px;padding:7px 9px;border-radius:8px;color:var(--ink-2);font-weight:500;text-decoration:none;cursor:pointer;font-size:12.5px}
.aerail .nav a:hover{background:var(--surface-2);color:var(--ink)}
.aerail .nav a.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
.aerail .nav .grp{display:flex;flex-direction:column}
.aerail .nav .gh{display:flex;align-items:center;gap:10px;padding:7px 9px;border-radius:8px;color:var(--ink-2);font-weight:500;text-decoration:none;cursor:pointer;font-size:12.5px;background:none;border:none;width:100%;text-align:left;font-family:inherit}
.aerail .nav .gh:hover{background:var(--surface-2);color:var(--ink)}
.aerail .nav .gh.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
.aerail .nav .gh .chev{width:13px;height:13px;margin-left:auto;flex:none;transition:transform .15s}
.aerail .nav .gh .chev.open{transform:rotate(90deg)}
.aerail .nav .children{display:flex;flex-direction:column;gap:1px;padding-left:26px}
.aerail .nav .children a{padding:6px 9px;font-size:12px}
.aerail .me{border-top:1px solid var(--border);padding:10px 13px;display:flex;align-items:center;gap:9px}
.aerail .me{position:relative;border-top:1px solid var(--border);padding:8px}
.aerail .me .mebtn{display:flex;align-items:center;gap:9px;width:100%;padding:5px;border:0;border-radius:8px;background:none;color:inherit;font:inherit;text-align:left;cursor:pointer}
.aerail .me .mebtn:hover{background:var(--surface-2)}
.aerail .me .av{width:28px;height:28px;border-radius:50%;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;font-weight:700;font-size:12px;flex:none}
.aerail .me .mi{min-width:0;flex:1} .aerail .me .nm{font-weight:600;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis} .aerail .me .rl{font-size:11px;color:var(--ink-3)}
.aerail .me .car{color:var(--ink-3)}
.aerail .me .menu{position:absolute;left:8px;right:8px;bottom:calc(100% - 2px);background:var(--surface);border:1px solid var(--border);border-radius:10px;box-shadow:0 12px 30px rgba(20,26,40,.16);padding:6px;z-index:30}
.aerail .me .menu .mh{padding:6px 9px;font-size:11.5px;color:var(--ink-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.aerail .me .menu .ms{padding:8px 9px 4px;font:600 10px var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.aerail .me .menu .sep{height:1px;background:var(--border);margin:5px 0}
.aerail .me .menu .it{display:flex;align-items:center;justify-content:space-between;gap:8px;width:100%;padding:7px 9px;border:0;border-radius:7px;background:none;color:var(--ink-2);font:500 12.5px var(--sans);text-align:left;cursor:pointer}
.aerail .me .menu .it:hover:not(:disabled){background:var(--surface-2);color:var(--ink)}
.aerail .me .menu .it.on{color:var(--accent);font-weight:600}
.aerail .me .menu .it.out{justify-content:flex-start;gap:9px;color:#dc2626}
.aerail .me .menu .ck{font-weight:700}
`;
