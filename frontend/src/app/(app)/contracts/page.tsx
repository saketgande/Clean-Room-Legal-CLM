"use client";

// Contracts — portfolio command center in the Legal Intake style (scoped `.ctl`).
// A live portfolio briefing (summary + tiles + in-flight load + attention chips),
// compact filter menus, and a table whose Stage column shows each contract's
// position on the 7-stage lifecycle. Real data throughout; rows open the
// contract workspace. Two mockup features are dropped for lack of backend
// signals: stall/"stuck" tracking and bulk actions.

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Search, Plus, ChevronDown, FileText } from "lucide-react";
import { contractsApi, usersApi } from "@/lib/endpoints";
import type { ContractResponse } from "@/lib/types";
import { titleCase, fmtDate, fmtMoney, riskTone, STAGE_ORDER } from "@/lib/utils";
import { CenterSpinner, ErrorState } from "@/components/ui";
import { ImportContractModal } from "@/components/import-contract-modal";
import { cn } from "@/lib/utils";

const STAGE_LABELS = STAGE_ORDER.map((s) => titleCase(s));
const NEGO_STAGES = new Set(["drafting", "review", "approval", "signature"]);
const RISK_RANK: Record<string, number> = { low: 0, medium: 1, high: 2, critical: 3 };

const riskOf = (c: ContractResponse) => (c.risk_band ?? c.risk_level ?? "").toLowerCase();
const isHighRisk = (c: ContractResponse) => riskOf(c) === "high" || riskOf(c) === "critical";
const inNego = (c: ContractResponse) => NEGO_STAGES.has(c.lifecycle_stage);
function dleftOf(c: ContractResponse): number | null {
  if (!c.expiration_date) return null;
  return Math.round((new Date(c.expiration_date + "T00:00:00").getTime() - Date.now()) / 86_400_000);
}
const isExpiring = (c: ContractResponse) => {
  const d = dleftOf(c);
  return d != null && d >= 0 && d <= 90 && c.lifecycle_stage !== "closed";
};

type SortKey = "upd" | "exp" | "val" | "risk" | "cp";

