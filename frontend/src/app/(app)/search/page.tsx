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
    <div className="space-y-4">
      <PageHeader
        title="Search"
        description="Look up contracts and projects by name, metadata and dates."
      />

      <Tabs
        active={tab}
        onChange={(id) => setTab(id as TabId)}
        tabs={[
          { id: "contracts", label: "Contracts" },
          { id: "text", label: "Full text" },
          { id: "clauses", label: "Clauses" },
          { id: "projects", label: "Projects" },
        ]}
      />

      {tab === "contracts" && <ContractsTab />}
      {tab === "text" && <TextTab />}
      {tab === "clauses" && <ClausesTab />}
      {tab === "projects" && <ProjectsTab />}

      <p className="text-sm text-slate-500">
        Looking for something <em>inside</em> your contracts — clauses, wording,
        concepts? Use{" "}
        <Link
          href="/brain"
          className="font-medium text-brand-600 hover:text-brand-700"
        >
          Contract Brain search
        </Link>{" "}
        — it understands meaning, not just keywords.
      </p>
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

// ---- Full text -------------------------------------------------------------
function TextTab() {
  const [q, setQ] = useState("");
  const { results, loading, error, run } = useSearch<ContractTextSearchResult>();

  function search() {
    if (!q.trim()) return;
    run(() => searchApi.text({ q: q.trim(), limit: 50 }));
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="flex items-end gap-3">
          <Field
            label="Exact words or phrases"
            hint="Searches the latest text of every contract you can read."
            className="flex-1"
          >
            <Input
              placeholder="termination for convenience"
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
        <div className="space-y-3">
          {results.map((r) => (
            <Card key={`${r.contract_id}-${r.text_snapshot_id}`}>
              <CardBody className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <Link
                    href={`/contracts/${r.contract_id}`}
                    className="text-sm font-semibold text-slate-900 hover:text-brand-700"
                  >
                    {r.contract_title}
                  </Link>
                  <Badge tone="slate">
                    {r.matches.length} match{r.matches.length === 1 ? "" : "es"}
                  </Badge>
                </div>
                <ul className="space-y-1.5">
                  {r.matches.slice(0, 3).map((m, i) => (
                    <li
                      key={i}
                      className="rounded border border-slate-200 bg-slate-50 px-3 py-2 text-[13px] leading-relaxed text-slate-600"
                    >
                      …{m.excerpt}…
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Clauses ---------------------------------------------------------------
const CLAUSE_TYPE_OPTIONS = [
  "confidentiality", "data_protection", "governing_law", "indemnification",
  "limitation_of_liability", "non_compete", "payment_terms", "renewal",
  "term_and_termination", "warranty",
];

function ClausesTab() {
  const [q, setQ] = useState("");
  const [clauseType, setClauseType] = useState("");
  const { results, loading, error, run } = useSearch<ClauseSearchResult>();

  function search() {
    run(() =>
      searchApi.clauses({
        q: q.trim() || undefined,
        clause_type: clauseType || undefined,
        limit: 50,
      }),
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="flex flex-wrap items-end gap-3">
          <Field label="Wording" className="min-w-[200px] flex-1">
            <Input
              placeholder="auto-renew, uncapped liability…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && search()}
            />
          </Field>
          <Field label="Clause type" className="w-56">
            <select
              value={clauseType}
              onChange={(e) => setClauseType(e.target.value)}
              className="h-9 w-full rounded border border-slate-300 bg-slate-100 px-3 text-sm text-slate-900 transition-colors focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
            >
              <option value="">Any type</option>
              {CLAUSE_TYPE_OPTIONS.map((t) => (
                <option key={t} value={t}>
                  {titleCase(t)}
                </option>
              ))}
            </select>
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
                <TH>Contract</TH>
                <TH>Clause</TH>
                <TH>Excerpt</TH>
                <TH className="text-right">Confidence</TH>
              </tr>
            </THead>
            <tbody>
              {results.map((r) => (
                <TR key={r.clause_id}>
                  <TD className="font-medium text-slate-900">
                    <Link
                      href={`/contracts/${r.contract_id}`}
                      className="hover:text-brand-700"
                    >
                      {r.contract_title}
                    </Link>
                  </TD>
                  <TD>
                    <Badge tone="blue">{titleCase(r.clause_type)}</Badge>
                  </TD>
                  <TD className="max-w-md">
                    <span className="line-clamp-2 text-[13px] text-slate-600">
                      {r.excerpt}
                    </span>
                  </TD>
                  <TD className="text-right tabular-nums">
                    {Math.round(r.confidence * 100)}%
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
