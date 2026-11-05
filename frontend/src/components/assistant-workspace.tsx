"use client";

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, History, MessageSquare, Paperclip, RefreshCw, Plus, Wrench, Quote, ShieldAlert, Check, X, Sparkles, Wand2, ChevronRight, ChevronDown, ArrowUp, FileText, ShieldCheck, ListChecks, PanelLeft, Upload, Search, Pencil } from "lucide-react";
import {
  assistantApi,
  contractsApi,
  promptsApi,
  approvalsApi,
  renewalsApi,
} from "@/lib/endpoints";
import { apiStream, HttpError } from "@/lib/api";
import { clearHandoff, LEGAL_QUESTION_SESSION_TYPE, readHandoff } from "@/lib/legal-question";
import { runNoticeFor, type RunNotice } from "@/lib/assistant-run";
import { Badge, Button, CenterSpinner, Input, Modal, Select, Spinner } from "@/components/ui";
import { ContractDocument } from "@/components/contract-document";
import { Markdown } from "@/components/markdown";
import { ScopePicker, scopeContractIds, scopePrefix, splitScope } from "@/components/ask-scope";
import { ImportContractModal } from "@/components/import-contract-modal";
import { useLayout } from "@/lib/layout";
import { cn, fmtRelative, riskBand, titleCase } from "@/lib/utils";
import { dueText } from "@/lib/approval-due";
import { isExpiringSoon } from "@/lib/expiry";
import { useToast } from "@/components/toast";
import { useAuth } from "@/lib/auth";
import type {
  AssistantSession,
  Citation,
  IntakeRequest,
  ContractEditResponse,
  ContractResponse,
  Prompt,
} from "@/lib/types";

// Guards against duplicate React keys when a list source contains repeats
// (e.g. a contract surfaced in both "recent" and "all", or a refetch race).
function uniqById<T extends { id: string }>(arr: readonly T[] | undefined): T[] {
  const seen = new Map<string, T>();
  for (const item of arr ?? []) {
    if (!seen.has(item.id)) seen.set(item.id, item);
  }
  return [...seen.values()];
}

type TimelineBlock =
  | { type: "content"; text: string }
  | {
      type: "tool";
      name: string;
      status: "running" | "done" | "error";
      artifact?: {
        artifact_type?: string;
        contract_id?: string;
        edits?: number;
        summary?: string;
        request_id?: string;
        request_ref?: string;
      };
    };

interface ChatItem {
  id: string;
  role: "user" | "assistant" | "tool" | "system";
  text: string;
  citations?: Citation[];
  tool?: {
    name: string;
    status: "running" | "done" | "error";
    toolUseId?: string;
    result?: Record<string, unknown> | null;
  };
  workflow?: { id: string; name: string };
  blocks?: TimelineBlock[];
}

function extractBlocks(
  meta: Record<string, unknown> | null | undefined,
): TimelineBlock[] | undefined {
  const b = (meta as { blocks?: unknown } | null | undefined)?.blocks;
  return Array.isArray(b) && b.length ? (b as TimelineBlock[]) : undefined;
}
interface PendingConfirmation {
  confirmationId: string;
  assistantRunId: string;
  toolName: string;
  /** The action's arguments (server-sanitised), shown so nothing is approved blind. */
  details: Record<string, string>;
}

function confirmationDetails(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== "object") return {};
  return Object.fromEntries(
    Object.entries(raw as Record<string, unknown>).filter(
      (e): e is [string, string] => typeof e[1] === "string" && e[1].length > 0,
    ),
  );
}
interface PickerPlaybookVersion {
  playbook_version_id: string;
  version_number: number;
  status: string;
  summary: string | null;
  is_current: boolean;
}
interface PickerPlaybook {
  playbook_id: string;
  name: string;
  status: string;
  description: string | null;
  versions: PickerPlaybookVersion[];
}
type DocTabKind = "draft" | "redline" | "document";
interface DocTab {
  id: string;
  contractId: string;
  kind: DocTabKind;
}

/** How a hand-off (e.g. Legal Intake) wants its new conversation created. */
type ConversationOpts = {
  sessionType: string;
  title: string;
  contractId: string | null;
  /** sessionStorage hand-off to clear once the session exists. */
  handoffKey?: string;
};

