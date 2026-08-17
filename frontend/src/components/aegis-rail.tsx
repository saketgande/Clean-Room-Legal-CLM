"use client";

// The one shared sidebar for the whole app (the mockup rail). Every route renders
// this via AppShell — no more per-screen sidebars. Self-contained indigo theme
// (scoped `.aerail`, dark-mode via the app's `.dark` class). Nav is complete so
// nothing is unreachable; items gate on the same permissions as before.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { can } from "@/lib/intake";
import { initials } from "@/lib/utils";

const svg = (p: string) => <svg className="ic" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: p }} />;

type Item = { href: string; label: string; icon: string; perm?: string };
const GROUPS: { sec: string; items: Item[] }[] = [
  { sec: "Workspace", items: [
    { href: "/intake", label: "Legal Intake", icon: '<path d="M3 7l9 6 9-6"/><rect x="3" y="5" width="18" height="14" rx="2"/>' },
    { href: "/my-work", label: "My Work", icon: '<path d="M9 11l3 3 8-8"/><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"/>' },
    { href: "/contracts", label: "Contracts", icon: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>' },
    { href: "/search", label: "Search", icon: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>' },
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
    { href: "/workflow-builder", label: "Workflows", icon: '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="12" r="2.5"/><path d="M8.5 6H14a2 2 0 0 1 2 2v2M8.5 18H14a2 2 0 0 0 2-2v-2"/>', perm: "admin_panel:access" },
    { href: "/admin", label: "Roles & teams", icon: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>', perm: "admin_panel:access" },
    { href: "/projects", label: "Projects", icon: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>' },
  ] },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/contracts") return pathname === "/contracts" || pathname.startsWith("/contracts/");
  if (href === "/workflow-builder") return pathname.startsWith("/workflow-builder");
  return pathname === href || pathname.startsWith(href + "/");
}

export function AegisRail({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { user } = useAuth();
  const name = user?.full_name ?? user?.email ?? "You";
  const role = user?.active_role_name ?? "Legal";
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
                <Link key={it.href} href={it.href} onClick={onNavigate} className={isActive(pathname, it.href) ? "on" : ""}>{svg(it.icon)}<span>{it.label}</span></Link>
              ))}
            </div>
          );
        })}
      </nav>
      <div className="me"><div className="av">{initials(name)}</div><div className="mi"><div className="nm">{name}</div><div className="rl">{role}</div></div></div>
    </aside>
  );
}

const RAIL_CSS = `
.aerail{--surface:#fff;--surface-2:#eef1f6;--ink:#18213a;--ink-2:#586178;--ink-3:#8a92a6;--border:#e4e8f0;--accent:#3b4aa0;--accent-ink:#fff;--accent-soft:#eaecf8;--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;width:212px;flex:none;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;min-height:0;height:100%;font:400 13px/1.5 var(--sans);color:var(--ink)}
.dark .aerail{--surface:#141922;--surface-2:#1b2130;--ink:#e8ebf3;--ink-2:#9aa3b8;--ink-3:#6b7488;--border:#242b39;--accent:#8290e6;--accent-ink:#0c0f16;--accent-soft:#1f2740}
.aerail .ic{width:16px;height:16px;flex:none;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.aerail .brand{display:flex;align-items:center;gap:9px;padding:14px 16px 12px;border-bottom:1px solid var(--border)}
.aerail .brand .mark{width:26px;height:26px;border-radius:8px;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;font-weight:700}
.aerail .brand b{font-size:15px;letter-spacing:-.02em}
.aerail .nav{padding:8px;display:flex;flex-direction:column;gap:1px;overflow:auto;flex:1}
.aerail .nav .sec{padding:11px 8px 4px;font:600 10px/1.4 var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.aerail .nav a{display:flex;align-items:center;gap:10px;padding:7px 9px;border-radius:8px;color:var(--ink-2);font-weight:500;text-decoration:none;cursor:pointer;font-size:12.5px}
.aerail .nav a:hover{background:var(--surface-2);color:var(--ink)}
.aerail .nav a.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
.aerail .me{border-top:1px solid var(--border);padding:10px 13px;display:flex;align-items:center;gap:9px}
.aerail .me .av{width:28px;height:28px;border-radius:50%;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;font-weight:700;font-size:12px;flex:none}
.aerail .me .mi{min-width:0} .aerail .me .nm{font-weight:600;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis} .aerail .me .rl{font-size:11px;color:var(--ink-3)}
`;
