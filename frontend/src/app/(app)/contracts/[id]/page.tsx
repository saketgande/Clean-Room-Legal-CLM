"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Download,
  RotateCcw,
  Check,
  X,
  Send,
  Brain as BrainIcon,
  ChevronRight,
  BookMarked,
  Sparkles,
  ListChecks,
  CheckSquare,
  PenLine,
  Wand2,
  Info,
  History,
  Activity as ActivityIcon,
} from "lucide-react";
import {
  aiApi,
  approvalsApi,
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
  stageTone,
  titleCase,
} from "@/lib/utils";
import { useToast } from "@/components/toast";
import { useLayout } from "@/lib/layout";
import { ContractDocument } from "@/components/contract-document";
import type { Citation } from "@/lib/types";

type PanelId = "overview" | "versions" | "redlines" | "activity" | "brain";

const PANELS: {
  id: PanelId;
  label: string;
  hint: string;
  icon: typeof Info;
}[] = [
  { id: "overview", label: "Overview", hint: "Metadata & lifecycle", icon: Info },
  { id: "versions", label: "Versions", hint: "Document history", icon: History },
  { id: "redlines", label: "Redlines", hint: "Tracked changes", icon: Wand2 },
  {
    id: "activity",
    label: "Activity",
    hint: "Timeline & stage history",
    icon: ActivityIcon,
  },
  {
    id: "brain",
    label: "Contract Brain",
    hint: "Ask about this contract",
    icon: BrainIcon,
  },
];

