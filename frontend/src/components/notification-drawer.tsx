"use client";

// Right-hand notifications drawer, opened from the top-bar bell. Same data and
// read-state calls as /notifications; the full page stays for the complete list.

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, Bell, BellOff, CheckCheck, CheckCircle2, Clock, Inbox, PenLine, RefreshCw, UserCheck, X, XCircle,
  type LucideIcon,
} from "lucide-react";
import { notificationsApi } from "@/lib/endpoints";
import { eventLabel } from "@/lib/event-label";
import { cn, fmtRelative, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { Notification } from "@/lib/types";

const CLOSE_MS = 260;

// One icon + colour family per kind of event, so the list can be scanned by shape.
function kindOf(eventType: string): { Icon: LucideIcon; chip: string; label: string } {
  const t = eventType.toLowerCase();
  if (/fail|error|reject|declin/.test(t)) return { Icon: XCircle, chip: "bg-danger-subtle text-danger", label: "Problem" };
  if (/sla|overdue|breach|deadline|expir|due/.test(t)) return { Icon: AlertTriangle, chip: "bg-warning-subtle text-warning", label: "Deadline" };
  if (/approv/.test(t)) return { Icon: CheckCircle2, chip: "bg-success-subtle text-success", label: "Approval" };
  if (/(^|[._\s-])e?sign|signature|docusign/.test(t)) return { Icon: PenLine, chip: "bg-brand-50 text-brand-700", label: "Signature" };
  if (/renew/.test(t)) return { Icon: RefreshCw, chip: "bg-warning-subtle text-warning", label: "Renewal" };
  if (/assign|workflow|step|task/.test(t)) return { Icon: UserCheck, chip: "bg-info-subtle text-info", label: "Work" };
  if (/intake|request|triage/.test(t)) return { Icon: Inbox, chip: "bg-brand-50 text-brand-700", label: "Request" };
  if (/remind|late/.test(t)) return { Icon: Clock, chip: "bg-warning-subtle text-warning", label: "Reminder" };
  return { Icon: Bell, chip: "bg-slate-50 text-slate-600", label: "Update" };
}

// Email bodies are stored as HTML; show them as plain text (never render the markup).
function plainText(html: string): string {
  return html
    .replace(/<\/(p|div|li|h\d)>/gi, " ")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function dayGroup(iso: string): string {
  const d = new Date(iso); const now = new Date();
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(now) - start(d)) / 86_400_000);
  if (diff <= 0) return "Today";
  if (diff === 1) return "Yesterday";
  if (diff < 7) return "This week";
  return "Earlier";
}

