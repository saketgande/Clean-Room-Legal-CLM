"use client";

import { use, useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Download,
  RotateCcw,
  Check,
  PenLine,
  Lock,
  ChevronUp,
  ChevronDown,
  X,
  Send,
  ChevronRight,
  BookMarked,
  Sparkles,
  ListChecks,
  RefreshCw,
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
  Users,
  UserPlus,
} from "lucide-react";
import {
  aiApi,
  approvalsApi,
  assistantApi,
  brainApi,
  contractsApi,
  grantsApi,
  obligationsApi,
  playbooksApi,
  renewalsApi,
  rolesApi,
  signaturesApi,
  usersApi,
} from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  CenterSpinner,
  EmptyState,
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
  statusTone,
  titleCase,
} from "@/lib/utils";
import { useToast } from "@/components/toast";
import { useAuth } from "@/lib/auth";
import { useLayout } from "@/lib/layout";
import { ContractDocument } from "@/components/contract-document";
import { Markdown } from "@/components/markdown";
import { apiStream } from "@/lib/api";
import type {
  ApprovalChainStep,
  Citation,
  ContractComment,
  ContractEditResponse,
  ContractResponse,
  ContractLifecycleStage,
  ContractShareResponse,
  ContractVersionResponse,
  ReviewChecklistItem,
  ReviewStatusResponse,
} from "@/lib/types";

type PanelId =
  | "ask"
  | "risk"
  | "redlines"
  | "comments"
  | "negotiation"
  | "versions"
  | "obligations"
  | "renewals";

// The workspace has two modes. Pre-signature it's a NEGOTIATION surface
// (redline / comment / negotiate); once signed it's a MANAGEMENT surface
// (monitor obligations & renewals). Deriving the phase from the stage lets the
// tab set, the at-a-glance strip, and the header all adapt to where the
// contract actually is, instead of showing the same heavy chrome everywhere.
type WorkspacePhase = "negotiate" | "manage";
function phaseForStage(stage: ContractLifecycleStage): WorkspacePhase {
  return stage === "active" || stage === "closed" ? "manage" : "negotiate";
}
const NEGOTIATE_PANELS: PanelId[] = [
  "ask",
  "redlines",
  "risk",
  "comments",
  "negotiation",
  "versions",
];
const MANAGE_PANELS: PanelId[] = [
  "ask",
  "risk",
  "comments",
  "versions",
  "obligations",
  "renewals",
];

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

const PANELS: Record<
  PanelId,
  { id: PanelId; label: string; hint: string; icon: typeof Wand2 }
> = {
  ask: { id: "ask", label: "Ask", hint: "Edit & analyze", icon: Sparkles },
  risk: { id: "risk", label: "Risk", hint: "Weighted risk score", icon: AlertTriangle },
  redlines: { id: "redlines", label: "Redlines", hint: "Tracked changes", icon: FileDiff },
  comments: { id: "comments", label: "Comments", hint: "Discussion", icon: MessageSquare },
  negotiation: { id: "negotiation", label: "Negotiation", hint: "Counterparty rounds", icon: Handshake },
  versions: { id: "versions", label: "Versions", hint: "Document history", icon: History },
  obligations: { id: "obligations", label: "Obligations", hint: "Commitments to monitor", icon: ListChecks },
  renewals: { id: "renewals", label: "Renewals", hint: "Renewal & expiry", icon: RefreshCw },
};

const CTA_LABEL: Record<string, string> = {
  run_ai: "Run AI review",
  resolve_issues: "Review issues",
  resolve_redlines: "Review redlines",
  resolve_comments: "Review comments",
  submit_approval: "Submit for approval",
  move_to_drafting: "Move to drafting",
  move_to_review: "Send for review",
  send_signature: "Send for signature",
  view_approvals: "View approvals",
  view_obligations: "View obligations",
};

