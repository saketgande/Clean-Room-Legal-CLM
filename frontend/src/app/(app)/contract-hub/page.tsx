"use client";

import { Suspense, useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload, Plus, X } from "lucide-react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  PieChart,
  Pie,
  AreaChart,
  Area,
} from "recharts";
import {
  contractsApi,
  obligationsApi,
  renewalsApi,
} from "@/lib/endpoints";
import { CenterSpinner, Modal, Button, Field, Input } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { fmtDate, nextStages, titleCase } from "@/lib/utils";
import type { ContractResponse } from "@/lib/types";

// ---- helpers --------------------------------------------------------------
type Lane = "drafting" | "negotiation" | "execution" | "active";
const LANES: {
  id: Lane;
  title: string;
  sub: string;
  dot: string;
  prefix: string;
}[] = [
  { id: "drafting", title: "Drafting", sub: "Contracts being prepared", dot: "#2563eb", prefix: "DRF" },
  { id: "negotiation", title: "Negotiation", sub: "Back-and-forth with counterparty", dot: "#d97706", prefix: "NEG" },
  { id: "execution", title: "Execution", sub: "Signatures and closing steps", dot: "#7c3aed", prefix: "EXE" },
  { id: "active", title: "Active", sub: "In force — monitor & renew", dot: "#16a34a", prefix: "LIV" },
];
const LANE_COLOR: Record<Lane, string> = {
  drafting: "#2563eb",
  negotiation: "#d97706",
  execution: "#7c3aed",
  active: "#16a34a",
};
function laneFor(stage: string): Lane | null {
  if (["intake", "drafting", "ai_review"].includes(stage)) return "drafting";
  if (["internal_review", "counterparty_review", "approval_pending"].includes(stage))
    return "negotiation";
  if (["approved", "signature_pending"].includes(stage)) return "execution";
  if (["active", "renewal_due"].includes(stage)) return "active";
  return null;
}
function riskTone(r?: string | null): "red" | "amber" | "green" | "blue" {
  const v = (r ?? "").toLowerCase();
  if (v === "high" || v === "critical") return "red";
  if (v === "medium") return "amber";
  if (v === "low") return "green";
  return "blue";
}
const ACCENT = { red: "#dc2626", amber: "#d97706", green: "#16a34a", blue: "#2563eb" };
function compactMoney(n: number): string {
  if (!n) return "$0";
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${Math.round(n / 1_000)}K`;
  return `$${Math.round(n)}`;
}
function displayId(prefix: string, id: string): string {
  return `${prefix}-${id.replace(/[^a-zA-Z0-9]/g, "").slice(-4).toUpperCase()}`;
}
function daysUntil(d?: string | null): number | null {
  if (!d) return null;
  const t = new Date(d).getTime();
  if (Number.isNaN(t)) return null;
  return Math.round((t - Date.now()) / 86_400_000);
}

type ChipFilter =
  | "all"
  | "mine"
  | "at-risk"
  | "redlines"
  | "expiring"
  | "deviations";
type ViewMode = "board" | "table" | "analytics";

export default function ContractHubPage() {
  return (
    <Suspense fallback={<CenterSpinner label="Loading Contract Hub…" />}>
      <ContractHub />
    </Suspense>
  );
}

function ContractHub() {
  const router = useRouter();
  const qc = useQueryClient();
  const { user } = useAuth();
  const { notify } = useToast();

  const [view, setView] = useState<ViewMode>("board");
  const [chip, setChip] = useState<ChipFilter>("all");
  const [laneFilter, setLaneFilter] = useState<Lane | null>(null);
  const [drawer, setDrawer] = useState<ContractResponse | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);

  const list = useQuery({ queryKey: ["contracts"], queryFn: contractsApi.list });
  const hub = useQuery({ queryKey: ["contract-hub"], queryFn: contractsApi.hub });
  const renewals = useQuery({
    queryKey: ["renewals"],
    queryFn: () => renewalsApi.list(),
  });
  const obligations = useQuery({
    queryKey: ["obligations"],
    queryFn: () => obligationsApi.list(),
  });

  const ids = useMemo(
    () => (list.data ?? []).map((c) => c.id),
    [list.data],
  );

  const redlineSet = useQuery({
    queryKey: ["hub-redline-set", ids.join(",")],
    enabled: (chip === "redlines" || chip === "deviations") && ids.length > 0,
    queryFn: async () => {
      const pairs = await Promise.all(
        ids.map(async (id) => {
          try {
            const e = await contractsApi.edits(id, "proposed");
            return [id, (e?.length ?? 0) > 0] as const;
          } catch {
            return [id, false] as const;
          }
        }),
      );
      return new Set(pairs.filter(([, v]) => v).map(([id]) => id));
    },
  });

  // Days-in-stage derived from each contract's stage history (lazy; bounded
  // by portfolio size; only when board/analytics is visible).
  const stageDays = useQuery({
    queryKey: ["hub-stage-days", ids.join(",")],
    enabled: ids.length > 0 && view !== "table",
    queryFn: async () => {
      const map = new Map<string, number>();
      await Promise.all(
        ids.map(async (id) => {
          try {
            const h = await contractsApi.stageHistory(id);
            const last = h[h.length - 1];
            if (last?.changed_at) {
              const d = Math.max(
                0,
                Math.round(
                  (Date.now() - new Date(last.changed_at).getTime()) /
                    86_400_000,
                ),
              );
              map.set(id, d);
            }
          } catch {
            /* ignore */
          }
        }),
      );
      return map;
    },
  });

  const chipFilter = useCallback(
    (c: ContractResponse): boolean => {
      const NINETY = 90 * 86_400_000;
      switch (chip) {
        case "mine":
          return c.owner_user_id === user?.id;
        case "at-risk":
          return ["high", "critical"].includes(
            (c.risk_level ?? "").toLowerCase(),
          );
        case "expiring": {
          const d = daysUntil(c.expiration_date);
          return d !== null && d >= 0 && d <= NINETY / 86_400_000;
        }
        case "redlines":
        case "deviations":
          return redlineSet.data ? redlineSet.data.has(c.id) : false;
        default:
          return true;
      }
    },
    [chip, user?.id, redlineSet.data],
  );

  const contracts = useMemo(
    () => (list.data ?? []).filter(chipFilter),
    [list.data, chipFilter],
  );

  // ---- derived analytics ----
  const totalValue = useMemo(
    () => contracts.reduce((s, c) => s + (c.value_amount ?? 0), 0),
    [contracts],
  );
  const atRiskValue = useMemo(
    () =>
      contracts
        .filter((c) =>
          ["high", "critical"].includes((c.risk_level ?? "").toLowerCase()),
        )
        .reduce((s, c) => s + (c.value_amount ?? 0), 0),
    [contracts],
  );
  const atRiskCount = contracts.filter((c) =>
    ["high", "critical"].includes((c.risk_level ?? "").toLowerCase()),
  ).length;
  const expiring30 = contracts.filter((c) => {
    const d = daysUntil(c.expiration_date);
    return d !== null && d >= 0 && d <= 30;
  }).length;
  const avgCycle = hub.data?.widgets?.average_cycle_time_days ?? null;

  const laneCounts = useMemo(() => {
    const m: Record<Lane, number> = {
      drafting: 0,
      negotiation: 0,
      execution: 0,
      active: 0,
    };
    for (const c of contracts) {
      const l = laneFor(c.lifecycle_stage);
      if (l) m[l]++;
    }
    return m;
  }, [contracts]);

  const throughput = LANES.map((l) => ({
    label: l.title,
    lane: l.id,
    count: laneCounts[l.id],
  }));

  const riskMix = useMemo(() => {
    let onPb = 0,
      watch = 0,
      high = 0;
    for (const c of contracts) {
      const t = riskTone(c.risk_level);
      if (t === "red") high++;
      else if (t === "amber") watch++;
      else onPb++;
    }
    return [
      { name: "On-playbook", value: onPb, color: "#16a34a" },
      { name: "Watch", value: watch, color: "#eab308" },
      { name: "High risk", value: high, color: "#dc2626" },
    ];
  }, [contracts]);
  const riskTotal = riskMix.reduce((s, r) => s + r.value, 0);

  const friction = (hub.data?.widgets?.counterparty_friction ?? []).slice(0, 4);
  const maxFriction = Math.max(1, ...friction.map((f) => f.count));

  const renewalBuckets = useMemo(() => {
    const valOf = (cid: string) =>
      (list.data ?? []).find((c) => c.id === cid)?.value_amount ?? 0;
    const b = [
      { label: "0–30 d", n: 0, v: 0, color: "#dc2626" },
      { label: "30–60 d", n: 0, v: 0, color: "#d97706" },
      { label: "60–90 d", n: 0, v: 0, color: "#2563eb" },
    ];
    for (const r of renewals.data ?? []) {
      const d = daysUntil(r.notice_date ?? r.expiration_date);
      if (d === null || d < 0 || d > 90) continue;
      const i = d <= 30 ? 0 : d <= 60 ? 1 : 2;
      b[i].n++;
      b[i].v += valOf(r.contract_id);
    }
    return b;
  }, [renewals.data, list.data]);
  const renewalTotal = renewalBuckets.reduce((s, x) => s + x.v, 0);

  const valueByType = useMemo(() => {
    const m = new Map<string, number>();
    for (const c of contracts)
      m.set(
        c.contract_type || "other",
        (m.get(c.contract_type || "other") ?? 0) + (c.value_amount ?? 0),
      );
    const palette = ["#2563eb", "#7c3aed", "#d97706", "#16a34a", "#db2777", "#0891b2"];
    return [...m.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([name, value], i) => ({
        name: titleCase(name),
        value,
        color: palette[i % palette.length],
      }));
  }, [contracts]);
  const typeTotal = valueByType.reduce((s, t) => s + t.value, 0);

  const deviations = (hub.data?.widgets?.top_deviated_clauses ?? []).slice(0, 6);
  const maxDev = Math.max(1, ...deviations.map((d) => d.count));

  const bottleneck = useMemo(() => {
    const m: Record<Lane, number> = {
      drafting: 0,
      negotiation: 0,
      execution: 0,
      active: 0,
    };
    if (!stageDays.data) return m;
    for (const c of contracts) {
      const l = laneFor(c.lifecycle_stage);
      const d = stageDays.data.get(c.id) ?? 0;
      if (l && d > 14) m[l]++;
    }
    return m;
  }, [contracts, stageDays.data]);
  const slowLane = (Object.entries(bottleneck) as [Lane, number][]).sort(
    (a, b) => b[1] - a[1],
  )[0];

  const boardLanes = useMemo(() => {
    const m: Record<Lane, ContractResponse[]> = {
      drafting: [],
      negotiation: [],
      execution: [],
      active: [],
    };
    for (const c of contracts) {
      const l = laneFor(c.lifecycle_stage);
      if (l && (!laneFilter || laneFilter === l)) m[l].push(c);
    }
    return m;
  }, [contracts, laneFilter]);

  const actionItems = useMemo(() => {
    const items: { title: string; detail: string; tone: keyof typeof ACCENT; href: string }[] = [];
    for (const o of (obligations.data ?? []).filter(
      (x) => x.status === "overdue" || x.status === "due_soon",
    ))
      items.push({
        title: o.description.slice(0, 70),
        detail: `${titleCase(o.status)} · due ${fmtDate(o.due_date)}`,
        tone: o.status === "overdue" ? "red" : "amber",
        href: `/contracts/${o.contract_id}`,
      });
    return items.slice(0, 6);
  }, [obligations.data]);

  if (list.isLoading) return <CenterSpinner label="Loading Contract Hub…" />;

  const total = list.data?.length ?? 0;

  return (
    <div className="cc-root">
      <style>{CC_STYLES}</style>

      {/* TOP BAND */}
      <div className="cc-top">
        <div className="cc-hero cc-panel">
          <div className="cc-hero-head">
            <div>
              <h1>
                Contracts Hub<span> Command Center</span>
              </h1>
              <p>
                Dense, operational, measurable — steer the portfolio and work
                straight from the page. {total} contracts under management.
              </p>
            </div>
            <div className="cc-hero-actions">
              <button className="cc-btn-sec" onClick={() => setUploadOpen(true)}>
                <Upload size={13} /> Import
              </button>
              <button
                className="cc-btn-pri"
                onClick={() => setUploadOpen(true)}
              >
                <Plus size={13} /> New Contract
              </button>
            </div>
          </div>
          <div className="cc-toolbar">
            <div className="cc-seg">
              {(["board", "table", "analytics"] as ViewMode[]).map((v) => (
                <button
                  key={v}
                  className={view === v ? "active" : ""}
                  onClick={() => setView(v)}
                >
                  {titleCase(v)}
                </button>
              ))}
            </div>
            {(
              [
                ["all", "All"],
                ["mine", "Mine"],
                ["at-risk", "At risk"],
                ["redlines", "Redlines"],
                ["expiring", "Expiring"],
                ["deviations", "Deviations"],
              ] as [ChipFilter, string][]
            ).map(([id, label]) => (
              <button
                key={id}
                className={`cc-chip ${chip === id ? "active" : ""}`}
                onClick={() => setChip(id)}
              >
                {label}
                {(id === "redlines" || id === "deviations") &&
                  chip === id &&
                  redlineSet.isFetching &&
                  " …"}
              </button>
            ))}
          </div>
        </div>

        <div className="cc-snapshot cc-panel">
          <div className="cc-eyebrow">Portfolio Snapshot</div>
          <div className="cc-stat-grid">
            <Stat
              label="Active Value"
              value={compactMoney(totalValue)}
              accent={ACCENT.blue}
              chip={{ text: "Live", bg: "#dbeafe", fg: "#1e40af" }}
              sub={`${total} contracts under management`}
            />
            <Stat
              label="At-risk Exposure"
              value={compactMoney(atRiskValue)}
              valueColor="#b91c1c"
              accent={ACCENT.red}
              chip={
                atRiskCount > 0
                  ? { text: "High", bg: "#fee2e2", fg: "#b91c1c" }
                  : undefined
              }
              sub={`${atRiskCount} deals at risk`}
            />
            <Stat
              label="Expiring in 30d"
              value={String(expiring30)}
              valueColor="#b45309"
              accent={ACCENT.amber}
              chip={{ text: "Act", bg: "#fef3c7", fg: "#b45309" }}
              sub="renewal window open"
            />
            <Stat
              label="Avg Cycle Time"
              value={avgCycle != null ? `${avgCycle.toFixed(0)}d` : "—"}
              accent="#94a3b8"
              chip={{ text: "On target", bg: "#dcfce7", fg: "#166534" }}
              sub="median lifetime"
            />
          </div>
        </div>
      </div>

      {/* ANALYTICS ROW (always on) */}
      <div className="cc-analytics">
        <div className="cc-section cc-panel">
          <div className="cc-sec-head">
            <h2>Stage Throughput</h2>
            {laneFilter ? (
              <button
                className="cc-filter-pill"
                style={{ background: LANE_COLOR[laneFilter] }}
                onClick={() => setLaneFilter(null)}
              >
                Filtering: {titleCase(laneFilter)} <X size={11} />
              </button>
            ) : (
              <span className="cc-hint">Click a stage to filter</span>
            )}
          </div>
          <ResponsiveContainer width="100%" height={170}>
            <BarChart data={throughput}>
              <XAxis
                dataKey="label"
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 11, fill: "#64748b", fontWeight: 600 }}
              />
              <YAxis hide />
              <Tooltip
                cursor={{ fill: "rgba(15,23,42,0.04)" }}
                contentStyle={{
                  borderRadius: 8,
                  border: "1px solid #e2e8f0",
                  boxShadow: "0 8px 20px rgba(15,23,42,.08)",
                  fontSize: 12,
                }}
              />
              <Bar
                dataKey="count"
                radius={[6, 6, 0, 0]}
                onClick={(d: { lane?: Lane }) =>
                  d?.lane && setLaneFilter(d.lane)
                }
              >
                {throughput.map((t) => (
                  <Cell
                    key={t.lane}
                    fill={LANE_COLOR[t.lane as Lane]}
                    fillOpacity={
                      !laneFilter || laneFilter === t.lane ? 1 : 0.28
                    }
                    stroke={laneFilter === t.lane ? "#0f172a" : undefined}
                    strokeWidth={laneFilter === t.lane ? 1.5 : 0}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="cc-section cc-panel">
          <div className="cc-sec-head">
            <h2>Risk Mix</h2>
          </div>
          <div className="cc-donut-wrap">
            <ResponsiveContainer width="100%" height={140}>
              <PieChart>
                <Pie
                  data={riskTotal ? riskMix : [{ name: "—", value: 1, color: "#e2e8f0" }]}
                  dataKey="value"
                  innerRadius={38}
                  outerRadius={60}
                  paddingAngle={riskMix.filter((r) => r.value).length > 1 ? 3 : 0}
                  stroke="none"
                >
                  {(riskTotal ? riskMix : [{ color: "#e2e8f0" }]).map((r, i) => (
                    <Cell key={i} fill={r.color} />
                  ))}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
            <div className="cc-donut-center">
              <strong>{riskTotal}</strong>
              <span>total</span>
            </div>
          </div>
          <div className="cc-legend">
            {riskMix.map((r) => (
              <span key={r.name}>
                <i style={{ background: r.color }} />
                {r.name} ({r.value})
              </span>
            ))}
          </div>
        </div>

        <div className="cc-section cc-panel">
          <div className="cc-sec-head">
            <h2>Counterparty Friction</h2>
          </div>
          {friction.length === 0 ? (
            <p className="cc-empty-txt">
              Not enough activity to measure friction yet.
            </p>
          ) : (
            <div className="cc-bars">
              {friction.map((f) => (
                <div key={f.counterparty_name} className="cc-bar">
                  <span className="cc-bar-name">{f.counterparty_name}</span>
                  <span className="cc-track">
                    <span
                      className="cc-fill"
                      style={{ width: `${(f.count / maxFriction) * 100}%` }}
                    />
                  </span>
                  <span className="cc-bar-val">{f.count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* CONTRACT BRAIN STRIP */}
      <div className="cc-brain">
        <div>
          <div className="cc-eyebrow">Contract Brain</div>
          <strong>Ask the graph</strong>
        </div>
        <div className="cc-pills">
          {[
            "Why is this deal stuck?",
            "Compare to precedent",
            "Show major playbook deviations",
            "Draft outreach to counterparty",
            "Summarize obligations due this week",
          ].map((p) => (
            <button
              key={p}
              className="cc-pill"
              onClick={() => router.push("/brain")}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* ANALYTICS DEEP-DIVE */}
      {view === "analytics" && (
        <div className="cc-exec-grid">
          <div className="cc-widget">
            <h3>
              Cycle Time Trend <span>median days</span>
            </h3>
            <div className="cc-cycle-big">
              {avgCycle != null ? avgCycle.toFixed(0) : "—"}
            </div>
            <p className="cc-w-note">median days · intake → signed</p>
          </div>

          <div className="cc-widget">
            <h3>
              Value by Contract Type <span>top 6</span>
            </h3>
            <div className="cc-type-row">
              <div style={{ width: 100, height: 100, position: "relative" }}>
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={
                        typeTotal
                          ? valueByType
                          : [{ name: "—", value: 1, color: "#e2e8f0" }]
                      }
                      dataKey="value"
                      innerRadius={28}
                      outerRadius={48}
                      paddingAngle={2}
                      stroke="none"
                    >
                      {(typeTotal
                        ? valueByType
                        : [{ color: "#e2e8f0" }]
                      ).map((t, i) => (
                        <Cell key={i} fill={t.color} />
                      ))}
                    </Pie>
                  </PieChart>
                </ResponsiveContainer>
                <div className="cc-donut-center sm">
                  <strong>{compactMoney(typeTotal)}</strong>
                </div>
              </div>
              <div className="cc-type-legend">
                {valueByType.map((t) => (
                  <div key={t.name}>
                    <i style={{ background: t.color }} />
                    <span>{t.name}</span>
                    <b>{compactMoney(t.value)}</b>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="cc-widget">
            <h3>
              Renewal Pipeline <span>$ at stake · 90d</span>
            </h3>
            <div className="cc-w-big amber">{compactMoney(renewalTotal)}</div>
            <div className="cc-pipeline">
              {renewalBuckets.map((b) => (
                <div
                  key={b.label}
                  style={{
                    flex: Math.max(b.v, renewalTotal ? 0 : 1),
                    background: b.color,
                  }}
                >
                  {b.v > 0 ? compactMoney(b.v) : ""}
                </div>
              ))}
            </div>
            <div className="cc-legend">
              {renewalBuckets.map((b) => (
                <span key={b.label}>
                  <i style={{ background: b.color }} />
                  {b.label} ({b.n})
                </span>
              ))}
            </div>
          </div>

          <div className="cc-widget">
            <h3>
              Top Playbook Deviations <span>by clause</span>
            </h3>
            {deviations.length === 0 ? (
              <p className="cc-empty-txt">No deviations recorded.</p>
            ) : (
              <div className="cc-bars">
                {deviations.map((d, i) => (
                  <div key={d.clause_type} className="cc-bar">
                    <span className="cc-bar-name">
                      {titleCase(d.clause_type)}
                    </span>
                    <span className="cc-track">
                      <span
                        className="cc-fill"
                        style={{
                          width: `${(d.count / maxDev) * 100}%`,
                          background:
                            i < 2
                              ? "linear-gradient(90deg,#dc2626,#f87171)"
                              : i < 4
                                ? "linear-gradient(90deg,#d97706,#fbbf24)"
                                : undefined,
                        }}
                      />
                    </span>
                    <span className="cc-bar-val">{d.count}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="cc-widget">
            <h3>
              Stage Bottleneck <span>&gt; 14d in stage</span>
            </h3>
            <div className="cc-bottleneck">
              {LANES.map((l) => {
                const n = bottleneck[l.id];
                const cls = n >= 5 ? "hot" : n >= 2 ? "warm" : "";
                return (
                  <div key={l.id} className={`cc-bn-cell ${cls}`}>
                    <span>{l.title}</span>
                    <strong>{n}</strong>
                  </div>
                );
              })}
            </div>
            <p className="cc-w-note">
              {slowLane && slowLane[1] > 0
                ? `${titleCase(slowLane[0])} is the slow stage — ${slowLane[1]} deals over SLA.`
                : "No stages bottlenecked."}
            </p>
          </div>

          <div className="cc-widget">
            <h3>
              Action Required <span>today</span>
            </h3>
            {actionItems.length === 0 ? (
              <p className="cc-empty-txt">Queue is clear.</p>
            ) : (
              <div className="cc-rail-list">
                {actionItems.map((a, i) => (
                  <button
                    key={i}
                    className="cc-item"
                    onClick={() => router.push(a.href)}
                  >
                    <span
                      className="cc-item-dot"
                      style={{ background: ACCENT[a.tone] }}
                    />
                    <span>
                      <b>{a.title}</b>
                      <em>{a.detail}</em>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* BOARD + RAIL */}
      {view === "board" && (
        <div className="cc-layout">
          <div className="cc-board">
            {LANES.filter((l) => !laneFilter || laneFilter === l.id).map(
              (l) => {
                const items = boardLanes[l.id];
                return (
                  <div key={l.id} className="cc-lane cc-panel">
                    <div className="cc-lane-head">
                      <div className="cc-lane-top">
                        <span
                          className="cc-lane-dot"
                          style={{ background: l.dot }}
                        />
                        <span className="cc-lane-title">{l.title}</span>
                        <span className="cc-lane-count">{items.length}</span>
                      </div>
                      <div className="cc-lane-sub">{l.sub}</div>
                    </div>
                    <div className="cc-lane-body">
                      {items.length === 0 ? (
                        <div className="cc-lane-empty">Nothing in this lane</div>
                      ) : (
                        items.map((c) => (
                          <BoardCard
                            key={c.id}
                            c={c}
                            prefix={l.prefix}
                            days={stageDays.data?.get(c.id) ?? null}
                            onOpen={() => setDrawer(c)}
                            onNav={() => router.push(`/contracts/${c.id}`)}
                          />
                        ))
                      )}
                    </div>
                  </div>
                );
              },
            )}
          </div>

          <div className="cc-rail">
            <div className="cc-panel cc-rail-panel">
              <div className="cc-eyebrow">Priority Queue</div>
              {actionItems.length === 0 ? (
                <p className="cc-empty-txt">Queue is clear.</p>
              ) : (
                <div className="cc-rail-list">
                  {actionItems.slice(0, 3).map((a, i) => (
                    <button
                      key={i}
                      className="cc-item"
                      onClick={() => router.push(a.href)}
                    >
                      <b>{a.title}</b>
                      <em>{a.detail}</em>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="cc-panel cc-rail-panel">
              <div className="cc-eyebrow">Negotiation Analytics</div>
              <div className="cc-rail-list">
                <div className="cc-item">
                  <b>Deviation-heavy deals</b>
                  <em>
                    {(hub.data?.widgets?.top_deviated_clauses ?? []).length}{" "}
                    clause types flagged
                  </em>
                </div>
                <div className="cc-item">
                  <b>Pending approvals</b>
                  <em>
                    {hub.data?.widgets?.pending_approvals ?? 0} awaiting sign-off
                  </em>
                </div>
                <div className="cc-item">
                  <b>Overdue obligations</b>
                  <em>
                    {hub.data?.widgets?.overdue_obligations ?? 0} need attention
                  </em>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* TABLE (board + table views) */}
      {view !== "analytics" && (
        <div className="cc-table-wrap cc-panel">
          <table className="cc-table">
            <thead>
              <tr>
                <th>Contract</th>
                <th>Counterparty</th>
                <th>Stage</th>
                <th>Risk</th>
                <th>Value</th>
                <th>Expires</th>
              </tr>
            </thead>
            <tbody>
              {contracts.map((c) => (
                <tr
                  key={c.id}
                  onClick={() => router.push(`/contracts/${c.id}`)}
                  style={{ cursor: "pointer" }}
                >
                  <td className="cc-td-title">
                    <Link
                      href={`/contracts/${c.id}`}
                      onClick={(e) => e.stopPropagation()}
                      className="cc-row-link"
                    >
                      {c.title}
                    </Link>
                  </td>
                  <td>{c.counterparty_name ?? "—"}</td>
                  <td>{titleCase(c.lifecycle_stage)}</td>
                  <td>
                    <span
                      className="cc-tag"
                      style={tagStyle(riskTone(c.risk_level))}
                    >
                      {c.risk_level ? titleCase(c.risk_level) : "—"}
                    </span>
                  </td>
                  <td>{compactMoney(c.value_amount ?? 0)}</td>
                  <td>{fmtDate(c.expiration_date)}</td>
                </tr>
              ))}
              {contracts.length === 0 && (
                <tr>
                  <td colSpan={6} className="cc-empty-txt">
                    No contracts match this filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* DRAWER */}
      {drawer && (
        <>
          <div
            className="cc-drawer-backdrop"
            onClick={() => setDrawer(null)}
          />
          <aside className="cc-drawer">
            <div className="cc-drawer-head">
              <span className="cc-eyebrow">Contract Drawer</span>
              <button className="cc-close" onClick={() => setDrawer(null)}>
                Close
              </button>
            </div>
            <div className="cc-drawer-meta">
              {displayId(
                LANES.find((l) => l.id === laneFor(drawer.lifecycle_stage))
                  ?.prefix ?? "CON",
                drawer.id,
              )}
            </div>
            <h2 className="cc-drawer-title">{drawer.title}</h2>
            <p className="cc-drawer-sum">
              {titleCase(drawer.contract_type || "Contract")} ·{" "}
              {drawer.counterparty_name ?? "—"}
            </p>
            <div className="cc-box-grid">
              <Box k="Stage" v={titleCase(drawer.lifecycle_stage)} />
              <Box k="Value" v={compactMoney(drawer.value_amount ?? 0)} />
              <Box
                k="Risk"
                v={drawer.risk_level ? titleCase(drawer.risk_level) : "—"}
              />
              <Box k="Expires" v={fmtDate(drawer.expiration_date)} />
            </div>
            <div className="cc-drawer-actions">
              <button
                className="cc-btn-pri"
                onClick={() => router.push(`/contracts/${drawer.id}`)}
              >
                Open full contract
              </button>
              <button
                className="cc-btn-sec"
                onClick={() => router.push(`/assistant?contract=${drawer.id}`)}
              >
                Ask Brain
              </button>
            </div>
          </aside>
        </>
      )}

      <UploadModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploaded={(id) => {
          qc.invalidateQueries({ queryKey: ["contracts"] });
          qc.invalidateQueries({ queryKey: ["contract-hub"] });
          notify("Contract uploaded", "success");
          router.push(`/contracts/${id}`);
        }}
      />
    </div>
  );
}

// ---- small pieces ---------------------------------------------------------
function Stat({
  label,
  value,
  valueColor,
  accent,
  chip,
  sub,
}: {
  label: string;
  value: string;
  valueColor?: string;
  accent: string;
  chip?: { text: string; bg: string; fg: string };
  sub: string;
}) {
  return (
    <div className="cc-stat" style={{ borderLeft: `3px solid ${accent}` }}>
      <div className="cc-stat-head">
        <span>{label}</span>
        {chip && (
          <span
            className="cc-stat-chip"
            style={{ background: chip.bg, color: chip.fg }}
          >
            {chip.text}
          </span>
        )}
      </div>
      <div className="cc-stat-val" style={{ color: valueColor }}>
        {value}
      </div>
      <div className="cc-stat-sub">{sub}</div>
    </div>
  );
}

function Box({ k, v }: { k: string; v: string }) {
  return (
    <div className="cc-box">
      <span>{k}</span>
      <b>{v}</b>
    </div>
  );
}

function tagStyle(tone: "red" | "amber" | "green" | "blue") {
  const map = {
    red: { background: "#fee2e2", color: "#b91c1c" },
    amber: { background: "#fef3c7", color: "#b45309" },
    green: { background: "#dcfce7", color: "#166534" },
    blue: { background: "#dbeafe", color: "#1e40af" },
  };
  return map[tone];
}

function BoardCard({
  c,
  prefix,
  days,
  onOpen,
  onNav,
}: {
  c: ContractResponse;
  prefix: string;
  days: number | null;
  onOpen: () => void;
  onNav: () => void;
}) {
  const tone = riskTone(c.risk_level);
  const expD = daysUntil(c.expiration_date);
  const tag =
    tone === "red"
      ? { t: "At risk", tone }
      : expD !== null && expD >= 0 && expD <= 90
        ? { t: "Expiring", tone: "amber" as const }
        : { t: "On track", tone: "green" as const };
  return (
    <div
      className="cc-card"
      style={{ borderLeft: `4px solid ${ACCENT[tone]}` }}
      onClick={onOpen}
      onDoubleClick={onNav}
    >
      <div className="cc-card-meta">
        {displayId(prefix, c.id)}
        {days != null ? ` · ${days}d in stage` : ""}
      </div>
      <h4>{c.title}</h4>
      <p className="cc-card-sum">
        {titleCase(c.contract_type || "Contract")} ·{" "}
        {c.counterparty_name ?? "—"}
      </p>
      <div className="cc-card-grid">
        <div className="cc-datum">
          <span>Value</span>
          <b>{compactMoney(c.value_amount ?? 0)}</b>
        </div>
        <div className="cc-datum">
          <span>Stage</span>
          <b>{titleCase(c.lifecycle_stage)}</b>
        </div>
      </div>
      <div className="cc-card-tags">
        <span className="cc-tag" style={tagStyle(tag.tone)}>
          {tag.t}
        </span>
        {nextStages(c.lifecycle_stage).length === 0 && (
          <span className="cc-tag" style={tagStyle("blue")}>
            Terminal
          </span>
        )}
      </div>
      <Link
        href={`/contracts/${c.id}`}
        onClick={(e) => e.stopPropagation()}
        className="cc-card-open"
      >
        Open contract →
      </Link>
    </div>
  );
}

function UploadModal({
  open,
  onClose,
  onUploaded,
}: {
  open: boolean;
  onClose: () => void;
  onUploaded: (id: string) => void;
}) {
  const { notify } = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit() {
    if (!file) return;
    setBusy(true);
    try {
      const res = await contractsApi.upload(file, {
        title: title || undefined,
      });
      onUploaded(res.contract.id);
      setFile(null);
      setTitle("");
      onClose();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Upload failed", "error");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New contract"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!file}>
            Upload
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Contract file" hint="PDF, DOCX, DOC, TXT, PNG, JPEG">
          <label className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center text-sm text-slate-600 hover:border-brand-400">
            {file ? file.name : "Click to choose a file"}
            <input
              type="file"
              className="hidden"
              accept=".pdf,.docx,.doc,.txt,.png,.jpg,.jpeg"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
        </Field>
        <Field label="Title" hint="Optional — extracted if blank">
          <Input value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

// ---- styles (original, AEGIS-matched tokens) ------------------------------
const CC_STYLES = `
.cc-root{max-width:1780px;margin:0 auto;padding:4px 0 48px;display:flex;flex-direction:column;gap:14px;color:#201C15;font-size:14px}
.cc-panel{background:#fff;border:1px solid #E0DCD0;border-radius:18px;box-shadow:0 18px 40px rgba(40,33,22,.06)}
.cc-top{display:grid;grid-template-columns:1.1fr .9fr;gap:14px}
@media(max-width:1100px){.cc-top{grid-template-columns:1fr}}
.cc-hero{padding:20px 22px}
.cc-hero-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}
.cc-hero h1{font-family:var(--font-serif),Georgia,serif;font-size:32px;font-weight:500;letter-spacing:-.02em;margin:0}
.cc-hero h1 span{font-family:var(--font-sans);font-size:15px;font-weight:500;letter-spacing:normal;color:#7F7665}
.cc-hero p{color:#7F7665;font-size:13.5px;max-width:740px;margin:8px 0 0}
.cc-hero-actions{display:flex;gap:8px;flex-shrink:0}
.cc-btn-sec{display:inline-flex;align-items:center;gap:6px;background:#fff;border:1px solid #E0DCD0;border-radius:10px;padding:8px 12px;font-size:12px;font-weight:600;color:#201C15;cursor:pointer}
.cc-btn-pri{display:inline-flex;align-items:center;gap:6px;background:#2F4A38;color:#fff;border:1px solid #2F4A38;border-radius:10px;padding:8px 12px;font-size:12px;font-weight:600;cursor:pointer}
.cc-toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:16px}
.cc-seg{display:inline-flex;padding:4px;background:#F6F4EF;border:1px solid #E0DCD0;border-radius:12px}
.cc-seg button{padding:7px 12px;border-radius:9px;font-size:12px;font-weight:600;color:#7F7665;background:transparent;border:0;cursor:pointer}
.cc-seg button.active{background:#2F4A38;color:#fff}
.cc-chip{background:#fff;border:1px solid #E0DCD0;border-radius:999px;padding:8px 12px;font-size:12px;font-weight:600;color:#4B4639;cursor:pointer}
.cc-chip.active{background:#2F4A38;border-color:#2F4A38;color:#fff}
.cc-snapshot{padding:18px}
.cc-eyebrow{font-size:11px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:#A39A86}
.cc-stat-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:10px}
.cc-stat{background:#F6F4EF;border:1px solid #E0DCD0;border-radius:10px;padding:10px 12px}
.cc-stat-head{display:flex;justify-content:space-between;align-items:center}
.cc-stat-head span{font-size:9.5px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#A39A86}
.cc-stat-chip{font-size:8.5px;font-weight:700;text-transform:uppercase;padding:2px 6px;border-radius:999px}
.cc-stat-val{font-family:var(--font-serif),Georgia,serif;font-size:24px;font-weight:500;letter-spacing:-.02em;margin:4px 0 2px}
.cc-stat-sub{font-size:10.5px;color:#7F7665}
.cc-analytics{display:grid;grid-template-columns:1.2fr .9fr .9fr;gap:12px}
@media(max-width:1100px){.cc-analytics{grid-template-columns:1fr}}
.cc-section{padding:16px}
.cc-sec-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.cc-sec-head h2{font-size:11px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:#A39A86;margin:0}
.cc-hint{font-size:11px;font-style:italic;color:#A39A86}
.cc-filter-pill{display:inline-flex;align-items:center;gap:5px;color:#fff;border:0;border-radius:999px;padding:4px 9px;font-size:10px;font-weight:700;cursor:pointer}
.cc-donut-wrap{position:relative}
.cc-donut-center{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;pointer-events:none}
.cc-donut-center strong{font-family:var(--font-serif),Georgia,serif;font-size:24px;font-weight:500}
.cc-donut-center.sm strong{font-size:13px}
.cc-donut-center span{font-size:9px;text-transform:uppercase;color:#A39A86;letter-spacing:.1em}
.cc-legend{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin-top:8px;font-size:11px;color:#635C4D}
.cc-legend span{display:inline-flex;align-items:center;gap:5px}
.cc-legend i{width:9px;height:9px;border-radius:999px;display:inline-block}
.cc-bars{display:flex;flex-direction:column;gap:9px;margin-top:4px}
.cc-bar{display:grid;grid-template-columns:110px 1fr 36px;align-items:center;gap:8px}
.cc-bar-name{font-size:11.5px;color:#635C4D;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cc-track{height:9px;background:#EDEAE1;border-radius:999px;overflow:hidden}
.cc-fill{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,#2F4A38,#6F8463)}
.cc-bar-val{font-size:12px;font-weight:700;text-align:right}
.cc-empty-txt{font-size:12px;color:#A39A86;text-align:center;padding:18px 0}
.cc-brain{background:#ECEFEA;border:1px solid #D8DFD3;border-radius:18px;display:grid;grid-template-columns:200px 1fr;gap:14px;padding:16px 18px;align-items:center}
@media(max-width:780px){.cc-brain{grid-template-columns:1fr}}
.cc-brain strong{font-family:var(--font-serif),Georgia,serif;display:block;font-size:17px;font-weight:500;color:#201C15;margin-top:2px}
.cc-pills{display:flex;flex-wrap:wrap;gap:8px}
.cc-pill{background:#fff;border:1px solid #D8DFD3;border-radius:11px;padding:8px 12px;font-size:12.5px;font-weight:600;color:#274032;cursor:pointer}
.cc-pill:hover{background:#D8DFD3}
.cc-exec-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
@media(max-width:1100px){.cc-exec-grid{grid-template-columns:1fr 1fr}}
@media(max-width:780px){.cc-exec-grid{grid-template-columns:1fr}}
.cc-widget{background:#fff;border:1px solid #E0DCD0;border-radius:14px;padding:14px 16px;box-shadow:0 6px 18px rgba(40,33,22,.04);display:flex;flex-direction:column;gap:10px}
.cc-widget h3{font-size:10.5px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:#A39A86;margin:0;display:flex;justify-content:space-between}
.cc-widget h3 span{text-transform:none;letter-spacing:normal;color:#A39A86;font-weight:500}
.cc-w-big{font-family:var(--font-serif),Georgia,serif;font-size:24px;font-weight:500;letter-spacing:-.02em}
.cc-w-big.amber{color:#b45309}.cc-w-big.red{color:#b91c1c}
.cc-w-note{font-size:11px;color:#A39A86;margin:0}
.cc-cycle-big{font-family:var(--font-serif),Georgia,serif;font-size:48px;font-weight:500;letter-spacing:-.03em}
.cc-pipeline{display:flex;height:26px;border-radius:8px;overflow:hidden;border:1px solid #E0DCD0}
.cc-pipeline div{display:flex;align-items:center;justify-content:center;color:#fff;font-size:10.5px;font-weight:700;min-width:0}
.cc-bottleneck{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
.cc-bn-cell{background:#F6F4EF;border:1px solid #E0DCD0;border-radius:8px;padding:8px;text-align:center}
.cc-bn-cell span{font-size:9.5px;text-transform:uppercase;color:#A39A86;letter-spacing:.08em}
.cc-bn-cell strong{display:block;font-family:var(--font-serif),Georgia,serif;font-size:24px;font-weight:500;margin-top:2px}
.cc-bn-cell.warm{background:#fef3c7;border-color:#fde68a}.cc-bn-cell.warm strong{color:#b45309}
.cc-bn-cell.hot{background:#fee2e2;border-color:#fecaca}.cc-bn-cell.hot strong{color:#b91c1c}
.cc-type-row{display:flex;align-items:center;gap:14px}
.cc-type-legend{flex:1;display:flex;flex-direction:column;gap:5px;font-size:11.5px}
.cc-type-legend>div{display:grid;grid-template-columns:10px 1fr auto;align-items:center;gap:7px}
.cc-type-legend i{width:9px;height:9px;border-radius:3px}
.cc-type-legend b{font-weight:700}
.cc-layout{display:grid;grid-template-columns:1fr 320px;gap:14px;align-items:start}
@media(max-width:1380px){.cc-layout{grid-template-columns:1fr}}
.cc-board{display:grid;grid-template-columns:repeat(4,minmax(220px,1fr));gap:10px;overflow-x:auto}
.cc-lane{height:620px;display:flex;flex-direction:column;overflow:hidden}
.cc-lane-head{padding:12px 14px;background:#F6F4EF;border-bottom:1px solid #E0DCD0}
.cc-lane-top{display:flex;align-items:center;gap:8px}
.cc-lane-dot{width:9px;height:9px;border-radius:999px}
.cc-lane-title{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#635C4D;flex:1}
.cc-lane-count{font-family:var(--font-serif),Georgia,serif;font-size:22px;font-weight:500;color:#201C15}
.cc-lane-sub{font-size:11.5px;color:#7F7665;margin-top:3px}
.cc-lane-body{flex:1;overflow-y:auto;padding:10px;display:flex;flex-direction:column;gap:10px}
.cc-lane-empty{border:1px dashed #CBC5B5;border-radius:10px;text-align:center;font-size:11.5px;color:#A39A86;padding:24px 8px}
.cc-card{background:#FBFAF6;border:1px solid #E0DCD0;border-radius:13px;padding:12px;cursor:pointer;transition:transform .12s,box-shadow .12s}
.cc-card:hover{transform:translateY(-2px);box-shadow:0 10px 18px rgba(40,33,22,.08)}
.cc-card-meta{font-family:ui-monospace,Menlo,monospace;font-size:10px;color:#A39A86}
.cc-card h4{font-family:var(--font-serif),Georgia,serif;font-size:16px;font-weight:500;letter-spacing:-.01em;margin:5px 0 3px}
.cc-card-sum{font-size:12px;color:#7F7665;margin:0}
.cc-card-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:9px 0}
.cc-datum{background:#fff;border:1px solid #E0DCD0;border-radius:9px;padding:8px}
.cc-datum span{font-size:10px;text-transform:uppercase;color:#A39A86}
.cc-datum b{display:block;font-size:12px;font-weight:700;margin-top:2px}
.cc-card-tags{display:flex;flex-wrap:wrap;gap:5px}
.cc-card{cursor:pointer}
.cc-card-open{display:inline-block;margin-top:8px;font-size:11px;font-weight:700;color:#2F4A38;text-decoration:none}
.cc-card-open:hover{text-decoration:underline}
.cc-row-link{color:inherit;text-decoration:none;font-weight:600}
.cc-row-link:hover{color:#2F4A38;text-decoration:underline}
.cc-tag{border-radius:999px;padding:2px 8px;font-size:10px;font-weight:700;text-transform:uppercase}
.cc-rail{display:flex;flex-direction:column;gap:14px}
.cc-rail-panel{padding:16px}
.cc-rail-list{display:flex;flex-direction:column;gap:8px;margin-top:10px}
.cc-item{display:flex;flex-direction:column;align-items:flex-start;gap:2px;background:#F6F4EF;border:1px solid #E0DCD0;border-radius:11px;padding:10px 12px;width:100%;text-align:left;cursor:pointer}
.cc-item b{font-size:13.5px;font-weight:700}
.cc-item em{font-size:12px;color:#7F7665;font-style:normal}
.cc-item-dot{width:8px;height:8px;border-radius:999px;margin-bottom:4px}
.cc-table-wrap{overflow:hidden}
.cc-table{width:100%;border-collapse:collapse;font-size:13px}
.cc-table th{text-align:left;padding:12px 16px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:#A39A86;background:#F6F4EF;border-bottom:1px solid #E0DCD0}
.cc-table td{padding:12px 16px;border-bottom:1px solid #EDEAE1;color:#4B4639}
.cc-table tr{cursor:pointer}
.cc-table tbody tr:hover{background:#F6F4EF}
.cc-td-title{font-weight:600;color:#201C15}
.cc-drawer-backdrop{position:fixed;inset:0;background:rgba(33,28,21,.35);z-index:190}
.cc-drawer{position:fixed;top:0;right:0;height:100%;width:min(420px,100vw);background:#fff;border-left:1px solid #E0DCD0;box-shadow:-20px 0 40px rgba(40,33,22,.12);padding:20px;z-index:200;overflow-y:auto;animation:cc-slide .18s ease}
@keyframes cc-slide{from{transform:translateX(100%)}to{transform:translateX(0)}}
.cc-drawer-head{display:flex;justify-content:space-between;align-items:center}
.cc-close{background:#fff;border:1px solid #E0DCD0;border-radius:9px;padding:5px 10px;font-size:12px;font-weight:700;color:#7F7665;cursor:pointer}
.cc-drawer-meta{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:#A39A86;margin-top:14px}
.cc-drawer-title{font-family:var(--font-serif),Georgia,serif;font-size:25px;font-weight:500;letter-spacing:-.02em;margin:4px 0}
.cc-drawer-sum{font-size:13px;color:#7F7665;margin:0 0 14px}
.cc-box-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.cc-box{background:#F6F4EF;border:1px solid #E0DCD0;border-radius:12px;padding:12px}
.cc-box span{font-size:10px;text-transform:uppercase;color:#A39A86}
.cc-box b{display:block;font-size:13px;font-weight:700;margin-top:3px}
.cc-drawer-actions{display:flex;flex-direction:column;gap:8px;margin-top:16px;border-top:1px solid #E0DCD0;padding-top:16px}
`;
