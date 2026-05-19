"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Plus, Send, Play } from "lucide-react";
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
  Select,
  Tabs,
  Table,
  TD,
  TH,
  THead,
  TR,
  Textarea,
} from "@/components/ui";
import { fmtDateTime, riskTone, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type {
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
    <div className="space-y-6">
      <div>
        <Link
          href="/playbooks"
          className="mb-3 inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800"
        >
          <ArrowLeft className="h-4 w-4" />
          Playbooks
        </Link>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-slate-900">
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
        ]}
      />

      {tab === "versions" && <VersionsTab playbookId={id} />}
      {tab === "rules" && <RulesTab playbookId={id} />}
      {tab === "runs" && <RunsTab playbookId={id} />}
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
function RulesTab({ playbookId }: { playbookId: string }) {
  const { data: versions } = useQuery({
    queryKey: ["playbook", playbookId, "versions"],
    queryFn: () => playbooksApi.versions(playbookId),
  });
  const [versionId, setVersionId] = useState("");
  const [addOpen, setAddOpen] = useState(false);

  useEffect(() => {
    if (!versionId && versions && versions.length > 0) {
      setVersionId(versions[0].id);
    }
  }, [versions, versionId]);

  const selectedVersion = versions?.find((v) => v.id === versionId);

  const { data: rules, isLoading } = useQuery({
    queryKey: ["playbook", playbookId, "rules", versionId],
    queryFn: () => playbooksApi.rules(playbookId, versionId),
    enabled: !!versionId,
  });

  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="flex flex-wrap items-end justify-between gap-3">
          <Field label="Version" className="w-64">
            <Select
              value={versionId}
              onChange={(e) => setVersionId(e.target.value)}
            >
              {(versions ?? []).length === 0 && (
                <option value="">No versions</option>
              )}
              {(versions ?? []).map((v) => (
                <option key={v.id} value={v.id}>
                  V{v.version_number} — {titleCase(v.status)}
                </option>
              ))}
            </Select>
          </Field>
          {selectedVersion?.status === "draft" && (
            <Button size="sm" onClick={() => setAddOpen(true)}>
              <Plus className="h-3.5 w-3.5" />
              Add rule
            </Button>
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
          <CardBody className="py-12 text-center text-sm text-slate-400">
            No rules in this version yet.
          </CardBody>
        </Card>
      ) : (
        <div className="space-y-3">
          {(rules ?? []).map((r) => (
            <Card key={r.id}>
              <CardBody className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-slate-900">
                      {titleCase(r.clause_type)}
                    </span>
                    <Badge tone="slate">{titleCase(r.rule_type)}</Badge>
                  </div>
                  <Badge tone={riskTone(r.risk_level)}>
                    {titleCase(r.risk_level)} risk
                  </Badge>
                </div>
                <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
                  <Detail label="Preferred position" value={r.preferred_position} />
                  <Detail label="Fallback position" value={r.fallback_position} />
                  <Detail
                    label="Prohibited language"
                    value={r.prohibited_language}
                  />
                  <Detail
                    label="Required language"
                    value={r.required_language}
                  />
                  <Detail label="Escalation role" value={r.escalation_role} />
                  <Detail
                    label="Approval required"
                    value={r.approval_required ? "Yes" : "No"}
                  />
                </dl>
                {r.rationale && (
                  <div>
                    <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                      Rationale
                    </p>
                    <p className="mt-1 text-sm text-slate-700">
                      {r.rationale}
                    </p>
                  </div>
                )}
                {r.negotiation_guidance && (
                  <div>
                    <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                      Negotiation guidance
                    </p>
                    <p className="mt-1 text-sm text-slate-700">
                      {r.negotiation_guidance}
                    </p>
                  </div>
                )}
                {r.sample_clause && (
                  <div>
                    <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                      Sample clause
                    </p>
                    <p className="mt-1 whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
                      {r.sample_clause}
                    </p>
                  </div>
                )}
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {addOpen && versionId && (
        <AddRuleModal
          playbookId={playbookId}
          versionId={versionId}
          onClose={() => setAddOpen(false)}
        />
      )}
    </div>
  );
}

function Detail({
  label,
  value,
}: {
  label: string;
  value: string | null | undefined;
}) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">
        {label}
      </dt>
      <dd className="mt-1 text-sm text-slate-700">{value || "—"}</dd>
    </div>
  );
}

function AddRuleModal({
  playbookId,
  versionId,
  onClose,
}: {
  playbookId: string;
  versionId: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    clause_type: "",
    rule_type: "",
    preferred_position: "",
    fallback_position: "",
    prohibited_language: "",
    required_language: "",
    risk_level: "medium",
    rationale: "",
    escalation_role: "",
    approval_required: false,
    sample_clause: "",
    negotiation_guidance: "",
  });

  function set<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function submit() {
    if (!form.clause_type.trim() || !form.rule_type.trim()) return;
    setBusy(true);
    try {
      const payload: Record<string, unknown> = {
        clause_type: form.clause_type.trim(),
        rule_type: form.rule_type.trim(),
        risk_level: form.risk_level,
        approval_required: form.approval_required,
      };
      const optional: (keyof typeof form)[] = [
        "preferred_position",
        "fallback_position",
        "prohibited_language",
        "required_language",
        "rationale",
        "escalation_role",
        "sample_clause",
        "negotiation_guidance",
      ];
      for (const k of optional) {
        const v = (form[k] as string).trim();
        if (v) payload[k] = v;
      }
      await playbooksApi.createRule(playbookId, versionId, payload);
      qc.invalidateQueries({
        queryKey: ["playbook", playbookId, "rules", versionId],
      });
      notify("Rule added", "success");
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
      title="Add rule"
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            loading={busy}
            disabled={!form.clause_type.trim() || !form.rule_type.trim()}
          >
            Add rule
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Clause type">
            <Input
              placeholder="e.g. limitation_of_liability"
              value={form.clause_type}
              onChange={(e) => set("clause_type", e.target.value)}
            />
          </Field>
          <Field label="Rule type">
            <Input
              placeholder="e.g. position, prohibition"
              value={form.rule_type}
              onChange={(e) => set("rule_type", e.target.value)}
            />
          </Field>
        </div>
        <Field label="Preferred position">
          <Textarea
            rows={2}
            value={form.preferred_position}
            onChange={(e) => set("preferred_position", e.target.value)}
          />
        </Field>
        <Field label="Fallback position">
          <Textarea
            rows={2}
            value={form.fallback_position}
            onChange={(e) => set("fallback_position", e.target.value)}
          />
        </Field>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Prohibited language">
            <Textarea
              rows={2}
              value={form.prohibited_language}
              onChange={(e) => set("prohibited_language", e.target.value)}
            />
          </Field>
          <Field label="Required language">
            <Textarea
              rows={2}
              value={form.required_language}
              onChange={(e) => set("required_language", e.target.value)}
            />
          </Field>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Risk level">
            <Select
              value={form.risk_level}
              onChange={(e) => set("risk_level", e.target.value)}
            >
              {["low", "medium", "high", "critical"].map((r) => (
                <option key={r} value={r}>
                  {titleCase(r)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Escalation role">
            <Input
              placeholder="e.g. general_counsel"
              value={form.escalation_role}
              onChange={(e) => set("escalation_role", e.target.value)}
            />
          </Field>
        </div>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={form.approval_required}
            onChange={(e) => set("approval_required", e.target.checked)}
          />
          Approval required
        </label>
        <Field label="Rationale">
          <Textarea
            rows={2}
            value={form.rationale}
            onChange={(e) => set("rationale", e.target.value)}
          />
        </Field>
        <Field label="Negotiation guidance">
          <Textarea
            rows={2}
            value={form.negotiation_guidance}
            onChange={(e) => set("negotiation_guidance", e.target.value)}
          />
        </Field>
        <Field label="Sample clause">
          <Textarea
            rows={3}
            value={form.sample_clause}
            onChange={(e) => set("sample_clause", e.target.value)}
          />
        </Field>
      </div>
    </Modal>
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
                  <TD>{run.model_name ?? "—"}</TD>
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
        {run.model_name && <Badge tone="slate">{run.model_name}</Badge>}
      </div>
      {run.error_message && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {run.error_message}
        </div>
      )}

      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Deviations ({run.deviations.length})
        </p>
        {run.deviations.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-300 bg-white py-8 text-center text-sm text-slate-400">
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
          <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
            Issue
          </p>
          <p className="mt-1 text-sm text-slate-700">{deviation.issue}</p>
        </div>
        {deviation.suggested_fix && (
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
              Suggested fix
            </p>
            <p className="mt-1 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800">
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