export default function ContractDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [panel, setPanel] = useState<PanelId>("overview");
  const [activeEditId, setActiveEditId] = useState<string | null>(null);
  const [runPbOpen, setRunPbOpen] = useState(false);
  const [sigOpen, setSigOpen] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const qc = useQueryClient();
  const { notify } = useToast();
  const { setForceCollapsed } = useLayout();

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
  const { data: versions } = useQuery({
    queryKey: ["contract", id, "versions"],
    queryFn: () => contractsApi.versions(id),
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
      notify("Submitted for approval", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed", "error");
    } finally {
      setBusy(null);
    }
  }

  if (isLoading) return <CenterSpinner label="Loading contract…" />;
  if (error) return <ErrorState error={error} />;
  if (!contract) return null;

  return (
    <div className="-mx-4 -my-4 flex h-[calc(100vh-3.5rem)] flex-col sm:-mx-6 sm:-my-6">
      {/* Header band */}
      <div className="shrink-0 border-b border-slate-200 bg-white px-4 py-3">
        <Link
          href="/contract-hub"
          className="mb-2 inline-flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-800"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Contract Hub
        </Link>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold tracking-tight text-slate-900">
              {contract.title}
            </h1>
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              <Badge tone={stageTone(contract.lifecycle_stage)}>
                {titleCase(contract.lifecycle_stage)}
              </Badge>
              {contract.risk_level && (
                <Badge tone={riskTone(contract.risk_level)}>
                  {titleCase(contract.risk_level)} risk
                </Badge>
              )}
              {contract.contract_type && (
                <Badge tone="slate">
                  {titleCase(contract.contract_type)}
                </Badge>
              )}
              {contract.counterparty_name && (
                <span className="text-sm text-slate-500">
                  vs. {contract.counterparty_name}
                </span>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" onClick={() => setRunPbOpen(true)}>
              <BookMarked className="h-4 w-4" />
              Run Playbook
            </Button>
            <Button
              size="sm"
              variant="outline"
              loading={busy === "analyze"}
              onClick={reAnalyze}
            >
              <Sparkles className="h-4 w-4" />
              Re-run Analysis
            </Button>
            <Button
              size="sm"
              variant="outline"
              loading={busy === "oblig"}
              onClick={extractObligations}
            >
              <ListChecks className="h-4 w-4" />
              Obligations
            </Button>
            <Button
              size="sm"
              variant="outline"
              loading={busy === "approve"}
              onClick={submitApproval}
            >
              <CheckSquare className="h-4 w-4" />
              Submit for Approval
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setSigOpen(true)}
            >
              <PenLine className="h-4 w-4" />
              Send for Signature
            </Button>
            <Button
              size="sm"
              variant={askOpen ? "primary" : "outline"}
              onClick={() => setAskOpen((o) => !o)}
            >
              <BrainIcon className="h-4 w-4" />
              Ask AI
            </Button>
          </div>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Left rail: navigator + versions explorer */}
        <aside className="hidden w-64 shrink-0 flex-col border-r border-slate-200 bg-white lg:flex">
          <div className="border-b border-slate-100 p-3">
            <p className="mb-2 px-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
              Workspace
            </p>
            {PANELS.map((p) => {
              const active = panel === p.id;
              const Icon = p.icon;
              return (
                <button
                  key={p.id}
                  onClick={() => {
                    setPanel(p.id);
                    setAskOpen(false);
                  }}
                  className={cn(
                    "mb-1 flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left transition-colors",
                    active ? "bg-brand-50" : "hover:bg-slate-50",
                  )}
                >
                  <Icon
                    className={cn(
                      "h-4 w-4 shrink-0",
                      active ? "text-brand-600" : "text-slate-400",
                    )}
                  />
                  <span className="min-w-0 flex-1">
                    <span
                      className={cn(
                        "block truncate text-sm font-medium",
                        active ? "text-brand-700" : "text-slate-700",
                      )}
                    >
                      {p.label}
                    </span>
                    <span className="block truncate text-xs text-slate-400">
                      {p.hint}
                    </span>
                  </span>
                  {p.id === "redlines" && pendingRedlines > 0 && (
                    <span className="shrink-0 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">
                      {pendingRedlines}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          <div className="flex-1 overflow-y-auto p-3">
            <div className="mb-2 flex items-center justify-between px-1">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                Versions
              </p>
              <span className="text-xs text-slate-400">
                {(versions ?? []).length}
              </span>
            </div>
            <div className="space-y-1">
              {(versions ?? [])
                .slice()
                .sort((a, b) => b.version_number - a.version_number)
                .map((v) => (
                  <button
                    key={v.id}
                    onClick={() => setPanel("versions")}
                    className="flex w-full items-center gap-2 rounded-lg border border-slate-200 px-2.5 py-2 text-left transition-colors hover:border-brand-300 hover:bg-brand-50/40"
                  >
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-slate-100 text-xs font-semibold text-slate-600">
                      V{v.version_number}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium text-slate-700">
                        {titleCase(v.source)}
                      </span>
                      <span className="block truncate text-[11px] text-slate-400">
                        {v.change_summary ?? "No summary"}
                      </span>
                    </span>
                    {v.is_authoritative && (
                      <span className="shrink-0 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700">
                        Auth
                      </span>
                    )}
                  </button>
                ))}
              {(versions ?? []).length === 0 && (
                <p className="px-1 py-4 text-center text-xs text-slate-400">
                  No versions yet.
                </p>
              )}
            </div>
          </div>
        </aside>

        {/* Center: the document, always visible */}
        <div className="hidden flex-1 overflow-hidden p-3 lg:block">
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

        {/* Right: context panel — replaced by the Ask AI chat when open */}
        {!askOpen && (
        <section className="flex flex-1 flex-col bg-white lg:w-[30rem] lg:flex-none lg:shrink-0 lg:border-l lg:border-slate-200">
          <div className="flex h-12 shrink-0 items-center gap-2 border-b border-slate-100 px-4">
            <span className="text-sm font-semibold text-slate-900">
              {PANELS.find((p) => p.id === panel)?.label}
            </span>
            <span className="text-xs text-slate-400">
              · {PANELS.find((p) => p.id === panel)?.hint}
            </span>
          </div>
          <div className="flex-1 overflow-y-auto p-4">
            {panel === "overview" && <Overview contractId={id} />}
            {panel === "versions" && <Versions contractId={id} />}
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
            {panel === "activity" && <ActivityTab contractId={id} />}
            {panel === "brain" && <BrainTab contractId={id} />}
          </div>
        </section>
        )}

        {askOpen && (
          <AskAIPanel
            contractId={id}
            contractTitle={contract.title}
            onClose={() => setAskOpen(false)}
          />
        )}
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

// ---- Overview ------------------------------------------------------------
function Overview({ contractId }: { contractId: string }) {
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

  if (!contract) return null;

  const facts: [string, string][] = [
    ["Counterparty", contract.counterparty_name ?? "—"],
    [
      "Contract type",
      contract.contract_type ? titleCase(contract.contract_type) : "—",
    ],
    ["Jurisdiction", contract.jurisdiction ?? "—"],
    ["Value", fmtMoney(contract.value_amount, contract.currency)],
    ["Effective date", fmtDate(contract.effective_date)],
    ["Expiration date", fmtDate(contract.expiration_date)],
  ];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Contract metadata</CardTitle>
        </CardHeader>
        <CardBody>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4">
            {facts.map(([k, v]) => (
              <div key={k}>
                <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">
                  {k}
                </dt>
                <dd className="mt-1 text-sm text-slate-800">{v}</dd>
              </div>
            ))}
          </dl>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Lifecycle</CardTitle>
        </CardHeader>
        <CardBody className="space-y-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
              Current stage
            </p>
            <div className="mt-2">
              <Badge tone={stageTone(contract.lifecycle_stage)}>
                {titleCase(contract.lifecycle_stage)}
              </Badge>
            </div>
          </div>
          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
              Allowed transitions
            </p>
            <div className="flex flex-wrap gap-1.5">
              {options?.allowed_transitions.length ? (
                options.allowed_transitions.map((s) => (
                  <span
                    key={s}
                    className="rounded-md bg-slate-100 px-2 py-1 text-xs text-slate-600"
                  >
                    {titleCase(s)}
                  </span>
                ))
              ) : (
                <span className="text-sm text-slate-400">None</span>
              )}
            </div>
          </div>
          <Button
            className="w-full"
            variant="outline"
            disabled={!options?.allowed_transitions.length}
            onClick={() => setTransitionOpen(true)}
          >
            Advance stage
          </Button>
        </CardBody>
      </Card>

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
                    onClick={() =>
                      contractsApi.downloadVersion(contractId, v.id)
                    }
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

// ---- Ask AI (docked, contract-scoped chat) -------------------------------
type ChatTurn = { role: "user" | "assistant"; text: string; citations?: Citation[] };

const ASK_SUGGESTIONS = [
  "Summarize the key risks in this contract",
  "What are the termination and renewal terms?",
  "What changed in the latest version?",
];

function AskAIPanel({
  contractId,
  contractTitle,
  onClose,
}: {
  contractId: string;
  contractTitle: string;
  onClose: () => void;
}) {
  const { notify } = useToast();
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(text: string) {
    const q = text.trim();
    if (q.length < 3 || busy) return;
    setInput("");
    setTurns((t) => [...t, { role: "user", text: q }]);
    setBusy(true);
    try {
      const res = await brainApi.ask({
        question: q,
        query_scope: "contract",
        contract_id: contractId,
      });
      setTurns((t) => [
        ...t,
        { role: "assistant", text: res.answer, citations: res.citations },
      ]);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Query failed", "error");
      setTurns((t) => [
        ...t,
        {
          role: "assistant",
          text: "I couldn't answer that just now. Please try again.",
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="flex w-full flex-col border-l border-slate-200 bg-white lg:w-[26rem] lg:flex-none lg:shrink-0">
      <div className="flex h-12 shrink-0 items-center gap-2.5 border-b border-slate-100 px-4">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
          <Sparkles className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-serif text-sm font-medium leading-tight text-slate-900">
            Ask AEGIS
          </p>
          <p className="truncate text-xs text-slate-400">
            Scoped to · {contractTitle}
          </p>
        </div>
        <button
          onClick={onClose}
          className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          aria-label="Close Ask AI"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {turns.length === 0 && (
          <div className="space-y-3">
            <p className="text-sm text-slate-500">
              Ask anything about this contract — answers are grounded in the
              document and cite their source.
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

        {turns.map((turn, i) =>
          turn.role === "user" ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-md bg-brand-600 px-3.5 py-2 text-sm leading-relaxed text-white">
                {turn.text}
              </div>
            </div>
          ) : (
            <div key={i} className="space-y-2">
              <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
                <Sparkles className="h-3 w-3 text-brand-600" />
                AEGIS
              </div>
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
                {turn.text}
              </p>
              {turn.citations && turn.citations.length > 0 && (
                <div className="space-y-2 border-t border-slate-100 pt-2.5">
                  {turn.citations.map((c, ci) => (
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
          ),
        )}

        {busy && (
          <div className="flex items-center gap-2 text-sm text-slate-400">
            <Spinner className="h-3.5 w-3.5" />
            Thinking…
          </div>
        )}
      </div>

      <div className="shrink-0 border-t border-slate-100 p-3">
        <div className="rounded-xl border border-slate-200 bg-white p-2 transition focus-within:border-slate-300">
          <Textarea
            rows={1}
            placeholder="Ask about this contract…"
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
              ⏎ send · cites this contract
            </span>
            <Button
              size="icon"
              onClick={() => send(input)}
              disabled={input.trim().length < 3 || busy}
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
    </aside>
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
  const { notify } = useToast();
  const [rows, setRows] = useState([{ name: "", email: "", role: "signer" }]);
  const [override, setOverride] = useState(false);
  const [busy, setBusy] = useState(false);

  function setRow(i: number, patch: Partial<(typeof rows)[number]>) {
    setRows((r) => r.map((x, idx) => (idx === i ? { ...x, ...patch } : x)));
  }

  const valid = rows.filter((r) => r.name.trim() && r.email.trim());

  async function send() {
    if (valid.length === 0) return;
    setBusy(true);
    try {
      await signaturesApi.send({
        contract_id: contractId,
        recipients: valid.map((r) => ({
          name: r.name.trim(),
          email: r.email.trim(),
          role: r.role.trim() || "signer",
        })),
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
          <Button onClick={send} loading={busy} disabled={valid.length === 0}>
            Send envelope
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="space-y-3">
          {rows.map((r, i) => (
            <div key={i} className="flex items-end gap-2">
              <Field label={i === 0 ? "Name" : undefined} className="flex-1">
                <Input
                  value={r.name}
                  onChange={(e) => setRow(i, { name: e.target.value })}
                  placeholder="Jane Counsel"
                />
              </Field>
              <Field label={i === 0 ? "Email" : undefined} className="flex-1">
                <Input
                  value={r.email}
                  onChange={(e) => setRow(i, { email: e.target.value })}
                  placeholder="jane@company.com"
                />
              </Field>
              <Button
                size="icon"
                variant="ghost"
                disabled={rows.length === 1}
                onClick={() =>
                  setRows((rs) => rs.filter((_, idx) => idx !== i))
                }
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          ))}
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              setRows((rs) => [...rs, { name: "", email: "", role: "signer" }])
            }
          >
            Add recipient
          </Button>
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
