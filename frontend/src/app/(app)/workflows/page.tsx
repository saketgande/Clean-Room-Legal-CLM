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
} from "lucide-react";
import { workflowsApi } from "@/lib/endpoints";
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
import type { Workflow } from "@/lib/types";

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
  "system_builtin",
];

const HIDDEN_KEY = "aegis_hidden_workflows";

function isBuiltin(w: Workflow) {
  return !!w.is_builtin || w.visibility === "system_builtin";
}

function practiceOf(w: Workflow): string {
  if (w.practice) return w.practice;
  const m = (w.description ?? "").match(/^Built-in workflow · (.+)$/);
  return m ? m[1] : "—";
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
        className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
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
          <div className="absolute right-0 z-20 mt-1 max-h-72 w-56 overflow-y-auto rounded-lg border border-slate-200 bg-white p-1 shadow-pop">
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
  const [newOpen, setNewOpen] = useState(false);
  const [selected, setSelected] = useState<Workflow | null>(null);
  const [reviewWfId, setReviewWfId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["workflows"],
    queryFn: workflowsApi.list,
  });

  const [tab, setTab] = useState<"all" | "builtin" | "custom" | "hidden">(
    "all",
  );
  const [typeF, setTypeF] = useState<string | null>(null);
  const [practiceF, setPracticeF] = useState<string | null>(null);
  const [typeOpen, setTypeOpen] = useState(false);
  const [practiceOpen, setPracticeOpen] = useState(false);
  const [menuId, setMenuId] = useState<string | null>(null);
  const [hidden, setHidden] = useState<string[]>([]);

  useEffect(() => {
    try {
      setHidden(JSON.parse(localStorage.getItem(HIDDEN_KEY) || "[]"));
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
    .filter((w) => !practiceF || practiceOf(w) === practiceF);

  const TABS: { id: typeof tab; label: string }[] = [
    { id: "all", label: "All" },
    { id: "builtin", label: "Built-in" },
    { id: "custom", label: "Custom" },
    { id: "hidden", label: "Hidden" },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title="Workflows"
        description="Reusable automations for assistant, review and drafting tasks."
        actions={
          <Button onClick={() => setNewOpen(true)}>
            <Plus className="h-4 w-4" />
            New workflow
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
        <CenterSpinner label="Loading workflows…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<WorkflowIcon className="h-6 w-6" />}
          title="No workflows here"
          description="Try a different tab or filter, or create a new workflow."
          action={
            <Button onClick={() => setNewOpen(true)}>
              <Plus className="h-4 w-4" />
              New workflow
            </Button>
          }
        />
      ) : (
        <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                <th className="px-5 py-3">Name</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Practice</th>
                <th className="px-4 py-3">Source</th>
                <th className="w-12 px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {rows.map((w) => {
                const asst = w.workflow_type === "assistant";
                const tab2 = w.workflow_type === "tabular_review";
                return (
                  <tr
                    key={w.id}
                    onClick={() => setSelected(w)}
                    className="cursor-pointer border-b border-slate-100 last:border-0 transition-colors hover:bg-slate-50"
                  >
                    <td className="px-5 py-3.5 font-medium text-slate-900">
                      {w.name}
                    </td>
                    <td className="px-4 py-3.5">
                      <span
                        className={cn(
                          "inline-flex items-center gap-1.5 text-xs font-medium",
                          asst
                            ? "text-brand-600"
                            : tab2
                              ? "text-violet-600"
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
                    <td className="px-4 py-3.5 text-slate-600">
                      {practiceOf(w)}
                    </td>
                    <td className="px-4 py-3.5">
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
                      className="relative px-4 py-3.5 text-right"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        onClick={() =>
                          setMenuId(menuId === w.id ? null : w.id)
                        }
                        className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                        aria-label="Workflow actions"
                      >
                        <MoreHorizontal className="h-4 w-4" />
                      </button>
                      {menuId === w.id && (
                        <>
                          <div
                            className="fixed inset-0 z-10"
                            onClick={() => setMenuId(null)}
                          />
                          <div className="absolute right-4 z-20 mt-1 w-40 rounded-lg border border-slate-200 bg-white p-1 shadow-pop">
                            <button
                              onClick={() => {
                                setSelected(w);
                                setMenuId(null);
                              }}
                              className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm text-slate-700 hover:bg-slate-50"
                            >
                              <ArrowRightIcon /> Open
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
          notify("Workflow created", "success");
          setNewOpen(false);
        }}
      />

      <WorkflowDetailModal
        workflow={selected}
        onClose={() => setSelected(null)}
        onUse={(w) => {
          if (w.workflow_type === "assistant") {
            setSelected(null);
            router.push(`/assistant?workflow=${w.id}`);
          } else if (w.workflow_type === "tabular_review") {
            setSelected(null);
            setReviewWfId(w.id);
          }
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
  onClose,
  onUse,
}: {
  workflow: Workflow | null;
  onClose: () => void;
  onUse: (w: Workflow) => void;
}) {
  if (!workflow) return null;
  const def = workflow.definition as {
    prompt?: string;
    columns?: { name: string; prompt: string }[];
  };
  const isAssistant = workflow.workflow_type === "assistant";
  const isTabular = workflow.workflow_type === "tabular_review";
  const columns = def?.columns ?? [];
  const usable = isAssistant || isTabular;

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
          {usable && (
            <Button onClick={() => onUse(workflow)}>
              {isAssistant ? (
                <>
                  <Sparkles className="h-4 w-4" />
                  Use in Assistant
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
          <Badge tone="violet">{titleCase(workflow.visibility)}</Badge>
        </div>
        {workflow.description && (
          <p className="text-sm text-slate-500">{workflow.description}</p>
        )}

        {isAssistant && (
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
              Prompt
            </p>
            <div className="max-h-[50vh] overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm">
              {def?.prompt ? (
                <Markdown>{def.prompt}</Markdown>
              ) : (
                <span className="text-slate-400">No prompt defined.</span>
              )}
            </div>
          </div>
        )}

        {isTabular && (
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
              {columns.length} column{columns.length === 1 ? "" : "s"}
            </p>
            <div className="max-h-[50vh] space-y-2 overflow-y-auto">
              {columns.length === 0 ? (
                <p className="text-sm text-slate-400">
                  No columns defined.
                </p>
              ) : (
                columns.map((c, i) => (
                  <div
                    key={i}
                    className="rounded-lg border border-slate-200 p-3"
                  >
                    <p className="text-sm font-medium text-slate-900">
                      {c.name}
                    </p>
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
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await workflowsApi.create({
        name: name.trim(),
        workflow_type: workflowType,
        visibility,
        description: description.trim() || undefined,
      });
      setName("");
      setWorkflowType(WORKFLOW_TYPES[0]);
      setVisibility(VISIBILITIES[0]);
      setDescription("");
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
      title="New workflow"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!name.trim()}>
            Create workflow
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
        <Field label="Workflow type">
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
