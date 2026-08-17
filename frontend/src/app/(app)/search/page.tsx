"use client";

// Global search — contracts / full text / clauses / projects (new mockup style, scoped `.srch`).
// Pure visual reskin: every query fn, param, and result field below is unchanged from the old version.

import { useState } from "react";
import Link from "next/link";
import {
  Search as SearchIcon,
  ArrowRight,
  FileText,
  AlignLeft,
  Quote,
  FolderKanban,
  Loader2,
} from "lucide-react";
import { searchApi } from "@/lib/endpoints";
import { titleCase } from "@/lib/utils";
import type {
  ClauseSearchResult,
  ContractResponse,
  ContractTextSearchResult,
  ProjectResponse,
} from "@/lib/types";

type TabId = "contracts" | "text" | "clauses" | "projects";

const TABS: { id: TabId; label: string; icon: typeof FileText }[] = [
  { id: "contracts", label: "Contracts", icon: FileText },
  { id: "text", label: "Full text", icon: AlignLeft },
  { id: "clauses", label: "Clauses", icon: Quote },
  { id: "projects", label: "Projects", icon: FolderKanban },
];

export default function SearchPage() {
  const [tab, setTab] = useState<TabId>("contracts");

  return (
    <div className="srch">
      <style dangerouslySetInnerHTML={{ __html: SRCH_CSS }} />
      <div className="hd">
        <div>
          <h1>Search</h1>
          <p className="sub">Look up contracts and projects by name, metadata and dates.</p>
        </div>
      </div>

      <div className="tabs">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              className={`tab${tab === t.id ? " on" : ""}`}
              onClick={() => setTab(t.id)}
            >
              <Icon className="ti" />
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === "contracts" && <ContractsTab />}
      {tab === "text" && <TextTab />}
      {tab === "clauses" && <ClausesTab />}
      {tab === "projects" && <ProjectsTab />}

      <p className="foot">
        Looking for something <em>inside</em> your contracts — clauses, wording,
        concepts? Use{" "}
        <Link href="/brain" className="link">
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
    <div className="pane">
      <div className="bar wide">
        <div className="fld grow">
          <label>Query</label>
          <div className="inputbar">
            <SearchIcon className="ic" />
            <input
              placeholder="Title, type…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && search()}
            />
          </div>
        </div>
        <div className="fld">
          <label>Stage</label>
          <input
            className="plain"
            placeholder="e.g. active"
            value={stage}
            onChange={(e) => setStage(e.target.value)}
          />
        </div>
        <div className="fld">
          <label>Risk</label>
          <input
            className="plain"
            placeholder="e.g. high"
            value={risk}
            onChange={(e) => setRisk(e.target.value)}
          />
        </div>
        <div className="fld">
          <label>Counterparty</label>
          <input
            className="plain"
            value={counterparty}
            onChange={(e) => setCounterparty(e.target.value)}
          />
        </div>
        <button className="btn pri" onClick={search} disabled={loading}>
          {loading ? <Loader2 className="ic spin" /> : <SearchIcon className="ic" />}
          Search
        </button>
      </div>

      {loading ? (
        <CenterLoading />
      ) : error ? (
        <ErrorPanel error={error} />
      ) : results === null ? null : results.length === 0 ? (
        <NoResults />
      ) : (
        <div className="rlist">
          {results.map((c) => (
            <Link key={c.id} href={`/contracts/${c.id}`} className="row">
              <FileText className="rico" />
              <div className="rmain">
                <div className="rtitle">{c.title}</div>
                <div className="rmeta">
                  {c.counterparty_name ?? "—"}
                  {c.contract_type ? ` · ${titleCase(c.contract_type)}` : ""}
                </div>
              </div>
              <span className="pill blue">{titleCase(c.lifecycle_stage)}</span>
              {c.risk_level && <span className="pill slate">{titleCase(c.risk_level)}</span>}
              <ArrowRight className="rarr" />
            </Link>
          ))}
        </div>
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
    <div className="pane">
      <div className="bar">
        <div className="fld grow">
          <label>Exact words or phrases</label>
          <div className="inputbar">
            <SearchIcon className="ic" />
            <input
              placeholder="termination for convenience"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && search()}
            />
          </div>
          <p className="hint">Searches the latest text of every contract you can read.</p>
        </div>
        <button className="btn pri" onClick={search} disabled={loading}>
          {loading ? <Loader2 className="ic spin" /> : <SearchIcon className="ic" />}
          Search
        </button>
      </div>

      {loading ? (
        <CenterLoading />
      ) : error ? (
        <ErrorPanel error={error} />
      ) : results === null ? null : results.length === 0 ? (
        <NoResults />
      ) : (
        <div className="clist">
          {results.map((r) => (
            <div key={`${r.contract_id}-${r.text_snapshot_id}`} className="ccard">
              <div className="cch">
                <Link href={`/contracts/${r.contract_id}`} className="ctitle">
                  {r.contract_title}
                </Link>
                <span className="pill slate">
                  {r.matches.length} match{r.matches.length === 1 ? "" : "es"}
                </span>
              </div>
              <ul className="snips">
                {r.matches.slice(0, 3).map((m, i) => (
                  <li key={i}>…{m.excerpt}…</li>
                ))}
              </ul>
            </div>
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
    <div className="pane">
      <div className="bar wide">
        <div className="fld grow">
          <label>Wording</label>
          <div className="inputbar">
            <SearchIcon className="ic" />
            <input
              placeholder="auto-renew, uncapped liability…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && search()}
            />
          </div>
        </div>
        <div className="fld">
          <label>Clause type</label>
          <select
            className="plain"
            value={clauseType}
            onChange={(e) => setClauseType(e.target.value)}
          >
            <option value="">Any type</option>
            {CLAUSE_TYPE_OPTIONS.map((t) => (
              <option key={t} value={t}>
                {titleCase(t)}
              </option>
            ))}
          </select>
        </div>
        <button className="btn pri" onClick={search} disabled={loading}>
          {loading ? <Loader2 className="ic spin" /> : <SearchIcon className="ic" />}
          Search
        </button>
      </div>

      {loading ? (
        <CenterLoading />
      ) : error ? (
        <ErrorPanel error={error} />
      ) : results === null ? null : results.length === 0 ? (
        <NoResults />
      ) : (
        <div className="rlist">
          {results.map((r) => (
            <Link key={r.clause_id} href={`/contracts/${r.contract_id}`} className="row">
              <Quote className="rico" />
              <div className="rmain">
                <div className="rtitle">{r.contract_title}</div>
                <div className="rmeta excerpt">{r.excerpt}</div>
              </div>
              <span className="pill blue">{titleCase(r.clause_type)}</span>
              <span className="conf">{Math.round(r.confidence * 100)}%</span>
            </Link>
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
    <div className="pane">
      <div className="bar">
        <div className="fld grow">
          <label>Query</label>
          <div className="inputbar">
            <SearchIcon className="ic" />
            <input
              placeholder="Project name or description…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && search()}
            />
          </div>
        </div>
        <button className="btn pri" onClick={search} disabled={loading}>
          {loading ? <Loader2 className="ic spin" /> : <SearchIcon className="ic" />}
          Search
        </button>
      </div>

      {loading ? (
        <CenterLoading />
      ) : error ? (
        <ErrorPanel error={error} />
      ) : results === null ? null : results.length === 0 ? (
        <NoResults />
      ) : (
        <div className="grid">
          {results.map((p) => (
            <Link key={p.id} href={`/projects/${p.id}`} className="pcard">
              <div className="pch">
                <h3>{p.name}</h3>
                <ArrowRight className="rarr" />
              </div>
              <span className="pill slate">{titleCase(p.project_type)}</span>
              {p.description && <p className="pdesc">{p.description}</p>}
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
    <div className="state">
      <Loader2 className="statei spin" />
      <p>Searching…</p>
    </div>
  );
}

function NoResults() {
  return (
    <div className="state">
      <SearchIcon className="statei" />
      <p className="t">No results</p>
      <p>Try a different query or adjust your filters.</p>
    </div>
  );
}

function ErrorPanel({ error }: { error: unknown }) {
  return (
    <div className="state err">
      <p className="t">Something went wrong</p>
      <p>{error instanceof Error ? error.message : "Couldn't run this search."}</p>
    </div>
  );
}

const SRCH_CSS = `
.srch{--surface:#fff;--surface-2:#eef1f6;--inset:#f8fafc;--ink:#18213a;--ink-2:#586178;--ink-3:#8a92a6;--border:#e4e8f0;--border-strong:#ccd3e0;--accent:#3b4aa0;--accent-ink:#fff;--accent-soft:#eaecf8;--good:#2f875f;--good-soft:#e4f1ea;--crit:#bb4835;--crit-soft:#f7e8e4;--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,monospace;padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .srch{--surface:#141922;--surface-2:#1b2130;--inset:#10141d;--ink:#e8ebf3;--ink-2:#9aa3b8;--ink-3:#6b7488;--border:#242b39;--border-strong:#333c4e;--accent:#8290e6;--accent-ink:#0c0f16;--accent-soft:#1f2740;--good:#5cbf90;--good-soft:#15271f;--crit:#e08a76;--crit-soft:#2c1c17;--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4)}
.srch .hd{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:16px}
.srch .hd h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.015em} .srch .hd .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:640px}
.srch .tabs{display:flex;gap:6px;border-bottom:1px solid var(--border);margin-bottom:18px;flex-wrap:wrap}
.srch .tab{display:inline-flex;align-items:center;gap:6px;padding:9px 13px;border:none;background:none;color:var(--ink-2);font:600 12.5px var(--sans);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
.srch .tab:hover{color:var(--ink)} .srch .tab.on{color:var(--accent);border-bottom-color:var(--accent)}
.srch .tab .ti{width:14px;height:14px}
.srch .pane{display:flex;flex-direction:column;gap:16px}
.srch .bar{display:flex;align-items:flex-end;gap:10px;flex-wrap:wrap;border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);padding:14px 16px}
.srch .fld{display:flex;flex-direction:column;gap:5px;width:170px}
.srch .fld.grow{flex:1;min-width:220px;width:auto}
.srch .fld label{font:600 10.5px var(--sans);letter-spacing:.02em;color:var(--ink-3);text-transform:uppercase}
.srch .fld .hint{margin:0;font-size:11.5px;color:var(--ink-3)}
.srch .plain{height:36px;border:1px solid var(--border-strong);border-radius:9px;background:var(--inset);color:var(--ink);font-size:12.5px;padding:0 10px;outline:none}
.srch .plain:focus{border-color:var(--accent)}
.srch .inputbar{display:flex;align-items:center;gap:8px;height:36px;border:1px solid var(--border-strong);border-radius:9px;background:var(--inset);padding:0 11px}
.srch .inputbar:focus-within{border-color:var(--accent)}
.srch .inputbar .ic{width:15px;height:15px;color:var(--ink-3);flex:none}
.srch .inputbar input{flex:1;border:none;background:none;color:var(--ink);font-size:12.5px;outline:none}
.srch .btn{display:inline-flex;align-items:center;gap:6px;height:36px;padding:0 16px;border-radius:9px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;font-size:12.5px;cursor:pointer}
.srch .btn .ic{width:14px;height:14px}
.srch .btn:hover{background:var(--surface-2)} .srch .btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)} .srch .btn.pri:hover{filter:brightness(1.06)} .srch .btn[disabled]{opacity:.65;pointer-events:none}
.srch .spin{animation:srchspin .8s linear infinite} @keyframes srchspin{to{transform:rotate(360deg)}}

.srch .rlist{display:flex;flex-direction:column;gap:8px}
.srch .row{display:flex;align-items:center;gap:12px;border:1px solid var(--border);border-radius:12px;background:var(--surface);box-shadow:var(--shadow);padding:12px 14px;text-decoration:none;color:inherit;transition:.12s}
.srch .row:hover{border-color:var(--accent);transform:translateY(-1px)}
.srch .rico{width:17px;height:17px;color:var(--ink-3);flex:none}
.srch .rmain{flex:1;min-width:0}
.srch .rtitle{font-weight:650;font-size:13px;color:var(--ink)}
.srch .rmeta{font-size:12px;color:var(--ink-2);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.srch .rmeta.excerpt{white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.srch .rarr{width:15px;height:15px;color:var(--ink-3);flex:none}
.srch .conf{font:600 12px var(--mono);color:var(--ink-2);flex:none;font-variant-numeric:tabular-nums}

.srch .pill{font:600 10px var(--sans);letter-spacing:.02em;padding:3px 9px;border-radius:99px;flex:none;white-space:nowrap}
.srch .pill.blue{background:var(--accent-soft);color:var(--accent)} .srch .pill.slate{background:var(--surface-2);color:var(--ink-2)}

.srch .clist{display:flex;flex-direction:column;gap:10px}
.srch .ccard{border:1px solid var(--border);border-radius:12px;background:var(--surface);box-shadow:var(--shadow);padding:13px 15px}
.srch .cch{display:flex;align-items:center;justify-content:space-between;gap:10px}
.srch .ctitle{font-weight:650;font-size:13px;color:var(--ink);text-decoration:none} .srch .ctitle:hover{color:var(--accent)}
.srch .snips{list-style:none;margin:9px 0 0;padding:0;display:flex;flex-direction:column;gap:6px}
.srch .snips li{border:1px solid var(--border);border-radius:8px;background:var(--inset);padding:8px 11px;font-size:12.5px;line-height:1.55;color:var(--ink-2)}

.srch .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.srch .pcard{display:flex;flex-direction:column;gap:8px;border:1px solid var(--border);border-radius:12px;background:var(--surface);box-shadow:var(--shadow);padding:14px 15px;text-decoration:none;color:inherit;transition:.12s}
.srch .pcard:hover{border-color:var(--accent);transform:translateY(-1px)}
.srch .pch{display:flex;align-items:center;justify-content:space-between;gap:8px}
.srch .pch h3{margin:0;font-size:13.5px;font-weight:650;color:var(--ink)}
.srch .pdesc{margin:0;font-size:12.5px;color:var(--ink-2);line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}

.srch .state{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;padding:52px 20px;text-align:center;color:var(--ink-3);border:1px dashed var(--border-strong);border-radius:12px;background:var(--inset)}
.srch .state .statei{width:22px;height:22px;color:var(--ink-3);margin-bottom:4px}
.srch .state .t{font-weight:650;font-size:13px;color:var(--ink-2)}
.srch .state p{margin:0;font-size:12.5px}
.srch .state.err{border-color:color-mix(in srgb,var(--crit) 40%,var(--border));background:var(--crit-soft)}
.srch .state.err .t{color:var(--crit)}

.srch .foot{margin-top:22px;font-size:12.5px;color:var(--ink-3)}
.srch .foot .link{color:var(--accent);font-weight:600;text-decoration:none} .srch .foot .link:hover{text-decoration:underline}
`;
