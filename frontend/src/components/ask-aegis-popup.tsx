"use client";

// Ask Aegis from anywhere: a round chat button at the bottom-right that opens a ChatGPT-style popup.
// It uses the same Contract Brain answer as the request page's Ask Aegis card; on a contract page
// the question is scoped to that contract, elsewhere to the whole portfolio. The full assistant
// (drafting, redlining, tools) stays on /assistant.

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { ArrowUp, Maximize2, MessageCircle, Sparkles, SquarePen, X } from "lucide-react";
import { brainApi } from "@/lib/endpoints";
import { Markdown } from "@/components/markdown";
import { cn } from "@/lib/utils";

type Msg = { role: "you" | "ai"; text: string; cites?: number };

const CONTRACT_STARTERS = ["What are the key risks here?", "What obligations does this create?", "How does this compare to our standard terms?"];
const STARTERS = ["Which contracts renew in the next 30 days?", "What are our biggest open risks?", "Which obligations are due this month?"];

/** Any page can open the popup (optionally tied to a contract): window.dispatchEvent(new CustomEvent(ASK_AEGIS_EVENT, { detail: { contractId } })). */
export const ASK_AEGIS_EVENT = "aegis:ask";

export function AskAegisPopup() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [pinned, setPinned] = useState<string | undefined>();
  const contractId = pinned ?? /^\/contracts\/([^/]+)/.exec(pathname)?.[1];
  const scopeText = contractId ? "this contract" : "your portfolio";

  const ask = useMutation({
    mutationFn: (question: string) => brainApi.ask({ question, query_scope: contractId ? "contract" : "portfolio", contract_id: contractId }),
    onSuccess: (res) => setMsgs((m) => [...m, { role: "ai", text: res.answer, cites: res.citations?.length ?? 0 }]),
    onError: (e) => setMsgs((m) => [...m, { role: "ai", text: e instanceof Error ? e.message : "Couldn't answer that." }]),
  });

  useEffect(() => {
    const h = (e: Event) => { setPinned((e as CustomEvent<{ contractId?: string | null }>).detail?.contractId ?? undefined); setOpen(true); };
    window.addEventListener(ASK_AEGIS_EVENT, h);
    return () => window.removeEventListener(ASK_AEGIS_EVENT, h);
  }, []);
  // Leaving the page that pinned a contract drops the pin.
  useEffect(() => { setPinned(undefined); }, [pathname]);
  useEffect(() => { endRef.current?.scrollIntoView?.({ block: "end", behavior: "smooth" }); }, [msgs, ask.isPending]);
  useEffect(() => { if (open) inputRef.current?.focus(); }, [open]);
  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [open]);

  // The assistant page is already a full chat.
  if (pathname === "/" || pathname.startsWith("/assistant")) return null;

  function send(text: string) {
    const t = text.trim();
    if (!t || ask.isPending) return;
    setMsgs((m) => [...m, { role: "you", text: t }]);
    setQ("");
    ask.mutate(t);
  }

  return (
    <>
      {open && (
        <section role="dialog" aria-label="Ask Aegis"
          className="fixed bottom-24 right-5 z-[55] flex h-[min(640px,calc(100vh-8rem))] w-[min(420px,calc(100vw-2.5rem))] animate-fade-in flex-col overflow-hidden rounded-2xl border border-slate-200 bg-slate-50 shadow-pop">
          <header className="flex items-center gap-2 border-b border-slate-200 px-4 py-3">
            <span className="grid h-8 w-8 place-items-center rounded-full bg-brand-600 text-white"><Sparkles className="h-4 w-4" aria-hidden /></span>
            <div className="min-w-0 flex-1 leading-tight">
              <div className="text-[14px] font-semibold text-slate-900">Ask Aegis</div>
              <div className="truncate text-[11.5px] text-slate-500">Answers from {scopeText}</div>
            </div>
            <button type="button" aria-label="New chat" title="New chat" disabled={!msgs.length} onClick={() => { setMsgs([]); inputRef.current?.focus(); }}
              className="grid h-8 w-8 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 disabled:opacity-40"><SquarePen className="h-4 w-4" /></button>
            <Link href="/assistant" aria-label="Open the full assistant" title="Open the full assistant" onClick={() => setOpen(false)}
              className="grid h-8 w-8 place-items-center rounded-lg text-slate-500 hover:bg-slate-100"><Maximize2 className="h-4 w-4" /></Link>
            <button type="button" aria-label="Close" title="Close" onClick={() => setOpen(false)}
              className="grid h-8 w-8 place-items-center rounded-lg text-slate-500 hover:bg-slate-100"><X className="h-4 w-4" /></button>
          </header>

          <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4" aria-live="polite">
            {msgs.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
                <span className="grid h-12 w-12 place-items-center rounded-full bg-brand-50 text-brand-700"><Sparkles className="h-6 w-6" aria-hidden /></span>
                <div>
                  <div className="text-[16px] font-semibold text-slate-900">How can I help?</div>
                  <p className="mt-1 text-[12.5px] text-slate-500">Ask about {scopeText}. Answers cite their sources.</p>
                </div>
                <div className="flex w-full flex-col gap-2">
                  {(contractId ? CONTRACT_STARTERS : STARTERS).map((s) => (
                    <button key={s} type="button" onClick={() => send(s)}
                      className="rounded-xl border border-slate-200 bg-slate-100 px-3.5 py-2.5 text-left text-[13px] text-slate-700 transition hover:border-brand-600 hover:bg-slate-50">{s}</button>
                  ))}
                </div>
              </div>
            ) : msgs.map((m, i) => m.role === "you" ? (
              <div key={i} className="flex justify-end">
                <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-slate-200 px-3.5 py-2 text-[13.5px] text-slate-900">{m.text}</div>
              </div>
            ) : (
              <div key={i} className="flex gap-2.5">
                <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-brand-600 text-white"><Sparkles className="h-3.5 w-3.5" aria-hidden /></span>
                <div className="min-w-0 flex-1 text-[13.5px] leading-relaxed text-slate-800">
                  <div className="[&>div]:!text-[13.5px] [&>div]:!leading-relaxed"><Markdown>{m.text}</Markdown></div>
                  {m.cites ? <div className="mt-1 text-[11.5px] text-slate-500">{m.cites} source{m.cites === 1 ? "" : "s"}</div> : null}
                </div>
              </div>
            ))}
            {ask.isPending && (
              <div className="flex items-center gap-2.5" role="status">
                <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-brand-600 text-white"><Sparkles className="h-3.5 w-3.5 animate-pulse" aria-hidden /></span>
                <span className="text-[13px] text-slate-500">Thinking…</span>
              </div>
            )}
            <div ref={endRef} />
          </div>

          <form className="border-t border-slate-200 p-3" onSubmit={(e) => { e.preventDefault(); send(q); }}>
            <div className="flex items-end gap-2 rounded-2xl border border-slate-300 bg-slate-100 py-1.5 pl-3.5 pr-1.5 focus-within:border-brand-600 focus-within:ring-2 focus-within:ring-brand-500/30">
              <textarea ref={inputRef} rows={1} value={q} aria-label="Message Aegis" placeholder="Message Aegis…" onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(q); } }}
                className="max-h-32 min-h-[28px] flex-1 resize-none bg-transparent py-1 text-[13.5px] text-slate-900 outline-none placeholder:text-slate-400" />
              <button type="submit" aria-label="Send" disabled={!q.trim() || ask.isPending}
                className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-brand-600 text-white transition hover:bg-brand-700 disabled:bg-slate-300"><ArrowUp className="h-4 w-4" /></button>
            </div>
            <p className="mt-1.5 text-center text-[11px] text-slate-500">Aegis can make mistakes. Check the sources.</p>
          </form>
        </section>
      )}
      <button type="button" aria-label={open ? "Close Ask Aegis" : "Ask Aegis"} aria-expanded={open} title="Ask Aegis" onClick={() => setOpen((o) => !o)}
        className={cn("fixed bottom-5 right-5 z-[55] grid h-14 w-14 place-items-center rounded-full bg-brand-600 text-white shadow-pop transition hover:scale-105 hover:bg-brand-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600",
          open && "rotate-90")}>
        {open ? <X className="h-6 w-6" aria-hidden /> : <MessageCircle className="h-6 w-6" aria-hidden />}
      </button>
    </>
  );
}
