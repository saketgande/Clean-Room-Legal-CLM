"use client";

import { Fragment, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, FileSearch, MessageCircle, MoreVertical, Plus, ScanLine, Stamp } from "lucide-react";
import { trademarksApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CenterSpinner,
  EmptyState,
  ErrorState,
  Input,
  MessageBar,
  Modal,
  PageHeader,
  StatCard,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { fmtDate, statusTone, titleCase } from "@/lib/utils";
import type { Trademark } from "@/lib/types";
import { TrademarkComments } from "@/components/TrademarkComments";

function RowMenu({
  trademark,
  expanded,
  onToggleExpand,
  onClose,
  anchor,
}: {
  trademark: Trademark;
  expanded: boolean;
  onToggleExpand: () => void;
  onClose: () => void;
  anchor: { top: number; right: number };
}) {
  const [copied, setCopied] = useState(false);

  async function copyId() {
    try {
      await navigator.clipboard.writeText(trademark.id);
      setCopied(true);
      setTimeout(onClose, 700);
    } catch {
      onClose();
    }
  }

  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div
        // Fixed (not absolute) so the table's `overflow-x-auto` scroll
        // wrapper (Table in ui.tsx) can't clip this outside its bounds.
        style={{ position: "fixed", top: anchor.top, right: anchor.right }}
        className="z-50 w-60 rounded-lg border border-slate-200 bg-slate-50 py-1 shadow-pop"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="truncate border-b border-slate-200 px-3 py-2 text-[11px] text-slate-500">
          {trademark.name} · {trademark.id}
        </div>
        <button
          type="button"
          className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] text-slate-700 hover:bg-slate-100"
          onClick={() => {
            onToggleExpand();
            onClose();
          }}
        >
          {expanded ? "Collapse details" : "Expand details"}
        </button>
        <button
          type="button"
          className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] text-slate-700 hover:bg-slate-100"
          onClick={copyId}
        >
          <Copy className="h-3.5 w-3.5" />
          {copied ? "Copied!" : "Copy trademark ID"}
        </button>
        <div className="my-1 border-t border-slate-200" />
        <Link
          href="/trademarks/calendar"
          className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] text-slate-700 hover:bg-slate-100"
          onClick={onClose}
        >
          Open renewal calendar
        </Link>
        <Link
          href="/trademarks/reports"
          className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] text-slate-700 hover:bg-slate-100"
          onClick={onClose}
        >
          View portfolio reports
        </Link>
      </div>
    </>
  );
}

