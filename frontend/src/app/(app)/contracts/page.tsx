"use client";

// Contracts — the Legal Intake layout: icon KPI cards, view pills, one table card (search, filters,
// sort, rows with a lifecycle bar, risk tag and expiry) and insight cards on the right. Real data
// throughout; rows open the contract workspace. Stall tracking and bulk actions are not built (no
// backend signal for them).

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarClock, ChevronDown, FileText, Handshake, Plus, Search, ShieldAlert, Users, Wallet, CheckCircle2, Tag, ChevronRight } from "lucide-react";
import { contractsApi, intakeApi, usersApi } from "@/lib/endpoints";
import type { ContractResponse, IntakeRequest } from "@/lib/types";
import {
  titleCase,
  fmtDate,
  fmtMoney,
  riskTone,
  riskBand,
  RISK_UNKNOWN_LABEL,
  STAGE_ORDER,
  contractDisplayName,
  looksLikeFilename,
  plural,
  initials,
  cn,
} from "@/lib/utils";
import { CenterSpinner, ErrorState } from "@/components/ui";
import { ImportContractModal } from "@/components/import-contract-modal";
import { useScreenAccess } from "@/lib/screen-access";
import { daysToExpiry, isExpiringSoon } from "@/lib/expiry";
import { FilterMenu, MockupShell, StageCell, pageList } from "../intake/_legal-intake";

const STAGE_LABELS = STAGE_ORDER.map((s) => titleCase(s));
const NEGO_STAGES = new Set(["drafting", "review", "approval", "signature"]);
// Unknown ranks above high: least known needs the most attention.
const RISK_RANK: Record<string, number> = { low: 0, medium: 1, high: 2, unknown: 3 };

// The band from the playbook review of the current text — never the legacy,
// hand-settable risk_level, and unknown when the cached summary is for other text.
const riskOf = (c: ContractResponse) => riskBand(c.risk_summary, c.current_authoritative_version_id);
const isHighRisk = (c: ContractResponse) => riskOf(c) === "high";
const inNego = (c: ContractResponse) => NEGO_STAGES.has(c.lifecycle_stage);
const dleftOf = (c: ContractResponse): number | null => daysToExpiry(c);
const isExpiring = (c: ContractResponse) => isExpiringSoon(c);

type SortKey = "upd" | "exp" | "val" | "risk" | "cp";
type View = "all" | "active" | "nego" | "expiring" | "high" | "renewal" | "untyped";
const VIEW_TEST: Record<View, (c: ContractResponse) => boolean> = {
  all: () => true,
  active: (c) => c.lifecycle_stage === "active",
  nego: inNego,
  expiring: isExpiring,
  high: isHighRisk,
  renewal: (c) => !!c.renewal_due,
  untyped: (c) => c.type_needed,
};

