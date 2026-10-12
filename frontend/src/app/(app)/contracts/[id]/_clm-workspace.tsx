"use client";

// The contract page: a calm reading desk. The contract is shown as one white page in the middle
// (ContractDocument, `variant="page"`), a slim row above it shows the lifecycle stage, and the panel
// on the right holds everything that works on the contract: the chat, the review of suggested
// changes, the lifecycle, the clause tree, sources and parties. The look is in _workspace-css.ts
// (scoped under `.clm`). Wired to the real contract and the workflow engine: the stage row and the
// Lifecycle tab come from the contract's stage and the WorkflowRun driving it, Review comes from the
// playbook deviations and the contract's edits, and the Assistant is the contract-scoped chat.

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { contractsApi, workflowsApi, playbooksApi } from "@/lib/endpoints";
import { HttpError } from "@/lib/api";
import { ContractDocument, DOCUMENT_PAGE_CSS } from "@/components/contract-document";
import { REVISION_CSS, RevisionChanges, RevisionDecisions } from "@/components/revision-round";
import { WORD_EDITOR_CSS, WordEditor } from "@/components/word-editor";
import { CLAUSE_TREE_CSS, ClauseTree } from "@/components/clause-tree";
import { currentStage, LIFECYCLE_STRIP_CSS, LifecycleView } from "@/components/lifecycle-view";
import { CenterSpinner, ErrorState, NotFound } from "@/components/ui";
import { buildSuggestions, editState, isHighRisk, reviewProgress, type ReviewProgress } from "@/lib/suggestions";
import { describeFixJob, isFixJobActive } from "@/lib/issue-fixes";
import { REVIEW_LIST_CSS, ReviewList } from "./_review-list";
import { LIFECYCLE_TAB_CSS, LifecycleTab, lifecycleQueryKeys } from "./_lifecycle-tab";
import { CHAT_TAB_CSS, ChatTab } from "./_chat-tab";
import { COMMENTS_TAB_CSS, CommentsTab, openCommentCount } from "./_comments-tab";
import { ContractReviewNotice, REVIEW_NOTICE_CSS } from "@/components/review-notice";
import { AFTER_ACCEPT, invalidateReviewQueries, reviewPollInterval } from "@/lib/review-retry";
import { CLM_CSS } from "./_workspace-css";
import { useToast } from "@/components/toast";
import { riskBand, RISK_UNKNOWN_LABEL } from "@/lib/utils";
import type { ContractDeviation, ContractEditResponse, ContractParty, RevisionChange } from "@/lib/types";

const svg = (p: string) => <svg className="ic" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: p }} />;

const SEV = (s: string) => { const v = (s || "").toLowerCase(); return v === "critical" || v === "high" ? "high" : v === "their_change" ? "ext" : v; };

