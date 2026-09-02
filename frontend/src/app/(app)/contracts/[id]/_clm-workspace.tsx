"use client";

// CLM Editor — Screen 4 ported: a focused, AI-first drafting/review workspace.
// Own full-screen shell (sidebar + editor + agent panel), scoped under `.clm`.
// Wired to the real contract + the workflow engine: the task banner comes from
// the WorkflowRun driving this contract, Review comes from the playbook deviations,
// and the Assistant is the contract-scoped Ask-Aegis. The centre reuses the real
// ContractDocument editor. Old lifecycle panels intentionally dropped.

import { useState } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { contractsApi, workflowsApi, brainApi } from "@/lib/endpoints";
import { ContractDocument } from "@/components/contract-document";
import { CenterSpinner, ErrorState, NotFound } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { initials } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { ContractDeviation } from "@/lib/types";

const svg = (p: string) => <svg className="ic" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: p }} />;

const NAV = [
  { label: "Legal Intake", href: "/intake", icon: '<path d="M3 7l9 6 9-6"/><rect x="3" y="5" width="18" height="14" rx="2"/>' },
  { label: "My Work", href: "/my-work", icon: '<path d="M9 11l3 3 8-8"/><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"/>' },
  { label: "Contracts", href: "/contracts", icon: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>', on: true },
  { label: "Search", href: "/search", icon: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>' },
];
const NAV2 = [
  { label: "Approvals", href: "/approvals", icon: '<path d="M9 11l3 3 8-8"/>' },
  { label: "Signatures", href: "/signatures", icon: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>' },
  { label: "Obligations", href: "/obligations", icon: '<path d="M12 8v4l3 2"/><circle cx="12" cy="12" r="9"/>' },
];

const SEV = (s: string) => { const v = (s || "").toLowerCase(); return v === "critical" || v === "high" ? "high" : v === "their_change" ? "ext" : v; };

export function ClmWorkspace({ id }: { id: string }) {
  const { user } = useAuth();
  const { notify } = useToast();
  const { data: contract, isLoading: contractLoading, error: contractError } = useQuery({ queryKey: ["contract", id], queryFn: () => contractsApi.get(id) });
  const { data: edits } = useQuery({ queryKey: ["contract-edits", id], queryFn: () => contractsApi.edits(id) });
  const { data: deviations } = useQuery({ queryKey: ["contract-devs", id], queryFn: () => contractsApi.deviations(id) });
  const { data: risk } = useQuery({ queryKey: ["contract-risk", id], queryFn: () => contractsApi.risk(id) });
  const { data: run } = useQuery({ queryKey: ["flow-run-contract", id], queryFn: () => workflowsApi.runForContract(id) });

  const [tab, setTab] = useState<"assistant" | "review" | "sources">("assistant");
  const [chat, setChat] = useState<{ role: "you" | "agent"; text: string }[]>([]);
  const [q, setQ] = useState("");
  const [track, setTrack] = useState(true);
  const [activeEditId, setActiveEditId] = useState<string | null>(null);

  const qc = useQueryClient();
  const ask = useMutation({
    mutationFn: (question: string) => brainApi.ask({ question, query_scope: "contract", contract_id: id }),
    onSuccess: (res) => setChat((c) => [...c, { role: "agent", text: res.answer }]),
    onError: (e) => setChat((c) => [...c, { role: "agent", text: e instanceof Error ? e.message : "Couldn't answer that." }]),
  });
  function send(text: string) { const t = text.trim(); if (!t || ask.isPending || redline.isPending) return; setChat((c) => [...c, { role: "you", text: t }]); setQ(""); ask.mutate(t); }

  // Real tracked-change generation (not Q&A): the assistant's edit_contract
  // engine turns a plain-language instruction into anchored ContractEdits.
  const redline = useMutation({
    mutationFn: (instructions: string) => contractsApi.aiRedline(id, instructions),
    onSuccess: (res) => {
      setChat((c) => [...c, { role: "agent", text: `Drafted ${res.edits} tracked change${res.edits === 1 ? "" : "s"} in the document.${res.summary ? ` ${res.summary}` : ""}` }]);
      qc.invalidateQueries({ queryKey: ["contract-edits", id] });
    },
    onError: (e) => setChat((c) => [...c, { role: "agent", text: e instanceof Error ? e.message : "Couldn't draft the redline." }]),
  });
  const busy = ask.isPending || redline.isPending;

  const devs: ContractDeviation[] = deviations ?? [];
  const openDevs = devs.filter((d) => (d.status || "open") === "open");
  const resolved = devs.length - openDevs.length;
  // Breakdown must match openDevs (the issues we count above), not risk.counts
  // (the per-clause risk distribution) — otherwise "N issues" and "X high · …"
  // disagree. SEV folds critical into "high".
  const devHigh = openDevs.filter((d) => SEV(d.severity) === "high").length;
  const devMedium = openDevs.filter((d) => SEV(d.severity) === "medium").length;
  const devLow = openDevs.filter((d) => SEV(d.severity) === "low").length;
  const reqId = run?.request_id ?? null;
  const nextStep = run && !["complete", "failed", "cancelled"].includes(run.status) ? run.steps?.[run.current_index]?.name : null;
  const editCount = (edits ?? []).length;
  const cpty = contract?.counterparty_name;
  const bandTone = risk?.band === "high" ? "crit" : risk?.band === "medium" ? "warn" : risk?.band === "low" ? "good" : "ink-3";

  function toggleTheme() {
    const el = document.documentElement;
    el.classList.toggle("dark");
  }
  function devInstruction(d: ContractDeviation): string {
    const quote = (d.citation as { quote?: string } | null)?.quote;
    return `Fix the ${(d.clause_type || "clause").replace(/_/g, " ")} issue as a tracked change. Issue: ${d.issue}${d.suggested_fix ? ` Required change: ${d.suggested_fix}` : ""}${quote ? ` The existing clause reads: "${quote}"` : ""}`;
  }
  function draftFix(d: ContractDeviation) {
    if (busy) return;
    setTab("assistant");
    setChat((c) => [...c, { role: "you", text: `Draft a fix for the ${(d.clause_type || "clause").replace(/_/g, " ")} issue.` }]);
    redline.mutate(devInstruction(d));
  }
  function draftAllFixes() {
    if (busy || openDevs.length === 0) return;
    setTab("assistant");
    setChat((c) => [...c, { role: "you", text: `Draft fixes for all ${openDevs.length} open issues.` }]);
    redline.mutate(`Apply tracked-change fixes for the following playbook issues:\n${openDevs.map((d, i) => `${i + 1}. ${devInstruction(d)}`).join("\n")}`);
  }

  if (contractLoading && !contract) return <CenterSpinner label="Loading contract…" />;
  if (contractError) return <ErrorState error={contractError} />;
  if (!contract)
    return (
      <div className="p-6">
        <NotFound
          title="Contract not found"
          description="This contract may have been deleted, or you may not have access to it."
          backHref="/contracts"
          backLabel="Back to contracts"
        />
      </div>
    );

  return (
    <div className="clm">
      <style dangerouslySetInnerHTML={{ __html: CLM_CSS }} />
      <div className="app">
        {/* main (the shared AegisRail sidebar is provided by AppShell) */}
        <div className="main">
          <div className="hd">
            <div className="crumb"><Link href={reqId ? `/intake?open=${reqId}` : "/contracts"}>← {reqId ? `Request${cpty ? ` · ${cpty}` : ""}` : "Contracts"}</Link><span>/</span><span>CLM editor</span></div>
            <div className="hrow">
              <h1>{contract?.title ?? "Contract"}</h1>
              <span className="ref">{(contract?.lifecycle_stage ?? "draft").replace(/_/g, " ")}</span>
              <div style={{ flex: 1 }} />
              <button className="btn" onClick={() => notify("Draft saved", "success")}>{svg('<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/>')}Save</button>
              <button className="iconbtn" title="Theme" onClick={toggleTheme}>{svg('<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>')}</button>
            </div>

            {/* task banner — from the workflow engine */}
            {run && (
              <div className={`ctx${openDevs.length === 0 ? " done" : ""}`}>
                <div className="cx-ic">{svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>')}</div>
                <div className="cx-t"><div className="k">{reqId ? "From the request · your task" : "Workflow task"}</div>
                  <div>{openDevs.length === 0 ? <><b>No open issues</b> — this draft is clear to move on{nextStep ? ` to ${nextStep}` : ""}.</> : <><b>Clear {openDevs.length} issue{openDevs.length === 1 ? "" : "s"}</b> before this draft returns to {nextStep ?? "review"}. The agent reviewed it.</>}</div></div>
                {devs.length > 0 ? <div className="prog"><span className="dim" style={{ fontSize: 11 }}>Resolved</span><div className="track"><div className="fill" style={{ width: `${devs.length ? Math.round((resolved / devs.length) * 100) : 0}%` }} /></div><span className="pn">{resolved} / {devs.length}</span></div> : <div className="prog" />}
                <div className="cx-act"><button className="btn sm" onClick={() => setTab("review")}>Open review ({openDevs.length})</button></div>
              </div>
            )}

            {/* format toolbar */}
            <div className="fmt">
              <div className="sel">Heading 2 {svg('<path d="M6 9l6 6 6-6"/>')}</div>
              <div className="fdiv" />
              <button className="fb" title="Bold" style={{ fontFamily: "var(--serif)" }}>B</button>
              <button className="fb" title="Italic" style={{ fontStyle: "italic", fontFamily: "var(--serif)" }}>I</button>
              <button className="fb" title="Underline" style={{ textDecoration: "underline" }}>U</button>
              <div className="fdiv" />
              <button className="fb" title="Bulleted list">{svg('<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>')}</button>
              <button className="fb" title="Numbered list">{svg('<path d="M10 6h11M10 12h11M10 18h11M4 6h1v4M4 10h2M6 14H4v2h2v2H4"/>')}</button>
              <div className="fdiv" />
              <button className={`tog${track ? " on" : ""}`} onClick={() => setTrack((v) => !v)}><span className="sw" />Track changes</button>
              {editCount > 0 ? <span className="sugcount">{svg('<path d="M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2z"/>')}{editCount} suggestion{editCount === 1 ? "" : "s"}</span> : null}
            </div>
          </div>

          {/* editor + agent */}
          <div className="ed">
            <div className="docpane">
              <div className="paper">
                <ContractDocument contractId={id} edits={edits ?? []} activeEditId={activeEditId} onSelectEdit={(eid) => { setActiveEditId(eid); setTab("review"); }} onSelectComment={() => setTab("assistant")} />
              </div>
            </div>

            <div className="agent">
              <div className="agthd"><div className="glb">✦</div><div style={{ flex: 1 }}><div className="n">Aegis · contract agent</div><div className="s">grounded in this draft + the playbook</div></div></div>
              <div className="tabs">
                <button className={tab === "assistant" ? "on" : ""} onClick={() => setTab("assistant")}>Assistant</button>
                <button className={tab === "review" ? "on" : ""} onClick={() => setTab("review")}>Review <span className="b">{openDevs.length}</span></button>
                <button className={tab === "sources" ? "on" : ""} onClick={() => setTab("sources")}>Sources <span className="b">{risk?.clause_count ? 2 : 1}</span></button>
              </div>

              <div className="tabwrap">
                {tab === "assistant" && (
                  <>
                    <div className="chat">
                      <div className="msg agent"><div className="mav">✦</div><div className="bubble">
                        I reviewed this draft{risk?.band ? <> — overall risk is <b>{risk.band}{risk.score != null ? ` (${risk.score})` : ""}</b></> : ""}. {openDevs.length > 0 ? <>Found <b>{openDevs.length} open issue{openDevs.length === 1 ? "" : "s"}</b>{openDevs.length > 0 ? ` (${devHigh} high · ${devMedium} medium · ${devLow} low)` : ""}. Open the <b>Review</b> tab for each with its fix, or ask me to draft redlines.</> : "No open playbook deviations — it's clean."}
                        <div className="mact"><button className="mbtn go" onClick={() => setTab("review")}>Open review</button><button className="mbtn" onClick={() => send("Summarise the key risks in this contract.")}>Summarise risks</button></div>
                      </div></div>
                      {chat.map((m, i) => (
                        <div key={i} className={`msg ${m.role}`}><div className="mav">{m.role === "agent" ? "✦" : initials(user?.full_name ?? "You")}</div><div className="bubble">{m.text}</div></div>
                      ))}
                      {busy ? <div className="msg agent"><div className="mav">✦</div><div className="bubble dim">{redline.isPending ? "Drafting tracked changes…" : "Thinking…"}</div></div> : null}
                    </div>
                    <div className="chips">
                      <button className="qchip" disabled={busy || openDevs.length === 0} onClick={draftAllFixes}>Draft fixes for all issues</button>
                      {["Compare to our standard", "Reply to counterparty"].map((c) => <button key={c} className="qchip" disabled={busy} onClick={() => send(c)}>{c}</button>)}
                    </div>
                    <div className="inputbar">
                      <input value={q} aria-label="Ask the agent to redline or analyse" placeholder="Ask the agent to redline or analyse…" onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") send(q); }} />
                      <button className="send" aria-label="Send" onClick={() => send(q)}>{svg('<path d="M22 2 11 13M22 2l-7 20-4-9-9-4z"/>')}</button>
                    </div>
                  </>
                )}

                {tab === "review" && (
                  <div className="list">
                    {openDevs.length === 0 ? <div className="dim" style={{ fontSize: 12.5, padding: 8 }}>No open issues — the draft is clean against the playbook.</div> :
                      openDevs.slice().sort((a, b) => (SEV(a.severity) === "high" ? -1 : 1) - (SEV(b.severity) === "high" ? -1 : 1)).map((d) => (
                        <div key={d.id} className={`rev ${SEV(d.severity)}`}>
                          <div className="rt"><span className={`sev ${SEV(d.severity)}`} /><span style={{ textTransform: "capitalize" }}>{(d.clause_type || "clause").replace(/_/g, " ")}</span><span className="cstat" style={{ marginLeft: "auto" }}>{d.severity}</span></div>
                          <div className="rd">{d.issue}</div>
                          {d.suggested_fix ? <div className="rd" style={{ color: "var(--good)" }}><b style={{ color: "var(--ink)" }}>Fix:</b> {d.suggested_fix}</div> : null}
                          <div className="rf"><button className="btn sm pri" disabled={busy} onClick={() => draftFix(d)}>Draft fix with AI</button></div>
                        </div>
                      ))}
                  </div>
                )}

                {tab === "sources" && (
                  <div className="list">
                    <div className="src"><div className="st">This contract — current draft<span className="stag">draft</span></div><div className="sx">{contract?.title ?? "The working draft"} · {(contract?.lifecycle_stage ?? "draft").replace(/_/g, " ")}. The agent reads the live document text.</div></div>
                    <div className="src"><div className="st">Playbook risk analysis<span className="stag">playbook</span></div><div className="sx">{risk?.summary ?? "The AI risk review scores each clause against the playbook."}{risk?.clause_count ? ` (${risk.clause_count} clauses assessed)` : ""}</div></div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const CLM_CSS = `
.clm{--bg:#f4f6f9;--ext:#75589f;--ext-soft:#efe8f7;--ai:#5a49c0;--ai-soft:#ecebf9;--shadow:0 1px 2px rgba(20,26,40,.05),0 10px 26px rgba(20,26,40,.06);--pop:0 12px 30px rgba(20,26,40,.16);--mono:ui-monospace,SFMono-Regular,Menlo,monospace;--sans:var(--font-sans);--serif:"Iowan Old Style",Georgia,"Times New Roman",serif;height:100%;background:var(--bg);color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .clm{--bg:#0c0f16;--ext:#ab90d2;--ext-soft:#221b31;--ai:#a99cf0;--ai-soft:#1c1a33;--shadow:0 1px 2px rgba(0,0,0,.4),0 12px 30px rgba(0,0,0,.4);--pop:0 14px 34px rgba(0,0,0,.55)}
.clm *{box-sizing:border-box}
.clm .dim{color:var(--ink-3)} .clm .lbl{font:600 10px/1.4 var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.clm .ic{width:15px;height:15px;flex:none;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.clm h1{margin:0;font-weight:660;letter-spacing:-.015em}
.clm button{cursor:pointer;border:0;background:none;color:inherit;font:inherit}
.clm .app{display:grid;grid-template-columns:1fr;height:100%}
.clm .rail{background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;min-height:0}
.clm .brand{display:flex;align-items:center;gap:9px;padding:14px 16px 12px;border-bottom:1px solid var(--border)}
.clm .brand .mark{width:26px;height:26px;border-radius:8px;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;font-weight:700}
.clm .brand b{font-size:15px}
.clm .nav{padding:8px;display:flex;flex-direction:column;gap:1px;overflow:auto;flex:1}
.clm .nav .sec{padding:11px 8px 4px}
.clm .nav a{display:flex;align-items:center;gap:10px;padding:7px 9px;border-radius:8px;color:var(--ink-2);font-weight:500;text-decoration:none}
.clm .nav a:hover{background:var(--surface-2);color:var(--ink)} .clm .nav a.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
.clm .me{border-top:1px solid var(--border);padding:10px 13px;display:flex;align-items:center;gap:9px}
.clm .me .av{width:28px;height:28px;border-radius:50%;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;font-weight:700;font-size:12px}
.clm .main{min-width:0;display:flex;flex-direction:column;min-height:0}
.clm .hd{flex:none;border-bottom:1px solid var(--border);background:var(--surface);padding:10px 18px}
.clm .crumb{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--ink-3);margin-bottom:7px}
.clm .crumb a{text-decoration:none;font-weight:600;color:var(--accent)}
.clm .hrow{display:flex;align-items:center;gap:11px;flex-wrap:wrap}
.clm .hrow h1{font-size:15.5px} .clm .ref{font:600 11px var(--mono);color:var(--ink-3);text-transform:capitalize}
.clm .btn{padding:6px 11px;border-radius:8px;font-size:12px}
  .clm .btn.sm{padding:5px 9px;font-size:11.5px}
.clm .iconbtn{width:30px;height:30px;border-radius:8px;display:grid;place-items:center;color:var(--ink-2)} .clm .iconbtn:hover{background:var(--surface-2)}
.clm .ctx{display:flex;align-items:center;gap:15px;margin-top:10px;padding:9px 13px;border:1px solid color-mix(in srgb,var(--accent) 26%,var(--border));background:var(--accent-soft);border-radius:10px;flex-wrap:wrap}
.clm .ctx.done{border-color:color-mix(in srgb,var(--good) 34%,var(--border));background:var(--good-soft)}
.clm .ctx .cx-ic{width:28px;height:28px;border-radius:8px;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;flex:none} .clm .ctx.done .cx-ic{background:var(--good)}
.clm .ctx .cx-t{font-size:12.5px;color:var(--ink);min-width:200px} .clm .cx-t .k{font:600 9px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--accent)} .clm .ctx.done .cx-t .k{color:var(--good)} .clm .cx-t b{font-weight:680}
.clm .prog{flex:1;min-width:140px;display:flex;align-items:center;gap:9px} .clm .prog .track{flex:1;height:6px;border-radius:4px;background:#ffffff66;overflow:hidden;min-width:70px} .clm .prog .track .fill{height:100%;background:var(--accent);transition:width .3s} .clm .ctx.done .prog .track .fill{background:var(--good)}
.clm .prog .pn{font:600 11.5px var(--mono);white-space:nowrap} .clm .ctx .cx-act{margin-left:auto}
.clm .fmt{display:flex;align-items:center;gap:3px;margin-top:9px;flex-wrap:wrap}
.clm .fmt .sel{display:flex;align-items:center;gap:6px;padding:5px 9px;border:1px solid var(--border-strong);border-radius:7px;font-size:12px;font-weight:600;color:var(--ink-2)} .clm .fmt .sel .ic{width:12px;height:12px}
.clm .fmt .fb{width:29px;height:29px;border-radius:7px;display:grid;place-items:center;color:var(--ink-2);font-weight:700} .clm .fmt .fb:hover{background:var(--surface-2);color:var(--ink)}
.clm .fmt .fdiv{width:1px;height:20px;background:var(--border);margin:0 5px}
.clm .fmt .tog{display:flex;align-items:center;gap:7px;padding:5px 9px;border:1px solid var(--border-strong);border-radius:7px;font-weight:600;font-size:11.5px;color:var(--ink-2)}
.clm .fmt .tog.on{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}
.clm .fmt .tog .sw{width:22px;height:13px;border-radius:99px;background:var(--border-strong);position:relative;transition:.15s} .clm .fmt .tog.on .sw{background:var(--accent)} .clm .fmt .tog .sw::after{content:"";position:absolute;top:2px;left:2px;width:9px;height:9px;border-radius:50%;background:#fff;transition:.15s} .clm .fmt .tog.on .sw::after{left:11px}
.clm .fmt .sugcount{display:inline-flex;align-items:center;gap:6px;padding:5px 9px;border-radius:7px;font-weight:600;font-size:11.5px;background:var(--ai-soft);color:var(--ai)} .clm .fmt .sugcount .ic{width:13px;height:13px}
.clm .ed{flex:1;min-height:0;display:grid;grid-template-columns:1fr 400px}
.clm .docpane{overflow:auto;background:var(--bg);position:relative}
.clm .docpane .paper{max-width:760px;margin:18px auto;background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:var(--shadow);padding:10px}
.clm .agent{border-left:1px solid var(--border);background:var(--surface);display:flex;flex-direction:column;min-height:0}
.clm .agthd{display:flex;align-items:center;gap:9px;padding:11px 14px;border-bottom:1px solid var(--border)}
.clm .agthd .glb{width:28px;height:28px;border-radius:8px;background:var(--ai-soft);color:var(--ai);display:grid;place-items:center;font-size:14px}
.clm .agthd .n{font-weight:660;font-size:12.5px} .clm .agthd .s{font-size:10.5px;color:var(--good);display:flex;align-items:center;gap:5px} .clm .agthd .s::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--good)}
.clm .tabs{display:flex;padding:0 10px;border-bottom:1px solid var(--border);gap:2px;flex:none}
.clm .tabs button{padding:9px 12px;font-weight:600;font-size:12px;color:var(--ink-3);border-bottom:2px solid transparent;margin-bottom:-1px;display:flex;align-items:center;gap:6px}
.clm .tabs button.on{color:var(--accent);border-bottom-color:var(--accent)}
.clm .tabs button .b{font:700 9px var(--mono);background:var(--surface-2);color:var(--ink-2);border-radius:99px;padding:0 5px} .clm .tabs button.on .b{background:var(--accent-soft);color:var(--accent)}
.clm .tabwrap{flex:1;min-height:0;display:flex;flex-direction:column}
.clm .chat{flex:1;overflow:auto;padding:14px;display:flex;flex-direction:column;gap:12px}
.clm .msg{display:flex;gap:8px;max-width:100%} .clm .msg .mav{width:23px;height:23px;border-radius:6px;flex:none;display:grid;place-items:center;font-size:10px;font-weight:700}
.clm .msg.agent .mav{background:var(--ai-soft);color:var(--ai)} .clm .msg.you{flex-direction:row-reverse} .clm .msg.you .mav{background:var(--accent-soft);color:var(--accent)}
.clm .bubble{padding:9px 12px;border-radius:12px;font-size:12.5px;line-height:1.5;max-width:290px;white-space:pre-wrap}
.clm .msg.agent .bubble{background:var(--surface-2);border-top-left-radius:4px} .clm .msg.you .bubble{background:var(--accent);color:var(--accent-ink);border-top-right-radius:4px}
.clm .bubble.dim{color:var(--ink-3)} .clm .bubble b{font-weight:680} .clm .bubble .mact{display:flex;gap:7px;margin-top:9px;flex-wrap:wrap}
.clm .bubble .mbtn{padding:5px 10px;border-radius:7px;font-weight:600;font-size:11.5px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink)} .clm .bubble .mbtn.go{background:var(--ai);border-color:var(--ai);color:#fff}
.clm .chips{padding:9px 12px 0;display:flex;gap:7px;flex-wrap:wrap}
.clm .qchip{border:1px solid var(--border-strong);border-radius:99px;padding:5px 10px;font-size:11.5px;font-weight:600;color:var(--ink-2)} .clm .qchip:hover{background:var(--surface-2);color:var(--ink)} .clm .qchip[disabled]{opacity:.5}
.clm .inputbar{display:flex;gap:8px;padding:11px 12px;border-top:1px solid var(--border)}
.clm .inputbar input{flex:1;padding:8px 11px;border:1px solid var(--border-strong);border-radius:9px;background:var(--inset);color:var(--ink);font-size:12.5px;outline:none} .clm .inputbar input:focus{border-color:var(--accent)}
.clm .inputbar .send{width:33px;height:33px;border-radius:9px;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;flex:none}
.clm .list{flex:1;overflow:auto;padding:14px}
.clm .rev{border:1px solid var(--border);border-radius:11px;padding:12px 13px;margin-bottom:11px} .clm .rev.high{border-left:3px solid var(--crit)} .clm .rev.ext{border-left:3px solid var(--ext)} .clm .rev.medium{border-left:3px solid var(--warn)}
.clm .rev .rt{display:flex;align-items:center;gap:8px;font-weight:650;font-size:12.5px}
.clm .rev .sev{width:8px;height:8px;border-radius:50%;flex:none} .clm .sev.high{background:var(--crit)} .clm .sev.ext{background:var(--ext)} .clm .sev.medium{background:var(--warn)} .clm .sev.low{background:var(--ink-3)}
.clm .rev .cstat{font:600 9px var(--sans);padding:1px 8px;border-radius:99px;text-transform:uppercase;letter-spacing:.04em;background:var(--crit-soft);color:var(--crit)}
.clm .rev .rd{font-size:12px;color:var(--ink-2);margin-top:5px;line-height:1.5}
.clm .rev .rf{display:flex;gap:7px;margin-top:10px}
.clm .src{border:1px solid var(--border);border-radius:11px;padding:12px 13px;margin-bottom:11px}
.clm .src .st{font-weight:650;font-size:12.5px;display:flex;align-items:center;gap:8px} .clm .src .stag{font:600 9px var(--mono);color:var(--ai);background:var(--ai-soft);padding:1px 7px;border-radius:99px;margin-left:auto}
.clm .src .sx{font-size:12px;color:var(--ink-2);margin-top:6px;line-height:1.55}
@media (max-width:1120px){.clm .ed{grid-template-columns:1fr}.clm .agent{border-left:0;border-top:1px solid var(--border);max-height:52vh}}
@media (max-width:820px){.clm .app{grid-template-columns:56px 1fr}.clm .brand b,.clm .nav .sec,.clm .nav a span,.clm .me>div{display:none}}
`;
