"use client";

/**
 * Aegis Command — the Operations Wall.
 *
 * Dedicated command center (landing page after login). Stations with real
 * hierarchy: Triage dominates the left, the Portfolio heat grid the right,
 * Pipeline / Risk / Engine / Radar support along the bottom. Everything is a
 * control: status segments cross-filter the wall, tiles and events open the
 * floating inspector, triage buttons act inline, ⌘K runs commands.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { approvalsApi, contractsApi } from "@/lib/endpoints";
import type { ConsoleResponse } from "@/lib/types";
import { cn, fmtDate, fmtRelative, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import { Button, Field, Input, Modal } from "@/components/ui";
import { X, Search, Upload } from "lucide-react";

type WallFilter =
  | { kind: "sla" }
  | { kind: "needs" }
  | { kind: "stage"; stage: string }
  | { kind: "health"; health: string }
  | null;

const SNOOZE_KEY = "aegis-triage-snooze";

function loadSnoozes(): Record<string, string> {
  if (typeof window === "undefined") return {};
  try {
    const raw = JSON.parse(localStorage.getItem(SNOOZE_KEY) ?? "{}");
    const today = new Date().toISOString().slice(0, 10);
    // drop expired snoozes (snoozed on a previous day)
    return Object.fromEntries(
      Object.entries(raw).filter(([, day]) => day === today),
    ) as Record<string, string>;
  } catch {
    return {};
  }
}

// Left-edge accent per health — turns each tile into a clean Fluent card whose
// coloured left border is the "heat" signal (works on light + dark surfaces).
const HEALTH_ACCENT: Record<string, string> = {
  critical: "border-l-rose-500",
  working: "border-l-amber-400",
  moving: "border-l-sky-400",
  healthy: "border-l-emerald-500",
  idle: "border-l-slate-300",
};

const HEALTH_DOT: Record<string, string> = {
  critical: "bg-rose-500",
  working: "bg-amber-400",
  moving: "bg-sky-400",
  healthy: "bg-emerald-400",
  idle: "bg-slate-400",
};

const STAGE_ORDER = ["intake", "drafting", "review", "approval", "signature", "active", "closed"];

function Station({
  led,
  title,
  meta,
  controls,
  className,
  children,
}: {
  led: string;
  title: string;
  meta?: string;
  controls?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={cn("flex min-h-0 flex-col overflow-hidden rounded-md border border-slate-200 bg-slate-100", className)}>
      <div className="flex flex-none items-center gap-2 border-b border-slate-200 bg-slate-50/70 px-3 py-1.5">
        <span className={cn("h-1.5 w-1.5 rounded-full", led)} />
        <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{title}</span>
        {meta && <span className="tabular-nums text-[10.5px] text-slate-400">{meta}</span>}
        <div className="ml-auto flex gap-1.5">{controls}</div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">{children}</div>
    </section>
  );
}

function Ctrl({ on, onClick, children }: { on?: boolean; onClick?: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "rounded-md border px-2 py-0.5 tabular-nums text-[10.5px]",
        on
          ? "border-brand-400/60 bg-brand-50 text-brand-700 dark:bg-brand-400/10 dark:text-brand-300"
          : "border-slate-200 text-slate-400 hover:border-brand-300 hover:text-brand-600",
      )}
    >
      {children}
    </button>
  );
}

export default function CommandPage() {
  const router = useRouter();
  const { notify } = useToast();
  const qc = useQueryClient();

  const [filter, setFilter] = useState<WallFilter>(null);
  const [inspected, setInspected] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [autoOnly, setAutoOnly] = useState(false);
  const [heatSort, setHeatSort] = useState<"health" | "stage" | "value">("health");
  const [snoozed, setSnoozed] = useState<Record<string, string>>({});
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);

  useEffect(() => setSnoozed(loadSnoozes()), []);

  const { data, isLoading } = useQuery<ConsoleResponse>({
    queryKey: ["command-console"],
    queryFn: contractsApi.console,
    refetchInterval: paused ? false : 30_000,
  });

  // ⌘K global listener
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
      if (e.key === "Escape") {
        setPaletteOpen(false);
        setInspected(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const archiveMutation = useMutation({
    mutationFn: (id: string) => contractsApi.update(id, { archived: true }),
    onSuccess: () => {
      notify("Archived — removed from the wall", "success");
      qc.invalidateQueries({ queryKey: ["command-console"] });
    },
    onError: () =>
      notify("Archive failed — try from the contract page", "error"),
  });

  const scoreAll = async (ids: string[]) => {
    notify(`Scoring ${ids.length} contract${ids.length === 1 ? "" : "s"} — results land shortly`, "info");
    await Promise.allSettled(ids.map((id) => contractsApi.computeRisk(id)));
    qc.invalidateQueries({ queryKey: ["command-console"] });
  };

  const snooze = (id: string) => {
    const today = new Date().toISOString().slice(0, 10);
    const next = { ...snoozed, [id]: today };
    setSnoozed(next);
    try {
      localStorage.setItem(SNOOZE_KEY, JSON.stringify(next));
    } catch {}
    notify("Snoozed until tomorrow", "success");
  };

  const matches = (row: { sla_breached?: boolean; stage?: string; health?: string; contract_id: string }) => {
    if (!filter) return true;
    if (filter.kind === "sla") return !!row.sla_breached;
    if (filter.kind === "stage") return row.stage === filter.stage;
    if (filter.kind === "health") return row.health === filter.health;
    if (filter.kind === "needs")
      return (data?.triage ?? []).some((t) => t.contract_id === row.contract_id);
    return true;
  };

  const triage = useMemo(
    () => (data?.triage ?? []).filter((t) => !snoozed[t.contract_id]).filter(matches),
    [data, snoozed, filter],
  );
  const tiles = useMemo(() => {
    let rows = (data?.register ?? []).filter(matches);
    if (heatSort === "stage")
      rows = [...rows].sort((a, b) => STAGE_ORDER.indexOf(a.stage) - STAGE_ORDER.indexOf(b.stage));
    if (heatSort === "value") rows = [...rows].sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
    return rows;
  }, [data, filter, heatSort]);

  const clearedToday = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    return (data?.engine_log ?? []).filter((e) => e.auto && e.ts.slice(0, 10) === today).length;
  }, [data]);

  const filterLabel =
    filter?.kind === "sla"
      ? "past SLA"
      : filter?.kind === "needs"
        ? "needs you"
        : filter?.kind === "stage"
          ? `stage: ${filter.stage}`
          : filter?.kind === "health"
            ? `health: ${filter.health}`
            : null;

  if (isLoading || !data)
    return <div className="flex h-full items-center justify-center text-sm text-slate-400">Bringing the wall online…</div>;

  const s = data.strip;
  const engine = (data.engine_log ?? []).filter((e) => (autoOnly ? e.auto : true));
  const money = (v: number) => (v >= 1000 ? `$${Math.round(v / 1000)}K` : `$${Math.round(v)}`);

  const triageActions = (t: ConsoleResponse["triage"][number]) => {
    const open = (label: string) => (
      <Link
        href={`/contracts/${t.contract_id}`}
        className="rounded-lg bg-brand-600 px-3 py-1.5 text-center text-[11px] font-bold text-white shadow-sm hover:bg-brand-700"
      >
        {label}
      </Link>
    );
    return (
      <div className="flex flex-none flex-col justify-center gap-1.5">
        {t.kind === "send" && open("Send →")}
        {t.kind === "work" && open("Open issues →")}
        {t.kind === "decide" && open("Decide →")}
        {t.kind === "classify" && open("Classify →")}
        {t.kind === "nudge" && open("Nudge →")}
        {t.kind === "move" && open("Advance →")}
        {t.kind === "classify" ? (
          <button
            onClick={() => archiveMutation.mutate(t.contract_id)}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-[11px] font-semibold text-slate-500 hover:border-slate-400 hover:text-slate-700"
          >
            Archive
          </button>
        ) : (
          <button
            onClick={() => snooze(t.contract_id)}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-[11px] font-semibold text-slate-500 hover:border-slate-400 hover:text-slate-700"
          >
            Snooze 1d
          </button>
        )}
      </div>
    );
  };

  const inspectedRow = data.register.find((r) => r.contract_id === inspected) ?? null;
  const inspectedTriage = data.triage.find((t) => t.contract_id === inspected) ?? null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* ===== command bar ===== */}
      <div className="flex flex-none items-center gap-3 border-b border-slate-200 px-4 py-2.5">
        <h1 className="font-serif text-lg text-slate-900">Aegis Command</h1>
        <span className="flex items-center gap-1.5 tabular-nums text-[10.5px] font-bold tracking-wide text-brand-600 dark:text-brand-400">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-500" />
          LIVE
        </span>
        {filterLabel && (
          <button
            onClick={() => setFilter(null)}
            className="flex items-center gap-1.5 rounded-full border border-brand-400/50 bg-brand-50 py-1 pl-3 pr-1.5 text-[10.5px] font-bold text-brand-700 dark:bg-brand-400/10 dark:text-brand-300"
          >
            FILTER: {filterLabel}
            <span className="grid h-4 w-4 place-items-center rounded-full bg-brand-200/60 text-[10.5px] dark:bg-brand-400/20">✕</span>
          </button>
        )}
        <button
          onClick={() => setUploadOpen(true)}
          className="ml-auto flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-bold text-white shadow-sm hover:bg-brand-700"
        >
          <Upload size={12} /> New contract
        </button>
        <button
          onClick={() => setPaletteOpen(true)}
          className="flex min-w-[300px] items-center gap-2 rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-400 hover:border-brand-300"
        >
          <Search size={12} />
          “send anthropic nda” · “open dataviz”…
          <span className="ml-auto rounded border border-slate-200 px-1.5 tabular-nums text-[10.5px]">⌘K</span>
        </button>
      </div>

      {/* ===== wall ===== */}
      <div className="grid min-h-0 flex-1 grid-cols-12 gap-2.5 overflow-auto p-2.5" style={{ gridTemplateRows: "auto minmax(0,3fr) minmax(0,1.2fr)" }}>
        {/* status station */}
        <div className="col-span-12 grid grid-cols-4 overflow-hidden rounded-md border border-slate-200 bg-slate-100 lg:grid-cols-7">
          {[
            { v: String(s.sla_breaches), k: "Past SLA", sub: "breached stages", cls: s.sla_breaches ? "text-rose-500" : "", f: { kind: "sla" } as WallFilter },
            { v: String(s.needs_you), k: "Need you", sub: "triage below", cls: s.needs_you ? "text-amber-500" : "", f: { kind: "needs" } as WallFilter },
            { v: String(s.to_sign), k: "To sign", sub: "approved · unsent", cls: s.to_sign ? "text-brand-600 dark:text-brand-400" : "", f: { kind: "stage", stage: "signature" } as WallFilter },
            { v: String(s.approvals_pending), k: "Approvals", sub: `${s.approvals_overdue} overdue`, cls: "", f: { kind: "stage", stage: "approval" } as WallFilter },
            { v: money(s.live_value), k: "In motion", sub: `${s.contracts} contracts`, cls: "", f: null },
            { v: s.automated_pct != null ? `${s.automated_pct}%` : "—", k: "Automated", sub: "moves · 7d", cls: "text-emerald-500", f: null },
            { v: s.cycle_median_days != null ? `${s.cycle_median_days}d` : "—", k: "Cycle", sub: "median", cls: "", f: null },
          ].map((seg, i) => {
            const active =
              (seg.f?.kind === "sla" && filter?.kind === "sla") ||
              (seg.f?.kind === "needs" && filter?.kind === "needs") ||
              (seg.f?.kind === "stage" && filter?.kind === "stage" && filter.stage === (seg.f as { stage: string }).stage);
            return (
              <button
                key={seg.k}
                disabled={!seg.f}
                onClick={() => seg.f && setFilter(active ? null : seg.f)}
                className={cn(
                  "flex flex-col items-start px-4 py-2 text-left",
                  i > 0 && "border-l border-slate-200",
                  seg.f && "cursor-pointer hover:bg-brand-50/40 dark:hover:bg-brand-400/5",
                  active && "bg-brand-50 dark:bg-brand-400/10",
                )}
              >
                <span className={cn("tabular-nums text-xl font-bold leading-none", seg.v === "0" || seg.v === "—" ? "text-slate-300 dark:text-slate-600" : cn("text-slate-900", seg.cls))}>
                  {seg.v}
                </span>
                <span className="mt-0.5 text-[10px] font-extrabold uppercase tracking-wide text-slate-400">{seg.k}</span>
                <span className="text-[10.5px] text-slate-400">{seg.sub}</span>
              </button>
            );
          })}
        </div>

        {/* triage station */}
        <Station
          led="bg-rose-500"
          title="Triage"
          meta={`${triage.length} open · ranked by impact`}
          className="col-span-12 xl:col-span-5"
          controls={<Ctrl onClick={() => setFilter(null)} on={!filter}>all</Ctrl>}
        >
          <div className="space-y-2 p-2.5">
            {triage.length === 0 && (
              <p className="rounded-lg border border-dashed border-emerald-400/40 bg-emerald-50/40 px-3 py-2 text-[11px] text-emerald-700 dark:bg-emerald-400/5 dark:text-emerald-300">
                ✓ Triage clear — nothing needs you right now.
              </p>
            )}
            {triage.map((t) => (
              <div
                key={t.contract_id + t.kind}
                className={cn(
                  "flex gap-2.5 rounded-md border border-slate-200 border-l-[3px] bg-slate-50/60 p-2.5 hover:bg-slate-50",
                  t.sla_breached ? "border-l-rose-500" : t.kind === "decide" ? "border-l-brand-500" : "border-l-amber-400",
                )}
              >
                <span className="w-5 flex-none pt-0.5 tabular-nums text-[10px] text-slate-400">
                  {String(t.rank).padStart(2, "0")}
                </span>
                <button className="min-w-0 flex-1 text-left" onClick={() => setInspected(t.contract_id)}>
                  <p className="text-[13px] font-bold text-slate-900">{t.title}</p>
                  <p className="text-[10.5px] leading-relaxed text-slate-500">{t.detail}</p>
                  <p className="mt-0.5 tabular-nums text-[10.5px] text-slate-400">flagged: {t.reason}</p>
                </button>
                {triageActions(t)}
              </div>
            ))}
            {clearedToday > 0 && (
              <p className="rounded-lg border border-dashed border-emerald-400/40 px-3 py-1.5 text-[10.5px] text-emerald-600 dark:text-emerald-300">
                ✓ Engine handled {clearedToday} thing{clearedToday === 1 ? "" : "s"} today — see Engine station
              </p>
            )}
          </div>
        </Station>

        {/* portfolio heat station */}
        <Station
          led="bg-sky-500"
          title={`Portfolio · ${tiles.length}`}
          meta="tile = contract · color = health"
          className="col-span-12 xl:col-span-7"
          controls={
            <>
              <Ctrl on={heatSort === "health"} onClick={() => setHeatSort("health")}>health</Ctrl>
              <Ctrl on={heatSort === "stage"} onClick={() => setHeatSort("stage")}>stage</Ctrl>
              <Ctrl on={heatSort === "value"} onClick={() => setHeatSort("value")}>value</Ctrl>
            </>
          }
        >
          <div className="grid grid-cols-2 gap-2.5 bg-slate-50 p-2.5 xl:grid-cols-3">
            {tiles.map((r) => (
              <button
                key={r.contract_id}
                onClick={() => setInspected(r.contract_id)}
                className={cn(
                  "min-h-[86px] rounded-md border border-l-[3px] border-slate-200 bg-slate-100 p-2.5 text-left shadow-card transition-all hover:-translate-y-0.5 hover:shadow-pop",
                  HEALTH_ACCENT[r.health] ?? "border-l-slate-300",
                  r.health === "idle" && "opacity-70",
                )}
              >
                <div className="mb-1 flex items-center gap-1.5">
                  <span className={cn("h-1.5 w-1.5 flex-none rounded-full", HEALTH_DOT[r.health] ?? "bg-slate-400")} />
                  <span className="truncate tabular-nums text-[10px] tracking-wide text-slate-400">
                    {r.stage.toUpperCase()} · {r.days_in_stage}d
                  </span>
                </div>
                <p className="mb-1.5 line-clamp-2 text-[12px] font-medium leading-snug text-slate-900">{r.title}</p>
                <div className="flex flex-wrap items-center gap-1 tabular-nums text-[10px] text-slate-400">
                  {r.sla_breached && (
                    <span className="rounded-md bg-rose-100 px-1.5 py-0.5 font-bold text-rose-600 dark:bg-rose-400/15 dark:text-rose-300">
                      {r.days_in_stage}d ⚠ SLA {r.sla_days}
                    </span>
                  )}
                  {r.issues > 0 && (
                    <span className="rounded-md bg-amber-100 px-1.5 py-0.5 font-bold text-amber-600 dark:bg-amber-400/15 dark:text-amber-300">{r.issues} issues</span>
                  )}
                  {r.redlines > 0 && (
                    <span className="rounded-md bg-amber-100 px-1.5 py-0.5 font-bold text-amber-600 dark:bg-amber-400/15 dark:text-amber-300">{r.redlines} redlines</span>
                  )}
                  {r.health === "healthy" && (
                    <span className="rounded-md bg-emerald-100 px-1.5 py-0.5 font-bold text-emerald-600 dark:bg-emerald-400/15 dark:text-emerald-300">
                      {r.obligations > 0 ? `${r.obligations} obligations` : "monitoring"}
                    </span>
                  )}
                  {r.value != null && r.value > 0 && <span>{money(r.value)}</span>}
                  {r.risk_score != null && <span>risk {r.risk_score}</span>}
                </div>
              </button>
            ))}
            <button
              onClick={() => setUploadOpen(true)}
              className="grid min-h-[86px] place-items-center rounded-md border border-dashed border-slate-300 bg-slate-100/50 text-[11px] text-slate-400 hover:border-brand-400/60 hover:text-brand-600"
            >
              + New contract
            </button>
          </div>
        </Station>

        {/* pipeline station */}
        <Station led="bg-sky-500" title="Pipeline" meta="count / avg vs SLA" className="col-span-12 md:col-span-4 xl:col-span-4">
          <div className="p-1.5">
            {data.pipeline.map((p) => {
              const max = Math.max(...data.pipeline.map((x) => x.count), 1);
              const hot = p.breached > 0;
              const active = filter?.kind === "stage" && filter.stage === p.stage;
              return (
                <button
                  key={p.stage}
                  onClick={() => setFilter(active ? null : { kind: "stage", stage: p.stage })}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-lg px-2.5 py-1 text-[10px] hover:bg-brand-50/50 dark:hover:bg-brand-400/5",
                    active && "bg-brand-50 ring-1 ring-inset ring-brand-300 dark:bg-brand-400/10 dark:ring-brand-400/40",
                  )}
                >
                  <span className="w-16 text-left font-bold text-slate-500">{titleCase(p.stage)}</span>
                  <span className="h-2 flex-1 overflow-hidden rounded bg-slate-200/60">
                    <span className={cn("block h-full rounded", hot ? "bg-amber-400" : "bg-brand-400/80")} style={{ width: `${(p.count / max) * 100}%` }} />
                  </span>
                  <span className="w-3 text-right tabular-nums">{p.count}</span>
                  <span className={cn("w-16 text-right tabular-nums text-[10px]", hot ? "font-bold text-amber-500" : "text-slate-400")}>
                    {p.avg_days != null ? `${p.avg_days}d` : "—"}{p.sla_days != null ? `/${p.sla_days}d` : ""}{hot ? " ⚠" : ""}
                  </span>
                </button>
              );
            })}
          </div>
        </Station>

        {/* risk station */}
        <Station led="bg-amber-400" title="Risk" meta="weighted · explainable" className="col-span-12 md:col-span-4 xl:col-span-3">
          <div className="p-1">
            {data.risk_board
              .filter((r) => r.score != null)
              .map((r) => (
                <button key={r.contract_id} onClick={() => setInspected(r.contract_id)} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1 text-left hover:bg-brand-50/50 dark:hover:bg-brand-400/5">
                  <span className={cn("w-6 text-right tabular-nums text-[13px] font-extrabold", (r.score ?? 0) >= 65 ? "text-rose-500" : (r.score ?? 0) >= 35 ? "text-amber-500" : "text-emerald-500")}>
                    {r.score}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[11px] text-slate-700">{r.title}</span>
                  <span className="max-w-[80px] truncate tabular-nums text-[10px] text-slate-400">{r.top_driver}</span>
                </button>
              ))}
            {data.risk_board.filter((r) => r.score == null).length > 0 && (
              <p className="px-2.5 py-1 text-[10px] text-slate-400">
                {data.risk_board.filter((r) => r.score == null).length} not scored —{" "}
                <button
                  onClick={() => scoreAll(data.risk_board.filter((r) => r.score == null).map((r) => r.contract_id))}
                  className="text-brand-600 hover:underline"
                >
                  score now →
                </button>
              </p>
            )}
          </div>
        </Station>

        {/* radar station */}
        <Station led="bg-cyan-400" title="Radar · 120d" className="col-span-12 md:col-span-4 xl:col-span-5">
          {data.deadlines.length === 0 ? (
            <p className="px-3 py-3 text-[10.5px] text-slate-400">
              ✓ No notice windows, expirations or dated obligations inside 120 days.
            </p>
          ) : (
            <div className="px-4 pt-4">
              <div className="relative h-8 border-b border-slate-300/70">
                <span className="absolute bottom-[-4px] left-0 top-0 w-px bg-brand-500" />
                {data.deadlines.slice(0, 6).map((d, i) => (
                  <button
                    key={i}
                    onClick={() => setInspected(d.contract_id)}
                    className="absolute flex -translate-x-1/2 flex-col items-center gap-0.5"
                    style={{ left: `${Math.min(96, Math.max(2, (d.days / 120) * 100))}%`, top: i % 2 ? 14 : 0 }}
                  >
                    <span className={cn("h-2 w-2 rounded-full border-2 border-slate-100", d.kind === "renewal" ? "bg-amber-400" : d.kind === "expiry" ? "bg-cyan-400" : "bg-sky-400")} />
                    <span className="whitespace-nowrap text-[10px] text-slate-400">{d.what.slice(0, 18)} · {d.days}d</span>
                  </button>
                ))}
              </div>
              <div className="flex justify-between py-1 tabular-nums text-[10px] text-slate-400">
                <span>now</span><span>+30d</span><span>+60d</span><span>+90d</span><span>+120d</span>
              </div>
            </div>
          )}
        </Station>

        {/* engine station */}
        <Station
          led="bg-brand-500"
          title="Engine · live"
          meta={`${engine.length} events`}
          className="col-span-12"
          controls={
            <>
              <Ctrl on={autoOnly} onClick={() => setAutoOnly((v) => !v)}>auto only</Ctrl>
              <Ctrl on={paused} onClick={() => setPaused((v) => !v)}>{paused ? "resume" : "pause"}</Ctrl>
            </>
          }
        >
          <div className="flex items-center gap-5 overflow-x-auto px-3 py-2 tabular-nums text-[10px] text-slate-500">
            {engine.length === 0 && <span className="text-slate-400">No engine activity yet.</span>}
            {engine.map((e, i) => (
              <button key={i} onClick={() => e.contract_id && setInspected(e.contract_id)} className="flex flex-none items-center gap-1.5 hover:text-brand-600">
                <span className="text-slate-400">{fmtRelative(e.ts)}</span>
                <span className="max-w-[320px] truncate text-slate-600 dark:text-slate-300">{e.event}</span>
                {e.auto && <span className="rounded bg-brand-100 px-1 text-[10px] font-bold text-brand-700 dark:bg-brand-400/15 dark:text-brand-300">auto</span>}
              </button>
            ))}
          </div>
        </Station>
      </div>

      {/* ===== inspector ===== */}
      {inspectedRow && (
        <div className="fixed right-5 top-24 z-40 w-[300px] max-w-[calc(100vw-2.5rem)] rounded-lg border border-slate-300 bg-slate-100 px-5 py-4 shadow-pop">
          <button onClick={() => setInspected(null)} className="absolute right-2.5 top-2.5 text-slate-400 hover:text-slate-600">
            <X size={14} />
          </button>
          <p className="pr-5 font-serif text-[14px] leading-snug text-slate-900">{inspectedRow.title}</p>
          <p className="mb-2 tabular-nums text-[10.5px] uppercase text-slate-400">
            {inspectedRow.stage} · {inspectedRow.days_in_stage}d{inspectedRow.sla_breached ? " ⚠" : ""} ·
            {inspectedRow.risk_score != null ? ` risk ${inspectedRow.risk_score}` : " unscored"}
          </p>
          <InspectorBody row={inspectedRow} triage={inspectedTriage} />
          <div className="mt-2.5 grid grid-cols-2 gap-1.5">
            <Link href={`/contracts/${inspectedRow.contract_id}`} className="col-span-2 rounded-lg bg-brand-600 py-2 text-center text-[11.5px] font-bold text-white hover:bg-brand-700">
              {inspectedTriage
                ? { send: "Send for signature →", work: "Open issues →", decide: "Decide redlines →", classify: "Classify →", nudge: "Open approvals →", move: "Advance stage →" }[inspectedTriage.kind] ?? "Open contract →"
                : "Open contract →"}
            </Link>
            <Link href={`/contracts/${inspectedRow.contract_id}`} className="rounded-lg border border-slate-300 py-1.5 text-center text-[10.5px] font-semibold text-slate-500 hover:text-slate-700">
              Full contract
            </Link>
            <Link href="/brain" className="rounded-lg border border-slate-300 py-1.5 text-center text-[10.5px] font-semibold text-slate-500 hover:text-slate-700">
              Ask Brain
            </Link>
          </div>
        </div>
      )}

      <UploadModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploaded={(id) => {
          qc.invalidateQueries({ queryKey: ["command-console"] });
          router.push(`/contracts/${id}`);
        }}
      />

      {/* ===== ⌘K palette ===== */}
      {paletteOpen && (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm" onClick={() => setPaletteOpen(false)}>
          <div className="mx-auto mt-28 w-full max-w-[520px] overflow-hidden rounded-lg border border-slate-300 bg-slate-100 shadow-pop" onClick={(e) => e.stopPropagation()}>
            <Palette
              data={data}
              onGo={(id) => {
                setPaletteOpen(false);
                router.push(`/contracts/${id}`);
              }}
              onInspect={(id) => {
                setPaletteOpen(false);
                setInspected(id);
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function InspectorBody({
  row,
  triage,
}: {
  row: ConsoleResponse["register"][number];
  triage: ConsoleResponse["triage"][number] | null;
}) {
  const { data: chain } = useQuery({
    queryKey: ["chain", row.contract_id],
    queryFn: () => approvalsApi.chain(row.contract_id),
    staleTime: 60_000,
  });
  const { data: contract } = useQuery({
    queryKey: ["contract", row.contract_id],
    queryFn: () => contractsApi.get(row.contract_id),
    staleTime: 60_000,
  });
  const drivers = (contract?.risk_summary?.drivers ?? []).slice(0, 3);
  return (
    <div className="space-y-2">
      <div className="border-t border-slate-200 pt-2">
        <p className="mb-1 text-[10px] font-extrabold uppercase tracking-[0.15em] text-slate-400">Why flagged</p>
        {triage ? (
          <>
            <p className="text-[11px] text-slate-600">{triage.detail}</p>
            <p className="mt-0.5 tabular-nums text-[10.5px] text-slate-400">{triage.reason}</p>
          </>
        ) : (
          <p className="text-[11px] text-slate-500">
            {row.health === "healthy" ? "Healthy — monitored, nothing pending." : `${titleCase(row.stage)} · ${row.days_in_stage}d in stage.`}
          </p>
        )}
      </div>
      {chain && chain.steps.length > 0 && (
        <div className="border-t border-slate-200 pt-2">
          <p className="mb-1 text-[10px] font-extrabold uppercase tracking-[0.15em] text-slate-400">Chain</p>
          <div className="flex flex-wrap items-center gap-1 text-[10.5px]">
            {chain.steps.map((st, i) => (
              <span key={i} className={cn("rounded-full px-2 py-0.5 font-bold", st.status === "approved" ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-400/15 dark:text-emerald-300" : st.overdue ? "bg-amber-100 text-amber-700 dark:bg-amber-400/15 dark:text-amber-300" : "bg-slate-200 text-slate-500")}>
                {st.status === "approved" ? "✓ " : ""}{st.approver_label}
              </span>
            ))}
          </div>
        </div>
      )}
      {drivers.length > 0 && (
        <div className="border-t border-slate-200 pt-2">
          <p className="mb-1 text-[10px] font-extrabold uppercase tracking-[0.15em] text-slate-400">Top drivers</p>
          {drivers.map((d: { label?: string; clause_type?: string; risk?: string }, i: number) => (
            <p key={i} className="flex justify-between text-[10.5px] text-slate-600">
              <span className="truncate">{d.label ?? d.clause_type}</span>
              <b className={cn(d.risk === "high" ? "text-rose-500" : d.risk === "medium" ? "text-amber-500" : "text-emerald-600")}>{d.risk}</b>
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function Palette({
  data,
  onGo,
  onInspect,
}: {
  data: ConsoleResponse;
  onGo: (id: string) => void;
  onInspect: (id: string) => void;
}) {
  const [q, setQ] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => inputRef.current?.focus(), []);
  const ql = q.toLowerCase();
  const actions = data.triage
    .filter((t) => !ql || t.title.toLowerCase().includes(ql))
    .map((t) => ({ id: t.contract_id, label: t.title, hint: t.reason, action: true }));
  const contracts = data.register
    .filter((r) => !ql || r.title.toLowerCase().includes(ql))
    .map((r) => ({ id: r.contract_id, label: r.title, hint: `${r.stage} · ${r.days_in_stage}d`, action: false }));
  const items = [...actions, ...contracts.filter((c) => !actions.some((a) => a.id === c.id))].slice(0, 8);
  return (
    <div>
      <div className="flex items-center gap-2 border-b border-slate-200 px-4 py-3 focus-within:border-brand-400">
        <Search size={14} className="text-slate-400" />
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && items[0]) (items[0].action ? onGo : onInspect)(items[0].id);
          }}
          placeholder="Type a command or contract…"
          className="flex-1 bg-transparent text-sm text-slate-900 outline-none placeholder:text-slate-500"
        />
      </div>
      <div className="max-h-[300px] overflow-auto p-1.5">
        {items.map((it, i) => (
          <button
            key={it.id + String(it.action)}
            onClick={() => (it.action ? onGo(it.id) : onInspect(it.id))}
            className={cn("flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left hover:bg-brand-50 dark:hover:bg-brand-400/10", i === 0 && "bg-brand-50/60 dark:bg-brand-400/5")}
          >
            <span className={cn("rounded px-1.5 py-0.5 tabular-nums text-[10px] font-bold", it.action ? "bg-brand-100 text-brand-700 dark:bg-brand-400/15 dark:text-brand-300" : "bg-slate-200 text-slate-500")}>
              {it.action ? "ACT" : "GO"}
            </span>
            <span className="min-w-0 flex-1 truncate text-[12.5px] text-slate-800">{it.label}</span>
            <span className="max-w-[130px] truncate tabular-nums text-[10.5px] text-slate-400">{it.hint}</span>
          </button>
        ))}
        {items.length === 0 && <p className="px-3 py-4 text-xs text-slate-400">Nothing matches.</p>}
      </div>
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
      const res = await contractsApi.upload(file, { title: title || undefined });
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
