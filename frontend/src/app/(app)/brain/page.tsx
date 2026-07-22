"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import {
  Brain as BrainIcon,
  Sparkles,
  Send,
  History,
  Search as SearchIcon,
  ArrowUpRight,
  Quote,
  FileText,
  Layers,
} from "lucide-react";
import { brainApi, contractsApi, projectsApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CenterSpinner,
  EmptyState,
  ErrorState,
} from "@/components/ui";
import { cn, fmtRelative, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type {
  BrainQuery,
  BrainScope,
  BrainSearchResponse,
  Citation,
} from "@/lib/types";

const SUGGESTIONS = [
  "Which contracts auto-renew without 60 days notice?",
  "Where do we have uncapped liability?",
  "Summarize my confidentiality obligations",
];

export default function BrainPage() {
  const { notify } = useToast();
  const [question, setQuestion] = useState("");
  const [scope, setScope] = useState<BrainScope>("portfolio");
  const [projectId, setProjectId] = useState("");
  const [contractId, setContractId] = useState("");
  const [busy, setBusy] = useState(false);
  const [findBusy, setFindBusy] = useState(false);
  const [result, setResult] = useState<BrainQuery | null>(null);
  const [sources, setSources] = useState<BrainSearchResponse | null>(null);

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

  const idle = !busy && !findBusy && !result && !sources;

  async function ask(q?: string) {
    const text = (q ?? question).trim();
    if (text.length < 3) return;
    if (q) setQuestion(q);
    if (scope === "project" && !projectId)
      return notify("Select a project for project scope", "error");
    if (scope === "contract" && !contractId)
      return notify("Select a contract for contract scope", "error");
    setBusy(true);
    setSources(null);
    setResult(null);
    try {
      const res = await brainApi.ask({
        question: text,
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

  async function find() {
    const text = question.trim();
    if (text.length < 2) return;
    setFindBusy(true);
    setResult(null);
    setSources(null);
    try {
      setSources(await brainApi.search(text));
    } catch (e) {
      notify(e instanceof Error ? e.message : "Search failed", "error");
    } finally {
      setFindBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      {/* ---- Hero + unified console ---------------------------------- */}
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-md bg-brand-50 text-brand-600">
          <BrainIcon className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-[20px] font-semibold leading-tight tracking-[-0.01em] text-slate-900">
            Contract Brain
          </h1>
          <p className="mt-0.5 text-sm text-slate-500">
            Ask anything about your contracts — answered from your portfolio,
            with sources you can open.
          </p>
        </div>
      </div>

      <div className="space-y-3">
        <div className="flex items-center gap-1.5 rounded-md border border-slate-200 bg-slate-100 p-1.5 transition-colors focus-within:border-brand-400 focus-within:ring-1 focus-within:ring-brand-400">
          <select
            value={scope}
            onChange={(e) => setScope(e.target.value as BrainScope)}
            className="shrink-0 rounded bg-transparent px-2 py-2 text-sm font-medium text-slate-600 focus:outline-none"
            aria-label="Scope"
          >
            <option value="portfolio">Portfolio</option>
            <option value="project">Project</option>
            <option value="contract">Contract</option>
          </select>
          <div className="h-6 w-px shrink-0 bg-slate-200" />
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask()}
            placeholder="Ask a question, or describe a clause to find…"
            className="min-w-0 flex-1 bg-transparent px-2 py-2 text-sm text-slate-900 placeholder:text-slate-500 focus:outline-none"
          />
          <Button
            onClick={() => ask()}
            loading={busy}
            disabled={findBusy}
            className="shrink-0 rounded"
          >
            <Send className="h-4 w-4" />
            Ask
          </Button>
        </div>

        {/* second-line scope target */}
        {scope === "project" && (
          <select
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            className="w-full rounded border border-slate-300 bg-slate-100 px-3 py-2 text-sm text-slate-700 focus:border-brand-400 focus:outline-none focus:ring-1 focus:ring-brand-400"
          >
            <option value="">Select a project…</option>
            {(projects ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        )}
        {scope === "contract" && (
          <select
            value={contractId}
            onChange={(e) => setContractId(e.target.value)}
            className="w-full rounded border border-slate-300 bg-slate-100 px-3 py-2 text-sm text-slate-700 focus:border-brand-400 focus:outline-none focus:ring-1 focus:ring-brand-400"
          >
            <option value="">Select a contract…</option>
            {(contracts ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </select>
        )}

        {/* helper row: what "Ask" vs "Find" mean, in one place */}
        <div className="flex items-center justify-between px-1 text-xs text-slate-400">
          <span>
            <span className="font-medium text-slate-500">Ask</span> gives a
            grounded answer.
          </span>
          <button
            onClick={find}
            disabled={findBusy || busy || question.trim().length < 2}
            className="inline-flex items-center gap-1 font-medium text-slate-500 hover:text-brand-600 disabled:opacity-40"
          >
            {findBusy ? (
              "Finding…"
            ) : (
              <>
                <SearchIcon className="h-3.5 w-3.5" />
                Find sources only
              </>
            )}
          </button>
        </div>
      </div>

      {/* ---- Results ------------------------------------------------- */}
      {busy && <CenterSpinner label="Reasoning across your contracts…" />}
      {findBusy && <CenterSpinner label="Searching the contract graph…" />}
      {result && !busy && <AnswerCard query={result} />}
      {sources && !findBusy && <SourcesPanel res={sources} />}

      {/* ---- Idle: suggestions + recent ------------------------------ */}
      {idle && (
        <div className="space-y-4">
          <div className="space-y-2">
            <p className="flex items-center gap-1.5 px-1 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
              <Sparkles className="h-3.5 w-3.5" />
              Try asking
            </p>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => ask(s)}
                  className="rounded-full border border-slate-200 bg-slate-100 px-3 py-1.5 text-sm text-slate-600 transition-colors hover:border-brand-300 hover:bg-brand-50/50 hover:text-brand-700"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <p className="flex items-center gap-1.5 px-1 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
              <History className="h-3.5 w-3.5" />
              Recent questions
            </p>
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
              <Card>
                <ul className="divide-y divide-slate-100">
                  {recent.map((q) => (
                    <li key={q.id}>
                      <button
                        onClick={() => {
                          setSources(null);
                          setResult(q);
                          setQuestion(q.question);
                          setScope(q.query_scope);
                          window.scrollTo({ top: 0, behavior: "smooth" });
                        }}
                        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-slate-100"
                      >
                        <span className="flex-1 text-sm text-slate-800">
                          {q.question}
                        </span>
                        <Badge tone="blue">{titleCase(q.query_scope)}</Badge>
                        <span className="w-16 shrink-0 text-right text-xs text-slate-400">
                          {fmtRelative(q.created_at)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** A single tappable source row — shared by answer citations & search hits. */
function SourceRow({
  contractId,
  title,
  excerpt,
  badge,
  meta,
}: {
  contractId?: string;
  title?: string;
  excerpt: string;
  badge?: React.ReactNode;
  meta?: string;
}) {
  const inner = (
    <>
      <div className="mb-1 flex flex-wrap items-center gap-2">
        {badge}
        {title && (
          <span className="text-sm font-medium text-slate-900">{title}</span>
        )}
        {meta && <span className="text-xs text-slate-400">{meta}</span>}
        {contractId && (
          <ArrowUpRight className="ml-auto h-3.5 w-3.5 text-slate-300 group-hover:text-brand-500" />
        )}
      </div>
      <p className="line-clamp-3 text-sm leading-relaxed text-slate-600">
        {excerpt}
      </p>
    </>
  );
  const base =
    "group block rounded-md border border-slate-200 bg-slate-100 p-3 transition-colors";
  return contractId ? (
    <Link
      href={`/contracts/${contractId}`}
      className={cn(base, "hover:border-brand-300 hover:bg-brand-50/40")}
    >
      {inner}
    </Link>
  ) : (
    <div className={base}>{inner}</div>
  );
}

function AnswerCard({ query }: { query: BrainQuery }) {
  const m = query.retrieval_metadata;
  const retrievalBits = [
    m.source_count && `${m.source_count} sources`,
    m.vector_chunks && `${m.vector_chunks} passages`,
    m.fulltext_clauses && `${m.fulltext_clauses} clauses`,
  ].filter(Boolean) as string[];
  const cites: Citation[] = query.citations ?? [];
  const sources: BrainSearchResponse = {
    query: query.question,
    semantic: m.sources?.semantic ?? [],
    clauses: m.sources?.clauses ?? [],
    text: m.sources?.text ?? [],
  };
  const hasSources =
    sources.semantic.length > 0 ||
    sources.clauses.length > 0 ||
    sources.text.length > 0;

  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <div className="flex h-7 w-7 items-center justify-center rounded bg-brand-50 text-brand-600">
                <Sparkles className="h-4 w-4" />
              </div>
              Answer
            </div>
            <div className="flex items-center gap-2">
              {typeof m.total_citations === "number" &&
                m.total_citations > 0 && (
                  <span
                    className="text-xs text-slate-400"
                    title="Citations that verify verbatim against the retrieved sources"
                  >
                    {m.verified_citations ?? 0}/{m.total_citations} verified
                  </span>
                )}
              <span className="text-xs text-slate-400">Confidence</span>
              <Badge tone={statusTone(m.confidence)}>
                {titleCase(m.confidence)}
              </Badge>
            </div>
          </div>

          <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-slate-800">
            {query.answer}
          </p>

          {m.limitations && (
            <div className="rounded-md border border-warning/30 bg-warning-subtle px-3 py-2.5 text-sm text-warning">
              {m.limitations}
            </div>
          )}

          {retrievalBits.length > 0 && (
            <p className="flex items-center gap-1.5 text-xs text-slate-400">
              <Layers className="h-3.5 w-3.5" />
              Grounded in {retrievalBits.join(" · ")}
            </p>
          )}
        </CardBody>
      </Card>

      {/* The answer is built from exactly these sources — identical to what
          "Find sources only" returns for the same query. */}
      {hasSources ? (
        <div className="space-y-2">
          <p className="flex items-center gap-1.5 px-1 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
            <Quote className="h-3.5 w-3.5" />
            Sources · the answer is built from these
          </p>
          <SourcesPanel res={sources} />
        </div>
      ) : cites.length > 0 ? (
        <div className="space-y-2">
          <p className="flex items-center gap-1.5 px-1 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
            <Quote className="h-3.5 w-3.5" />
            Sources ({cites.length})
          </p>
          <div className="space-y-2">
            {cites.map((c, i) => (
              <SourceRow
                key={i}
                contractId={c.contract_id}
                excerpt={c.quote ?? c.excerpt ?? "—"}
                meta={c.label ?? undefined}
                badge={
                  c.validation_status === "valid" ? (
                    <Badge tone="green">Verified</Badge>
                  ) : undefined
                }
              />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function SourcesPanel({ res }: { res: BrainSearchResponse }) {
  const empty =
    res.semantic.length === 0 &&
    res.clauses.length === 0 &&
    res.text.length === 0;
  if (empty) {
    return (
      <EmptyState
        icon={<SearchIcon className="h-6 w-6" />}
        title="No matches"
        description={`Nothing in your portfolio matched “${res.query}”.`}
      />
    );
  }
  return (
    <div className="space-y-5">
      {res.semantic.length > 0 && (
        <Section
          icon={<Sparkles className="h-3.5 w-3.5" />}
          title="Closest in meaning"
          hint="semantic — not just keywords"
        >
          {res.semantic.map((s, i) => (
            <SourceRow
              key={i}
              contractId={s.contract_id}
              title={s.contract_title}
              excerpt={`…${s.text}…`}
              badge={<Badge tone="blue">{Math.round(s.score * 100)}%</Badge>}
            />
          ))}
        </Section>
      )}
      {res.clauses.length > 0 && (
        <Section
          icon={<Layers className="h-3.5 w-3.5" />}
          title="Clauses"
          hint="extracted & classified"
        >
          {res.clauses.map((c) => (
            <SourceRow
              key={c.clause_id}
              contractId={c.contract_id}
              title={c.contract_title}
              excerpt={c.excerpt}
              meta={c.heading ?? undefined}
              badge={<Badge tone="blue">{titleCase(c.clause_type)}</Badge>}
            />
          ))}
        </Section>
      )}
      {res.text.length > 0 && (
        <Section
          icon={<FileText className="h-3.5 w-3.5" />}
          title="Text matches"
          hint="full-text, ranked"
        >
          {res.text.map((t, i) => (
            <SourceRow
              key={i}
              contractId={t.contract_id}
              title={t.contract_title}
              excerpt={t.matches.map((m) => `…${m.excerpt}…`).join("\n")}
            />
          ))}
        </Section>
      )}
    </div>
  );
}

function Section({
  icon,
  title,
  hint,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-2">
      <p className="flex items-center gap-1.5 px-1 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
        {icon}
        {title}
        <span className="font-normal normal-case text-slate-300">· {hint}</span>
      </p>
      <div className="space-y-2">{children}</div>
    </div>
  );
}
