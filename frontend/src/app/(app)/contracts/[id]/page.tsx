"use client";

import { use, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Download,
  RotateCcw,
  Check,
  X,
  Send,
  ChevronRight,
  BookMarked,
  Sparkles,
  ListChecks,
  ClipboardCheck,
  FileSignature,
  FileDiff,
  ScanSearch,
  Wand2,
  Info,
  History,
  Activity as ActivityIcon,
  CheckCircle2,
  Circle,
  AlertTriangle,
  ArrowRight,
  MessageSquare,
  Trash2,
  Handshake,
  Copy,
  GitCompare,
  MoreHorizontal,
} from "lucide-react";
import {
  aiApi,
  approvalsApi,
  assistantApi,
  brainApi,
  contractsApi,
  obligationsApi,
  playbooksApi,
  signaturesApi,
} from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  CenterSpinner,
  ErrorState,
  Field,
  Input,
  Modal,
  Select,
  Spinner,
  Textarea,
} from "@/components/ui";
import {
  cn,
  fmtDate,
  fmtDateTime,
  fmtMoney,
  riskTone,
  titleCase,
} from "@/lib/utils";
import { useToast } from "@/components/toast";
import { useLayout } from "@/lib/layout";
import { ContractDocument } from "@/components/contract-document";
import { Markdown } from "@/components/markdown";
import { apiStream } from "@/lib/api";
import type {
  Citation,
  ContractComment,
  ContractLifecycleStage,
  ContractShareResponse,
  ContractVersionResponse,
  ReviewStatusResponse,
} from "@/lib/types";

type PanelId = "redlines" | "versions" | "comments" | "negotiation" | "ask";

// The full contract lifecycle, in order — drives the stage stepper.
const LIFECYCLE_STAGES: ContractLifecycleStage[] = [
  "intake",
  "drafting",
  "review",
  "approval",
  "signature",
  "active",
  "closed",
];

const PANELS: {
  id: PanelId;
  label: string;
  hint: string;
  icon: typeof Wand2;
}[] = [
  { id: "redlines", label: "Redlines", hint: "Tracked changes", icon: FileDiff },
  { id: "versions", label: "Versions", hint: "Document history", icon: History },
  { id: "comments", label: "Comments", hint: "Discussion", icon: MessageSquare },
  { id: "negotiation", label: "Negotiation", hint: "Counterparty rounds", icon: Handshake },
  { id: "ask", label: "Ask AI", hint: "Edit & analyze", icon: Sparkles },
];

const CTA_LABEL: Record<string, string> = {
  run_ai: "Run AI review",
  resolve_issues: "Review issues",
  resolve_redlines: "Review redlines",
  resolve_comments: "Review comments",
  submit_approval: "Submit for approval",
  send_signature: "Send for signature",
  view_approvals: "View approvals",
  view_obligations: "View obligations",
};

// Actions the "Next step" hero can ask the page to perform. The early-stage set
// comes from the review-status engine; the later-stage ones are stage-driven.
type HeroAction =
  | "run_ai"
  | "resolve_issues"
  | "resolve_redlines"
  | "resolve_comments"
  | "submit_approval"
  | "send_signature"
  | "view_approvals"
  | "view_obligations";

// For stages past review the review-status engine returns no action, so the
// hero supplies its own stage-appropriate guidance and primary action.
const LATER_STAGE_HERO: Record<
  string,
  {
    msg: string;
    action: HeroAction | null;
    label: string | null;
    icon: typeof Wand2;
    tone: "brand" | "emerald" | "slate";
  }
> = {
  approval: {
    msg: "In approval — awaiting decisions from the approval chain.",
    action: "view_approvals",
    label: "View approvals",
    icon: ClipboardCheck,
    tone: "brand",
  },
  signature: {
    msg: "Ready to sign — send to the counterparty for signature.",
    action: "send_signature",
    label: "Send for signature",
    icon: FileSignature,
    tone: "brand",
  },
  active: {
    msg: "Active — monitor obligations and upcoming renewals.",
    action: "view_obligations",
    label: "View obligations",
    icon: ListChecks,
    tone: "emerald",
  },
  closed: {
    msg: "Closed — this contract is complete.",
    action: null,
    label: null,
    icon: CheckCircle2,
    tone: "slate",
  },
};

