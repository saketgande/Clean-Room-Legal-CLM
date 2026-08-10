"use client";

import { use, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Send, Play, Pencil, Trash2 } from "lucide-react";
import { contractsApi, playbooksApi } from "@/lib/endpoints";
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
  Breadcrumbs,
  Select,
  Tabs,
  Table,
  TD,
  TH,
  THead,
  TR,
  Textarea,
} from "@/components/ui";
import { cn, fmtDateTime, riskTone, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type {
  PlaybookInsights,
  PlaybookRecommendation,
  PlaybookRunDetailResponse,
  PlaybookVersionResponse,
} from "@/lib/types";

export default function PlaybookDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const qc = useQueryClient();
  const { notify } = useToast();
  const [tab, setTab] = useState("versions");
  const [publishing, setPublishing] = useState(false);

  const {
    data: playbook,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["playbook", id],
    queryFn: () => playbooksApi.get(id),
  });

  async function publish() {
    setPublishing(true);
    try {
      await playbooksApi.publish(id);
      qc.invalidateQueries({ queryKey: ["playbook", id] });
      qc.invalidateQueries({ queryKey: ["playbook", id, "versions"] });
      qc.invalidateQueries({ queryKey: ["playbooks"] });
      notify("Playbook published", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Publish failed", "error");
    } finally {
      setPublishing(false);
    }
  }

  if (isLoading) return <CenterSpinner label="Loading playbook…" />;
  if (error) return <ErrorState error={error} />;
  if (!playbook) return null;

  return (
    <div className="space-y-4">
      <div>
        <Breadcrumbs
          items={[
            { label: "Playbooks", href: "/playbooks" },
            { label: playbook.name },
          ]}
        />
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-[20px] font-semibold leading-tight tracking-[-0.01em] text-slate-900">
              {playbook.name}
            </h1>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Badge tone={statusTone(playbook.status)}>
                {titleCase(playbook.status)}
              </Badge>
              {playbook.description && (
                <span className="text-sm text-slate-500">
                  {playbook.description}
                </span>
              )}
            </div>
          </div>
          <Button
            variant="outline"
            onClick={publish}
            loading={publishing}
            disabled={playbook.status === "published"}
          >
            <Send className="h-4 w-4" />
            Publish
          </Button>
        </div>
      </div>

      <Tabs
        active={tab}
        onChange={setTab}
        tabs={[
          { id: "versions", label: "Versions" },
          { id: "rules", label: "Rules" },
          { id: "runs", label: "Runs" },
          { id: "insights", label: "AI insights" },
        ]}
      />

      {tab === "versions" && <VersionsTab playbookId={id} />}
      {tab === "rules" && <RulesTab playbookId={id} />}
      {tab === "runs" && <RunsTab playbookId={id} />}
      {tab === "insights" && <InsightsTab playbookId={id} />}
    </div>
  );
}

// ---- Versions ------------------------------------------------------------
function VersionsTab({ playbookId }: { playbookId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["playbook", playbookId, "versions"],
    queryFn: () => playbooksApi.versions(playbookId),
  });

  async function newVersion() {
    setBusy(true);
    try {
      await playbooksApi.createVersion(playbookId);
      qc.invalidateQueries({ queryKey: ["playbook", playbookId, "versions"] });
      qc.invalidateQueries({ queryKey: ["playbook", playbookId] });
      notify("Version created", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed", "error");
    } finally {
      setBusy(false);
    }
  }

  if (isLoading) return <CenterSpinner />;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Versions</CardTitle>
        <Button size="sm" onClick={newVersion} loading={busy}>
          <Plus className="h-3.5 w-3.5" />
          New version
        </Button>
      </CardHeader>
      <Table>
        <THead>
          <tr>
            <TH>Version</TH>
            <TH>Status</TH>
            <TH>Summary</TH>
          </tr>
        </THead>
        <tbody>
          {(data ?? []).map((v) => (
            <TR key={v.id}>
              <TD className="font-medium text-slate-900">
                V{v.version_number}
              </TD>
              <TD>
                <Badge tone={statusTone(v.status)}>
                  {titleCase(v.status)}
                </Badge>
              </TD>
              <TD className="max-w-md truncate">{v.summary ?? "—"}</TD>
            </TR>
          ))}
          {data?.length === 0 && (
            <TR>
              <TD className="py-8 text-center text-slate-400" colSpan={3}>
                No versions yet.
              </TD>
            </TR>
          )}
        </tbody>
      </Table>
    </Card>
  );
}

