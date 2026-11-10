"use client";

// Ask Aegis scope: the whole portfolio, or only the requests you pick. A chosen request is asked about
// through its linked contract (when it has one) and named in the message, so the answer stays on it.

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, FileText, Globe2, Layers, ListChecks, Search } from "lucide-react";
import { intakeApi } from "@/lib/endpoints";
import { cn } from "@/lib/utils";
import type { IntakeRequest } from "@/lib/types";

const MARK = "[[scope: only these requests (use their linked contracts): ";

export const scopeContractIds = (reqs: IntakeRequest[]): string[] =>
  [...new Set(reqs.map((r) => r.contract_id).filter((x): x is string => !!x))];

/** The line put in front of the question so the model stays on the chosen requests. */
export const scopePrefix = (reqs: IntakeRequest[]): string =>
  reqs.length ? `${MARK}${reqs.map((r) => `${r.ref} “${(r.subject || r.type_label).slice(0, 80)}”`).join("; ")}]]\n\n` : "";

/** Splits a stored user message back into the scope line and what the person typed. */
export function splitScope(text: string): { scope: string | null; body: string } {
  if (!text.startsWith(MARK)) return { scope: null, body: text };
  const end = text.indexOf("]]\n\n");
  if (end < 0) return { scope: null, body: text };
  return { scope: text.slice(MARK.length, end), body: text.slice(end + 4) };
}

export function ScopePicker({ value, onChange, disabled, up }: { value: IntakeRequest[]; onChange: (next: IntakeRequest[]) => void; disabled?: boolean; up?: boolean }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"all" | "pick">(value.length ? "pick" : "all");
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", down); document.removeEventListener("keydown", key); };
  }, [open]);

  // A person who cannot see requests just gets an empty list.
  const { data } = useQuery({ queryKey: ["intake-requests-all"], queryFn: () => intakeApi.list(), enabled: open, staleTime: 60_000, retry: false });
  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (data ?? []).filter((r) => !needle || `${r.ref} ${r.subject ?? ""} ${r.type_label}`.toLowerCase().includes(needle)).slice(0, 60);
  }, [data, q]);
  const picked = new Set(value.map((r) => r.id));
  const toggle = (r: IntakeRequest) => onChange(picked.has(r.id) ? value.filter((x) => x.id !== r.id) : [...value, r]);

  const label = value.length === 0 ? "Whole portfolio" : value.length === 1 ? value[0].ref : `${value.length} requests`;
  const choose = (m: "all" | "pick") => { setMode(m); if (m === "all") onChange([]); };

  return (
    <div ref={ref} className="relative">
      <button type="button" disabled={disabled} onClick={() => setOpen((o) => !o)} aria-haspopup="dialog" aria-expanded={open}
        className={cn("aa-tool", value.length > 0 && "aa-tool-on")}>
        {value.length ? <ListChecks className="h-4 w-4" /> : <Layers className="h-4 w-4" />}
        {label}
        <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div role="dialog" aria-label="Choose what to ask about"
          className={cn("aa-pop absolute left-0 z-50 w-[380px] max-w-[88vw] rounded-2xl border border-slate-200 bg-slate-50 p-3 shadow-2xl", up ? "bottom-full mb-2" : "top-full mt-2")}>
          <p className="px-1 pb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-400">Ask about</p>
          <div className="grid gap-2">
            {([
              { m: "all" as const, I: Globe2, t: "Whole portfolio", d: "Every contract you can access" },
              { m: "pick" as const, I: ListChecks, t: "Chosen requests", d: "Only the requests you pick below" },
            ]).map((o) => (
              <button key={o.m} type="button" onClick={() => choose(o.m)}
                className={cn("flex items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition", mode === o.m ? "border-brand-500 bg-brand-50" : "border-slate-200 bg-slate-100 hover:border-slate-300")}>
                <span className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg", mode === o.m ? "bg-brand-600 text-white" : "bg-slate-200 text-slate-500")}><o.I className="h-4 w-4" /></span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[13px] font-semibold text-slate-900">{o.t}</span>
                  <span className="block text-[11.5px] text-slate-500">{o.d}</span>
                </span>
                {mode === o.m && <Check className="h-4 w-4 text-brand-600" />}
              </button>
            ))}
          </div>

          {mode === "pick" && (
            <div className="mt-3">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by reference or subject" style={{ outline: "none" }}
                  className="h-9 w-full rounded-xl border border-slate-200 bg-slate-100 pl-9 pr-3 text-[13px] text-slate-800 outline-none placeholder:text-slate-400 focus:border-brand-400" />
              </div>
              <ul className="mt-2 max-h-[176px] space-y-0.5 overflow-y-auto pr-0.5">
                {list.length === 0 && <li className="px-2 py-4 text-center text-[12.5px] text-slate-400">{data ? "No requests match." : "Loading requests…"}</li>}
                {list.map((r) => {
                  const on = picked.has(r.id);
                  return (
                    <li key={r.id}>
                      <button type="button" onClick={() => toggle(r)} className={cn("flex w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left transition", on ? "bg-brand-50" : "hover:bg-slate-100")}>
                        <span className={cn("flex h-5 w-5 shrink-0 items-center justify-center rounded-md border", on ? "border-brand-600 bg-brand-600 text-white" : "border-slate-300")}>{on && <Check className="h-3.5 w-3.5" />}</span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13px] font-medium text-slate-800">{r.subject || r.type_label}</span>
                          <span className="block truncate text-[11.5px] text-slate-400">{r.ref} · {r.type_label}</span>
                        </span>
                        {r.contract_id && <span title="Has a contract" className="shrink-0 text-slate-400"><FileText className="h-3.5 w-3.5" /></span>}
                      </button>
                    </li>
                  );
                })}
              </ul>
              <p className="mt-2 px-1 text-[11px] text-slate-400">The document icon marks requests that already have a contract. Others are asked about by their details.</p>
            </div>
          )}
          <div className="mt-3 flex items-center gap-2">
            {value.length > 0 && <button type="button" onClick={() => onChange([])} className="text-[12.5px] font-medium text-slate-500 hover:text-slate-800">Clear</button>}
            <button type="button" onClick={() => { if (mode === "pick" && value.length === 0) setMode("all"); setOpen(false); }} className="aa-go ml-auto h-9">Done</button>
          </div>
        </div>
      )}
    </div>
  );
}
