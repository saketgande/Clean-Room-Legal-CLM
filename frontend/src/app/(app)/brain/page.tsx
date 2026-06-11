"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Brain as BrainIcon, Send, History } from "lucide-react";
import { brainApi, contractsApi, projectsApi } from "@/lib/endpoints";
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
  Select,
} from "@/components/ui";
import { fmtRelative, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { BrainQuery, BrainScope } from "@/lib/types";

export default function BrainPage() {
  const { notify } = useToast();
  const [question, setQuestion] = useState("");
  const [scope, setScope] = useState<BrainScope>("portfolio");
  const [projectId, setProjectId] = useState("");
  const [contractId, setContractId] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BrainQuery | null>(null);

  const { data: projects } = useQuery({
    queryKey: ["projects"],
    queryFn: projectsApi.list,
  });
  const { data: contracts } = useQuery({
    queryKey: ["contracts"],
    queryFn: contractsApi.list,
  });
  const {
    data: recent,
    isLoading: recentLoading,
    error: recentError,
  } = useQuery({
    queryKey: ["brain-queries"],
    queryFn: () => brainApi.queries({ limit: 25 }),
  });

  async function ask() {
    if (question.trim().length < 3) return;
    if (scope === "project" && !projectId) {
      notify("Select a project for project scope", "error");
      return;
    }
    if (scope === "contract" && !contractId) {
      notify("Select a contract for contract scope", "error");
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      const res = await brainApi.ask({
        question,
        query_scope: scope,
        project_id: scope === "project" ? projectId : undefined,
        contract_id: scope === "contract" ? contractId : undefined,
      });
      setResult(res);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Query failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
          <BrainIcon className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">
            Contract Brain
          </h1>
          <p className="mt-0.5 text-sm text-slate-500">
            Ask grounded questions across your entire contract portfolio.
          </p>
        </div>
      </div>

      <Card>
        <CardBody className="space-y-4">
          <Field label="Question">
            <div className="flex gap-2">
              <Input
                placeholder="e.g. Which contracts auto-renew without 60 days notice?"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && ask()}
              />
              <Button onClick={ask} loading={busy} className="shrink-0">
                <Send className="h-4 w-4" />
                Ask
              </Button>
            </div>
          </Field>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Field label="Scope">
              <Select
                value={scope}
                onChange={(e) => setScope(e.target.value as BrainScope)}
              >
                <option value="portfolio">Portfolio</option>
                <option value="project">Project</option>
                <option value="contract">Contract</option>
              </Select>
            </Field>
            {scope === "project" && (
              <Field label="Project" className="sm:col-span-2">
                <Select
                  value={projectId}
                  onChange={(e) => setProjectId(e.target.value)}
                >
                  <option value="">Select a project…</option>
                  {(projects ?? []).map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            {scope === "contract" && (
              <Field label="Contract" className="sm:col-span-2">
                <Select
                  value={contractId}
                  onChange={(e) => setContractId(e.target.value)}
                >
                  <option value="">Select a contract…</option>
                  {(contracts ?? []).map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.title}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
          </div>
        </CardBody>
      </Card>

      {busy && <CenterSpinner label="Searching the contract graph…" />}

      {result && !busy && <AnswerCard query={result} />}

      <Card>
        <CardHeader>
          <CardTitle>Recent questions</CardTitle>
          <History className="h-4 w-4 text-slate-400" />
        </CardHeader>
        <CardBody>
          {recentLoading ? (
            <CenterSpinner />
          ) : recentError ? (
            <ErrorState error={recentError} />
          ) : !recent?.length ? (
            <EmptyState
              icon={<BrainIcon className="h-6 w-6" />}
              title="No questions yet"
              description="Ask your first question above to start building portfolio memory."
            />
          ) : (
            <ul className="divide-y divide-slate-100">
              {recent.map((q) => (
                <li key={q.id}>
                  <button
                    onClick={() => {
                      setResult(q);
                      setQuestion(q.question);
                      setScope(q.query_scope);
                      window.scrollTo({ top: 0, behavior: "smooth" });
                    }}
                    className="flex w-full items-center gap-3 py-3 text-left transition-colors hover:bg-slate-50/60"
                  >
                    <span className="flex-1 text-sm text-slate-800">
                      {q.question}
                    </span>
                    <Badge tone="blue">{titleCase(q.query_scope)}</Badge>
                    <span className="w-20 shrink-0 text-right text-xs text-slate-400">
                      {fmtRelative(q.created_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function AnswerCard({ query }: { query: BrainQuery }) {
  const m = query.retrieval_metadata;
  const stats: [string, number][] = [
    ["Sources", m.source_count],
    ["Graph facts", m.graph_facts],
    ["Vector chunks", m.vector_chunks],
    ["Fulltext clauses", m.fulltext_clauses],
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Answer</CardTitle>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400">Confidence</span>
          <Badge tone={statusTone(m.confidence)}>
            {titleCase(m.confidence)}
          </Badge>
        </div>
      </CardHeader>
      <CardBody className="space-y-4">
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
          {query.answer}
        </p>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {stats.map(([label, value]) => (
            <div
              key={label}
              className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5"
            >
              <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                {label}
              </p>
              <p className="mt-1 text-lg font-semibold text-slate-900">
                {value}
              </p>
            </div>
          ))}
        </div>

        {m.limitations && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            {m.limitations}
          </div>
        )}

        {query.citations.length > 0 && (
          <div className="space-y-2 border-t border-slate-200 pt-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Citations ({query.citations.length})
            </p>
            {query.citations.map((c, i) => (
              <blockquote
                key={i}
                className="border-l-2 border-brand-400 pl-3 text-sm text-slate-600"
              >
                {c.quote ?? c.excerpt}
                {c.label && (
                  <span className="mt-1 block text-xs text-slate-400">
                    {c.label}
                  </span>
                )}
              </blockquote>
            ))}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
