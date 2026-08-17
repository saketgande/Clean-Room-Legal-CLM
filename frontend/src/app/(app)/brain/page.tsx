"use client";

// Contract Brain — intelligence workspace (scoped `.brn`, mockup screen-16).
// Full-height layout: a top bar with portfolio-index stats, a full-width ask
// console (scope + Ask/Find), and a two-column body — the answer/sources result
// on the left, a rail (portfolio grounding + suggested + recent) on the right.
// Same data flow as before: brainApi.ask/search/queries. Pure visual redesign.

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { brainApi, contractsApi, projectsApi } from "@/lib/endpoints";
import { fmtRelative, titleCase } from "@/lib/utils";
import { Markdown } from "@/components/markdown";
import { useToast } from "@/components/toast";
import type { BrainQuery, BrainScope, BrainSearchResponse, Citation } from "@/lib/types";

const svg = (p: string) => <svg className="ic" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: p }} />;
const SUGGESTIONS = [
  "Which contracts auto-renew without 60 days notice?",
  "Where do we have uncapped liability?",
  "Summarize my confidentiality obligations",
  "Which agreements allow assignment without consent?",
];
const CONF_TONE: Record<string, string> = { high: "good", medium: "warn", low: "crit" };

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

  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list });
  const { data: contracts } = useQuery({ queryKey: ["contracts"], queryFn: contractsApi.list });
  const { data: recent, isLoading: recentLoading, error: recentError } = useQuery({ queryKey: ["brain-queries"], queryFn: () => brainApi.queries({ limit: 25 }) });

  const idle = !busy && !findBusy && !result && !sources;
  const contractsCount = contracts?.length ?? 0;
  const weekAgo = Date.now() - 7 * 86_400_000;
  const askedThisWeek = (recent ?? []).filter((q) => new Date(q.created_at).getTime() >= weekAgo).length;

  async function ask(q?: string) {
    const text = (q ?? question).trim();
    if (text.length < 3) return;
    if (q) setQuestion(q);
    if (scope === "project" && !projectId) return notify("Select a project for project scope", "error");
    if (scope === "contract" && !contractId) return notify("Select a contract for contract scope", "error");
    setBusy(true); setSources(null); setResult(null);
    try {
      const res = await brainApi.ask({ question: text, query_scope: scope, project_id: scope === "project" ? projectId : undefined, contract_id: scope === "contract" ? contractId : undefined });
      setResult(res);
    } catch (e) { notify(e instanceof Error ? e.message : "Query failed", "error"); }
    finally { setBusy(false); }
  }

  async function find() {
    const text = question.trim();
    if (text.length < 2) return;
    setFindBusy(true); setResult(null); setSources(null);
    try { setSources(await brainApi.search(text)); }
    catch (e) { notify(e instanceof Error ? e.message : "Search failed", "error"); }
    finally { setFindBusy(false); }
  }

  function loadRecent(q: BrainQuery) {
    setSources(null);
    setResult(q);
    setQuestion(q.question);
    setScope(q.query_scope);
    document.querySelector(".brn .body")?.scrollTo({ top: 0, behavior: "smooth" });
  }

  return (
    <div className="brn">
      <style dangerouslySetInnerHTML={{ __html: BRN_CSS }} />

      <div className="top">
        <div className="hic">{svg('<path d="M12 5a3 3 0 0 0-6 0 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 0M12 5a3 3 0 0 1 6 0 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 0M12 5v14"/>')}</div>
        <div>
          <h1>Contract Brain</h1>
          <p className="sub">Ask anything about your contracts — answered from your portfolio, with sources you can open.</p>
        </div>
        <div className="sp" />
        <div className="idxstat">
          <span><b>{contractsCount}</b> contracts</span>
          {askedThisWeek > 0 && <span><b>{askedThisWeek}</b> asked this week</span>}
        </div>
      </div>

      <div className="body">
        <div className="wrap">
          <div className="console">
            <div className="cbar">
              <select className="scopesel" value={scope} onChange={(e) => setScope(e.target.value as BrainScope)} aria-label="Scope">
                <option value="portfolio">Portfolio</option>
                <option value="project">Project</option>
                <option value="contract">Contract</option>
              </select>
              <div className="cdiv" />
              <input className="cin" value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask()} placeholder="Ask a question, or describe a clause to find…" />
              <button className="btn pri" disabled={findBusy} onClick={() => ask()}>{busy ? "Asking…" : <>{svg('<path d="M22 2 11 13M22 2l-7 20-4-9-9-4z"/>')}Ask</>}</button>
            </div>

            {scope === "project" && (
              <select className="targetsel" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                <option value="">Select a project…</option>
                {(projects ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            )}
            {scope === "contract" && (
              <select className="targetsel" value={contractId} onChange={(e) => setContractId(e.target.value)}>
                <option value="">Select a contract…</option>
                {(contracts ?? []).map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
              </select>
            )}

            <div className="crow">
              <span><b>Ask</b> gives a grounded answer across your portfolio.</span>
              <button className="findbtn" disabled={findBusy || busy || question.trim().length < 2} onClick={find}>
                {findBusy ? "Finding…" : <>{svg('<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>')}Find sources only</>}
              </button>
            </div>
          </div>

          <div className="work">
            <div className="colmain">
              {busy && <div className="loading">{svg('<circle cx="12" cy="12" r="9" opacity=".3"/><path d="M12 3a9 9 0 0 1 9 9"/>')}Reasoning across your contracts…</div>}
              {findBusy && <div className="loading">{svg('<circle cx="12" cy="12" r="9" opacity=".3"/><path d="M12 3a9 9 0 0 1 9 9"/>')}Searching the contract graph…</div>}
              {result && !busy && <AnswerCard query={result} />}
              {sources && !findBusy && <SourcesPanel res={sources} />}
              {idle && (
                <div className="idlemain">
                  <div className="idleic">{svg('<path d="M12 5a3 3 0 0 0-6 0 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 0M12 5a3 3 0 0 1 6 0 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 0M12 5v14"/>')}</div>
                  <p className="idlet">Ask anything about your portfolio</p>
                  <p className="idled">Get a grounded answer with citations, or find the exact source clauses. Type a question above, or pick a suggestion on the right.</p>
                </div>
              )}
            </div>

            <aside className="rail">
              <div className="railcard">
                <h3>{svg('<rect x="3" y="4" width="18" height="6" rx="1"/><rect x="3" y="14" width="18" height="6" rx="1"/>')}Portfolio grounding</h3>
                <div className="gtiles">
                  <div className="gtile"><div className="gn">{contractsCount}</div><div className="gl">contracts indexed</div></div>
                  <div className="gtile"><div className="gn">{askedThisWeek}</div><div className="gl">asked this week</div></div>
                </div>
              </div>

              <div className="railcard">
                <h3>{svg('<path d="M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2z"/>')}Suggested</h3>
                <div className="suggest">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} onClick={() => ask(s)}>
                      {svg('<path d="M5 12h14M13 6l6 6-6 6"/>')}
                      <span>{s}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div className="railcard">
                <h3>{svg('<path d="M3 3v5h5"/><path d="M3.05 13A9 9 0 1 0 6 5.3L3 8"/><path d="M12 7v5l4 2"/>')}Recent questions</h3>
                {recentLoading ? (
                  <div className="dim" style={{ fontSize: 12.5 }}>Loading…</div>
                ) : recentError ? (
                  <div className="empty err">{recentError instanceof Error ? recentError.message : "Couldn't load recent questions."}</div>
                ) : !recent?.length ? (
                  <div className="empty">No questions yet. Ask your first to start building portfolio memory.</div>
                ) : (
                  <ul className="recentlist">
                    {recent.slice(0, 8).map((q) => (
                      <li key={q.id}>
                        <button onClick={() => loadRecent(q)}>
                          <span className="rq">{q.question}</span>
                          <span className="rmeta">
                            <span className="tag blue">{titleCase(q.query_scope)}</span>
                            <span className="rt">{fmtRelative(q.created_at)}</span>
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </aside>
          </div>
        </div>
      </div>
    </div>
  );
}

function SourceRow({ contractId, title, excerpt, badge, meta }: { contractId?: string; title?: string; excerpt: string; badge?: React.ReactNode; meta?: string }) {
  const inner = (
    <>
      <div className="srh">{badge}{title ? <span className="srt">{title}</span> : null}{meta ? <span className="srm">{meta}</span> : null}{contractId ? <span className="sra">{svg('<path d="M7 17 17 7M7 7h10v10"/>')}</span> : null}</div>
      <p className="sre">{excerpt}</p>
    </>
  );
  return contractId ? <Link href={`/contracts/${contractId}`} className="srow link">{inner}</Link> : <div className="srow">{inner}</div>;
}

function AnswerCard({ query }: { query: BrainQuery }) {
  const m = query.retrieval_metadata;
  const retrievalBits = [m.source_count && `${m.source_count} sources`, m.vector_chunks && `${m.vector_chunks} passages`, m.fulltext_clauses && `${m.fulltext_clauses} clauses`].filter(Boolean) as string[];
  const cites: Citation[] = query.citations ?? [];
  const sources: BrainSearchResponse = { query: query.question, semantic: m.sources?.semantic ?? [], clauses: m.sources?.clauses ?? [], text: m.sources?.text ?? [] };
  const hasSources = sources.semantic.length > 0 || sources.clauses.length > 0 || sources.text.length > 0;

  return (
    <div className="result">
      <div className="answercard">
        <div className="ach">
          <div className="aic">{svg('<path d="M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2z"/>')}</div>
          <b>Answer</b>
          <div style={{ flex: 1 }} />
          {typeof m.total_citations === "number" && m.total_citations > 0 ? <span className="dim" style={{ fontSize: 11 }}>{m.verified_citations ?? 0}/{m.total_citations} verified</span> : null}
          <span className="dim" style={{ fontSize: 11 }}>Confidence</span>
          <span className={`tag ${CONF_TONE[m.confidence] ?? "slate"}`}>{titleCase(m.confidence)}</span>
        </div>
        <p className="qecho"><b>Q</b> · {query.question} <span className="dim">· {titleCase(query.query_scope)} scope</span></p>
        <div className="atext"><Markdown>{query.answer}</Markdown></div>
        {m.limitations ? <div className="alimit">{m.limitations}</div> : null}
        {retrievalBits.length > 0 ? <p className="agrounded">{svg('<rect x="3" y="4" width="18" height="6" rx="1"/><rect x="3" y="14" width="18" height="6" rx="1"/>')}Grounded in {retrievalBits.join(" · ")}{typeof m.verified_citations === "number" && m.verified_citations > 0 ? <> · <span className="v">{m.verified_citations}/{m.total_citations} verified</span></> : null}</p> : null}
      </div>
      {hasSources ? (
        <div className="sec"><div className="seclbl">{svg('<path d="M3 21c0-4 3-7 7-7"/><path d="M14 3a4 4 0 0 0-4 4v3h4"/>')}Sources · the answer is built from these</div><SourcesPanel res={sources} bare /></div>
      ) : cites.length > 0 ? (
        <div className="sec"><div className="seclbl">Sources ({cites.length})</div>
          <div className="srclist">{cites.map((c, i) => <SourceRow key={i} contractId={c.contract_id} excerpt={c.quote ?? c.excerpt ?? "—"} meta={c.label ?? undefined} badge={c.validation_status === "valid" ? <span className="tag good">Verified</span> : undefined} />)}</div>
        </div>
      ) : null}
    </div>
  );
}

function SourcesPanel({ res, bare }: { res: BrainSearchResponse; bare?: boolean }) {
  const empty = res.semantic.length === 0 && res.clauses.length === 0 && res.text.length === 0;
  if (empty) return <div className="empty">Nothing in your portfolio matched &ldquo;{res.query}&rdquo;.</div>;
  const body = (
    <>
      {res.semantic.length > 0 && (
        <div className="sec"><div className="seclbl">{svg('<path d="M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2z"/>')}Closest in meaning<span className="hintdim"> · semantic — not just keywords</span></div>
          <div className="srclist">{res.semantic.map((s, i) => <SourceRow key={i} contractId={s.contract_id} title={s.contract_title} excerpt={`…${s.text}…`} badge={<span className="tag blue">{Math.round(s.score * 100)}%</span>} />)}</div>
        </div>
      )}
      {res.clauses.length > 0 && (
        <div className="sec"><div className="seclbl">{svg('<rect x="3" y="4" width="18" height="6" rx="1"/><rect x="3" y="14" width="18" height="6" rx="1"/>')}Clauses<span className="hintdim"> · extracted &amp; classified</span></div>
          <div className="srclist">{res.clauses.map((c) => <SourceRow key={c.clause_id} contractId={c.contract_id} title={c.contract_title} excerpt={c.excerpt} meta={c.heading ?? undefined} badge={<span className="tag blue">{titleCase(c.clause_type)}</span>} />)}</div>
        </div>
      )}
      {res.text.length > 0 && (
        <div className="sec"><div className="seclbl">{svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>')}Text matches<span className="hintdim"> · full-text, ranked</span></div>
          <div className="srclist">{res.text.map((t, i) => <SourceRow key={i} contractId={t.contract_id} title={t.contract_title} excerpt={t.matches.map((mm) => `…${mm.excerpt}…`).join("\n")} />)}</div>
        </div>
      )}
    </>
  );
  return bare ? body : <div className="result">{body}</div>;
}

const BRN_CSS = `
.brn{--surface:#fff;--surface-2:#eef1f6;--inset:#f8fafc;--ink:#18213a;--ink-2:#586178;--ink-3:#8a92a6;--border:#e4e8f0;--border-strong:#ccd3e0;--accent:#3b4aa0;--accent-ink:#fff;--accent-soft:#eaecf8;--good:#2f875f;--good-soft:#e4f1ea;--warn:#a3690a;--warn-soft:#faf1de;--crit:#bb4835;--crit-soft:#fbeae6;--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,monospace;height:100%;display:flex;flex-direction:column;min-height:0;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .brn{--surface:#141922;--surface-2:#1b2130;--inset:#10141d;--ink:#e8ebf3;--ink-2:#9aa3b8;--ink-3:#6b7488;--border:#242b39;--border-strong:#333c4e;--accent:#8290e6;--accent-ink:#0c0f16;--accent-soft:#1f2740;--good:#5cbf90;--good-soft:#15271f;--warn:#e0ab54;--warn-soft:#2c2313;--crit:#e2705c;--crit-soft:#301c18;--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4)}
.brn .dim{color:var(--ink-3)} .brn .ic{width:15px;height:15px;flex:none;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.brn .sp{flex:1}
.brn .top{flex:none;border-bottom:1px solid var(--border);background:var(--surface);padding:12px 24px;display:flex;align-items:center;gap:12px}
.brn .top .hic{width:34px;height:34px;border-radius:9px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;flex:none} .brn .top .hic .ic{width:18px;height:18px}
.brn .top h1{margin:0;font-size:16px;font-weight:680;letter-spacing:-.015em}
.brn .top .sub{margin:1px 0 0;font-size:12px;color:var(--ink-3)}
.brn .idxstat{display:flex;gap:16px;align-items:center;font-size:12px;color:var(--ink-2)}
.brn .idxstat b{color:var(--ink);font-weight:650;font-family:var(--mono)}
.brn .body{flex:1;min-height:0;overflow:auto;padding:20px 24px 40px}
.brn .wrap{max-width:1120px;margin:0 auto}
.brn .console{margin-bottom:8px}
.brn .cbar{display:flex;align-items:center;gap:6px;border:1px solid var(--border-strong);border-radius:12px;background:var(--surface);padding:5px 6px 5px 5px;box-shadow:var(--shadow)}
.brn .cbar:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.brn .scopesel{border:0;background:none;color:var(--ink-2);font:600 12.5px var(--sans);padding:8px 6px;border-radius:8px;flex:none;cursor:pointer}
.brn .cdiv{width:1px;height:22px;background:var(--border);flex:none}
.brn .cin{flex:1;min-width:0;border:0;background:none;outline:none;color:var(--ink);font-size:13.5px;padding:8px 6px}
.brn .btn{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border-radius:9px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;font-size:12.5px;cursor:pointer;flex:none}
.brn .btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)} .brn .btn.pri:hover{filter:brightness(1.06)} .brn .btn[disabled]{opacity:.55;pointer-events:none}
.brn .targetsel{width:100%;margin-top:8px;padding:9px 12px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:500 12.5px var(--sans)}
.brn .crow{display:flex;align-items:center;justify-content:space-between;padding:7px 2px 0;font-size:11.5px;color:var(--ink-3)} .brn .crow b{color:var(--ink-2);font-weight:600}
.brn .findbtn{display:inline-flex;align-items:center;gap:5px;background:none;border:0;color:var(--ink-3);font-weight:600;font-size:11.5px;cursor:pointer} .brn .findbtn:hover{color:var(--accent)} .brn .findbtn[disabled]{opacity:.4;pointer-events:none} .brn .findbtn .ic{width:12px;height:12px}
.brn .work{display:grid;grid-template-columns:1fr 320px;gap:20px;margin-top:18px;align-items:start}
@media(max-width:960px){.brn .work{grid-template-columns:1fr}}
.brn .colmain{min-width:0}
.brn .loading{display:flex;align-items:center;gap:9px;padding:22px 4px;color:var(--ink-2);font-size:12.5px} .brn .loading .ic{width:16px;height:16px;color:var(--accent);animation:brnspin 1s linear infinite}
@keyframes brnspin{to{transform:rotate(360deg)}}
.brn .idlemain{border:1px dashed var(--border-strong);border-radius:13px;background:var(--inset);padding:44px 28px;text-align:center;display:flex;flex-direction:column;align-items:center;gap:4px}
.brn .idleic{width:46px;height:46px;border-radius:12px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;margin-bottom:8px} .brn .idleic .ic{width:23px;height:23px}
.brn .idlet{margin:0;font-size:15px;font-weight:660;color:var(--ink)}
.brn .idled{margin:0;font-size:12.5px;color:var(--ink-2);max-width:400px;line-height:1.6}
.brn .result{display:flex;flex-direction:column;gap:18px}
.brn .answercard{border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);padding:17px 18px}
.brn .ach{display:flex;align-items:center;gap:9px;margin-bottom:12px} .brn .ach b{font-size:13px}
.brn .aic{width:26px;height:26px;border-radius:8px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;flex:none} .brn .aic .ic{width:15px;height:15px}
.brn .qecho{font-size:12px;color:var(--ink-3);margin:0 0 10px;padding-bottom:11px;border-bottom:1px solid var(--border)} .brn .qecho b{color:var(--ink-2);font-weight:600}
.brn .atext>div{font-size:13.5px;line-height:1.7;color:var(--ink)}
.brn .atext strong{font-weight:670;color:var(--ink)}
.brn .atext p{margin:0 0 10px} .brn .atext p:last-child{margin-bottom:0}
.brn .atext ul,.brn .atext ol{margin:10px 0;padding-left:20px} .brn .atext li{margin:3px 0}
.brn .alimit{margin-top:11px;padding:9px 12px;border:1px solid color-mix(in srgb,var(--warn) 30%,var(--border));background:var(--warn-soft);color:var(--warn);border-radius:9px;font-size:12.5px}
.brn .agrounded{display:flex;align-items:center;gap:7px;margin:14px 0 0;padding-top:12px;border-top:1px solid var(--border);font-size:11.5px;color:var(--ink-3)} .brn .agrounded .v{color:var(--good);font-weight:650}
.brn .sec{display:flex;flex-direction:column;gap:9px}
.brn .seclbl{display:flex;align-items:center;gap:7px;font:600 10.5px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.brn .hintdim{font-weight:400;text-transform:none;letter-spacing:0;color:var(--ink-3)}
.brn .srclist{display:flex;flex-direction:column;gap:8px}
.brn .srow{display:block;border:1px solid var(--border);border-radius:10px;background:var(--inset);padding:10px 12px}
.brn .srow.link{color:inherit;text-decoration:none;cursor:pointer} .brn .srow.link:hover{border-color:var(--accent);background:var(--accent-soft)}
.brn .srh{display:flex;align-items:center;gap:8px;margin-bottom:4px} .brn .srt{font-weight:600;font-size:12.5px;color:var(--ink)} .brn .srm{font-size:11px;color:var(--ink-3)} .brn .sra{margin-left:auto;color:var(--ink-3)} .brn .sra .ic{width:13px;height:13px}
.brn .sre{margin:0;font-size:12.5px;line-height:1.55;color:var(--ink-2);white-space:pre-wrap;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.brn .tag{display:inline-flex;align-items:center;font:600 10.5px var(--sans);padding:2px 8px;border-radius:99px;flex:none}
.brn .tag.blue{background:var(--accent-soft);color:var(--accent)} .brn .tag.good{background:var(--good-soft);color:var(--good)} .brn .tag.warn{background:var(--warn-soft);color:var(--warn)} .brn .tag.crit{background:var(--crit-soft);color:var(--crit)} .brn .tag.slate{background:var(--surface-2);color:var(--ink-2)}
.brn .rail{display:flex;flex-direction:column;gap:14px}
.brn .railcard{border:1px solid var(--border);border-radius:12px;background:var(--surface);box-shadow:var(--shadow);padding:14px}
.brn .railcard h3{margin:0 0 10px;font:600 10.5px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);display:flex;align-items:center;gap:7px}
.brn .railcard h3 .ic{width:13px;height:13px}
.brn .gtiles{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.brn .gtile{border:1px solid var(--border);border-radius:9px;background:var(--inset);padding:9px 10px}
.brn .gtile .gn{font:700 17px/1 var(--mono);letter-spacing:-.02em;color:var(--ink)} .brn .gtile .gl{font-size:10px;color:var(--ink-3);margin-top:4px}
.brn .suggest{display:flex;flex-direction:column;gap:1px}
.brn .suggest button{display:flex;align-items:center;gap:8px;width:100%;text-align:left;background:none;border:0;cursor:pointer;padding:8px;border-radius:8px;font:inherit;color:var(--ink-2);font-size:12.5px}
.brn .suggest button:hover{background:var(--surface-2);color:var(--accent)}
.brn .suggest button .ic{width:13px;height:13px;color:var(--accent)} .brn .suggest button span{flex:1;min-width:0}
.brn .recentlist{list-style:none;margin:0;padding:0;display:flex;flex-direction:column}
.brn .recentlist li{border-bottom:1px solid var(--border)} .brn .recentlist li:last-child{border-bottom:0}
.brn .recentlist button{display:block;width:100%;padding:9px 4px;background:none;border:0;cursor:pointer;text-align:left;font:inherit;color:inherit}
.brn .recentlist button:hover{background:var(--inset)}
.brn .recentlist .rq{font-size:12px;color:var(--ink);display:block;margin-bottom:3px}
.brn .recentlist .rmeta{display:flex;align-items:center;gap:7px} .brn .recentlist .rt{font-size:10.5px;color:var(--ink-3)}
.brn .empty{border:1px dashed var(--border-strong);border-radius:10px;background:var(--inset);padding:16px;text-align:center;font-size:12px;color:var(--ink-2)} .brn .empty.err{border-color:color-mix(in srgb,var(--crit) 40%,var(--border));color:var(--crit)}
`;
