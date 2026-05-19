"use client";

import { useState } from "react";
import Link from "next/link";
import { Search as SearchIcon, ArrowRight } from "lucide-react";
import { searchApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CardBody,
  EmptyState,
  ErrorState,
  Field,
  Input,
  PageHeader,
  Spinner,
  Table,
  Tabs,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { titleCase } from "@/lib/utils";
import type {
  ClauseSearchResult,
  ContractResponse,
  ContractTextSearchResult,
  ProjectResponse,
} from "@/lib/types";

type TabId = "contracts" | "text" | "clauses" | "projects";

export default function SearchPage() {
  const [tab, setTab] = useState<TabId>("contracts");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Search"
        description="Search contracts, full text, extracted clauses and projects."
      />

      <Tabs
        active={tab}
        onChange={(id) => setTab(id as TabId)}
        tabs={[
          { id: "contracts", label: "Contracts" },
          { id: "text", label: "Text" },
          { id: "clauses", label: "Clauses" },
          { id: "projects", label: "Projects" },
        ]}
      />

      {tab === "contracts" && <ContractsTab />}
      {tab === "text" && <TextTab />}
      {tab === "clauses" && <ClausesTab />}
      {tab === "projects" && <ProjectsTab />}
    </div>
  );
}

function useSearch<T>() {
  const [results, setResults] = useState<T[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function run(fn: () => Promise<T[]>) {
    setLoading(true);
    setError(null);
    try {
      setResults(await fn());
    } catch (e) {
      setError(e);
      setResults(null);
    } finally {
      setLoading(false);
    }
  }

  return { results, loading, error, run };
}

// ---- Contracts -----------------------------------------------------------
function ContractsTab() {
  const [q, setQ] = useState("");
  const [stage, setStage] = useState("");
  const [risk, setRisk] = useState("");
  const [counterparty, setCounterparty] = useState("");
  const { results, loading, error, run } = useSearch<ContractResponse>();

  function search() {
    run(() =>
      searchApi.contracts({
        q: q || undefined,
        stage: stage || undefined,
        risk: risk || undefined,
        counterparty: counterparty || undefined,
      }),
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="flex flex-wrap items-end gap-3">
          <Field label="Query" className="min-w-[200px] flex-1">
            <Input
              placeholder="Title, type…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && search()}
            />
          </Field>
          <Field label="Stage" className="w-44">
            <Input
              placeholder="e.g. active"
              value={stage}
              onChange={(e) => setStage(e.target.value)}
            />
          </Field>
          <Field label="Risk" className="w-36">
            <Input
              placeholder="e.g. high"
              value={risk}
              onChange={(e) => setRisk(e.target.value)}
            />
          </Field>
          <Field label="Counterparty" className="w-44">
            <Input
              value={counterparty}
              onChange={(e) => setCounterparty(e.target.value)}
            />
          </Field>
          <Button onClick={search} loading={loading}>
            <SearchIcon className="h-4 w-4" />
            Search
          </Button>
        </CardBody>
      </Card>

      {loading ? (
        <CenterLoading />
      ) : error ? (
        <ErrorState error={error} />
      ) : results === null ? null : results.length === 0 ? (
        <NoResults />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Title</TH>
                <TH>Counterparty</TH>
                <TH>Type</TH>
                <TH>Stage</TH>
                <TH>Risk</TH>
              </tr>
            </THead>
            <tbody>
              {results.map((c) => (
                <TR key={c.id}>
                  <TD className="font-medium text-slate-900">
                    <Link
                      href={`/contracts/${c.id}`}
                      className="hover:text-brand-700"
                    >
                      {c.title}
                    </Link>
                  </TD>
                  <TD>{c.counterparty_name ?? "—"}</TD>
                  <TD>
                    {c.contract_type ? titleCase(c.contract_type) : "—"}
                  </TD>
                  <TD>
                    <Badge tone="blue">
                      {titleCase(c.lifecycle_stage)}
                    </Badge>
                  </TD>
                  <TD>
                    {c.risk_level ? (
                      <Badge tone="slate">{titleCase(c.risk_level)}</Badge>
                    ) : (
                      "—"
                    )}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </div>
  );
}

// ---- Text ----------------------------------------------------------------
function TextTab() {
  const [q, setQ] = useState("");
  const { results, loading, error, run } = useSearch<ContractTextSearchResult>();

  function search() {
    if (q.trim().length < 2) return;
    run(() => searchApi.text({ q: q.trim(), limit: 50 }));
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="flex items-end gap-3">
          <Field label="Full-text query" className="flex-1">
            <Input
              placeholder="Phrase to find inside contract text…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && search()}
            />
          </Field>
          <Button onClick={search} loading={loading} disabled={!q.trim()}>
            <SearchIcon className="h-4 w-4" />
            Search
          </Button>
        </CardBody>
      </Card>

      {loading ? (
        <CenterLoading />
      ) : error ? (
        <ErrorState error={error} />
      ) : results === null ? null : results.length === 0 ? (
        <NoResults />
      ) : (
        <div className="space-y-3">
          {results.map((r) => (
            <Card key={r.contract_id}>
              <CardBody className="space-y-3">
                <div className="flex items-center justify-between">
                  <Link
                    href={`/contracts/${r.contract_id}`}
                    className="text-sm font-semibold text-slate-900 hover:text-brand-700"
                  >
                    {r.contract_title}
                  </Link>
                  <Badge tone="slate">
                    {r.matches.length} match
                    {r.matches.length === 1 ? "" : "es"}
                  </Badge>
                </div>
                <div className="space-y-2">
                  {r.matches.map((m, i) => (
                    <blockquote
                      key={i}
                      className="border-l-2 border-brand-400 pl-3 text-sm text-slate-600"
                    >
                      …{m.excerpt}…
                    </blockquote>
                  ))}
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Clauses -------------------------------------------------------------
function ClausesTab() {
  const [q, setQ] = useState("");
  const [clauseType, setClauseType] = useState("");
  const { results, loading, error, run } = useSearch<ClauseSearchResult>();

  function search() {
    run(() =>
      searchApi.clauses({
        q: q || undefined,
        clause_type: clauseType || undefined,
      }),
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="flex flex-wrap items-end gap-3">
          <Field label="Query" className="min-w-[200px] flex-1">
            <Input
              placeholder="e.g. limitation of liability"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && search()}
            />
          </Field>
          <Field label="Clause type" className="w-56">
            <Input
              placeholder="e.g. indemnification"
              value={clauseType}
              onChange={(e) => setClauseType(e.target.value)}
            />
          </Field>
          <Button onClick={search} loading={loading}>
            <SearchIcon className="h-4 w-4" />
            Search
          </Button>
        </CardBody>
      </Card>

      {loading ? (
        <CenterLoading />
      ) : error ? (
        <ErrorState error={error} />
      ) : results === null ? null : results.length === 0 ? (
        <NoResults />
      ) : (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {results.map((c) => (
            <Card key={c.clause_id}>
              <CardBody className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <Badge tone="violet">{titleCase(c.clause_type)}</Badge>
                  {Number.isFinite(Number(c.confidence)) && (
                    <Badge tone="slate">
                      {Math.round(Number(c.confidence) * 100)}%
                    </Badge>
                  )}
                </div>
                <Link
                  href={`/contracts/${c.contract_id}`}
                  className="block text-sm font-semibold text-slate-900 hover:text-brand-700"
                >
                  {c.heading || c.contract_title}
                </Link>
                <p className="text-xs text-slate-400">{c.contract_title}</p>
                <p className="text-sm text-slate-600">{c.excerpt}</p>
              </CardBody>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Projects ------------------------------------------------------------
function ProjectsTab() {
  const [q, setQ] = useState("");
  const { results, loading, error, run } = useSearch<ProjectResponse>();

  function search() {
    run(() => searchApi.projects({ q: q || undefined }));
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="flex items-end gap-3">
          <Field label="Query" className="flex-1">
            <Input
              placeholder="Project name or description…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && search()}
            />
          </Field>
          <Button onClick={search} loading={loading}>
            <SearchIcon className="h-4 w-4" />
            Search
          </Button>
        </CardBody>
      </Card>

      {loading ? (
        <CenterLoading />
      ) : error ? (
        <ErrorState error={error} />
      ) : results === null ? null : results.length === 0 ? (
        <NoResults />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {results.map((p) => (
            <Link key={p.id} href={`/projects/${p.id}`}>
              <Card className="h-full transition-colors hover:border-brand-300">
                <CardBody className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="text-sm font-semibold text-slate-900">
                      {p.name}
                    </h3>
                    <ArrowRight className="h-4 w-4 shrink-0 text-slate-300" />
                  </div>
                  <Badge tone="slate">{titleCase(p.project_type)}</Badge>
                  {p.description && (
                    <p className="line-clamp-2 text-sm text-slate-500">
                      {p.description}
                    </p>
                  )}
                </CardBody>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Shared states -------------------------------------------------------
function CenterLoading() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-slate-400">
      <Spinner className="h-6 w-6" />
      <p className="text-sm">Searching…</p>
    </div>
  );
}

function NoResults() {
  return (
    <EmptyState
      icon={<SearchIcon className="h-6 w-6" />}
      title="No results"
      description="Try a different query or adjust your filters."
    />
  );
}
