"use client";

// The signed-in person in the top bar: name and role, with a menu to switch role and sign out.
// (It used to sit at the foot of the sidebar.)

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, LogOut } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { cn, initials } from "@/lib/utils";

export function AccountMenu({ compact = false }: { compact?: boolean }) {
  const { user, logout, switchRole } = useAuth();
  const [open, setOpen] = useState(false);
  const [switching, setSwitching] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", down); document.removeEventListener("keydown", key); };
  }, [open]);
  const name = user?.full_name ?? user?.email ?? "You";
  const role = user?.active_role_name ?? "Legal";
  const roles = user?.roles ?? [];

  async function pickRole(r: string) {
    if (r === user?.active_role_name || switching) return;
    setSwitching(r);
    try {
      await switchRole(undefined, r);
      // The access token carries the active role, so reload to pick up a fresh token and role-scoped menu.
      window.location.reload();
    } catch {
      setSwitching(null);
    }
  }

  return (
    <div className="relative" ref={ref}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open} title={user?.email ?? "Account"}
        className="flex items-center gap-2.5 rounded-xl py-1 pl-1 pr-2 text-left transition-colors hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600">
        <span className="grid h-9 w-9 place-items-center rounded-full bg-slate-900 text-[13px] font-semibold text-white" aria-hidden>{initials(name)}</span>
        {compact ? null : (
          <span className="leading-tight">
            <span className="block text-[13px] font-semibold text-slate-900">{name}</span>
            <span className="block text-[11.5px] text-slate-500">{role}</span>
          </span>
        )}
        <ChevronDown className={cn("h-4 w-4 text-slate-400 transition-transform", open && "rotate-180")} aria-hidden />
      </button>
      {open && (
        <div role="menu" aria-label="Account" className="absolute right-0 top-[calc(100%+6px)] z-50 w-[240px] rounded-xl border border-slate-200 bg-slate-50 p-1.5 shadow-pop">
          <div className="truncate px-2.5 py-1.5 text-[11.5px] text-slate-500">{user?.email}</div>
          {roles.length > 0 && (
            <>
              <div className="px-2.5 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">Switch role</div>
              {roles.map((r) => (
                <button key={r} type="button" role="menuitemradio" aria-checked={r === user?.active_role_name} disabled={!!switching} onClick={() => void pickRole(r)}
                  className={cn("flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-[13px] hover:bg-slate-100 disabled:opacity-60",
                    r === user?.active_role_name ? "font-semibold text-brand-700" : "text-slate-700")}>
                  <span>{r}</span>
                  {r === user?.active_role_name ? <Check className="h-4 w-4" aria-hidden /> : switching === r ? <span>…</span> : null}
                </button>
              ))}
              <div className="my-1 h-px bg-slate-200" />
            </>
          )}
          <button type="button" role="menuitem" onClick={() => { setOpen(false); void logout(); }}
            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] text-danger hover:bg-slate-100">
            <LogOut className="h-4 w-4" aria-hidden />Sign out
          </button>
        </div>
      )}
    </div>
  );
}
