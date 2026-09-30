"use client";

// The one shared sidebar for the whole app (the mockup rail). Every route renders
// this via AppShell — no more per-screen sidebars. Self-contained indigo theme
// (scoped `.aerail`, dark-mode via the app's `.dark` class). Nav is complete so
// nothing is unreachable; items gate on the same permissions as before.

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth";
import { initials } from "@/lib/utils";
import { menuApi } from "@/lib/endpoints";
import type { MenuNode } from "@/lib/types";

const svg = (p: string) => <svg className="ic" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: p }} />;

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/contracts") return pathname === "/contracts" || pathname.startsWith("/contracts/");
  if (href === "/workflow-builder") return pathname.startsWith("/workflow-builder");
  return pathname === href || pathname.startsWith(href + "/");
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
