"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, FileText, Wand2, Upload } from "lucide-react";
import {
  contractsApi,
  projectsApi,
  tabularApi,
  workflowsApi,
} from "@/lib/endpoints";
import { Button, Field, Input, Modal, Select } from "@/components/ui";
import { useToast } from "@/components/toast";
import { ImportContractModal } from "@/components/import-contract-modal";

type Column = { name: string; prompt: string };

/**
 * Shared review-creation modal. Used standalone from /tabular-reviews and from
 * a project's Reviews tab (with the project pre-selected & locked) so a review
 * is always linked to its project + optionally seeded from a workflow template.
 */
export function CreateReviewModal({
  open,
  onClose,
  onCreated,
  defaultProjectId,
  defaultWorkflowId,
  lockProject = false,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
  defaultProjectId?: string;
  defaultWorkflowId?: string;
  lockProject?: boolean;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [name, setName] = useState("");
  const [projectId, setProjectId] = useState(defaultProjectId ?? "");
  const [workflowId, setWorkflowId] = useState("");
  const [contractIds, setContractIds] = useState<string[]>([]);
  const [importOpen, setImportOpen] = useState(false);
  const [columns, setColumns] = useState<Column[]>([{ name: "", prompt: "" }]);
  const [busy, setBusy] = useState(false);

  const { data: projects } = useQuery({
    queryKey: ["projects"],
    queryFn: projectsApi.list,
    enabled: open,
  });
  const { data: contracts } = useQuery({
    queryKey: ["contracts"],
    queryFn: contractsApi.list,
    enabled: open,
  });
  const { data: projectContracts } = useQuery({
    queryKey: ["project", projectId, "contracts"],
    queryFn: () => projectsApi.contracts(projectId),
    enabled: open && !!projectId,
  });
  const { data: workflows } = useQuery({
    queryKey: ["workflows"],
    queryFn: workflowsApi.list,
    enabled: open,
  });

  const tabularWorkflows = (workflows ?? []).filter(
    (w) => w.workflow_type === "tabular_review",
  );

  useEffect(() => {
    if (open) setProjectId(defaultProjectId ?? "");
  }, [open, defaultProjectId]);

  // Pre-select & seed columns from a workflow template when launched
  // from the Workflows page "Use workflow" action.
  useEffect(() => {
    if (open && defaultWorkflowId && workflows) {
      applyWorkflow(defaultWorkflowId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, defaultWorkflowId, workflows]);

  // When a project is chosen, pre-select that project's contracts.
  useEffect(() => {
    if (projectId && projectContracts) {
      setContractIds(projectContracts.map((pc) => pc.contract_id));
    }
  }, [projectId, projectContracts]);

  function reset() {
    setName("");
    setProjectId(defaultProjectId ?? "");
    setWorkflowId("");
    setContractIds([]);
    setColumns([{ name: "", prompt: "" }]);
  }

  function applyWorkflow(id: string) {
    setWorkflowId(id);
    const wf = tabularWorkflows.find((w) => w.id === id);
    const cols = (wf?.definition as { columns?: Column[] } | undefined)
      ?.columns;
    if (cols && cols.length) {
      setColumns(cols.map((c) => ({ name: c.name, prompt: c.prompt })));
      if (!name.trim() && wf) setName(wf.name);
    }
  }

  function toggleContract(id: string) {
    setContractIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  }

  function setColumn(idx: number, patch: Partial<Column>) {
    setColumns((cols) =>
      cols.map((c, i) => (i === idx ? { ...c, ...patch } : c)),
    );
  }

  const validColumns = columns.filter((c) => c.name.trim() && c.prompt.trim());
  const canSubmit =
    name.trim().length > 0 &&
    contractIds.length > 0 &&
    validColumns.length > 0;

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    try {
      const review = await tabularApi.create({
        name: name.trim(),
        project_id: projectId || undefined,
        contract_ids: contractIds,
        columns: validColumns.map((c) => ({
          name: c.name.trim(),
          prompt: c.prompt.trim(),
        })),
      });
      qc.invalidateQueries({ queryKey: ["tabular-reviews"] });
      notify("Review created", "success");
      reset();
      onClose();
      onCreated(review.id);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Create failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
    <Modal
      open={open}
      onClose={onClose}
      title="New tabular review"
      size="lg"
      footer={
        <>
          {!canSubmit && (
            <span className="mr-auto text-xs text-slate-400">
              {!name.trim()
                ? "Add a review name"
                : contractIds.length === 0
                  ? "Select at least one contract"
                  : "Add at least one column with a name and prompt"}{" "}
              to continue
            </span>
          )}
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!canSubmit}>
            Create review
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <Field label="Name">
          <Input
            placeholder="e.g. Q2 vendor MSA audit"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>

        <Field
          label="Workflow template"
          hint="Optional — seeds the columns from a reusable tabular workflow"
        >
          <Select
            value={workflowId}
            onChange={(e) => applyWorkflow(e.target.value)}
          >
            <option value="">No template — define columns manually</option>
            {tabularWorkflows.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </Select>
          {workflowId && (
            <p className="mt-1 flex items-center gap-1.5 text-xs text-brand-600">
              <Wand2 className="h-3.5 w-3.5" />
              Columns seeded from “
              {tabularWorkflows.find((w) => w.id === workflowId)?.name}”
            </p>
          )}
        </Field>

        <Field label="Project" hint={lockProject ? undefined : "Optional"}>
          <Select
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            disabled={lockProject}
          >
            <option value="">No project</option>
            {(projects ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
        </Field>

        <Field
          label={`Contracts (${contractIds.length} selected)`}
          hint={
            projectId
              ? "Pre-filled from the project — adjust if needed"
              : "Pick the contracts to run every column against"
          }
        >
          <button
            type="button"
            onClick={() => setImportOpen(true)}
            className="mb-2 inline-flex items-center gap-1.5 text-sm font-medium text-brand-600 hover:text-brand-700"
          >
            <Upload className="h-3.5 w-3.5" />
            Import a new file
          </button>
          <div className="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-slate-200 p-2">
            {(contracts ?? []).length === 0 ? (
              <p className="px-2 py-3 text-sm text-slate-400">
                No contracts available.
              </p>
            ) : (
              (contracts ?? []).map((c) => (
                <label
                  key={c.id}
                  className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
                >
                  <input
                    type="checkbox"
                    checked={contractIds.includes(c.id)}
                    onChange={() => toggleContract(c.id)}
                  />
                  <span className="flex-1 truncate">{c.title}</span>
                  {c.counterparty_name && (
                    <span className="shrink-0 text-xs text-slate-400">
                      {c.counterparty_name}
                    </span>
                  )}
                </label>
              ))
            )}
          </div>
        </Field>

        <Field
          label="Columns"
          hint="Each column is a question asked per contract"
        >
          <div className="space-y-3">
            {columns.map((col, idx) => (
              <div
                key={idx}
                className="space-y-2 rounded-lg border border-slate-200 p-3"
              >
                <div className="flex items-center gap-2">
                  <Input
                    placeholder="Column name (e.g. Governing law)"
                    value={col.name}
                    onChange={(e) => setColumn(idx, { name: e.target.value })}
                  />
                  <Button
                    size="icon"
                    variant="ghost"
                    disabled={columns.length === 1}
                    onClick={() =>
                      setColumns((cols) => cols.filter((_, i) => i !== idx))
                    }
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
                <Input
                  placeholder="Prompt (e.g. What is the governing law clause?)"
                  value={col.prompt}
                  onChange={(e) => setColumn(idx, { prompt: e.target.value })}
                />
              </div>
            ))}
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                setColumns((cols) => [...cols, { name: "", prompt: "" }])
              }
            >
              <Plus className="h-3.5 w-3.5" />
              Add column
            </Button>
          </div>
        </Field>

        {!canSubmit && (
          <p className="flex items-center gap-1.5 text-xs text-slate-400">
            <FileText className="h-3.5 w-3.5" />
            Provide a name, at least one contract and one complete column.
          </p>
        )}
      </div>
    </Modal>
    <ImportContractModal
      open={importOpen}
      onClose={() => setImportOpen(false)}
      defaultProjectId={projectId || undefined}
      onUploaded={(c) => {
        qc.invalidateQueries({ queryKey: ["contracts"] });
        setContractIds((prev) =>
          prev.includes(c.id) ? prev : [...prev, c.id],
        );
      }}
    />
    </>
  );
}
