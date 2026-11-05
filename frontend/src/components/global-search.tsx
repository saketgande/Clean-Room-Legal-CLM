"use client";

// The header search field: type to find requests and contracts, Enter or click to open one.
// ⌘K / Ctrl+K focuses it from anywhere. Contracts come from the search API (access-checked on the
// server); requests are matched in the browser against the requests the person can already see.

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { FileText, Inbox, Search } from "lucide-react";
import { intakeApi, searchApi } from "@/lib/endpoints";
import { contractDisplayName, titleCase } from "@/lib/utils";
import { cn } from "@/lib/utils";

type Hit = { key: string; href: string; title: string; sub: string; kind: "request" | "contract" };

export function GlobalSearch() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => setQ(text.trim()), 200);
    return () => clearTimeout(t);
  }, [text]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); inputRef.current?.focus(); setOpen(true); }
    };
    const onDown = (e: MouseEvent) => { if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => { window.removeEventListener("keydown", onKey); document.removeEventListener("mousedown", onDown); };
  }, []);

  const enabled = q.length >= 2;
  const { data: contracts, isFetching } = useQuery({
    queryKey: ["global-search-contracts", q],
    queryFn: () => searchApi.contracts({ q, limit: 6 }),
    enabled,
    staleTime: 30_000,
    retry: false,
  });
  // A person who cannot see requests simply gets no request results.
  const { data: requests } = useQuery({ queryKey: ["intake-requests-all"], queryFn: () => intakeApi.list(), enabled, staleTime: 60_000, retry: false });

  const hits = useMemo<Hit[]>(() => {
    if (!enabled) return [];
    const needle = q.toLowerCase();
    const reqs = (requests ?? [])
      .filter((r) => `${r.ref} ${r.subject ?? ""} ${r.type_label} ${r.requester_name ?? ""}`.toLowerCase().includes(needle))
      .slice(0, 5)
      .map<Hit>((r) => ({ key: `r-${r.id}`, href: `/intake?open=${r.id}`, title: r.subject || r.type_label, sub: `${r.ref} · ${r.type_label}`, kind: "request" }));
    const cons = (contracts ?? []).slice(0, 6).map<Hit>((c) => ({
      key: `c-${c.id}`, href: `/contracts/${c.id}`, title: contractDisplayName(c),
      sub: [c.counterparty_name, c.contract_type ? titleCase(c.contract_type) : null].filter(Boolean).join(" · ") || "Contract", kind: "contract",
    }));
    return [...reqs, ...cons];
  }, [enabled, q, requests, contracts]);

  const go = (h: Hit) => { setOpen(false); setText(""); setQ(""); router.push(h.href); };
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { setOpen(false); inputRef.current?.blur(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(i + 1, hits.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
    else if (e.key === "Enter" && hits[active]) { e.preventDefault(); go(hits[active]); }
  };

  const groups: { label: string; kind: Hit["kind"] }[] = [{ label: "Requests", kind: "request" }, { label: "Contracts", kind: "contract" }];
  return (
    <div ref={boxRef} className="relative w-full max-w-[420px]">
      <div className="flex h-10 items-center gap-2.5 rounded-xl bg-slate-50 px-3.5 text-[13px] ring-1 ring-slate-200 transition focus-within:bg-slate-100 focus-within:ring-1 focus-within:ring-slate-300">
        <Search className="h-4 w-4 shrink-0 text-slate-400" aria-hidden />
        <input
          ref={inputRef}
          value={text}
          onChange={(e) => { setText(e.target.value); setOpen(true); setActive(0); }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          role="combobox"
          aria-expanded={open && enabled}
          aria-controls="global-search-list"
          aria-label="Search requests and contracts"
          // The unlayered global :focus-visible outline beats utility classes; the wrapper draws the focus ring instead.
          style={{ outline: "none" }}
          placeholder="Search requests, contracts, counterparties…"
          className="h-full min-w-0 flex-1 appearance-none border-0 bg-transparent p-0 text-[14px] text-slate-900 shadow-none outline-none ring-0 placeholder:text-slate-400 focus:outline-none focus:ring-0 focus-visible:outline-none"
        />
        <kbd className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-500 ring-1 ring-slate-200">⌘ K</kbd>
      </div>
      {open && enabled && (
        <div id="global-search-list" role="listbox" className="absolute left-0 right-0 top-12 z-50 max-h-[420px] overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-1.5 shadow-xl">
          {hits.length === 0 ? (
            <p className="px-3 py-4 text-[13px] text-slate-500">{isFetching ? "Searching…" : `Nothing found for “${q}”.`}</p>
          ) : groups.map((g) => {
            const list = hits.filter((h) => h.kind === g.kind);
            if (!list.length) return null;
            return (
              <div key={g.kind}>
                <div className="px-3 pb-1 pt-2 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">{g.label}</div>
                {list.map((h) => {
                  const i = hits.indexOf(h);
                  const Icon = h.kind === "request" ? Inbox : FileText;
                  return (
                    <button key={h.key} type="button" role="option" aria-selected={i === active} onMouseEnter={() => setActive(i)} onClick={() => go(h)}
                      className={cn("flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left", i === active ? "bg-slate-100" : "")}>
                      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-brand-50 text-brand-600"><Icon className="h-4 w-4" aria-hidden /></span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] font-medium text-slate-900">{h.title}</span>
                        <span className="block truncate text-[11.5px] text-slate-500">{h.sub}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