export default function TrademarksPage() {
  const qc = useQueryClient();
  const [query, setQuery] = useState("");
  const searchParams = useSearchParams();
  const justCreatedId = searchParams.get("created");
  const highlightId = searchParams.get("highlight");
  const focusId = justCreatedId || highlightId;

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [menuAnchor, setMenuAnchor] = useState<{ top: number; right: number } | null>(null);
  const [chatTrademark, setChatTrademark] = useState<Trademark | null>(null);

  const { data: metrics, isLoading: metricsLoading } = useQuery({
    queryKey: ["trademarks", "dashboard"],
    queryFn: () => trademarksApi.dashboard(),
  });
  const { data: trademarks, isLoading, error } = useQuery({
    queryKey: ["trademarks"],
    queryFn: () => trademarksApi.list(),
  });
  // One bulk {trademark_id: count} call drives every row's discussion
  // indicator — not one comment-count request per row.
  const { data: commentCounts } = useQuery({
    queryKey: ["trademark-comment-counts"],
    queryFn: () => trademarksApi.commentCounts(),
  });

  const filtered = (trademarks ?? []).filter((t) =>
    t.name.toLowerCase().includes(query.trim().toLowerCase()),
  );

  return (
    <div className="space-y-4">
      <PageHeader
        title="Trademarks"
        description="Portfolio overview, intake, and renewal tracking for your trademark filings."
        actions={
          <>
            <Link href="/trademarks/extract">
              <Button variant="outline">
                <ScanLine className="h-4 w-4" />
                Extract from document
              </Button>
            </Link>
            <Link href="/trademarks/intake">
              <Button>
                <Plus className="h-4 w-4" />
                New intake
              </Button>
            </Link>
          </>
        }
      />

      {justCreatedId && (
        <MessageBar intent="success">
          {justCreatedId} was just submitted and written to the database — it appears below (and is now part
          of the internal portfolio future similarity searches check against).
        </MessageBar>
      )}
      {highlightId && !justCreatedId && (
        <MessageBar intent="info">Jumped to {highlightId} — highlighted below.</MessageBar>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard
          label="Total trademarks"
          value={metricsLoading ? "—" : (metrics?.total_trademarks ?? 0)}
          icon={<Stamp className="h-4 w-4" />}
        />
        <StatCard
          label="Active prosecutions"
          value={metricsLoading ? "—" : (metrics?.active_prosecutions ?? 0)}
          tone="amber"
          icon={<FileSearch className="h-4 w-4" />}
        />
        <StatCard
          label="Renewals due in 90 days"
          value={metricsLoading ? "—" : (metrics?.upcoming_renewals ?? 0)}
          tone="violet"
        />
      </div>

      <Card>
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <h3 className="text-[13px] font-semibold text-slate-900">
            My trademarks
          </h3>
          <Input
            placeholder="Filter by name…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="max-w-xs"
          />
        </div>

        {isLoading ? (
          <CenterSpinner label="Loading trademarks…" />
        ) : error ? (
          <div className="p-5">
            <ErrorState error={error} />
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={<Stamp className="h-6 w-6" />}
              title="No trademarks yet"
              description="Start a new intake or extract records from a filed document to populate your portfolio."
              action={
                <Link href="/trademarks/intake">
                  <Button size="sm">
                    <Plus className="h-4 w-4" />
                    New intake
                  </Button>
                </Link>
              }
            />
          </div>
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Name</TH>
                <TH>Status</TH>
                <TH>Type</TH>
                <TH>Jurisdiction</TH>
                <TH>Nice class</TH>
                <TH>Renewal due</TH>
                <TH>Source</TH>
                <TH></TH>
                <TH></TH>
              </tr>
            </THead>
            <tbody>
              {filtered.map((t) => {
                const count = commentCounts?.[t.id] ?? 0;
                return (
                <Fragment key={t.id}>
                  <TR
                    className={`cursor-pointer ${t.id === focusId ? "bg-brand-50" : ""}`}
                    onClick={() => setExpandedId(expandedId === t.id ? null : t.id)}
                  >
                    <TD className="font-medium text-slate-900">
                      <Link
                        href={`/trademarks/${t.id}`}
                        className="hover:text-brand-700 hover:underline"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {t.name}
                      </Link>
                    </TD>
                    <TD>
                      <Badge tone={statusTone(t.status)}>{titleCase(t.status)}</Badge>
                    </TD>
                    <TD>{titleCase(t.trademark_type)}</TD>
                    <TD>{t.jurisdiction}</TD>
                    <TD>{t.nice_class ?? "—"}</TD>
                    <TD>{fmtDate(t.renewal_due_on)}</TD>
                    <TD>{titleCase(t.source)}</TD>
                    <TD className="w-10" onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        className={`relative rounded-md p-1.5 hover:bg-slate-100 ${count > 0 ? "text-brand-700" : "text-slate-400 hover:text-slate-700"}`}
                        aria-label={count > 0 ? `${count} comment(s) — open discussion` : "Open discussion"}
                        title={count > 0 ? `${count} comment(s)` : "Start a discussion"}
                        onClick={() => setChatTrademark(t)}
                      >
                        <MessageCircle className="h-4 w-4" fill={count > 0 ? "currentColor" : "none"} />
                        {count > 0 && (
                          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-brand-600 px-1 text-[9px] font-bold text-white">
                            {count > 9 ? "9+" : count}
                          </span>
                        )}
                      </button>
                    </TD>
                    <TD className="relative w-10" onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        className="rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                        aria-label="Row actions"
                        onClick={(e) => {
                          const rect = e.currentTarget.getBoundingClientRect();
                          setMenuAnchor({ top: rect.bottom + 4, right: window.innerWidth - rect.right });
                          setMenuOpenId(menuOpenId === t.id ? null : t.id);
                        }}
                      >
                        <MoreVertical className="h-4 w-4" />
                      </button>
                      {menuOpenId === t.id && menuAnchor && (
                        <RowMenu
                          trademark={t}
                          expanded={expandedId === t.id}
                          onToggleExpand={() => setExpandedId(expandedId === t.id ? null : t.id)}
                          onClose={() => setMenuOpenId(null)}
                          anchor={menuAnchor}
                        />
                      )}
                    </TD>
                  </TR>
                  {expandedId === t.id && (
                    <TR>
                      <TD colSpan={9} className="bg-slate-50">
                        <div className="grid grid-cols-3 gap-4 py-1 text-[13px]">
                          <div>
                            <div className="text-xs font-medium uppercase tracking-[0.04em] text-slate-500">Internal ID</div>
                            <div className="mt-0.5 text-slate-800">{t.id}</div>
                          </div>
                          <div>
                            <div className="text-xs font-medium uppercase tracking-[0.04em] text-slate-500">Status</div>
                            <div className="mt-0.5 text-slate-800">{titleCase(t.status)}</div>
                          </div>
                          <div>
                            <div className="text-xs font-medium uppercase tracking-[0.04em] text-slate-500">Trademark type</div>
                            <div className="mt-0.5 text-slate-800">{titleCase(t.trademark_type)}</div>
                          </div>
                          <div className="col-span-3">
                            <div className="text-xs font-medium uppercase tracking-[0.04em] text-slate-500">Description / goods &amp; services</div>
                            <div className="mt-0.5 whitespace-pre-wrap text-slate-800">{t.description || t.goods_services || "—"}</div>
                          </div>
                        </div>
                      </TD>
                    </TR>
                  )}
                </Fragment>
                );
              })}
            </tbody>
          </Table>
        )}
      </Card>

      {chatTrademark && (
        <Modal
          open
          onClose={() => {
            setChatTrademark(null);
            qc.invalidateQueries({ queryKey: ["trademark-comment-counts"] });
          }}
          title={`Discussion — ${chatTrademark.name}`}
          size="lg"
        >
          <TrademarkComments trademarkId={chatTrademark.id} />
        </Modal>
      )}
    </div>
  );
}
