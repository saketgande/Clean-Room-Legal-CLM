"use client";

// CLM Editor — Screen 4 ported: a focused, AI-first drafting/review workspace.
// Own full-screen shell (sidebar + editor + agent panel), scoped under `.clm`.
// Wired to the real contract + the workflow engine: the task banner comes from
// the WorkflowRun driving this contract, Review comes from the playbook deviations,
// and the Assistant is the contract-scoped Ask-Aegis. The centre reuses the real
// ContractDocument editor. Old lifecycle panels intentionally dropped.

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { contractsApi, workflowsApi, brainApi, playbooksApi } from "@/lib/endpoints";
import { ContractDocument } from "@/components/contract-document";
import { REVISION_CSS, RevisionChanges, RevisionDecisions } from "@/components/revision-round";
import { WORD_EDITOR_CSS, WordEditor } from "@/components/word-editor";
import { CLAUSE_TREE_CSS, ClauseTree } from "@/components/clause-tree";
import { Markdown } from "@/components/markdown";
import { currentStage, LifecycleView } from "@/components/lifecycle-view";
import { CenterSpinner, ErrorState, NotFound } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { initials } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { ContractDeviation, ContractEditResponse, ContractParty, RevisionChange } from "@/lib/types";

const svg = (p: string) => <svg className="ic" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: p }} />;

const SEV = (s: string) => { const v = (s || "").toLowerCase(); return v === "critical" || v === "high" ? "high" : v === "their_change" ? "ext" : v; };