export function ClmWorkspace({ id }: { id: string }) {
  const { notify } = useToast();
  const { data: contract, isLoading: contractLoading, error: contractError } = useQuery({ queryKey: ["contract", id], queryFn: () => contractsApi.get(id) });
  const { data: edits } = useQuery({ queryKey: ["contract-edits", id], queryFn: () => contractsApi.edits(id) });
  const { data: deviations } = useQuery({ queryKey: ["contract-devs", id], queryFn: () => contractsApi.deviations(id) });
  const { data: risk } = useQuery({ queryKey: ["contract-risk", id], queryFn: () => contractsApi.risk(id) });
  const { data: run } = useQuery({ queryKey: ["flow-run-contract", id], queryFn: () => workflowsApi.runForContract(id) });
  const { data: parties } = useQuery({ queryKey: ["contract-parties", id], queryFn: () => contractsApi.parties(id) });
  // Same key as the Comments tab and the document (which marks commented text), so they share one request.
  const { data: comments } = useQuery({ queryKey: ["contract-comments", id], queryFn: () => contractsApi.comments(id) });
  const openComments = openCommentCount(comments);
  // The comment to bring into view when the Comments tab opens (set when one is written or its text is clicked).
  const [focusComment, setFocusComment] = useState<string | null>(null);
  const clearFocusComment = useCallback(() => setFocusComment(null), []);
  const { data: versions } = useQuery({ queryKey: ["contract", id, "versions"], queryFn: () => contractsApi.versions(id) });
  // Same key as the Lifecycle tab, so they share one request and its invalidations.
  // While an automatic review is running, ask again every few seconds so the page notices when it ends.
  const { data: reviewStatus } = useQuery({ queryKey: ["review-status", id], queryFn: () => contractsApi.reviewStatus(id), refetchInterval: (q) => reviewPollInterval(q.state.data?.auto_review) });
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

  const [tab, setTab] = useState<Tab>("assistant");
  const [pickedClause, setPickedClause] = useState<{ id: string; text: string } | null>(null);
  // How the page is shown (display only; nothing is decided by switching): with the tracked changes
  // drawn (markup), as it reads if every pending change is accepted (final), or as it was (original).
  const [viewMode, setViewMode] = useState<"markup" | "final" | "original">("markup");
  const [zoom, setZoom] = useState(1);
  const [activeEditId, setActiveEditId] = useState<string | null>(null);

  const qc = useQueryClient();
  // A review that was pending and no longer is has just ended: what it wrote (suggestions, issues, risk,
  // drafted fixes, the workflow that waited on it) is on the server now, so fetch it.
  const reviewPending = !!reviewStatus?.auto_review;
  const reviewWasPending = useRef(false);
  useEffect(() => {
    if (reviewWasPending.current && !reviewPending) invalidateReviewQueries(qc, id);
    reviewWasPending.current = reviewPending;
  }, [reviewPending, qc, id]);
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
  // Drafting a fix for ONE issue ("Draft fix with AI" on its card): the assistant's edit engine turns
  // the issue into anchored suggested changes, tied to that issue, which then wait in this same Review
  // list. Asking for changes in the chat does the same thing.
  const redline = useMutation({
    mutationFn: ({ instructions, deviationId }: { instructions: string; deviationId?: string }) => contractsApi.aiRedline(id, instructions, deviationId),
    onSuccess: (res) => {
      if (res.edits === 0) notify(res.summary || "Aegis found nothing to change for this.", "info");
      else notify(`Drafted ${res.edits} suggested change${res.edits === 1 ? "" : "s"} — accept or reject ${res.edits === 1 ? "it" : "them"} below.`, "success");
      for (const k of [["contract-edits", id], ["review-status", id]]) qc.invalidateQueries({ queryKey: k });
    },
    onError: (e) => notify(e instanceof Error && e.message ? e.message : "Couldn't draft the change", "error"),
  });
  // Drafting for ALL the issues that have no change yet is a background job on the server, one request per
  // issue so each change is tied to its issue. A playbook review starts it by itself; the button starts it by
  // hand. The page asks how far it has got while it runs, and shows each change as it lands.
  const fixJobKey = ["issue-fix-job", id];
  const { data: fixJob } = useQuery({
    queryKey: fixJobKey,
    queryFn: () => contractsApi.issueFixJob(id),
    retry: false,
    refetchInterval: (query) => (isFixJobActive(query.state.data) ? 3000 : false),
  });
  const fixing = isFixJobActive(fixJob);
  const startFixJob = useMutation({
    mutationFn: () => contractsApi.draftIssueFixes(id),
    onSuccess: (res) => {
      if (res.job) {
        qc.setQueryData(fixJobKey, res.job);
        return;
      }
      notify("Every open issue already has a suggested change.", "info");
      for (const k of [["contract-edits", id], ["contract-devs", id]]) qc.invalidateQueries({ queryKey: k });
    },
    onError: (e) => notify(e instanceof Error && e.message ? e.message : "Couldn't start drafting", "error"),
  });
  useEffect(() => {
    // The server writes a change per issue; show each as soon as it exists.
    if (!fixing) return;
    for (const k of [["contract-edits", id], ["review-status", id]]) qc.invalidateQueries({ queryKey: k });
  }, [fixing, fixJob?.done, id, qc]);
  // Say how it went, once, when a job this page watched ends. A job that finished before the page looked
  // (or an old one) says nothing: it is not news.
  const watchedJob = useRef<string | null>(null);
  useEffect(() => {
    if (!fixJob) return;
    if (isFixJobActive(fixJob)) {
      watchedJob.current = fixJob.job_id;
      return;
    }
    if (watchedJob.current !== fixJob.job_id) return;
    watchedJob.current = null;
    const result = describeFixJob(fixJob);
    notify(result.text, result.tone);
    for (const k of [["contract-edits", id], ["contract-devs", id], ["review-status", id]]) qc.invalidateQueries({ queryKey: k });
  }, [fixJob, id, notify, qc]);
  // A review can finish while another tab is open (or in the chat): ask when Review is opened.
  useEffect(() => {
    if (tab === "review") qc.invalidateQueries({ queryKey: ["issue-fix-job", id] });
  }, [tab, id, qc]);
  // Every suggestion is decided the same way, whoever made it. Deciding a change
  // made for a playbook issue decides the issue too: the server does both in one
  // transaction. Older playbook changes don't name their issue, so those close it
  // here; an issue with no change (or one already decided) is closed on its own.
  type Decision =
    | { kind: "edit"; edit: ContractEditResponse; accept: boolean; issue?: ContractDeviation; closeIssue?: ContractDeviation }
    | { kind: "issue"; issue: ContractDeviation; accept: boolean; reason?: string };
  // "fixed": our wording went in, so the rule was held. "accepted" would mean we took
  // their deviation, a concession (same as _close_paired_issue on the backend).
  const closeIssueCall = (issue: ContractDeviation, accept: boolean, reason?: string) =>
    playbooksApi.decideDeviation(issue.id, accept ? "fixed" : "waived", accept ? "Accepted the proposed change" : reason ? `Kept the original wording. ${reason}` : "Kept the original wording");
  // True from the click until the server has answered. The mutation's own isPending only turns on at
  // the next render, so a quick second click used to send the same decision twice (the second got a 409).
  const deciding = useRef(false);
  const decide = useMutation({
    mutationFn: async (v: Decision) => {
      if (v.kind === "issue") return closeIssueCall(v.issue, v.accept, v.reason);
      if (v.accept) await contractsApi.acceptEdit(id, v.edit.id);
      else await contractsApi.rejectEdit(id, v.edit.id);
      // Rejecting the fix for a high or critical risk does not close the risk: that is its own
      // decision, with a reason (the server leaves such an issue open too: _close_paired_issue).
      if (v.closeIssue && (v.accept || !isHighRisk(v.closeIssue))) await closeIssueCall(v.closeIssue, v.accept);
    },
    onSuccess: (_r, v) => {
      const highRiskStaysOpen = v.kind === "edit" && !v.accept && !!v.issue && isHighRisk(v.issue);
      notify(
        v.kind === "issue" ? (v.accept ? "Issue closed" : "Issue closed — original wording kept")
          : v.accept ? "Change accepted"
          : highRiskStaysOpen ? "Change rejected — the issue stays open until you say why the risk can stay"
          : "Change rejected — original wording kept",
        "success",
      );
      // A decision moves the risk band (fixed resolves) or makes a new version (unknown until re-reviewed).
      for (const k of [["contract-edits", id], ["contract-devs", id], ["contract", id], ["contract", id, "versions"], ["review-status", id], ["contract-risk", id]])
        qc.invalidateQueries({ queryKey: k });
    },
    onError: (e) => {
      // Decided already (another tab, or a slow second click): not an error, just out of date.
      if (e instanceof HttpError && e.status === 409 && /^Only proposed edits can be/.test(e.message))
        notify("That change was already decided. Showing what is saved.", "info");
      else notify(e instanceof Error ? e.message : "Couldn't save the decision", "error");
      // The change may have been decided or gone out of date meanwhile: show what is true now.
      qc.invalidateQueries({ queryKey: ["contract-edits", id] });
    },
    onSettled: () => {
      deciding.current = false;
    },
  });
  function startDecision(v: Decision) {
    if (deciding.current) return;
    deciding.current = true;
    decide.mutate(v);
  }
  function decideEdit(edit: ContractEditResponse, accept: boolean, issue?: ContractDeviation) {
    const legacyIssue = issue && !edit.playbook_deviation_id && editState(edit, contract?.current_authoritative_version_id) === "ready" ? issue : undefined;
    startDecision({ kind: "edit", edit, accept, issue, closeIssue: legacyIssue });
  }
  const busy = redline.isPending || decide.isPending || startFixJob.isPending;
  // The chat answered and something on the contract changed (suggestions drafted, a stage
  // moved): show what is true now in every tab.
  function chatSettled() {
    // (a playbook review run from the chat also starts the drafting job)
    for (const queryKey of [...lifecycleQueryKeys(id), ["contract-devs", id], ["issue-fix-job", id], ["contract-risk", id]]) qc.invalidateQueries({ queryKey });
  }

  const devs: ContractDeviation[] = useMemo(() => deviations ?? [], [deviations]);
  // needs_review = a finding without a quote to check it against (e.g. a
  // missing clause). It is still open, and the readiness check counts it.
  const openDevs = devs.filter((d) => ["open", "needs_review"].includes(d.status || "open"));
  // Breakdown must match openDevs (the issues we count above), not risk.counts
  // (the per-clause risk distribution) — otherwise "N issues" and "X high · …"
  // disagree. SEV folds critical into "high".
  const devHigh = openDevs.filter((d) => SEV(d.severity) === "high").length;
  const devMedium = openDevs.filter((d) => SEV(d.severity) === "medium").length;
  const devLow = openDevs.filter((d) => SEV(d.severity) === "low").length;
  // Say "reviewed" only when the AI playbook review really ran on the text on screen — the
  // current version, or an identical copy right before it (a signed copy, a no-op save).
  // A keyword scan, an unfinished run or no run at all must not read as a clean review.
  const lastReview = reviewStatus?.last_review;
  const reviewedNow = lastReview?.source === "ai" && lastReview.status === "succeeded"
    && (lastReview.on_current_text || lastReview.contract_version_id === contract?.current_authoritative_version_id);
  const reviewLead = !reviewStatus ? null // still loading: claim nothing
    : reviewStatus.auto_review?.state === "running" ? (reviewStatus.auto_review.after === AFTER_ACCEPT
      ? "I'm re-checking your changes against the playbook now. I'll list what still applies; I won't change anything myself."
      : "I'm reviewing this draft against the playbook now. The suggested changes appear when I'm done.")
    : reviewStatus.auto_review?.state === "interrupted" ? "The automatic review of this draft was interrupted, so what follows may be out of date. Use Retry review above to run it again."
    : !lastReview ? "No playbook review has run on this draft yet. Ask me to review it against a playbook."
    : lastReview.source !== "ai" ? "This draft has only had a keyword check, not an AI review, so reworded or missing clauses may not be caught. Ask me to review it against a playbook."
    : lastReview.status !== "succeeded" ? "The last playbook review didn't finish. Ask me to run it again."
    : !reviewedNow ? "I reviewed an earlier version of this draft; changes made since then haven't been checked."
    : "I reviewed this draft.";
  // Known only when the band was computed for the text on screen; otherwise say so
  // rather than show a level (a stale "low" is the dangerous case).
  const band = riskBand(risk, contract?.current_authoritative_version_id ?? null);
  const reqId = run?.request_id ?? null;
  const suggestions = useMemo(() => buildSuggestions({ deviations: devs, edits: edits ?? [], currentVersionId: contract?.current_authoritative_version_id }), [devs, edits, contract?.current_authoritative_version_id]);
  const unfixed = suggestions.issues.filter((i) => i.edits.length === 0).map((i) => i.deviation);
  const editCount = suggestions.proposedEdits;
  // Already newest first: the server lists edits by creation time, newest first.
  const decidedEdits = useMemo(() => (edits ?? []).filter((e) => e.status !== "proposed"), [edits]);
  const progress = useMemo(() => reviewProgress(suggestions, edits ?? [], contract?.current_authoritative_version_id), [suggestions, edits, contract?.current_authoritative_version_id]);
  const cpty = contract?.counterparty_name;

  // The previous / next suggested change in the page, in the order they appear in the text.
  function stepEdit(dir: 1 | -1) {
    const marks = Array.from(document.querySelectorAll<HTMLElement>('.docpane [id^="edit-"][data-st="proposed"]'));
    if (marks.length === 0) {
      notify("No suggested changes are marked in the page.", "info");
      return;
    }
    const at = marks.findIndex((m) => m.id === `edit-${activeEditId}`);
    const next = marks[at < 0 ? (dir > 0 ? 0 : marks.length - 1) : (at + dir + marks.length) % marks.length];
    setActiveEditId(next.id.slice("edit-".length));
    setTab("review");
  }
  function toggleTheme() {
    const el = document.documentElement;
    el.classList.toggle("dark");
  }
  // The same sentence the server builds when it drafts for every issue (fix_instruction in
  // backend app/contract_files/issue_fixes.py): change both together.
  function devInstruction(d: ContractDeviation): string {
    const quote = (d.citation as { quote?: string } | null)?.quote;
    return `Fix the ${(d.clause_type || "clause").replace(/_/g, " ")} issue as a tracked change. Issue: ${d.issue}${d.suggested_fix ? ` Required change: ${d.suggested_fix}` : ""}${quote ? ` The existing clause reads: "${quote}"` : ""}`;
  }
  function draftFix(d: ContractDeviation) {
    // Not while the job is writing: it could be on this very issue.
    if (busy || fixing) return;
    redline.mutate({ instructions: devInstruction(d), deviationId: d.id });
  }
  // Only issues with no change yet: the others already have a fix waiting in Review.
  function draftAllFixes() {
    if (busy || fixing || unfixed.length === 0) return;
    startFixJob.mutate();
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

  const versionNo = (versions ?? []).find((v) => v.id === contract.current_authoritative_version_id)?.version_number ?? null;
  const backHref = reqId ? `/intake?open=${reqId}` : "/contracts";
  const backLabel = reqId ? `Back to the request${cpty ? ` · ${cpty}` : ""}` : "Back to contracts";

  // Right end of the stage row: what the contract is flagged for.
  const stageChips = (
    <>
      {contract.renewal_due ? <span className="lchip warn">Renewal due</span> : null}
      {contract.archived ? <span className="lchip">Archived</span> : null}
    </>
  );

  // Floats at the top of the page: how it is shown, the previous / next suggested change, text size.
  const MODES: [typeof viewMode, string][] = [["markup", "Markup"], ["final", "Final"], ["original", "Original"]];
  const pageControls = (
    <>
      <div className="seg-ctl" role="group" aria-label="Show">
        {MODES.map(([m, label]) => (
          <button key={m} type="button" aria-pressed={viewMode === m} onClick={() => setViewMode(m)}>{label}</button>
        ))}
      </div>
      <span className="dsep" />
      <button type="button" className="tbtn" aria-label="Previous suggestion" disabled={editCount === 0} onClick={() => stepEdit(-1)}>{svg('<path d="M18 15l-6-6-6 6"/>')}</button>
      <button type="button" className="tbtn" aria-label="Next suggestion" disabled={editCount === 0} onClick={() => stepEdit(1)}>{svg('<path d="M6 9l6 6 6-6"/>')}</button>
      <span className="dsep" />
      <button type="button" className="tbtn" aria-label="Zoom out" disabled={zoom <= 0.8} onClick={() => setZoom((z) => Math.max(0.8, Math.round((z - 0.1) * 10) / 10))}>{svg('<path d="M5 12h14"/>')}</button>
      <span className="zoomv" aria-live="polite">{Math.round(zoom * 100)}%</span>
      <button type="button" className="tbtn" aria-label="Zoom in" disabled={zoom >= 1.6} onClick={() => setZoom((z) => Math.min(1.6, Math.round((z + 0.1) * 10) / 10))}>{svg('<path d="M12 5v14M5 12h14"/>')}</button>
    </>
  );

  return (
    <div className="clm">
      <style dangerouslySetInnerHTML={{ __html: CLM_CSS + LIFECYCLE_STRIP_CSS + DOCUMENT_PAGE_CSS + LIFECYCLE_TAB_CSS + CHAT_TAB_CSS + COMMENTS_TAB_CSS + REVIEW_NOTICE_CSS + REVIEW_LIST_CSS + REVISION_CSS + WORD_EDITOR_CSS + CLAUSE_TREE_CSS }} />
      <div className="app">
        {/* main (the shared AegisRail sidebar is provided by AppShell) */}
        <div className="main">
          <header className="bar">
            <div className="bar-l">
              <Link href={backHref} className="iconbtn" aria-label={backLabel} title={backLabel}>{svg('<path d="M15 18l-6-6 6-6"/>')}</Link>
              <div className="ttl">
                <div className="crumb"><Link href={backHref}>{reqId ? `Request${cpty ? ` · ${cpty}` : ""}` : "Contracts"}</Link><span>/</span><span>CLM editor</span></div>
                <h1>{contract?.title ?? "Contract"}</h1>
              </div>
              {versionNo != null ? <span className="vchip" title="The version on screen">V{versionNo}</span> : null}
              {run ? <span className="vchip flow" title="The workflow driving this contract">{run.flow_name}</span> : null}
              {run === null ? <button type="button" className="vchip flow none" onClick={() => setTab("lifecycle")} title="This contract has no workflow yet. Open the Lifecycle tab to start one.">No workflow</button> : null}
            </div>
            <div className="bar-r">
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
              <button className="iconbtn" title="Theme" aria-label="Switch between light and dark" onClick={toggleTheme}>{svg('<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>')}</button>
            </div>
          </header>

          {/* the 7-stage lifecycle; a stage opens its workflow steps */}
          <nav className="life" aria-label="Contract lifecycle">
            <LifecycleView compact strip trailing={stageChips} steps={run?.steps ?? []} comments={run?.comments ?? []} extras={lifeRows ?? {}}
              current={currentStage({ run, contractStage: contract.lifecycle_stage })} />
          </nav>

          {roundOpen && round && (
            <div className="ctx">
              <div className="cx-ic">{svg('<path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>')}</div>
              <div className="cx-t"><div className="k">Negotiation · round {round.round_number}</div>
                <div><b>They sent it back with {round.changes.length} change{round.changes.length === 1 ? "" : "s"}</b>{round.open ? ` — ${round.open} to decide` : " — all decided"}{round.changes.some((c) => c.unmarked) ? `, ${round.changes.filter((c) => c.unmarked).length} not marked by them` : ""}.</div></div>
              <div className="cx-act"><button className="btn sm" onClick={() => { setShowDocument(false); setTab("changes"); }}>Review their changes</button></div>
            </div>
          )}

          {/* the desk and the panel */}
          <div className="work">
            <section className="canvas docpane" aria-label="Contract">
              {wordOpen ? (
                <div className="canvas-fill"><WordEditor contractId={id} /></div>
              ) : roundOpen && round && !showDocument ? (
                <div className="canvas-fill">
                  <RevisionChanges round={round} selected={selChange ?? round.changes[0]?.id ?? null}
                    onSelect={(cid) => { setSelChange(cid); setTab("changes"); }} onShowDocument={() => setShowDocument(true)} />
                </div>
              ) : (
                <>
                  {roundOpen ? <div className="backrow"><button className="btn sm" onClick={() => setShowDocument(false)}>← Back to their changes</button></div> : null}
                  <ContractDocument className="!h-auto min-h-0 flex-1" variant="page" mode={viewMode} zoom={zoom} toolbar={pageControls}
                    contractId={id} edits={edits ?? []} activeEditId={activeEditId}
                    onSelectEdit={(eid) => { setActiveEditId(eid); setTab("review"); }} onSelectComment={(cid) => { setFocusComment(cid); setTab("comments"); }}
                    highlightQuote={pickedClause?.text ?? null} />
                </>
              )}
            </section>

            <aside className="panel agent" aria-label="Aegis">
              <div className="ph2">
                <div className="ph2-top"><h2>Aegis</h2><span className="sub">Grounded in this draft and the playbook</span></div>
                <ReviewSummary p={progress} />
                <div className="tabs">
                  <div className="tabs-main">
                    <button className={tab === "assistant" ? "on" : ""} onClick={() => setTab("assistant")}>Assistant</button>
                    {round ? <button className={tab === "changes" ? "on" : ""} onClick={() => setTab("changes")}>Changes <span className="b">{round.open}</span></button> : null}
                    <button className={tab === "review" ? "on" : ""} onClick={() => setTab("review")}>Review{suggestions.toDecide > 0 ? <> <span className="b">{suggestions.toDecide}</span></> : null}</button>
                    <button className={tab === "comments" ? "on" : ""} onClick={() => setTab("comments")}>Comments{openComments > 0 ? <> <span className="b">{openComments}</span></> : null}</button>
                    <button className={tab === "lifecycle" ? "on" : ""} onClick={() => setTab("lifecycle")}>Lifecycle</button>
                  </div>
                  <MoreTabs
                    tab={tab}
                    onPick={setTab}
                    items={[
                      { id: "clauses", label: "Clauses" },
                      { id: "sources", label: "Sources" },
                      { id: "parties", label: "Parties", count: (parties ?? []).length },
                    ]}
                  />
                </div>
              </div>

              <div className="tabwrap">
                {/* Kept mounted while another tab is open, so an answer in progress, a confirmation
                    waiting for a yes and an unsent message are all still there when you come back. */}
                <div style={{ display: tab === "assistant" ? "contents" : "none" }}>
                  <ChatTab
                    contractId={id}
                    stage={contract.lifecycle_stage}
                    banner={<ContractReviewNotice contractId={id} auto={reviewStatus?.auto_review} />}
                    onSettled={chatSettled}
                    onOpenReview={() => setTab("review")}
                    onOpenLifecycle={() => setTab("lifecycle")}
                    intro={
                      <>
                        {reviewLead}{band !== "unknown" ? <> Overall risk is <b>{band}</b>.</> : " Risk: not reviewed on this text."} {openDevs.length > 0 ? <>Found <b>{openDevs.length} open issue{openDevs.length === 1 ? "" : "s"}</b> ({devHigh} high · {devMedium} medium · {devLow} low). Open the <b>Review</b> tab for each with its fix, or ask me to draft redlines.</> : reviewedNow && band === "low" ? "No open playbook deviations — it's clean." : ""}
                        {openDevs.length > 0 && <div className="mact"><button className="mbtn go" onClick={() => setTab("review")}>Open review</button></div>}
                      </>
                    }
                  />
                </div>

                {/* Kept mounted like the Assistant, so a comment being typed is still there when you come back. */}
                <div style={{ display: tab === "comments" ? "contents" : "none" }}>
                  <CommentsTab contractId={id} focusId={focusComment} onFocused={clearFocusComment} />
                </div>

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
                  <ReviewList
                    suggestions={suggestions}
                    currentVersionId={contract?.current_authoritative_version_id ?? null}
                    busy={busy}
                    drafting={redline.isPending || startFixJob.isPending || fixing}
                    draftProgress={fixing && fixJob ? { done: fixJob.done, total: fixJob.total } : null}
                    unfixedCount={unfixed.length}
                    onDraftAll={draftAllFixes}
                    decided={decidedEdits}
                    activeEditId={activeEditId}
                    onDecide={decideEdit}
                    onCloseIssue={(issue, accept, reason) => startDecision({ kind: "issue", issue, accept, reason })}
                    onDraftFix={draftFix}
                    onShow={showEdit}
                  />
                )}

                {tab === "lifecycle" && (
                  <LifecycleTab contractId={id} contractStage={contract.lifecycle_stage} run={run} onOpenReview={() => setTab("review")} onOpenComments={() => setTab("comments")} />
                )}

                {tab === "sources" && (
                  <div className="list">
                    <div className="src"><div className="st">This contract — current draft<span className="stag">draft</span></div><div className="sx">{contract?.title ?? "The working draft"} · {(contract?.lifecycle_stage ?? "draft").replace(/_/g, " ")}. The agent reads the live document text.</div></div>
                    <div className="src"><div className="st">Playbook risk analysis<span className="stag">playbook</span></div><div className="sx">{band !== "unknown"
                      ? `Overall risk is ${band}. It comes from the playbook review of this text. An issue you decided to keep as it is still counts, because its wording is still in the contract; only a fix that goes into the text lowers it.`
                      : `${RISK_UNKNOWN_LABEL}.${risk?.note ? ` ${risk.note}` : ""}`}</div></div>
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

              {tab === "review" || tab === "comments" ? (
                <div className="pfoot">
                  {svg('<path d="M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2z"/>')}
                  {tab === "review" ? "Select text in the contract to propose a change or comment." : "Select text in the contract to comment on that passage."}
                </div>
              ) : null}
            </aside>
          </div>
        </div>
      </div>
    </div>
  );
}

type Tab = "assistant" | "review" | "comments" | "lifecycle" | "sources" | "parties" | "changes" | "clauses";

/** The tabs used less often sit behind one "More" button, so the tab row always fits the panel.
 *  When one of them is open, the button carries its name and is marked as the current tab. */
function MoreTabs({ items, tab, onPick }: { items: { id: Tab; label: string; count?: number }[]; tab: Tab; onPick: (t: Tab) => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  const active = items.find((i) => i.id === tab);
  return (
    <div className="more" ref={root}>
      <button type="button" className={active ? "on" : ""} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {active ? active.label : "More"}
        <svg className="ic" viewBox="0 0 24 24" aria-hidden="true" style={{ width: 12, height: 12 }}><path d="M6 9l6 6 6-6" /></svg>
      </button>
      {open ? (
        <div className="menu" role="menu">
          {items.map((i) => (
            <button key={i.id} type="button" role="menuitem" className={i.id === tab ? "on" : ""} onClick={() => { onPick(i.id); setOpen(false); }}>
              {i.label}
              {i.count ? <span className="b">{i.count}</span> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** How much of the review is decided, and how risky what is left is: one line and a bar above the tabs. */
function ReviewSummary({ p }: { p: ReviewProgress }) {
  const risks: [string, number, string][] = [
    ["high", p.risk.high, "var(--crit)"],
    ["medium", p.risk.medium, "var(--risk-med)"],
    ["low", p.risk.low, "var(--risk-low)"],
  ];
  return (
    <div className="rsum" role="group" aria-label="Review progress">
      <div className="rsum-t">
        <b>{p.total === 0 ? "Nothing to decide" : `${p.decided} of ${p.total} decided`}</b>
        <span className="risks">
          {risks.filter(([, n]) => n > 0).map(([label, n, color]) => (
            <span key={label} style={{ "--rc": color } as CSSProperties}><i />{n} {label}</span>
          ))}
        </span>
      </div>
      {p.total > 0 ? <div className="track"><i style={{ width: `${Math.round((p.decided / p.total) * 100)}%` }} /></div> : null}
    </div>
  );
}