export function NotificationDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  // `mounted` keeps the panel in the DOM while it slides out; `shown` drives the CSS transition.
  const [mounted, setMounted] = useState(false);
  const [shown, setShown] = useState(false);
  const [tab, setTab] = useState<"all" | "unread">("all");
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (open) {
      setMounted(true);
      const id = requestAnimationFrame(() => setShown(true));
      return () => cancelAnimationFrame(id);
    }
    setShown(false);
    const t = setTimeout(() => setMounted(false), CLOSE_MS);
    return () => clearTimeout(t);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["notifications"], queryFn: notificationsApi.list, enabled: open, staleTime: 15_000,
  });
  const all = useMemo(() => data ?? [], [data]);
  const unread = all.filter((n) => !n.read_at).length;
  const list = tab === "unread" ? all.filter((n) => !n.read_at) : all;
  const groups = useMemo(() => {
    const m = new Map<string, Notification[]>();
    list.forEach((n) => { const g = dayGroup(n.created_at); m.set(g, [...(m.get(g) ?? []), n]); });
    return [...m.entries()];
  }, [list]);

  function refresh() {
    qc.invalidateQueries({ queryKey: ["notifications"] });
    qc.invalidateQueries({ queryKey: ["notifications", "unread-count"] });
  }
  async function markRead(n: Notification) {
    if (n.read_at) return;
    qc.setQueryData<Notification[]>(["notifications"], (old) => (old ?? []).map((x) => (x.id === n.id ? { ...x, read_at: new Date().toISOString() } : x)));
    try { await notificationsApi.markRead(n.id); } catch (e) { notify(e instanceof Error ? e.message : "Failed to mark read", "error"); }
    refresh();
  }
  async function markAll() {
    try { await notificationsApi.markAllRead(); refresh(); } catch (e) { notify(e instanceof Error ? e.message : "Failed", "error"); }
  }

  if (!mounted) return null;
  let idx = 0;
  return (
    <div className="fixed inset-0 z-[70]" role="dialog" aria-modal="true" aria-label="Notifications">
      <div
        className={cn("absolute inset-0 bg-slate-950/35 backdrop-blur-[2px] transition-opacity duration-[260ms] ease-out", shown ? "opacity-100" : "opacity-0")}
        onClick={onClose}
      />
      <aside
        className={cn(
          "absolute right-0 top-0 flex h-full w-full max-w-[440px] flex-col border-l border-slate-200 bg-slate-50 shadow-modal",
          "transition-transform duration-[260ms] ease-[cubic-bezier(.22,.8,.2,1)]",
          shown ? "translate-x-0" : "translate-x-full",
        )}
      >
        <header className="shrink-0 border-b border-slate-200 bg-slate-100 px-5 pb-0 pt-4">
          <div className="flex items-center gap-3">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-brand-600 text-white shadow-sm"><Bell className="h-[18px] w-[18px]" /></span>
            <div className="min-w-0 flex-1">
              <h2 className="text-[16px] font-bold tracking-[-0.02em] text-slate-900">Notifications</h2>
              <p className="text-[12px] text-slate-500">{unread > 0 ? `${unread} unread` : "You're all caught up"}</p>
            </div>
            {unread > 0 && (
              <button type="button" onClick={markAll} className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12px] font-semibold text-brand-700 transition-colors hover:bg-brand-50">
                <CheckCheck className="h-3.5 w-3.5" />Mark all read
              </button>
            )}
            <button ref={closeRef} type="button" onClick={onClose} aria-label="Close notifications" className="grid h-8 w-8 place-items-center rounded-lg text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-900">
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="mt-3 flex gap-5" role="tablist">
            {([["all", "All", all.length], ["unread", "Unread", unread]] as const).map(([k, label, n]) => (
              <button
                key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                className={cn("-mb-px inline-flex items-center gap-2 border-b-2 pb-2.5 text-[13px] transition-colors", tab === k ? "border-brand-600 font-semibold text-slate-900" : "border-transparent font-medium text-slate-500 hover:text-slate-800")}
              >
                {label}
                <span className={cn("rounded-full px-2 py-px text-[11px] font-bold", tab === k ? "bg-brand-600 text-white" : "bg-slate-50 text-slate-600")}>{n}</span>
              </button>
            ))}
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          {isLoading ? (
            <div className="space-y-3" aria-busy="true">{[0, 1, 2, 3].map((i) => <div key={i} className="h-[88px] animate-pulse rounded-2xl bg-slate-100" />)}</div>
          ) : error ? (
            <p role="alert" className="rounded-xl bg-danger-subtle px-4 py-3 text-[13px] text-danger">Couldn't load notifications. Close this and try again.</p>
          ) : list.length === 0 ? (
            <div className="mt-16 flex flex-col items-center gap-3 text-center">
              <span className="grid h-14 w-14 place-items-center rounded-2xl bg-brand-50 text-brand-600"><BellOff className="h-6 w-6" /></span>
              <p className="text-[14px] font-semibold text-slate-900">{tab === "unread" ? "Nothing unread" : "No notifications yet"}</p>
              <p className="max-w-[28ch] text-[12.5px] text-slate-500">Approvals, reminders and signature updates will show up here.</p>
            </div>
          ) : (
            groups.map(([group, items]) => (
              <section key={group} className="mb-5">
                <h3 className="mb-2 px-1 text-[11px] font-bold uppercase tracking-[0.08em] text-slate-500">{group}</h3>
                <ul className="space-y-2.5">
                  {items.map((n) => {
                    const k = kindOf(n.event_type);
                    const isUnread = !n.read_at;
                    const failed = /fail|error/.test(n.status.toLowerCase());
                    return (
                      <li key={n.id} className="nd-card" style={{ animationDelay: `${Math.min(idx++, 10) * 35 + 80}ms` }}>
                        <button
                          type="button" onClick={() => markRead(n)}
                          className={cn(
                            "group flex w-full gap-3 rounded-2xl border p-3.5 text-left transition-all hover:-translate-y-px hover:shadow-pop focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40",
                            isUnread ? "border-brand-200 bg-brand-50/60" : "border-slate-200 bg-slate-100",
                          )}
                        >
                          <span className={cn("grid h-10 w-10 shrink-0 place-items-center rounded-xl", k.chip)}><k.Icon className="h-[19px] w-[19px]" /></span>
                          <span className="min-w-0 flex-1">
                            <span className="flex items-start gap-2">
                              <span className={cn("min-w-0 flex-1 text-[13.5px] leading-snug tracking-[-0.005em] text-slate-900", isUnread ? "font-semibold" : "font-medium")}>{plainText(n.subject ?? "") || eventLabel(n.event_type)}</span>
                              {isUnread && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-brand-600" aria-label="Unread" />}
                            </span>
                            {plainText(n.body ?? "") && <span className="mt-1 line-clamp-2 block text-[12.5px] leading-[1.45] text-slate-600">{plainText(n.body ?? "")}</span>}
                            <span className="mt-2 flex flex-wrap items-center gap-1.5">
                              <span className={cn("rounded-md px-1.5 py-px text-[10.5px] font-bold", k.chip)}>{k.label}</span>
                              <span className="rounded-md bg-slate-50 px-1.5 py-px text-[10.5px] font-semibold text-slate-600">{titleCase(n.channel)}</span>
                              {failed && <span className="rounded-md bg-danger-subtle px-1.5 py-px text-[10.5px] font-semibold text-danger">{titleCase(n.status)}</span>}
                              <span className="ml-auto text-[11.5px] tabular-nums text-slate-500">{fmtRelative(n.created_at)}</span>
                            </span>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))
          )}
        </div>

        <footer className="shrink-0 border-t border-slate-200 bg-slate-100 px-5 py-3">
          <Link href="/notifications" onClick={onClose} className="flex h-9 items-center justify-center rounded-lg border border-slate-300 text-[13px] font-semibold text-slate-800 transition-colors hover:bg-slate-50">
            Open full notifications page
          </Link>
        </footer>
      </aside>
    </div>
  );
}
