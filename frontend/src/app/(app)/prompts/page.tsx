"use client";

// Prompt Library — catalog of reusable prompts in the new mockup style (scoped `.pmpt`).
// Same behavior as before: variable fill-in, launch/use flow, versioning, hide/show,
// favorites (localStorage), visibility sharing, usage analytics, duplicate-via-scaffold.

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
  ArrowRight,
  X,
} from "lucide-react";
import { contractsApi, usersApi, promptsApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import { CenterSpinner, ErrorState } from "@/components/ui";
import { Markdown } from "@/components/markdown";
import { CreateReviewModal } from "@/components/create-review-modal";
import { titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { Prompt, PromptVersion, PromptUsage } from "@/lib/types";

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
function matchesSearch(w: Prompt, q: string): boolean {
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

function isBuiltin(w: Prompt) {
  return !!w.is_builtin || w.visibility === "system_builtin";
}

function practiceOf(w: Prompt): string {
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
    <div className="fmenu">
      <button type="button" className="fbtn" onClick={() => setOpen(!open)}>
        {value
          ? `${short}: ${options.find((o) => o.value === value)?.label ?? value}`
          : label}
        <ChevronDown className="ic" />
      </button>
      {open && (
        <>
          <div className="fscrim" onClick={() => setOpen(false)} />
          <div className="fpanel">
            <button
              type="button"
              className={`fitem${value === null ? " on" : ""}`}
              onClick={() => {
                onPick(null);
                setOpen(false);
              }}
            >
              All {short.toLowerCase()}
              {value === null && <Check className="ic" />}
            </button>
            {options.map((o) => (
              <button
                key={o.value}
                type="button"
                className={`fitem${value === o.value ? " on" : ""}`}
                onClick={() => {
                  onPick(o.value);
                  setOpen(false);
                }}
              >
                {o.label}
                {value === o.value && <Check className="ic" />}
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
  const [selected, setSelected] = useState<Prompt | null>(null);
  const [editing, setEditing] = useState<Prompt | null>(null);
  const [reviewWfId, setReviewWfId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["workflows"],
    queryFn: promptsApi.list,
  });
  const { data: analytics } = useQuery({
    queryKey: ["workflow-analytics"],
    queryFn: promptsApi.analytics,
  });

  // The current user owns (and may edit) a prompt if it's a custom one they
  // created — built-ins are read-only for everyone.
  const canEdit = (w: Prompt) =>
    !isBuiltin(w) && !!user && w.created_by_user_id === user.id;

  // Fire-and-forget usage ping so the analytics rollup reflects real use.
  function recordLaunch(w: Prompt, mode: string, contractId?: string) {
    promptsApi
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
    <div className="pmpt">
      <style dangerouslySetInnerHTML={{ __html: PMPT_CSS }} />
      <div className="hd">
        <div>
          <h1>Prompt Library</h1>
          <p className="sub">
            Reusable, ready-to-run prompts for review, drafting and analysis —
            organized by practice area.
          </p>
        </div>
        <div className="acts">
          <button className="btn pri" onClick={() => setNewOpen(true)}>
            <Plus className="ic" />
            New prompt
          </button>
        </div>
      </div>

      <div className="toprow">
        <div className="tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`tab${tab === t.id ? " on" : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="toolbar">
          <div className="srch">
            <Search className="sic" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search prompts…"
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
        <div className="empty">
          <div className="eicon">
            <WorkflowIcon className="ic" />
          </div>
          <p className="et">No prompts here</p>
          <p className="ed">
            Try a different tab or filter, or add a new prompt.
          </p>
          <button className="btn pri" onClick={() => setNewOpen(true)}>
            <Plus className="ic" />
            New prompt
          </button>
        </div>
      ) : (
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Practice</th>
                <th>Used</th>
                <th>Source</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((w) => {
                const asst = w.workflow_type === "assistant";
                const tab2 = w.workflow_type === "tabular_review";
                const usage = analytics?.[w.id];
                return (
                  <tr key={w.id} onClick={() => setSelected(w)}>
                    <td>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleFavorite(w.id);
                        }}
                        className={`star${favorites.includes(w.id) ? " on" : ""}`}
                        aria-label={
                          favorites.includes(w.id) ? "Unpin prompt" : "Pin prompt"
                        }
                        title={favorites.includes(w.id) ? "Unpin" : "Pin to top"}
                      >
                        <Star
                          className="ic"
                          fill={favorites.includes(w.id) ? "currentColor" : "none"}
                        />
                      </button>
                      <span className="rowname">{w.name}</span>
                    </td>
                    <td>
                      <span className={`typeicon${asst || tab2 ? " accent" : ""}`}>
                        {asst ? (
                          <MessageSquare className="ic" />
                        ) : tab2 ? (
                          <Table2 className="ic" />
                        ) : (
                          <WorkflowIcon className="ic" />
                        )}
                        {asst
                          ? "Assistant"
                          : tab2
                            ? "Tabular"
                            : titleCase(w.workflow_type)}
                      </span>
                    </td>
                    <td className="dim">{practiceOf(w)}</td>
                    <td>
                      {usage && usage.run_count > 0 ? (
                        <span className="usage" title={`Last used ${timeAgo(usage.last_run_at)}`}>
                          {usage.run_count}×
                        </span>
                      ) : (
                        <span className="dim">—</span>
                      )}
                    </td>
                    <td>
                      <span className="source">
                        {isBuiltin(w) ? (
                          <>
                            <Sparkles className="ic" />
                            Built-in
                          </>
                        ) : (
                          "You"
                        )}
                      </span>
                    </td>
                    <td className="rowacts" onClick={(e) => e.stopPropagation()}>
                      <button
                        onClick={() => setMenuId(menuId === w.id ? null : w.id)}
                        className="kebab"
                        aria-label="Prompt actions"
                      >
                        <MoreHorizontal className="ic" />
                      </button>
                      {menuId === w.id && (
                        <>
                          <div className="fscrim" onClick={() => setMenuId(null)} />
                          <div className="menu">
                            <button
                              className="mitem"
                              onClick={() => {
                                setSelected(w);
                                setMenuId(null);
                              }}
                            >
                              <ArrowRight className="ic" /> Open
                            </button>
                            {canEdit(w) && (
                              <button
                                className="mitem"
                                onClick={() => {
                                  setEditing(w);
                                  setMenuId(null);
                                }}
                              >
                                <Pencil className="ic" /> Edit
                              </button>
                            )}
                            <button
                              className="mitem"
                              onClick={() => {
                                toggleFavorite(w.id);
                                setMenuId(null);
                              }}
                            >
                              <Star
                                className="ic"
                                fill={
                                  favorites.includes(w.id) ? "currentColor" : "none"
                                }
                              />
                              {favorites.includes(w.id) ? "Unpin" : "Pin to top"}
                            </button>
                            <button className="mitem" onClick={() => toggleHide(w.id)}>
                              {hidden.includes(w.id) ? (
                                <>
                                  <Eye className="ic" /> Unhide
                                </>
                              ) : (
                                <>
                                  <EyeOff className="ic" /> Hide
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

function ModalShell({
  open,
  onClose,
  title,
  size = "md",
  footer,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  size?: "md" | "lg";
  footer?: React.ReactNode;
  children: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="ovl" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal${size === "lg" ? " lg" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="mhd">
          <h2>{title}</h2>
          <button className="iconbtn" onClick={onClose} aria-label="Close">
            <X className="ic" />
          </button>
        </div>
        <div className="mbody">{children}</div>
        {footer && <div className="mft">{footer}</div>}
      </div>
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
  workflow: Prompt | null;
  usage?: PromptUsage;
  canEdit: boolean;
  onEdit: (w: Prompt) => void;
  onClose: () => void;
  onUse: (w: Prompt, filledPrompt?: string, contractId?: string) => void;
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
    <ModalShell
      open={!!workflow}
      onClose={onClose}
      title={workflow.name}
      size="lg"
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Close
          </button>
          {canEdit && (
            <button className="btn" onClick={() => onEdit(workflow)}>
              <Pencil className="ic" />
              Edit
            </button>
          )}
          {usable && (
            <button className="btn" onClick={copyPrompt}>
              <Copy className="ic" />
              Copy prompt
            </button>
          )}
          {usable && (
            <button
              className="btn pri"
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
                  <Sparkles className="ic" />
                  {chosenContract
                    ? `Run on ${chosenContract.title.length > 24 ? chosenContract.title.slice(0, 24) + "…" : chosenContract.title}`
                    : "Use in Assistant"}
                </>
              ) : (
                <>
                  <Table2 className="ic" />
                  Start tabular review
                </>
              )}
            </button>
          )}
        </>
      }
    >
      <div className="tagrow">
        <span className="tag blue">{titleCase(workflow.workflow_type)}</span>
        <span className="tag violet">
          {VISIBILITY_LABEL[workflow.visibility] ?? titleCase(workflow.visibility)}
        </span>
        {workflow.practice && <span className="tag slate">{workflow.practice}</span>}
      </div>
      {workflow.description && <p className="desc">{workflow.description}</p>}
      {usage && usage.run_count > 0 && (
        <div className="usagebar">
          <span>
            <BarChart3 className="ic" /> <strong>{usage.run_count}</strong> run
            {usage.run_count === 1 ? "" : "s"}
          </span>
          <span>Last used {timeAgo(usage.last_run_at)}</span>
          <span>
            {usage.distinct_users} {usage.distinct_users === 1 ? "person" : "people"}
          </span>
        </div>
      )}

      {isAssistant && vars.length > 0 && (
        <div className="varbox">
          <p className="vt">
            Fill in {vars.length} variable{vars.length === 1 ? "" : "s"}
          </p>
          <div className="vargrid">
            {vars.map((v) => (
              <div className="field" key={v}>
                <label>{titleCase(v)}</label>
                <input
                  type="text"
                  value={values[v] ?? ""}
                  placeholder={`{{${v}}}`}
                  onChange={(e) =>
                    setValues((prev) => ({ ...prev, [v]: e.target.value }))
                  }
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {isAssistant && (
        <div className="field">
          <label>Run against a contract</label>
          <div className="selwrap">
            <FileText className="selic" />
            <select
              value={contractId}
              onChange={(e) => setContractId(e.target.value)}
            >
              <option value="">No contract — just draft in the Assistant</option>
              {contractOptions.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                  {c.counterparty_name ? ` · ${c.counterparty_name}` : ""}
                </option>
              ))}
            </select>
          </div>
          <span className="fhint">Optional — opens that contract alongside the prompt</span>
        </div>
      )}

      {isAssistant && (
        <div>
          <p className="pretitle">{vars.length > 0 ? "Preview" : "Prompt"}</p>
          <div className="prebox">
            {rawPrompt ? (
              <Markdown>{filledPrompt}</Markdown>
            ) : (
              <span className="noprompt">No prompt defined.</span>
            )}
          </div>
        </div>
      )}

      {isTabular && (
        <div>
          <p className="pretitle">
            {columns.length} column{columns.length === 1 ? "" : "s"}
          </p>
          <div className="colstack">
            {columns.length === 0 ? (
              <p className="noprompt">No columns defined.</p>
            ) : (
              columns.map((c, idx) => (
                <div key={idx} className="colitem">
                  <p className="cn">{c.name}</p>
                  <p className="cp">{c.prompt}</p>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {!usable && <p className="noprompt">This workflow type has no interactive preview yet.</p>}
    </ModalShell>
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
      await promptsApi.create({
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
    <ModalShell
      open={open}
      onClose={onClose}
      title="New prompt"
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn pri" onClick={submit} disabled={busy || !name.trim()}>
            {busy ? "Creating…" : "Create prompt"}
          </button>
        </>
      }
    >
      <div className="field">
        <label>Name</label>
        <input
          type="text"
          autoFocus
          placeholder="e.g. NDA fast-track review"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <div className="field">
        <label>Prompt type</label>
        <select value={workflowType} onChange={(e) => setWorkflowType(e.target.value)}>
          {WORKFLOW_TYPES.map((t) => (
            <option key={t} value={t}>
              {titleCase(t)}
            </option>
          ))}
        </select>
      </div>
      {isAssistant && (
        <div className="field">
          <div className="fieldhead">
            <label>Prompt</label>
            <button type="button" className="scaffbtn" onClick={() => setPrompt(PROMPT_SCAFFOLD)}>
              <Sparkles className="ic" /> Insert 5-part scaffold
            </button>
          </div>
          <span className="fhint" style={{ marginBottom: 4 }}>
            A strong legal prompt sets role, task, context, output shape, and guardrails.
          </span>
          <textarea
            rows={10}
            value={prompt}
            placeholder="e.g. Summarize the indemnity and liability provisions of the attached contract…"
            onChange={(e) => setPrompt(e.target.value)}
            className="mono"
          />
          <span className="fhint">Use {"{{variables}}"} for fill-in blanks.</span>
        </div>
      )}
      <div className="field">
        <label>Visibility</label>
        <select value={visibility} onChange={(e) => setVisibility(e.target.value)}>
          {VISIBILITIES.map((v) => (
            <option key={v} value={v}>
              {titleCase(v)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label>Description</label>
        <textarea
          rows={3}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <span className="fhint">Optional</span>
      </div>
    </ModalShell>
  );
}

function EditWorkflowModal({
  workflow,
  onClose,
  onSaved,
}: {
  workflow: Prompt | null;
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
    queryFn: () => promptsApi.versions(workflow!.id),
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

  function applyWorkflow(w: Prompt) {
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
      const payload: Parameters<typeof promptsApi.update>[1] = {
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
      await promptsApi.update(workflow.id, payload);
      notify("Prompt updated", "success");
      onSaved();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "error");
    } finally {
      setBusy(false);
    }
  }

  async function restore(v: PromptVersion) {
    if (!workflow) return;
    setBusy(true);
    try {
      const updated = await promptsApi.revert(workflow.id, v.id);
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

  const shareablePeople = (orgUsers ?? []).filter((u) => u.id !== workflow.created_by_user_id);

  return (
    <ModalShell
      open={!!workflow}
      onClose={onClose}
      title="Edit prompt"
      size="lg"
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn pri" onClick={save} disabled={busy || !name.trim()}>
            {busy ? "Saving…" : "Save changes"}
          </button>
        </>
      }
    >
      <div className="field">
        <label>Name</label>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <label>Description</label>
        <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        <span className="fhint">Optional</span>
      </div>
      {isAssistant && (
        <div className="field">
          <div className="fieldhead">
            <label>Prompt</label>
            <button type="button" className="scaffbtn" onClick={() => setPrompt(PROMPT_SCAFFOLD)}>
              <Sparkles className="ic" /> Insert 5-part scaffold
            </button>
          </div>
          <textarea
            rows={9}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            className="mono"
          />
          <span className="fhint">Use {"{{variables}}"} for fill-in blanks.</span>
        </div>
      )}

      <div className="field">
        <label>Who can use this</label>
        <div className="seg3">
          {SHAREABLE_VISIBILITIES.map((v) => {
            const Icon = v.icon;
            const active = visibility === v.value;
            return (
              <button
                key={v.value}
                type="button"
                className={active ? "on" : ""}
                onClick={() => setVisibility(v.value)}
              >
                <Icon className="ic" />
                {v.label}
              </button>
            );
          })}
        </div>
      </div>

      {visibility === "shared_with_users" && (
        <div className="sharebox">
          <p className="sharehd">
            Share with {sharedIds.length > 0 ? `(${sharedIds.length})` : "…"}
          </p>
          <div className="sharelist">
            {shareablePeople.length === 0 ? (
              <p className="noprompt" style={{ padding: "8px 4px" }}>
                No other people in your organization yet.
              </p>
            ) : (
              shareablePeople.map((u) => {
                const on = sharedIds.includes(u.id);
                return (
                  <button
                    key={u.id}
                    type="button"
                    className="shareitem"
                    onClick={() =>
                      setSharedIds((prev) =>
                        on ? prev.filter((x) => x !== u.id) : [...prev, u.id],
                      )
                    }
                  >
                    <span>
                      {u.full_name || u.email}
                      {u.full_name && <span className="shareemail">{u.email}</span>}
                    </span>
                    <span className={`chk${on ? " on" : ""}`}>
                      {on && <Check className="ic" style={{ width: 11, height: 11 }} />}
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}

      <div className="histsec">
        <button type="button" className="histtoggle" onClick={() => setShowHistory((s) => !s)}>
          <History className="ic" />
          Version history
          {versions && versions.length > 0 && <span className="dim">({versions.length})</span>}
          <ChevronDown className={`ic chev${showHistory ? " open" : ""}`} />
        </button>
        {showHistory && (
          <div className="histlist">
            {!versions || versions.length === 0 ? (
              <p className="noprompt">No earlier versions yet — the first edit starts the history.</p>
            ) : (
              versions.map((v) => (
                <div key={v.id} className="histrow">
                  <div style={{ minWidth: 0 }}>
                    <p className="histname">
                      v{v.version_number} · {v.name}
                    </p>
                    <p className="histmeta">
                      {timeAgo(v.created_at)}
                      {v.note ? ` · ${v.note}` : ""}
                    </p>
                  </div>
                  <button className="btn sm" onClick={() => restore(v)} disabled={busy}>
                    <RotateCcw className="ic" />
                    Restore
                  </button>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </ModalShell>
  );
}

const PMPT_CSS = `
.pmpt{--surface:#fff;--surface-2:#eef1f6;--inset:#f8fafc;--ink:#18213a;--ink-2:#586178;--ink-3:#8a92a6;--border:#e4e8f0;--border-strong:#ccd3e0;--accent:#3b4aa0;--accent-ink:#fff;--accent-soft:#eaecf8;--good:#2f875f;--good-soft:#e4f1ea;--warn:#a3690a;--warn-soft:#faf1de;--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--pop:0 12px 30px rgba(20,26,40,.16);--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,monospace;padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .pmpt{--surface:#141922;--surface-2:#1b2130;--inset:#10141d;--ink:#e8ebf3;--ink-2:#9aa3b8;--ink-3:#6b7488;--border:#242b39;--border-strong:#333c4e;--accent:#8290e6;--accent-ink:#0c0f16;--accent-soft:#1f2740;--good:#5cbf90;--good-soft:#15271f;--warn:#d3a24e;--warn-soft:#2a2213;--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4);--pop:0 14px 34px rgba(0,0,0,.55)}
.pmpt .ic{width:14px;height:14px;flex:none}
.pmpt .dim{color:var(--ink-3)}
.pmpt .hd{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:14px}
.pmpt .hd h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.015em}
.pmpt .hd .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:640px}
.pmpt .acts{margin-left:auto;display:flex;gap:8px}
.pmpt .btn{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border-radius:9px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;font-size:12.5px;cursor:pointer;font-family:var(--sans)}
.pmpt .btn:hover{background:var(--surface-2)}
.pmpt .btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
.pmpt .btn.pri:hover{filter:brightness(1.06)}
.pmpt .btn.sm{padding:6px 11px;font-size:12px}
.pmpt .btn[disabled]{opacity:.55;pointer-events:none}

.pmpt .toprow{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px 16px;border-bottom:1px solid var(--border);margin-bottom:16px}
.pmpt .tabs{display:flex;gap:6px;flex-wrap:wrap}
.pmpt .tab{display:inline-flex;align-items:center;gap:6px;padding:9px 12px;border:none;background:none;color:var(--ink-2);font:600 12.5px var(--sans);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
.pmpt .tab:hover{color:var(--ink)}
.pmpt .tab.on{color:var(--accent);border-bottom-color:var(--accent)}
.pmpt .toolbar{display:flex;align-items:center;gap:8px;padding-bottom:10px;flex-wrap:wrap}

.pmpt .srch{position:relative;width:210px;max-width:100%}
.pmpt .srch .sic{position:absolute;left:9px;top:50%;transform:translateY(-50%);width:14px;height:14px;color:var(--ink-3);pointer-events:none}
.pmpt .srch input{width:100%;padding:7px 10px 7px 30px;border-radius:8px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-size:12.5px;font-family:var(--sans)}
.pmpt .srch input:focus{outline:2px solid var(--accent-soft);border-color:var(--accent)}

.pmpt .fmenu{position:relative}
.pmpt .fbtn{display:inline-flex;align-items:center;gap:6px;padding:7px 11px;border-radius:8px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink-2);font-size:12px;font-weight:600;cursor:pointer;font-family:var(--sans)}
.pmpt .fbtn:hover{background:var(--surface-2)}
.pmpt .fscrim{position:fixed;inset:0;z-index:10}
.pmpt .fpanel{position:absolute;right:0;z-index:20;margin-top:4px;width:210px;max-height:280px;overflow-y:auto;border:1px solid var(--border);border-radius:10px;background:var(--surface);box-shadow:var(--pop);padding:4px}
.pmpt .fitem{display:flex;width:100%;align-items:center;justify-content:space-between;border:0;background:none;border-radius:7px;padding:7px 9px;text-align:left;font-size:12.5px;color:var(--ink-2);cursor:pointer;font-family:var(--sans)}
.pmpt .fitem:hover{background:var(--inset)}
.pmpt .fitem.on{color:var(--accent);font-weight:600}

.pmpt .tablewrap{overflow-x:auto}
.pmpt table{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px;min-width:760px}
.pmpt thead th{text-align:left;font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);padding:9px 12px;border-bottom:1px solid var(--border);white-space:nowrap}
.pmpt tbody td{padding:9px 12px;border-bottom:1px solid var(--border);vertical-align:middle}
.pmpt tbody tr{cursor:pointer}
.pmpt tbody tr:last-child td{border-bottom:0}
.pmpt tbody tr:hover td{background:var(--inset)}
.pmpt .star{display:inline-flex;vertical-align:middle;background:none;border:0;padding:2px;margin:-2px 4px -2px -4px;cursor:pointer;color:var(--ink-3)}
.pmpt .star:hover{color:var(--ink-2)}
.pmpt .star.on{color:var(--warn)}
.pmpt .rowname{font-weight:560;color:var(--ink);vertical-align:middle}
.pmpt .typeicon{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;color:var(--ink-2)}
.pmpt .typeicon.accent{color:var(--accent)}
.pmpt .usage{font-variant-numeric:tabular-nums;color:var(--ink-2)}
.pmpt .source{display:inline-flex;align-items:center;gap:5px;color:var(--ink-2)}
.pmpt .source .ic{color:var(--accent)}

.pmpt .rowacts{position:relative;text-align:right}
.pmpt .kebab{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:7px;border:0;background:none;color:var(--ink-3);cursor:pointer}
.pmpt .kebab:hover{background:var(--surface-2);color:var(--ink)}
.pmpt .menu{position:absolute;right:8px;z-index:20;margin-top:2px;width:160px;border:1px solid var(--border);border-radius:10px;background:var(--surface);box-shadow:var(--pop);padding:4px}
.pmpt .mitem{display:flex;width:100%;align-items:center;gap:8px;border:0;background:none;border-radius:7px;padding:7px 9px;text-align:left;font-size:12.5px;color:var(--ink);cursor:pointer;font-family:var(--sans)}
.pmpt .mitem:hover{background:var(--inset)}
.pmpt .mitem .ic{color:var(--ink-3)}

.pmpt .empty{border:1px dashed var(--border-strong);border-radius:12px;background:var(--inset);padding:36px 24px;text-align:center;display:flex;flex-direction:column;align-items:center;gap:6px}
.pmpt .eicon{width:44px;height:44px;border-radius:11px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;margin-bottom:4px}
.pmpt .eicon .ic{width:20px;height:20px}
.pmpt .et{margin:0;font-size:14px;font-weight:660;color:var(--ink)}
.pmpt .ed{margin:0 0 6px;font-size:12.5px;color:var(--ink-2);max-width:380px}

.pmpt .tag{display:inline-flex;align-items:center;font:600 10.5px var(--sans);letter-spacing:.02em;padding:3px 9px;border-radius:99px}
.pmpt .tag.blue,.pmpt .tag.violet{background:var(--accent-soft);color:var(--accent)}
.pmpt .tag.slate{background:var(--surface-2);color:var(--ink-2)}
.pmpt .tagrow{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.pmpt .desc{margin:0;font-size:13px;color:var(--ink-2)}

.pmpt .ovl{position:fixed;inset:0;background:rgba(12,15,22,.5);display:grid;place-items:center;z-index:50;padding:24px}
.pmpt .modal{width:100%;max-width:460px;max-height:min(88vh,720px);display:flex;flex-direction:column;background:var(--surface);border:1px solid var(--border);border-radius:14px;box-shadow:var(--pop)}
.pmpt .modal.lg{max-width:640px}
.pmpt .mhd{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--border)}
.pmpt .mhd h2{margin:0;font-size:15px;font-weight:660}
.pmpt .iconbtn{width:28px;height:28px;border-radius:8px;display:grid;place-items:center;color:var(--ink-2);background:none;border:0;cursor:pointer}
.pmpt .iconbtn:hover{background:var(--surface-2)}
.pmpt .mbody{padding:16px 18px;overflow:auto;display:flex;flex-direction:column;gap:14px}
.pmpt .mft{display:flex;justify-content:flex-end;gap:8px;padding:14px 18px;border-top:1px solid var(--border);flex-wrap:wrap}

.pmpt .field{display:flex;flex-direction:column;gap:5px}
.pmpt .field label{font:600 10px var(--sans);letter-spacing:.05em;text-transform:uppercase;color:var(--ink-3)}
.pmpt .field input[type=text],.pmpt .field select,.pmpt .field textarea{border:1px solid var(--border-strong);border-radius:8px;padding:8px 10px;background:var(--surface);color:var(--ink);font-size:12.5px;font-family:var(--sans)}
.pmpt .field textarea{resize:vertical;line-height:1.5}
.pmpt .field textarea.mono{font-family:var(--mono)}
.pmpt .field input:focus,.pmpt .field select:focus,.pmpt .field textarea:focus{outline:none;border-color:var(--accent)}
.pmpt .field .fhint{font-size:11px;color:var(--ink-3)}
.pmpt .fieldhead{display:flex;align-items:center;justify-content:space-between;gap:8px}
.pmpt .scaffbtn{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--accent-soft);background:var(--accent-soft);color:var(--accent);border-radius:8px;padding:5px 9px;font-size:11.5px;font-weight:600;cursor:pointer;flex:none}
.pmpt .scaffbtn:hover{filter:brightness(0.97)}
.pmpt .selwrap{position:relative}
.pmpt .selwrap .selic{position:absolute;left:9px;top:50%;transform:translateY(-50%);width:14px;height:14px;color:var(--ink-3);pointer-events:none}
.pmpt .selwrap select{width:100%;padding-left:30px}

.pmpt .varbox{border:1px solid var(--accent-soft);background:var(--accent-soft);border-radius:10px;padding:11px}
.pmpt .varbox .vt{margin:0 0 8px;font:600 10.5px var(--sans);letter-spacing:.05em;text-transform:uppercase;color:var(--accent)}
.pmpt .vargrid{display:grid;gap:8px}
@media(min-width:520px){.pmpt .vargrid{grid-template-columns:1fr 1fr}}

.pmpt .pretitle{margin:0 0 6px;font:600 10.5px var(--sans);letter-spacing:.05em;text-transform:uppercase;color:var(--ink-3)}
.pmpt .prebox{max-height:50vh;overflow-y:auto;border:1px solid var(--border);border-radius:10px;background:var(--inset);padding:14px;font-size:13px}
.pmpt .noprompt{color:var(--ink-3);font-size:13px}
.pmpt .colstack{max-height:50vh;overflow-y:auto;display:flex;flex-direction:column;gap:8px}
.pmpt .colitem{border:1px solid var(--border);border-radius:9px;padding:9px 11px}
.pmpt .colitem .cn{margin:0;font-size:12.5px;font-weight:660;color:var(--ink)}
.pmpt .colitem .cp{margin:4px 0 0;font-size:12.5px;color:var(--ink-2)}

.pmpt .usagebar{display:flex;flex-wrap:wrap;align-items:center;gap:14px;border:1px solid var(--border);border-radius:9px;background:var(--inset);padding:9px 12px;font-size:12px;color:var(--ink-2)}
.pmpt .usagebar span{display:inline-flex;align-items:center;gap:5px}
.pmpt .usagebar strong{color:var(--ink);font-variant-numeric:tabular-nums}

.pmpt .seg3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.pmpt .seg3 button{display:flex;flex-direction:column;align-items:center;gap:4px;border:1px solid var(--border-strong);border-radius:9px;padding:10px 8px;font-size:11.5px;font-weight:600;color:var(--ink-2);background:var(--surface);cursor:pointer;font-family:var(--sans)}
.pmpt .seg3 button.on{border-color:var(--accent);background:var(--accent-soft);color:var(--accent)}

.pmpt .sharebox{border:1px solid var(--border);border-radius:9px;background:var(--inset);padding:8px}
.pmpt .sharehd{margin:0 0 6px;padding:0 3px;font:600 10px var(--sans);letter-spacing:.05em;text-transform:uppercase;color:var(--ink-3)}
.pmpt .sharelist{max-height:160px;overflow-y:auto;display:flex;flex-direction:column;gap:2px}
.pmpt .shareitem{display:flex;width:100%;align-items:center;justify-content:space-between;border:0;background:none;border-radius:7px;padding:6px 8px;text-align:left;font-size:12.5px;cursor:pointer;color:var(--ink);font-family:var(--sans)}
.pmpt .shareitem:hover{background:var(--surface-2)}
.pmpt .shareemail{margin-left:6px;font-size:11px;color:var(--ink-3)}
.pmpt .chk{display:flex;height:16px;width:16px;flex:none;align-items:center;justify-content:center;border-radius:5px;border:1px solid var(--border-strong)}
.pmpt .chk.on{border-color:var(--accent);background:var(--accent);color:var(--accent-ink)}

.pmpt .histsec{border-top:1px solid var(--border);padding-top:12px}
.pmpt .histtoggle{display:inline-flex;align-items:center;gap:6px;border:0;background:none;padding:0;font-size:13px;font-weight:600;color:var(--ink-2);cursor:pointer;font-family:var(--sans)}
.pmpt .histtoggle:hover{color:var(--ink)}
.pmpt .histtoggle .chev{transition:transform .15s;color:var(--ink-3)}
.pmpt .histtoggle .chev.open{transform:rotate(180deg)}
.pmpt .histlist{margin-top:8px;display:flex;flex-direction:column;gap:6px}
.pmpt .histrow{display:flex;align-items:center;justify-content:space-between;gap:10px;border:1px solid var(--border);border-radius:9px;padding:8px 10px}
.pmpt .histname{margin:0;font-size:12.5px;font-weight:600;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pmpt .histmeta{margin:0;font-size:11px;color:var(--ink-3)}
`;