// Internal/shared discussion thread for a contract. Posting/resolving also
// refreshes the review-status rail (open comments are a "next step" signal).
function CommentsPanel({ contractId }: { contractId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [body, setBody] = useState("");
  const [visibility, setVisibility] = useState<"internal" | "shared">("internal");
  const [busy, setBusy] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["contract-comments", contractId],
    queryFn: () => contractsApi.comments(contractId),
  });

  function refresh() {
    qc.invalidateQueries({ queryKey: ["contract-comments", contractId] });
    qc.invalidateQueries({ queryKey: ["review-status", contractId] });
  }

  async function post() {
    if (!body.trim()) return;
    setBusy(true);
    try {
      await contractsApi.addComment(contractId, { body: body.trim(), visibility });
      setBody("");
      refresh();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to post", "error");
    } finally {
      setBusy(false);
    }
  }

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      refresh();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed", "error");
    }
  }

  const comments = data ?? [];

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-slate-200 bg-slate-100 p-3">
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Add a comment…"
          rows={3}
          className="w-full resize-none rounded-md border border-slate-200 p-2 text-sm focus:border-brand-400 focus:outline-none focus:ring-1 focus:ring-brand-400"
        />
        <div className="mt-2 flex items-center justify-between">
          <div className="flex items-center gap-1 rounded-md bg-slate-200 p-0.5 text-xs">
            {(["internal", "shared"] as const).map((v) => (
              <button
                key={v}
                onClick={() => setVisibility(v)}
                className={cn(
                  "rounded px-2 py-1 font-medium capitalize",
                  visibility === v ? "bg-slate-100 text-slate-900 shadow-sm" : "text-slate-500",
                )}
              >
                {v}
              </button>
            ))}
          </div>
          <Button size="sm" loading={busy} disabled={!body.trim()} onClick={post}>
            Comment
          </Button>
        </div>
        <p className="mt-1.5 text-[11px] text-slate-400">
          {visibility === "internal"
            ? "Internal — visible to your team only."
            : "Shared — may be shown to the counterparty."}
        </p>
      </div>

      {isLoading ? (
        <Spinner />
      ) : comments.length === 0 ? (
        <p className="text-sm text-slate-500">No comments yet.</p>
      ) : (
        <div className="space-y-2">
          {comments.map((c) => (
            <div
              key={c.id}
              className={cn(
                "rounded-lg border p-3",
                c.resolved
                  ? "border-slate-200 bg-slate-50 opacity-70"
                  : "border-slate-200 bg-slate-100",
              )}
            >
              <div className="mb-1 flex items-center gap-2">
                <span className="text-sm font-medium text-slate-800">{c.author_name}</span>
                <Badge tone={c.visibility === "shared" ? "blue" : "slate"}>{c.visibility}</Badge>
                {c.resolved && <Badge tone="green">Resolved</Badge>}
                <span className="ml-auto text-[11px] text-slate-400">
                  {new Date(c.created_at).toLocaleString()}
                </span>
              </div>
              <p className="whitespace-pre-wrap text-sm text-slate-700">{c.body}</p>
              <div className="mt-2 flex items-center gap-3 text-xs">
                <button
                  onClick={() => act(() => contractsApi.resolveComment(contractId, c.id, !c.resolved))}
                  className="font-medium text-slate-500 hover:text-slate-900"
                >
                  {c.resolved ? "Reopen" : "Resolve"}
                </button>
                <button
                  onClick={() => act(() => contractsApi.deleteComment(contractId, c.id))}
                  className="inline-flex items-center gap-1 text-slate-400 hover:text-rose-600"
                >
                  <Trash2 className="h-3.5 w-3.5" /> Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Phase 3: counterparty negotiation ----------------------------------
function genPasscode(): string {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  let out = "";
  for (let i = 0; i < 8; i++) out += chars[Math.floor(Math.random() * chars.length)];
  return out;
}

function shareBadge(s: ContractShareResponse) {
  if (s.revoked_at) return <Badge tone="slate">Revoked</Badge>;
  if (s.expires_at && new Date(s.expires_at) < new Date())
    return <Badge tone="amber">Expired</Badge>;
  return <Badge tone="green">Active</Badge>;
}

type Round =
  | { kind: "sent"; at: string; share: ContractShareResponse }
  | { kind: "received"; at: string; version: ContractVersionResponse };

function NegotiationPanel({ contractId }: { contractId: string }) {
  const qc = useQueryClient();
  const [sendOpen, setSendOpen] = useState(false);
  const [diff, setDiff] = useState<{ base: string; target: string } | null>(null);

  const { data: shares } = useQuery({
    queryKey: ["contract-shares", contractId],
    queryFn: () => contractsApi.shares(contractId),
  });
  const { data: versions } = useQuery({
    queryKey: ["contract", contractId, "versions"],
    queryFn: () => contractsApi.versions(contractId),
  });

  const sorted = [...(versions ?? [])].sort((a, b) => a.version_number - b.version_number);
  const prevVersionId = (v: ContractVersionResponse): string | null => {
    const idx = sorted.findIndex((x) => x.id === v.id);
    return idx > 0 ? sorted[idx - 1].id : null;
  };

  const rounds: Round[] = [
    ...(shares ?? []).map((s): Round => ({ kind: "sent", at: s.created_at, share: s })),
    ...(versions ?? [])
      .filter((v) => v.source === "counterparty_revision")
      .map((v): Round => ({ kind: "received", at: v.created_at, version: v })),
  ].sort((a, b) => (a.at < b.at ? 1 : -1));

  function refresh() {
    qc.invalidateQueries({ queryKey: ["contract-shares", contractId] });
    qc.invalidateQueries({ queryKey: ["review-status", contractId] });
  }

  return (
    <div className="space-y-4">
      <Button className="w-full" onClick={() => setSendOpen(true)}>
        <Handshake className="h-4 w-4" />
        Send to counterparty
      </Button>

      {rounds.length === 0 ? (
        <p className="text-sm text-slate-500">
          No counterparty activity yet. Send the contract to start a negotiation round.
        </p>
      ) : (
        <div className="space-y-2">
          {rounds.map((r, i) =>
            r.kind === "sent" ? (
              <div key={i} className="rounded-lg border border-slate-200 bg-slate-100 p-3">
                <div className="flex items-center gap-2 text-sm">
                  <Send className="h-3.5 w-3.5 text-slate-400" />
                  <span className="font-medium text-slate-700">Sent to counterparty</span>
                  {shareBadge(r.share)}
                  <span className="ml-auto text-[11px] text-slate-400">
                    {new Date(r.at).toLocaleString()}
                  </span>
                </div>
              </div>
            ) : (
              <div key={i} className="rounded-lg border border-slate-200 bg-slate-100 p-3">
                <div className="flex items-center gap-2 text-sm">
                  <Download className="h-3.5 w-3.5 text-slate-400" />
                  <span className="font-medium text-slate-700">
                    Counterparty returned v{r.version.version_number}
                  </span>
                  <span className="ml-auto text-[11px] text-slate-400">
                    {new Date(r.at).toLocaleString()}
                  </span>
                </div>
                {r.version.change_summary && (
                  <p className="mt-1 text-xs text-slate-500">{r.version.change_summary}</p>
                )}
                {prevVersionId(r.version) && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="mt-2"
                    onClick={() =>
                      setDiff({ base: prevVersionId(r.version)!, target: r.version.id })
                    }
                  >
                    <GitCompare className="h-3.5 w-3.5" />
                    View changes
                  </Button>
                )}
              </div>
            ),
          )}
        </div>
      )}

      <SendCounterpartyModal
        contractId={contractId}
        open={sendOpen}
        onClose={() => setSendOpen(false)}
        onSent={refresh}
      />
      <DiffModal
        contractId={contractId}
        baseId={diff?.base ?? null}
        targetId={diff?.target ?? null}
        open={!!diff}
        onClose={() => setDiff(null)}
      />
    </div>
  );
}

function SendCounterpartyModal({
  contractId,
  open,
  onClose,
  onSent,
}: {
  contractId: string;
  open: boolean;
  onClose: () => void;
  onSent: () => void;
}) {
  const { notify } = useToast();
  const [passcode, setPasscode] = useState(genPasscode());
  const [expiryDays, setExpiryDays] = useState("14");
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (open) {
      setPasscode(genPasscode());
      setExpiryDays("14");
      setLink(null);
      setCopied(false);
    }
  }, [open]);

  async function create() {
    setBusy(true);
    try {
      const days = Number(expiryDays) || 0;
      const expires_at =
        days > 0 ? new Date(Date.now() + days * 86_400_000).toISOString() : undefined;
      const res = await contractsApi.createShare(contractId, {
        access_mode: "view_only",
        passcode,
        expires_at,
      });
      setLink(`${window.location.origin}/s/${res.token}?p=${encodeURIComponent(passcode)}`);
      onSent();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to create link", "error");
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      notify("Copy failed — select the link and copy manually", "error");
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Send to counterparty"
      footer={
        link ? (
          <Button onClick={onClose}>Done</Button>
        ) : (
          <>
            <Button variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={create} loading={busy} disabled={passcode.length < 8}>
              Create link
            </Button>
          </>
        )
      }
    >
      {link ? (
        <div className="space-y-3">
          <p className="text-sm text-slate-600">
            Share this secure link. The counterparty can view the document and leave comments —
            no account needed.
          </p>
          <div className="flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 p-2">
            <input
              readOnly
              value={link}
              className="flex-1 bg-transparent text-xs text-slate-700 outline-none"
            />
            <Button size="sm" variant="outline" onClick={copy}>
              <Copy className="h-3.5 w-3.5" />
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <p className="text-xs text-slate-500">
            Passcode <span className="font-mono font-medium text-slate-700">{passcode}</span> is
            embedded in the link. Send it via a separate channel if you prefer.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          <Field label="Passcode" hint="At least 8 characters; the counterparty needs it to open the link.">
            <div className="flex gap-2">
              <Input value={passcode} onChange={(e) => setPasscode(e.target.value)} />
              <Button size="sm" variant="outline" onClick={() => setPasscode(genPasscode())}>
                Regenerate
              </Button>
            </div>
          </Field>
          <Field label="Link expires in">
            <Select value={expiryDays} onChange={(e) => setExpiryDays(e.target.value)}>
              <option value="7">7 days</option>
              <option value="14">14 days</option>
              <option value="30">30 days</option>
              <option value="0">No expiry</option>
            </Select>
          </Field>
          <p className="text-xs text-slate-500">
            Creates a view-only, passcode-protected link. They can read the document and add
            shared comments that flow back into the Comments tab.
          </p>
        </div>
      )}
    </Modal>
  );
}

function DiffModal({
  contractId,
  baseId,
  targetId,
  open,
  onClose,
}: {
  contractId: string;
  baseId: string | null;
  targetId: string | null;
  open: boolean;
  onClose: () => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["version-diff", contractId, baseId, targetId],
    queryFn: () => contractsApi.versionDiff(contractId, baseId!, targetId!),
    enabled: open && !!baseId && !!targetId,
  });

  return (
    <Modal open={open} onClose={onClose} title="Counterparty changes" size="lg">
      {isLoading || !data ? (
        <Spinner />
      ) : (
        <div className="space-y-3">
          <div className="flex items-center gap-3 text-sm">
            <Badge tone="green">+{data.added}</Badge>
            <Badge tone="red">−{data.removed}</Badge>
            <span className="text-slate-500">
              v{data.base_version_number} → v{data.target_version_number}
            </span>
          </div>
          <div className="max-h-[60vh] overflow-y-auto rounded-md border border-slate-200 font-mono text-xs">
            {data.lines.map((l, i) => (
              <div
                key={i}
                className={cn(
                  "whitespace-pre-wrap px-3 py-0.5",
                  l.type === "add"
                    ? "bg-emerald-50 text-emerald-800"
                    : l.type === "remove"
                      ? "bg-rose-50 text-rose-800"
                      : "text-slate-600",
                )}
              >
                <span className="select-none opacity-50">
                  {l.type === "add" ? "+ " : l.type === "remove" ? "− " : "  "}
                </span>
                {l.text || " "}
              </div>
            ))}
          </div>
          {data.truncated && (
            <p className="text-xs text-amber-600">Diff truncated for a large document.</p>
          )}
        </div>
      )}
    </Modal>
  );
}

// Guided "what to do next" rail — derived from /contracts/{id}/review-status.
// Only shown while the contract is pre-approval (intake/drafting/review).
// Which main-component busy flag corresponds to each rail action (for spinners).
const ACTION_BUSY: Record<string, string> = {
  run_ai: "analyze",
  submit_approval: "approve",
};

function NextStepHero({
  contract,
  busyAction,
  onAction,
}: {
  contract: {
    id: string;
    lifecycle_stage: ContractLifecycleStage;
    counterparty_name?: string | null;
  };
  busyAction: string | null;
  onAction: (action: HeroAction) => void;
}) {
  const { data } = useQuery({
    queryKey: ["review-status", contract.id],
    queryFn: () => contractsApi.reviewStatus(contract.id),
  });

  const chipIcon = (status: string) =>
    status === "done" ? (
      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
    ) : status === "blocked" ? (
      <AlertTriangle className="h-3.5 w-3.5 text-rose-500" />
    ) : status === "in_progress" ? (
      <Circle className="h-3.5 w-3.5 fill-amber-400 text-amber-400" />
    ) : (
      <Circle className="h-3.5 w-3.5 text-slate-300" />
    );

  const TONE = {
    brand: { band: "bg-brand-50/60", icon: "bg-brand-100 text-brand-700" },
    emerald: { band: "bg-emerald-50/60", icon: "bg-emerald-100 text-emerald-700" },
    slate: { band: "bg-slate-50", icon: "bg-slate-100 text-slate-500" },
  };

  const early = ["intake", "drafting", "review"].includes(
    contract.lifecycle_stage,
  );

  // Later stages: stage-driven guidance + a single primary action.
  if (!early) {
    const h = LATER_STAGE_HERO[contract.lifecycle_stage];
    if (!h) return null;
    const tone = TONE[h.tone];
    const Icon = h.icon;
    const msg =
      contract.lifecycle_stage === "signature" && contract.counterparty_name
        ? `Ready to sign — send to ${contract.counterparty_name} for signature.`
        : h.msg;
    return (
      <div
        className={cn(
          "shrink-0 border-b border-slate-200 px-4 py-3",
          tone.band,
        )}
      >
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <span
            className={cn(
              "flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
              tone.icon,
            )}
          >
            <Icon className="h-4 w-4" />
          </span>
          <p className="min-w-0 flex-1 text-sm font-medium text-slate-800">
            {msg}
          </p>
          {h.action && (
            <Button
              size="sm"
              variant={h.tone === "slate" ? "outline" : "primary"}
              loading={busyAction === ACTION_BUSY[h.action]}
              onClick={() => h.action && onAction(h.action)}
            >
              {h.label}
              <ArrowRight className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>
    );
  }

  // Early stages (intake/drafting/review): guided review from the engine.
  const review: ReviewStatusResponse | undefined = data;
  if (!review) return null;
  return (
    <div className="shrink-0 border-b border-slate-200 bg-brand-50/60 px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-100 text-brand-700">
          <Wand2 className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-brand-500">
            Next step
          </p>
          <p className="text-sm font-medium text-slate-800">
            {review.next_step}
          </p>
        </div>
        {review.next_action && (
          <Button
            size="sm"
            loading={busyAction === ACTION_BUSY[review.next_action]}
            onClick={() => onAction(review.next_action as HeroAction)}
          >
            {CTA_LABEL[review.next_action] ?? "Next"}
            <ArrowRight className="h-4 w-4" />
          </Button>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1">
          {review.checklist.map((c) => (
            <span
              key={c.key}
              className="inline-flex items-center gap-1.5 text-xs text-slate-500"
              title={c.detail ?? undefined}
            >
              {chipIcon(c.status)}
              <span className={c.status === "done" ? "text-slate-400" : ""}>
                {c.label}
                {c.count ? ` (${c.count})` : ""}
              </span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function ContractDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [panel, setPanel] = useState<PanelId>("ask");
  const [activeEditId, setActiveEditId] = useState<string | null>(null);
  const [runPbOpen, setRunPbOpen] = useState(false);
  const [sigOpen, setSigOpen] = useState(false);
  // Ask AI is a tab in the right panel; mount it on first use and keep it
  // mounted (hidden when inactive) so the chat survives tab switches.
  const [askMounted, setAskMounted] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const qc = useQueryClient();
  const { notify } = useToast();
  const { setForceCollapsed } = useLayout();
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);

  // The workspace needs the room — collapse the main nav to an icon rail while
  // the contract document pane is open.
  useEffect(() => {
    setForceCollapsed(true);
    return () => setForceCollapsed(false);
  }, [setForceCollapsed]);

  const { data: contract, isLoading, error } = useQuery({
    queryKey: ["contract", id],
    queryFn: () => contractsApi.get(id),
  });
  const { data: edits } = useQuery({
    queryKey: ["contract", id, "edits"],
    queryFn: () => contractsApi.edits(id),
  });

  const pendingRedlines = (edits ?? []).filter(
    (e) => e.status === "proposed",
  ).length;

  async function reAnalyze() {
    setBusy("analyze");
    try {
      await Promise.all([aiApi.rerunMetadata(id), aiApi.rerunClauses(id)]);
      qc.invalidateQueries({ queryKey: ["contract", id] });
      qc.invalidateQueries({ queryKey: ["review-status", id] });
      notify("Re-analysis complete — metadata & clauses refreshed", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Analysis failed", "error");
    } finally {
      setBusy(null);
    }
  }
  async function extractObligations() {
    setBusy("oblig");
    try {
      await obligationsApi.extract(id);
      notify("Obligation extraction queued", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed", "error");
    } finally {
      setBusy(null);
    }
  }
  async function submitApproval() {
    setBusy("approve");
    try {
      await approvalsApi.submit({ contract_id: id });
      qc.invalidateQueries({ queryKey: ["contract", id] });
      qc.invalidateQueries({ queryKey: ["review-status", id] });
      notify("Submitted for approval", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed", "error");
    } finally {
      setBusy(null);
    }
  }

  // The "Next step" hero asks for a stage action; route it to the right handler.
  function handleHeroAction(action: HeroAction) {
    switch (action) {
      case "run_ai":
        reAnalyze();
        break;
      case "resolve_issues":
      case "resolve_redlines":
        setPanel("redlines");
        break;
      case "resolve_comments":
        setPanel("comments");
        break;
      case "submit_approval":
        submitApproval();
        break;
      case "send_signature":
        setSigOpen(true);
        break;
      case "view_approvals":
        router.push("/approvals");
        break;
      case "view_obligations":
        router.push("/obligations");
        break;
    }
  }

  // Everything that used to crowd the header now lives in one "More" menu.
  const moreActions: {
    key: string;
    label: string;
    icon: typeof Wand2;
    onClick: () => void;
    loading?: boolean;
  }[] = [
    { key: "playbook", label: "Run playbook", icon: BookMarked, onClick: () => setRunPbOpen(true) },
    { key: "analyze", label: "Re-run analysis", icon: ScanSearch, onClick: reAnalyze, loading: busy === "analyze" },
    { key: "oblig", label: "Extract obligations", icon: ListChecks, onClick: extractObligations, loading: busy === "oblig" },
    { key: "approve", label: "Submit for approval", icon: ClipboardCheck, onClick: submitApproval, loading: busy === "approve" },
    { key: "sign", label: "Send for signature", icon: FileSignature, onClick: () => setSigOpen(true) },
    { key: "ask", label: "Ask AI", icon: Sparkles, onClick: () => { setAskMounted(true); setPanel("ask"); } },
  ];

  if (isLoading) return <CenterSpinner label="Loading contract…" />;
  if (error) return <ErrorState error={error} />;
  if (!contract) return null;

  return (
    <div className="flex h-full flex-col">
      {/* Slim header — title + status; every action lives in the More menu. */}
      <div className="shrink-0 border-b border-slate-200 bg-slate-100 px-4 py-2.5">
        <div className="flex items-center gap-3">
          <Link
            href="/contract-hub"
            title="Contract Hub"
            aria-label="Back to Contract Hub"
            className="shrink-0 text-slate-400 transition-colors hover:text-slate-700"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <h1 className="truncate text-base font-semibold tracking-tight text-slate-900">
              {contract.title}
            </h1>
            {contract.risk_level && (
              <Badge tone={riskTone(contract.risk_level)}>
                {titleCase(contract.risk_level)} risk
              </Badge>
            )}
            {contract.counterparty_name && (
              <span className="hidden truncate text-sm text-slate-500 sm:inline">
                vs. {contract.counterparty_name}
              </span>
            )}
          </div>
          <div className="relative shrink-0">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setMenuOpen((o) => !o)}
            >
              <MoreHorizontal className="h-4 w-4" />
              More
            </Button>
            {menuOpen && (
              <>
                <div
                  className="fixed inset-0 z-20"
                  onClick={() => setMenuOpen(false)}
                />
                <div className="absolute right-0 z-30 mt-1.5 w-56 rounded-xl border border-slate-200 bg-slate-100 p-1.5 shadow-pop">
                  {moreActions.map((a) => {
                    const Icon = a.icon;
                    return (
                      <button
                        key={a.key}
                        disabled={a.loading}
                        onClick={() => {
                          setMenuOpen(false);
                          a.onClick();
                        }}
                        className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 disabled:opacity-50"
                      >
                        <Icon className="h-4 w-4 text-slate-400" />
                        {a.label}
                        {a.loading && (
                          <span className="ml-auto text-xs text-slate-400">…</span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <OverviewBar contractId={id} />

      <NextStepHero
        contract={contract}
        busyAction={busy}
        onAction={handleHeroAction}
      />

      <div className="flex flex-1 overflow-hidden">
        {/* The document is the focus; it fills the whole left column. */}
        <div className="hidden flex-1 flex-col overflow-hidden lg:flex">
          <div className="min-h-0 flex-1 bg-slate-50 p-4">
            <ContractDocument
              contractId={id}
              edits={edits ?? []}
              activeEditId={activeEditId}
              onSelectEdit={(eid) => {
                setActiveEditId(eid);
                setPanel("redlines");
                requestAnimationFrame(() =>
                  document
                    .getElementById(`redcard-${eid}`)
                    ?.scrollIntoView({ behavior: "smooth", block: "center" }),
                );
              }}
            />
          </div>
        </div>

        {/* Right: Redlines / Versions / Ask AI as switchable tabs, so the
            document stays put while you move between them. */}
        <section className="flex flex-1 flex-col bg-slate-100 lg:w-[26rem] lg:flex-none lg:shrink-0 lg:border-l lg:border-slate-200">
          <div className="flex shrink-0 items-center gap-1.5 border-b border-slate-200 p-2">
            {/* Ask Aegis is the featured view; the review tools are compact icons. */}
            <button
              onClick={() => {
                setAskMounted(true);
                setPanel("ask");
              }}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors",
                panel === "ask"
                  ? "bg-brand-50 text-brand-700"
                  : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
              )}
            >
              <Sparkles className="h-4 w-4" />
              Ask Aegis
            </button>
            <div className="mx-0.5 h-5 w-px bg-slate-200" />
            {PANELS.filter((p) => p.id !== "ask").map((p) => {
              const active = panel === p.id;
              const Icon = p.icon;
              return (
                <button
                  key={p.id}
                  onClick={() => setPanel(p.id)}
                  title={p.label}
                  aria-label={p.label}
                  className={cn(
                    "relative flex h-9 w-9 items-center justify-center rounded-lg transition-colors",
                    active
                      ? "bg-brand-50 text-brand-700"
                      : "text-slate-500 hover:bg-slate-100 hover:text-slate-900",
                  )}
                >
                  <Icon className="h-4 w-4" />
                  {p.id === "redlines" && pendingRedlines > 0 && (
                    <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-100 px-1 text-[10px] font-semibold text-amber-700">
                      {pendingRedlines}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <div className="relative flex-1 overflow-hidden">
            {panel !== "ask" && (
              <div className="h-full overflow-y-auto p-4">
                {panel === "versions" && <Versions contractId={id} />}
                {panel === "comments" && <CommentsPanel contractId={id} />}
                {panel === "negotiation" && <NegotiationPanel contractId={id} />}
                {panel === "redlines" && (
                  <Redlines
                    contractId={id}
                    onGenerate={() => setRunPbOpen(true)}
                    activeEditId={activeEditId}
                    onSelect={(eid) => {
                      setActiveEditId(eid);
                      requestAnimationFrame(() =>
                        document
                          .getElementById(`edit-${eid}`)
                          ?.scrollIntoView({ behavior: "smooth", block: "center" }),
                      );
                    }}
                  />
                )}
              </div>
            )}
            {/* Ask AI: mounted on first use and kept alive (hidden, not
                unmounted) so the chat survives switching tabs. */}
            {askMounted && (
              <div
                className={cn(
                  "absolute inset-0 flex flex-col",
                  panel === "ask" ? "" : "hidden",
                )}
              >
                <AskAIPanel contractId={id} contractTitle={contract.title} />
              </div>
            )}
          </div>
        </section>
      </div>

      <RunPlaybookModal
        open={runPbOpen}
        contractId={id}
        onClose={() => setRunPbOpen(false)}
        onDone={() => {
          setRunPbOpen(false);
          qc.invalidateQueries({ queryKey: ["contract", id, "edits"] });
          qc.invalidateQueries({ queryKey: ["contract", id] });
          setPanel("redlines");
        }}
      />
      <SignatureModal
        open={sigOpen}
        contractId={id}
        onClose={() => setSigOpen(false)}
        onDone={() => {
          setSigOpen(false);
          qc.invalidateQueries({ queryKey: ["contract", id] });
        }}
      />
    </div>
  );
}

// ---- Overview band (sits above the document) -----------------------------
function OverviewBar({ contractId }: { contractId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data: contract } = useQuery({
    queryKey: ["contract", contractId],
    queryFn: () => contractsApi.get(contractId),
  });
  const { data: options } = useQuery({
    queryKey: ["contract", contractId, "lifecycle"],
    queryFn: () => contractsApi.lifecycleOptions(contractId),
  });
  const [transitionOpen, setTransitionOpen] = useState(false);
  const [showDetails, setShowDetails] = useState(false);

  if (!contract) return null;

  const currentIdx = LIFECYCLE_STAGES.indexOf(contract.lifecycle_stage);
  const facts: [string, string][] = [
    ["Counterparty", contract.counterparty_name ?? "—"],
    ["Type", contract.contract_type ? titleCase(contract.contract_type) : "—"],
    ["Value", fmtMoney(contract.value_amount, contract.currency)],
    ["Jurisdiction", contract.jurisdiction ?? "—"],
    ["Effective", fmtDate(contract.effective_date)],
    ["Expires", fmtDate(contract.expiration_date)],
  ];

  return (
    <div className="shrink-0 border-b border-slate-200 bg-slate-50 px-4 py-2.5">
      {/* Lifecycle stepper + collapsible details + advance. */}
      <div className="flex items-center gap-3">
        <ol className="flex flex-1 items-center gap-1.5 overflow-x-auto pb-0.5">
          {LIFECYCLE_STAGES.map((s, i) => {
            const done = i < currentIdx;
            const current = i === currentIdx;
            return (
              <li key={s} className="flex shrink-0 items-center gap-1.5">
                {i > 0 && (
                  <ChevronRight
                    className={cn(
                      "h-3 w-3 shrink-0",
                      done || current ? "text-brand-300" : "text-slate-300",
                    )}
                  />
                )}
                <span
                  aria-current={current ? "step" : undefined}
                  className={cn(
                    "whitespace-nowrap text-xs transition-colors",
                    current
                      ? "rounded-full bg-brand-600 px-2.5 py-1 font-semibold text-white"
                      : done
                        ? "font-medium text-brand-600"
                        : "text-slate-400",
                  )}
                >
                  {titleCase(s)}
                </span>
              </li>
            );
          })}
        </ol>
        <Button
          size="sm"
          variant="outline"
          className="shrink-0"
          onClick={() => setShowDetails((s) => !s)}
        >
          <Info className="h-4 w-4" />
          Details
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="shrink-0"
          disabled={!options?.allowed_transitions.length}
          onClick={() => setTransitionOpen(true)}
        >
          Advance stage
        </Button>
      </div>

      {/* Key metadata — tucked away by default to keep the document in focus. */}
      {showDetails && (
        <dl className="mt-3 flex flex-wrap items-baseline gap-y-1.5 text-xs">
          {facts.map(([k, v], i) => (
            <div
              key={k}
              className={cn(
                "flex items-baseline gap-1.5",
                i > 0 && "ml-4 border-l border-slate-200 pl-4",
              )}
            >
              <dt className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                {k}
              </dt>
              <dd className="font-medium text-slate-800">{v}</dd>
            </div>
          ))}
        </dl>
      )}

      {transitionOpen && options && (
        <TransitionModal
          contractId={contractId}
          allowed={options.allowed_transitions}
          onClose={() => setTransitionOpen(false)}
          onDone={() => {
            qc.invalidateQueries({ queryKey: ["contract", contractId] });
            notify("Lifecycle updated", "success");
            setTransitionOpen(false);
          }}
        />
      )}
    </div>
  );
}

function TransitionModal({
  contractId,
  allowed,
  onClose,
  onDone,
}: {
  contractId: string;
  allowed: string[];
  onClose: () => void;
  onDone: () => void;
}) {
  const { notify } = useToast();
  const [stage, setStage] = useState(allowed[0]);
  const [reason, setReason] = useState("");
  const [signedConfirmation, setSignedConfirmation] = useState(false);
  const [busy, setBusy] = useState(false);

  async function go() {
    setBusy(true);
    try {
      await contractsApi.transition(contractId, stage as never, {
        reason: reason || undefined,
        signed_confirmation: signedConfirmation,
      });
      onDone();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Transition failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Advance lifecycle stage"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={go} loading={busy}>
            Transition
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Target stage">
          <Select value={stage} onChange={(e) => setStage(e.target.value)}>
            {allowed.map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Reason" hint="Optional">
          <Textarea
            rows={2}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>
        {stage === "active" && (
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={signedConfirmation}
              onChange={(e) => setSignedConfirmation(e.target.checked)}
            />
            Confirm a signed copy exists (required to activate without a signed
            version)
          </label>
        )}
      </div>
    </Modal>
  );
}

// ---- Versions ------------------------------------------------------------
function Versions({ contractId }: { contractId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data, isLoading } = useQuery({
    queryKey: ["contract", contractId, "versions"],
    queryFn: () => contractsApi.versions(contractId),
  });

  if (isLoading) return <CenterSpinner />;

  if (!data?.length)
    return (
      <p className="py-8 text-center text-sm text-slate-400">No versions.</p>
    );

  return (
    <div className="space-y-3">
      {data
        .slice()
        .sort((a, b) => b.version_number - a.version_number)
        .map((v) => (
          <Card key={v.id}>
            <CardBody className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-slate-900">
                  Version {v.version_number}
                </span>
                <Badge tone="slate">{titleCase(v.source)}</Badge>
              </div>
              {v.change_summary && (
                <p className="text-sm text-slate-600">{v.change_summary}</p>
              )}
              <div className="flex items-center justify-between gap-2 pt-1">
                {v.is_authoritative ? (
                  <Badge tone="green">Authoritative</Badge>
                ) : (
                  <span className="text-xs text-slate-400">
                    Not authoritative
                  </span>
                )}
                <div className="flex gap-1.5">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={async () => {
                      try {
                        await contractsApi.downloadVersion(contractId, v.id);
                      } catch (e) {
                        notify(
                          e instanceof Error ? e.message : "Download failed",
                          "error",
                        );
                      }
                    }}
                  >
                    <Download className="h-3.5 w-3.5" />
                    Download
                  </Button>
                  {!v.is_authoritative && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={async () => {
                        try {
                          await contractsApi.restoreVersion(contractId, v.id);
                          qc.invalidateQueries({
                            queryKey: ["contract", contractId],
                          });
                          notify("Version restored", "success");
                        } catch (e) {
                          notify(
                            e instanceof Error ? e.message : "Failed",
                            "error",
                          );
                        }
                      }}
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                      Restore
                    </Button>
                  )}
                </div>
              </div>
            </CardBody>
          </Card>
        ))}
    </div>
  );
}

// ---- Redlines ------------------------------------------------------------
function editAnchor(
  citation: unknown[] | null,
): { matched: boolean; risk_level?: string } | null {
  for (const c of citation ?? []) {
    if (
      c &&
      typeof c === "object" &&
      (c as { type?: string }).type === "anchor"
    )
      return c as { matched: boolean; risk_level?: string };
  }
  return null;
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

function Redlines({
  contractId,
  onGenerate,
  activeEditId,
  onSelect,
}: {
  contractId: string;
  onGenerate: () => void;
  activeEditId: string | null;
  onSelect: (id: string) => void;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data, isLoading } = useQuery({
    queryKey: ["contract", contractId, "edits"],
    queryFn: () => contractsApi.edits(contractId),
  });

  async function decide(editId: string, accept: boolean) {
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

  if (isLoading) return <CenterSpinner />;
  if (!data?.length)
    return (
      <div className="flex flex-col items-center gap-3 py-12 text-center">
        <p className="text-sm text-slate-500">
          No proposed changes. If you just ran a playbook, an empty list means
          the contract matched its positions (no deviations). You can also ask
          the AI assistant to edit this contract.
        </p>
        <Button onClick={onGenerate}>
          <Wand2 className="h-4 w-4" />
          Generate redline
        </Button>
      </div>
    );

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-400">
          Click a change to jump to it in the document.
        </p>
        <Button size="sm" variant="outline" onClick={onGenerate}>
          <Wand2 className="h-4 w-4" />
          Generate more
        </Button>
      </div>
      {data.map((e) => {
        const anchor = editAnchor(e.citation);
        const quotes = editQuotes(e.citation);
        const unlocated =
          e.status === "proposed" && anchor && !anchor.matched;
        const active = activeEditId === e.id;
        return (
          <Card
            key={e.id}
            id={`redcard-${e.id}`}
            onClick={() => onSelect(e.id)}
            className={
              "cursor-pointer transition-shadow" +
              (active ? " ring-2 ring-brand-400" : " hover:shadow-sm")
            }
          >
            <CardBody className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <Badge tone="slate">{titleCase(e.edit_type)}</Badge>
                <div className="flex items-center gap-1.5">
                  {anchor?.risk_level && e.status === "proposed" && (
                    <Badge tone={riskTone(anchor.risk_level)}>
                      {titleCase(anchor.risk_level)}
                    </Badge>
                  )}
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
              </div>
              {e.original_text && (
                <div className="max-h-32 overflow-y-auto rounded-lg bg-red-50 p-3 text-sm text-red-800 line-through">
                  {e.original_text}
                </div>
              )}
              {e.replacement_text && (
                <div className="max-h-32 overflow-y-auto rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800">
                  {e.replacement_text}
                </div>
              )}
              {e.rationale && (
                <p className="text-sm text-slate-500">{e.rationale}</p>
              )}
              {quotes.length > 0 && (
                <div className="space-y-1.5 border-t border-slate-100 pt-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                    Citations
                  </p>
                  {quotes.map((q, i) => (
                    <blockquote
                      key={i}
                      className="border-l-2 border-brand-400 pl-3 text-xs italic text-slate-600"
                    >
                      {q}
                    </blockquote>
                  ))}
                </div>
              )}
              {unlocated && (
                <p className="text-xs text-amber-600">
                  Could not locate this text in the current document — review
                  manually.
                </p>
              )}
              {e.status === "proposed" && (
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    onClick={(ev) => {
                      ev.stopPropagation();
                      decide(e.id, true);
                    }}
                  >
                    <Check className="h-3.5 w-3.5" />
                    Accept
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={(ev) => {
                      ev.stopPropagation();
                      decide(e.id, false);
                    }}
                  >
                    <X className="h-3.5 w-3.5" />
                    Reject
                  </Button>
                </div>
              )}
            </CardBody>
          </Card>
        );
      })}
    </div>
  );
}

// ---- Activity ------------------------------------------------------------
function ActivityTab({ contractId }: { contractId: string }) {
  const { data: activity, isLoading } = useQuery({
    queryKey: ["contract", contractId, "activity"],
    queryFn: () => contractsApi.activity(contractId),
  });
  const { data: history } = useQuery({
    queryKey: ["contract", contractId, "stage-history"],
    queryFn: () => contractsApi.stageHistory(contractId),
  });

  if (isLoading) return <CenterSpinner />;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Timeline</CardTitle>
        </CardHeader>
        <CardBody>
          <ol className="space-y-4">
            {(activity ?? []).map((a) => (
              <li key={a.id} className="flex gap-3">
                <div className="mt-1 h-2 w-2 shrink-0 rounded-full bg-brand-500" />
                <div>
                  <p className="text-sm text-slate-800">{a.title}</p>
                  <p className="text-xs text-slate-400">
                    {titleCase(a.event_type)} · {fmtDateTime(a.created_at)}
                  </p>
                </div>
              </li>
            ))}
            {activity?.length === 0 && (
              <p className="text-sm text-slate-400">No activity.</p>
            )}
          </ol>
        </CardBody>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Stage history</CardTitle>
        </CardHeader>
        <CardBody>
          <ol className="space-y-3">
            {(history ?? []).map((h) => (
              <li key={h.id} className="flex items-center gap-2 text-sm">
                <span className="text-slate-500">
                  {h.from_stage ? titleCase(h.from_stage) : "—"}
                </span>
                <ChevronRight className="h-3.5 w-3.5 text-slate-300" />
                <span className="font-medium text-slate-800">
                  {titleCase(h.to_stage)}
                </span>
                <span className="ml-auto text-xs text-slate-400">
                  {fmtDateTime(h.changed_at)}
                </span>
              </li>
            ))}
            {history?.length === 0 && (
              <p className="text-sm text-slate-400">No stage changes.</p>
            )}
          </ol>
        </CardBody>
      </Card>
    </div>
  );
}

// ---- Brain ---------------------------------------------------------------
function BrainTab({ contractId }: { contractId: string }) {
  const { notify } = useToast();
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<{
    text: string;
    citations: Citation[];
  } | null>(null);

  async function ask() {
    if (question.trim().length < 3) return;
    setBusy(true);
    setAnswer(null);
    try {
      const res = await brainApi.ask({
        question,
        query_scope: "contract",
        contract_id: contractId,
      });
      setAnswer({ text: res.answer, citations: res.citations });
    } catch (e) {
      notify(e instanceof Error ? e.message : "Query failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <Input
          placeholder="e.g. What is the termination notice period?"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && ask()}
        />
        <Button onClick={ask} loading={busy}>
          <Send className="h-4 w-4" />
          Ask
        </Button>
      </div>
      {busy && (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Spinner className="h-3.5 w-3.5" />
          Thinking…
        </div>
      )}
      {answer && (
        <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
          <p className="whitespace-pre-wrap text-sm text-slate-800">
            {answer.text}
          </p>
          {answer.citations.length > 0 && (
            <div className="space-y-2 border-t border-slate-200 pt-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                Citations
              </p>
              {answer.citations.map((c, i) => (
                <blockquote
                  key={i}
                  className="border-l-2 border-brand-400 pl-3 text-sm text-slate-600"
                >
                  {c.quote ?? c.excerpt}
                </blockquote>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---- Ask AI (docked, contract-scoped FULL assistant) ---------------------
// Drives a real contract-scoped assistant session (tools: edit_contract,
// redline, summarize, …) — not the read-only Brain Q&A — so it can edit the
// open document. Tracked changes land in the Redlines panel.
type AskItem = {
  id: string;
  role: "user" | "assistant" | "tool" | "system";
  text: string;
  tool?: {
    name: string;
    status: "running" | "done" | "error";
    toolUseId?: string;
  };
  citations?: Citation[];
  // A tracked change the user can accept/reject inline (no Redlines panel trip).
  edit?: { id: string; status: "pending" | "accepted" | "rejected" };
};
type AskPending = {
  confirmationId: string;
  assistantRunId: string;
  toolName: string;
};

const ASK_SUGGESTIONS = [
  "Remove the drafting assumptions from the document",
  "Tighten the confidentiality clause and show tracked changes",
  "Summarize the key risks in this contract",
];

function AskAIPanel({
  contractId,
  contractTitle,
}: {
  contractId: string;
  contractTitle: string;
}) {
  const { notify } = useToast();
  const qc = useQueryClient();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [items, setItems] = useState<AskItem[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [pending, setPending] = useState<AskPending | null>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const bootRef = useRef(false);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (bootRef.current) return;
    bootRef.current = true;
    assistantApi
      .createSession({
        session_type: "contract",
        contract_id: contractId,
        title: `Edit · ${contractTitle}`.slice(0, 60),
      })
      .then((s) => setSessionId(s.id))
      .catch((e) =>
        notify(
          e instanceof Error ? e.message : "Could not start assistant",
          "error",
        ),
      );
    return () => abortRef.current?.();
  }, [contractId, contractTitle, notify]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [items, pending]);

  function handleEvent(event: string, data: Record<string, unknown>) {
    if (event === "message_delta") {
      const text = String(data.text ?? "");
      setItems((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.role === "assistant" && !last.tool)
          return [...prev.slice(0, -1), { ...last, text: last.text + text }];
        return [
          ...prev,
          { id: crypto.randomUUID(), role: "assistant", text },
        ];
      });
    } else if (event === "tool_started") {
      const name = String(data.tool_name ?? "tool");
      const toolUseId =
        typeof data.tool_use_id === "string" ? data.tool_use_id : undefined;
      setItems((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "tool",
          text: name,
          tool: { name, status: "running", toolUseId },
        },
      ]);
    } else if (event === "tool_finished") {
      const toolUseId =
        typeof data.tool_use_id === "string" ? data.tool_use_id : undefined;
      setItems((prev) =>
        prev.map((it) =>
          it.tool &&
          it.tool.status === "running" &&
          (!toolUseId || it.tool.toolUseId === toolUseId)
            ? {
                ...it,
                tool: { ...it.tool, status: data.error ? "error" : "done" },
              }
            : it,
        ),
      );
    } else if (event === "citation") {
      setItems((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.role === "assistant")
          return [
            ...prev.slice(0, -1),
            {
              ...last,
              citations: [...(last.citations ?? []), data as Citation],
            },
          ];
        return prev;
      });
    } else if (event === "tracked_change_created") {
      qc.invalidateQueries({ queryKey: ["contract", contractId, "edits"] });
      qc.invalidateQueries({ queryKey: ["contract", contractId] });
      const editId =
        typeof data.contract_edit_id === "string"
          ? data.contract_edit_id
          : undefined;
      setItems((prev) => [
        ...prev,
        editId
          ? {
              id: crypto.randomUUID(),
              role: "system",
              text: "Tracked change ready — accept to apply it to the document.",
              edit: { id: editId, status: "pending" },
            }
          : {
              id: crypto.randomUUID(),
              role: "system",
              text: "Tracked change created — see the Redlines panel.",
            },
      ]);
    } else if (event === "contract_generated") {
      const genId =
        typeof data.contract_id === "string" ? data.contract_id : undefined;
      const isThisContract = !genId || genId === contractId;
      if (isThisContract) {
        // A full redraft of THIS contract — refresh its whole query subtree
        // (contract + versions + edits + document text) so the new version
        // becomes the visible document. invalidateQueries is a prefix match.
        qc.invalidateQueries({ queryKey: ["contract", contractId] });
      }
      setItems((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "system",
          text: isThisContract
            ? "Redraft complete — the document is now a new version. Use the Versions panel to compare or restore the previous one."
            : "A document was generated — see Versions.",
        },
      ]);
    } else if (event === "confirmation_required") {
      setPending({
        confirmationId: String(data.confirmation_id),
        assistantRunId: String(data.assistant_run_id),
        toolName: String(data.tool_name),
      });
      setStreaming(false);
    } else if (event === "error") {
      notify(String(data.message ?? "Assistant error"), "error");
      setStreaming(false);
    } else if (event === "done") {
      setStreaming(false);
    }
  }

  function handleStreamError(err: Error) {
    notify(err.message || "Assistant stream failed", "error");
    setStreaming(false);
  }

  async function send(text: string) {
    const t = text.trim();
    if (!t || streaming || pending || !sessionId) return;
    setInput("");
    setItems((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: "user", text: t },
    ]);
    setStreaming(true);
    try {
      abortRef.current = await apiStream(
        `/assistant/sessions/${sessionId}/stream`,
        { message: t, contract_ids: [contractId] },
        {
          onEvent: handleEvent,
          onError: handleStreamError,
          onClose: () => setStreaming(false),
        },
      );
    } catch (e) {
      notify(e instanceof Error ? e.message : "Send failed", "error");
      setStreaming(false);
    }
  }

  // Accept/reject a tracked change right here in the chat (same endpoints the
  // Redlines panel uses) so the user never has to leave Ask AI.
  async function resolveEdit(item: AskItem, accept: boolean) {
    if (!item.edit || item.edit.status !== "pending") return;
    try {
      if (accept) await contractsApi.acceptEdit(contractId, item.edit.id);
      else await contractsApi.rejectEdit(contractId, item.edit.id);
      // Refresh the whole contract subtree so the document reflects the result.
      qc.invalidateQueries({ queryKey: ["contract", contractId] });
      setItems((prev) =>
        prev.map((it) =>
          it.id === item.id && it.edit
            ? { ...it, edit: { ...it.edit, status: accept ? "accepted" : "rejected" } }
            : it,
        ),
      );
      notify(
        accept ? "Change accepted — applied to the document." : "Change rejected.",
        "success",
      );
    } catch (e) {
      notify(e instanceof Error ? e.message : "Action failed", "error");
    }
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
            text: `Confirmed ${titleCase(p.toolName.replace(/_/g, " "))} — applying…`,
          },
        ]);
        setStreaming(true);
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
            text: "Cancelled — no changes were made.",
          },
        ]);
      }
    } catch (e) {
      notify(e instanceof Error ? e.message : "Action failed", "error");
    }
  }

  const busy = streaming || !!pending;

  return (
    <div className="flex h-full w-full flex-col bg-slate-100">
      <div className="flex h-12 shrink-0 items-center gap-2.5 border-b border-slate-100 px-4">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
          <Sparkles className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-serif text-sm font-medium leading-tight text-slate-900">
            Ask AEGIS
          </p>
          <p className="truncate text-xs text-slate-400">
            Editing · {contractTitle}
          </p>
        </div>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {items.length === 0 && !sessionId && (
          <div className="flex items-center gap-2 text-sm text-slate-400">
            <Spinner className="h-3.5 w-3.5" />
            Connecting the assistant…
          </div>
        )}
        {items.length === 0 && sessionId && (
          <div className="space-y-3">
            <p className="text-sm text-slate-500">
              The full assistant — ask it to analyze <em>or edit</em> this
              contract. Edits return as tracked changes you approve.
            </p>
            <div className="space-y-1.5">
              {ASK_SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="flex w-full items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-left text-sm text-slate-700 transition-colors hover:border-brand-200 hover:bg-brand-50"
                >
                  <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {items.map((it) => {
          if (it.role === "user")
            return (
              <div key={it.id} className="flex justify-end">
                <div className="max-w-[85%] rounded-2xl rounded-br-md bg-brand-600 px-3.5 py-2 text-sm leading-relaxed text-white">
                  {it.text}
                </div>
              </div>
            );
          if (it.role === "tool")
            return (
              <div
                key={it.id}
                className="flex items-center gap-2 text-xs text-slate-500"
              >
                {it.tool?.status === "running" ? (
                  <Spinner className="h-3 w-3" />
                ) : it.tool?.status === "error" ? (
                  <X className="h-3.5 w-3.5 text-red-500" />
                ) : (
                  <Check className="h-3.5 w-3.5 text-brand-600" />
                )}
                {titleCase(it.text.replace(/_/g, " "))}
              </div>
            );
          if (it.edit)
            return (
              <div
                key={it.id}
                className="space-y-2 rounded-xl border border-brand-200 bg-brand-50/50 px-3 py-2.5"
              >
                <p className="flex items-center gap-1.5 text-xs font-medium text-slate-700">
                  <Wand2 className="h-3.5 w-3.5 text-brand-600" />
                  {it.text}
                </p>
                {it.edit.status === "pending" ? (
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => resolveEdit(it, true)}>
                      <Check className="h-3.5 w-3.5" />
                      Accept
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => resolveEdit(it, false)}
                    >
                      <X className="h-3.5 w-3.5" />
                      Reject
                    </Button>
                  </div>
                ) : (
                  <p className="text-xs font-medium text-slate-500">
                    {it.edit.status === "accepted"
                      ? "✓ Accepted — applied to the document."
                      : "Rejected — no change made."}
                  </p>
                )}
              </div>
            );
          if (it.role === "system")
            return (
              <div
                key={it.id}
                className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500"
              >
                {it.text}
              </div>
            );
          return (
            <div key={it.id} className="space-y-2">
              <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
                <Sparkles className="h-3 w-3 text-brand-600" />
                AEGIS
              </div>
              <div className="text-sm leading-relaxed text-slate-800">
                <Markdown>{it.text}</Markdown>
              </div>
              {it.citations && it.citations.length > 0 && (
                <div className="space-y-2 border-t border-slate-100 pt-2.5">
                  {it.citations.map((c, ci) => (
                    <blockquote
                      key={ci}
                      className="border-l-2 border-brand-400 pl-3 text-xs text-slate-600"
                    >
                      {c.quote ?? c.excerpt}
                    </blockquote>
                  ))}
                </div>
              )}
            </div>
          );
        })}

        {pending && (
          <div className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-3">
            <p className="text-sm font-medium text-amber-900">
              Approve {titleCase(pending.toolName.replace(/_/g, " "))}?
            </p>
            <p className="text-xs text-amber-700">
              The assistant needs your confirmation before changing the
              contract.
            </p>
            <div className="flex gap-2">
              <Button size="sm" onClick={() => resolveConfirmation(true)}>
                <Check className="h-3.5 w-3.5" />
                Approve
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => resolveConfirmation(false)}
              >
                Cancel
              </Button>
            </div>
          </div>
        )}

        {streaming && !pending && (
          <div className="flex items-center gap-2 text-sm text-slate-400">
            <Spinner className="h-3.5 w-3.5" />
            Working…
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="shrink-0 border-t border-slate-100 p-3">
        <div className="rounded-xl border border-slate-200 bg-slate-100 p-2 transition focus-within:border-slate-300">
          <Textarea
            rows={1}
            placeholder="Ask or instruct an edit…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) =>
              e.key === "Enter" &&
              !e.shiftKey &&
              (e.preventDefault(), send(input))
            }
            className="max-h-32 resize-none border-0 px-2 py-1.5 text-sm focus:ring-0"
          />
          <div className="flex items-center justify-between px-1 pt-1">
            <span className="text-[11px] text-slate-400">
              ⏎ send · edits this contract
            </span>
            <Button
              size="icon"
              onClick={() => send(input)}
              disabled={!input.trim() || busy || !sessionId}
              className="h-8 w-8 rounded-full"
            >
              <Send className="h-4 w-4" />
            </Button>
          </div>
        </div>
        <p className="mt-2 text-center text-[11px] text-slate-400">
          AI can make mistakes. Not legal advice.
        </p>
      </div>
    </div>
  );
}

// ---- Run Playbook modal --------------------------------------------------
function RunPlaybookModal({
  open,
  contractId,
  onClose,
  onDone,
}: {
  open: boolean;
  contractId: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const { notify } = useToast();
  const { data: playbooks } = useQuery({
    queryKey: ["playbooks"],
    queryFn: playbooksApi.list,
    enabled: open,
  });
  const published = (playbooks ?? []).filter((p) => p.status === "published");
  const [pbId, setPbId] = useState("");
  const [createRedline, setCreateRedline] = useState(true);
  const [busy, setBusy] = useState(false);

  async function run() {
    if (!pbId) return;
    setBusy(true);
    try {
      const runRes = await playbooksApi.createRun(pbId, {
        contract_id: contractId,
        create_redline: createRedline,
        use_ai: true,
      });
      let n = 0;
      try {
        const detail = await playbooksApi.runDetail(runRes.id);
        n = detail.deviations?.length ?? 0;
      } catch {
        /* fall back to a generic message below */
      }
      if (runRes.status === "failed" || runRes.error_message) {
        notify(
          `Playbook run failed: ${runRes.error_message ?? "unknown error"}`,
          "error",
        );
      } else if (n > 0) {
        notify(
          `Playbook complete — ${n} deviation${n === 1 ? "" : "s"} found${
            createRedline ? "; tracked-change redline created" : ""
          }. See the Redlines panel.`,
          "success",
        );
      } else {
        notify(
          "Playbook complete — no deviations. This contract matches the playbook’s positions.",
          "success",
        );
      }
      onDone();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Run failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Run a playbook against this contract"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={run} loading={busy} disabled={!pbId}>
            Run playbook
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field
          label="Published playbook"
          hint={
            published.length === 0
              ? "No published playbooks — publish one in Playbooks first"
              : "Only published playbooks can produce official redlines"
          }
        >
          <Select value={pbId} onChange={(e) => setPbId(e.target.value)}>
            <option value="">Select a playbook…</option>
            {published.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
        </Field>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={createRedline}
            onChange={(e) => setCreateRedline(e.target.checked)}
          />
          Create tracked-change redline (recommended)
        </label>
      </div>
    </Modal>
  );
}

// ---- Send for signature modal --------------------------------------------
function SignatureModal({
  open,
  contractId,
  onClose,
  onDone,
}: {
  open: boolean;
  contractId: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [selected, setSelected] = useState<string[]>([]); // emails, in signing order
  const [override, setOverride] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pName, setPName] = useState("");
  const [pEmail, setPEmail] = useState("");
  const [addingParty, setAddingParty] = useState(false);

  const { data: signers } = useQuery({
    queryKey: ["contract-signers", contractId],
    queryFn: () => contractsApi.signers(contractId),
    enabled: open,
  });

  useEffect(() => {
    if (!open) {
      setSelected([]);
      setOverride(false);
      setPName("");
      setPEmail("");
    }
  }, [open]);

  function toggle(email: string) {
    setSelected((s) => (s.includes(email) ? s.filter((e) => e !== email) : [...s, email]));
  }

  async function addSigner() {
    if (!pName.trim() || !pEmail.trim()) return;
    setAddingParty(true);
    try {
      await contractsApi.addParty(contractId, {
        name: pName.trim(),
        contact_email: pEmail.trim(),
      });
      const email = pEmail.trim();
      setPName("");
      setPEmail("");
      await qc.invalidateQueries({ queryKey: ["contract-signers", contractId] });
      setSelected((s) => (s.includes(email) ? s : [...s, email]));
    } catch (e) {
      notify(e instanceof Error ? e.message : "Couldn't add signer", "error");
    } finally {
      setAddingParty(false);
    }
  }

  async function send() {
    if (selected.length === 0) return;
    const opts = signers ?? [];
    const recipients = selected.map((email) => {
      const o = opts.find((x) => x.email.toLowerCase() === email.toLowerCase());
      return { name: o?.name ?? email, email, role: "signer" };
    });
    setBusy(true);
    try {
      await signaturesApi.send({
        contract_id: contractId,
        recipients,
        override_lifecycle: override,
      });
      notify("Sent for signature", "success");
      onDone();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Send failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Send for e-signature"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={send} loading={busy} disabled={selected.length === 0}>
            Send envelope
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <div className="mb-1 text-sm font-medium text-slate-700">Signers</div>
          <p className="mb-2 text-xs text-slate-500">
            Pick who signs (numbers show the signing order). Not listed? Add them below.
          </p>
          {(signers ?? []).length === 0 ? (
            <p className="text-sm text-slate-500">No signers yet — add one below.</p>
          ) : (
            <div className="space-y-0.5 rounded-md border border-slate-200 p-1">
              {(signers ?? []).map((o) => {
                const idx = selected.indexOf(o.email);
                return (
                  <label
                    key={o.email}
                    className="flex items-center gap-2 rounded px-2 py-1.5 hover:bg-slate-50"
                  >
                    <input
                      type="checkbox"
                      checked={idx >= 0}
                      onChange={() => toggle(o.email)}
                      className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                    />
                    <span className="text-sm text-slate-800">{o.name}</span>
                    <span className="text-xs text-slate-400">{o.email}</span>
                    <span className="ml-1 rounded-full bg-slate-100 px-1.5 text-[10px] uppercase text-slate-500">
                      {o.kind}
                    </span>
                    {idx >= 0 && (
                      <span className="ml-auto text-[11px] font-semibold text-brand-600">
                        #{idx + 1}
                      </span>
                    )}
                  </label>
                );
              })}
            </div>
          )}
        </div>

        <div className="rounded-md border border-dashed border-slate-300 p-3">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Add a signer
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <Input value={pName} onChange={(e) => setPName(e.target.value)} placeholder="Name" />
            <Input
              value={pEmail}
              onChange={(e) => setPEmail(e.target.value)}
              placeholder="email@company.com"
            />
          </div>
          <div className="mt-2 flex justify-end">
            <Button
              size="sm"
              variant="outline"
              loading={addingParty}
              disabled={!pName.trim() || !pEmail.trim()}
              onClick={addSigner}
            >
              Add to contract
            </Button>
          </div>
        </div>

        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={override}
            onChange={(e) => setOverride(e.target.checked)}
          />
          Override lifecycle (send even if not yet approved)
        </label>
      </div>
    </Modal>
  );
}
