"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Send,
  Bot,
  User as UserIcon,
  Wrench,
  Quote,
  ShieldAlert,
  Check,
  X,
  Sparkles,
  FolderKanban,
  Wand2,
  ChevronRight,
  ChevronDown,
  ArrowUp,
  FileText,
  ShieldCheck,
  ListChecks,
  PanelLeft,
  Upload,
  Search,
  Pencil,
} from "lucide-react";
import {
  assistantApi,
  contractsApi,
  mattersApi,
  promptsApi,
  approvalsApi,
  renewalsApi,
} from "@/lib/endpoints";
import { apiStream } from "@/lib/api";
import { Badge, Button, CenterSpinner, Input, Modal, Select, Spinner } from "@/components/ui";
import { ContractDocument } from "@/components/contract-document";
import { Markdown } from "@/components/markdown";
import { ImportContractModal } from "@/components/import-contract-modal";
import { useLayout } from "@/lib/layout";
import { cn, fmtRelative, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import { useAuth } from "@/lib/auth";
import type {
  AssistantSession,
  Citation,
  ContractEditResponse,
  ContractResponse,
  MatterResponse,
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

export function AssistantWorkspace() {
  const params = useSearchParams();
  const router = useRouter();
  const contractParam = params.get("contract");
  const projectParam = params.get("project");
  const sessionParam = params.get("session");
  const workflowParam = params.get("workflow");
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
  // Front-page starters organised by what the agents can DO, each with a real
  // portfolio-shaped example — this is the command surface, not generic prompts.
  const capabilities = [
    {
      verb: "Ask · research",
      icon: Sparkles,
      prompt:
        "What's our exposure if a key counterparty terminates their MSA early? Ground it in the contract.",
    },
    {
      verb: "Draft",
      icon: Wand2,
      prompt: "Draft a mutual NDA with Acme Corp — 2-year term, governed by EU law.",
    },
    {
      verb: "Review · redline",
      icon: ShieldCheck,
      prompt: "Find a Globex MSA and redline it against our negotiation playbook.",
    },
    {
      verb: "Find",
      icon: ListChecks,
      prompt: "Which contracts in the portfolio auto-renew within the next 30 days?",
    },
  ];

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
  const [projPickerOpen, setProjPickerOpen] = useState(false);
  const [newProjectId, setNewProjectId] = useState(projectParam ?? "");
  const [railOpen, setRailOpen] = useState(false);
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

  async function syncFromServer(sessionId: string) {
    try {
      const msgs = await assistantApi.messages(sessionId);
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
    } catch {
      /* ignore */
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
    queryKey: ["assistant-sessions", projectParam, debouncedChatSearch],
    queryFn: () =>
      assistantApi.sessions({
        ...(projectParam ? { matter_id: projectParam } : {}),
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
  const { data: projects } = useQuery({
    queryKey: ["projects"],
    queryFn: mattersApi.list,
  });
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

  const projectName = useMemo(() => {
    const m = new Map<string, string>();
    for (const p of projects ?? []) m.set(p.id, p.name);
    return (id?: string | null) => (id ? m.get(id) ?? "Matter" : null);
  }, [projects]);

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
      notify(accept ? "Edit accepted" : "Edit rejected", "success");
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
          matter_id: projectParam ?? undefined,
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
  }, [sessionParam, contractParam, projectParam]);

  async function selectSession(s: AssistantSession) {
    stopWatch();
    abortActiveStream();
    sessRef.current = s.id;
    setActiveSession(s);
    // Keep the URL in sync with the active session so project scope stays
    // explicit and reload / back / share land on the same scoped chat.
    const sp = new URLSearchParams();
    const pid = s.matter_id ?? projectParam;
    const cid = s.contract_id ?? contractParam;
    if (pid) sp.set("project", pid);
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
    try {
      const msgs = await assistantApi.messages(s.id);
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
  }

  async function newSession() {
    const pid = newProjectId || projectParam || undefined;
    const s = await assistantApi.createSession({
      session_type: pid ? "project" : "general",
      matter_id: pid,
      title: "New conversation",
    });
    qc.invalidateQueries({ queryKey: ["assistant-sessions", projectParam] });
    void selectSession(s);
  }

  // Legora-style: from the opening screen, type → spin up a session and stream.
  async function startConversation(text: string) {
    const t = text.trim();
    if (!t || streaming) return;
    const pid = newProjectId || projectParam || undefined;
    const cid = activeContractId ?? undefined;
    setInput("");
    try {
      const s = await assistantApi.createSession({
        session_type: cid ? "contract" : pid ? "project" : "general",
        contract_id: cid,
        matter_id: pid,
        title: t.slice(0, 60),
      });
      qc.invalidateQueries({ queryKey: ["assistant-sessions", projectParam] });
      sessRef.current = s.id;
      setActiveSession(s);
      setItems([{ id: crypto.randomUUID(), role: "user", text: t }]);
      setStreaming(true);
      startWatch(s.id);
      abortActiveStream();
      abortRef.current = await apiStream(
        `/assistant/sessions/${s.id}/stream`,
        { message: t, contract_ids: cid ? [cid] : [] },
        {
          onEvent: handleEvent,
          onError: handleStreamError,
          onClose: () => setStreaming(false),
        },
      );
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not start chat", "error");
    }
  }

  function handleEvent(event: string, data: Record<string, unknown>) {
    if (event === "message_delta") {
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
      });
      setStreaming(false);
    } else if (event === "error") {
      stopWatch();
      notify(String(data.message ?? "Assistant error"), "error");
      setStreaming(false);
    } else if (event === "done") {
      stopWatch();
      setStreaming(false);
      if (sessRef.current) void syncFromServer(sessRef.current);
    }
  }

  function handleStreamError(err: Error) {
    stopWatch();
    notify(err.message || "Assistant stream failed", "error");
    setStreaming(false);
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
    abortActiveStream();
    const wfBody = wf ? workflowPrompt(wf) : "";
    const wfPrompt = wf && wfBody ? `[Prompt: ${wf.name}]\n${wfBody}\n\n` : "";
    abortRef.current = await apiStream(
      `/assistant/sessions/${activeSession.id}/stream`,
      {
        message: wfPrompt + message,
        contract_ids: activeContractId ? [activeContractId] : [],
        ...(wf ? { workflow_id: wf.id } : {}),
      },
      {
        onEvent: handleEvent,
        onError: handleStreamError,
        onClose: () => setStreaming(false),
      },
    );
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
        abortActiveStream();
        abortRef.current = await apiStream(
          `/assistant/runs/${p.assistantRunId}/resume?confirmation_id=${p.confirmationId}`,
          {},
          {
            onEvent: handleEvent,
            onError: handleStreamError,
            onClose: () => setStreaming(false),
          },
        );
      } else {
        await assistantApi.reject(p.confirmationId, "Rejected by user");
        setItems((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "system",
            text: `Rejected ${titleCase(p.toolName)}.`,
          },
        ]);
      }
    } catch (e) {
      notify(e instanceof Error ? e.message : "Confirmation failed", "error");
    }
  }

  const hasDoc = !!activeContractId;

  return (
    <div className="flex h-full flex-col bg-slate-50">
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* Conversation column */}
        <section className="relative flex min-w-0 flex-1 flex-col">
          {/* Floating controls — replaces the removed top bar */}
          <div className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-start justify-between p-2">
            <div className="pointer-events-auto flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50/85 p-1 shadow-pop backdrop-blur">
              <button
                onClick={() => setRailOpen(true)}
                title="Chats"
                aria-label="Chats"
                className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800"
              >
                <PanelLeft className="h-4 w-4" />
              </button>
              <button
                onClick={newSession}
                title="New chat"
                aria-label="New chat"
                className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800"
              >
                <Plus className="h-4 w-4" />
              </button>
            </div>
            {activeContractId && (
              <Link
                href={`/contracts/${activeContractId}`}
                className="pointer-events-auto flex items-center gap-1.5 rounded-md border border-slate-200 bg-slate-50/85 px-2.5 py-1.5 text-xs font-medium text-brand-600 shadow-pop backdrop-blur transition-colors hover:bg-brand-50"
              >
                <FileText className="h-3.5 w-3.5" />
                Open contract page
              </Link>
            )}
          </div>
          {!activeSession ? (
            <div className="flex-1 overflow-y-auto">
              <div className="mx-auto w-full max-w-[720px] px-6 py-8">
                {/* grounding strip — this is your desk, grounded in your data */}
                <div className="flex items-center gap-3 text-[12.5px] text-slate-400">
                  <span className="inline-flex items-center gap-1.5 font-mono text-success">
                    <span className="h-1.5 w-1.5 rounded-full bg-success" />
                    Grounded in your portfolio
                  </span>
                  {attention.length > 0 && (
                    <>
                      <span className="text-slate-300">·</span>
                      <span className="font-mono text-warning">
                        {attention.length} need{attention.length === 1 ? "s" : ""} your attention
                      </span>
                    </>
                  )}
                </div>

                <h1 className="answer-serif mt-4 animate-rise-in text-[clamp(28px,4vw,38px)] font-normal leading-[1.1] tracking-[-0.01em] text-slate-900">
                  {greeting}, {firstName}.{" "}
                  <span className="text-slate-400">What are we working on?</span>
                </h1>
                <p className="mt-3 max-w-[56ch] animate-rise-in text-[15px] leading-relaxed text-slate-500">
                  Ask about a contract, a clause, or the whole portfolio — every
                  answer is grounded in your own documents, with the sources to open.
                </p>

                {workflow && (
                  <div className="mt-5 flex items-center gap-2 rounded border border-brand-200 bg-brand-50 px-3 py-1.5 text-xs text-brand-700">
                    <Wand2 className="h-3.5 w-3.5" />
                    <span className="font-medium">Prompt:</span>
                    {workflow.name}
                    <button
                      onClick={() => setWorkflow(null)}
                      className="ml-auto text-brand-500 hover:text-brand-700"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                )}

                {/* hero composer */}
                <div className="mt-6 rounded-2xl border border-slate-300 bg-slate-100 p-3 shadow-pop transition focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-500/20">
                  <textarea
                    rows={1}
                    aria-label="Ask Aegis about a contract, a clause, or your whole portfolio"
                    placeholder="Ask about a contract, a clause, or your whole portfolio…"
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) =>
                      e.key === "Enter" &&
                      !e.shiftKey &&
                      (e.preventDefault(), startConversation(input))
                    }
                    className="block max-h-44 w-full resize-none bg-transparent px-2 pb-2 pt-1.5 text-[16px] leading-6 text-slate-800 placeholder:text-slate-400 focus:outline-none"
                  />
                  <div className="mt-1 flex items-center gap-1">
                    <button
                      onClick={() => setDocPickerOpen(true)}
                      className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[13px] font-medium text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800"
                    >
                      <Plus className="h-4 w-4" />
                      Documents
                    </button>
                    <button
                      onClick={() => setWfModalOpen(true)}
                      className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[13px] font-medium text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800"
                    >
                      <Wand2 className="h-4 w-4" />
                      Prompts
                    </button>
                    <button
                      onClick={() => setProjPickerOpen(true)}
                      className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[13px] font-medium text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800"
                    >
                      <FolderKanban className="h-4 w-4" />
                      Matters
                    </button>
                    <span className="ml-auto mr-1.5 hidden items-center gap-1.5 font-mono text-[11px] text-success sm:inline-flex">
                      <span className="h-1.5 w-1.5 rounded-full bg-success" />
                      grounded in your portfolio
                    </span>
                    <button
                      onClick={() => startConversation(input)}
                      disabled={!input.trim() || streaming}
                      className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-600 text-white transition hover:bg-brand-700 disabled:opacity-30"
                    >
                      <ArrowUp className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {/* capability starters — what the agents can do */}
                <p className="mb-3 mt-8 font-mono text-[11px] uppercase tracking-[0.1em] text-slate-400">
                  Start with what Aegis can do
                </p>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {capabilities.map((c) => {
                    const Icon = c.icon;
                    return (
                      <button
                        key={c.verb}
                        onClick={() => startConversation(c.prompt)}
                        disabled={streaming}
                        className="flex gap-3 rounded-2xl border border-slate-200 bg-slate-100 p-4 text-left transition hover:-translate-y-0.5 hover:border-brand-300 disabled:opacity-50"
                      >
                        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-brand-50 text-brand-600">
                          <Icon className="h-[18px] w-[18px]" />
                        </span>
                        <span className="min-w-0">
                          <span className="block font-mono text-[10.5px] font-semibold uppercase tracking-[0.06em] text-brand-600">
                            {c.verb}
                          </span>
                          <span className="answer-serif mt-0.5 block text-[15px] leading-snug text-slate-800">
                            {c.prompt}
                          </span>
                        </span>
                      </button>
                    );
                  })}
                </div>

                {/* recents + attention — real portfolio data */}
                <div className="mt-8 grid grid-cols-1 gap-6 sm:grid-cols-2">
                  <div>
                    <p className="mb-1.5 font-mono text-[11px] uppercase tracking-[0.08em] text-slate-400">
                      Pick up where you left off
                    </p>
                    {recents.length ? (
                      recents.map((s) => (
                        <button
                          key={s.id}
                          onClick={() => selectSession(s)}
                          className="flex w-full items-center gap-3 border-b border-slate-200 py-2.5 text-left text-[13.5px] text-slate-600 last:border-0 hover:text-slate-900"
                        >
                          <span className="flex-1 truncate">
                            {s.title || "Untitled chat"}
                          </span>
                          <span className="shrink-0 font-mono text-[11px] text-slate-400">
                            {fmtRelative(s.updated_at)}
                          </span>
                        </button>
                      ))
                    ) : (
                      <p className="py-2.5 text-[13px] text-slate-400">
                        No chats yet — ask something above.
                      </p>
                    )}
                  </div>
                  <div>
                    <p className="mb-1.5 font-mono text-[11px] uppercase tracking-[0.08em] text-slate-400">
                      Needs your attention
                    </p>
                    {attention.length ? (
                      attention.map((a, i) => (
                        <Link
                          key={i}
                          href={a.href}
                          className="flex w-full items-center gap-3 border-b border-slate-200 py-2.5 text-left text-[13.5px] text-slate-600 last:border-0 hover:text-slate-900"
                        >
                          <span className="flex-1 truncate">{a.text}</span>
                          <span
                            className={cn(
                              "shrink-0 rounded-full border px-2 py-0.5 font-mono text-[10px]",
                              a.due
                                ? "border-warning/40 text-warning"
                                : "border-slate-200 text-slate-400",
                            )}
                          >
                            {a.pill}
                          </span>
                        </Link>
                      ))
                    ) : (
                      <p className="py-2.5 text-[13px] text-slate-400">
                        All clear — nothing needs you right now.
                      </p>
                    )}
                  </div>
                </div>

                <p className="mt-9 pb-6 text-center text-[11px] text-slate-400">
                  Aegis can err. Verify material conclusions with counsel — not
                  legal advice.
                </p>
              </div>
            </div>
          ) : (
            <>
              <div className="flex-1 overflow-y-auto">
                <div className="mx-auto w-full max-w-5xl space-y-8 px-6 py-10">
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
                          label={running ? `${toolLabel(running, false)}…` : "Thinking…"}
                          hint={
                            running && LONG_OPS.includes(running)
                              ? "this can take a minute or two"
                              : undefined
                          }
                          onStop={() => {
                            abortActiveStream();
                            setStreaming(false);
                          }}
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

              <div className="shrink-0 border-t border-slate-200 bg-slate-100">
                <div className="mx-auto w-full max-w-5xl px-6 py-4">
                  {workflow && (
                    <div className="mb-2 flex items-center gap-2 rounded border border-brand-200 bg-brand-50 px-3 py-1.5 text-xs text-brand-700">
                      <Wand2 className="h-3.5 w-3.5" />
                      <span className="font-medium">Prompt:</span>
                      {workflow.name}
                      <button
                        onClick={() => setWorkflow(null)}
                        className="ml-auto text-brand-500 hover:text-brand-700"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  )}
                  <div className="rounded-2xl border border-slate-300 bg-slate-100 px-4 py-3 transition focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-500/20">
                    <textarea
                      rows={1}
                      aria-label="Ask Aegis about a contract, a clause, or your whole portfolio"
                      placeholder="Ask about a contract, a clause, or your whole portfolio…"
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      onKeyDown={(e) =>
                        e.key === "Enter" &&
                        !e.shiftKey &&
                        (e.preventDefault(), send())
                      }
                      disabled={streaming || !!pending}
                      className="block max-h-44 w-full resize-none bg-transparent py-1.5 text-[15px] leading-6 text-slate-800 placeholder:text-slate-500 focus:outline-none disabled:opacity-60"
                    />
                    <div className="mt-2 flex items-center gap-4">
                      <button
                        onClick={() => setDocPickerOpen(true)}
                        disabled={streaming || !!pending}
                        className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 disabled:opacity-40"
                      >
                        <Plus className="h-4 w-4" />
                        Documents
                      </button>
                      <button
                        onClick={() => setWfModalOpen(true)}
                        disabled={streaming || !!pending}
                        className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 disabled:opacity-40"
                      >
                        <Wand2 className="h-4 w-4" />
                        Prompts
                      </button>
                      <button
                        onClick={() => setProjPickerOpen(true)}
                        disabled={streaming || !!pending}
                        className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 disabled:opacity-40"
                      >
                        <FolderKanban className="h-4 w-4" />
                        Matters
                      </button>
                      <span className="ml-auto mr-1 hidden items-center gap-1.5 font-mono text-[11px] text-success sm:inline-flex">
                        <span className="h-1.5 w-1.5 rounded-full bg-success" />
                        grounded
                      </span>
                      <button
                        onClick={send}
                        aria-label="Send"
                        disabled={streaming || !!pending || !input.trim()}
                        className="flex h-9 w-9 items-center justify-center rounded-full bg-brand-600 text-white transition-colors hover:bg-brand-700 disabled:opacity-30"
                      >
                        <ArrowUp className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                  <p className="mt-2 text-center text-xs text-slate-400">
                    AI can make mistakes. Answers are not legal advice.
                  </p>
                </div>
              </div>
            </>
          )}
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

      {/* Sessions drawer */}
      {railOpen && (
        <div className="fixed inset-0 z-40 flex">
          <div
            className="absolute inset-0 bg-slate-900/20"
            onClick={() => setRailOpen(false)}
          />
          <aside className="relative z-10 flex h-full w-72 flex-col border-r border-slate-200 bg-slate-100 shadow-xl">
            <div className="flex items-center justify-between border-b border-slate-100 p-3">
              <span className="text-sm font-semibold text-slate-900">
                {projectParam ? "Matter chats" : "Your chats"}
              </span>
              <button
                onClick={() => setRailOpen(false)}
                className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-2 border-b border-slate-100 p-3">
              <Button
                size="sm"
                className="w-full"
                onClick={() => {
                  void newSession();
                  setRailOpen(false);
                }}
              >
                <Plus className="h-3.5 w-3.5" />
                New chat
              </Button>
              {!projectParam && (
                <Select
                  value={newProjectId}
                  onChange={(e) => setNewProjectId(e.target.value)}
                  className="h-8 text-xs"
                >
                  <option value="">New chats: no matter</option>
                  {(projects ?? []).map((p) => (
                    <option key={p.id} value={p.id}>
                      New chats in: {p.name}
                    </option>
                  ))}
                </Select>
              )}
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                <Input
                  value={chatSearch}
                  onChange={(e) => setChatSearch(e.target.value)}
                  placeholder="Search chats..."
                  className="h-8 pl-8 text-xs"
                />
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-2">
              {isLoading ? (
                <CenterSpinner />
              ) : sessions?.length ? (
                uniqById(sessions).map((s) => (
                  <div key={s.id} className="group relative mb-1">
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
                        className="w-full rounded-lg border border-brand-300 bg-slate-100 px-3 py-2 text-sm font-medium text-slate-800 outline-none"
                      />
                    ) : (
                      <button
                        onClick={() => {
                          void selectSession(s);
                          setRailOpen(false);
                        }}
                        className={cn(
                          "w-full rounded-lg px-3 py-2 pr-8 text-left transition-colors",
                          activeSession?.id === s.id
                            ? "bg-brand-50"
                            : "hover:bg-slate-50",
                        )}
                      >
                        <p className="truncate text-sm font-medium text-slate-800">
                          {s.title ?? "Untitled"}
                        </p>
                        <div className="mt-0.5 flex items-center gap-1.5">
                          {s.contract_id && (
                            <FileText className="h-3 w-3 text-slate-400" />
                          )}
                          {s.matter_id && (
                            <span className="inline-flex items-center gap-1 rounded bg-brand-50 px-1.5 py-0.5 text-[10px] font-medium text-brand-700">
                              <FolderKanban className="h-3 w-3" />
                              {projectName(s.matter_id)}
                            </span>
                          )}
                          <span className="text-xs text-slate-400">
                            {fmtRelative(s.updated_at)}
                          </span>
                        </div>
                      </button>
                    )}
                    {renamingSessionId !== s.id && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setRenameValue(s.title ?? "");
                          setRenamingSessionId(s.id);
                        }}
                        title="Rename chat"
                        className="absolute right-1.5 top-1.5 rounded p-1 text-slate-300 opacity-0 hover:bg-slate-200 hover:text-slate-600 group-hover:opacity-100"
                      >
                        <Pencil className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                ))
              ) : (
                <p className="p-4 text-center text-sm text-slate-400">
                  No chats yet.
                </p>
              )}
            </div>
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
      <MatterPickerModal
        open={projPickerOpen}
        onClose={() => setProjPickerOpen(false)}
        onPick={(p) => {
          setNewProjectId(p.id);
          setProjPickerOpen(false);
          notify(`New chats will be scoped to "${p.name}"`, "success");
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
  ask_contract_brain: [
    "Consulting Contract Brain",
    "Consulted Contract Brain",
  ],
  extract_obligations: [
    "Extracting obligations",
    "Obligations extracted",
  ],
  list_playbooks: ["Looking up playbooks", "Found playbooks"],
  list_workflows: ["Looking up prompts", "Found prompts"],
  list_project_contracts: [
    "Listing project contracts",
    "Listed project contracts",
  ],
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
  submit_for_approval: [
    "Submitting for approval",
    "Submitted for approval",
  ],
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
  list_projects: ["Looking up projects", "Found projects"],
  read_project: ["Reading the project", "Read the project"],
  find_contracts: ["Searching contracts", "Found contracts"],
  my_attention_items: ["Checking what needs you", "Found your items"],
};

// Tools whose result the user should be able to open. Intake-request tools
// return {id, ref} → /intake?open=<id>; contract-read tools return {contract_id}
// → /contracts/<id>. We surface a click-through so the chat hands off to the
// real page instead of the model inventing a (dead) link.
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
    submit_for_approval: {
      title: "Submit for approval?",
      body: "I'll submit this contract into the approval workflow.",
    },
    run_workflow: {
      title: "Run this prompt?",
      body: "I'll run the selected prompt on this contract.",
    },
    replicate_contract_version: {
      title: "Duplicate this contract?",
      body: "I'll create a copy of this contract version.",
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
    <div className="space-y-3">
      {groups.map((g, i) =>
        g.k === "steps" ? (
          <TimelineStepCard key={i} tools={g.tools} />
        ) : (
          <div className="answer-serif" key={i}>
            <Markdown>{g.text}</Markdown>
          </div>
        ),
      )}
      {item.citations && <CitationList citations={item.citations} onCite={onCite} />}
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
  onStop,
}: {
  label: string;
  hint?: string;
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
        className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-300 bg-slate-100 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:text-slate-900"
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
      <div className="flex items-center gap-2 text-xs text-slate-400">
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
    return (
      <div data-user-msg className="flex justify-end scroll-mt-6">
        <div className="max-w-[80%] space-y-1.5">
          {item.workflow && (
            <div className="flex justify-end">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-700">
                <Wand2 className="h-3 w-3" />
                {item.workflow.name}
              </span>
            </div>
          )}
          <div className="whitespace-pre-wrap rounded-2xl bg-slate-900 px-4 py-2.5 text-[15px] leading-7 text-white">
            {item.text}
          </div>
        </div>
      </div>
    );
  }

  // assistant — borderless prose, Claude style
  return (
    <div className="space-y-3">
      {item.workflow && (
        <button
          onClick={onReopenWorkflow}
          className="inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-700 hover:bg-brand-100"
        >
          <Wand2 className="h-3 w-3" />
          {item.workflow.name}
        </button>
      )}
      {item.text ? (
        <div className="answer-serif">
          <Markdown>{item.text}</Markdown>
        </div>
      ) : (
        <span className="text-slate-300">…</span>
      )}
      {item.citations && <CitationList citations={item.citations} onCite={onCite} />}
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

function MatterPickerModal({
  open,
  onClose,
  onPick,
}: {
  open: boolean;
  onClose: () => void;
  onPick: (p: MatterResponse) => void;
}) {
  const [q, setQ] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: mattersApi.list,
    enabled: open,
  });
  const items = uniqById(data).filter((p) =>
    p.name.toLowerCase().includes(q.toLowerCase()),
  );
  return (
    <Modal open={open} onClose={onClose} title="Scope this chat to a matter">
      <div className="space-y-3">
        <Input
          autoFocus
          placeholder="Search matters…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="max-h-80 space-y-1 overflow-y-auto">
          {isLoading ? (
            <CenterSpinner />
          ) : items.length ? (
            items.map((p) => (
              <button
                key={p.id}
                onClick={() => onPick(p)}
                className="flex w-full items-center gap-2.5 rounded-lg border border-slate-200 px-3 py-2 text-left text-sm transition-colors hover:border-brand-300 hover:bg-brand-50/50"
              >
                <FolderKanban className="h-4 w-4 shrink-0 text-slate-400" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-slate-800">
                    {p.name}
                  </span>
                  {p.description && (
                    <span className="block truncate text-xs text-slate-400">
                      {p.description}
                    </span>
                  )}
                </span>
              </button>
            ))
          ) : (
            <p className="py-6 text-center text-sm text-slate-400">
              No projects found.
            </p>
          )}
        </div>
      </div>
    </Modal>
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
  const preview = hovered ?? selected;
  const all = workflows ?? [];
  const groups = [
    {
      label: "Assistant prompts",
      items: all.filter((w) => w.workflow_type === "assistant"),
    },
    {
      label: "Document review prompts",
      items: all.filter((w) => w.workflow_type === "tabular_review"),
    },
    {
      label: "Other prompts",
      items: all.filter(
        (w) =>
          w.workflow_type !== "assistant" &&
          w.workflow_type !== "tabular_review",
      ),
    },
  ].filter((g) => g.items.length > 0);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Apply a prompt"
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!selected}
            onClick={() => selected && onPick(selected)}
          >
            <Wand2 className="h-4 w-4" />
            Use prompt
          </Button>
        </>
      }
    >
      {isLoading ? (
        <CenterSpinner />
      ) : (
        <div className="grid grid-cols-2 gap-4">
          <div
            className="max-h-[60vh] space-y-4 overflow-y-auto pr-1"
            onMouseLeave={() => setHovered(null)}
          >
            {all.length === 0 && (
              <p className="p-4 text-center text-sm text-slate-400">
                No prompts available.
              </p>
            )}
            {groups.map((g) => (
              <div key={g.label} className="space-y-1.5">
                <p className="px-1 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
                  {g.label} · {g.items.length}
                </p>
                {g.items.map((w) => (
                  <button
                    key={w.id}
                    onClick={() => setSelected(w)}
                    onMouseEnter={() => setHovered(w)}
                    onFocus={() => setHovered(w)}
                    className={cn(
                      "w-full rounded-lg border p-3 text-left transition-colors",
                      selected?.id === w.id
                        ? "border-brand-400 bg-brand-50"
                        : preview?.id === w.id
                          ? "border-slate-300 bg-slate-50"
                          : "border-slate-200 hover:bg-slate-50",
                    )}
                  >
                    <p className="text-sm font-medium text-slate-900">{w.name}</p>
                    <p className="mt-0.5 line-clamp-2 text-xs text-slate-500">
                      {w.description}
                    </p>
                  </button>
                ))}
              </div>
            ))}
          </div>
          <div className="max-h-[60vh] overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 p-4">
            {preview ? (
              (() => {
                const cols = workflowColumns(preview);
                const prompt = (preview.definition as { prompt?: string })
                  ?.prompt;
                return (
                  <>
                    <p className="text-sm font-semibold text-slate-900">
                      {preview.name}
                    </p>
                    {preview.description && (
                      <p className="mt-0.5 text-xs text-slate-500">
                        {preview.description}
                      </p>
                    )}
                    {prompt ? (
                      <>
                        <p className="mt-3 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
                          What it does
                        </p>
                        <p className="mt-1 whitespace-pre-wrap text-sm text-slate-700">
                          {prompt}
                        </p>
                      </>
                    ) : cols.length ? (
                      <>
                        <p className="mt-3 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
                          Extracts {cols.length} field
                          {cols.length === 1 ? "" : "s"}
                        </p>
                        <ul className="mt-1 space-y-1.5">
                          {cols.map((c, i) => (
                            <li key={i} className="text-sm text-slate-700">
                              <span className="font-medium">{c.name}</span>
                              {c.prompt && (
                                <span className="mt-0.5 block text-xs text-slate-500">
                                  {c.prompt}
                                </span>
                              )}
                            </li>
                          ))}
                        </ul>
                      </>
                    ) : (
                      <p className="mt-3 text-sm text-slate-400">
                        This workflow has no preview.
                      </p>
                    )}
                  </>
                );
              })()
            ) : (
              <p className="text-sm text-slate-400">
                Hover a prompt to preview its details, then click to select it.
              </p>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}