export default function ContractsPage() {
  const router = useRouter();
  const qc = useQueryClient();
  // FR-9: UI-layer control gating only — the API independently re-verifies
  // every ADD request (FR-10/FR-13); this is a usability aid, not the
  // security boundary.
  const { canAdd } = useScreenAccess("contracts");
  const { data, isLoading, error } = useQuery({ queryKey: ["contracts-list"], queryFn: contractsApi.list });
  const { data: users } = useQuery({ queryKey: ["users"], queryFn: () => usersApi.list() });
  // The request each contract came from. A person who cannot see requests just gets no request column data.
  const { data: requests } = useQuery({ queryKey: ["intake-requests-all"], queryFn: () => intakeApi.list(), retry: false });
  const requestOf = useMemo(() => {
    const m = new Map<string, IntakeRequest>();
    (requests ?? []).forEach((r) => { if (r.contract_id && !m.has(r.contract_id)) m.set(r.contract_id, r); });
    return (id: string) => m.get(id);
  }, [requests]);

  const [search, setSearch] = useState("");
  const [view, setView] = useState<View>("all");
  const [stageF, setStageF] = useState<Set<string>>(new Set());
  const [typeF, setTypeF] = useState<Set<string>>(new Set());
  const [riskF, setRiskF] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<SortKey>("upd");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [importOpen, setImportOpen] = useState(false);

  const rows = useMemo(() => data ?? [], [data]);
  const ownerName = useMemo(() => {
    const m = new Map<string, string>();
    (users ?? []).forEach((u) => m.set(u.id, u.full_name));
    return (id: string) => m.get(id) ?? "Unassigned";
  }, [users]);

  const typeOptions = useMemo(() => {
    const seen = new Map<string, string>();
    rows.forEach((c) => { if (c.contract_type) seen.set(c.contract_type.toLowerCase(), titleCase(c.contract_type)); });
    return [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [rows]);

  const q = search.trim().toLowerCase();
  const filtered = rows.filter((c) => {
    if (!VIEW_TEST[view](c)) return false;
    if (stageF.size && !stageF.has(c.lifecycle_stage)) return false;
    if (typeF.size && !typeF.has((c.contract_type ?? "").toLowerCase())) return false;
    if (riskF.size && !riskF.has(riskOf(c))) return false;
    if (q && !`${c.title} ${c.counterparty_name ?? ""} ${c.contract_type ?? ""}`.toLowerCase().includes(q)) return false;
    return true;
  });
  const shown =
    sort === "upd"
      ? filtered
      : [...filtered].sort((a, b) => {
          switch (sort) {
            case "exp": {
              const da = dleftOf(a), db = dleftOf(b);
              return (da == null ? 1 : 0) - (db == null ? 1 : 0) || (da ?? 1e9) - (db ?? 1e9);
            }
            case "val": return (b.value_amount ?? -1) - (a.value_amount ?? -1);
            case "risk": return (RISK_RANK[riskOf(b)] ?? -1) - (RISK_RANK[riskOf(a)] ?? -1);
            case "cp": return (a.counterparty_name ?? "").localeCompare(b.counterparty_name ?? "");
          }
        });
  const pageCount = Math.max(1, Math.ceil(shown.length / pageSize));
  const cur = Math.min(page, pageCount - 1);
  const visible = shown.slice(cur * pageSize, (cur + 1) * pageSize);

  // ── portfolio figures (over the full list, not the filtered view) ──
  const counts: Record<View, number> = {
    all: rows.length,
    active: rows.filter(VIEW_TEST.active).length,
    nego: rows.filter(VIEW_TEST.nego).length,
    expiring: rows.filter(VIEW_TEST.expiring).length,
    high: rows.filter(VIEW_TEST.high).length,
    renewal: rows.filter(VIEW_TEST.renewal).length,
    untyped: rows.filter(VIEW_TEST.untyped).length,
  };
  const totalVal = rows.reduce((s, c) => s + (c.value_amount ?? 0), 0);
  const currency = rows.find((c) => c.currency)?.currency ?? "USD";

  const load = useMemo(() => {
    const m = new Map<string, number>();
    rows.filter(inNego).forEach((c) => { const n = ownerName(c.owner_user_id); m.set(n, (m.get(n) ?? 0) + 1); });
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
  }, [rows, ownerName]);


  const attention = rows
    .filter((c) => (dleftOf(c) ?? 999) <= 30 && isExpiring(c))
    .sort((a, b) => (dleftOf(a) ?? 0) - (dleftOf(b) ?? 0));

  const filtersActive = view !== "all" || stageF.size > 0 || typeF.size > 0 || riskF.size > 0 || !!q;
  const clearAll = () => { setView("all"); setStageF(new Set()); setTypeF(new Set()); setRiskF(new Set()); setSearch(""); setPage(0); };
  const toggle = (set: Set<string>, fn: (s: Set<string>) => void, v: string) => {
    const next = new Set(set);
    if (next.has(v)) next.delete(v); else next.add(v);
    fn(next); setPage(0);
  };
  const pick = (v: View) => { setView(v); setPage(0); };

  if (isLoading) return <CenterSpinner label="Loading contracts…" />;
  if (error) return <ErrorState error={error} />;

  const todo: { n: number; l: string; s: string; tone: string; go: () => void }[] = [
    { n: counts.high, l: "high-risk contracts", s: "Review the flagged clauses", tone: "", go: () => pick("high") },
    { n: counts.expiring, l: "expiring within 90 days", s: "Plan renewals or exits", tone: "risk", go: () => pick("expiring") },
    { n: counts.untyped, l: "need a contract type", s: "So type-based checks can find them", tone: "risk", go: () => pick("untyped") },
    { n: rows.filter((c) => riskOf(c) === "unknown").length, l: "not yet reviewed", s: "No playbook review of the current text", tone: "mute", go: () => { setView("all"); setRiskF(new Set(["unknown"])); setPage(0); } },
    { n: counts.renewal, l: "renewals due", s: "Renewal window is open", tone: "risk", go: () => pick("renewal") },
  ].filter((t) => t.n > 0);

  const kcards: { v: View | null; n: number | string; l: string; I: typeof FileText; t: string }[] = [
    { v: "all", n: counts.all, l: "All contracts", I: FileText, t: "ind" },
    { v: "active", n: counts.active, l: "Active", I: CheckCircle2, t: "emr" },
    { v: "nego", n: counts.nego, l: "In negotiation", I: Handshake, t: "sky" },
    { v: "expiring", n: counts.expiring, l: "Expiring ≤90d", I: CalendarClock, t: "amb" },
    { v: "high", n: counts.high, l: "High risk", I: ShieldAlert, t: "red" },
  ];
  const pills: { v: View; label: string; c: string }[] = [
    { v: "all", label: "All", c: "ind" },
    { v: "active", label: "Active", c: "emr" },
    { v: "nego", label: "In negotiation", c: "sky" },
    { v: "expiring", label: "Expiring", c: "amb" },
    { v: "high", label: "High risk", c: "red" },
    { v: "renewal", label: "Renewal due", c: "vio" },
    { v: "untyped", label: "Needs a type", c: "amb" },
  ];

  return (
    <MockupShell
      icon={<FileText />}
      title="Contracts"
      subtitle="every executed and in-flight agreement in the org"
      actions={
        <button className="btn pri" disabled={!canAdd} title={canAdd ? undefined : "You don't have add access to this screen"} onClick={() => setImportOpen(true)}>
          <Plus className="ic" />New contract
        </button>
      }
    >
      <div className="split">
        <div className="maincol">
                    <section className="ov" aria-label="Portfolio at a glance">
            <div className="kcards">
              {kcards.map((k) => (
                <button key={k.l} type="button" className={`kcard kc-${k.t}`} onClick={() => k.v && pick(k.v)} tabIndex={-1} aria-hidden="true">
                  <span className="kic"><k.I className="ic" aria-hidden /></span>
                  <span className="ktx"><span className="kl">{k.l}</span><b className="kn num">{k.n}</b></span>
                </button>
              ))}
            </div>
            <div className="stabs" role="group" aria-label="Filter contracts">
              {pills.map((p) => (
                <button key={p.v} type="button" aria-pressed={view === p.v} onClick={() => pick(p.v)} className={`stab${view === p.v ? " on" : ""}`} style={{ ["--c" as string]: `var(--c-${p.c})` }}>
                  <i aria-hidden /><span className="tl">{p.label}</span><span className="tn num">{counts[p.v]}</span>
                </button>
              ))}
            </div>
          </section>

          <div className="tcard">
            <div className="toolbar">
              <div className="search">
                <Search className="ic" />
                <input aria-label="Search contracts" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} placeholder="Search title, counterparty, type…" />
              </div>
              <FilterMenu label="Stage" options={STAGE_ORDER.map((s, i) => ({ value: s, label: STAGE_LABELS[i] }))} selected={stageF} onToggle={(v) => toggle(stageF, setStageF, v)} />
              <FilterMenu label="Type" options={typeOptions.map(([value, label]) => ({ value, label }))} selected={typeF} onToggle={(v) => toggle(typeF, setTypeF, v)} />
              <FilterMenu label="Risk" options={[
                { value: "unknown", label: "Not reviewed", dot: "var(--ink-3)" },
                { value: "high", label: "High", dot: "var(--crit)" },
                { value: "medium", label: "Medium", dot: "var(--warn)" },
                { value: "low", label: "Low", dot: "var(--good)" },
              ]} selected={riskF} onToggle={(v) => toggle(riskF, setRiskF, v)} />
              <span className="sortwrap"><select aria-label="Sort contracts" className="sortsel" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
                <option value="upd">Recently updated</option>
                <option value="exp">Expiring soonest</option>
                <option value="val">Value high→low</option>
                <option value="risk">Risk: not reviewed, then high→low</option>
                <option value="cp">Counterparty A–Z</option>
              </select></span>
              {filtersActive && <button type="button" className="clear" onClick={clearAll}>Clear all</button>}
              <span className="sp" />
              <span className="dim num count">{shown.length} of {rows.length}</span>
            </div>

            <div className="tablewrap">
              <table className="ctt">
                <thead><tr>
                  <th>Contract</th><th>Counterparty</th><th>Request</th><th>Risk</th><th className="cdue">Expiration</th>
                  <th><span className="sr-only">Actions</span></th>
                </tr></thead>
                <tbody>
                  {visible.map((c) => {
                    const idx = STAGE_ORDER.indexOf(c.lifecycle_stage) + 1;
                    const dleft = dleftOf(c);
                    const risk = riskOf(c);
                    return (
                      <tr key={c.id} onClick={() => router.push(`/contracts/${c.id}`)}>
                        <td><div className="req">
                          <div className="chan"><FileText className="ic" /></div>
                          <div style={{ minWidth: 0 }}>
                            <button type="button" className="nm" onClick={(e) => { e.stopPropagation(); router.push(`/contracts/${c.id}`); }}>{contractDisplayName(c)}</button>
                            {/* The upload it came from is provenance under the name, not the name. */}
                            {looksLikeFilename(c.title) && c.title ? <div className="prev">{c.title}</div> : c.contract_type ? <div className="prev">{titleCase(c.contract_type)}</div> : null}
                          </div>
                        </div></td>
                        <td>
                          <div className="cp">{c.counterparty_name ?? <span className="dim">—</span>}</div>
                          {/* Only say something when there is a value: "no value" on every row reads as data but carries none. */}
                          {c.value_amount != null && <div className="ty num">{fmtMoney(c.value_amount, c.currency)}</div>}
                        </td>
                        <td>{(() => {
                          const rq = requestOf(c.id);
                          // No request behind it (imported straight in): no stage, no lifecycle.
                          if (!rq) return <span className="dim">No request</span>;
                          return (
                            <div className="rqcell">
                              <span className="rqref">{rq.ref}</span>
                              {(rq.workflow ?? []).length ? <StageCell r={rq} /> : <span className="dim">{rq.stage}</span>}
                            </div>
                          );
                        })()}</td>
                        <td><div className="flags">
                          {/* Unknown gets a quiet tag, not silence: a blank cell read as "nothing wrong" when nobody had reviewed the text. */}
                          {risk === "unknown"
                            ? <span className="spill done" title={[RISK_UNKNOWN_LABEL, c.risk_summary?.note].filter(Boolean).join(". ")}>Not reviewed</span>
                            : <span className={`spill ${riskTone(risk) === "red" ? "crit" : riskTone(risk) === "amber" ? "risk" : "ok"}`}>{titleCase(risk)}</span>}
                          {c.renewal_due && <span className="spill done">Renewal due</span>}
                          {c.type_needed && <span className="spill risk" title="Choose a type on the contract so type-based checks can find it">Needs a type</span>}
                        </div></td>
                        <td className="cdue">
                          {c.expiration_date ? (
                            <>
                              <div className="age num">{fmtDate(c.expiration_date)}</div>
                              <div className={`from${dleft != null && dleft <= 90 ? " late" : ""}`}>
                                {dleft == null ? "" : dleft < 0 ? `expired ${-dleft}d ago` : dleft <= 90 ? `in ${plural(dleft, "day")}` : `in ${Math.round(dleft / 30)} mo`}
                              </div>
                            </>
                          ) : <span className="dim">no end date</span>}
                        </td>
                        <td><div className="rowact"><button className="btn sm" onClick={(e) => { e.stopPropagation(); router.push(`/contracts/${c.id}`); }}>Open</button></div></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {shown.length === 0 && <div className="empty">{filtersActive ? "No contracts match these filters." : "No contracts yet."}</div>}
            </div>
            <div className="tfoot">
              <span className="num">{shown.length === 0 ? "0 results" : `${cur * pageSize + 1}–${Math.min((cur + 1) * pageSize, shown.length)} of ${shown.length}`}</span>
              <nav className="pager" aria-label="Pagination">
                <button type="button" className="pgb" disabled={cur === 0} onClick={() => setPage(cur - 1)} aria-label="Previous page"><ChevronDown className="ic prev" /></button>
                {pageList(pageCount, cur).map((n, i) => n === -1
                  ? <span key={`e${i}`} className="pge">…</span>
                  : <button key={n} type="button" className={`pgb${n === cur ? " on" : ""}`} aria-current={n === cur ? "page" : undefined} onClick={() => setPage(n)}>{n + 1}</button>)}
                <button type="button" className="pgb" disabled={cur >= pageCount - 1} onClick={() => setPage(cur + 1)} aria-label="Next page"><ChevronDown className="ic next" /></button>
              </nav>
              <label className="rpp">Rows <select aria-label="Rows per page" value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}>{[10, 25, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
            </div>
          </div>
        </div>

        <aside className="side" aria-label="Portfolio insights">
          <section className="scard attn">
            <header>
              <span className="sic crit"><AlertTriangle className="ic" /></span>
              <h3>Needs attention</h3>
            </header>
            {todo.length === 0 ? <p className="allclear">Nothing needs you right now.</p> : (
              <ul className="alist">
                {todo.map((t) => (
                  <li key={t.l} className="arow">
                    <button type="button" className="amainbtn" onClick={t.go}>
                      <i className={t.tone} aria-hidden />
                      <span className="amain"><span className="pt"><b className="num">{t.n}</b> {t.l}</span><span className="ps">{t.s}</span></span>
                    </button>
                    <span className="aside2"><button type="button" className="abtn" onClick={t.go}>View<ChevronRight className="ic" /></button></span>
                  </li>
                ))}
              </ul>
            )}
            {attention.length > 0 && (
              <>
                <div className="subhd">Expiring within 30 days</div>
                <ul className="alist">
                  {attention.slice(0, 3).map((c) => (
                    <li key={c.id} className="arow">
                      <button type="button" onClick={() => router.push(`/contracts/${c.id}`)} className="amainbtn" aria-label={`Open ${contractDisplayName(c)}`}>
                        <span className="amain"><span className="pt" title={contractDisplayName(c)}>{contractDisplayName(c)}</span><span className="ps">{c.counterparty_name ?? "—"} · in {plural(dleftOf(c) ?? 0, "day")}</span></span>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
          <section className="scard work">
            <header>
              <span className="sic"><Users className="ic" /></span>
              <h3>Who has the negotiations</h3>
              <span className="ssub">{counts.nego} in flight</span>
            </header>
            {load.length === 0 ? <p className="allclear">No contract is in negotiation.</p> : (
              <ul className="wlist">
                {load.map(([name, n], i) => (
                  <li key={name}>
                    <span className={`av av${i % 6}`} aria-hidden>{initials(name)}</span>
                    <span className="winfo"><span className="pn" title={name}>{name}</span><span className="wsub ok">{plural(n, "contract")} in negotiation</span></span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>
      </div>

      <ImportContractModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onUploaded={(contract) => {
          qc.invalidateQueries({ queryKey: ["contracts-list"] });
          setImportOpen(false);
          router.push(`/contracts/${contract.id}`);
        }}
      />
      <style dangerouslySetInnerHTML={{ __html: CT_CSS }} />
    </MockupShell>
  );
}

const CT_CSS = `
.li-board .kcards{grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}
.li-board .kcard{padding:12px;gap:10px}
.li-board .kcard .kic{width:36px;height:36px;border-radius:10px}
.li-board .kcard .kl{white-space:nowrap}
.li-board .kcard .kn{font-size:20px}
@media (max-width:1400px){.li-board .kcards{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:640px){.li-board .kcards{grid-template-columns:repeat(2,minmax(0,1fr))}}
.li-board .rqcell{display:flex;flex-direction:column;gap:5px;min-width:0}
.li-board .rqref{font:600 11px var(--mono);color:var(--accent)}
.li-board .arow i.mute{background:var(--border-strong)}
.li-board .subhd{margin:12px 0 4px;font:600 10.5px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.li-board .abtn .ic{width:12px;height:12px;margin-left:2px}
.li-board .ctbar{display:flex;gap:2px;margin-top:7px;max-width:200px}
.li-board .ctbar .seg{height:7px;flex:1 1 0;min-width:3px;border-radius:2px;background:var(--surface-2)}
.li-board .ctbar .seg.d{background:color-mix(in srgb,var(--good) 42%,transparent)}
.li-board .ctbar .seg.c{background:var(--good)}
.li-board .tcard .tablewrap td .nm{display:block;max-width:230px;text-align:left}
/* six columns sized for what they hold; names wrap instead of being cut */
.li-board .tablewrap table.ctt{table-layout:fixed;width:100%;min-width:0}
.li-board table.ctt thead th:nth-child(1){width:29%}.li-board table.ctt thead th:nth-child(2){width:17%}.li-board table.ctt thead th:nth-child(3){width:25%}.li-board table.ctt thead th:nth-child(4){width:13%}.li-board table.ctt thead th:nth-child(5){width:96px}.li-board table.ctt thead th:nth-child(6){width:76px}
.li-board table.ctt th.cdue,.li-board table.ctt td.cdue{width:96px;min-width:96px;white-space:normal}
.li-board table.ctt tbody td{vertical-align:middle;padding-top:12px;padding-bottom:12px}
.li-board table.ctt tbody td:nth-child(-n+3){overflow:hidden}
.li-board table.ctt .req .nm,.li-board table.ctt .req .prev,.li-board table.ctt .cp,.li-board table.ctt .ty,.li-board table.ctt .stagenm,.li-board table.ctt .wf{white-space:normal;overflow:visible;text-overflow:clip;word-break:break-word;max-width:100%;line-height:1.3}
.li-board table.ctt .req .nm{display:block;max-width:100%}
.li-board table.ctt .flags{flex-wrap:wrap}
.li-board table.ctt .spill{white-space:nowrap}
`;