// ---- Rules ---------------------------------------------------------------
const CLAUSE_TYPES = [
  "amendment", "assignment", "audit_rights", "confidentiality", "counterparts",
  "data_protection", "dispute_resolution", "entire_agreement", "exclusivity",
  "force_majeure", "governing_law", "indemnification", "insurance", "ip_ownership",
  "license_grant", "limitation_of_liability", "non_compete", "non_solicitation",
  "notices", "payment_terms", "remedies", "renewal", "representations_and_warranties",
  "service_levels", "severability", "term_and_termination", "waiver", "warranty",
];
const RULE_TYPES = ["position", "prohibition", "requirement", "fallback", "approval_gate", "preference"];

type PlaybookRule = Awaited<ReturnType<typeof playbooksApi.rules>>[number];

function RulesTab({ playbookId }: { playbookId: string }) {
  const { data: versions } = useQuery({
    queryKey: ["playbook", playbookId, "versions"],
    queryFn: () => playbooksApi.versions(playbookId),
  });
  const [versionId, setVersionId] = useState("");
  const [modal, setModal] = useState<{ mode: "add" } | { mode: "edit"; rule: PlaybookRule } | null>(null);

  useEffect(() => {
    if (!versionId && versions && versions.length > 0) {
      // Default to the newest draft if there is one, else the first version.
      const draft = versions.find((v) => v.status === "draft");
      setVersionId((draft ?? versions[0]).id);
    }
  }, [versions, versionId]);

  const selectedVersion = versions?.find((v) => v.id === versionId);
  const editable = selectedVersion?.status === "draft";

  const { data: rules, isLoading } = useQuery({
    queryKey: ["playbook", playbookId, "rules", versionId],
    queryFn: () => playbooksApi.rules(playbookId, versionId),
    enabled: !!versionId,
  });

  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Editing version" className="w-64">
              <Select value={versionId} onChange={(e) => setVersionId(e.target.value)}>
                {(versions ?? []).length === 0 && <option value="">No versions</option>}
                {(versions ?? []).map((v) => (
                  <option key={v.id} value={v.id}>
                    V{v.version_number} — {titleCase(v.status)}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="pb-2 text-sm text-slate-500">
              {rules ? `${rules.length} rule${rules.length === 1 ? "" : "s"}` : ""}
            </div>
          </div>
          {editable ? (
            <Button size="sm" onClick={() => setModal({ mode: "add" })}>
              <Plus className="h-3.5 w-3.5" />
              Add rule
            </Button>
          ) : (
            <span className="pb-2 text-xs text-slate-400">
              This version is {titleCase(selectedVersion?.status ?? "")} — create a new draft to edit rules.
            </span>
          )}
        </CardBody>
      </Card>

      {!versionId ? (
        <Card>
          <CardBody className="py-12 text-center text-sm text-slate-400">
            Create a version to define rules.
          </CardBody>
        </Card>
      ) : isLoading ? (
        <CenterSpinner />
      ) : (rules ?? []).length === 0 ? (
        <Card>
          <CardBody className="flex flex-col items-center gap-3 py-12 text-center">
            <p className="text-sm text-slate-400">No rules in this version yet.</p>
            {editable && (
              <Button size="sm" onClick={() => setModal({ mode: "add" })}>
                <Plus className="h-3.5 w-3.5" />
                Add your first rule
              </Button>
            )}
          </CardBody>
        </Card>
      ) : (
        <div className="space-y-3">
          {(rules ?? []).map((r) => (
            <RuleCard
              key={r.id}
              rule={r}
              playbookId={playbookId}
              versionId={versionId}
              editable={editable}
              onEdit={() => setModal({ mode: "edit", rule: r })}
            />
          ))}
        </div>
      )}

      {modal && versionId && (
        <RuleModal
          playbookId={playbookId}
          versionId={versionId}
          rule={modal.mode === "edit" ? modal.rule : undefined}
          onClose={() => setModal(null)}
        />
      )}
    </div>
  );
}

function RuleCard({
  rule: r,
  playbookId,
  versionId,
  editable,
  onEdit,
}: {
  rule: PlaybookRule;
  playbookId: string;
  versionId: string;
  editable: boolean;
  onEdit: () => void;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  async function del() {
    setDeleting(true);
    try {
      await playbooksApi.deleteRule(playbookId, versionId, r.id);
      qc.invalidateQueries({ queryKey: ["playbook", playbookId, "rules", versionId] });
      notify("Rule deleted", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Delete failed", "error");
    } finally {
      setDeleting(false);
      setConfirming(false);
    }
  }

  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-slate-900">
              {titleCase(r.clause_type)}
            </span>
            <Badge tone="slate">{titleCase(r.rule_type)}</Badge>
            <Badge tone={riskTone(r.risk_level)}>{titleCase(r.risk_level)} risk</Badge>
            {r.approval_required && <Badge tone="amber">Approval required</Badge>}
          </div>
          {editable && (
            <div className="flex items-center gap-2">
              {confirming ? (
                <>
                  <span className="text-xs text-slate-500">Delete this rule?</span>
                  <Button size="sm" variant="danger" loading={deleting} onClick={del}>
                    Confirm
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setConfirming(false)}>
                    Cancel
                  </Button>
                </>
              ) : (
                <>
                  <Button size="sm" variant="outline" onClick={onEdit}>
                    <Pencil className="h-3.5 w-3.5" />
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setConfirming(true)}
                    aria-label="Delete rule"
                  >
                    <Trash2 className="h-3.5 w-3.5 text-slate-400" />
                  </Button>
                </>
              )}
            </div>
          )}
        </div>

        {(r.preferred_position || r.fallback_position) && (
          <div className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
            {r.preferred_position && (
              <Detail label="Preferred position" value={r.preferred_position} />
            )}
            {r.fallback_position && (
              <Detail label="Fallback position" value={r.fallback_position} />
            )}
          </div>
        )}

        {(r.prohibited_language || r.required_language) && (
          <div className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
            {r.prohibited_language && (
              <Detail label="Prohibited language" value={r.prohibited_language} tone="red" />
            )}
            {r.required_language && (
              <Detail label="Required language" value={r.required_language} tone="green" />
            )}
          </div>
        )}

        {(r.escalation_role || r.rationale || r.negotiation_guidance) && (
          <div className="space-y-3 border-t border-slate-200 pt-3">
            {r.escalation_role && <Detail label="Escalation role" value={r.escalation_role} />}
            {r.rationale && <Detail label="Rationale" value={r.rationale} />}
            {r.negotiation_guidance && (
              <Detail label="Negotiation guidance" value={r.negotiation_guidance} />
            )}
          </div>
        )}

        {r.sample_clause && (
          <div>
            <p className="text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
              Sample clause
            </p>
            <p className="mt-1 whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-[13px] text-slate-600">
              {r.sample_clause}
            </p>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function Detail({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | null | undefined;
  tone?: "red" | "green";
}) {
  return (
    <div>
      <dt className="text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">{label}</dt>
      <dd
        className={cn(
          "mt-1 text-[13px]",
          tone === "red"
            ? "text-danger"
            : tone === "green"
              ? "text-success"
              : "text-slate-700",
        )}
      >
        {value || "\u2014"}
      </dd>
    </div>
  );
}

function RuleModal({
  playbookId,
  versionId,
  rule,
  onClose,
}: {
  playbookId: string;
  versionId: string;
  rule?: PlaybookRule;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);
  const isEdit = !!rule;
  const [form, setForm] = useState({
    clause_type: rule?.clause_type ?? "",
    rule_type: rule?.rule_type ?? "position",
    preferred_position: rule?.preferred_position ?? "",
    fallback_position: rule?.fallback_position ?? "",
    prohibited_language: rule?.prohibited_language ?? "",
    required_language: rule?.required_language ?? "",
    risk_level: (rule?.risk_level ?? "medium") as string,
    rationale: rule?.rationale ?? "",
    escalation_role: rule?.escalation_role ?? "",
    approval_required: rule?.approval_required ?? false,
    sample_clause: rule?.sample_clause ?? "",
    negotiation_guidance: rule?.negotiation_guidance ?? "",
  });

  function set<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  const valid = form.clause_type.trim() && form.rule_type.trim();

  async function submit() {
    if (!valid) return;
    setBusy(true);
    try {
      const OPTIONAL = [
        "preferred_position", "fallback_position", "prohibited_language",
        "required_language", "rationale", "escalation_role", "sample_clause",
        "negotiation_guidance",
      ] as const;
      const payload: Record<string, unknown> = {
        clause_type: form.clause_type.trim(),
        rule_type: form.rule_type.trim(),
        risk_level: form.risk_level,
        approval_required: form.approval_required,
      };
      for (const k of OPTIONAL) {
        const v = (form[k] as string).trim();
        // On edit, send empty string to clear a field; on add, omit blanks.
        if (v || isEdit) payload[k] = v || null;
      }
      if (isEdit && rule) {
        await playbooksApi.updateRule(playbookId, versionId, rule.id, payload);
      } else {
        await playbooksApi.createRule(playbookId, versionId, payload);
      }
      qc.invalidateQueries({ queryKey: ["playbook", playbookId, "rules", versionId] });
      notify(isEdit ? "Rule updated" : "Rule added", "success");
      onClose();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={isEdit ? "Edit rule" : "Add rule"}
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!valid}>
            {isEdit ? "Save changes" : "Add rule"}
          </Button>
        </>
      }
    >
      <datalist id="pb-clause-types">
        {CLAUSE_TYPES.map((c) => (
          <option key={c} value={c}>
            {titleCase(c)}
          </option>
        ))}
      </datalist>
      <datalist id="pb-rule-types">
        {RULE_TYPES.map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>

      <div className="space-y-5">
        <FormSection title="What this rule covers">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Clause type" hint="Pick a standard type or type your own">
              <Input
                list="pb-clause-types"
                placeholder="e.g. limitation_of_liability"
                value={form.clause_type}
                onChange={(e) => set("clause_type", e.target.value)}
              />
            </Field>
            <Field label="Rule type" hint="position, prohibition, requirement…">
              <Input
                list="pb-rule-types"
                placeholder="position"
                value={form.rule_type}
                onChange={(e) => set("rule_type", e.target.value)}
              />
            </Field>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Risk level">
              <Select value={form.risk_level} onChange={(e) => set("risk_level", e.target.value)}>
                {["low", "medium", "high", "critical"].map((r) => (
                  <option key={r} value={r}>
                    {titleCase(r)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Escalation role" hint="Who signs off if breached">
              <Input
                placeholder="e.g. general_counsel"
                value={form.escalation_role}
                onChange={(e) => set("escalation_role", e.target.value)}
              />
            </Field>
          </div>
          <label className="flex items-center gap-2 text-[13px] text-slate-600">
            <input
              type="checkbox"
              checked={form.approval_required}
              onChange={(e) => set("approval_required", e.target.checked)}
            />
            Require approval when a contract deviates from this rule
          </label>
        </FormSection>

        <FormSection title="Negotiation positions">
          <Field label="Preferred position" hint="Your ideal outcome">
            <Textarea
              rows={2}
              value={form.preferred_position}
              onChange={(e) => set("preferred_position", e.target.value)}
            />
          </Field>
          <Field label="Fallback position" hint="Acceptable compromise">
            <Textarea
              rows={2}
              value={form.fallback_position}
              onChange={(e) => set("fallback_position", e.target.value)}
            />
          </Field>
        </FormSection>

        <FormSection title="Language guardrails">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Prohibited language" hint="Never accept these terms">
              <Textarea
                rows={2}
                value={form.prohibited_language}
                onChange={(e) => set("prohibited_language", e.target.value)}
              />
            </Field>
            <Field label="Required language" hint="Must be present">
              <Textarea
                rows={2}
                value={form.required_language}
                onChange={(e) => set("required_language", e.target.value)}
              />
            </Field>
          </div>
        </FormSection>

        <FormSection title="Guidance (optional)">
          <Field label="Rationale" hint="Why this rule exists">
            <Textarea
              rows={2}
              value={form.rationale}
              onChange={(e) => set("rationale", e.target.value)}
            />
          </Field>
          <Field label="Negotiation guidance" hint="How to argue it with counterparties">
            <Textarea
              rows={2}
              value={form.negotiation_guidance}
              onChange={(e) => set("negotiation_guidance", e.target.value)}
            />
          </Field>
          <Field label="Sample clause" hint="Drop-in language the AI can propose">
            <Textarea
              rows={3}
              value={form.sample_clause}
              onChange={(e) => set("sample_clause", e.target.value)}
            />
          </Field>
        </FormSection>
      </div>
    </Modal>
  );
}

function FormSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <p className="text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">{title}</p>
      {children}
    </div>
  );
}

// ---- Runs ----------------------------------------------------------------
function RunsTab({ playbookId }: { playbookId: string }) {
  const [runOpen, setRunOpen] = useState(false);
  const [openRunId, setOpenRunId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["playbook", playbookId, "runs"],
    queryFn: () => playbooksApi.runs(playbookId),
  });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Runs</CardTitle>
          <Button size="sm" onClick={() => setRunOpen(true)}>
            <Play className="h-3.5 w-3.5" />
            Run playbook
          </Button>
        </CardHeader>
        {isLoading ? (
          <CardBody>
            <CenterSpinner />
          </CardBody>
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Contract</TH>
                <TH>Status</TH>
                <TH>Validation</TH>
                <TH>Model</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((run) => (
                <TR
                  key={run.id}
                  className="cursor-pointer"
                  onClick={() => setOpenRunId(run.id)}
                >
                  <TD className="font-mono text-xs text-slate-600">
                    {run.contract_id}
                  </TD>
                  <TD>
                    <Badge tone={statusTone(run.status)}>
                      {titleCase(run.status)}
                    </Badge>
                  </TD>
                  <TD>
                    {run.validation_status ? (
                      <Badge tone={statusTone(run.validation_status)}>
                        {titleCase(run.validation_status)}
                      </Badge>
                    ) : (
                      "—"
                    )}
                  </TD>
                  <TD>
                    {run.model_name === "claude"
                      ? "AI"
                      : run.model_name
                        ? "Keyword scan"
                        : "—"}
                  </TD>
                </TR>
              ))}
              {data?.length === 0 && (
                <TR>
                  <TD
                    className="py-8 text-center text-slate-400"
                    colSpan={4}
                  >
                    No runs yet.
                  </TD>
                </TR>
              )}
            </tbody>
          </Table>
        )}
      </Card>

      {runOpen && (
        <RunPlaybookModal
          playbookId={playbookId}
          onClose={() => setRunOpen(false)}
        />
      )}
      {openRunId && (
        <RunDetailModal
          runId={openRunId}
          onClose={() => setOpenRunId(null)}
        />
      )}
    </div>
  );
}

function RunPlaybookModal({
  playbookId,
  onClose,
}: {
  playbookId: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);
  const [contractId, setContractId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [createRedline, setCreateRedline] = useState(false);
  const [testMode, setTestMode] = useState(false);

  const { data: contracts } = useQuery({
    queryKey: ["contracts"],
    queryFn: contractsApi.list,
  });
  const { data: versions } = useQuery({
    queryKey: ["playbook", playbookId, "versions"],
    queryFn: () => playbooksApi.versions(playbookId),
  });

  useEffect(() => {
    if (!contractId && contracts && contracts.length > 0) {
      setContractId(contracts[0].id);
    }
  }, [contracts, contractId]);

  async function submit() {
    if (!contractId) return;
    setBusy(true);
    try {
      await playbooksApi.createRun(playbookId, {
        contract_id: contractId,
        playbook_version_id: versionId || undefined,
        create_redline: createRedline,
        test_mode: testMode,
      });
      qc.invalidateQueries({ queryKey: ["playbook", playbookId, "runs"] });
      notify("Playbook run started", "success");
      onClose();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Run failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Run playbook"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!contractId}>
            <Play className="h-4 w-4" />
            Run
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Contract">
          <Select
            value={contractId}
            onChange={(e) => setContractId(e.target.value)}
          >
            {(contracts ?? []).length === 0 && (
              <option value="">No contracts</option>
            )}
            {(contracts ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label="Playbook version"
          hint="Defaults to the current published version"
        >
          <Select
            value={versionId}
            onChange={(e) => setVersionId(e.target.value)}
          >
            <option value="">Default version</option>
            {(versions ?? []).map((v) => (
              <option key={v.id} value={v.id}>
                V{v.version_number} — {titleCase(v.status)}
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
          Create redline
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={testMode}
            onChange={(e) => setTestMode(e.target.checked)}
          />
          Test mode
        </label>
      </div>
    </Modal>
  );
}

function RunDetailModal({
  runId,
  onClose,
}: {
  runId: string;
  onClose: () => void;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["playbook-run", runId],
    queryFn: () => playbooksApi.runDetail(runId),
  });

  return (
    <Modal open onClose={onClose} title="Run detail" size="xl">
      {isLoading ? (
        <CenterSpinner />
      ) : error ? (
        <ErrorState error={error} />
      ) : !data ? null : (
        <RunDetailContent run={data} />
      )}
    </Modal>
  );
}

function RunDetailContent({ run }: { run: PlaybookRunDetailResponse }) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={statusTone(run.status)}>{titleCase(run.status)}</Badge>
        {run.validation_status && (
          <Badge tone={statusTone(run.validation_status)}>
            {titleCase(run.validation_status)}
          </Badge>
        )}
        {run.model_name === "claude" ? (
          <Badge tone="slate">AI</Badge>
        ) : run.model_name ? (
          <Badge tone="amber">Keyword scan{run.error_message ? " · AI unavailable" : ""}</Badge>
        ) : null}
      </div>
      {run.error_message && (
        <div className="rounded-md border border-danger/30 bg-danger-subtle p-3 text-[13px] text-danger">
          {run.error_message}
        </div>
      )}

      <div>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
          Deviations ({run.deviations.length})
        </p>
        {run.deviations.length === 0 ? (
          <div className="rounded-md border border-dashed border-slate-300 bg-slate-100 py-8 text-center text-[13px] text-slate-400">
            No deviations found.
          </div>
        ) : (
          <div className="space-y-3">
            {run.deviations.map((d) => (
              <DeviationCard key={d.id} deviation={d} runId={run.id} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const DECISION_VALUES = [
  "accepted",
  "accepted_fallback",
  "rejected",
  "waived",
  "escalated",
] as const;

function DeviationCard({
  deviation,
  runId,
}: {
  deviation: PlaybookRunDetailResponse["deviations"][number];
  runId: string;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [decision, setDecision] = useState<string>(DECISION_VALUES[0]);
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);

  async function record() {
    setBusy(true);
    try {
      await playbooksApi.decideDeviation(
        deviation.id,
        decision,
        rationale.trim() || undefined,
      );
      qc.invalidateQueries({ queryKey: ["playbook-run", runId] });
      notify("Decision recorded", "success");
      setRationale("");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-slate-900">
              {titleCase(deviation.clause_type)}
            </span>
            <Badge tone={riskTone(deviation.severity)}>
              {titleCase(deviation.severity)}
            </Badge>
          </div>
          <Badge tone={statusTone(deviation.status)}>
            {titleCase(deviation.status)}
          </Badge>
        </div>
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
            Issue
          </p>
          <p className="mt-1 text-[13px] text-slate-700">{deviation.issue}</p>
        </div>
        {deviation.suggested_fix && (
          <div>
            <p className="text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
              Suggested fix
            </p>
            <p className="mt-1 rounded-md bg-success-subtle p-3 text-[13px] text-success">
              {deviation.suggested_fix}
            </p>
          </div>
        )}
        <div className="flex flex-wrap items-end gap-2 border-t border-slate-100 pt-3">
          <Field label="Decision" className="w-44">
            <Select
              value={decision}
              onChange={(e) => setDecision(e.target.value)}
            >
              {DECISION_VALUES.map((d) => (
                <option key={d} value={d}>
                  {titleCase(d)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Rationale" className="min-w-[200px] flex-1">
            <Input
              placeholder="Optional"
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
            />
          </Field>
          <Button size="sm" onClick={record} loading={busy}>
            Record decision
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}

function riskDirectionTone(dir: string): "red" | "green" | "slate" {
  return dir === "less_protected" ? "red" : dir === "more_protected" ? "green" : "slate";
}

function InsightsTab({ playbookId }: { playbookId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [data, setData] = useState<PlaybookInsights | null>(null);
  const [busy, setBusy] = useState(false);
  const [applying, setApplying] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());

  async function analyze() {
    setBusy(true);
    try {
      setData(await playbooksApi.insights(playbookId));
      setDismissed(new Set());
    } catch (e) {
      notify(e instanceof Error ? e.message : "Analysis failed", "error");
    } finally {
      setBusy(false);
    }
  }

  async function apply(rec: PlaybookRecommendation, idx: number) {
    setApplying(rec.clause_type + idx);
    try {
      await playbooksApi.applyInsight(playbookId, {
        clause_type: rec.clause_type,
        preferred_position: rec.proposed_preferred_position,
        fallback_position: rec.proposed_fallback_position,
        negotiation_guidance: rec.proposed_negotiation_guidance,
        summary: rec.change_summary,
      });
      qc.invalidateQueries({ queryKey: ["playbook", playbookId, "versions"] });
      setDismissed((s) => new Set(s).add(idx));
      notify("Applied to a new draft version — review and publish it", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Apply failed", "error");
    } finally {
      setApplying(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>AI insights</CardTitle>
        <Button size="sm" loading={busy} onClick={analyze}>
          Analyze usage
        </Button>
      </CardHeader>
      <CardBody>
        {!data ? (
          <p className="text-sm text-slate-500">
            Analyze how this playbook&apos;s rules performed across past contract reviews and get
            evidence-based suggestions for changes. (Needs decided deviation history first.)
          </p>
        ) : !data.ready ? (
          <div className="rounded-md border border-warning/30 bg-warning-subtle p-4 text-[13px] text-warning">
            Not enough usage data yet — {data.decided_count} decided deviation(s); need at least{" "}
            {data.min_required}. Run this playbook on contracts and decide the flagged deviations to
            build history, then re-analyze.
          </div>
        ) : data.recommendations.length === 0 ? (
          <p className="text-sm text-slate-500">
            No changes recommended — your rules align with how deviations are being decided.
          </p>
        ) : (
          <div className="space-y-3">
            {data.summary && <p className="text-sm text-slate-600">{data.summary}</p>}
            {data.recommendations.map((r, i) =>
              dismissed.has(i) ? null : (
                <div key={i} className="rounded-md border border-slate-200 p-3">
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <span className="font-medium text-slate-900">{titleCase(r.clause_type)}</span>
                    <Badge tone={riskDirectionTone(r.risk_direction)}>
                      {r.risk_direction.replace(/_/g, " ")}
                    </Badge>
                    <Badge tone="slate">{r.confidence} confidence</Badge>
                  </div>
                  <p className="text-sm text-slate-800">{r.change_summary}</p>
                  {r.proposed_preferred_position && (
                    <p className="mt-1 text-xs text-slate-600">
                      <span className="font-medium">New preferred:</span>{" "}
                      {r.proposed_preferred_position}
                    </p>
                  )}
                  {r.proposed_fallback_position && (
                    <p className="text-xs text-slate-600">
                      <span className="font-medium">New fallback:</span>{" "}
                      {r.proposed_fallback_position}
                    </p>
                  )}
                  <p className="mt-1 text-xs text-slate-500">{r.rationale}</p>
                  <div className="mt-2 flex gap-2">
                    <Button
                      size="sm"
                      loading={applying === r.clause_type + i}
                      onClick={() => apply(r, i)}
                    >
                      Apply to new version
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setDismissed((s) => new Set(s).add(i))}
                    >
                      Dismiss
                    </Button>
                  </div>
                </div>
              ),
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
