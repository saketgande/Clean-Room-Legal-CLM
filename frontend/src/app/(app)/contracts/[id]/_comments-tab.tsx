"use client";

// The Comments tab on the contract page: the discussion on this contract.
//
// Comments come from three places and all of them are listed here:
//   - a person writing one in the box at the top of this tab,
//   - a person selecting text in the contract and choosing Comment (the comment then carries that
//     passage, and this tab can scroll the contract back to it), and
//   - the other side, through a share link (shown as "Counterparty", always Shared).
//
// "Internal" comments stay with your team. "Shared" ones can be shown to the other side. A comment can be
// resolved (and reopened), and removed. Open comments are listed first so what is left to do comes first;
// the count on the tab and the "Resolve comments" line in the Lifecycle tab are the same numbers.
//
// `CommentsPanel` only shows things and reports clicks; `CommentsTab` owns the data and the server calls.

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { contractsApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { CenterSpinner, LoadError } from "@/components/ui";
import { fmtDateTime } from "@/lib/utils";
import type { ContractComment } from "@/lib/types";

type Visibility = ContractComment["visibility"];

/** The text a comment was written on, when it was written on a passage. */
export function commentQuote(c: ContractComment): string | null {
  const quote = (c.anchor as { quote?: unknown } | null)?.quote;
  return typeof quote === "string" && quote.trim() ? quote.trim() : null;
}

/** Open comments first, then resolved ones; each group keeps the order the server gave (oldest first). */
export function orderComments(comments: ContractComment[]): ContractComment[] {
  return [...comments.filter((c) => !c.resolved), ...comments.filter((c) => c.resolved)];
}

export const openCommentCount = (comments: ContractComment[] | undefined) => (comments ?? []).filter((c) => !c.resolved).length;

/** Whether the person may delete this comment. The server decides in the end (the author, or someone who
 *  can edit the contract); this only keeps a button out of sight that would be refused. */
export function canDeleteComment(c: ContractComment, user: { id: string; permissions: string[] } | null | undefined): boolean {
  if (!user) return false;
  return c.author_user_id === user.id || user.permissions.includes("contract:update") || user.permissions.includes("*");
}

// ---------------------------------------------------------------------------
// The view
// ---------------------------------------------------------------------------

export interface CommentsPanelProps {
  comments: ContractComment[];
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  /** The comment id of an item that has a request in flight, so its buttons wait. */
  pendingIds: ReadonlySet<string>;
  posting: boolean;
  canDelete: (c: ContractComment) => boolean;
  onPost: (body: string, visibility: Visibility) => Promise<boolean>;
  onToggleResolved: (c: ContractComment) => void;
  onDelete: (c: ContractComment) => void;
  onShowPassage: (c: ContractComment) => void;
  /** A comment to bring into view and flash, once. */
  focusId?: string | null;
  onFocused?: () => void;
}

export function CommentsPanel(p: CommentsPanelProps) {
  const [body, setBody] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("internal");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);

  const ordered = useMemo(() => orderComments(p.comments), [p.comments]);
  const open = ordered.filter((c) => !c.resolved).length;

  // Bring the asked-for comment into view, once it is on the list.
  const { focusId, onFocused } = p;
  useEffect(() => {
    if (!focusId || !p.comments.some((c) => c.id === focusId)) return;
    const card = Array.from(root.current?.querySelectorAll("[data-comment-id]") ?? []).find((el) => el.getAttribute("data-comment-id") === focusId);
    card?.scrollIntoView?.({ behavior: "smooth", block: "center" });
    setFlash(focusId);
    onFocused?.();
    // Not cleaned up on purpose: asking the parent to forget focusId re-runs this effect, which must not
    // cancel the timer that switches the highlight off again.
    setTimeout(() => setFlash((cur) => (cur === focusId ? null : cur)), 1800);
  }, [focusId, p.comments, onFocused]);

  // `p.posting` only turns true a moment after the click, so a quick second click is stopped here, at once.
  const sending = useRef(false);
  async function post() {
    const text = body.trim();
    if (!text || p.posting || sending.current) return;
    sending.current = true;
    try {
      if (await p.onPost(text, visibility)) setBody("");
    } finally {
      sending.current = false;
    }
  }

  return (
    <div className="list cmx" ref={root}>
      <section className="cmx-new" aria-label="Add a comment">
        <label htmlFor="cmx-body" className="cmx-k">Add a comment</label>
        <textarea id="cmx-body" rows={3} maxLength={5000} value={body} onChange={(e) => setBody(e.target.value)}
          placeholder="Write a comment…"
          onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); void post(); } }} />
        <div className="cmx-row">
          <div className="cmx-seg" role="group" aria-label="Who can see it">
            {(["internal", "shared"] as const).map((v) => (
              <button key={v} type="button" aria-pressed={visibility === v} className={visibility === v ? "on" : ""} onClick={() => setVisibility(v)}>
                {v === "internal" ? "Internal" : "Shared"}
              </button>
            ))}
          </div>
          <button type="button" className="btn sm pri" disabled={!body.trim() || p.posting} onClick={() => void post()}>
            {p.posting ? "Posting…" : "Comment"}
          </button>
        </div>
        <p className="cmx-hint">
          {visibility === "internal" ? "Internal: only your team sees it." : "Shared: the other side can see it when you share this contract with them."}
          {" "}Or select text in the contract and choose Comment to write on that passage.
        </p>
      </section>

      {p.loading ? (
        <CenterSpinner label="Loading comments…" />
      ) : p.error ? (
        <LoadError error={p.error} what="the comments" onRetry={p.onRetry} />
      ) : ordered.length === 0 ? (
        <div className="cmx-empty">
          <b>No comments yet.</b>
          <span>Comments from your team, and from the other side when they comment on a shared link, show up here.</span>
        </div>
      ) : (
        <>
          <div className="cmx-count" aria-live="polite">{open === 0 ? "Everything is resolved" : `${open} open`}{ordered.length > open ? ` · ${ordered.length - open} resolved` : ""}</div>
          <ul className="cmx-list" aria-label="Comments">
            {ordered.map((c) => {
              const quote = commentQuote(c);
              const wait = p.pendingIds.has(c.id);
              return (
                <li key={c.id} data-comment-id={c.id} id={`comment-${c.id}`}
                  className={`cmx-item${c.resolved ? " done" : ""}${flash === c.id ? " flash" : ""}`}>
                  <div className="cmx-head">
                    <span className="cmx-who">{c.author_name}</span>
                    {c.author_kind === "counterparty" ? <span className="cmx-tag ext">Counterparty</span> : null}
                    <span className={`cmx-tag${c.visibility === "shared" ? " shared" : ""}`}>{c.visibility === "shared" ? "Shared" : "Internal"}</span>
                    {c.resolved ? <span className="cmx-tag good">Resolved</span> : null}
                    <time className="cmx-time" dateTime={c.created_at}>{fmtDateTime(c.created_at)}</time>
                  </div>
                  {quote ? (
                    c.resolved ? (
                      <div className="cmx-quote plain">“{quote}”</div>
                    ) : (
                      <button type="button" className="cmx-quote" title="Show this passage in the contract" onClick={() => p.onShowPassage(c)}>“{quote}”</button>
                    )
                  ) : null}
                  <p className="cmx-body">{c.body}</p>
                  <div className="cmx-act">
                    <button type="button" className="cmx-link" disabled={wait} onClick={() => p.onToggleResolved(c)}>{c.resolved ? "Reopen" : "Resolve"}</button>
                    {p.canDelete(c) ? (
                      confirming === c.id ? (
                        <span className="cmx-confirm" role="group" aria-label="Delete this comment?">
                          <span>Delete this comment?</span>
                          <button type="button" className="cmx-link danger" disabled={wait} onClick={() => { setConfirming(null); p.onDelete(c); }}>Yes, delete</button>
                          <button type="button" className="cmx-link" onClick={() => setConfirming(null)}>Cancel</button>
                        </span>
                      ) : (
                        <button type="button" className="cmx-link muted" disabled={wait} onClick={() => setConfirming(c.id)}>Delete</button>
                      )
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The tab: data and server calls
// ---------------------------------------------------------------------------

/** After a comment changes: the list, and the open-comment count the Lifecycle tab reads. */
function useRefreshComments(contractId: string) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["contract-comments", contractId] });
    qc.invalidateQueries({ queryKey: ["review-status", contractId] });
  };
}

export function CommentsTab({ contractId, focusId, onFocused }: { contractId: string; focusId?: string | null; onFocused?: () => void }) {
  const { user } = useAuth();
  const { notify } = useToast();
  const qc = useQueryClient();
  const refresh = useRefreshComments(contractId);
  const key = ["contract-comments", contractId] as const;
  const list = useQuery({ queryKey: key, queryFn: () => contractsApi.comments(contractId) });
  const [pendingIds, setPendingIds] = useState<ReadonlySet<string>>(new Set());
  const hold = (id: string, on: boolean) =>
    setPendingIds((s) => { const n = new Set(s); if (on) n.add(id); else n.delete(id); return n; });

  const post = useMutation({
    mutationFn: (v: { body: string; visibility: Visibility }) => contractsApi.addComment(contractId, v),
    onSuccess: refresh,
  });

  async function onPost(body: string, visibility: Visibility) {
    try {
      const created = await post.mutateAsync({ body, visibility });
      qc.setQueryData<ContractComment[]>(key, (old) => (old?.some((c) => c.id === created.id) ? old : [...(old ?? []), created]));
      return true;
    } catch (e) {
      notify(e instanceof Error ? e.message : "Couldn't post the comment", "error");
      return false;
    }
  }

  // Resolve, reopen and delete show at once and are put back if the server says no.
  async function act(c: ContractComment, call: () => Promise<unknown>, apply: (l: ContractComment[]) => ContractComment[], failed: string) {
    if (pendingIds.has(c.id)) return;
    const before = qc.getQueryData<ContractComment[]>(key);
    hold(c.id, true);
    if (before) qc.setQueryData<ContractComment[]>(key, apply(before));
    try {
      await call();
    } catch (e) {
      if (before) qc.setQueryData<ContractComment[]>(key, before);
      notify(e instanceof Error ? e.message : failed, "error");
    } finally {
      hold(c.id, false);
      refresh();
    }
  }

  const toggleResolved = (c: ContractComment) =>
    act(c, () => contractsApi.resolveComment(contractId, c.id, !c.resolved),
      (l) => l.map((x) => (x.id === c.id ? { ...x, resolved: !c.resolved } : x)),
      c.resolved ? "Couldn't reopen the comment" : "Couldn't resolve the comment");
  const remove = (c: ContractComment) =>
    act(c, () => contractsApi.deleteComment(contractId, c.id), (l) => l.filter((x) => x.id !== c.id), "Couldn't delete the comment");

  function showPassage(c: ContractComment) {
    const el = document.getElementById(`anchor-comment-${c.id}`);
    if (!el) {
      notify("That passage is not on screen right now. Open the contract's page view to see it.", "info");
      return;
    }
    el.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  return (
    <CommentsPanel
      comments={list.data ?? []}
      loading={list.isLoading}
      // A failed refresh keeps the list that is already on screen; the error box is for when there is none.
      error={list.isError && list.data === undefined ? list.error : null}
      onRetry={() => void list.refetch()}
      pendingIds={pendingIds}
      posting={post.isPending}
      canDelete={(c) => canDeleteComment(c, user)}
      onPost={onPost}
      onToggleResolved={(c) => void toggleResolved(c)}
      onDelete={(c) => void remove(c)}
      onShowPassage={showPassage}
      focusId={focusId}
      onFocused={onFocused}
    />
  );
}

export const COMMENTS_TAB_CSS = `
.clm .cmx{display:flex;flex-direction:column;gap:12px}
.clm .cmx-k{font:600 10px var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.clm .cmx-new{border:1px solid var(--border);border-radius:12px;padding:12px 14px;background:var(--surface);display:flex;flex-direction:column;gap:8px}
.clm .cmx-new textarea{padding:8px 10px;border:1px solid var(--border-strong);border-radius:8px;background:var(--surface);color:var(--ink);font:12.5px/1.5 var(--sans);outline:none;resize:vertical;min-height:64px}
.clm .cmx-new textarea:focus{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft)}
.clm .cmx-row{display:flex;align-items:center;justify-content:space-between;gap:8px}
.clm .cmx-seg{display:inline-flex;padding:2px;border-radius:9px;background:var(--surface-2);border:1px solid var(--border)}
.clm .cmx-seg button{height:26px;padding:0 11px;border-radius:7px;font:500 12px var(--sans);color:var(--ink-3)}
.clm .cmx-seg button.on{background:var(--surface);color:var(--ink);box-shadow:0 1px 2px rgba(20,24,34,.12)}
.clm .cmx-hint{margin:0;font-size:11.5px;line-height:1.45;color:var(--ink-3)}
.clm .cmx-count{font:600 10px var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.clm .cmx-empty{display:flex;flex-direction:column;gap:3px;padding:14px;border:1px dashed var(--border-strong);border-radius:12px;font-size:12.5px;color:var(--ink-2)}
.clm .cmx-empty b{color:var(--ink)}
.clm .cmx-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.clm .cmx-item{border:1px solid var(--border);border-radius:12px;padding:11px 13px;background:var(--surface);transition:box-shadow .3s,border-color .3s}
.clm .cmx-item.done{background:var(--surface-2)} .clm .cmx-item.done .cmx-body{color:var(--ink-3)}
.clm .cmx-item.flash{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.clm .cmx-head{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.clm .cmx-who{font-weight:600;font-size:12.5px;color:var(--ink)}
.clm .cmx-time{margin-left:auto;font-size:11px;color:var(--ink-3)}
.clm .cmx-tag{font:600 9.5px var(--sans);letter-spacing:.04em;text-transform:uppercase;padding:2px 7px;border-radius:99px;background:var(--surface-2);color:var(--ink-3)}
.clm .cmx-tag.shared{background:var(--accent-soft);color:var(--accent)}
.clm .cmx-tag.ext{background:var(--ext-soft);color:var(--ext)}
.clm .cmx-tag.good{background:var(--good-soft);color:var(--good)}
.clm .cmx-quote{width:100%;margin:7px 0 0;padding:1px 0 1px 9px;border-left:2px solid var(--p-cmt-line);text-align:left;font-size:12px;font-style:italic;line-height:1.45;color:var(--ink-2);overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.clm button.cmx-quote:hover{color:var(--accent)}
.clm .cmx-quote.plain{cursor:default;color:var(--ink-3)}
.clm .cmx-body{margin:6px 0 0;font-size:12.5px;line-height:1.5;color:var(--ink);white-space:pre-wrap;overflow-wrap:anywhere}
.clm .cmx-act{display:flex;align-items:center;gap:12px;margin-top:8px;font-size:12px}
.clm .cmx-link{font:600 12px var(--sans);color:var(--ink-2)} .clm .cmx-link:hover{color:var(--ink)}
.clm .cmx-link.muted{font-weight:500;color:var(--ink-3)} .clm .cmx-link.danger{color:var(--crit)}
.clm .cmx-link:disabled{opacity:.4;pointer-events:none}
.clm .cmx-confirm{display:inline-flex;align-items:center;gap:10px;color:var(--ink-2)}
`;