export function ClmWorkspace({ id }: { id: string }) {
  const { user } = useAuth();
  const { notify } = useToast();
  const { data: contract, isLoading: contractLoading, error: contractError } = useQuery({ queryKey: ["contract", id], queryFn: () => contractsApi.get(id) });
  const { data: edits } = useQuery({ queryKey: ["contract-edits", id], queryFn: () => contractsApi.edits(id) });
  const { data: deviations } = useQuery({ queryKey: ["contract-devs", id], queryFn: () => contractsApi.deviations(id) });
  const { data: risk } = useQuery({ queryKey: ["contract-risk", id], queryFn: () => contractsApi.risk(id) });
  const { data: run } = useQuery({ queryKey: ["flow-run-contract", id], queryFn: () => workflowsApi.runForContract(id) });
  const { data: parties } = useQuery({ queryKey: ["contract-parties", id], queryFn: () => contractsApi.parties(id) });
  const [partyName, setPartyName] = useState("");
  const [partyEmail, setPartyEmail] = useState("");
  const addParty = useMutation({
    mutationFn: () => contractsApi.addParty(id, { name: partyName.trim(), contact_email: partyEmail.trim() || undefined, party_type: "counterparty" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["contract-parties", id] });
      setPartyName("");
      setPartyEmail("");
      notify("Counterparty added", "success");
    },
    onError: (e) => notify(e instanceof Error ? e.message : "Couldn't add counterparty", "error"),
  });

  const [tab, setTab] = useState<"assistant" | "review" | "sources" | "parties" | "changes" | "clauses">("assistant");
  const [pickedClause, setPickedClause] = useState<{ id: string; text: string } | null>(null);
  const [chat, setChat] = useState<{ role: "you" | "agent"; text: string }[]>([]);
  const [q, setQ] = useState("");
  const chatEnd = useRef<HTMLDivElement>(null);
  const [track, setTrack] = useState(true);
  const [activeEditId, setActiveEditId] = useState<string | null>(null);

  const qc = useQueryClient();
  // Their returned version against the one we sent: the round of changes to decide.
  const { data: round } = useQuery({ queryKey: ["revision-round", id], queryFn: () => contractsApi.revisionRound(id) });
  const [selChange, setSelChange] = useState<string | null>(null);
  const [showDocument, setShowDocument] = useState(false);
  const revisionFile = useRef<HTMLInputElement>(null);
  const afterRevision = () => {
    for (const k of [["revision-round", id], ["contract", id], ["contract-edits", id], ["contract-devs", id], ["flow-run-contract", id]])
      qc.invalidateQueries({ queryKey: k });
  };
  const logRevision = useMutation({
    mutationFn: (file: File) => contractsApi.logCounterpartyRevision(id, file),
    onSuccess: () => { afterRevision(); setShowDocument(false); setTab("changes"); notify("Their revision is in — compared with the version we sent", "success"); },
    onError: (e) => notify(e instanceof Error ? e.message : "Couldn't read their file", "error"),
  });
  const decideChange = useMutation({
    mutationFn: ({ c, d, counter }: { c: RevisionChange; d: "open" | "accepted" | "kept" | "countered"; counter?: string }) =>
      contractsApi.decideRevisionChange(id, round!.id, c.id, d, counter),
    onSuccess: (r, v) => {
      qc.setQueryData(["revision-round", id], r);
      // Move on to the next change still to decide.
      const next = r.changes.find((x) => x.decision === "open" && x.id !== v.c.id);
      if (v.d !== "open" && next) setSelChange(next.id);
    },
    onError: (e) => notify(e instanceof Error ? e.message : "Couldn't save the decision", "error"),
  });
  const finishRound = useMutation({
    mutationFn: () => contractsApi.finishRevisionRound(id, round!.id),
    onSuccess: (r) => {
      qc.setQueryData(["revision-round", id], r);
      afterRevision();
      qc.invalidateQueries({ queryKey: ["contract", id, "versions"] });
      setShowDocument(true);
      notify(r.outcome === "agreed" ? "Agreed — negotiation step finished" : "Our reply version is ready to send", "success");
    },
    onError: (e) => notify(e instanceof Error ? e.message : "Couldn't finish the round", "error"),
  });
  const roundOpen = round?.status === "open";
  // The Word editor (ONLYOFFICE): shown in place of the document while open.
  const [wordOpen, setWordOpen] = useState(false);
  const { data: editorCfg } = useQuery({ queryKey: ["editor-available", id], queryFn: () => contractsApi.editorConfig(id), staleTime: 60_000 });
  // The document marks and scrolls to a picked clause itself, except in the
  // redline view (undecided tracked changes), which draws its own spans: there,
  // find the clause's opening words in what is shown and scroll to them.
  function showClause(text: string, number: string | null) {
    if (document.querySelector(".docpane #cite-hl")) return;
    const root = document.querySelector(".docpane article");
    if (!root) return;
    const opening = text.replace(/^\s*\S+\s+/, "").split(/\s+/).slice(0, 8).join(" ");
    const tries: ((t: string) => boolean)[] = [
      (t) => !!opening && t.includes(opening),
      (t) => !!number && t.trimStart().startsWith(`${number} `),
    ];
    for (const test of tries) {
      const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let n = walk.nextNode(); n; n = walk.nextNode()) {
        if (test((n.textContent ?? "").replace(/\s+/g, " "))) {
          const el = n.parentElement!;
          el.scrollIntoView({ behavior: "smooth", block: "center" });
          el.classList.add("ct-flash");
          setTimeout(() => el.classList.remove("ct-flash"), 1800);
          return;
        }
      }
    }
    notify(`${number ?? "This clause"} has a pending change, so the redline shows the proposed wording instead — decide it in Review.`, "info");
  }
  // The document may be covered by the Word editor or the negotiation view:
  // switch back to it, then point at the change. A change the document can't
  // place (its words aren't in the text) gets a word instead of nothing.
  function showEdit(editId: string) {
    setWordOpen(false);
    setShowDocument(true);
    setActiveEditId(null);
    setTimeout(() => setActiveEditId(editId), 0);
    setTimeout(() => {
      if (!document.querySelector(`.docpane #edit-${CSS.escape(editId)}`))
        notify("This change can't be placed in the current text — its words aren't there any more.", "info");
    }, 900);
  }
  function closeWord() {
    setWordOpen(false);
    // The editor saves through its own callback a moment after closing.
    for (const delay of [500, 4000, 10000]) setTimeout(afterRevision, delay);
  }
  const { data: lifeRows } = useQuery({
    queryKey: ["lifecycle-rows-contract", id, run?.id, run?.status, run?.current_index],
    queryFn: () => workflowsApi.lifecycle({ contract_id: id }),
  });
  // Every contract gets a workflow: one uploaded straight into the CLM starts one here.
  const startWorkflow = useMutation({
    mutationFn: () => workflowsApi.startForContract(id),
    onSuccess: (r) => { qc.setQueryData(["flow-run-contract", id], r); notify(`Workflow started: ${r.flow_name}`, "success"); },
    onError: (e) => notify(e instanceof Error ? e.message : "Couldn't start a workflow", "error"),
  });
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
  // The playbook review already proposes a tracked change for most issues (its
  // rationale is the issue text). Deciding it applies or drops that change and
  // closes the issue — no need to ask the AI to draft the same fix again.
  const decide = useMutation({
    mutationFn: async ({ d, edit, accept }: { d: ContractDeviation; edit?: ContractEditResponse; accept: boolean }) => {
      // Only a still-proposed change needs deciding; an issue whose change was
      // already decided (or that has none) just gets closed.
      if (edit?.status === "proposed") {
        if (accept) await contractsApi.acceptEdit(id, edit.id);
        else await contractsApi.rejectEdit(id, edit.id);
      }
      await playbooksApi.decideDeviation(d.id, accept ? "accepted" : "waived",
        accept ? "Accepted the proposed change" : "Kept the original wording");
    },
    onSuccess: (_r, v) => {
      notify(v.accept ? "Change accepted" : "Change rejected — original wording kept", "success");
      qc.invalidateQueries({ queryKey: ["contract-edits", id] });
      qc.invalidateQueries({ queryKey: ["contract-devs", id] });
      qc.invalidateQueries({ queryKey: ["contract", id] });
      qc.invalidateQueries({ queryKey: ["review-status", id] });
    },
    onError: (e) => notify(e instanceof Error ? e.message : "Couldn't save the decision", "error"),
  });
  // The playbook redline names its issue in its citation; older rows only share
  // the issue text as their rationale.
  const editFor = (d: ContractDeviation) => {
    const mine = (edits ?? []).filter((e) => e.edit_type === "playbook_redline");
    return (
      mine.find((e) => (e.citation as { playbook_deviation_id?: string }[] | null)?.some?.((c) => c?.playbook_deviation_id === d.id)) ??
      mine.find((e) => e.rationale === d.issue)
    );
  };
  const busy = ask.isPending || redline.isPending || decide.isPending;
  // Keep the newest message in view as the conversation grows.
  useEffect(() => { chatEnd.current?.scrollIntoView({ block: "end" }); }, [chat.length, busy]);

  const devs: ContractDeviation[] = deviations ?? [];
  // needs_review = a finding without a quote to check it against (e.g. a
  // missing clause). It is still open, and the readiness check counts it.
  const openDevs = devs.filter((d) => ["open", "needs_review"].includes(d.status || "open"));
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
      <style dangerouslySetInnerHTML={{ __html: CLM_CSS + REVISION_CSS + WORD_EDITOR_CSS + CLAUSE_TREE_CSS }} />
      <div className="app">
        {/* main (the shared AegisRail sidebar is provided by AppShell) */}
        <div className="main">
          <div className="hd">
            <div className="crumb"><Link href={reqId ? `/intake?open=${reqId}` : "/contracts"}>← {reqId ? `Request${cpty ? ` · ${cpty}` : ""}` : "Contracts"}</Link><span>/</span><span>CLM editor</span></div>
            <div className="hrow">
              <h1>{contract?.title ?? "Contract"}</h1>
              {run ? <span className="ref">{run.flow_name}</span> : null}
              {run === null ? <button className="btn pri" disabled={startWorkflow.isPending} onClick={() => startWorkflow.mutate()} title="This contract has no workflow yet — start one so it gets its review, approval and signature steps">{svg('<path d="M5 3l14 9-14 9z"/>')}{startWorkflow.isPending ? "Starting…" : "Start workflow"}</button> : null}
              <div style={{ flex: 1 }} />
              <input ref={revisionFile} type="file" hidden accept=".docx,.pdf,.doc,.txt"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) logRevision.mutate(f); e.target.value = ""; }} />
              <button className="btn" disabled={logRevision.isPending} onClick={() => revisionFile.current?.click()}
                title="Upload the version the counterparty sent back; Aegis compares it with the one we sent">
                {svg('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>')}{logRevision.isPending ? "Comparing…" : "Log their revision"}
              </button>
              {editorCfg?.enabled ? (
                <button className={`btn${wordOpen ? " pri" : ""}`} onClick={() => (wordOpen ? closeWord() : setWordOpen(true))}
                  title="Open this version in the Word editor; Save there makes the next version">
                  {svg('<path d="M4 4h16v16H4z"/><path d="M7 8l2 8 3-6 3 6 2-8"/>')}{wordOpen ? "Done editing" : "Edit in Word"}
                </button>
              ) : null}
              <button className="btn" onClick={() => notify("Draft saved", "success")}>{svg('<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/>')}Save</button>
              <button className="iconbtn" title="Theme" onClick={toggleTheme}>{svg('<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>')}</button>
            </div>

            {/* the 7-stage lifecycle; a stage opens its workflow steps */}
            <div className="lc">
              <LifecycleView compact steps={run?.steps ?? []} comments={run?.comments ?? []} extras={lifeRows ?? {}}
                current={currentStage({ run, contractStage: contract.lifecycle_stage })} />
            </div>

            {roundOpen && round && (
              <div className="ctx">
                <div className="cx-ic">{svg('<path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>')}</div>
                <div className="cx-t"><div className="k">Negotiation · round {round.round_number}</div>
                  <div><b>They sent it back with {round.changes.length} change{round.changes.length === 1 ? "" : "s"}</b>{round.open ? ` — ${round.open} to decide` : " — all decided"}{round.changes.some((c) => c.unmarked) ? `, ${round.changes.filter((c) => c.unmarked).length} not marked by them` : ""}.</div></div>
                <div className="cx-act"><button className="btn sm" onClick={() => { setShowDocument(false); setTab("changes"); }}>Review their changes</button></div>
              </div>
            )}

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
              {wordOpen ? (
                <WordEditor contractId={id} />
              ) : roundOpen && round && !showDocument ? (
                <RevisionChanges round={round} selected={selChange ?? round.changes[0]?.id ?? null}
                  onSelect={(cid) => { setSelChange(cid); setTab("changes"); }} onShowDocument={() => setShowDocument(true)} />
              ) : (
              <div className="paper">
                {roundOpen ? <button className="btn sm" style={{ marginBottom: 10 }} onClick={() => setShowDocument(false)}>← Back to their changes</button> : null}
                <ContractDocument contractId={id} edits={edits ?? []} activeEditId={activeEditId} onSelectEdit={(eid) => { setActiveEditId(eid); setTab("review"); }} onSelectComment={() => setTab("assistant")} highlightQuote={pickedClause?.text ?? null} />
              </div>
              )}
            </div>

            <div className="agent">
              <div className="agthd"><div className="glb">✦</div><div style={{ flex: 1 }}><div className="n">Aegis · contract agent</div><div className="s">grounded in this draft + the playbook</div></div></div>
              <div className="tabs">
                <button className={tab === "assistant" ? "on" : ""} onClick={() => setTab("assistant")}>Assistant</button>
                {round ? <button className={tab === "changes" ? "on" : ""} onClick={() => setTab("changes")}>Changes <span className="b">{round.open}</span></button> : null}
                <button className={tab === "review" ? "on" : ""} onClick={() => setTab("review")}>Review <span className="b">{openDevs.length}</span></button>
                <button className={tab === "clauses" ? "on" : ""} onClick={() => setTab("clauses")}>Clauses</button>
                <button className={tab === "sources" ? "on" : ""} onClick={() => setTab("sources")}>Sources <span className="b">{risk?.clause_count ? 2 : 1}</span></button>
                <button className={tab === "parties" ? "on" : ""} onClick={() => setTab("parties")}>Parties <span className="b">{(parties ?? []).length}</span></button>
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
                        <div key={i} className={`msg ${m.role}`}><div className="mav">{m.role === "agent" ? "✦" : initials(user?.full_name ?? "You")}</div>
                          {m.role === "agent" ? <div className="bubble md"><Markdown>{m.text}</Markdown></div> : <div className="bubble">{m.text}</div>}</div>
                      ))}
                      {busy ? <div className="msg agent"><div className="mav">✦</div><div className="bubble dim">{redline.isPending ? "Drafting tracked changes…" : "Thinking…"}</div></div> : null}
                      <div ref={chatEnd} />
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

                {tab === "changes" && round && (
                  <RevisionDecisions round={round} selected={selChange} onSelect={(cid) => { setSelChange(cid); setShowDocument(false); }}
                    busy={decideChange.isPending || finishRound.isPending}
                    onDecide={(c, d, counter) => decideChange.mutate({ c, d, counter })}
                    onFinish={() => finishRound.mutate()} />
                )}

                {tab === "clauses" && (
                  <div className="list">
                    <ClauseTree contractId={id} selected={pickedClause?.id ?? null}
                      onPick={(c) => { setPickedClause({ id: c.id, text: c.text }); setWordOpen(false); setShowDocument(true); setTimeout(() => showClause(c.text, c.number), 400); }} />
                  </div>
                )}

                {tab === "review" && (
                  <div className="list">
                    {openDevs.length === 0 ? <div className="dim" style={{ fontSize: 12.5, padding: 8 }}>No open issues — the draft is clean against the playbook.</div> :
                      openDevs.slice().sort((a, b) => (SEV(a.severity) === "high" ? -1 : 1) - (SEV(b.severity) === "high" ? -1 : 1)).map((d) => (
                        <div key={d.id} className={`rev ${SEV(d.severity)}${editFor(d) && editFor(d)!.id === activeEditId ? " focus" : ""}`}>
                          <div className="rt"><span className={`sev ${SEV(d.severity)}`} /><span style={{ textTransform: "capitalize" }}>{(d.clause_type || "clause").replace(/_/g, " ")}</span><span className="cstat" style={{ marginLeft: "auto" }}>{d.severity}</span></div>
                          <div className="rd">{d.issue}</div>
                          {d.suggested_fix ? <div className="rd" style={{ color: "var(--good)" }}><b style={{ color: "var(--ink)" }}>Fix:</b> {d.suggested_fix}</div> : null}
                          {(() => {
                            const e = editFor(d);
                            if (e && e.status === "proposed") return (
                              <div className="rf">
                                <button className="btn sm pri" disabled={busy} onClick={() => decide.mutate({ d, edit: e, accept: true })}>Accept change</button>
                                <button className="btn sm" disabled={busy} onClick={() => decide.mutate({ d, edit: e, accept: false })}>Reject</button>
                                <button className="btn sm ghost" onClick={() => showEdit(e.id)}>Show in document</button>
                              </div>
                            );
                            if (e) return (
                              <div className="rf">
                                <span className="dim" style={{ fontSize: 12 }}>Change already {e.status}.</span>
                                <button className="btn sm pri" disabled={busy} onClick={() => decide.mutate({ d, edit: e, accept: e.status === "accepted" })}>Close issue</button>
                              </div>
                            );
                            return (
                              <div className="rf">
                                <button className="btn sm pri" disabled={busy} onClick={() => draftFix(d)}>Draft fix with AI</button>
                                <button className="btn sm" disabled={busy} onClick={() => decide.mutate({ d, accept: false })}>Keep as is</button>
                              </div>
                            );
                          })()}
                        </div>
                      ))}
                  </div>
                )}

                {tab === "sources" && (
                  <div className="list">
                    <div className="src"><div className="st">This contract — current draft<span className="stag">draft</span></div><div className="sx">{contract?.title ?? "The working draft"} · {(contract?.lifecycle_stage ?? "draft").replace(/_/g, " ")}. The agent reads the live document text.</div></div>
                    <div className="src"><div className="st">Playbook risk analysis<span className="stag">playbook</span></div><div className="sx">{risk?.summary ?? "The AI risk review scores each clause against the playbook."}{risk?.clause_count ? ` (${risk.assessed_count ?? risk.clause_count} of ${risk.clause_count} clauses assessed)` : ""}</div></div>
                  </div>
                )}

                {tab === "parties" && (
                  <div className="list">
                    <div className="src">
                      <div className="st">New counterparty</div>
                      <div className="partyform">
                        <input value={partyName} onChange={(e) => setPartyName(e.target.value)} placeholder="Name (e.g. Acme Ltd)" />
                        <input value={partyEmail} onChange={(e) => setPartyEmail(e.target.value)} placeholder="Contact email (optional)" type="email" />
                        <button className="btn sm pri" disabled={!partyName.trim() || addParty.isPending} onClick={() => addParty.mutate()}>
                          {addParty.isPending ? "Adding…" : "Add counterparty"}
                        </button>
                      </div>
                    </div>
                    {(parties ?? []).length === 0 ? (
                      <div className="dim" style={{ fontSize: 12.5, padding: 8 }}>No parties recorded yet.</div>
                    ) : (
                      (parties as ContractParty[]).map((p) => (
                        <div key={p.id} className="src">
                          <div className="st">{p.name}{p.party_type ? <span className="stag">{p.party_type}</span> : null}</div>
                          {p.contact_email ? <div className="sx">{p.contact_email}</div> : null}
                        </div>
                      ))
                    )}
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
.clm .lc{margin-top:8px;overflow-x:auto}
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
.clm .msg{display:flex;gap:8px;max-width:100%;flex:none} .clm .msg .mav{width:23px;height:23px;border-radius:6px;flex:none;display:grid;place-items:center;font-size:10px;font-weight:700}
.clm .msg.agent .mav{background:var(--ai-soft);color:var(--ai)} .clm .msg.you{flex-direction:row-reverse} .clm .msg.you .mav{background:var(--accent-soft);color:var(--accent)}
.clm .bubble{padding:9px 12px;border-radius:12px;font-size:12.5px;line-height:1.5;max-width:290px;white-space:pre-wrap}
.clm .msg.agent .bubble{background:var(--surface-2);border-top-left-radius:4px} .clm .msg.you .bubble{background:var(--accent);color:var(--accent-ink);border-top-right-radius:4px}
.clm .bubble.md{white-space:normal;max-width:calc(100% - 31px)} .clm .bubble.md > div{font-size:12.5px;line-height:1.55;color:var(--ink)}
.clm .bubble.md p,.clm .bubble.md ul,.clm .bubble.md ol{margin:0 0 7px} .clm .bubble.md li{margin:2px 0} .clm .bubble.md h1,.clm .bubble.md h2,.clm .bubble.md h3{font-size:13px;margin:8px 0 4px}
.clm .bubble.md hr{margin:8px 0;border:0;border-top:1px solid var(--border)} .clm .bubble.md *:last-child{margin-bottom:0}
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
.clm .rev.focus{box-shadow:0 0 0 2px var(--accent)} .clm .rev .rf .ghost{color:var(--accent);font-weight:600}
.clm .src{border:1px solid var(--border);border-radius:11px;padding:12px 13px;margin-bottom:11px}
.clm .src .st{font-weight:650;font-size:12.5px;display:flex;align-items:center;gap:8px} .clm .src .stag{font:600 9px var(--mono);color:var(--ai);background:var(--ai-soft);padding:1px 7px;border-radius:99px;margin-left:auto}
.clm .src .sx{font-size:12px;color:var(--ink-2);margin-top:6px;line-height:1.55}
.clm .partyform{display:flex;flex-direction:column;gap:7px;margin-top:8px}
.clm .partyform input{padding:7px 10px;border:1px solid var(--border-strong);border-radius:8px;background:var(--inset);color:var(--ink);font-size:12.5px;outline:none}
.clm .partyform input:focus{border-color:var(--accent)}
@media (max-width:1120px){.clm .ed{grid-template-columns:1fr}.clm .agent{border-left:0;border-top:1px solid var(--border);max-height:52vh}}
@media (max-width:820px){.clm .app{grid-template-columns:56px 1fr}.clm .brand b,.clm .nav .sec,.clm .nav a span,.clm .me>div{display:none}}
`;