export function AssistantWorkspace() {
  const params = useSearchParams();
  const router = useRouter();
  const contractParam = params.get("contract");
  const sessionParam = params.get("session");
  const workflowParam = params.get("workflow");
  // Hand-off key from Legal Intake's "General legal question" form.
  const legalQuestionParam = params.get("legal_question");
  const qc = useQueryClient();
  const { notify } = useToast();
  const { setForceCollapsed } = useLayout();
  const { user } = useAuth();
  const firstName = (user?.full_name ?? "").trim().split(/\s+/)[0] || "there";
  const greeting = (() => {
    const h = new Date().getHours();
    if (h < 12) return "Good morning";
    if (h < 18) return "Good afternoon";
    return "Good evening";
  })();
  // The composer's hint rotates through real examples while it is empty.
  const [phIdx, setPhIdx] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setPhIdx((i) => (i + 1) % ASK_PLACEHOLDERS.length), 3800);
    return () => clearInterval(t);
  }, []);
  const [activeSession, setActiveSession] = useState<AssistantSession | null>(
    null,
  );
  const [items, setItems] = useState<ChatItem[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [pending, setPending] = useState<PendingConfirmation | null>(null);
  const [workflow, setWorkflow] = useState<Prompt | null>(null);
  const [wfModalOpen, setWfModalOpen] = useState(false);
  const [docPickerOpen, setDocPickerOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  // What a new question is about: empty = the whole portfolio, else only these requests.
  const [scopeReqs, setScopeReqs] = useState<IntakeRequest[]>([]);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  // The box grows with what is typed, up to a limit.
  useEffect(() => {
    const t = composerRef.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = `${Math.min(t.scrollHeight, 224)}px`;
  }, [input]);
  const openChats = () => setRailOpen(true);
  const [generatedDocs, setGeneratedDocs] = useState<
    { id: string; contractId: string; kind: "draft" | "redline" }[]
  >([]);
  const [tabs, setTabs] = useState<DocTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  const [playbookPicker, setPlaybookPicker] = useState<
    PickerPlaybook[] | null
  >(null);
  const [citeHighlight, setCiteHighlight] = useState<string | null>(null);
  const abortRef = useRef<(() => void) | null>(null);
  // The answer being watched. Answers are produced by a background worker, so
  // the page only *watches* them: a dropped connection reconnects and carries
  // on after the last event seen; leaving the page doesn't stop the answer.
  const runRef = useRef<string | null>(null);
  const lastEventIdRef = useRef<string | undefined>(undefined);
  const endedRef = useRef(false);
  const reconnectsRef = useRef(0);
  const lastSentRef = useRef<string | null>(null);
  const [stopping, setStopping] = useState(false);
  // Shown under the chat when its latest answer didn't finish.
  const [runNotice, setRunNotice] = useState<RunNotice | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const sessRef = useRef<string | null>(null);

  function stopWatch() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  function abortActiveStream() {
    abortRef.current?.();
    abortRef.current = null;
  }

  // Read an answer's live events (sending, resuming, or watching one already
  // running). On a dropped connection, reconnect a few times and continue
  // after the last event received.
  async function follow(path: string, body: unknown) {
    abortActiveStream();
    endedRef.current = false;
    const sessionAtStart = sessRef.current;
    let aborted = false;
    const abort = await apiStream(path, body, {
      onEvent: (event, data, id) => {
        if (aborted || sessRef.current !== sessionAtStart) return;
        if (id) lastEventIdRef.current = id;
        handleEvent(event, data);
      },
      onError: (err) => {
        if (!aborted && sessRef.current === sessionAtStart) handleStreamError(err);
      },
      onClose: () => {
        if (aborted || sessRef.current !== sessionAtStart) return;
        const runId = runRef.current;
        if (!endedRef.current && runId && reconnectsRef.current < 5) {
          reconnectsRef.current += 1;
          const wait = 1000 * reconnectsRef.current;
          setTimeout(() => {
            if (sessRef.current !== sessionAtStart || endedRef.current) return;
            void watchRun(runId, lastEventIdRef.current);
          }, wait);
          return;
        }
        setStreaming(false);
        setStopping(false);
      },
    });
    abortRef.current = () => {
      aborted = true;
      abort();
    };
  }

  function watchRun(runId: string, after?: string) {
    runRef.current = runId;
    const q = after ? `?after=${encodeURIComponent(after)}` : "";
    return follow(`/assistant/runs/${runId}/events${q}`, null);
  }

  // Before sending: forget the previous answer's stream position.
  function beginTurn(text: string | null, runId: string | null = null) {
    runRef.current = runId;
    lastEventIdRef.current = undefined;
    reconnectsRef.current = 0;
    lastSentRef.current = text;
    setRunNotice(null);
    setStopping(false);
  }

  async function stopAnswer() {
    const runId = runRef.current;
    if (!runId || endedRef.current) {
      abortActiveStream();
      setStreaming(false);
      return;
    }
    setStopping(true);
    try {
      // The worker stops at its next check, saves what it has and sends
      // "done", which ends this stream as usual.
      await assistantApi.cancelRun(runId);
    } catch (e) {
      setStopping(false);
      notify(e instanceof Error ? e.message : "Couldn't stop the answer", "error");
    }
  }

  // After a chat loads (or an answer ends): restore the confirmation card,
  // keep watching an answer that is still being produced, or explain one that
  // didn't finish.
  async function restoreSessionState(
    sessionId: string,
    msgs: { id: string; role: string; content: string }[],
    opts: { follow: boolean },
  ) {
    try {
      const res = await assistantApi.session(sessionId);
      if (sessRef.current !== sessionId) return; // switched chats meanwhile
      const pc = res.pending_confirmation;
      setPending(
        pc
          ? {
              confirmationId: pc.confirmation_id,
              assistantRunId: pc.assistant_run_id,
              toolName: pc.tool_name,
              details: confirmationDetails(pc.details),
            }
          : null,
      );
      const run = res.latest_run;
      if (run?.status === "running" && opts.follow) {
        beginTurn(null, run.id);
        setStreaming(true);
        startWatch(sessionId);
        await watchRun(run.id);
        return;
      }
      setRunNotice(runNoticeFor(run, msgs));
    } catch {
      /* the chat itself already loaded; these are conveniences */
    }
  }

  async function syncFromServer(sessionId: string) {
    try {
      const msgs = await assistantApi.messages(sessionId);
      if (sessRef.current !== sessionId) return msgs;
      if (msgs.length) {
        setItems(
          msgs.map((m) => ({
            id: m.id,
            role: m.role,
            text: m.content,
            citations: m.citations ?? undefined,
            blocks:
              m.role === "assistant"
                ? extractBlocks(m.metadata_json)
                : undefined,
          })),
        );
      }
      return msgs;
    } catch {
      return null;
    }
  }

  // Safety net: the SSE reader can stall behind buffering middleware. Poll the
  // persisted messages so the final answer always renders and "Thinking…"
  // always clears, even if no live deltas arrive.
  function startWatch(sessionId: string) {
    stopWatch();
    const startedAt = Date.now();
    pollRef.current = setInterval(async () => {
      if (Date.now() - startedAt > 300_000) {
        stopWatch();
        setStreaming(false);
        return;
      }
      try {
        const msgs = await assistantApi.messages(sessionId);
        const freshAssistant = msgs.some(
          (m) =>
            m.role === "assistant" &&
            new Date(m.created_at).getTime() >= startedAt - 2000,
        );
        if (freshAssistant) {
          stopWatch();
          abortActiveStream();
          setItems(
            msgs.map((m) => ({
              id: m.id,
              role: m.role,
              text: m.content,
              citations: m.citations ?? undefined,
              blocks:
                m.role === "assistant"
                  ? extractBlocks(m.metadata_json)
                  : undefined,
            })),
          );
          setStreaming(false);
        }
      } catch {
        /* keep polling */
      }
    }, 2500);
  }

  const [chatSearch, setChatSearch] = useState("");
  const [debouncedChatSearch, setDebouncedChatSearch] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedChatSearch(chatSearch.trim()), 250);
    return () => clearTimeout(t);
  }, [chatSearch]);

  const { data: sessions, isLoading } = useQuery({
    queryKey: ["assistant-sessions", debouncedChatSearch],
    queryFn: () =>
      assistantApi.sessions({
        ...(debouncedChatSearch ? { q: debouncedChatSearch } : {}),
      }),
  });

  const [renamingSessionId, setRenamingSessionId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  async function commitRename(id: string) {
    const value = renameValue.trim();
    setRenamingSessionId(null);
    if (!value) return;
    await assistantApi.updateSession(id, { title: value });
    qc.invalidateQueries({ queryKey: ["assistant-sessions"] });
  }
  const { data: allWorkflows } = useQuery({
    queryKey: ["workflows"],
    queryFn: promptsApi.list,
    enabled: !!workflowParam,
  });
  // Home "Needs your attention" — real portfolio data, fetched only on the
  // empty landing (not while a chat is open).
  const homeActive = !activeSession;
  const { data: homeApprovals } = useQuery({
    queryKey: ["home-approvals"],
    queryFn: () => approvalsApi.list(),
    enabled: homeActive,
  });
  const { data: homeRenewals } = useQuery({
    queryKey: ["home-renewals"],
    queryFn: () => renewalsApi.list(),
    enabled: homeActive,
  });
  const { data: homeContracts } = useQuery({
    queryKey: ["contracts-list"],
    queryFn: contractsApi.list,
    enabled: homeActive,
  });
  const attention = useMemo(() => {
    const out: { text: string; pill: string; due: boolean; href: string }[] = [];
    const waiting = (homeApprovals ?? []).filter(
      (a) => a.status === "pending" || a.status === "waiting",
    );
    if (waiting.length)
      out.push({
        text: `${waiting.length} approval${waiting.length === 1 ? "" : "s"} waiting on the team`,
        pill: "approvals",
        due: false,
        href: "/approvals",
      });
    for (const r of (homeRenewals ?? [])
      .filter((r) => r.decision === "undecided")
      .slice(0, 3)) {
      out.push({
        text: `${r.contract_title ?? "Contract"} — renewal decision`,
        pill: "renewal",
        due: true,
        href: "/renewals",
      });
    }
    return out.slice(0, 3);
  }, [homeApprovals, homeRenewals]);
  const recents = (sessions ?? []).slice(0, 3);
  const deskApprovals = (homeApprovals ?? []).filter((a) => a.status === "pending" || a.status === "waiting").length;
  const deskRenewals = (homeRenewals ?? []).filter((r) => r.decision === "undecided").length;

  // Seed an assistant workflow when arrived via /assistant?workflow=<id>.
  useEffect(() => {
    if (!workflowParam || workflow || !allWorkflows) return;
    const wf = allWorkflows.find((w) => w.id === workflowParam);
    if (wf) setWorkflow(wf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowParam, allWorkflows]);

  // Pre-fill the composer with a variable-filled prompt handed over from the
  // Prompt Library ("Use in Assistant" after filling in {{variables}}).
  useEffect(() => {
    try {
      const pre = sessionStorage.getItem("aegis-prompt-prefill");
      if (pre) {
        setInput(pre);
        sessionStorage.removeItem("aegis-prompt-prefill");
      }
    } catch {
      /* ignore storage errors */
    }
  }, []);

  const sessionContractId =
    activeSession?.contract_id ?? contractParam ?? null;

  // One tab per contract (Mike-style upsert). If nothing is explicitly
  // open but the session is contract-scoped, show that contract.
  const effectiveTabs: DocTab[] = useMemo(() => {
    if (tabs.length) return tabs;
    return sessionContractId
      ? [
          {
            id: sessionContractId,
            contractId: sessionContractId,
            kind: "document" as DocTabKind,
          },
        ]
      : [];
  }, [tabs, sessionContractId]);
  const activeTab =
    effectiveTabs.find((t) => t.id === activeTabId) ??
    effectiveTabs[effectiveTabs.length - 1] ??
    null;
  const docContractId = activeTab?.contractId ?? null;
  const activeContractId = docContractId;

  function upsertTab(contractId: string, kind: DocTabKind) {
    setTabs((prev) => {
      const idx = prev.findIndex((t) => t.id === contractId);
      if (idx >= 0) {
        // Don't downgrade a redline tab to a plain view.
        const keepKind =
          prev[idx].kind === "redline" && kind === "document"
            ? "redline"
            : kind;
        const copy = prev.slice();
        copy[idx] = { ...copy[idx], kind: keepKind };
        return copy;
      }
      return [...prev, { id: contractId, contractId, kind }];
    });
    setActiveTabId(contractId);
  }

  function closeTab(contractId: string) {
    setTabs((prev) => {
      const idx = prev.findIndex((t) => t.id === contractId);
      const next = prev.filter((t) => t.id !== contractId);
      if (activeTabId === contractId)
        setActiveTabId(
          (next[idx] ?? next[idx - 1] ?? next[next.length - 1])?.id ?? null,
        );
      return next;
    });
  }

  const { data: openDocEdits } = useQuery({
    queryKey: ["contract", activeTab?.contractId, "edits"],
    enabled: !!activeTab && activeTab.kind === "redline",
    queryFn: () => contractsApi.edits(activeTab!.contractId),
  });

  // The contract that has a redline in THIS conversation. Drives the
  // inline accept/reject in the chat — independent of which doc tab is
  // active (Mike keeps the edit cards in the conversation, not the panel).
  const redlineCid = useMemo(() => {
    const g = [...generatedDocs]
      .reverse()
      .find((d) => d.kind === "redline");
    return (
      g?.contractId ??
      (activeTab?.kind === "redline" ? activeTab.contractId : null) ??
      null
    );
  }, [generatedDocs, activeTab]);

  const { data: inlineEdits } = useQuery({
    queryKey: ["contract", redlineCid, "edits"],
    enabled: !!redlineCid,
    queryFn: () => contractsApi.edits(redlineCid as string),
  });

  async function decideEdit(
    contractId: string,
    editId: string,
    accept: boolean,
  ) {
    try {
      if (accept) await contractsApi.acceptEdit(contractId, editId);
      else await contractsApi.rejectEdit(contractId, editId);
      qc.invalidateQueries({ queryKey: ["contract", contractId, "edits"] });
      qc.invalidateQueries({ queryKey: ["contract", contractId] });
      notify(
        accept
          ? "Edit accepted. The contract updates once every suggested change on it has been decided."
          : "Edit rejected",
        "success",
      );
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed", "error");
    }
  }

  function handleCite(cid: string | undefined, q: string) {
    setCiteHighlight(q);
    // Open (or focus) the cited contract as a tab. upsertTab keeps an
    // existing redline tab's kind so highlighting doesn't hide edits.
    if (cid) upsertTab(cid, "document");
  }

  // Collapse the main nav to an icon rail only while a contract document pane
  // is open (it needs the room). The plain chat home keeps the full nav.
  useEffect(() => {
    setForceCollapsed(!!activeContractId);
    return () => setForceCollapsed(false);
  }, [activeContractId, setForceCollapsed]);

  useEffect(() => {
    const last = items[items.length - 1];
    if (last?.role === "user") {
      // Mike-style: float the new question to the top so the answer
      // renders from the top of the viewport, not pinned to the bottom.
      requestAnimationFrame(() => {
        const els = document.querySelectorAll("[data-user-msg]");
        const el = els[els.length - 1] as HTMLElement | undefined;
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        else bottomRef.current?.scrollIntoView({ behavior: "smooth" });
      });
    } else {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [items]);

  useEffect(() => {
    return () => {
      endedRef.current = true; // no reconnect after leaving the page
      stopWatch();
      abortActiveStream();
    };
  }, []);

  useEffect(() => {
    if (activeSession) return;
    if (sessionParam) {
      assistantApi
        .session(sessionParam)
        .then((res) => selectSession(res.session))
        .catch((e) =>
          notify(e instanceof Error ? e.message : "Could not load this chat", "error"),
        );
    } else if (contractParam) {
      assistantApi
        .createSession({
          session_type: "contract",
          contract_id: contractParam,
          title: "Contract review",
        })
        .then((s) => {
          qc.invalidateQueries({ queryKey: ["assistant-sessions"] });
          void selectSession(s);
        })
        .catch((e) =>
          notify(e instanceof Error ? e.message : "Could not start assistant", "error"),
        );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionParam, contractParam]);

  // Legal Intake hand-off: /assistant?legal_question=<key>. The question waits
  // in sessionStorage under that key; start a NEW legal-question chat with it as
  // the first message. The entry is only cleared once that chat exists, so a
  // failed start keeps the question (see startConversation). The ref keeps React
  // StrictMode's double effect run from starting two chats.
  const handoffRef = useRef<string | null>(null);
  // Hand-off settings to reuse when a failed start is retried from the composer.
  const [pendingHandoff, setPendingHandoff] = useState<ConversationOpts | null>(null);
  useEffect(() => {
    if (!legalQuestionParam || activeSession || handoffRef.current === legalQuestionParam) return;
    handoffRef.current = legalQuestionParam;
    let h: ReturnType<typeof readHandoff> = null;
    try {
      h = readHandoff(legalQuestionParam);
    } catch {
      h = null; // storage blocked
    }
    if (!h) {
      window.history.replaceState(null, "", "/assistant");
      notify("This question link has expired or was already used. Start again from Legal Intake, or ask below.", "error");
      return;
    }
    const opts: ConversationOpts = {
      sessionType: LEGAL_QUESTION_SESSION_TYPE,
      title: h.title,
      contractId: h.contractId,
      handoffKey: legalQuestionParam,
    };
    setPendingHandoff(opts);
    void startConversation(h.message, opts);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [legalQuestionParam]);

  async function selectSession(s: AssistantSession) {
    stopWatch();
    abortActiveStream();
    sessRef.current = s.id;
    setActiveSession(s);
    // Keep the URL in sync with the active session so reload / back / share
    // land on the same chat.
    const sp = new URLSearchParams();
    const cid = s.contract_id ?? contractParam;
    if (cid) sp.set("contract", cid);
    sp.set("session", s.id);
    router.replace(`/assistant?${sp.toString()}`, { scroll: false });
    setItems([]);
    setPending(null);
    setStreaming(false);
    setGeneratedDocs([]);
    setTabs([]);
    setActiveTabId(null);
    setPlaybookPicker(null);
    setCiteHighlight(null);
    setRunNotice(null);
    setStopping(false);
    runRef.current = null;
    endedRef.current = true; // cancels a pending reconnect to the previous chat
    let msgs: Awaited<ReturnType<typeof assistantApi.messages>> = [];
    try {
      msgs = await assistantApi.messages(s.id);
      if (sessRef.current !== s.id) return;
      setItems(
        msgs.map((m) => ({
          id: m.id,
          role: m.role,
          text: m.content,
          citations: m.citations ?? undefined,
          blocks:
            m.role === "assistant"
              ? extractBlocks(m.metadata_json)
              : undefined,
        })),
      );
    } catch {
      /* ignore */
    }
    // Only after the stored messages are shown: a replayed answer appends to them.
    void restoreSessionState(s.id, msgs, { follow: true });
  }

  async function newSession() {
    const s = await assistantApi.createSession({
      session_type: "general",
      title: "New conversation",
    });
    qc.invalidateQueries({ queryKey: ["assistant-sessions"] });
    void selectSession(s);
  }

  // Legora-style: from the opening screen, type → spin up a session and stream.
  // `opts` is used by hand-offs (Legal Intake): they bring their own session
  // type, title and contract, and pin the URL to the new session so a refresh
  // reopens this chat instead of re-sending the question.
  async function startConversation(text: string, opts?: ConversationOpts) {
    const t = text.trim();
    if (!t || streaming) return;
    const cid = opts ? (opts.contractId ?? undefined) : (activeContractId ?? undefined);
    setInput("");
    try {
      const s = await assistantApi.createSession(
        opts
          ? { session_type: opts.sessionType, title: opts.title }
          : { session_type: cid ? "contract" : "general", contract_id: cid, title: t.slice(0, 60) },
      );
      qc.invalidateQueries({ queryKey: ["assistant-sessions"] });
      // The chat now exists: the hand-off has done its job.
      if (opts?.handoffKey) {
        try {
          clearHandoff(opts.handoffKey);
        } catch {
          /* storage blocked — the entry expires on its own */
        }
      }
      setPendingHandoff(null);
      sessRef.current = s.id;
      setActiveSession(s);
      // Pin the chat in the URL so a refresh mid-answer reopens it (and keeps
      // watching the answer) instead of landing on the empty screen.
      const sp = new URLSearchParams();
      if (cid) sp.set("contract", cid);
      sp.set("session", s.id);
      window.history.replaceState(null, "", `/assistant?${sp.toString()}`);
      setItems([{ id: crypto.randomUUID(), role: "user", text: t }]);
      setStreaming(true);
      startWatch(s.id);
      beginTurn(t);
      const scoped = cid ? [] : scopeReqs;
      await follow(`/assistant/sessions/${s.id}/stream`, {
        message: scopePrefix(scoped) + t,
        contract_ids: cid ? [cid] : scopeContractIds(scoped),
      });
    } catch (e) {
      // Only reached when the chat could not be created (streaming errors are
      // reported through onError). Put the text back so nothing typed — or
      // handed over from Legal Intake — is lost, and say how to retry.
      setInput(t);
      const reason = e instanceof Error ? e.message : "Could not start chat";
      notify(
        opts?.handoffKey
          ? `Couldn't start the chat (${reason}). Your question is in the box below — press send to try again.`
          : `Couldn't start the chat (${reason}). Your message is still in the box — try again.`,
        "error",
      );
    }
  }

  function handleEvent(event: string, data: Record<string, unknown>) {
    if (event === "session_started") {
      if (typeof data.assistant_run_id === "string") runRef.current = data.assistant_run_id;
    } else if (event === "message_delta") {
      const text = String(data.text ?? "");
      setItems((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.role === "assistant" && !last.tool)
          return [...prev.slice(0, -1), { ...last, text: last.text + text }];
        return [...prev, { id: crypto.randomUUID(), role: "assistant", text }];
      });
    } else if (event === "tool_started") {
      const toolUseId =
        typeof data.tool_use_id === "string" ? data.tool_use_id : undefined;
      setItems((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "tool",
          text: String(data.tool_name ?? "tool"),
          tool: {
            name: String(data.tool_name ?? "tool"),
            status: "running",
            toolUseId,
          },
        },
      ]);
    } else if (event === "tool_finished") {
      const toolUseId =
        typeof data.tool_use_id === "string" ? data.tool_use_id : undefined;
      const result =
        data.result && typeof data.result === "object"
          ? (data.result as Record<string, unknown>)
          : null;
      setItems((prev) =>
        prev.map((it) =>
          it.tool &&
          it.tool.status === "running" &&
          (!toolUseId || it.tool.toolUseId === toolUseId)
            ? { ...it, tool: { ...it.tool, status: data.error ? "error" : "done", result } }
            : it,
        ),
      );
    } else if (event === "citation") {
      setItems((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.role === "assistant")
          return [
            ...prev.slice(0, -1),
            { ...last, citations: [...(last.citations ?? []), data as Citation] },
          ];
        return prev;
      });
    } else if (event === "contract_generated") {
      const cid = String(data.contract_id ?? "");
      if (cid) {
        setGeneratedDocs((p) =>
          p.some((d) => d.contractId === cid && d.kind === "draft")
            ? p
            : [
                ...p,
                { id: crypto.randomUUID(), contractId: cid, kind: "draft" },
              ],
        );
        upsertTab(cid, "draft");
      }
    } else if (event === "tracked_change_created") {
      const cid = String(data.contract_id ?? "");
      if (cid) {
        setGeneratedDocs((p) =>
          p.some((d) => d.contractId === cid && d.kind === "redline")
            ? p
            : [
                ...p,
                { id: crypto.randomUUID(), contractId: cid, kind: "redline" },
              ],
        );
        upsertTab(cid, "redline");
        qc.invalidateQueries({ queryKey: ["contract", cid, "edits"] });
      }
    } else if (event === "playbooks_offered") {
      const pbs = (data.playbooks as PickerPlaybook[]) ?? [];
      if (pbs.length) setPlaybookPicker(pbs);
    } else if (event === "confirmation_required") {
      stopWatch();
      setPending({
        confirmationId: String(data.confirmation_id),
        assistantRunId: String(data.assistant_run_id),
        toolName: String(data.tool_name),
        details: confirmationDetails(data.details),
      });
      setStreaming(false);
    } else if (event === "error") {
      stopWatch();
      notify(String(data.message ?? "Assistant error"), "error");
      setStreaming(false);
    } else if (event === "done") {
      endedRef.current = true;
      reconnectsRef.current = 0;
      stopWatch();
      setStreaming(false);
      setStopping(false);
      const sid = sessRef.current;
      if (sid)
        void syncFromServer(sid).then((msgs) => {
          if (msgs) void restoreSessionState(sid, msgs, { follow: false });
        });
    }
  }

  function handleStreamError(err: Error) {
    // A network drop mid-answer: the answer keeps going on the server and
    // onClose reconnects. Only an error the server sent ends the turn here.
    if (!(err instanceof HttpError) && runRef.current && !endedRef.current) return;
    endedRef.current = true;
    stopWatch();
    setStreaming(false);
    setStopping(false);
    notify(err.message || "Assistant stream failed", "error");
    // Not started (e.g. still answering the last message, or rate-limited):
    // show what the server really has and give the text back to edit/resend.
    if (!runRef.current && lastSentRef.current) {
      const text = lastSentRef.current;
      setInput((cur) => cur || text);
      if (sessRef.current) void syncFromServer(sessRef.current);
    }
  }

  async function submitMessage(text: string, wf?: Prompt | null) {
    if (!activeSession || !text.trim() || streaming) return;
    const message = text.trim();
    setItems((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        role: "user",
        text: message,
        workflow: wf ? { id: wf.id, name: wf.name } : undefined,
      },
    ]);
    setStreaming(true);
    sessRef.current = activeSession.id;
    startWatch(activeSession.id);
    beginTurn(message);
    const wfBody = wf ? workflowPrompt(wf) : "";
    const wfPrompt = wf && wfBody ? `[Prompt: ${wf.name}]\n${wfBody}\n\n` : "";
    const scoped = activeContractId ? [] : scopeReqs;
    await follow(`/assistant/sessions/${activeSession.id}/stream`, {
      message: scopePrefix(scoped) + wfPrompt + message,
      contract_ids: activeContractId ? [activeContractId] : scopeContractIds(scoped),
      ...(wf ? { workflow_id: wf.id } : {}),
    });
  }

  async function send() {
    if (!activeSession || !input.trim() || streaming) return;
    const wf = workflow;
    const text = input.trim();
    setInput("");
    setWorkflow(null);
    await submitMessage(text, wf);
  }

  async function resolveConfirmation(approve: boolean) {
    if (!pending) return;
    const p = pending;
    setPending(null);
    try {
      if (approve) {
        await assistantApi.confirm(p.confirmationId);
        setItems((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "system",
            text: `Confirmed ${titleCase(p.toolName)} — resuming…`,
          },
        ]);
        setStreaming(true);
        if (sessRef.current) startWatch(sessRef.current);
        beginTurn(null, p.assistantRunId);
        await follow(
          `/assistant/runs/${p.assistantRunId}/resume?confirmation_id=${p.confirmationId}`,
          {},
        );
      } else {
        await assistantApi.reject(p.confirmationId, "Rejected by user");
        // The server closes the turn with a "Cancelled — nothing was changed"
        // reply; show the stored conversation so it matches what a reload shows.
        if (sessRef.current) await syncFromServer(sessRef.current);
      }
    } catch (e) {
      notify(e instanceof Error ? e.message : "Confirmation failed", "error");
      // e.g. it expired: the server has closed the turn with an explanation —
      // show the stored conversation instead of leaving a request with no reply.
      if (sessRef.current) await syncFromServer(sessRef.current);
    }
  }

  const hasDoc = !!activeContractId;

  // The chat list: one body, shown in the docked column on wide screens and in the drawer on narrow ones.
  const chatBody = (afterPick: () => void) => (
    <>
      <div className="px-4 pb-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            value={chatSearch}
            onChange={(e) => setChatSearch(e.target.value)}
            placeholder="Search chats"
            style={{ outline: "none" }}
            className="h-10 w-full rounded-xl border border-slate-200 bg-slate-100 pl-9 pr-3 text-[13px] text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-brand-400 focus:ring-2 focus:ring-brand-500/15"
          />
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-3 pb-4">
        {isLoading ? (
          <CenterSpinner />
        ) : sessions?.length ? (
          chatGroups(uniqById(sessions)).map((g) => (
            <div key={g.label}>
              <p className="px-2 pb-1 pt-3 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-slate-400">{g.label}</p>
              {g.items.map((s, i) => (
                <div key={s.id} className="aa-up group relative mb-0.5" style={{ animationDelay: `${Math.min(i, 8) * 25}ms` }}>
                  {renamingSessionId === s.id ? (
                    <input
                      autoFocus
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onBlur={() => void commitRename(s.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") e.currentTarget.blur();
                        if (e.key === "Escape") setRenamingSessionId(null);
                      }}
                      style={{ outline: "none" }}
                      className="w-full rounded-xl border border-brand-300 bg-slate-100 px-3 py-2.5 text-[13px] font-medium text-slate-800 outline-none"
                    />
                  ) : (
                    <button
                      onClick={() => { void selectSession(s); afterPick(); }}
                      className={cn("flex w-full items-center gap-3 rounded-xl px-2.5 py-2 pr-9 text-left transition-colors", activeSession?.id === s.id ? "bg-brand-50" : "hover:bg-slate-100")}
                    >
                      <span className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg", s.contract_id ? "bg-info/15 text-info" : "bg-slate-200 text-slate-500")}>
                        {s.contract_id ? <FileText className="h-4 w-4" /> : <MessageSquare className="h-4 w-4" />}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] font-medium text-slate-800">{s.title ?? "Untitled"}</span>
                        <span className="block text-[11.5px] text-slate-400">{fmtRelative(s.updated_at)}</span>
                      </span>
                    </button>
                  )}
                  {renamingSessionId !== s.id && (
                    <button
                      onClick={(e) => { e.stopPropagation(); setRenameValue(s.title ?? ""); setRenamingSessionId(s.id); }}
                      title="Rename chat"
                      aria-label="Rename chat"
                      className="absolute right-2 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-lg text-slate-400 opacity-0 transition hover:bg-slate-200 hover:text-slate-700 group-hover:opacity-100 focus:opacity-100"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          ))
        ) : (
          <p className="p-6 text-center text-sm text-slate-400">No chats yet.</p>
        )}
      </div>
    </>
  );

  return (
    <div className="flex h-full flex-col bg-slate-50">
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* Conversation column */}
        <section className="relative flex min-w-0 flex-1 flex-col">
          {/* Top bar */}
          <div className="relative z-20 flex shrink-0 items-center gap-2 px-5 pb-1 pt-4">
            <style dangerouslySetInnerHTML={{ __html: AA_CSS }} />
            <button onClick={openChats} className="aa-btn"><History className="h-4 w-4" />Chat history</button>
            <button onClick={newSession} className="aa-btn aa-btn-pri"><Plus className="h-4 w-4" />New chat</button>
            {activeContractId && (
              <Link href={`/contracts/${activeContractId}`} className="aa-btn ml-auto"><FileText className="h-4 w-4" />Open contract page</Link>
            )}
          </div>
          {!activeSession ? (
            <div className="aa flex-1 overflow-y-auto">
              <div className="mx-auto flex min-h-full w-full max-w-[860px] flex-col justify-center px-6 pb-6 pt-2">
                {/* the chat is the page */}
                <div className="aa-up text-center">
                  <span className="aa-orb mx-auto" aria-hidden><Sparkles className="h-5 w-5" /></span>
                  <p className="mt-5 text-[14px] font-medium text-slate-500">{greeting}, <span className="aa-grad font-semibold">{firstName}</span></p>
                  <h1 className="mt-1 text-[clamp(30px,4.6vw,48px)] font-semibold leading-[1.08] tracking-[-0.03em] text-slate-900">How can I help today?</h1>
                </div>

                {/* starters: short, under the box */}
                <div className="aa-up mx-auto mt-8 flex w-full max-w-[780px] flex-wrap justify-center gap-2" style={{ animationDelay: "100ms" }}>
                  {[
                    { I: Wand2, t: "Draft a contract", fill: "Draft a ", cls: "aa-t-violet" },
                    { I: ShieldCheck, t: "Review and redline", fill: "Review and redline ", cls: "aa-t-good" },
                    { I: Search, t: "Find in the portfolio", fill: "Which contracts ", cls: "aa-t-warn" },
                    { I: FileText, t: "Summarise a contract", fill: "Summarise ", cls: "aa-t-brand" },
                  ].map((q) => (
                    <button key={q.t} onClick={() => { setInput(q.fill); requestAnimationFrame(() => { const t = composerRef.current; t?.focus(); t?.setSelectionRange(q.fill.length, q.fill.length); }); }} className={cn("aa-starter", q.cls)}>
                      <q.I className="h-4 w-4" />{q.t}
                    </button>
                  ))}
                </div>

                {/* small, quiet: where things stand and where you left off */}
                <div className="aa-up mx-auto mt-12 flex w-full max-w-[780px] flex-wrap items-center justify-center gap-x-6 gap-y-2 text-[12.5px] text-slate-500" style={{ animationDelay: "230ms" }}>
                  <Link href="/approvals" className="aa-meta"><ShieldAlert className="h-4 w-4 text-warning" /><CountUp n={deskApprovals} className="font-semibold text-slate-800" /> approvals waiting</Link>
                  <Link href="/renewals" className="aa-meta"><RefreshCw className="h-4 w-4 text-info" /><CountUp n={deskRenewals} className="font-semibold text-slate-800" /> renewals to decide</Link>
                  <Link href="/contracts" className="aa-meta"><FileText className="h-4 w-4 text-brand-600" /><CountUp n={(homeContracts ?? []).length} className="font-semibold text-slate-800" /> contracts</Link>
                </div>
                {recents.length > 0 && (
                  <div className="aa-up mx-auto mt-4 flex w-full max-w-[780px] flex-wrap items-center justify-center gap-2" style={{ animationDelay: "290ms" }}>
                    <span className="text-[12px] text-slate-400">Recent</span>
                    {recents.map((c) => (
                      <button key={c.id} onClick={() => selectSession(c)} className="aa-sug max-w-[240px]">
                        <MessageSquare className="h-3.5 w-3.5 shrink-0" /><span className="truncate">{c.title || "Untitled chat"}</span>
                      </button>
                    ))}
                    <button onClick={openChats} className="text-[12px] font-medium text-brand-600 hover:underline">All chats</button>
                  </div>
                )}

              </div>
            </div>
          ) : (
              <div className="flex-1 overflow-y-auto">
                <div className="mx-auto w-full max-w-[820px] space-y-7 px-6 py-8">
                  {items.length === 0 && !streaming && (
                    <div className="flex items-center gap-2 text-sm text-slate-400">
                      <Sparkles className="h-4 w-4" />
                      No messages in this chat yet — ask a question below to
                      get started.
                    </div>
                  )}
                  {groupItems(items).map((g) => {
                    if (g.kind === "steps")
                      return (
                        <StepTrace key={g.items[0].id} steps={g.items} />
                      );
                    if (
                      g.item.role === "assistant" &&
                      g.item.blocks &&
                      g.item.blocks.length > 0
                    )
                      return (
                        <AssistantTimeline
                          key={g.item.id}
                          item={g.item}
                          onCite={handleCite}
                        />
                      );
                    return (
                      <ChatBubble
                        key={g.item.id}
                        item={g.item}
                        onReopenWorkflow={() => setWfModalOpen(true)}
                        onCite={handleCite}
                      />
                    );
                  })}
                  {runNotice && !streaming && (
                    <div
                      role="status"
                      className="flex items-center gap-3 rounded-md border border-warning/30 bg-warning-subtle px-4 py-3 text-[13.5px] text-slate-700"
                    >
                      <span className="min-w-0 flex-1">{runNotice.message}</span>
                      {runNotice.retryText && (
                        <button
                          onClick={() => {
                            const text = runNotice.retryText;
                            setRunNotice(null);
                            if (text) void submitMessage(text);
                          }}
                          className="shrink-0 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-[12.5px] font-medium text-slate-700 transition hover:text-slate-900"
                        >
                          Try again
                        </button>
                      )}
                    </div>
                  )}
                  {/* The one live status for the whole turn — names the step in
                      flight (or "Thinking…") with a Stop. No competing spinners. */}
                  {streaming &&
                    (() => {
                      const running = items.find(
                        (it) => it.tool?.status === "running",
                      )?.tool?.name;
                      const LONG_OPS = [
                        "generate_contract_docx",
                        "edit_contract",
                        "redline_against_playbook",
                        "run_playbook_review",
                        "redraft_contract",
                      ];
                      return (
                        <WorkingBeat
                          label={
                            stopping
                              ? "Stopping…"
                              : running
                                ? `${toolLabel(running, false)}…`
                                : "Thinking…"
                          }
                          hint={
                            !stopping && running && LONG_OPS.includes(running)
                              ? "this can take a minute or two"
                              : undefined
                          }
                          stopping={stopping}
                          onStop={() => void stopAnswer()}
                        />
                      );
                    })()}
                  {playbookPicker && (
                    <PlaybookPicker
                      playbooks={playbookPicker}
                      disabled={streaming}
                      onCancel={() => setPlaybookPicker(null)}
                      onRun={(pb, ver) => {
                        setPlaybookPicker(null);
                        void submitMessage(
                          `Redline this contract against the "${pb.name}" playbook ` +
                            `(playbook_id=${pb.playbook_id}, ` +
                            `playbook_version_id=${ver.playbook_version_id}). ` +
                            `Call redline_against_playbook with those exact IDs.`,
                        );
                      }}
                    />
                  )}
                  {redlineCid && (inlineEdits?.length ?? 0) > 0 && (
                    <InlineEditReview
                      edits={inlineEdits ?? []}
                      onDecide={(editId, accept) =>
                        decideEdit(redlineCid, editId, accept)
                      }
                    />
                  )}
                  {pending && (
                    <div className="rounded-md border border-warning/30 bg-warning-subtle p-4">
                      <div className="flex items-center gap-2 text-sm font-medium text-warning">
                        <ShieldAlert className="h-4 w-4" />
                        {confirmCopy(pending.toolName).title}
                      </div>
                      <p className="mt-1 text-sm text-warning">
                        {confirmCopy(pending.toolName).body}
                      </p>
                      {Object.keys(pending.details).length > 0 && (
                        <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 rounded border border-warning/20 bg-white/60 px-3 py-2 text-[12.5px]">
                          {Object.entries(pending.details).map(([k, v]) => (
                            <Fragment key={k}>
                              <dt className="font-medium text-slate-600">{titleCase(k)}</dt>
                              <dd className="whitespace-pre-line break-words text-slate-900">{v}</dd>
                            </Fragment>
                          ))}
                        </dl>
                      )}
                      <div className="mt-3 flex gap-2">
                        <Button
                          size="sm"
                          onClick={() => resolveConfirmation(true)}
                        >
                          <Check className="h-3.5 w-3.5" />
                          Approve &amp; continue
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => resolveConfirmation(false)}
                        >
                          <X className="h-3.5 w-3.5" />
                          Reject
                        </Button>
                      </div>
                    </div>
                  )}
                  {generatedDocs.length > 0 && (
                    <div className="space-y-1.5">
                      {/* Only the most recent artifact — older ones stay
                          reachable via the doc tabs / Chats. */}
                      {generatedDocs.slice(-1).map((d) => {
                        const open = activeTab?.contractId === d.contractId;
                        return (
                          <button
                            key={d.id}
                            onClick={() => upsertTab(d.contractId, d.kind)}
                            className={cn(
                              "flex w-full items-center gap-2 rounded-md border px-3.5 py-2.5 text-sm transition-colors",
                              d.kind === "redline"
                                ? "border-warning/30 bg-warning-subtle text-warning hover:bg-warning-subtle"
                                : "border-success/30 bg-success-subtle text-success hover:bg-success-subtle",
                            )}
                          >
                            <FileText className="h-4 w-4 shrink-0" />
                            <span className="flex-1 text-left font-medium">
                              {d.kind === "draft"
                                ? "Contract drafted"
                                : "Tracked-change redline ready"}
                            </span>
                            <span className="inline-flex items-center gap-1">
                              {open
                                ? "Showing"
                                : d.kind === "draft"
                                  ? "Open document"
                                  : "Review redlines"}
                              <ChevronRight className="h-3.5 w-3.5" />
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                  <div ref={bottomRef} />
                </div>
              </div>

          )}

          {/* One composer for the empty page and for every chat: the chat is always the focus */}
          <div className="relative z-10 shrink-0 px-5 pb-4 pt-2">
            <div className="mx-auto w-full max-w-[820px]">
              {workflow && (
                <div className="mb-2 flex items-center gap-2 rounded-xl border border-brand-200 bg-brand-50 px-3 py-1.5 text-xs text-brand-700">
                  <Wand2 className="h-3.5 w-3.5" />
                  <span className="font-medium">Prompt:</span>
                  {workflow.name}
                  <button onClick={() => setWorkflow(null)} aria-label="Remove prompt" className="ml-auto text-brand-500 hover:text-brand-700"><X className="h-3.5 w-3.5" /></button>
                </div>
              )}
              <div className="aa-composer">
                <div className="aa-composer-in">
                  <div className="relative">
                    <textarea
                      ref={composerRef}
                      rows={1}
                      aria-label="Ask Aegis about a contract, a clause, or your whole portfolio"
                      placeholder={
                        !activeSession
                          ? ""
                          : activeSession.session_type === LEGAL_QUESTION_SESSION_TYPE
                            ? "Add detail, ask a follow-up, or say \u201csend it to Legal\u201d…"
                            : "Ask a follow-up, or something new…"
                      }
                      value={input}
                      onChange={(e) => {
                        // "/" on an empty box opens the prompt list.
                        if (e.target.value === "/") { setInput(""); setWfModalOpen(true); return; }
                        setInput(e.target.value);
                      }}
                      onKeyDown={(e) =>
                        e.key === "Enter" &&
                        !e.shiftKey &&
                        (e.preventDefault(), activeSession ? send() : startConversation(input, pendingHandoff ?? undefined))
                      }
                      disabled={!!activeSession && (streaming || !!pending)}
                      style={{ outline: "none" }}
                      className={cn(
                        "block max-h-64 w-full resize-none bg-transparent px-6 pb-2 pt-5 leading-7 text-slate-800 outline-none placeholder:text-slate-400 focus:outline-none disabled:opacity-60",
                        activeSession ? "min-h-[76px] text-[16px]" : "min-h-[112px] text-[17px]",
                      )}
                    />
                    {!input && !activeSession && (
                      <span key={phIdx} className="aa-ph pointer-events-none absolute left-6 top-5 text-[17px] leading-7 text-slate-400">
                        {ASK_PLACEHOLDERS[phIdx]}
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-2 px-3.5 pb-3.5 pt-1">
                    <button onClick={() => setDocPickerOpen(true)} disabled={!!activeSession && (streaming || !!pending)} className="aa-tool disabled:opacity-40"><Paperclip className="h-4 w-4" />Add documents</button>
                    <button onClick={() => setWfModalOpen(true)} disabled={!!activeSession && (streaming || !!pending)} className="aa-tool disabled:opacity-40"><Wand2 className="h-4 w-4" />Prompts<kbd className="aa-kbd">/</kbd></button>
                    {!activeContractId && <ScopePicker value={scopeReqs} onChange={setScopeReqs} disabled={!!activeSession && (streaming || !!pending)} up />}
                    <span className="ml-auto hidden text-[11.5px] text-slate-400 md:inline">Enter to send</span>
                    <button
                      onClick={() => (activeSession ? send() : startConversation(input, pendingHandoff ?? undefined))}
                      aria-label="Send"
                      disabled={!input.trim() || streaming || !!pending}
                      className="aa-go !h-10 !px-5"
                    >
                      Ask <ArrowUp className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              </div>
              <p className="mt-2 text-center text-[11px] text-slate-400">Aegis can err. Verify material conclusions with counsel. This is not legal advice.</p>
            </div>
          </div>
        </section>

        {/* Document side panel (slides in) */}
        {hasDoc && activeContractId && (
          <aside className="hidden w-[44%] min-w-[26rem] shrink-0 flex-col border-l border-slate-200 bg-slate-100 xl:flex">
            <div className="flex h-11 shrink-0 items-center gap-1 overflow-x-auto border-b border-slate-200 bg-slate-50/80 px-2">
              {effectiveTabs.map((t) => {
                const on = t.id === activeTab?.id;
                const closable = tabs.some((x) => x.id === t.id);
                return (
                  <div
                    key={t.id}
                    onClick={() => setActiveTabId(t.id)}
                    className={cn(
                      "group flex shrink-0 cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors",
                      on
                        ? t.kind === "redline"
                          ? "bg-warning-subtle text-warning"
                          : t.kind === "draft"
                            ? "bg-success-subtle text-success"
                            : "bg-slate-100 text-slate-800 ring-1 ring-slate-200"
                        : "text-slate-500 hover:bg-slate-200/60",
                    )}
                  >
                    <FileText className="h-3.5 w-3.5 shrink-0" />
                    <span className="max-w-[12rem] truncate">
                      <TabTitle
                        contractId={t.contractId}
                        fallback={
                          t.kind === "redline"
                            ? "Tracked-change redline"
                            : t.kind === "draft"
                              ? "Drafted document"
                              : "Document"
                        }
                      />
                    </span>
                    {closable && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          closeTab(t.id);
                        }}
                        title="Close tab"
                        className="-mr-1 rounded p-0.5 opacity-60 hover:bg-slate-900/10 hover:opacity-100"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                );
              })}
              <Link
                href={`/contracts/${activeContractId}`}
                className="ml-auto shrink-0 px-2 text-xs text-slate-400 hover:text-brand-600"
              >
                Open full page
              </Link>
            </div>
            <div className="min-h-0 flex-1 p-3">
              {activeContractId && (
                <ContractDocument
                  key={activeContractId}
                  contractId={activeContractId}
                  edits={
                    activeTab?.kind === "redline"
                      ? (openDocEdits ?? [])
                      : undefined
                  }
                  highlightQuote={citeHighlight}
                />
              )}
            </div>
          </aside>
        )}
      </div>

      {/* Chat history drawer */}
      {railOpen && (
        <div className="fixed inset-0 z-40 flex">
          <div className="aa-scrim absolute inset-0 bg-slate-900/30 backdrop-blur-[2px]" onClick={() => setRailOpen(false)} />
          <aside className="aa-drawer relative z-10 flex h-full w-[340px] max-w-[92vw] flex-col border-r border-slate-200 bg-slate-50 shadow-2xl">
            <div className="flex items-center gap-2 px-4 pb-3 pt-4">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-50 text-brand-600"><History className="h-[18px] w-[18px]" /></span>
              <div className="min-w-0 flex-1">
                <p className="text-[14.5px] font-semibold text-slate-900">Your chats</p>
                <p className="text-[11.5px] text-slate-400">{sessions?.length ?? 0} conversations</p>
              </div>
              <button onClick={() => setRailOpen(false)} aria-label="Close chats" className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-200 hover:text-slate-700"><X className="h-4 w-4" /></button>
            </div>
            <div className="px-4 pb-3">
              <button onClick={() => { void newSession(); setRailOpen(false); }} className="aa-go w-full justify-center"><Plus className="h-4 w-4" /> New chat</button>
            </div>
            {chatBody(() => setRailOpen(false))}
          </aside>
        </div>
      )}

      <WorkflowModal
        open={wfModalOpen}
        onClose={() => setWfModalOpen(false)}
        onPick={(w) => {
          setWorkflow(w);
          setWfModalOpen(false);
        }}
      />
      <DocPickerModal
        open={docPickerOpen}
        onClose={() => setDocPickerOpen(false)}
        onPick={(c) => {
          upsertTab(c.id, "document");
          setDocPickerOpen(false);
          notify(`Attached "${c.title}" to this chat`, "success");
        }}
      />
    </div>
  );
}

type ItemGroup =
  | { kind: "bubble"; item: ChatItem }
  | { kind: "steps"; items: ChatItem[] };

// Mike-style human labels for tool steps, instead of raw tool names.
const TOOL_LABELS: Record<string, [string, string]> = {
  generate_contract_docx: ["Drafting the contract", "Drafted the contract"],
  edit_contract: [
    "Preparing tracked changes",
    "Proposed tracked changes",
  ],
  redline_against_playbook: [
    "Redlining against the playbook",
    "Redline ready",
  ],
  run_playbook_review: [
    "Reviewing against the playbook",
    "Playbook review complete",
  ],
  read_contract: ["Reading the contract", "Read the contract"],
  find_in_contract: ["Searching the contract", "Searched the contract"],
  list_contract_versions: ["Checking the versions", "Checked the versions"],
  compare_contract_versions: ["Comparing the versions", "Compared the versions"],
  ask_contract_brain: [
    "Consulting Contract Brain",
    "Consulted Contract Brain",
  ],
  query_contracts: ["Checking every contract", "Checked contracts"],
  extract_obligations: [
    "Extracting obligations",
    "Obligations extracted",
  ],
  list_playbooks: ["Looking up playbooks", "Found playbooks"],
  list_workflows: ["Looking up prompts", "Found prompts"],
  get_contract_status: [
    "Checking contract status",
    "Checked contract status",
  ],
  create_tabular_review: [
    "Building tabular review",
    "Tabular review created",
  ],
  read_table_cells: ["Reading review table", "Read review table"],
  replicate_contract_version: [
    "Duplicating the contract",
    "Contract duplicated",
  ],
  run_workflow: ["Running the prompt", "Prompt complete"],
  send_for_signature: ["Sending for signature", "Sent for signature"],
  create_intake_request: ["Raising the request", "Request raised"],
  get_intake_request: ["Looking up the request", "Found the request"],
  list_my_requests: ["Finding your requests", "Found your requests"],
  start_intake_workflow: ["Starting the workflow", "Workflow started"],
  advance_intake_workflow: ["Advancing the workflow", "Workflow advanced"],
  create_workflow: ["Building the workflow", "Workflow built"],
  decide_approval: ["Recording the decision", "Decision recorded"],
  list_my_approvals: ["Checking your approvals", "Found your approvals"],
  reassign_request: ["Reassigning the request", "Request reassigned"],
  complete_task: ["Completing the task", "Task completed"],
  advance_contract_stage: ["Advancing the contract", "Contract advanced"],
  add_contract_comment: ["Adding your comment", "Comment added"],
  send_for_negotiation: ["Sending for negotiation", "Sent for negotiation"],
  get_signature_link: ["Fetching the signing link", "Signing link ready"],
  get_signature_status: ["Checking signature status", "Checked signature status"],
  list_notices: ["Looking up notices", "Found notices"],
  create_notice: ["Logging the notice", "Notice logged"],
  draft_notice_response: ["Drafting a response", "Response drafted"],
  read_notice: ["Reading the notice", "Read the notice"],
  complete_obligation: ["Completing the obligation", "Obligation completed"],
  list_renewals: ["Looking up renewals", "Found renewals"],
  list_obligations: ["Looking up obligations", "Found obligations"],
  find_contracts: ["Searching contracts", "Found contracts"],
  my_attention_items: ["Checking what needs you", "Found your items"],
};

// Tools whose result the user should be able to open. Intake-request tools
// return {id, ref} → /intake?open=<id>; contract-read tools return {contract_id}
// → /contracts/<id>. We surface a click-through so the chat hands off to the
// real page instead of the model inventing a (dead) link.
const EXAMPLES: { label: string; icon: typeof Sparkles; prompts: string[] }[] = [
  {
    label: "Ask",
    icon: Sparkles,
    prompts: [
      "What's our exposure if a key counterparty terminates their MSA early?",
      "Which contracts have uncapped liability?",
      "Summarise the termination rights in our Globex MSA.",
    ],
  },
  {
    label: "Draft",
    icon: Wand2,
    prompts: [
      "Draft a mutual NDA with Acme Corp, 2-year term, governed by EU law.",
      "Draft a consultancy agreement for a 6-month engagement.",
      "Write a data processing addendum for a SaaS vendor.",
    ],
  },
  {
    label: "Review",
    icon: ShieldCheck,
    prompts: [
      "Find a Globex MSA and redline it against our negotiation playbook.",
      "Review the latest counterparty revision and list what changed.",
      "Check our standard NDA against the playbook.",
    ],
  },
  {
    label: "Find",
    icon: ListChecks,
    prompts: [
      "Which contracts in the portfolio auto-renew within the next 30 days?",
      "List contracts expiring this quarter with a value above 1M.",
      "Show contracts with no governing law clause.",
    ],
  },
];

const ASK_PLACEHOLDERS = [
  "Ask about a contract, a clause, or your whole portfolio…",
  "Which contracts auto-renew in the next 30 days?",
  "Summarise the termination rights in our Globex MSA…",
  "Draft a mutual NDA with Acme Corp, 2-year term…",
  "What changed in the latest counterparty revision?",
];

const AA_TONES = [
  { cls: "aa-t-brand" },
  { cls: "aa-t-violet" },
  { cls: "aa-t-good" },
  { cls: "aa-t-warn" },
];

/** Chats grouped by how recently they were touched, newest first (the list arrives newest first). */
function chatGroups(list: AssistantSession[]): { label: string; items: AssistantSession[] }[] {
  const day = 86_400_000;
  const start = new Date().setHours(0, 0, 0, 0);
  const out = new Map<string, AssistantSession[]>();
  for (const s of list) {
    const t = new Date(s.updated_at).getTime();
    const label = t >= start ? "Today" : t >= start - day ? "Yesterday" : t >= start - 7 * day ? "Previous 7 days" : "Older";
    out.set(label, [...(out.get(label) ?? []), s]);
  }
  return ["Today", "Yesterday", "Previous 7 days", "Older"].filter((l) => out.has(l)).map((l) => ({ label: l, items: out.get(l)! }));
}

// Ask Aegis landing + chats drawer: staggered rise-in, soft hover lifts, a calm focus state on the box.
// All motion is switched off for people who ask for reduced motion.
const AA_CSS = `
.aa{position:relative;background:radial-gradient(900px 340px at 50% -80px,rgb(var(--color-brand-500) / .10),transparent 70%)}
@keyframes aa-up{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
.aa-up{animation:aa-up .55s cubic-bezier(.2,.7,.2,1) both}
@keyframes aa-slide{from{transform:translateX(-24px);opacity:0}to{transform:none;opacity:1}}
.aa-drawer{animation:aa-slide .26s cubic-bezier(.2,.7,.2,1) both}
@keyframes aa-fade{from{opacity:0}to{opacity:1}}
.aa-scrim{animation:aa-fade .2s ease both}
.aa-pill{display:inline-flex;align-items:center;gap:8px;padding:5px 13px;border-radius:99px;font-size:12px;font-weight:600;color:rgb(var(--color-success));background:rgb(var(--color-success) / .12)}
.aa-pill i{width:7px;height:7px;border-radius:50%;background:currentColor;animation:aa-pulse 2s ease-in-out infinite}
@keyframes aa-pulse{50%{opacity:.35;transform:scale(.8)}}
.aa-grad{background:linear-gradient(100deg,rgb(var(--color-brand-500)),rgb(139 92 246),rgb(var(--color-brand-500)));background-size:200% 100%;-webkit-background-clip:text;background-clip:text;color:transparent;animation:aa-shift 6s ease-in-out infinite}
@keyframes aa-shift{50%{background-position:100% 0}}
.aa-box{border-radius:22px;border:1px solid rgb(var(--color-slate-200));background:rgb(var(--color-slate-100));box-shadow:0 1px 2px rgb(0 0 0 / .04),0 18px 40px -26px rgb(0 0 0 / .35);transition:border-color .2s,box-shadow .25s,transform .25s}
.aa-box:focus-within{border-color:rgb(var(--color-brand-500) / .7);box-shadow:0 0 0 4px rgb(var(--color-brand-500) / .12),0 22px 46px -26px rgb(var(--color-brand-500) / .55);transform:translateY(-1px)}
.aa-ph{animation:aa-ph .5s ease both}
@keyframes aa-ph{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
.aa-tool{display:inline-flex;align-items:center;gap:7px;height:34px;padding:0 12px;border-radius:10px;font-size:13px;font-weight:500;color:rgb(var(--color-slate-600));background:rgb(var(--color-slate-200) / .55);transition:background .15s,color .15s}
.aa-tool:hover{background:rgb(var(--color-slate-200));color:rgb(var(--color-slate-900))}
.aa-scope{align-items:center;gap:6px;height:34px;padding:0 11px;border-radius:10px;font-size:12px;font-weight:500;color:rgb(var(--color-slate-500));border:1px dashed rgb(var(--color-slate-300))}
.aa-sans,.aa-sans p,.aa-sans li,.aa-sans blockquote{font-family:var(--font-sans)}
.aa-sans p,.aa-sans li{font-size:15px;line-height:1.7}
.aa-tool-on{color:rgb(var(--color-brand-600));background:rgb(var(--color-brand-500) / .13)}
@keyframes aa-pop{from{opacity:0;transform:translateY(6px) scale(.98)}to{opacity:1;transform:none}}
.aa-pop{animation:aa-pop .18s cubic-bezier(.2,.7,.2,1) both}
.aa-wide{display:none!important}
@media (min-width:1280px){.aa-wide{display:inline-flex!important}.aa-narrow,.aa-hide-wide{display:none!important}}
.aa-btn-on{background:rgb(var(--color-brand-500) / .12);border-color:rgb(var(--color-brand-500) / .35);color:rgb(var(--color-brand-600))}
.aa-dock{animation:aa-slide .3s cubic-bezier(.2,.7,.2,1) both}
.aa-kbd{margin-left:2px;padding:0 6px;border-radius:6px;font:600 11px/18px var(--font-sans);color:rgb(var(--color-slate-500));background:rgb(var(--color-slate-100));border:1px solid rgb(var(--color-slate-300))}
.aa-seg{position:relative;display:grid;grid-template-columns:repeat(4,1fr);padding:4px;border-radius:14px;background:rgb(var(--color-slate-200) / .6)}
.aa-seg-pill{position:absolute;top:4px;bottom:4px;left:4px;width:calc((100% - 8px) / 4);border-radius:10px;background:rgb(var(--color-slate-100));box-shadow:0 1px 3px rgb(0 0 0 / .12);transform:translateX(calc(var(--i,0) * 100%));transition:transform .3s cubic-bezier(.3,.8,.2,1)}
.aa-seg-btn{position:relative;z-index:1;display:inline-flex;align-items:center;justify-content:center;gap:7px;height:36px;border-radius:10px;font-size:13px;font-weight:500;color:rgb(var(--color-slate-500));transition:color .2s}
.aa-seg-btn.on{color:rgb(var(--color-slate-900));font-weight:600}
.aa-ex{display:flex;align-items:center;gap:12px;width:100%;padding:12px 14px;border-radius:14px;border:1px solid rgb(var(--color-slate-200));background:rgb(var(--color-slate-100));font-size:14px;color:rgb(var(--color-slate-700));transition:transform .2s,border-color .2s,box-shadow .2s,color .2s}
.aa-ex:hover{transform:translateX(4px);border-color:rgb(var(--color-brand-500) / .5);color:rgb(var(--color-slate-900));box-shadow:0 10px 24px -18px rgb(var(--color-brand-500) / .7)}
.aa-ex-go{display:inline-flex;align-items:center;gap:4px;font-size:12px;font-weight:600;color:rgb(var(--color-brand-600));opacity:0;transform:translateX(-6px);transition:opacity .2s,transform .2s}
.aa-ex:hover .aa-ex-go{opacity:1;transform:none}
.aa-stat{display:inline-flex;align-items:center;gap:8px;height:38px;padding:0 15px;border-radius:99px;font-size:13px;color:rgb(var(--color-slate-600));background:rgb(var(--t) / .10);border:1px solid rgb(var(--t) / .18);transition:transform .2s,background .2s,box-shadow .2s;--t:var(--color-brand-500)}
.aa-stat svg{color:rgb(var(--t))}
.aa-stat b{color:rgb(var(--color-slate-900));font-weight:700}
.aa-stat:hover{transform:translateY(-2px);background:rgb(var(--t) / .18);box-shadow:0 10px 20px -14px rgb(var(--t) / .8)}
.aa-pi{--t:var(--color-brand-500);transition:transform .18s,border-color .18s}
.aa-pi:hover{transform:translateX(3px)}
.aa-pi .aa-ic{color:rgb(var(--t));background:rgb(var(--t) / .13)}
.aa-tile{position:relative;overflow:hidden;border-radius:22px;border:1px solid rgb(var(--color-slate-200));background:rgb(var(--color-slate-100));transition:border-color .2s,box-shadow .25s}
.aa-tile::before{content:"";position:absolute;inset:0;pointer-events:none;opacity:0;transition:opacity .3s;background:radial-gradient(260px circle at var(--mx,50%) var(--my,0%),rgb(var(--color-brand-500) / .10),transparent 62%)}
.aa-tile:hover::before{opacity:1}
.aa-tile:hover{border-color:rgb(var(--color-slate-300));box-shadow:0 18px 40px -30px rgb(0 0 0 / .4)}
.aa-tile>*{position:relative}
.aa-hero{background:linear-gradient(160deg,rgb(var(--color-brand-500) / .07),transparent 55%),rgb(var(--color-slate-100))}
.aa-sug{display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 12px;border-radius:99px;font-size:12px;color:rgb(var(--color-slate-600));background:rgb(var(--color-slate-200) / .55);transition:background .15s,color .15s,transform .15s}
.aa-sug:hover{background:rgb(var(--color-brand-500) / .14);color:rgb(var(--color-brand-600));transform:translateY(-1px)}
.aa-qs{--t:var(--color-brand-500);transition:background .15s,transform .2s,border-color .2s}
.aa-qs:hover{background:rgb(var(--color-slate-200) / .55);transform:translateX(3px)}
.aa-qs .aa-ic{color:rgb(var(--t));background:rgb(var(--t) / .13);transition:transform .25s,background .2s,color .2s}
.aa-qs:hover .aa-ic{background:rgb(var(--t));color:#fff;transform:rotate(-6deg) scale(1.06)}
.aa-qs .aa-arrow{opacity:0;transform:translateX(-6px);transition:opacity .2s,transform .2s}
.aa-qs:hover .aa-arrow{opacity:1;transform:none}
.aa-mix{display:flex;gap:3px;height:12px;border-radius:7px;overflow:hidden;background:rgb(var(--color-slate-200))}
.aa-mix i{display:block;transform-origin:left;animation:aa-grow .8s cubic-bezier(.2,.7,.2,1) both}
@keyframes aa-grow{from{transform:scaleX(0)}to{transform:none}}
.aa-orb{position:relative;display:grid;place-items:center;width:46px;height:46px;border-radius:15px;color:#fff;background:linear-gradient(135deg,rgb(var(--color-brand-500)),rgb(139 92 246));box-shadow:0 10px 26px -10px rgb(var(--color-brand-500) / .75)}
.aa-orb::after{content:"";position:absolute;inset:-6px;border-radius:19px;border:1.5px solid rgb(var(--color-brand-500) / .4);animation:aa-ping 3s ease-out infinite}
@keyframes aa-ping{0%{transform:scale(.92);opacity:.9}100%{transform:scale(1.28);opacity:0}}
.aa-composer{border-radius:26px;padding:1.5px;background:rgb(var(--color-slate-300) / .7);box-shadow:0 24px 50px -34px rgb(0 0 0 / .45);transition:box-shadow .3s,background .3s}
.aa-composer-in{border-radius:25px;background:rgb(var(--color-slate-100))}
.aa-composer:focus-within{background:linear-gradient(110deg,rgb(var(--color-brand-500)),rgb(139 92 246),rgb(56 189 248),rgb(var(--color-brand-500)));background-size:300% 100%;animation:aa-edge 6s linear infinite;box-shadow:0 26px 60px -30px rgb(var(--color-brand-500) / .7)}
@keyframes aa-edge{to{background-position:300% 0}}
.aa-starter{--t:var(--color-brand-500);display:inline-flex;align-items:center;gap:8px;height:38px;padding:0 16px;border-radius:99px;font-size:13px;font-weight:500;color:rgb(var(--color-slate-700));background:rgb(var(--color-slate-100));border:1px solid rgb(var(--color-slate-200));transition:transform .2s,border-color .2s,background .2s,box-shadow .2s}
.aa-starter svg{color:rgb(var(--t))}
.aa-starter:hover{transform:translateY(-2px);border-color:rgb(var(--t) / .55);background:rgb(var(--t) / .08);box-shadow:0 12px 22px -16px rgb(var(--t) / .8)}
.aa-meta{display:inline-flex;align-items:center;gap:7px;transition:color .15s}
.aa-meta:hover{color:rgb(var(--color-slate-900))}
.aa-go{display:inline-flex;align-items:center;gap:6px;height:38px;padding:0 16px;border-radius:11px;font-size:13.5px;font-weight:600;color:#fff;background:rgb(var(--color-brand-600));transition:transform .15s,box-shadow .2s,opacity .2s}
.aa-go:disabled{opacity:.35}
.aa-go:not(:disabled):hover{transform:translateY(-1px);box-shadow:0 8px 18px -6px rgb(var(--color-brand-500) / .8)}
.aa-go:not(:disabled):active{transform:scale(.96)}
.aa-btn{display:inline-flex;align-items:center;gap:7px;height:36px;padding:0 13px;border-radius:11px;font-size:13px;font-weight:500;color:rgb(var(--color-slate-700));background:rgb(var(--color-slate-100));border:1px solid rgb(var(--color-slate-200));transition:background .15s,transform .15s,border-color .15s}
.aa-btn:hover{background:rgb(var(--color-slate-200) / .7);transform:translateY(-1px)}
.aa-btn-pri{color:#fff;background:rgb(var(--color-brand-600));border-color:transparent}
.aa-btn-pri:hover{background:rgb(var(--color-brand-700))}
.aa-card{--t:var(--color-brand-500);transition:transform .22s cubic-bezier(.2,.7,.2,1),border-color .2s,box-shadow .22s}
.aa-card:hover{transform:translateY(-4px);border-color:rgb(var(--t) / .55);box-shadow:0 18px 34px -20px rgb(var(--t) / .6)}
.aa-t-brand{--t:var(--color-brand-500)} .aa-t-violet{--t:139 92 246} .aa-t-good{--t:var(--color-success)} .aa-t-warn{--t:var(--color-warning)} .aa-t-info{--t:56 130 246}
.aa-card .aa-ic,.aa-card .aa-kic{color:rgb(var(--t));background:rgb(var(--t) / .13);transition:transform .25s,background .2s,color .2s}
.aa-card:hover .aa-ic,.aa-card:hover .aa-kic{background:rgb(var(--t));color:#fff;transform:rotate(-6deg) scale(1.06)}
.aa-card .aa-try{color:rgb(var(--t));opacity:.8;transition:transform .2s,opacity .2s}
.aa-card:hover .aa-try{opacity:1;transform:translateX(3px)}
.aa-arrow{opacity:0;transform:translateX(-6px);transition:opacity .2s,transform .2s}
.aa-card:hover .aa-arrow{opacity:1;transform:none}
.aa-row{transition:background .15s,transform .15s}
.aa-row:hover{background:rgb(var(--color-slate-200) / .6);transform:translateX(2px)}
@media (prefers-reduced-motion:reduce){.aa *,.aa-drawer,.aa-scrim,.aa-up{animation:none!important;transition:none!important}}
`;

const REQUEST_RESULT_TOOLS = new Set([
  "create_intake_request",
  "get_intake_request",
  "start_intake_workflow",
  "advance_intake_workflow",
]);
const CONTRACT_OPEN_TOOLS = new Set([
  "read_contract",
  "find_in_contract",
  "get_contract_status",
  "list_contract_versions",
  "compare_contract_versions",
]);

type OpenLink = { key: string; href: string; label: string };

// Build the open-links for a tool from a result-shaped object. `fields` differs
// between the live stream (id/ref/contract_id) and the persisted block artifact
// (request_id/request_ref/contract_id), so callers pass the right field names.
function openLinksFrom(
  name: string,
  fields: {
    requestId?: unknown;
    requestRef?: unknown;
    contractId?: unknown;
  },
): OpenLink[] {
  const out: OpenLink[] = [];
  if (REQUEST_RESULT_TOOLS.has(name)) {
    const id = typeof fields.requestId === "string" ? fields.requestId : null;
    const ref = typeof fields.requestRef === "string" ? fields.requestRef : null;
    if (id && ref)
      out.push({ key: `r${id}`, href: `/intake?open=${id}`, label: `Open ${ref}` });
  }
  if (CONTRACT_OPEN_TOOLS.has(name)) {
    const cid =
      typeof fields.contractId === "string" ? fields.contractId : null;
    if (cid)
      out.push({ key: `c${cid}`, href: `/contracts/${cid}`, label: "Open contract" });
  }
  return out;
}

// Live stream: read the ephemeral tool result.
function openLinksOf(item: ChatItem): OpenLink[] {
  if (!item.tool || item.tool.status !== "done") return [];
  const r = item.tool.result;
  if (!r) return [];
  return openLinksFrom(item.tool.name, {
    requestId: r.id,
    requestRef: r.ref,
    contractId: r.contract_id,
  });
}

function toolLabel(name: string, done: boolean): string {
  const pair = TOOL_LABELS[name];
  if (pair) return done ? pair[1] : pair[0];
  return titleCase(name);
}

// Plain-language confirmation copy — no "mutating/external action" jargon.
function confirmCopy(name: string): { title: string; body: string } {
  const m: Record<string, { title: string; body: string }> = {
    edit_contract: {
      title: "Apply tracked changes?",
      body: "I'll propose tracked changes to this contract for you to review and accept or reject.",
    },
    redline_against_playbook: {
      title: "Redline against the playbook?",
      body: "I'll compare this contract to the selected playbook and propose tracked changes for your review.",
    },
    generate_contract_docx: {
      title: "Draft this contract?",
      body: "I'll draft a new contract document you can review and edit.",
    },
    send_for_signature: {
      title: "Send for e-signature?",
      body: "I'll send this contract to the recipients for signature.",
    },
    run_workflow: {
      title: "Run this prompt?",
      body: "I'll run the selected prompt on this contract.",
    },
    replicate_contract_version: {
      title: "Duplicate this contract?",
      body: "I'll create a copy of this contract version.",
    },
    decide_approval: {
      title: "Record this approval decision?",
      body: "I'll approve or reject the pending approval on this request, as shown below.",
    },
    reassign_request: {
      title: "Reassign this request?",
      body: "I'll change who owns this request.",
    },
    send_for_negotiation: {
      title: "Send to the counterparty?",
      body: "I'll send this contract out for negotiation — this can create a link the counterparty can open.",
    },
    add_contract_comment: {
      title: "Add this comment?",
      body: "I'll add a comment to the contract. If it's shared, the counterparty will see it.",
    },
    advance_contract_stage: {
      title: "Move the contract to another stage?",
      body: "I'll move this contract forward in its lifecycle.",
    },
    start_intake_workflow: {
      title: "Start a workflow on this request?",
      body: "I'll start the workflow below on this request; its steps will be assigned to people.",
    },
    advance_intake_workflow: {
      title: "Advance this request's workflow?",
      body: "I'll complete the current step or re-check its approval or signature.",
    },
    create_workflow: {
      title: "Create a new workflow?",
      body: "I'll add a new governance workflow your team can use.",
    },
    create_intake_request: {
      title: "Raise this legal request?",
      body: "I'll create a new request for the legal team with the details below.",
    },
    create_notice: {
      title: "Log this notice?",
      body: "I'll add this notice to the notice register.",
    },
    draft_notice_response: {
      title: "Draft a reply to this notice?",
      body: "I'll write an AI draft reply and save it on the notice, replacing any earlier draft.",
    },
    complete_obligation: {
      title: "Mark this obligation as done?",
      body: "I'll mark the obligation below as completed. If it repeats, the next one is scheduled.",
    },
    complete_task: {
      title: "Mark this task as done?",
      body: "I'll mark the task below as done on its request.",
    },
    extract_obligations: {
      title: "Re-read this contract's obligations?",
      body: "I'll extract obligations from the contract again. Earlier AI-found obligations that are still open are replaced.",
    },
  };
  return (
    m[name] ?? {
      title: `Confirm: ${titleCase(name)}?`,
      body: "This action changes your data or contacts an external service.",
    }
  );
}

// Fold consecutive tool/system events into one collapsible trace, like Mike's
// "Completed in N steps" card, so the conversation stays readable.
function groupItems(items: ChatItem[]): ItemGroup[] {
  const groups: ItemGroup[] = [];
  for (const it of items) {
    const isStep = it.role === "tool" || it.role === "system";
    const last = groups[groups.length - 1];
    if (isStep && last && last.kind === "steps") {
      last.items.push(it);
    } else if (isStep) {
      groups.push({ kind: "steps", items: [it] });
    } else {
      groups.push({ kind: "bubble", item: it });
    }
  }
  return groups;
}

function editQuotes(citation: unknown[] | null): string[] {
  return (citation ?? [])
    .filter(
      (c): c is { type: string; quote?: string } =>
        !!c &&
        typeof c === "object" &&
        (c as { type?: string }).type === "source_quote",
    )
    .map((c) => c.quote ?? "")
    .filter(Boolean);
}

function InlineEditReview({
  edits,
  onDecide,
}: {
  edits: ContractEditResponse[];
  onDecide: (editId: string, accept: boolean) => void;
}) {
  const proposed = edits.filter((e) => e.status === "proposed").length;
  return (
    <div className="space-y-2 rounded-md border border-warning/30 bg-warning-subtle p-3">
      <p className="text-sm font-semibold text-slate-800">
        Proposed changes{" "}
        <span className="font-normal text-slate-500">
          ({proposed} pending · {edits.length} total)
        </span>
      </p>
      {edits.map((e) => {
        const quotes = editQuotes(e.citation);
        return (
          <div
            key={e.id}
            className="space-y-2 rounded-lg border border-slate-200 bg-slate-100 p-3"
          >
            <div className="flex items-center justify-between gap-2">
              <Badge tone="slate">{titleCase(e.edit_type)}</Badge>
              <Badge
                tone={
                  e.status === "accepted"
                    ? "green"
                    : e.status === "rejected"
                      ? "red"
                      : "amber"
                }
              >
                {titleCase(e.status)}
              </Badge>
            </div>
            {e.original_text && (
              <div className="max-h-28 overflow-y-auto rounded bg-danger-subtle p-2 text-xs text-danger line-through">
                {e.original_text}
              </div>
            )}
            {e.replacement_text && (
              <div className="max-h-28 overflow-y-auto rounded bg-success-subtle p-2 text-xs text-success">
                {e.replacement_text}
              </div>
            )}
            {e.rationale && (
              <p className="text-xs text-slate-500">{e.rationale}</p>
            )}
            {quotes.length > 0 && (
              <div className="space-y-1 border-t border-slate-100 pt-1.5">
                {quotes.map((q, i) => (
                  <blockquote
                    key={i}
                    className="border-l-2 border-brand-300 pl-2 text-[11px] italic text-slate-500"
                  >
                    {q}
                  </blockquote>
                ))}
              </div>
            )}
            {e.status === "proposed" && (
              <div className="flex gap-2">
                <Button size="sm" onClick={() => onDecide(e.id, true)}>
                  <Check className="h-3.5 w-3.5" />
                  Accept
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => onDecide(e.id, false)}
                >
                  <X className="h-3.5 w-3.5" />
                  Reject
                </Button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function PlaybookPicker({
  playbooks,
  disabled,
  onRun,
  onCancel,
}: {
  playbooks: PickerPlaybook[];
  disabled: boolean;
  onRun: (pb: PickerPlaybook, ver: PickerPlaybookVersion) => void;
  onCancel: () => void;
}) {
  const [pbId, setPbId] = useState(playbooks[0]?.playbook_id ?? "");
  const pb = playbooks.find((p) => p.playbook_id === pbId) ?? playbooks[0];
  const defaultVer =
    pb?.versions.find((v) => v.is_current)?.playbook_version_id ??
    pb?.versions[0]?.playbook_version_id ??
    "";
  const [verId, setVerId] = useState(defaultVer);
  const ver =
    pb?.versions.find((v) => v.playbook_version_id === verId) ??
    pb?.versions[0];

  function pickPlaybook(id: string) {
    setPbId(id);
    const next = playbooks.find((p) => p.playbook_id === id);
    setVerId(
      next?.versions.find((v) => v.is_current)?.playbook_version_id ??
        next?.versions[0]?.playbook_version_id ??
        "",
    );
  }

  return (
    <div className="mb-2 rounded-md border border-brand-200 bg-brand-50/60 p-3">
      <p className="mb-2 text-sm font-semibold text-slate-800">
        Choose a playbook to redline against
      </p>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className="flex-1 text-xs font-medium text-slate-500">
          Playbook
          <Select
            value={pbId}
            onChange={(e) => pickPlaybook(e.target.value)}
            className="mt-1 h-9 text-sm"
          >
            {playbooks.map((p) => (
              <option key={p.playbook_id} value={p.playbook_id}>
                {p.name} ({p.status})
              </option>
            ))}
          </Select>
        </label>
        <label className="flex-1 text-xs font-medium text-slate-500">
          Version
          <Select
            value={verId}
            onChange={(e) => setVerId(e.target.value)}
            className="mt-1 h-9 text-sm"
          >
            {(pb?.versions ?? []).map((v) => (
              <option
                key={v.playbook_version_id}
                value={v.playbook_version_id}
              >
                Version {v.version_number} ({v.status}
                {v.is_current ? ", current" : ""})
              </option>
            ))}
          </Select>
        </label>
        <div className="flex gap-2">
          <Button
            size="sm"
            disabled={disabled || !pb || !ver}
            onClick={() => pb && ver && onRun(pb, ver)}
          >
            <Wand2 className="h-4 w-4" />
            Generate redline
          </Button>
          <Button size="sm" variant="ghost" onClick={onCancel}>
            Dismiss
          </Button>
        </div>
      </div>
      {pb?.description && (
        <p className="mt-2 text-xs text-slate-500">{pb.description}</p>
      )}
    </div>
  );
}

function TabTitle({
  contractId,
  fallback,
}: {
  contractId: string;
  fallback: string;
}) {
  const { data } = useQuery({
    queryKey: ["contract", contractId],
    queryFn: () => contractsApi.get(contractId),
  });
  return <>{data?.title || fallback}</>;
}

function TimelineStepCard({
  tools,
}: {
  tools: Extract<TimelineBlock, { type: "tool" }>[];
}) {
  const [open, setOpen] = useState(false);
  const openLinks: OpenLink[] = [];
  const seen = new Set<string>();
  for (const t of tools) {
    for (const l of openLinksFrom(t.name, {
      requestId: t.artifact?.request_id,
      requestRef: t.artifact?.request_ref,
      contractId: t.artifact?.contract_id,
    })) {
      if (!seen.has(l.key)) {
        seen.add(l.key);
        openLinks.push(l);
      }
    }
  }
  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-slate-100/70">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-[13px] text-slate-500"
      >
        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-success" />
        <span className="font-medium">
          Worked through {tools.length} step
          {tools.length === 1 ? "" : "s"}
        </span>
        <ChevronDown
          className={cn(
            "ml-auto h-4 w-4 text-slate-400 transition-transform",
            !open && "-rotate-90",
          )}
        />
      </button>
      {openLinks.length > 0 && (
        <div className="flex flex-wrap gap-2 border-t border-slate-200 px-4 py-2.5">
          {openLinks.map((l) => (
            <Link
              key={l.key}
              href={l.href}
              className="inline-flex items-center gap-1.5 rounded-full bg-brand-600 px-3 py-1 text-xs font-semibold text-white transition hover:bg-brand-700"
            >
              {l.label}
              <ChevronRight className="h-3.5 w-3.5" />
            </Link>
          ))}
        </div>
      )}
      {open && (
        <ol className="ml-4 space-y-3 border-t border-slate-100 py-4 pl-5 pr-4">
          {tools.map((t, i) => (
            <li key={i} className="relative text-sm text-slate-600">
              <span
                className={cn(
                  "absolute -left-[1.05rem] top-1.5 h-2 w-2 rounded-full ring-4 ring-slate-100",
                  t.status === "error" ? "bg-danger" : "bg-success",
                )}
              />
              {i < tools.length - 1 && (
                <span className="absolute -left-[0.55rem] top-3.5 h-[calc(100%+0.6rem)] w-px bg-slate-200" />
              )}
              <span>{toolLabel(t.name, true)}</span>
              {t.artifact?.summary && (
                <p className="mt-1 text-xs text-slate-400">
                  {t.artifact.summary}
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function AssistantTimeline({
  item,
  onCite,
}: {
  item: ChatItem;
  onCite?: (contractId: string | undefined, quote: string) => void;
}) {
  const blocks = item.blocks ?? [];
  type RG =
    | { k: "steps"; tools: Extract<TimelineBlock, { type: "tool" }>[] }
    | { k: "content"; text: string };
  const groups: RG[] = [];
  for (const b of blocks) {
    if (b.type === "tool") {
      const last = groups[groups.length - 1];
      if (last && last.k === "steps") last.tools.push(b);
      else groups.push({ k: "steps", tools: [b] });
    } else if (b.type === "content" && b.text.trim()) {
      groups.push({ k: "content", text: b.text });
    }
  }
  return (
    <div className="aa-up flex gap-3">
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-violet-500 text-white shadow-sm"><Sparkles className="h-4 w-4" /></span>
      <div className="min-w-0 flex-1 space-y-3">
        {groups.map((g, i) =>
          g.k === "steps" ? (
            <TimelineStepCard key={i} tools={g.tools} />
          ) : (
            <div className="answer-serif aa-sans" key={i}>
              <Markdown>{g.text}</Markdown>
            </div>
          ),
        )}
        {item.citations && <CitationList citations={item.citations} onCite={onCite} />}
      </div>
    </div>
  );
}

// Renders the "Sources" citation chips. A citation is only clickable when it
// carries a contract_id to open — otherwise it's a static quote chip (no dead
// click / hover / open affordance), so passages the retriever couldn't tie to a
// specific document don't look broken.
function CitationList({
  citations,
  onCite,
}: {
  citations: Citation[];
  onCite?: (contractId: string | undefined, quote: string) => void;
}) {
  // Drop empty citation entries — the backend sometimes returns numbered
  // citations with neither a quote nor a contract_id (nothing to show or open),
  // which otherwise render as meaningless numbered chips. Keep only citations
  // with a passage to read or a document to open, then renumber sequentially.
  const shown = citations.filter(
    (c) => (c.quote ?? c.excerpt ?? "").trim() || c.contract_id,
  );
  if (!shown.length) return null;
  return (
    <div className="space-y-1.5 pt-1">
      <p className="text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
        Sources
      </p>
      {shown.map((c, i) => {
        const q = c.quote ?? c.excerpt ?? "";
        const target = c.contract_id;
        const inner = (
          <>
            <span className="flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-[5px] bg-brand-600 text-[10px] font-bold text-white">
              {i + 1}
            </span>
            <span className="answer-serif flex-1 leading-relaxed text-slate-600">{q}</span>
            {target && <Quote className="h-3.5 w-3.5 shrink-0 text-brand-400" />}
          </>
        );
        return target ? (
          <button
            key={i}
            onClick={() => q && onCite?.(target, q)}
            title="Show this passage in the document"
            className="flex w-full gap-2.5 rounded-xl border border-slate-200 bg-slate-50/60 p-3 text-left text-xs leading-relaxed text-slate-600 transition-colors hover:border-brand-300 hover:bg-brand-50/60"
          >
            {inner}
          </button>
        ) : (
          <div
            key={i}
            className="flex w-full gap-2.5 rounded-xl border border-slate-200 bg-slate-50/60 p-3 text-xs leading-relaxed text-slate-600"
          >
            {inner}
          </div>
        );
      })}
    </div>
  );
}

// One calm live status for the whole stream: the Aegis mark, the step actually
// in flight, and a Stop — instead of several spinners competing for attention.
function WorkingBeat({
  label,
  hint,
  stopping = false,
  onStop,
}: {
  label: string;
  hint?: string;
  stopping?: boolean;
  onStop: () => void;
}) {
  return (
    <div className="flex items-center gap-3 py-1">
      <span className="h-6 w-6 shrink-0 rounded-lg bg-gradient-to-br from-brand-500 to-brand-600" />
      <span className="min-w-0 flex-1 truncate">
        <span className="shimmer-text text-[15px] text-slate-500">{label}</span>
        {hint ? (
          <span className="ml-1.5 text-[12.5px] text-slate-400">{hint}</span>
        ) : null}
      </span>
      <button
        onClick={onStop}
        disabled={stopping}
        className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-300 bg-slate-100 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:text-slate-900 disabled:opacity-50"
      >
        <span className="h-2 w-2 rounded-[2px] bg-slate-500" />
        Stop
      </button>
    </div>
  );
}

function StepTrace({ steps }: { steps: ChatItem[] }) {
  const [open, setOpen] = useState(false);
  const runningStep = [...steps]
    .reverse()
    .find((s) => s.tool && s.tool.status === "running");
  const running = Boolean(runningStep);
  // The live "working" status lives in one place now (WorkingBeat, below the
  // stream). StepTrace is just the quiet, collapsed record of what ran.
  // Any request/contract a step touched becomes a visible click-through, shown
  // even when the trace is collapsed so the user never loses the link.
  const uniqueLinks: OpenLink[] = [];
  const seen = new Set<string>();
  for (const s of steps) {
    for (const l of openLinksOf(s)) {
      if (!seen.has(l.key)) {
        seen.add(l.key);
        uniqueLinks.push(l);
      }
    }
  }
  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-slate-100/70">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-[13px] text-slate-500"
      >
        <span
          className={cn(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            running ? "bg-warning" : "bg-success",
          )}
        />
        <span className="font-medium">
          Worked through {steps.length} step{steps.length === 1 ? "" : "s"}
        </span>
        <ChevronRight
          className={cn(
            "ml-auto h-4 w-4 shrink-0 text-slate-400 transition-transform",
            open && "rotate-90",
          )}
        />
      </button>
      {uniqueLinks.length > 0 && (
        <div className="flex flex-wrap gap-2 border-t border-slate-200 px-4 py-2.5">
          {uniqueLinks.map((l) => (
            <Link
              key={l.key}
              href={l.href}
              className="inline-flex items-center gap-1.5 rounded-full bg-brand-600 px-3 py-1 text-xs font-semibold text-white transition hover:bg-brand-700"
            >
              {l.label}
              <ChevronRight className="h-3.5 w-3.5" />
            </Link>
          ))}
        </div>
      )}
      {open && (
        <ol className="space-y-1.5 border-t border-slate-200 px-4 py-3">
          {steps.map((s) => (
            <li
              key={s.id}
              className="flex items-center gap-2 text-xs text-slate-500"
            >
              <span
                className={cn(
                  "h-1.5 w-1.5 shrink-0 rounded-full",
                  s.tool?.status === "error"
                    ? "bg-danger"
                    : s.tool?.status === "running"
                      ? "bg-warning"
                      : "bg-success",
                )}
              />
              <span className="truncate">
                {s.role === "tool"
                  ? toolLabel(s.text, s.tool?.status !== "running")
                  : s.text}
              </span>
              {s.tool?.status === "running" && (
                <Spinner className="h-3 w-3" />
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function ChatBubble({
  item,
  onReopenWorkflow,
  onCite,
}: {
  item: ChatItem;
  onReopenWorkflow: () => void;
  onCite?: (contractId: string | undefined, quote: string) => void;
}) {
  if (item.role === "tool") {
    return (
      <div className="aa-up ml-11 inline-flex w-fit items-center gap-2 rounded-full border border-slate-200 bg-slate-100 px-3 py-1 text-xs text-slate-500">
        <Wrench className="h-3.5 w-3.5" />
        <span>
          {toolLabel(item.text, item.tool?.status !== "running")}
        </span>
        {item.tool?.status === "running" && <Spinner className="h-3 w-3" />}
        {item.tool?.status === "done" && (
          <span className="rounded-full bg-success-subtle px-1.5 py-0.5 font-medium text-success">
            done
          </span>
        )}
        {item.tool?.status === "error" && (
          <span className="rounded-full bg-danger-subtle px-1.5 py-0.5 font-medium text-danger">
            error
          </span>
        )}
      </div>
    );
  }
  if (item.role === "system")
    return (
      <p className="text-center text-xs text-slate-400">{item.text}</p>
    );

  if (item.role === "user") {
    const { scope, body } = splitScope(item.text);
    return (
      <div data-user-msg className="aa-up flex scroll-mt-6 justify-end">
        <div className="max-w-[80%] space-y-1.5">
          {item.workflow && (
            <div className="flex justify-end">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-700">
                <Wand2 className="h-3 w-3" />
                {item.workflow.name}
              </span>
            </div>
          )}
          {scope && (
            <div className="flex justify-end">
              <span className="inline-flex max-w-full items-center gap-1.5 truncate rounded-full bg-slate-200 px-2.5 py-1 text-[11.5px] font-medium text-slate-600" title={scope}>
                <ListChecks className="h-3 w-3 shrink-0" />
                <span className="truncate">{scope}</span>
              </span>
            </div>
          )}
          <div className="whitespace-pre-wrap rounded-2xl rounded-br-md bg-brand-600 px-4 py-2.5 text-[15px] leading-7 text-white shadow-sm">
            {body}
          </div>
        </div>
      </div>
    );
  }

  // assistant: an avatar and borderless prose
  return (
    <div className="aa-up flex gap-3">
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-violet-500 text-white shadow-sm"><Sparkles className="h-4 w-4" /></span>
      <div className="min-w-0 flex-1 space-y-3">
        {item.workflow && (
          <button onClick={onReopenWorkflow} className="inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-700 hover:bg-brand-100">
            <Wand2 className="h-3 w-3" />
            {item.workflow.name}
          </button>
        )}
        {item.text ? (
          <div className="answer-serif aa-sans">
            <Markdown>{item.text}</Markdown>
          </div>
        ) : (
          <span className="text-slate-300">…</span>
        )}
        {item.citations && <CitationList citations={item.citations} onCite={onCite} />}
      </div>
    </div>
  );
}

function DocPickerModal({
  open,
  onClose,
  onPick,
}: {
  open: boolean;
  onClose: () => void;
  onPick: (c: ContractResponse) => void;
}) {
  const [q, setQ] = useState("");
  const qc = useQueryClient();
  const [importOpen, setImportOpen] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["contracts"],
    queryFn: contractsApi.list,
    enabled: open,
  });
  const matched = uniqById(data).filter((c) =>
    (c.title + " " + (c.counterparty_name ?? ""))
      .toLowerCase()
      .includes(q.toLowerCase()),
  );
  const LIMIT = 25;
  const items = matched.slice(0, LIMIT);
  return (
    <>
    <Modal open={open} onClose={onClose} title="Add a document to this chat">
      <div className="space-y-3">
        <Input
          autoFocus
          placeholder="Search documents…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button
          type="button"
          onClick={() => setImportOpen(true)}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-brand-600 hover:text-brand-700"
        >
          <Upload className="h-3.5 w-3.5" />
          Import a new file
        </button>
        {matched.length > LIMIT && (
          <p className="text-xs text-slate-400">
            Showing first {LIMIT} of {matched.length} — type to narrow.
          </p>
        )}
        <div className="max-h-80 space-y-1 overflow-y-auto">
          {isLoading ? (
            <CenterSpinner />
          ) : items.length ? (
            items.map((c) => (
              <button
                key={c.id}
                onClick={() => onPick(c)}
                className="flex w-full items-center gap-2.5 rounded-lg border border-slate-200 px-3 py-2 text-left text-sm transition-colors hover:border-brand-300 hover:bg-brand-50/50"
              >
                <FileText className="h-4 w-4 shrink-0 text-slate-400" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-slate-800">
                    {c.title}
                  </span>
                  {c.counterparty_name && (
                    <span className="block truncate text-xs text-slate-400">
                      {c.counterparty_name}
                    </span>
                  )}
                </span>
              </button>
            ))
          ) : (
            <p className="py-6 text-center text-sm text-slate-400">
              No documents found.
            </p>
          )}
        </div>
        <p className="text-xs text-slate-400">
          Import brings a new file in and attaches it to this chat
          immediately.
        </p>
      </div>
    </Modal>
    <ImportContractModal
      open={importOpen}
      onClose={() => setImportOpen(false)}
      onUploaded={(c) => {
        qc.invalidateQueries({ queryKey: ["contracts"] });
        onPick(c);
        onClose();
      }}
    />
    </>
  );
}

// A workflow's definition is either { prompt } (assistant workflows) or
// { columns: [{ name, prompt }] } (tabular-review workflows). Read the columns.
function workflowColumns(wf: Prompt): { name: string; prompt?: string }[] {
  const cols = (wf.definition as { columns?: { name: string; prompt?: string }[] })
    ?.columns;
  return Array.isArray(cols) ? cols : [];
}

// Turn any workflow into a chat instruction. Assistant workflows carry a ready
// prompt; for review workflows we synthesize one from their extraction columns
// so they can be applied to the attached document(s) in conversation too.
function workflowPrompt(wf: Prompt): string {
  const direct = (wf.definition as { prompt?: string })?.prompt;
  if (direct && direct.trim()) return direct;
  const cols = workflowColumns(wf);
  if (cols.length) {
    const lines = cols
      .map((c, i) => `${i + 1}. ${c.name}${c.prompt ? ` — ${c.prompt}` : ""}`)
      .join("\n");
    return `Review the attached document(s) using the "${wf.name}" review. For each document, extract and report on the following, citing the relevant clause where possible:\n\n${lines}\n\nPresent the results as a clear, structured summary.`;
  }
  return "";
}

/** A number that counts up to its value, so a fresh page feels alive. */
function CountUp({ n, className }: { n: number; className?: string }) {
  const [v, setV] = useState(0);
  useEffect(() => {
    if (typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { setV(n); return; }
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / 700);
      setV(Math.round(n * (1 - Math.pow(1 - k, 3))));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [n]);
  return <span className={className}>{v}</span>;
}

function WorkflowModal({
  open,
  onClose,
  onPick,
}: {
  open: boolean;
  onClose: () => void;
  onPick: (w: Prompt) => void;
}) {
  const { data: workflows, isLoading } = useQuery({
    queryKey: ["workflows"],
    queryFn: promptsApi.list,
    enabled: open,
  });
  const [selected, setSelected] = useState<Prompt | null>(null);
  const [hovered, setHovered] = useState<Prompt | null>(null);
  const [filter, setFilter] = useState<"all" | "assistant" | "tabular_review" | "other">("all");
  const [q, setQ] = useState("");
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);
  if (!open) return null;

  const all = workflows ?? [];
  const kindOf = (w: Prompt): "assistant" | "tabular_review" | "other" =>
    w.workflow_type === "assistant" ? "assistant" : w.workflow_type === "tabular_review" ? "tabular_review" : "other";
  const KIND = {
    assistant: { label: "Assistant", I: Sparkles, tone: "aa-t-brand" },
    tabular_review: { label: "Document review", I: ListChecks, tone: "aa-t-violet" },
    other: { label: "Other", I: Wand2, tone: "aa-t-warn" },
  } as const;
  const tabs = [
    { k: "all" as const, l: "All", n: all.length },
    { k: "assistant" as const, l: "Assistant", n: all.filter((w) => kindOf(w) === "assistant").length },
    { k: "tabular_review" as const, l: "Document review", n: all.filter((w) => kindOf(w) === "tabular_review").length },
    { k: "other" as const, l: "Other", n: all.filter((w) => kindOf(w) === "other").length },
  ].filter((t) => t.k === "all" || t.n > 0);
  const needle = q.trim().toLowerCase();
  const list = all.filter((w) => (filter === "all" || kindOf(w) === filter) && (!needle || `${w.name} ${w.description ?? ""}`.toLowerCase().includes(needle)));
  const preview = hovered ?? selected ?? list[0] ?? null;
  const use = (w: Prompt) => onPick(w);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="aa-scrim absolute inset-0 bg-slate-900/40 backdrop-blur-[3px]" onClick={onClose} />
      <div role="dialog" aria-label="Apply a prompt" className="aa-pop relative flex h-[min(720px,92vh)] w-full max-w-[1060px] flex-col overflow-hidden rounded-3xl border border-slate-200 bg-slate-50 shadow-2xl">
        {/* header */}
        <div className="flex items-center gap-3 border-b border-slate-200 px-6 py-4">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-50 text-brand-600"><Wand2 className="h-5 w-5" /></span>
          <div className="min-w-0">
            <h2 className="text-[16px] font-semibold text-slate-900">Apply a prompt</h2>
            <p className="text-[12px] text-slate-400">Start your question from a ready-made prompt.</p>
          </div>
          <div className="relative ml-auto hidden w-[300px] sm:block">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)} autoFocus placeholder="Search prompts" style={{ outline: "none" }}
              className="h-10 w-full rounded-xl border border-slate-200 bg-slate-100 pl-9 pr-3 text-[13px] text-slate-800 outline-none placeholder:text-slate-400 focus:border-brand-400 focus:ring-2 focus:ring-brand-500/15" />
          </div>
          <button onClick={onClose} aria-label="Close" className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-200 hover:text-slate-700"><X className="h-4 w-4" /></button>
        </div>

        {isLoading ? (
          <div className="flex-1"><CenterSpinner /></div>
        ) : (
          <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[380px_minmax(0,1fr)]">
            {/* list */}
            <div className="flex min-h-0 flex-col border-r border-slate-200">
              <div className="flex flex-wrap gap-1.5 px-4 pb-2 pt-3">
                {tabs.map((t) => (
                  <button key={t.k} onClick={() => setFilter(t.k)} className={cn("inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[12px] font-medium transition", filter === t.k ? "bg-brand-600 text-white" : "bg-slate-200/70 text-slate-600 hover:bg-slate-200")}>
                    {t.l}<span className={cn("text-[10.5px]", filter === t.k ? "text-white/80" : "text-slate-400")}>{t.n}</span>
                  </button>
                ))}
              </div>
              <ul className="min-h-0 flex-1 space-y-1.5 overflow-y-auto px-3 pb-4 pt-1" onMouseLeave={() => setHovered(null)}>
                {list.length === 0 && <li className="p-6 text-center text-sm text-slate-400">{all.length ? "No prompts match." : "No prompts available."}</li>}
                {list.map((w, i) => {
                  const k = KIND[kindOf(w)];
                  const on = selected?.id === w.id;
                  return (
                    <li key={w.id} className="aa-up" style={{ animationDelay: `${Math.min(i, 10) * 22}ms` }}>
                      <button onClick={() => setSelected(w)} onDoubleClick={() => use(w)} onMouseEnter={() => setHovered(w)} onFocus={() => setHovered(w)}
                        className={cn("aa-pi flex w-full items-center gap-3 rounded-2xl border p-3 text-left", k.tone, on ? "border-brand-500 bg-brand-50" : "border-slate-200 bg-slate-100 hover:border-slate-300")}>
                        <span className="aa-ic flex h-10 w-10 shrink-0 items-center justify-center rounded-xl"><k.I className="h-[18px] w-[18px]" /></span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13.5px] font-semibold text-slate-900">{w.name}</span>
                          <span className="block truncate text-[11.5px] text-slate-400">{w.description || k.label}</span>
                        </span>
                        {on && <Check className="h-4 w-4 shrink-0 text-brand-600" />}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>

            {/* preview */}
            <div className="hidden min-h-0 overflow-y-auto p-6 md:block">
              {preview ? (
                (() => {
                  const k = KIND[kindOf(preview)];
                  const cols = workflowColumns(preview);
                  const prompt = (preview.definition as { prompt?: string })?.prompt;
                  return (
                    <div key={preview.id} className="aa-up">
                      <div className={cn("flex items-start gap-4", k.tone)}>
                        <span className="aa-ic flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl"><k.I className="h-6 w-6" /></span>
                        <div className="min-w-0">
                          <h3 className="text-[19px] font-semibold leading-tight text-slate-900">{preview.name}</h3>
                          <div className="mt-2 flex flex-wrap items-center gap-1.5">
                            <span className="rounded-full bg-slate-200 px-2.5 py-0.5 text-[11px] font-medium text-slate-600">{k.label}</span>
                            {preview.is_builtin && <span className="rounded-full bg-brand-50 px-2.5 py-0.5 text-[11px] font-medium text-brand-700">Built in</span>}
                            {preview.description && <span className="text-[12px] text-slate-400">{preview.description}</span>}
                          </div>
                        </div>
                      </div>
                      {prompt ? (
                        <div className="mt-5 rounded-2xl border border-slate-200 bg-slate-100 p-5">
                          <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-400">What it does</p>
                          <div className="answer-serif aa-sans text-[14px]"><Markdown>{prompt}</Markdown></div>
                        </div>
                      ) : cols.length ? (
                        <div className="mt-5">
                          <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-400">Extracts {cols.length} field{cols.length === 1 ? "" : "s"}</p>
                          <ul className="grid gap-2 sm:grid-cols-2">
                            {cols.map((c, i) => (
                              <li key={i} className="rounded-xl border border-slate-200 bg-slate-100 p-3">
                                <span className="block text-[13px] font-semibold text-slate-800">{c.name}</span>
                                {c.prompt && <span className="mt-0.5 line-clamp-3 block text-[12px] text-slate-500">{c.prompt}</span>}
                              </li>
                            ))}
                          </ul>
                        </div>
                      ) : (
                        <p className="mt-5 text-sm text-slate-400">This prompt has no preview.</p>
                      )}
                    </div>
                  );
                })()
              ) : (
                <div className="flex h-full flex-col items-center justify-center text-center text-slate-400">
                  <Wand2 className="mb-2 h-8 w-8" />
                  <p className="text-sm">Pick a prompt to see what it does.</p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* footer */}
        <div className="flex items-center gap-3 border-t border-slate-200 px-6 py-3.5">
          <p className="min-w-0 flex-1 truncate text-[12.5px] text-slate-400">{selected ? <>Selected: <span className="font-medium text-slate-700">{selected.name}</span></> : "Click a prompt to select it, double-click to use it."}</p>
          <button onClick={onClose} className="aa-btn">Cancel</button>
          <button disabled={!selected} onClick={() => selected && use(selected)} className="aa-go"><Wand2 className="h-4 w-4" />Use prompt</button>
        </div>
      </div>
    </div>
  );
}
