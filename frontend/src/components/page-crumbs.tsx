"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { menuApi } from "@/lib/endpoints";
import type { MenuNode } from "@/lib/types";

/** Section › page for the current route, from the same menu tree the sidebar draws. */
function useCrumbs(pathname: string): { section: string | null; page: string | null; href: string | null } {
  const { data } = useQuery({ queryKey: ["menu-tree"], queryFn: () => menuApi.tree(), staleTime: 60_000 });
  let best: { section: string | null; page: string; href: string } | null = null;
  const walk = (nodes: MenuNode[], section: string | null) => {
    for (const n of nodes) {
      if (n.menu_type === "group") walk(n.children, n.label);
      else if (n.route_path && (pathname === n.route_path || (n.route_path !== "/" && pathname.startsWith(n.route_path + "/")))) {
        if (!best || n.route_path.length > best.href.length) best = { section, page: n.label, href: n.route_path };
      }
    }
  };
  walk(data?.nodes ?? [], null);
  const b = best as { section: string | null; page: string; href: string } | null;
  return { section: b?.section ?? null, page: b?.page ?? null, href: b?.href ?? null };
}

/** The breadcrumb, shown inside a page's own heading (under its title), not in the app's top bar. */
export function PageCrumbs({ className }: { className?: string }) {
  const pathname = usePathname();
  const c = useCrumbs(pathname);
  if (!c.page) return null;
  return (
    <nav aria-label="Breadcrumb" className={className ?? "flex min-w-0 items-center gap-1.5 text-[12.5px] text-slate-500"}>
      {c.section ? <><span>{c.section}</span><ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden /></> : null}
      {pathname === c.href
        ? <span aria-current="page" className="truncate font-medium text-slate-900">{c.page}</span>
        : <><Link href={c.href ?? "/"} className="truncate hover:text-slate-900">{c.page}</Link><ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden /><span aria-current="page" className="font-medium text-slate-900">Details</span></>}
    </nav>
  );
}