export default function ContractsPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["contracts-list"],
    queryFn: contractsApi.list,
  });
  const { data: users } = useQuery({ queryKey: ["users"], queryFn: () => usersApi.list() });

  const [search, setSearch] = useState("");
  const [stageF, setStageF] = useState<Set<string>>(new Set());
  const [typeF, setTypeF] = useState<Set<string>>(new Set());
  const [riskF, setRiskF] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<SortKey>("upd");
  const [openDD, setOpenDD] = useState<string | null>(null);
  const [briefCollapsed, setBriefCollapsed] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  useEffect(() => {
    if (!openDD) return;
    const close = () => setOpenDD(null);
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, [openDD]);

  const rows = useMemo(() => data ?? [], [data]);
  const ownerName = useMemo(() => {
    const m = new Map<string, string>();
    (users ?? []).forEach((u) => m.set(u.id, u.full_name));
    return (id: string) => m.get(id) ?? "Unassigned";
  }, [users]);

  const typeOptions = useMemo(() => {
    const seen = new Map<string, string>();
    rows.forEach((c) => {
      if (c.contract_type) seen.set(c.contract_type.toLowerCase(), titleCase(c.contract_type));
    });
    return [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [rows]);

  const q = search.trim().toLowerCase();
  const filtered = rows.filter((c) => {
    if (stageF.size && !stageF.has(c.lifecycle_stage)) return false;
    if (typeF.size && !typeF.has((c.contract_type ?? "").toLowerCase())) return false;
    if (riskF.size) {
      const toks = new Set([riskOf(c)]);
      if (isExpiring(c)) toks.add("_expiring");
      if (c.renewal_due) toks.add("_renewal");
      if (![...riskF].some((f) => toks.has(f))) return false;
    }
    if (q && !`${c.title} ${c.counterparty_name ?? ""} ${c.contract_type ?? ""}`.toLowerCase().includes(q))
      return false;
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
            case "val":
              return (b.value_amount ?? -1) - (a.value_amount ?? -1);
            case "risk":
              return (RISK_RANK[riskOf(b)] ?? -1) - (RISK_RANK[riskOf(a)] ?? -1);
            case "cp":
              return (a.counterparty_name ?? "").localeCompare(b.counterparty_name ?? "");
          }
        });

  // ── portfolio briefing (computed over the full list, not the filtered view) ──
  const active = rows.filter((c) => c.lifecycle_stage === "active").length;
  const negoRows = rows.filter(inNego);
  const expiringRows = rows.filter(isExpiring);
  const highRows = rows.filter(isHighRisk);
  const renewals = rows.filter((c) => c.renewal_due).length;
  const totalVal = rows.reduce((s, c) => s + (c.value_amount ?? 0), 0);
  const currency = rows.find((c) => c.currency)?.currency ?? "USD";
  const nearest = [...expiringRows].sort((a, b) => (dleftOf(a) ?? 1e9) - (dleftOf(b) ?? 1e9))[0];

  const load = useMemo(() => {
    const m = new Map<string, number>();
    negoRows.forEach((c) => {
      const name = ownerName(c.owner_user_id);
      m.set(name, (m.get(name) ?? 0) + 1);
    });
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
  }, [negoRows, ownerName]);
  const maxLoad = Math.max(...load.map(([, n]) => n), 1);

  const attention = [
    ...[...expiringRows]
      .filter((c) => (dleftOf(c) ?? 999) <= 30)
      .sort((a, b) => (dleftOf(a) ?? 0) - (dleftOf(b) ?? 0))
      .map((c) => ({ c, cls: "w", t: `${c.counterparty_name ?? c.title} — expires ${dleftOf(c)}d` })),
    ...highRows
      .filter((c) => riskOf(c) === "critical")
      .map((c) => ({ c, cls: "c", t: `${c.counterparty_name ?? c.title} — critical risk` })),
  ].slice(0, 5);

  const filtersActive = stageF.size || typeF.size || riskF.size || !!q;
  const clearAll = () => {
    setStageF(new Set());
    setTypeF(new Set());
    setRiskF(new Set());
    setSearch("");
  };

  if (isLoading) return <CenterSpinner label="Loading contracts…" />;
  if (error) return <ErrorState error={error} />;

  const SORT_LABELS: Record<SortKey, string> = {
    upd: "Recently updated",
    exp: "Expiring soonest",
    val: "Value high→low",
    risk: "Risk high→low",
    cp: "Counterparty A–Z",
  };

  return (
    <div className="ctl">
      <style dangerouslySetInnerHTML={{ __html: CTL_CSS }} />

      <div className="top">
        <div className="toprow">
          <h1>Contracts</h1>
          <span className="sub">every executed and in-flight agreement in the org</span>
          <div className="sp" />
          <div className="stat">
            <span>
              <b>{active}</b> active
            </span>
            <span className="warn">
              <b>{expiringRows.length}</b> expiring
            </span>
            <span className="crit">
              <b>{highRows.length}</b> high-risk
            </span>
          </div>
          <button className="btn pri" onClick={() => setImportOpen(true)}>
            <Plus className="ic" />
            New contract
          </button>
        </div>

        <div className="toolbar">
          <div className="search">
            <Search className="ic" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search title, counterparty, type…"
            />
          </div>

          <FilterMenu
            label="Stage"
            group="stage"
            open={openDD}
            setOpen={setOpenDD}
            sel={stageF}
            onToggle={(v) => setStageF(toggleSet(stageF, v))}
            options={STAGE_ORDER.map((s, i) => ({ v: s, label: STAGE_LABELS[i] }))}
          />
          <FilterMenu
            label="Type"
            group="type"
            open={openDD}
            setOpen={setOpenDD}
            sel={typeF}
            onToggle={(v) => setTypeF(toggleSet(typeF, v))}
            options={typeOptions.map(([v, label]) => ({ v, label }))}
          />
          <FilterMenu
            label="Risk"
            group="risk"
            open={openDD}
            setOpen={setOpenDD}
            sel={riskF}
            onToggle={(v) => setRiskF(toggleSet(riskF, v))}
            options={[
              { v: "critical", label: "Critical", dot: "var(--bad)" },
              { v: "high", label: "High", dot: "var(--bad)" },
              { v: "medium", label: "Medium", dot: "var(--warn)" },
              { v: "low", label: "Low", dot: "var(--good)" },
              { v: "_expiring", label: "Expiring ≤90d", dot: "var(--warn)" },
              { v: "_renewal", label: "Renewal due", dot: "var(--accent)" },
            ]}
          />

          <div className={cn("dd", openDD === "sort" && "open")}>
            <button
              className="ddbtn"
              onClick={(e) => {
                e.stopPropagation();
                setOpenDD(openDD === "sort" ? null : "sort");
              }}
            >
              <span>{SORT_LABELS[sort]}</span>
              <ChevronDown className="caret" />
            </button>
            <div className="ddmenu" onClick={(e) => e.stopPropagation()}>
              {(Object.keys(SORT_LABELS) as SortKey[]).map((k) => (
                <label key={k}>
                  <input
                    type="radio"
                    name="ctl-sort"
                    checked={sort === k}
                    onChange={() => {
                      setSort(k);
                      setOpenDD(null);
                    }}
                  />
                  {SORT_LABELS[k]}
                </label>
              ))}
            </div>
          </div>

          {filtersActive && (
            <span className="clear" onClick={clearAll}>
              Clear
            </span>
          )}
          <div className="sp" />
          <span className="dim num">
            {shown.length} of {rows.length}
          </span>
        </div>
      </div>

      <div className={cn("brief", briefCollapsed && "collapsed")}>
        <div className="briefhd">
          <div className="aiglyph">✦</div>
          <div>
            <b>Portfolio briefing</b>{" "}
            <span className="when">· reads all {rows.length} contracts · updated just now</span>
          </div>
          <button className="tog" onClick={() => setBriefCollapsed((v) => !v)}>
            {briefCollapsed ? "Show" : "Hide"}
          </button>
        </div>
        <div className="oneline">
          <b>{active}</b> active · <b>{negoRows.length}</b> in negotiation ·{" "}
          <b style={{ color: "var(--warn)" }}>{expiringRows.length} expiring</b> ·{" "}
          <b style={{ color: "var(--bad)" }}>{highRows.length} high-risk</b>
        </div>
        <div className="briefbody">
          <p className="summary">
            <b>{rows.length} contracts in the portfolio.</b> {active} active, {negoRows.length} in
            negotiation, and{" "}
            <span className="c">
              {highRows.length} {highRows.length === 1 ? "carries" : "carry"} high or critical risk
            </span>
            .{" "}
            {nearest && (
              <>
                <b>{nearest.counterparty_name ?? nearest.title}</b>
                {nearest.counterparty_name ? ` — ${nearest.title}` : ""} expires in{" "}
                <span className="w">
                  {dleftOf(nearest)} {Math.abs(dleftOf(nearest) ?? 0) === 1 ? "day" : "days"}
                </span>{" "}
                — the nearest renewal.{" "}
              </>
            )}
            Total contract value under management is <b>{fmtMoney(totalVal, currency)}</b>.
          </p>

          <div className="tiles">
            <Tile cls="g" n={active} l="active" />
            <Tile cls="a" n={negoRows.length} l="in negotiation" />
            <Tile cls="w" n={expiringRows.length} l="expiring ≤90d" />
            <Tile cls="c" n={highRows.length} l="high / critical" />
            <Tile n={fmtMoney(totalVal, currency)} l="total value" />
            <Tile n={renewals} l="renewals due" />
          </div>

          {load.length > 0 && (
            <div className="load">
              <div className="loadhd">In-flight negotiation load · by owner</div>
              <div className="loadgrid">
                {load.map(([name, n]) => (
                  <div key={name} className="loadrow">
                    <span className="ldname">{name}</span>
                    <div className="ldbar">
                      <div className="ldfill" style={{ width: `${(n / maxLoad) * 100}%` }} />
                    </div>
                    <span className="ldn">{n}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="attention">
            <span className="attlbl">Needs you now</span>
            {attention.length ? (
              attention.map((a, i) => (
                <button
                  key={i}
                  className={cn("attchip", a.cls)}
                  onClick={() => router.push(`/contracts/${a.c.id}`)}
                >
                  ⚠ {a.t}
                </button>
              ))
            ) : (
              <span className="dim" style={{ fontSize: 12 }}>
                Nothing pressing — portfolio is healthy.
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="tablewrap">
        {shown.length === 0 ? (
          <div className="empty">
            {q || filtersActive ? "No contracts match these filters. " : "No contracts yet. "}
            {filtersActive && (
              <span className="clear" onClick={clearAll}>
                Clear
              </span>
            )}
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th className="c-idx">#</th>
                <th>Contract</th>
                <th className="c-cp">Counterparty</th>
                <th>Stage / lifecycle</th>
                <th>Risk &amp; flags</th>
                <th className="c-exp">Expiration</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {shown.map((c, i) => {
                const idx = STAGE_ORDER.indexOf(c.lifecycle_stage) + 1;
                const closed = c.lifecycle_stage === "closed";
                const dleft = dleftOf(c);
                const risk = riskOf(c);
                return (
                  <tr key={c.id} onClick={() => router.push(`/contracts/${c.id}`)}>
                    <td className="c-idx">
                      <span className="idx">{i + 1}</span>
                    </td>
                    <td>
                      <div className="req">
                        <div className="chan">
                          <FileText className="ic" />
                        </div>
                        <div style={{ minWidth: 0 }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                            <span className="nm">{c.title}</span>
                          </div>
                          <div className="prev">
                            {c.contract_type ? titleCase(c.contract_type) : "—"} · {c.counterparty_name ?? "—"}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="c-cp">
                      <div className="cp">{c.counterparty_name ?? <span className="dim">—</span>}</div>
                      <div className="ty">
                        {c.value_amount != null ? (
                          <span className="val">{fmtMoney(c.value_amount, c.currency)}</span>
                        ) : (
                          <span className="dim">no value</span>
                        )}
                      </div>
                    </td>
                    <td>
                      <div className="stagewrap">
                        <div className="stagehead">
                          <span className={cn("sdot", closed ? "done" : "ok")} />
                          <span className="stagenm">{titleCase(c.lifecycle_stage)}</span>
                          <span className="stepno">{idx}/7</span>
                        </div>
                        <div className="stepbar">
                          {Array.from({ length: 7 }, (_, j) => (
                            <span
                              key={j}
                              className={cn("seg", j < idx - 1 && "done", j === idx - 1 && "cur")}
                            />
                          ))}
                        </div>
                        <div className="wflabel">
                          {c.contract_type ? titleCase(c.contract_type) : "Contract"} lifecycle
                        </div>
                      </div>
                    </td>
                    <td>
                      <div className="flags">
                        {risk ? (
                          <span className={`tag ${riskTone(risk)}`}>{titleCase(risk)}</span>
                        ) : (
                          <span className="tag slate">Unrated</span>
                        )}
                        {isExpiring(c) && <span className="tag amber">expires {dleft}d</span>}
                        {c.renewal_due && <span className="tag blue">renewal due</span>}
                      </div>
                    </td>
                    <td className="c-exp">
                      {c.expiration_date ? (
                        <>
                          <div className="exp">{fmtDate(c.expiration_date)}</div>
                          <div className="expsub">
                            {dleft != null && dleft < 0 ? (
                              <span className="past">expired {-dleft}d ago</span>
                            ) : dleft != null && dleft <= 90 ? (
                              <span className="soon">in {dleft} days</span>
                            ) : dleft != null ? (
                              `in ${Math.round(dleft / 30)} mo`
                            ) : (
                              ""
                            )}
                          </div>
                        </>
                      ) : (
                        <>
                          <div className="exp dim">—</div>
                          <div className="expsub">no end date</div>
                        </>
                      )}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="rowact">
                        <button className="btn sm" onClick={() => router.push(`/contracts/${c.id}`)}>
                          Open
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
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
    </div>
  );
}

function Tile({ cls, n, l }: { cls?: string; n: number | string; l: string }) {
  return (
    <div className={cn("tile", cls)}>
      <div className="tn">{n}</div>
      <div className="tl">{l}</div>
    </div>
  );
}

function toggleSet(set: Set<string>, v: string): Set<string> {
  const next = new Set(set);
  next.has(v) ? next.delete(v) : next.add(v);
  return next;
}

function FilterMenu({
  label,
  group,
  open,
  setOpen,
  sel,
  onToggle,
  options,
}: {
  label: string;
  group: string;
  open: string | null;
  setOpen: (v: string | null) => void;
  sel: Set<string>;
  onToggle: (v: string) => void;
  options: { v: string; label: string; dot?: string }[];
}) {
  return (
    <div className={cn("dd", open === group && "open")}>
      <button
        className={cn("ddbtn", sel.size > 0 && "active")}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(open === group ? null : group);
        }}
      >
        <span>{label}</span>
        {sel.size > 0 && <span className="cnt">{sel.size}</span>}
        <ChevronDown className="caret" />
      </button>
      <div className="ddmenu" onClick={(e) => e.stopPropagation()}>
        {options.length === 0 ? (
          <div className="dim" style={{ padding: "7px 9px", fontSize: 12 }}>
            None available
          </div>
        ) : (
          options.map((o) => (
            <label key={o.v}>
              <input type="checkbox" checked={sel.has(o.v)} onChange={() => onToggle(o.v)} />
              {o.dot && <span className="dot" style={{ background: o.dot }} />}
              {o.label}
            </label>
          ))
        )}
      </div>
    </div>
  );
}

const CTL_CSS = `
.ctl{--surface:#fff;--surface-2:#eef1f6;--inset:#f8fafc;--ink:#18213a;--ink-2:#586178;--ink-3:#8a92a6;--border:#e4e8f0;--border-strong:#ccd3e0;--accent:#3b4aa0;--accent-ink:#fff;--accent-soft:#eaecf8;--good:#2f875f;--good-soft:#e4f1ea;--warn:#a3690a;--warn-soft:#faf1de;--bad:#bb4835;--bad-soft:#fbeae6;--cyan:#1c7f96;--cyan-soft:#e2f3f6;--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--pop:0 12px 30px rgba(20,26,40,.16);--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,monospace;height:100%;display:flex;flex-direction:column;min-height:0;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .ctl{--surface:#141922;--surface-2:#1b2130;--inset:#10141d;--ink:#e8ebf3;--ink-2:#9aa3b8;--ink-3:#6b7488;--border:#242b39;--border-strong:#333c4e;--accent:#8290e6;--accent-ink:#0c0f16;--accent-soft:#1f2740;--good:#5cbf90;--good-soft:#15271f;--warn:#e0ab54;--warn-soft:#2c2313;--bad:#e2705c;--bad-soft:#301c18;--cyan:#5fc2d8;--cyan-soft:#132a2e;--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4);--pop:0 14px 34px rgba(0,0,0,.55)}
.ctl .ic{width:15px;height:15px;flex:none}
.ctl .dim{color:var(--ink-3)} .ctl .num{font-variant-numeric:tabular-nums}
.ctl .sp{flex:1}
.ctl .top{flex:none;border-bottom:1px solid var(--border);background:var(--surface);padding:12px 20px}
.ctl .toprow{display:flex;align-items:center;gap:12px}
.ctl .top h1{margin:0;font-size:16px;font-weight:660;letter-spacing:-.015em}
.ctl .top .sub{color:var(--ink-3);font-weight:500;font-size:12.5px}
.ctl .stat{display:flex;gap:14px;align-items:center;font-size:12px;color:var(--ink-2)}
.ctl .stat b{color:var(--ink);font-weight:650}
.ctl .stat .warn{color:var(--warn)} .ctl .stat .crit{color:var(--bad)}
.ctl .btn{display:inline-flex;align-items:center;gap:6px;padding:6px 11px;border-radius:8px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;font-size:12px;cursor:pointer;font-family:var(--sans)}
.ctl .btn:hover{background:var(--surface-2)}
.ctl .btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)} .ctl .btn.pri:hover{filter:brightness(1.06)}
.ctl .btn.sm{padding:5px 9px}
.ctl .toolbar{display:flex;align-items:center;gap:8px;margin-top:11px;flex-wrap:wrap}
.ctl .search{display:flex;align-items:center;gap:8px;background:var(--inset);border:1px solid var(--border-strong);border-radius:8px;padding:6px 10px;min-width:230px;color:var(--ink-3)}
.ctl .search input{border:0;background:none;outline:none;color:var(--ink);width:100%;font-size:12.5px}
.ctl .dd{position:relative}
.ctl .ddbtn{display:flex;align-items:center;gap:6px;padding:6px 10px;border:1px solid var(--border-strong);border-radius:8px;background:var(--surface);color:var(--ink-2);font-weight:600;font-size:12.5px;cursor:pointer}
.ctl .ddbtn:hover{background:var(--surface-2)}
.ctl .ddbtn.active{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}
.ctl .ddbtn .caret{width:12px;height:12px}
.ctl .ddbtn .cnt{font:700 10px var(--mono);background:var(--accent);color:var(--accent-ink);border-radius:99px;padding:1px 6px}
.ctl .ddmenu{position:absolute;top:calc(100% + 5px);left:0;min-width:190px;background:var(--surface);border:1px solid var(--border);border-radius:10px;box-shadow:var(--pop);padding:6px;display:none;z-index:15}
.ctl .dd.open .ddmenu{display:block}
.ctl .ddmenu label{display:flex;align-items:center;gap:9px;padding:7px 9px;border-radius:7px;font-size:12.5px;cursor:pointer}
.ctl .ddmenu label:hover{background:var(--surface-2)}
.ctl .ddmenu input{width:15px;height:15px;accent-color:var(--accent)}
.ctl .ddmenu .dot{width:8px;height:8px;border-radius:2px;flex:none}
.ctl .clear{font-size:12px;color:var(--accent);font-weight:600;padding:6px 4px;cursor:pointer}
.ctl .brief{flex:none;margin:14px 20px 0;border:1px solid color-mix(in srgb,var(--accent) 22%,var(--border));border-radius:13px;background:var(--surface);box-shadow:var(--shadow);overflow:hidden}
.ctl .briefhd{display:flex;align-items:center;gap:10px;padding:11px 15px}
.ctl .aiglyph{width:28px;height:28px;border-radius:8px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;font-size:15px}
.ctl .briefhd b{font-size:13px} .ctl .briefhd .when{color:var(--ink-3);font-size:11px}
.ctl .briefhd .tog{margin-left:auto;font-size:12px;font-weight:600;color:var(--accent);padding:5px 8px;border-radius:7px;background:none;border:0;cursor:pointer}
.ctl .briefhd .tog:hover{background:var(--surface-2)}
.ctl .brief.collapsed .briefbody{display:none}
.ctl .brief.collapsed .oneline{display:block}
.ctl .oneline{display:none;padding:0 15px 12px;font-size:12.5px;color:var(--ink-2)}
.ctl .oneline b{color:var(--ink)}
.ctl .briefbody{padding:0 15px 15px;display:flex;flex-direction:column;gap:13px}
.ctl .summary{margin:0;font-size:13.5px;line-height:1.62;color:var(--ink)}
.ctl .summary b{font-weight:680} .ctl .summary .c{color:var(--bad);font-weight:680} .ctl .summary .w{color:var(--warn);font-weight:680}
.ctl .tiles{display:grid;grid-template-columns:repeat(6,1fr);gap:9px}
@media(max-width:1100px){.ctl .tiles{grid-template-columns:repeat(3,1fr)}}
.ctl .tile{border:1px solid var(--border);border-radius:10px;padding:9px 11px;background:var(--inset)}
.ctl .tile .tn{font:700 20px/1 var(--mono);letter-spacing:-.02em}
.ctl .tile .tl{font-size:10.5px;color:var(--ink-3);margin-top:4px}
.ctl .tile.a .tn{color:var(--accent)} .ctl .tile.g .tn{color:var(--good)} .ctl .tile.w .tn{color:var(--warn)} .ctl .tile.c .tn{color:var(--bad)}
.ctl .load{display:flex;flex-direction:column;gap:7px}
.ctl .loadhd{font:600 10px var(--sans);text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3)}
.ctl .loadgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px 22px}
@media(max-width:1100px){.ctl .loadgrid{grid-template-columns:1fr}}
.ctl .loadrow{display:grid;grid-template-columns:130px 1fr 40px;align-items:center;gap:10px}
.ctl .ldname{font-size:12px;font-weight:600;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ctl .ldbar{height:9px;border-radius:5px;background:var(--surface-2);overflow:hidden;display:flex}
.ctl .ldfill{background:var(--good);height:100%}
.ctl .ldn{font:600 11px var(--mono);color:var(--ink-2);text-align:right}
.ctl .attention{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ctl .attlbl{font:600 10px var(--sans);text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3)}
.ctl .attchip{display:inline-flex;align-items:center;gap:6px;padding:5px 11px;border-radius:99px;border:1px solid var(--border-strong);background:var(--surface);font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap}
.ctl .attchip:hover{filter:brightness(.98);background:var(--surface-2)}
.ctl .attchip.c{border-color:color-mix(in srgb,var(--bad) 40%,var(--border));color:var(--bad);background:var(--bad-soft)}
.ctl .attchip.w{border-color:color-mix(in srgb,var(--warn) 40%,var(--border));color:var(--warn);background:var(--warn-soft)}
.ctl .tablewrap{flex:1;min-height:0;overflow:auto;padding:0 8px}
.ctl table{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px}
.ctl thead th{position:sticky;top:0;z-index:2;background:var(--inset);text-align:left;font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);padding:9px 12px;border-bottom:1px solid var(--border);white-space:nowrap}
.ctl tbody td{padding:9px 12px;border-bottom:1px solid var(--border);vertical-align:middle;background:var(--surface)}
.ctl tbody tr{cursor:pointer}
.ctl tbody tr:hover td{background:var(--inset)}
.ctl td.c-idx,.ctl th.c-idx{width:34px;text-align:right;padding-right:4px}
.ctl .idx{font:600 11px var(--mono);color:var(--ink-3)}
.ctl .req{display:flex;gap:10px;align-items:flex-start;min-width:0}
.ctl .chan{width:30px;height:30px;border-radius:8px;flex:none;display:grid;place-items:center;background:var(--surface-2);color:var(--ink-2)}
.ctl .req .nm{font-weight:650;color:var(--ink)}
.ctl .prev{color:var(--ink-3);font-size:11.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:300px;margin-top:1px}
.ctl .cp{font-weight:600;color:var(--ink)} .ctl .ty{color:var(--ink-2);font-size:11.5px;margin-top:1px}
.ctl .val{font:600 12px var(--mono);color:var(--ink)}
.ctl .stagewrap{min-width:186px}
.ctl .stagehead{display:flex;align-items:center;gap:7px}
.ctl .sdot{width:8px;height:8px;border-radius:50%;flex:none}
.ctl .sdot.ok{background:var(--good)} .ctl .sdot.done{background:var(--ink-3)}
.ctl .stagenm{font-weight:660;color:var(--ink);font-size:13px;letter-spacing:-.01em}
.ctl .stepno{margin-left:auto;font:600 11px var(--mono);color:var(--ink-2)}
.ctl .stepbar{display:flex;gap:2px;margin-top:7px;max-width:200px}
.ctl .seg{height:7px;flex:1 1 0;min-width:3px;border-radius:2px;background:var(--surface-2)}
.ctl .seg.done{background:color-mix(in srgb,var(--good) 42%,transparent)}
.ctl .seg.cur{background:var(--good)}
.ctl .wflabel{margin-top:6px;font-size:10.5px;color:var(--ink-3)}
.ctl .flags{display:flex;gap:5px;flex-wrap:wrap;align-items:center}
.ctl .tag{display:inline-flex;align-items:center;font:600 10.5px var(--sans);letter-spacing:.02em;padding:2px 8px;border-radius:99px;white-space:nowrap}
.ctl .tag.slate{background:var(--surface-2);color:var(--ink-2)}
.ctl .tag.blue{background:var(--accent-soft);color:var(--accent)}
.ctl .tag.green{background:var(--good-soft);color:var(--good)}
.ctl .tag.amber{background:var(--warn-soft);color:var(--warn)}
.ctl .tag.red{background:var(--bad-soft);color:var(--bad)}
.ctl .tag.violet{background:var(--accent-soft);color:var(--accent)}
.ctl .tag.cyan{background:var(--cyan-soft);color:var(--cyan)}
.ctl .exp{font-size:12px;color:var(--ink)} .ctl .expsub{font-size:11px;color:var(--ink-3);margin-top:1px}
.ctl .exp .soon{color:var(--warn);font-weight:650} .ctl .exp .past{color:var(--bad);font-weight:650}
.ctl .rowact{display:flex;gap:6px;justify-content:flex-end;white-space:nowrap}
.ctl .empty{padding:60px;text-align:center;color:var(--ink-3);font-size:13px}
@media(max-width:1200px){.ctl .prev,.ctl .c-cp{display:none}}
@media(max-width:960px){.ctl .c-exp{display:none}}
`;
