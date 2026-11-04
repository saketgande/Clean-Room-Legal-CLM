"use client";

// The one shared sidebar for the whole app (the mockup rail). Every route renders
// this via AppShell — no more per-screen sidebars. Self-contained indigo theme
// (scoped `.aerail`, dark-mode via the app's `.dark` class). Nav is complete so
// nothing is unreachable; items gate on the same permissions as before.

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { menuApi } from "@/lib/endpoints";
import type { MenuNode } from "@/lib/types";

const svg = (p: string) => <svg className="ic" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: p }} />;

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/contracts") return pathname === "/contracts" || pathname.startsWith("/contracts/");
  if (href === "/workflow-builder") return pathname.startsWith("/workflow-builder");
  // "My trademarks" is /trademarks, the prefix of every sibling Trademark Suite
  // route (/trademarks/dashboard, /intake, ...) — exact match keeps it from lighting up with them.
  if (href === "/trademarks") return pathname === "/trademarks";
  return pathname === href || pathname.startsWith(href + "/");
}

export function AegisRail({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  // Collapsed = icons only. Remembered per browser; the mobile drawer (onNavigate) is never collapsed.
  const [collapsedPref, setCollapsedPref] = useState(false);
  useEffect(() => {
    try { setCollapsedPref(localStorage.getItem("aegis.rail.collapsed") === "1"); } catch { /* storage blocked: stay expanded */ }
  }, []);
  const collapsed = collapsedPref && !onNavigate;
  function toggleRail() {
    setCollapsedPref((c) => {
      const next = !c;
      try { localStorage.setItem("aegis.rail.collapsed", next ? "1" : "0"); } catch { /* ignore */ }
      return next;
    });
  }
  const { data } = useQuery({
    queryKey: ["menu-tree"],
    queryFn: () => menuApi.tree(),
    staleTime: 60_000,
  });

  function renderNode(n: MenuNode, depth = 0): ReactNode {
    if (n.menu_type === "group") {
      return (
        <div key={n.id}>
          <div className="sec" style={depth ? { paddingLeft: depth * 8 } : undefined}><span>{n.label}</span></div>
          {n.children.map((c) => renderNode(c, depth + 1))}
        </div>
      );
    }
    // Notifications live in the top bar (bell), not the sidebar.
    if (n.route_path === "/notifications") return null;
    return (
      <Link key={n.id} href={n.route_path ?? "#"} onClick={onNavigate} title={collapsed ? n.label : undefined} aria-label={n.label} className={isActive(pathname, n.route_path ?? "") ? "on" : ""}>{svg(n.icon ?? "")}<span>{n.label}</span></Link>
    );
  }

  return (
    <aside className={`aerail${collapsed ? " collapsed" : ""}`} data-collapsed={collapsed}>
      <style dangerouslySetInnerHTML={{ __html: RAIL_CSS }} />
      <div className="brand">
        <div className="mark" aria-hidden>⚖</div><b>Aegis</b>
        {!onNavigate && (
          <button type="button" className="tgl" onClick={toggleRail} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed} title={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            <svg className="ic" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/>' }} />
          </button>
        )}
      </div>
      <nav className="nav">
        {(data?.nodes ?? []).map((n) => renderNode(n))}
      </nav>
    </aside>
  );
}

const RAIL_CSS = `
.aerail{--accent-ink:#fff;--sans:var(--font-sans);--ease:cubic-bezier(.22,.8,.2,1);width:244px;flex:none;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;min-height:0;height:100%;font:400 13px/1.5 var(--sans);color:var(--ink);transition:width .28s var(--ease);overflow:visible}
.aerail.collapsed{width:68px}
.dark .aerail{--accent-ink:#0c0f16;--accent-soft:#1f2740}
.aerail .ic{width:18px;height:18px;flex:none;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.aerail .brand{display:flex;align-items:center;gap:10px;height:56px;padding:0 14px 0 16px;flex:none;position:relative}
.aerail .brand .mark{width:32px;height:32px;border-radius:10px;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;font-weight:700;flex:none}
.aerail .brand b{font-size:16px;letter-spacing:-.02em;white-space:nowrap;transition:opacity .18s,transform .28s var(--ease)}
.aerail .tgl{margin-left:auto;width:30px;height:30px;border-radius:8px;display:grid;place-items:center;color:var(--ink-3);border:0;background:none;cursor:pointer;transition:background .15s,color .15s}
.aerail .tgl:hover{background:var(--surface-2);color:var(--ink)}
.aerail .tgl:focus-visible,.aerail .nav a:focus-visible,.aerail.collapsed .brand{padding:0;justify-content:center;flex-direction:column;height:auto;padding:12px 0 6px;gap:8px}
.aerail.collapsed .brand b{display:none}
.aerail.collapsed .tgl{margin:0}
.aerail .nav{padding:4px 10px 8px;display:flex;flex-direction:column;gap:2px;overflow-y:auto;overflow-x:hidden;flex:1;min-height:0}
.aerail .nav .sec{height:34px;display:flex;align-items:flex-end;padding:0 10px 6px;font:600 10.5px/1 var(--sans);letter-spacing:.09em;text-transform:uppercase;color:var(--ink-3);white-space:nowrap;transition:opacity .15s}
.aerail.collapsed .nav .sec{height:14px;padding:0;margin:6px 8px 0;border-top:1px solid var(--border);align-items:center}
.aerail.collapsed .nav .sec span{opacity:0;width:0;overflow:hidden}
.aerail .nav .sec:first-child{height:28px}
.aerail.collapsed .nav>div:first-child>.sec{border-top:0;margin-top:0}
.aerail .nav a{position:relative;display:flex;align-items:center;gap:12px;height:36px;padding:0 10px;border-radius:10px;color:var(--ink-2);font-weight:500;text-decoration:none;cursor:pointer;font-size:13.5px;white-space:nowrap;transition:background .15s,color .15s}
.aerail .nav a span{overflow:hidden;text-overflow:ellipsis;transition:opacity .18s}
.aerail.collapsed .nav a{justify-content:center;padding:0}
.aerail.collapsed .nav a span{opacity:0;width:0}
.aerail .nav a:hover{background:var(--surface-2);color:var(--ink)}
.aerail .nav a.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
.aerail .nav a.on::before{content:'';position:absolute;left:-10px;top:8px;bottom:8px;width:3px;border-radius:0 3px 3px 0;background:var(--accent)}
@keyframes aemenu{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
`;
