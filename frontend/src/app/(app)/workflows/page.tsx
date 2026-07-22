"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Workflow as WorkflowIcon,
  Sparkles,
  Table2,
  MessageSquare,
  MoreHorizontal,
  EyeOff,
  Eye,
  ChevronDown,
  Check,
  Copy,
  Search,
  Star,
  FileText,
  Pencil,
  History,
  Users,
  RotateCcw,
  Globe,
  Lock,
  BarChart3,
} from "lucide-react";
import { contractsApi, usersApi, workflowsApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import {
  Badge,
  Button,
  CenterSpinner,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Modal,
  PageHeader,
  Select,
  Textarea,
} from "@/components/ui";
import { Markdown } from "@/components/markdown";
import { CreateReviewModal } from "@/components/create-review-modal";
import { cn, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { Workflow, WorkflowVersion, WorkflowUsage } from "@/lib/types";

const WORKFLOW_TYPES = [
  "assistant",
  "tabular_review",
  "contract_review",
  "drafting",
  "intake",
];
const VISIBILITIES = [
  "private",
  "shared_with_users",
  "org_wide",
];

// Prompts can contain {{variables}}. We parse them so the library can offer a
// fill-in form on Use, then substitute the values into the prompt.
const PREFILL_KEY = "aegis-prompt-prefill";
const VAR_RE = /\{\{\s*([^{}]+?)\s*\}\}/g;
function extractVars(prompt: string): string[] {
  const set = new Set<string>();
  const re = new RegExp(VAR_RE);
  let m: RegExpExecArray | null;
  while ((m = re.exec(prompt))) set.add(m[1].trim());
  return [...set];
}
function fillVars(prompt: string, values: Record<string, string>): string {
  return prompt.replace(new RegExp(VAR_RE), (_, k) => {
    const key = String(k).trim();
    return (values[key] ?? "").trim() ? values[key] : `{{${key}}}`;
  });
}
function matchesSearch(w: Workflow, q: string): boolean {
  if (!q.trim()) return true;
  const def = w.definition as {
    prompt?: string;
    columns?: { name: string; prompt: string }[];
  };
  const hay = [
    w.name,
    w.description ?? "",
    def?.prompt ?? "",
    ...(def?.columns ?? []).flatMap((c) => [c.name, c.prompt]),
  ]
    .join(" ")
    .toLowerCase();
  return hay.includes(q.toLowerCase());
}

const HIDDEN_KEY = "aegis_hidden_workflows";
const FAVORITES_KEY = "aegis_favorite_workflows";

// A well-formed legal prompt has five parts: role, task, context, output
// shape, and guardrails. New assistant prompts can start from this scaffold —
// the {{placeholders}} plug straight into the Tier-1 fill-in form.
const PROMPT_SCAFFOLD = `## Role
You are a senior {{practice_area}} attorney reviewing on behalf of our company.

## Task
{{task}}

## Context
- Document under review: the attached contract.
- Our standard position / playbook: {{standard_position}}
- Counterparty: {{counterparty}}

## Output
1. **Bottom line** — 2–3 sentences: can we sign, sign-with-changes, or escalate?
2. **Findings** — a table of issue · clause reference · risk (High/Med/Low) · recommendation.
3. **Suggested redlines** — exact replacement language for each High-risk item.

## Constraints
- Cite the specific clause or section number for every finding.
- Flag anything ambiguous, missing, or non-market instead of assuming.
- This is drafting support, not a legal opinion — mark where counsel sign-off is required.`;

function isBuiltin(w: Workflow) {
  return !!w.is_builtin || w.visibility === "system_builtin";
}

function practiceOf(w: Workflow): string {
  if (w.practice) return w.practice;
  const m = (w.description ?? "").match(/^Built-in prompt · (.+)$/);
  return m ? m[1] : "—";
}

const VISIBILITY_LABEL: Record<string, string> = {
  private: "Only me",
  shared_with_users: "Specific people",
  org_wide: "Everyone in org",
  system_builtin: "Built-in",
};

// The three visibilities a user can pick for their own prompt.
const SHAREABLE_VISIBILITIES = [
  { value: "private", label: "Only me", icon: Lock },
  { value: "org_wide", label: "Everyone in org", icon: Globe },
  { value: "shared_with_users", label: "Specific people", icon: Users },
] as const;

function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

function ArrowRightIcon() {
  return (
    <svg
      className="h-4 w-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}

function FilterMenu({
  label,
  value,
  open,
  setOpen,
  options,
  onPick,
}: {
  label: string;
  value: string | null;
  open: boolean;
  setOpen: (b: boolean) => void;
  options: { value: string; label: string }[];
  onPick: (v: string | null) => void;
}) {
  const short = label.replace("Filter by ", "");
  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1.5 rounded border border-slate-200 bg-slate-100 px-3 py-1.5 text-[13px] text-slate-600 hover:bg-slate-50"
      >
        {value
          ? `${short}: ${options.find((o) => o.value === value)?.label ?? value}`
          : label}
        <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
      </button>
      {open && (
        <>
          <div
            className="fixed inset-0 z-10"
            onClick={() => setOpen(false)}
          />
          <div className="absolute right-0 z-20 mt-1 max-h-72 w-56 overflow-y-auto rounded-md border border-slate-200 bg-slate-100 p-1 shadow-pop">
            <button
              onClick={() => {
                onPick(null);
                setOpen(false);
              }}
              className="flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left text-sm text-slate-700 hover:bg-slate-50"
            >
              All {short.toLowerCase()}
              {value === null && (
                <Check className="h-3.5 w-3.5 text-brand-600" />
              )}
            </button>
            {options.map((o) => (
              <button
                key={o.value}
                onClick={() => {
                  onPick(o.value);
                  setOpen(false);
                }}
                className="flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left text-sm text-slate-700 hover:bg-slate-50"
              >
                {o.label}
                {value === o.value && (
                  <Check className="h-3.5 w-3.5 text-brand-600" />
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export default function WorkflowsPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const { notify } = useToast();
  const { user } = useAuth();
  const [newOpen, setNewOpen] = useState(false);
  const [selected, setSelected] = useState<Workflow | null>(null);
  const [editing, setEditing] = useState<Workflow | null>(null);
  const [reviewWfId, setReviewWfId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["workflows"],
    queryFn: workflowsApi.list,
  });
  const { data: analytics } = useQuery({
    queryKey: ["workflow-analytics"],
    queryFn: workflowsApi.analytics,
  });

  // The current user owns (and may edit) a prompt if it's a custom one they
  // created — built-ins are read-only for everyone.
  const canEdit = (w: Workflow) =>
    !isBuiltin(w) && !!user && w.created_by_user_id === user.id;

  // Fire-and-forget usage ping so the analytics rollup reflects real use.
  function recordLaunch(w: Workflow, mode: string, contractId?: string) {
    workflowsApi
      .launch(w.id, { mode, contract_id: contractId })
      .then(() => qc.invalidateQueries({ queryKey: ["workflow-analytics"] }))
      .catch(() => {
        /* analytics is best-effort — never block the user */
      });
  }

  const [tab, setTab] = useState<"all" | "builtin" | "custom" | "hidden">(
    "all",
  );
  const [typeF, setTypeF] = useState<string | null>(null);
  const [practiceF, setPracticeF] = useState<string | null>(null);
  const [typeOpen, setTypeOpen] = useState(false);
  const [practiceOpen, setPracticeOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [menuId, setMenuId] = useState<string | null>(null);
  const [hidden, setHidden] = useState<string[]>([]);
  const [favorites, setFavorites] = useState<string[]>([]);

  useEffect(() => {
    try {
      setHidden(JSON.parse(localStorage.getItem(HIDDEN_KEY) || "[]"));
      setFavorites(JSON.parse(localStorage.getItem(FAVORITES_KEY) || "[]"));
    } catch {
      /* ignore */
    }
  }, []);

  function toggleHide(id: string) {
    const next = hidden.includes(id)
      ? hidden.filter((x) => x !== id)
      : [...hidden, id];
    setHidden(next);
    localStorage.setItem(HIDDEN_KEY, JSON.stringify(next));
    setMenuId(null);
  }

  function toggleFavorite(id: string) {
    const next = favorites.includes(id)
      ? favorites.filter((x) => x !== id)
      : [...favorites, id];
    setFavorites(next);
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(next));
  }

  const all = useMemo(() => data ?? [], [data]);
  const practices = useMemo(
    () =>
      [...new Set(all.map(practiceOf).filter((p) => p && p !== "—"))].sort(),
    [all],
  );
  const types = useMemo(
    () => [...new Set(all.map((w) => w.workflow_type))],
    [all],
  );

  const rows = all
    .filter((w) => {
      const h = hidden.includes(w.id);
      if (tab === "hidden") return h;
      if (h) return false;
      if (tab === "builtin") return isBuiltin(w);
      if (tab === "custom") return !isBuiltin(w);
      return true;
    })
    .filter((w) => !typeF || w.workflow_type === typeF)
    .filter((w) => !practiceF || practiceOf(w) === practiceF)
    .filter((w) => matchesSearch(w, search))
    // Pinned prompts float to the top; Array.sort is stable so the original
    // order is preserved within the pinned and unpinned groups.
    .sort(
      (a, b) =>
        (favorites.includes(b.id) ? 1 : 0) - (favorites.includes(a.id) ? 1 : 0),
    );

  const TABS: { id: typeof tab; label: string }[] = [
    { id: "all", label: "All" },
    { id: "builtin", label: "Built-in" },
    { id: "custom", label: "Custom" },
    { id: "hidden", label: "Hidden" },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title="Prompt Library"
        description="Reusable, ready-to-run prompts for review, drafting and analysis — organized by practice area."
        actions={
          <Button onClick={() => setNewOpen(true)}>
            <Plus className="h-4 w-4" />
            New prompt
          </Button>
        }
      />

      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200">
        <div className="flex gap-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                tab === t.id
                  ? "border-brand-600 text-slate-900"
                  : "border-transparent text-slate-500 hover:text-slate-800",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 pb-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search prompts…"
              className="h-8 w-52 pl-8 text-xs"
            />
          </div>
          <FilterMenu
            label="Filter by type"
            value={typeF}
            open={typeOpen}
            setOpen={setTypeOpen}
            options={types.map((t) => ({ value: t, label: titleCase(t) }))}
            onPick={setTypeF}
          />
          <FilterMenu
            label="Filter by practice"
            value={practiceF}
            open={practiceOpen}
            setOpen={setPracticeOpen}
            options={practices.map((p) => ({ value: p, label: p }))}
            onPick={setPracticeF}
          />
        </div>
      </div>

      {isLoading ? (
        <CenterSpinner label="Loading prompts…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<WorkflowIcon className="h-6 w-6" />}
          title="No prompts here"
          description="Try a different tab or filter, or add a new prompt."
          action={
            <Button onClick={() => setNewOpen(true)}>
              <Plus className="h-4 w-4" />
              New prompt
            </Button>
          }
        />
      ) : (
        <div className="overflow-x-auto rounded-md border border-slate-200 bg-slate-100">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-slate-200 text-left text-[11px] font-medium uppercase tracking-[0.04em] text-slate-500">
                <th className="px-3 py-2">Name</th>
                <th className="px-3 py-2">Type</th>
                <th className="px-3 py-2">Practice</th>
                <th className="px-3 py-2">Used</th>
                <th className="px-3 py-2">Source</th>
                <th className="w-12 px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((w) => {
                const asst = w.workflow_type === "assistant";
                const tab2 = w.workflow_type === "tabular_review";
                const usage = analytics?.[w.id];
                return (
                  <tr
                    key={w.id}
                    onClick={() => setSelected(w)}
                    className="cursor-pointer border-b border-slate-100 last:border-0 transition-colors hover:bg-slate-100"
                  >
                    <td className="px-3 py-2 font-medium text-slate-900">
                      <div className="flex items-center gap-2">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            toggleFavorite(w.id);
                          }}
                          className={cn(
                            "-ml-1 rounded p-0.5 transition-colors",
                            favorites.includes(w.id)
                              ? "text-warning"
                              : "text-slate-300 hover:text-slate-500",
                          )}
                          aria-label={
                            favorites.includes(w.id) ? "Unpin prompt" : "Pin prompt"
                          }
                          title={favorites.includes(w.id) ? "Unpin" : "Pin to top"}
                        >
                          <Star
                            className="h-4 w-4"
                            fill={favorites.includes(w.id) ? "currentColor" : "none"}
                          />
                        </button>
                        {w.name}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={cn(
                          "inline-flex items-center gap-1.5 text-xs font-medium",
                          asst
                            ? "text-brand-600"
                            : tab2
                              ? "text-brand-600"
                              : "text-slate-500",
                        )}
                      >
                        {asst ? (
                          <MessageSquare className="h-3.5 w-3.5" />
                        ) : tab2 ? (
                          <Table2 className="h-3.5 w-3.5" />
                        ) : (
                          <WorkflowIcon className="h-3.5 w-3.5" />
                        )}
                        {asst
                          ? "Assistant"
                          : tab2
                            ? "Tabular"
                            : titleCase(w.workflow_type)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-slate-600">
                      {practiceOf(w)}
                    </td>
                    <td className="px-3 py-2">
                      {usage && usage.run_count > 0 ? (
                        <span
                          className="tabular-nums text-slate-600"
                          title={`Last used ${timeAgo(usage.last_run_at)}`}
                        >
                          {usage.run_count}×
                        </span>
                      ) : (
                        <span className="text-slate-300">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <span className="inline-flex items-center gap-1.5 text-slate-600">
                        {isBuiltin(w) ? (
                          <>
                            <Sparkles className="h-3.5 w-3.5 text-brand-600" />
                            Built-in
                          </>
                        ) : (
                          "You"
                        )}
                      </span>
                    </td>
                    <td
                      className="relative px-3 py-2 text-right"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        onClick={() =>
                          setMenuId(menuId === w.id ? null : w.id)
                        }
                        className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                        aria-label="Prompt actions"
                      >
                        <MoreHorizontal className="h-4 w-4" />
                      </button>
                      {menuId === w.id && (
                        <>
                          <div
                            className="fixed inset-0 z-10"
                            onClick={() => setMenuId(null)}
                          />
                          <div className="absolute right-4 z-20 mt-1 w-40 rounded-md border border-slate-200 bg-slate-100 p-1 shadow-pop">
                            <button
                              onClick={() => {
                                setSelected(w);
                                setMenuId(null);
                              }}
                              className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm text-slate-700 hover:bg-slate-50"
                            >
                              <ArrowRightIcon /> Open
                            </button>
                            {canEdit(w) && (
                              <button
                                onClick={() => {
                                  setEditing(w);
                                  setMenuId(null);
                                }}
                                className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm text-slate-700 hover:bg-slate-50"
                              >
                                <Pencil className="h-4 w-4" /> Edit
                              </button>
                            )}
                            <button
                              onClick={() => {
                                toggleFavorite(w.id);
                                setMenuId(null);
                              }}
                              className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm text-slate-700 hover:bg-slate-50"
                            >
                              <Star
                                className="h-4 w-4"
                                fill={
                                  favorites.includes(w.id) ? "currentColor" : "none"
                                }
                              />
                              {favorites.includes(w.id) ? "Unpin" : "Pin to top"}
                            </button>
                            <button
                              onClick={() => toggleHide(w.id)}
                              className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm text-slate-700 hover:bg-slate-50"
                            >
                              {hidden.includes(w.id) ? (
                                <>
                                  <Eye className="h-4 w-4" /> Unhide
                                </>
                              ) : (
                                <>
                                  <EyeOff className="h-4 w-4" /> Hide
                                </>
                              )}
                            </button>
                          </div>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <NewWorkflowModal
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onCreated={() => {
          qc.invalidateQueries({ queryKey: ["workflows"] });
          notify("Prompt created", "success");
          setNewOpen(false);
        }}
      />

      <WorkflowDetailModal
        workflow={selected}
        usage={selected ? analytics?.[selected.id] : undefined}
        canEdit={selected ? canEdit(selected) : false}
        onEdit={(w) => {
          setSelected(null);
          setEditing(w);
        }}
        onClose={() => setSelected(null)}
        onUse={(w, filledPrompt, contractId) => {
          if (w.workflow_type === "assistant") {
            setSelected(null);
            recordLaunch(w, contractId ? "contract" : "assistant", contractId);
            // Running against a contract always hands the full prompt text over
            // (so the composer is pre-typed) and opens that contract's session.
            if (contractId) {
              try {
                sessionStorage.setItem(PREFILL_KEY, filledPrompt ?? "");
              } catch {
                /* ignore storage errors */
              }
              router.push(`/assistant?contract=${contractId}`);
            } else if (filledPrompt !== undefined) {
              try {
                sessionStorage.setItem(PREFILL_KEY, filledPrompt);
              } catch {
                /* ignore storage errors */
              }
              router.push("/assistant");
            } else {
              router.push(`/assistant?workflow=${w.id}`);
            }
          } else if (w.workflow_type === "tabular_review") {
            setSelected(null);
            recordLaunch(w, "tabular");
            setReviewWfId(w.id);
          }
        }}
      />

      <EditWorkflowModal
        workflow={editing}
        onClose={() => setEditing(null)}
        onSaved={() => {
          qc.invalidateQueries({ queryKey: ["workflows"] });
          setEditing(null);
        }}
      />

      <CreateReviewModal
        open={!!reviewWfId}
        defaultWorkflowId={reviewWfId ?? undefined}
        onClose={() => setReviewWfId(null)}
        onCreated={(id) => {
          setReviewWfId(null);
          router.push(`/tabular-reviews/${id}`);
        }}
      />
    </div>
  );
}

function WorkflowDetailModal({
  workflow,
  usage,
  canEdit,
  onEdit,
  onClose,
  onUse,
}: {
  workflow: Workflow | null;
  usage?: WorkflowUsage;
  canEdit: boolean;
  onEdit: (w: Workflow) => void;
  onClose: () => void;
  onUse: (w: Workflow, filledPrompt?: string, contractId?: string) => void;
}) {
  const { notify } = useToast();
  const def = (workflow?.definition ?? {}) as {
    prompt?: string;
    columns?: { name: string; prompt: string }[];
  };
  const rawPrompt = def?.prompt ?? "";
  const vars = useMemo(() => extractVars(rawPrompt), [rawPrompt]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [contractId, setContractId] = useState<string>("");
  const [hydratedFor, setHydratedFor] = useState<string | null>(null);
  // Contracts to optionally run an assistant prompt against.
  const { data: contracts } = useQuery({
    queryKey: ["contracts"],
    queryFn: contractsApi.list,
    enabled: !!workflow && workflow.workflow_type === "assistant",
  });
  // Reset the fill-in form + contract choice whenever a different prompt opens.
  if (workflow && hydratedFor !== workflow.id) {
    setValues({});
    setContractId("");
    setHydratedFor(workflow.id);
  }
  if (!workflow) return null;

  const isAssistant = workflow.workflow_type === "assistant";
  const isTabular = workflow.workflow_type === "tabular_review";
  const columns = def?.columns ?? [];
  const usable = isAssistant || isTabular;
  const filledPrompt = fillVars(rawPrompt, values);
  const allFilled = vars.every((v) => (values[v] ?? "").trim() !== "");
  const contractOptions = (contracts ?? [])
    .filter((c) => !c.archived)
    .slice()
    .sort((a, b) => a.title.localeCompare(b.title));
  const chosenContract = contractOptions.find((c) => c.id === contractId);

  async function copyPrompt() {
    const text = isAssistant
      ? filledPrompt
      : columns.map((c) => `${c.name}: ${c.prompt}`).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      notify("Prompt copied to clipboard", "success");
    } catch {
      notify("Couldn't copy — select the text and copy manually", "error");
    }
  }

  return (
    <Modal
      open={!!workflow}
      onClose={onClose}
      title={workflow.name}
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
          {canEdit && (
            <Button variant="outline" onClick={() => onEdit(workflow)}>
              <Pencil className="h-4 w-4" />
              Edit
            </Button>
          )}
          {usable && (
            <Button variant="outline" onClick={copyPrompt}>
              <Copy className="h-4 w-4" />
              Copy prompt
            </Button>
          )}
          {usable && (
            <Button
              onClick={() =>
                onUse(
                  workflow,
                  isAssistant && (vars.length > 0 || chosenContract)
                    ? filledPrompt
                    : undefined,
                  chosenContract ? contractId : undefined,
                )
              }
              disabled={isAssistant && vars.length > 0 && !allFilled}
            >
              {isAssistant ? (
                <>
                  <Sparkles className="h-4 w-4" />
                  {chosenContract
                    ? `Run on ${chosenContract.title.length > 24 ? chosenContract.title.slice(0, 24) + "…" : chosenContract.title}`
                    : "Use in Assistant"}
                </>
              ) : (
                <>
                  <Table2 className="h-4 w-4" />
                  Start tabular review
                </>
              )}
            </Button>
          )}
        </>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="blue">{titleCase(workflow.workflow_type)}</Badge>
          <Badge tone="violet">
            {VISIBILITY_LABEL[workflow.visibility] ??
              titleCase(workflow.visibility)}
          </Badge>
          {workflow.practice && <Badge tone="slate">{workflow.practice}</Badge>}
        </div>
        {workflow.description && (
          <p className="text-sm text-slate-500">{workflow.description}</p>
        )}
        {usage && usage.run_count > 0 && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500">
            <span className="inline-flex items-center gap-1.5">
              <BarChart3 className="h-3.5 w-3.5 text-slate-400" />
              <span className="font-semibold tabular-nums text-slate-700">
                {usage.run_count}
              </span>{" "}
              run{usage.run_count === 1 ? "" : "s"}
            </span>
            <span>Last used {timeAgo(usage.last_run_at)}</span>
            <span>
              {usage.distinct_users}{" "}
              {usage.distinct_users === 1 ? "person" : "people"}
            </span>
          </div>
        )}

        {isAssistant && vars.length > 0 && (
          <div className="rounded-md border border-brand-200 bg-brand-50 p-3">
            <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-brand-700">
              Fill in {vars.length} variable{vars.length === 1 ? "" : "s"}
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              {vars.map((v) => (
                <Field key={v} label={titleCase(v)}>
                  <Input
                    value={values[v] ?? ""}
                    placeholder={`{{${v}}}`}
                    onChange={(e) =>
                      setValues((prev) => ({ ...prev, [v]: e.target.value }))
                    }
                  />
                </Field>
              ))}
            </div>
          </div>
        )}

        {isAssistant && (
          <Field
            label="Run against a contract"
            hint="Optional — opens that contract alongside the prompt"
          >
            <div className="relative">
              <FileText className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <Select
                value={contractId}
                onChange={(e) => setContractId(e.target.value)}
                className="pl-8"
              >
                <option value="">No contract — just draft in the Assistant</option>
                {contractOptions.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.title}
                    {c.counterparty_name ? ` · ${c.counterparty_name}` : ""}
                  </option>
                ))}
              </Select>
            </div>
          </Field>
        )}

        {isAssistant && (
          <div>
            <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
              {vars.length > 0 ? "Preview" : "Prompt"}
            </p>
            <div className="max-h-[50vh] overflow-y-auto rounded-md border border-slate-200 bg-slate-50 p-4 text-sm">
              {rawPrompt ? (
                <Markdown>{filledPrompt}</Markdown>
              ) : (
                <span className="text-slate-400">No prompt defined.</span>
              )}
            </div>
          </div>
        )}

        {isTabular && (
          <div>
            <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
              {columns.length} column{columns.length === 1 ? "" : "s"}
            </p>
            <div className="max-h-[50vh] space-y-2 overflow-y-auto">
              {columns.length === 0 ? (
                <p className="text-sm text-slate-400">No columns defined.</p>
              ) : (
                columns.map((c, idx) => (
                  <div key={idx} className="rounded-md border border-slate-200 p-3">
                    <p className="text-sm font-medium text-slate-900">{c.name}</p>
                    <p className="mt-1 text-sm text-slate-500">{c.prompt}</p>
                  </div>
                ))
              )}
            </div>
          </div>
        )}

        {!usable && (
          <p className="text-sm text-slate-400">
            This workflow type has no interactive preview yet.
          </p>
        )}
      </div>
    </Modal>
  );
}

function NewWorkflowModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { notify } = useToast();
  const [name, setName] = useState("");
  const [workflowType, setWorkflowType] = useState(WORKFLOW_TYPES[0]);
  const [visibility, setVisibility] = useState(VISIBILITIES[0]);
  const [description, setDescription] = useState("");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const isAssistant = workflowType === "assistant";

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await workflowsApi.create({
        name: name.trim(),
        workflow_type: workflowType,
        visibility,
        description: description.trim() || undefined,
        definition:
          isAssistant && prompt.trim() ? { prompt: prompt.trim() } : undefined,
      });
      setName("");
      setWorkflowType(WORKFLOW_TYPES[0]);
      setVisibility(VISIBILITIES[0]);
      setDescription("");
      setPrompt("");
      onCreated();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Create failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New prompt"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!name.trim()}>
            Create prompt
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name">
          <Input
            autoFocus
            placeholder="e.g. NDA fast-track review"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="Prompt type">
          <Select
            value={workflowType}
            onChange={(e) => setWorkflowType(e.target.value)}
          >
            {WORKFLOW_TYPES.map((t) => (
              <option key={t} value={t}>
                {titleCase(t)}
              </option>
            ))}
          </Select>
        </Field>
        {isAssistant && (
          <Field
            label="Prompt"
            hint="What the Assistant should do. Use {{variables}} for fill-in blanks."
          >
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-xs text-slate-400">
                A strong legal prompt sets role, task, context, output shape, and
                guardrails.
              </span>
              <button
                type="button"
                onClick={() => setPrompt(PROMPT_SCAFFOLD)}
                className="inline-flex shrink-0 items-center gap-1 rounded-md border border-brand-200 bg-brand-50 px-2 py-1 text-xs font-medium text-brand-700 hover:bg-brand-100"
              >
                <Sparkles className="h-3.5 w-3.5" /> Insert 5-part scaffold
              </button>
            </div>
            <Textarea
              rows={10}
              value={prompt}
              placeholder="e.g. Summarize the indemnity and liability provisions of the attached contract…"
              onChange={(e) => setPrompt(e.target.value)}
              className="font-mono text-xs leading-relaxed"
            />
          </Field>
        )}
        <Field label="Visibility">
          <Select
            value={visibility}
            onChange={(e) => setVisibility(e.target.value)}
          >
            {VISIBILITIES.map((v) => (
              <option key={v} value={v}>
                {titleCase(v)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Description" hint="Optional">
          <Textarea
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
      </div>
    </Modal>
  );
}

function EditWorkflowModal({
  workflow,
  onClose,
  onSaved,
}: {
  workflow: Workflow | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify } = useToast();
  const qc = useQueryClient();
  const isAssistant = workflow?.workflow_type === "assistant";

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [prompt, setPrompt] = useState("");
  const [visibility, setVisibility] = useState("private");
  const [sharedIds, setSharedIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [hydratedFor, setHydratedFor] = useState<string | null>(null);

  const { data: versions, refetch: refetchVersions } = useQuery({
    queryKey: ["workflow-versions", workflow?.id],
    queryFn: () => workflowsApi.versions(workflow!.id),
    enabled: !!workflow,
  });
  const { data: orgUsers } = useQuery({
    queryKey: ["org-users"],
    queryFn: () => usersApi.list(),
    enabled: !!workflow,
  });

  // Seed the form from the workflow whenever a different one is opened.
  if (workflow && hydratedFor !== workflow.id) {
    setName(workflow.name);
    setDescription(workflow.description ?? "");
    setPrompt(((workflow.definition as { prompt?: string })?.prompt) ?? "");
    setVisibility(workflow.visibility);
    setSharedIds(workflow.shared_user_ids ?? []);
    setShowHistory(false);
    setHydratedFor(workflow.id);
  }
  if (!workflow) return null;

  function applyWorkflow(w: Workflow) {
    setName(w.name);
    setDescription(w.description ?? "");
    setPrompt(((w.definition as { prompt?: string })?.prompt) ?? "");
    setVisibility(w.visibility);
    setSharedIds(w.shared_user_ids ?? []);
  }

  async function save() {
    if (!name.trim() || !workflow) return;
    setBusy(true);
    try {
      const payload: Parameters<typeof workflowsApi.update>[1] = {
        name: name.trim(),
        description: description.trim() || null,
        visibility,
        shared_user_ids: visibility === "shared_with_users" ? sharedIds : [],
      };
      if (isAssistant) {
        payload.definition = {
          ...(workflow.definition as Record<string, unknown>),
          prompt,
        };
      }
      await workflowsApi.update(workflow.id, payload);
      notify("Prompt updated", "success");
      onSaved();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "error");
    } finally {
      setBusy(false);
    }
  }

  async function restore(v: WorkflowVersion) {
    if (!workflow) return;
    setBusy(true);
    try {
      const updated = await workflowsApi.revert(workflow.id, v.id);
      applyWorkflow(updated);
      notify(`Restored version ${v.version_number}`, "success");
      refetchVersions();
      qc.invalidateQueries({ queryKey: ["workflows"] });
    } catch (e) {
      notify(e instanceof Error ? e.message : "Restore failed", "error");
    } finally {
      setBusy(false);
    }
  }

  const userLabel = (id: string) => {
    const u = orgUsers?.find((x) => x.id === id);
    return u ? u.full_name || u.email : id;
  };
  const shareablePeople = (orgUsers ?? []).filter((u) => u.id !== workflow.created_by_user_id);

  return (
    <Modal
      open={!!workflow}
      onClose={onClose}
      title="Edit prompt"
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} loading={busy} disabled={!name.trim()}>
            Save changes
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Description" hint="Optional">
          <Textarea
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
        {isAssistant && (
          <Field label="Prompt" hint="Use {{variables}} for fill-in blanks.">
            <div className="mb-1.5 flex justify-end">
              <button
                type="button"
                onClick={() => setPrompt(PROMPT_SCAFFOLD)}
                className="inline-flex items-center gap-1 rounded-md border border-brand-200 bg-brand-50 px-2 py-1 text-xs font-medium text-brand-700 hover:bg-brand-100"
              >
                <Sparkles className="h-3.5 w-3.5" /> Insert 5-part scaffold
              </button>
            </div>
            <Textarea
              rows={9}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              className="font-mono text-xs leading-relaxed"
            />
          </Field>
        )}

        <Field label="Who can use this">
          <div className="grid grid-cols-3 gap-2">
            {SHAREABLE_VISIBILITIES.map((v) => {
              const Icon = v.icon;
              const active = visibility === v.value;
              return (
                <button
                  key={v.value}
                  type="button"
                  onClick={() => setVisibility(v.value)}
                  className={cn(
                    "flex flex-col items-center gap-1 rounded-md border px-2 py-2.5 text-center text-xs font-medium transition-colors",
                    active
                      ? "border-brand-500 bg-brand-50 text-brand-700"
                      : "border-slate-200 text-slate-600 hover:bg-slate-50",
                  )}
                >
                  <Icon className="h-4 w-4" />
                  {v.label}
                </button>
              );
            })}
          </div>
        </Field>

        {visibility === "shared_with_users" && (
          <div className="rounded-md border border-slate-200 bg-slate-50 p-2">
            <p className="mb-1.5 px-1 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
              Share with {sharedIds.length > 0 ? `(${sharedIds.length})` : "…"}
            </p>
            <div className="max-h-40 space-y-0.5 overflow-y-auto">
              {shareablePeople.length === 0 ? (
                <p className="px-1 py-2 text-sm text-slate-400">
                  No other people in your organization yet.
                </p>
              ) : (
                shareablePeople.map((u) => {
                  const on = sharedIds.includes(u.id);
                  return (
                    <button
                      key={u.id}
                      type="button"
                      onClick={() =>
                        setSharedIds((prev) =>
                          on ? prev.filter((x) => x !== u.id) : [...prev, u.id],
                        )
                      }
                      className="flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-sm hover:bg-slate-100"
                    >
                      <span className="text-slate-700">
                        {u.full_name || u.email}
                        {u.full_name && (
                          <span className="ml-1.5 text-xs text-slate-400">
                            {u.email}
                          </span>
                        )}
                      </span>
                      <span
                        className={cn(
                          "flex h-4 w-4 items-center justify-center rounded border",
                          on
                            ? "border-brand-500 bg-brand-500 text-white"
                            : "border-slate-300",
                        )}
                      >
                        {on && <Check className="h-3 w-3" />}
                      </span>
                    </button>
                  );
                })
              )}
            </div>
          </div>
        )}

        <div className="border-t border-slate-200 pt-3">
          <button
            type="button"
            onClick={() => setShowHistory((s) => !s)}
            className="flex items-center gap-1.5 text-sm font-medium text-slate-600 hover:text-slate-900"
          >
            <History className="h-4 w-4" />
            Version history
            {versions && versions.length > 0 && (
              <span className="text-slate-400">({versions.length})</span>
            )}
            <ChevronDown
              className={cn(
                "h-4 w-4 text-slate-400 transition-transform",
                showHistory && "rotate-180",
              )}
            />
          </button>
          {showHistory && (
            <div className="mt-2 space-y-1.5">
              {!versions || versions.length === 0 ? (
                <p className="text-sm text-slate-400">
                  No earlier versions yet — the first edit starts the history.
                </p>
              ) : (
                versions.map((v) => (
                  <div
                    key={v.id}
                    className="flex items-center justify-between gap-3 rounded-md border border-slate-200 px-3 py-2"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-slate-800">
                        v{v.version_number} · {v.name}
                      </p>
                      <p className="text-xs text-slate-400">
                        {timeAgo(v.created_at)}
                        {v.note ? ` · ${v.note}` : ""}
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => restore(v)}
                      disabled={busy}
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                      Restore
                    </Button>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