// Actions the "Next step" hero can ask the page to perform. The early-stage set
// comes from the review-status engine; the later-stage ones are stage-driven.
type HeroAction =
  | "run_ai"
  | "move_to_drafting"
  | "move_to_review"
  | "counterparty_round"
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
      const created = await contractsApi.addComment(contractId, {
        body: body.trim(),
        visibility,
      });
      setBody("");
      // Show the new comment immediately (list is oldest-first); the refetch
      // below reconciles ordering/fields from the server.
      qc.setQueryData<ContractComment[]>(
        ["contract-comments", contractId],
        (old) => [...(old ?? []), created],
      );
      refresh();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to post", "error");
    } finally {
      setBusy(false);
    }
  }

  // Optimistic action: apply the expected list change instantly, roll back on
  // failure, and reconcile with the server afterwards.
  async function act(
    fn: () => Promise<unknown>,
    apply?: (list: ContractComment[]) => ContractComment[],
  ) {
    const key = ["contract-comments", contractId] as const;
    const prev = qc.getQueryData<ContractComment[]>(key);
    if (apply && prev) qc.setQueryData<ContractComment[]>(key, apply(prev));
    try {
      await fn();
      refresh();
    } catch (e) {
      if (prev) qc.setQueryData(key, prev);
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
              id={`comment-${c.id}`}
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
              {(c.anchor as { quote?: string } | null)?.quote && (
                <button
                  onClick={() =>
                    document
                      .getElementById(`anchor-comment-${c.id}`)
                      ?.scrollIntoView({ behavior: "smooth", block: "center" })
                  }
                  title="Jump to this passage in the document"
                  className="mb-1.5 block w-full truncate border-l-2 border-sky-300 pl-2 text-left text-xs italic text-slate-500 hover:text-sky-700"
                >
                  “{(c.anchor as { quote?: string }).quote}”
                </button>
              )}
              <p className="whitespace-pre-wrap text-sm text-slate-700">{c.body}</p>
              <div className="mt-2 flex items-center gap-3 text-xs">
                <button
                  onClick={() =>
                    act(
                      () => contractsApi.resolveComment(contractId, c.id, !c.resolved),
                      (list) =>
                        list.map((x) =>
                          x.id === c.id ? { ...x, resolved: !c.resolved } : x,
                        ),
                    )
                  }
                  className="font-medium text-slate-500 hover:text-slate-900"
                >
                  {c.resolved ? "Reopen" : "Resolve"}
                </button>
                <button
                  onClick={() =>
                    act(
                      () => contractsApi.deleteComment(contractId, c.id),
                      (list) => list.filter((x) => x.id !== c.id),
                    )
                  }
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
          <div className="flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 p-2 focus-within:border-brand-400 focus-within:ring-1 focus-within:ring-brand-400">
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
  move_to_drafting: "stage",
  move_to_review: "stage",
};

function ChainPill({ step }: { step: ApprovalChainStep }) {
  const overdueDays =
    step.overdue && step.due_at
      ? Math.max(1, Math.floor((Date.now() - new Date(step.due_at).getTime()) / 86_400_000))
      : 0;
  if (step.status === "approved")
    return (
      <span
        title={step.decided_by ? `Approved by ${step.decided_by}` : "Approved"}
        className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-semibold text-emerald-700"
      >
        <CheckCircle2 className="h-3.5 w-3.5" />
        {step.approver_label}
      </span>
    );
  if (step.status === "rejected")
    return (
      <span
        title={step.decided_by ? `Rejected by ${step.decided_by}` : "Rejected"}
        className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2.5 py-1 text-xs font-semibold text-rose-700"
      >
        <X className="h-3.5 w-3.5" />
        {step.approver_label}
      </span>
    );
  if (step.status === "pending")
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold",
          step.overdue ? "bg-rose-600 text-white" : "bg-brand-600 text-white",
        )}
      >
        <Circle className="h-2 w-2 fill-current" />
        {step.approver_label}
        {step.overdue && ` · ${overdueDays}d overdue`}
      </span>
    );
  return (
    <span className="inline-flex items-center rounded-full border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-400">
      {step.approver_label}
    </span>
  );
}

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
  const { data: chain } = useQuery({
    queryKey: ["approval-chain", contract.id],
    queryFn: () => approvalsApi.chain(contract.id),
    enabled: contract.lifecycle_stage === "approval",
    refetchInterval: 60_000,
  });
  // Mission control can collapse to a slim strip — the header must never
  // crowd out the document (the actual work surface). Remembered per user.
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("aegis-mission-collapsed") === "1";
  });
  const toggleCollapsed = () => {
    setCollapsed((c) => {
      try {
        window.localStorage.setItem("aegis-mission-collapsed", c ? "0" : "1");
      } catch {
        /* ignore */
      }
      return !c;
    });
  };

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
        {contract.lifecycle_stage === "approval" &&
          (chain?.steps?.length ?? 0) > 0 && (
            <div className="mt-2.5 flex flex-wrap items-center gap-2 pl-11">
              {(chain?.steps ?? []).map((step, i) => (
                <span key={step.approval_request_id} className="flex items-center gap-2">
                  {i > 0 && <span className="text-slate-300">→</span>}
                  <ChainPill step={step} />
                </span>
              ))}
            </div>
          )}
      </div>
    );
  }

  // Early stages (intake/drafting/review): MISSION CONTROL — the guided
  // checklist as real, clickable steps with one reconciled set of numbers.
  const review: ReviewStatusResponse | undefined = data;
  if (!review) return null;
  const doneCount = review.checklist.filter((c) => c.status === "done").length;

  const stepMeta = (
    c: ReviewChecklistItem,
  ): { style: string; sub: string; action: HeroAction | null } => {
    switch (c.key) {
      case "ai_review":
        return c.status === "done"
          ? { style: "done", sub: c.detail ?? "Completed", action: null }
          : { style: "todo", sub: c.detail ?? "One-click risk & clause analysis", action: "run_ai" };
      case "issues":
        // Advisory now — reviewing is encouraged but doesn't block approval.
        return c.count > 0
          ? {
              style: "amber",
              sub: `${c.count} open${c.detail ? ` · ${c.detail}` : ""}`,
              action: "resolve_issues",
            }
          : { style: "done", sub: "None open", action: null };
      case "redlines":
        return c.count > 0
          ? { style: "amber", sub: `${c.count} proposed — blocks approval`, action: "resolve_redlines" }
          : { style: "done", sub: "All decided", action: null };
      case "comments":
        return c.count > 0
          ? { style: "todo", sub: `${c.count} open`, action: "resolve_comments" }
          : { style: "done", sub: "All resolved", action: null };
      case "counterparty":
        return c.status === "in_progress"
          ? { style: "amber", sub: c.detail ?? "Awaiting response", action: "counterparty_round" }
          : { style: "opt", sub: c.detail ?? "Optional", action: "counterparty_round" };
      case "approval":
        return c.status === "done"
          ? { style: "done", sub: "Submitted", action: null }
          : c.status === "blocked"
            ? { style: "lock", sub: c.detail ?? "Clear blockers first", action: null }
            : { style: "ready", sub: c.detail ?? "Ready", action: "submit_approval" };
      default:
        return { style: "todo", sub: c.detail ?? "", action: null };
    }
  };
  const stepIcon = (style: string) =>
    style === "done" ? (
      <CheckCircle2 className="h-3.5 w-3.5" />
    ) : style === "red" ? (
      <AlertTriangle className="h-3.5 w-3.5" />
    ) : style === "amber" ? (
      <FileDiff className="h-3.5 w-3.5" />
    ) : style === "lock" ? (
      <Lock className="h-3 w-3" />
    ) : style === "ready" ? (
      <ArrowRight className="h-3 w-3" />
    ) : (
      <Circle className="h-3 w-3" />
    );

  if (collapsed) {
    return (
      <div className="flex shrink-0 items-center gap-3 border-b border-slate-200 bg-brand-50/60 px-4 py-1.5">
        <Wand2 className="h-3.5 w-3.5 shrink-0 text-brand-600" />
        <p className="min-w-0 flex-1 truncate font-serif text-[13.5px] text-slate-800">
          {review.next_step}
        </p>
        {review.next_action && (
          <Button
            size="sm"
            loading={busyAction === ACTION_BUSY[review.next_action]}
            onClick={() => onAction(review.next_action as HeroAction)}
          >
            {CTA_LABEL[review.next_action] ?? "Next"}
          </Button>
        )}
        <span className="font-mono text-[10px] text-slate-400">
          {doneCount}/{review.checklist.length}
        </span>
        <button
          onClick={toggleCollapsed}
          aria-label="Expand checklist"
          className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
        >
          <ChevronDown className="h-4 w-4" />
        </button>
      </div>
    );
  }

  return (
    <div className="relative grid shrink-0 grid-cols-1 border-b border-slate-200 lg:grid-cols-[minmax(260px,0.9fr)_minmax(0,1.5fr)]">
      <button
        onClick={toggleCollapsed}
        aria-label="Collapse checklist"
        className="absolute right-1.5 top-1.5 z-10 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
      >
        <ChevronUp className="h-4 w-4" />
      </button>
      {/* Left: the single next step, spoken plainly. */}
      <div className="flex flex-col justify-center gap-1.5 border-b border-slate-200 bg-brand-50/60 px-4 py-2.5 lg:border-b-0 lg:border-r">
        <p className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.18em] text-brand-600">
          <Wand2 className="h-3.5 w-3.5" />
          Next step · {titleCase(contract.lifecycle_stage)}
        </p>
        <p className="font-serif text-[15px] leading-snug text-slate-900">
          {review.next_step}
        </p>
        <div className="flex items-center gap-3">
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
          <div className="flex min-w-0 max-w-[160px] flex-1 flex-col gap-1">
            <div className="h-1.5 overflow-hidden rounded-full bg-slate-200">
              <div
                className="h-full rounded-full bg-brand-500 transition-all"
                style={{ width: `${Math.round((doneCount / Math.max(review.checklist.length, 1)) * 100)}%` }}
              />
            </div>
            <span className="font-mono text-[10px] text-slate-400">
              {doneCount} of {review.checklist.length} complete
            </span>
          </div>
        </div>
      </div>

      {/* Right: every step, clickable, with real state + consequence. */}
      <div className="grid grid-cols-1 gap-1 bg-slate-50 p-2 pr-8 sm:grid-cols-2 xl:grid-cols-3">
        {review.checklist.map((c) => {
          const m = stepMeta(c);
          const clickable = Boolean(m.action);
          return (
            <button
              key={c.key}
              disabled={!clickable}
              onClick={() => m.action && onAction(m.action)}
              className={cn(
                "flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-colors",
                m.style === "red" &&
                  "border-rose-200 bg-rose-50/40 hover:border-rose-300 dark:border-rose-400/40 dark:bg-rose-400/10",
                m.style === "amber" &&
                  "border-amber-200 bg-amber-50/40 hover:border-amber-300 dark:border-amber-400/40 dark:bg-amber-400/10",
                m.style === "done" && "cursor-default border-slate-200 bg-slate-100/60",
                m.style === "todo" && "border-slate-200 bg-slate-100 hover:border-brand-300",
                m.style === "opt" && "border-dashed border-slate-300 bg-transparent hover:border-brand-300",
                m.style === "lock" && "cursor-default border-slate-200 bg-slate-100/40 opacity-70",
                m.style === "ready" && "border-brand-300 bg-brand-50/60 hover:border-brand-400",
              )}
            >
              <span
                className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full",
                  m.style === "done" &&
                    "bg-emerald-100 text-emerald-600 dark:bg-emerald-400/15 dark:text-emerald-400",
                  m.style === "red" &&
                    "bg-rose-100 text-rose-600 dark:bg-rose-400/15 dark:text-rose-400",
                  m.style === "amber" &&
                    "bg-amber-100 text-amber-600 dark:bg-amber-400/15 dark:text-amber-400",
                  (m.style === "todo" || m.style === "opt" || m.style === "lock") &&
                    "text-slate-400",
                  m.style === "ready" && "bg-brand-100 text-brand-600",
                )}
              >
                {stepIcon(m.style)}
              </span>
              <span className="min-w-0 flex-1">
                <span
                  className={cn(
                    "block truncate text-xs font-semibold",
                    m.style === "done" ? "text-slate-500" : "text-slate-800",
                  )}
                >
                  {c.label}
                </span>
                <span
                  className={cn(
                    "block truncate text-[10.5px]",
                    m.style === "red"
                      ? "text-rose-600/90 dark:text-rose-400/90"
                      : m.style === "amber"
                        ? "text-amber-600/90 dark:text-amber-400/90"
                        : "text-slate-400",
                  )}
                >
                  {m.sub}
                </span>
              </span>
              {clickable && (
                <ArrowRight className="h-3.5 w-3.5 shrink-0 text-slate-300" />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// A constant 3-line summary pinned above the right-rail tabs so the contract's
// health is visible whatever tab you're on. Content is phase-appropriate:
// Manage -> risk / obligations / renewal; Negotiate -> issues / redlines / comments.
function AtAGlance({
  contractId,
  phase,
  riskScore,
  riskBand,
  onOpen,
}: {
  contractId: string;
  phase: WorkspacePhase;
  riskScore: number | null;
  riskBand: string | null;
  onOpen: (panel: PanelId) => void;
}) {
  const { data: review } = useQuery({
    queryKey: ["review-status", contractId],
    queryFn: () => contractsApi.reviewStatus(contractId),
  });
  const { data: obligations } = useQuery({
    queryKey: ["contract", contractId, "obligations"],
    queryFn: () => obligationsApi.list({ contract_id: contractId }),
    enabled: phase === "manage",
  });
  const { data: renewals } = useQuery({
    queryKey: ["contract", contractId, "renewals"],
    queryFn: () => renewalsApi.list(contractId),
    enabled: phase === "manage",
  });

  const Row = ({
    tone,
    icon,
    label,
    value,
    valueTone,
    onClick,
  }: {
    tone: string;
    icon: ReactNode;
    label: string;
    value: ReactNode;
    valueTone?: string;
    onClick?: () => void;
  }) => (
    <button
      type="button"
      onClick={onClick}
      disabled={!onClick}
      className={cn(
        "flex w-full items-center gap-2 py-1 text-left text-xs",
        onClick && "-mx-1 rounded px-1 hover:bg-slate-100",
      )}
    >
      <span className={cn("grid h-5 w-5 flex-none place-items-center rounded", tone)}>{icon}</span>
      <span className="text-slate-500">{label}</span>
      <span className={cn("ml-auto font-semibold text-slate-900", valueTone)}>{value}</span>
    </button>
  );

  const band = (riskBand ?? "").toLowerCase();
  const riskValueTone =
    band === "high" ? "text-rose-600" : band === "medium" ? "text-amber-600" : "text-emerald-600";

  return (
    <div className="shrink-0 border-b border-slate-200 bg-slate-50 px-3 py-2">
      <p className="mb-1 text-[9.5px] font-bold uppercase tracking-[0.1em] text-slate-400">
        At a glance
      </p>
      <Row
        tone="bg-amber-100 text-amber-700"
        icon={<AlertTriangle className="h-3 w-3" />}
        label="Risk"
        value={riskScore != null ? `${riskScore} Â· ${titleCase(riskBand ?? "")}` : "Not scored"}
        valueTone={riskScore != null ? riskValueTone : "text-slate-400 font-normal"}
        onClick={() => onOpen("risk")}
      />
      {phase === "manage" ? (
        <>
          <Row
            tone="bg-brand-50 text-brand-700"
            icon={<ListChecks className="h-3 w-3" />}
            label="Obligations"
            value={`${(obligations ?? []).filter((o) => o.status !== "completed" && o.status !== "cancelled").length} open`}
            onClick={() => onOpen("obligations")}
          />
          <Row
            tone="bg-sky-100 text-sky-700"
            icon={<RefreshCw className="h-3 w-3" />}
            label="Renewal notice"
            value={renewalNoticeLabel(renewals ?? [])}
            valueTone={renewalNoticeTone(renewals ?? [])}
            onClick={() => onOpen("renewals")}
          />
        </>
      ) : (
        <>
          <Row
            tone="bg-rose-100 text-rose-700"
            icon={<AlertTriangle className="h-3 w-3" />}
            label="Open issues"
            value={
              review
                ? `${review.open_issues}${review.high_severity_issues ? ` Â· ${review.high_severity_issues} high` : ""}`
                : "â"
            }
            valueTone={review?.high_severity_issues ? "text-rose-600" : undefined}
            onClick={() => onOpen("redlines")}
          />
          <Row
            tone="bg-amber-100 text-amber-700"
            icon={<FileDiff className="h-3 w-3" />}
            label="Pending redlines"
            value={review ? String(review.pending_redlines) : "â"}
            onClick={() => onOpen("redlines")}
          />
          <Row
            tone="bg-brand-50 text-brand-700"
            icon={<MessageSquare className="h-3 w-3" />}
            label="Open comments"
            value={review ? String(review.open_comments) : "â"}
            onClick={() => onOpen("comments")}
          />
        </>
      )}
    </div>
  );
}

function renewalNoticeLabel(renewals: { notice_date: string | null }[]): string {
  const next = renewals
    .map((r) => r.notice_date)
    .filter((d): d is string => !!d)
    .sort()[0];
  if (!next) return "none set";
  const days = Math.round((new Date(next).getTime() - Date.now()) / 86_400_000);
  if (days < 0) return "window open";
  if (days === 0) return "today";
  return `in ${days}d`;
}
function renewalNoticeTone(renewals: { notice_date: string | null }[]): string {
  const next = renewals
    .map((r) => r.notice_date)
    .filter((d): d is string => !!d)
    .sort()[0];
  if (!next) return "text-slate-400 font-normal";
  const days = Math.round((new Date(next).getTime() - Date.now()) / 86_400_000);
  return days <= 30 ? "text-amber-600" : "text-slate-900";
}

// Inline obligations for the contract (Manage phase) -- no jump to the global page.
function ObligationsPanel({ contractId }: { contractId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["contract", contractId, "obligations"],
    queryFn: () => obligationsApi.list({ contract_id: contractId }),
  });
  if (isLoading) return <CenterSpinner />;
  const rows = data ?? [];
  if (rows.length === 0)
    return (
      <EmptyState
        icon={<ListChecks className="h-6 w-6" />}
        title="No obligations yet"
        description="Obligations extracted from this contract will appear here."
      />
    );
  return (
    <div className="space-y-2">
      {rows.map((o) => (
        <div key={o.id} className="rounded-md border border-slate-200 p-3">
          <div className="mb-1 flex items-center gap-2">
            {o.obligation_type && (
              <span className="rounded-sm bg-brand-50 px-1.5 py-0.5 text-[10px] font-bold uppercase text-brand-700 dark:bg-brand-400/10 dark:text-brand-300">
                {o.obligation_type}
              </span>
            )}
            <Badge tone={statusTone(o.status)}>{titleCase(o.status)}</Badge>
          </div>
          <p className="text-sm font-medium text-slate-900">{o.description}</p>
          <p className="mt-1 text-[11px] text-slate-400">
            {o.responsible_party ?? "Unassigned"}
            {o.due_date ? ` Â· due ${fmtDate(o.due_date)}` : o.recurrence ? ` Â· ${o.recurrence}` : " Â· on trigger"}
          </p>
        </div>
      ))}
      <Link
        href="/obligations"
        className="block pt-1 text-center text-xs font-semibold text-brand-600 hover:underline"
      >
        Open in Obligations â
      </Link>
    </div>
  );
}

// Inline renewals / expiry for the contract (Manage phase).
function RenewalsPanel({ contractId }: { contractId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["contract", contractId, "renewals"],
    queryFn: () => renewalsApi.list(contractId),
  });
  if (isLoading) return <CenterSpinner />;
  const rows = data ?? [];
  if (rows.length === 0)
    return (
      <EmptyState
        icon={<RefreshCw className="h-6 w-6" />}
        title="No renewal tracked"
        description="Renewal and expiry dates extracted from this contract will appear here."
      />
    );
  return (
    <div className="space-y-3">
      {rows.map((r) => (
        <div key={r.id} className="rounded-md border border-brand-200 bg-brand-50 p-3 dark:bg-brand-400/5">
          <p className="mb-1 text-xs font-bold text-brand-700 dark:text-brand-300">
            {r.notice_date
              ? `Renewal notice window: ${fmtDate(r.notice_date)}`
              : r.expiration_date
                ? `Expires: ${fmtDate(r.expiration_date)}`
                : "Renewal tracked"}
          </p>
          <div className="space-y-0.5 text-[11px] text-slate-600 dark:text-slate-300">
            {r.expiration_date && <p>Expiration: {fmtDate(r.expiration_date)}</p>}
            {r.renewal_window_starts_at && <p>Window opens: {fmtDate(r.renewal_window_starts_at)}</p>}
            <p>
              Decision:{" "}
              <span className="font-semibold text-slate-900">{titleCase(r.decision)}</span>
            </p>
          </div>
          <Link
            href="/renewals"
            className="mt-2 inline-block text-[11px] font-semibold text-brand-600 hover:underline"
          >
            Manage renewal â
          </Link>
        </div>
      ))}
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
  const { user } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [accessOpen, setAccessOpen] = useState(false);

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
  async function advanceStage(to: ContractLifecycleStage) {
    setBusy("stage");
    try {
      await contractsApi.transition(id, to, {
        reason: "Guided next step",
      });
      qc.invalidateQueries({ queryKey: ["contract", id] });
      qc.invalidateQueries({ queryKey: ["review-status", id] });
      notify(`Moved to ${to}`, "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Transition failed", "error");
    } finally {
      setBusy(null);
    }
  }

  function handleHeroAction(action: HeroAction) {
    switch (action) {
      case "run_ai":
        reAnalyze();
        break;
      case "move_to_drafting":
        advanceStage("drafting");
        break;
      case "move_to_review":
        advanceStage("review");
        break;
      case "resolve_issues":
      case "resolve_redlines":
        setPanel("redlines");
        break;
      case "resolve_comments":
        setPanel("comments");
        break;
      case "counterparty_round":
        setPanel("negotiation");
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
        setPanel("obligations");
        break;
    }
  }

  // Everything that used to crowd the header now lives in one "More" menu.
  // The More menu is UTILITY only + stage-aware. Primary stage actions (submit
  // for approval, send for signature) live in the mission-control CTA and the
  // Advance-stage button, so we don't duplicate them here. `show` gates each
  // action to the stages where it's meaningful.
  const stage = contract?.lifecycle_stage;
  const moreActions = (
    [
      { key: "playbook", label: "Run playbook", icon: BookMarked, onClick: () => setRunPbOpen(true), show: stage !== "active" && stage !== "closed" },
      { key: "analyze", label: "Re-run AI analysis", icon: ScanSearch, onClick: reAnalyze, loading: busy === "analyze", show: true },
      { key: "oblig", label: "Extract obligations", icon: ListChecks, onClick: extractObligations, loading: busy === "oblig", show: stage === "active" },
      { key: "access", label: "Manage access", icon: Users, onClick: () => setAccessOpen(true), show: !!user && (!!user.roles?.includes("admin") || contract?.owner_user_id === user.id) },
      { key: "activity", label: "Activity & audit trail", icon: History, onClick: () => setActivityOpen(true), show: true },
    ] as {
      key: string;
      label: string;
      icon: typeof Wand2;
      onClick: () => void;
      loading?: boolean;
      show: boolean;
    }[]
  ).filter((a) => a.show);

  if (isLoading) return <CenterSpinner label="Loading contract…" />;
  if (error) return <ErrorState error={error} />;
  if (!contract) return null;

  // The workspace adapts to the contract's phase: negotiate (pre-signature) vs
  // manage (active/closed). The tab set, at-a-glance strip, and header all key
  // off this. `shownPanel` guards against a stale tab from another phase.
  const phase = phaseForStage(contract.lifecycle_stage as ContractLifecycleStage);
  const visiblePanels = phase === "manage" ? MANAGE_PANELS : NEGOTIATE_PANELS;
  const shownPanel: PanelId = visiblePanels.includes(panel) ? panel : "ask";

  return (
    <div className="flex h-full flex-col">
      {/* Slim header — title + status; every action lives in the More menu. */}
      <div className="shrink-0 border-b border-slate-200 bg-slate-100 px-4 py-2.5">
        <div className="flex items-center gap-3">
          <nav
            aria-label="Breadcrumb"
            className="flex shrink-0 items-center gap-1 text-sm"
          >
            <Link
              href="/intake"
              className="flex items-center gap-1.5 rounded px-1 py-0.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              Legal Intake
            </Link>
            <ChevronRight
              aria-hidden="true"
              className="h-3.5 w-3.5 text-slate-400"
            />
          </nav>
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <h1 className="truncate text-base font-semibold tracking-tight text-slate-900">
              {contract.title}
            </h1>
            {(contract.risk_score != null || contract.risk_level) && (
              <button
                onClick={() => setPanel("risk")}
                title="View the weighted risk breakdown"
                className="shrink-0"
              >
                <Badge tone={riskTone(contract.risk_band ?? contract.risk_level)}>
                  {contract.risk_score != null
                    ? `Risk ${contract.risk_score} · ${titleCase(contract.risk_band ?? "")}`
                    : `${titleCase(contract.risk_level ?? "")} risk`}
                </Badge>
              </button>
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

      <OverviewBar contractId={id} collapsed={phase === "manage"} />

      {phase !== "manage" && (
        <NextStepHero
          contract={contract}
          busyAction={busy}
          onAction={handleHeroAction}
        />
      )}

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
              onSelectComment={(cid) => {
                setPanel("comments");
                requestAnimationFrame(() =>
                  document
                    .getElementById(`comment-${cid}`)
                    ?.scrollIntoView({ behavior: "smooth", block: "center" }),
                );
              }}
            />
          </div>
        </div>

        {/* Right: Redlines / Versions / Ask AI as switchable tabs, so the
            document stays put while you move between them. */}
        <section className="flex flex-1 flex-col bg-slate-100 lg:w-[26rem] lg:flex-none lg:shrink-0 lg:border-l lg:border-slate-200">
          {/* At a glance — Manage phase only. In Negotiate the mission-control
              band already summarises issues/redlines/comments, so a second copy
              here would just duplicate it. */}
          {phase === "manage" && (
            <AtAGlance
              contractId={id}
              phase={phase}
              riskScore={contract.risk_score ?? null}
              riskBand={contract.risk_band ?? null}
              onOpen={(pnl) => {
                if (pnl === "ask") setAskMounted(true);
                setPanel(pnl);
              }}
            />
          )}
          {/* Labeled Pivot tabs, filtered to the current phase. */}
          <div className="flex shrink-0 items-center gap-0 overflow-x-auto border-b border-slate-200 px-1.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {visiblePanels.map((pid) => {
              const meta = PANELS[pid];
              const active = shownPanel === pid;
              const Icon = meta.icon;
              const count =
                pid === "redlines" && pendingRedlines > 0 ? pendingRedlines : undefined;
              return (
                <button
                  key={pid}
                  onClick={() => {
                    if (pid === "ask") setAskMounted(true);
                    setPanel(pid);
                  }}
                  className={cn(
                    "relative flex items-center gap-1.5 whitespace-nowrap px-2.5 py-2.5 text-xs font-semibold transition-colors",
                    active ? "text-slate-900" : "text-slate-500 hover:text-slate-900",
                  )}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {meta.label}
                  {count !== undefined && (
                    <span className="flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-500 px-1 text-[10px] font-bold text-white">
                      {count}
                    </span>
                  )}
                  {active && (
                    <span className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-brand-600" />
                  )}
                </button>
              );
            })}
          </div>
          <div className="relative flex-1 overflow-hidden">
            {shownPanel !== "ask" && (
              <div className="h-full overflow-y-auto p-4">
                {shownPanel === "risk" && <RiskPanel contractId={id} />}
                {shownPanel === "versions" && <Versions contractId={id} />}
                {shownPanel === "comments" && <CommentsPanel contractId={id} />}
                {shownPanel === "negotiation" && <NegotiationPanel contractId={id} />}
                {shownPanel === "obligations" && <ObligationsPanel contractId={id} />}
                {shownPanel === "renewals" && <RenewalsPanel contractId={id} />}
                {shownPanel === "redlines" && (
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
                  shownPanel === "ask" ? "" : "hidden",
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
      {activityOpen && (
        <Modal
          open
          onClose={() => setActivityOpen(false)}
          title="Activity & audit trail"
          size="lg"
        >
          <ActivityTab contractId={id} />
        </Modal>
      )}
      {accessOpen && (
        <Modal
          open
          onClose={() => setAccessOpen(false)}
          title="Manage access"
          size="lg"
        >
          <AccessManager contractId={id} contract={contract} />
        </Modal>
      )}
    </div>
  );
}

// ---- Access / sharing (Phase 2 resource grants) --------------------------
const ACCESS_LEVELS = [
  { value: "read", label: "Read" },
  { value: "comment", label: "Comment" },
  { value: "update", label: "Edit" },
  { value: "share", label: "Share" },
  { value: "owner", label: "Owner" },
];

const CONFIDENTIALITY_LEVELS = [
  { value: "public", label: "Public", tone: "slate" as const },
  { value: "internal", label: "Internal", tone: "blue" as const },
  { value: "confidential", label: "Confidential", tone: "amber" as const },
  { value: "restricted", label: "Restricted", tone: "red" as const },
];

function AccessManager({
  contractId,
  contract,
}: {
  contractId: string;
  contract?: ContractResponse;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const grantsKey = ["grants", "contract", contractId];
  const [classifying, setClassifying] = useState(false);

  async function reclassify(next: string) {
    if (!contract || next === contract.confidentiality) return;
    setClassifying(true);
    try {
      await contractsApi.update(contractId, { confidentiality: next });
      qc.invalidateQueries({ queryKey: ["contract", contractId] });
      notify("Classification updated", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "error");
    } finally {
      setClassifying(false);
    }
  }
  const { data: grants, isLoading, error } = useQuery({
    queryKey: grantsKey,
    queryFn: () => grantsApi.list("contract", contractId),
  });
  const { data: users } = useQuery({
    queryKey: ["org-users-all"],
    queryFn: () => usersApi.list(),
  });
  const { data: roles } = useQuery({ queryKey: ["roles"], queryFn: rolesApi.list });

  const [principalType, setPrincipalType] = useState<"user" | "role">("user");
  const [principalId, setPrincipalId] = useState("");
  const [level, setLevel] = useState("read");
  const [expiry, setExpiry] = useState("");
  const [busy, setBusy] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);

  async function add() {
    if (!principalId) return;
    setBusy(true);
    try {
      await grantsApi.create({
        principal_type: principalType,
        principal_id: principalId,
        resource_type: "contract",
        resource_id: contractId,
        access_level: level,
        valid_until: expiry ? new Date(expiry).toISOString() : null,
      });
      qc.invalidateQueries({ queryKey: grantsKey });
      setPrincipalId("");
      setExpiry("");
      notify("Access granted", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Grant failed", "error");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    setRevoking(id);
    try {
      await grantsApi.revoke(id);
      qc.invalidateQueries({ queryKey: grantsKey });
      notify("Access revoked", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Revoke failed", "error");
    } finally {
      setRevoking(null);
    }
  }

  const principals =
    principalType === "user"
      ? (users ?? []).map((u) => ({ id: u.id, label: `${u.full_name} · ${u.email}` }))
      : (roles ?? []).map((r) => ({ id: r.id, label: titleCase(r.name) }));

  return (
    <div className="space-y-5">
      {/* confidentiality classification (MAC) */}
      {contract && (
        <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                Confidentiality
              </p>
              <p className="mt-0.5 text-xs text-slate-500">
                Users below this clearance can’t open it — or reach it via search
                or the assistant.
              </p>
            </div>
            <Select
              value={contract.confidentiality}
              disabled={classifying}
              onChange={(e) => reclassify(e.target.value)}
              className="w-40"
            >
              {CONFIDENTIALITY_LEVELS.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </Select>
          </div>
        </div>
      )}

      {/* current access */}
      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Who has access
        </p>
        {isLoading ? (
          <CenterSpinner />
        ) : error ? (
          <ErrorState error={error} />
        ) : (grants ?? []).length === 0 ? (
          <p className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-500">
            No explicit grants yet — access follows ownership, matter/project
            membership and admin.
          </p>
        ) : (
          <div className="space-y-1.5">
            {(grants ?? []).map((g) => (
              <div
                key={g.id}
                className="flex items-center justify-between gap-3 rounded-md border border-slate-200 px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-800">
                    {g.principal_label}
                    <Badge tone="slate" className="ml-2">
                      {g.principal_type}
                    </Badge>
                  </p>
                  <p className="text-xs text-slate-400">
                    {ACCESS_LEVELS.find((l) => l.value === g.access_level)?.label ??
                      g.access_level}
                    {g.valid_until
                      ? ` · until ${new Date(g.valid_until).toLocaleDateString()}`
                      : ""}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => revoke(g.id)}
                  loading={revoking === g.id}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Revoke
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* add access */}
      <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
        <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
          <UserPlus className="h-3.5 w-3.5" /> Grant access
        </p>
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="To">
            <div className="flex gap-2">
              <Select
                value={principalType}
                onChange={(e) => {
                  setPrincipalType(e.target.value as "user" | "role");
                  setPrincipalId("");
                }}
                className="w-28 shrink-0"
              >
                <option value="user">Person</option>
                <option value="role">Role</option>
              </Select>
              <Select
                value={principalId}
                onChange={(e) => setPrincipalId(e.target.value)}
              >
                <option value="">Select…</option>
                {principals.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </Select>
            </div>
          </Field>
          <Field label="Access level">
            <Select value={level} onChange={(e) => setLevel(e.target.value)}>
              {ACCESS_LEVELS.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Expires" hint="Optional — leave blank for no expiry">
            <Input
              type="date"
              value={expiry}
              onChange={(e) => setExpiry(e.target.value)}
            />
          </Field>
          <div className="flex items-end justify-end">
            <Button onClick={add} loading={busy} disabled={!principalId}>
              Grant access
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---- Overview band (sits above the document) -----------------------------
function RiskPanel({ contractId }: { contractId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["contract-risk", contractId],
    queryFn: () => contractsApi.risk(contractId),
  });

  async function compute() {
    setBusy(true);
    try {
      const res = await contractsApi.computeRisk(contractId);
      qc.setQueryData(["contract-risk", contractId], res);
      qc.invalidateQueries({ queryKey: ["contract", contractId] });
      notify("Risk score computed", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to compute risk", "error");
    } finally {
      setBusy(false);
    }
  }

  const bandTone = (b?: string | null) =>
    b === "high" ? "red" : b === "medium" ? "amber" : "green";

  if (isLoading) return <CenterSpinner label="Loading risk…" />;
  const has = data && typeof data.score === "number";

  if (!has) {
    return (
      <div className="space-y-3">
        <EmptyState
          icon={<AlertTriangle className="h-6 w-6" />}
          title="No risk score yet"
          description={
            data?.note ??
            "Compute a weighted, explainable risk score from this contract's clauses."
          }
        />
        <Button className="w-full" loading={busy} onClick={compute}>
          <AlertTriangle className="h-4 w-4" />
          Compute risk score
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-slate-100 p-4">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-3xl font-semibold tabular-nums text-slate-900">
            {data!.score}
          </span>
          <span className="text-sm text-slate-400">/100</span>
          <Badge tone={bandTone(data!.band)} className="ml-auto">
            {titleCase(data!.band)} risk
          </Badge>
        </div>
        <p className="mt-1.5 text-xs text-slate-500">
          <span className="font-medium text-rose-600">{data!.counts.high} high</span>{" "}
          ·{" "}
          <span className="font-medium text-amber-600">
            {data!.counts.medium} medium
          </span>{" "}
          · {data!.counts.low} low — weighted by clause impact
        </p>
        {data!.summary && (
          <p className="mt-2.5 text-sm leading-relaxed text-slate-600">
            {data!.summary}
          </p>
        )}
      </div>

      <div className="space-y-2">
        <p className="px-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Top risk drivers
        </p>
        {data!.drivers.map((d, i) => (
          <div
            key={i}
            className="rounded-lg border border-slate-200 bg-slate-100 p-3"
          >
            <div className="mb-1 flex items-center gap-2">
              <Badge tone={bandTone(d.risk)}>{titleCase(d.risk)}</Badge>
              <span className="text-sm font-medium text-slate-800">{d.label}</span>
              <span className="ml-auto font-mono text-xs text-slate-400">
                weight {d.weight}
              </span>
            </div>
            <p className="text-sm text-slate-600">{d.rationale}</p>
            {d.quote && (
              <p className="mt-1.5 border-l-2 border-slate-300 pl-2 text-xs italic text-slate-500">
                “{d.quote}”
              </p>
            )}
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between pt-1">
        <span className="text-xs text-slate-400">
          {data!.clause_count} clauses assessed
        </span>
        <Button size="sm" variant="outline" loading={busy} onClick={compute}>
          Recompute
        </Button>
      </div>
    </div>
  );
}

function OverviewBar({
  contractId,
  collapsed = false,
}: {
  contractId: string;
  collapsed?: boolean;
}) {
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
  const [trackerOpen, setTrackerOpen] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const { user } = useAuth();
  const { data: history } = useQuery({
    queryKey: ["contract", contractId, "stage-history"],
    queryFn: () => contractsApi.stageHistory(contractId),
  });
  const { data: review } = useQuery({
    queryKey: ["review-status", contractId],
    queryFn: () => contractsApi.reviewStatus(contractId),
  });
  const { data: chain } = useQuery({
    queryKey: ["approval-chain", contractId],
    queryFn: () => approvalsApi.chain(contractId),
    enabled: contract?.lifecycle_stage === "approval",
  });

  if (!contract) return null;

  const currentIdx = LIFECYCLE_STAGES.indexOf(contract.lifecycle_stage);

  // ---- Stage-tracker derivations (all from data we already store) ----
  const sortedHistory = [...(history ?? [])].sort(
    (a, b) => new Date(a.changed_at).getTime() - new Date(b.changed_at).getTime(),
  );
  const enteredAt = (stage: string): string | null => {
    for (let i = sortedHistory.length - 1; i >= 0; i--)
      if (sortedHistory[i].to_stage === stage) return sortedHistory[i].changed_at;
    return null;
  };
  const daysBetween = (a: string, b: string) =>
    Math.max(0, Math.round((new Date(b).getTime() - new Date(a).getTime()) / 86_400_000));
  const stageMeta = (s: ContractLifecycleStage, i: number): string | null => {
    const at = enteredAt(s);
    if (i < currentIdx) {
      if (!at) return null;
      const idx = sortedHistory.findIndex((h) => h.changed_at === at && h.to_stage === s);
      const next = sortedHistory.slice(idx + 1).find((h) => h.to_stage !== s);
      const d = next ? daysBetween(at, next.changed_at) : null;
      return `${fmtDate(at)}${d ? ` · ${d}d` : ""}`;
    }
    if (i === currentIdx && at) {
      const d = daysBetween(at, new Date().toISOString());
      return d > 0 ? `${d}d in stage` : "entered today";
    }
    return null;
  };
  const preApproval = ["intake", "drafting", "review"].includes(contract.lifecycle_stage);
  // The ONE gate to approval is undecided redlines (the text must be final).
  // Open issues / comments are advisory and no longer lock the Approval node.
  const blockerParts: string[] = [];
  if (preApproval && review && review.pending_redlines > 0) {
    blockerParts.push(
      `${review.pending_redlines} open redline${review.pending_redlines === 1 ? "" : "s"}`,
    );
  }
  const lastMove = sortedHistory[sortedHistory.length - 1];
  const pendingApprover = chain?.steps?.find((st) => st.status === "pending");
  const waitingOn =
    contract.lifecycle_stage === "approval"
      ? pendingApprover
        ? `Waiting on ${pendingApprover.approver_label}`
        : "Awaiting approvals"
      : contract.lifecycle_stage === "signature"
        ? "Waiting on signature"
        : contract.lifecycle_stage === "active"
          ? "Monitoring — renewals & obligations"
          : contract.lifecycle_stage === "closed"
            ? null
            : user?.id === contract.owner_user_id
              ? "Waiting on you"
              : "Waiting on contract owner";

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
      {/* Rich lifecycle tracker: state + dates + durations per stage, whose
          turn, last move, and a gate-aware Advance button. */}
      {collapsed && !trackerOpen ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span className="flex items-center gap-1.5 font-semibold text-slate-700">
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
            {titleCase(contract.lifecycle_stage)}
          </span>
          {(() => {
            const at = enteredAt(contract.lifecycle_stage);
            const d = at ? daysBetween(at, new Date().toISOString()) : null;
            return (
              <span className="text-slate-400">
                {at ? `since ${fmtDate(at)}` : ""}
                {d != null ? ` · ${d}d in stage` : ""}
              </span>
            );
          })()}
          {waitingOn && <span className="text-slate-400">· {waitingOn}</span>}
          <button
            onClick={() => setTrackerOpen(true)}
            className="ml-auto font-semibold text-brand-600 hover:underline"
          >
            Timeline ▾
          </button>
          {options && options.allowed_transitions.length > 0 && (
            <button
              onClick={() => setTransitionOpen(true)}
              className="rounded-md border border-slate-300 px-2.5 py-1 font-semibold text-slate-700 hover:bg-slate-100"
            >
              Advance stage
            </button>
          )}
        </div>
      ) : (
      <>
      {collapsed && (
        <button
          onClick={() => setTrackerOpen(false)}
          className="mb-1.5 text-[11px] font-semibold text-brand-600 hover:underline"
        >
          ▴ Hide timeline
        </button>
      )}
      <div className="flex items-start gap-3">
        <ol className="flex flex-1 items-start overflow-x-auto pb-0.5">
          {LIFECYCLE_STAGES.map((s, i) => {
            const done = i < currentIdx;
            const current = i === currentIdx;
            const locked =
              s === "approval" && preApproval && blockerParts.length > 0;
            const meta = stageMeta(s, i);
            return (
              <li key={s} className="flex min-w-0 flex-1 items-start last:flex-none">
                <div className="flex shrink-0 flex-col items-center gap-1 px-1" style={{ minWidth: 70 }}>
                  <span
                    aria-current={current ? "step" : undefined}
                    className={cn(
                      "flex h-6 w-6 items-center justify-center rounded-full text-[11px] transition-colors",
                      done && "bg-brand-600 text-white",
                      current &&
                        "bg-brand-500 text-white ring-4 ring-brand-500/20",
                      locked &&
                        "border-[1.5px] border-dashed border-amber-400/70 text-amber-500",
                      !done && !current && !locked &&
                        "border-[1.5px] border-slate-300 text-slate-300",
                    )}
                  >
                    {done ? (
                      <Check className="h-3.5 w-3.5" />
                    ) : current ? (
                      <Circle className="h-2.5 w-2.5 fill-current" />
                    ) : locked ? (
                      <Lock className="h-3 w-3" />
                    ) : null}
                  </span>
                  <span
                    className={cn(
                      "whitespace-nowrap text-[11px] font-semibold",
                      current
                        ? "text-brand-600"
                        : done
                          ? "text-slate-500"
                          : locked
                            ? "text-amber-600/90 dark:text-amber-400/90"
                            : "text-slate-400",
                    )}
                  >
                    {titleCase(s)}
                  </span>
                  <span
                    className={cn(
                      "whitespace-nowrap font-mono text-[9.5px]",
                      current
                        ? options?.sla_breached
                          ? "font-semibold text-rose-500"
                          : "text-brand-500/90"
                        : locked
                          ? "text-amber-500/80"
                          : "text-slate-400",
                    )}
                  >
                    {locked
                      ? `${blockerParts.length} blocker${blockerParts.length === 1 ? "" : "s"}`
                      : current && options?.sla_breached
                        ? `${meta ?? ""} · SLA ${options.stage_sla_days}d ⚠`
                        : meta ?? "—"}
                  </span>
                </div>
                {i < LIFECYCLE_STAGES.length - 1 && (
                  <span
                    className={cn(
                      "mt-3 h-0.5 flex-1",
                      i < currentIdx ? "bg-brand-500/70" : "bg-slate-200",
                    )}
                    style={{ minWidth: 10 }}
                  />
                )}
              </li>
            );
          })}
        </ol>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => setShowDetails((v) => !v)}>
              <Info className="h-4 w-4" />
              Details
            </Button>
            <Button
              size="sm"
              variant="outline"
              className={cn(
                blockerParts.length > 0 &&
                  "border-amber-300 text-amber-700 hover:border-amber-400 dark:border-amber-400/50 dark:text-amber-400",
              )}
              disabled={!options?.allowed_transitions.length}
              onClick={() => setTransitionOpen(true)}
            >
              Advance stage
              {blockerParts.length > 0 && (
                <span className="rounded bg-amber-100 px-1.5 font-mono text-[10px] font-semibold text-amber-700 dark:bg-amber-400/15 dark:text-amber-300">
                  {blockerParts.length}
                </span>
              )}
            </Button>
          </div>
          {blockerParts.length > 0 && (
            <span className="text-[10px] text-slate-400">
              {blockerParts.join(" · ")} — override needs a reason
            </span>
          )}
        </div>
      </div>

      {/* Whose turn + last move + expandable history. */}
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
        {waitingOn && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-brand-200 bg-brand-50 px-2.5 py-0.5 text-[11px] font-semibold text-brand-700 dark:border-brand-400/40 dark:bg-brand-400/10 dark:text-brand-300">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-500" />
            {waitingOn}
          </span>
        )}
        {lastMove && (
          <span>
            Moved to{" "}
            <span className="font-medium text-slate-600">
              {titleCase(lastMove.to_stage)}
            </span>
            {user?.id && lastMove.changed_by_user_id === user.id ? " by you" : ""} ·{" "}
            {fmtDateTime(lastMove.changed_at)}
          </span>
        )}
        {sortedHistory.length > 0 && (
          <button
            onClick={() => setHistoryOpen((o) => !o)}
            className="font-medium text-brand-600 hover:text-brand-700"
          >
            {historyOpen ? "Hide history" : "View full history"}
          </button>
        )}
      </div>
      {historyOpen && (
        <ol className="mt-2 space-y-1 border-l-2 border-slate-200 pl-3 text-xs text-slate-500">
          {[...sortedHistory].reverse().map((h) => (
            <li key={h.id}>
              {h.from_stage ? `${titleCase(h.from_stage)} → ` : ""}
              <span className="font-medium text-slate-700">{titleCase(h.to_stage)}</span>
              {" · "}
              {fmtDateTime(h.changed_at)}
              {h.override_used && (
                <span className="ml-1 rounded bg-amber-100 px-1 text-[10px] text-amber-700 dark:bg-amber-400/15 dark:text-amber-300">
                  override
                </span>
              )}
              {h.reason && <span className="text-slate-400"> — {h.reason}</span>}
            </li>
          ))}
        </ol>
      )}

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
      </>
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

  const [modifyId, setModifyId] = useState<string | null>(null);
  const [modifyText, setModifyText] = useState("");

  // "Modify" = supersede the AI's suggestion with your own wording. Rides the
  // manual-redline pipeline (propose your text, reject the original) so accept
  // applies exactly your language and it works even for multi-edit AI batches.
  async function saveModify(e: ContractEditResponse) {
    if (!e.original_text || !modifyText.trim()) return;
    try {
      await contractsApi.proposeEdit(contractId, {
        original_text: e.original_text,
        replacement_text: modifyText,
        rationale: `Modified from AI suggestion${e.rationale ? " — " + e.rationale : ""}`,
      });
      await contractsApi.rejectEdit(contractId, e.id);
      qc.invalidateQueries({ queryKey: ["contract", contractId, "edits"] });
      qc.invalidateQueries({ queryKey: ["contract", contractId, "versions"] });
      qc.invalidateQueries({ queryKey: ["review-status", contractId] });
      setModifyId(null);
      notify("Saved your modified redline", "success");
    } catch (err) {
      notify(err instanceof Error ? err.message : "Failed to modify", "error");
    }
  }

  async function decide(editId: string, accept: boolean) {
    // Optimistically flip the edit's status so the card updates instantly;
    // roll back if the API call fails, reconcile with the server either way.
    const key = ["contract", contractId, "edits"] as const;
    const prev = qc.getQueryData<ContractEditResponse[]>(key);
    if (prev) {
      qc.setQueryData<ContractEditResponse[]>(
        key,
        prev.map((e) =>
          e.id === editId
            ? { ...e, status: accept ? "accepted" : "rejected" }
            : e,
        ),
      );
    }
    try {
      if (accept) await contractsApi.acceptEdit(contractId, editId);
      else await contractsApi.rejectEdit(contractId, editId);
      qc.invalidateQueries({ queryKey: key });
      qc.invalidateQueries({ queryKey: ["contract", contractId] });
      qc.invalidateQueries({ queryKey: ["review-status", contractId] });
      notify(accept ? "Edit accepted" : "Edit rejected", "success");
    } catch (e) {
      if (prev) qc.setQueryData(key, prev);
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
      {[...data]
        .sort((a, b) => {
          // Proposed first, then by severity (high → low) so the changes that
          // matter most sit at the top.
          const ap = a.status === "proposed" ? 0 : 1;
          const bp = b.status === "proposed" ? 0 : 1;
          if (ap !== bp) return ap - bp;
          const sev: Record<string, number> = { high: 0, medium: 1, low: 2 };
          const as = sev[editAnchor(a.citation)?.risk_level ?? ""] ?? 3;
          const bs = sev[editAnchor(b.citation)?.risk_level ?? ""] ?? 3;
          return as - bs;
        })
        .map((e) => {
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
              {e.status === "proposed" &&
                (modifyId === e.id ? (
                  <div
                    className="space-y-2"
                    onClick={(ev) => ev.stopPropagation()}
                  >
                    <textarea
                      value={modifyText}
                      onChange={(ev) => setModifyText(ev.target.value)}
                      rows={4}
                      autoFocus
                      placeholder="Your replacement language…"
                      className="w-full resize-none rounded-lg border border-slate-200 bg-slate-50 p-2.5 text-sm focus:border-brand-400 focus:outline-none focus:ring-1 focus:ring-brand-400"
                    />
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        loading={false}
                        disabled={!modifyText.trim()}
                        onClick={() => saveModify(e)}
                      >
                        <Check className="h-3.5 w-3.5" />
                        Save change
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setModifyId(null)}
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
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
                    <Button
                      size="sm"
                      variant="ghost"
                      title="Edit the language before accepting"
                      onClick={(ev) => {
                        ev.stopPropagation();
                        setModifyId(e.id);
                        setModifyText(e.replacement_text ?? "");
                      }}
                    >
                      <PenLine className="h-3.5 w-3.5" />
                      Modify
                    </Button>
                  </div>
                ))}
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
