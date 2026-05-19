"use client";

import { use, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Download,
  RotateCcw,
  Send,
  ChevronRight,
  Plus,
  Trash2,
  Columns3,
  FileText,
} from "lucide-react";
import { contractsApi, projectsApi, tabularApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  CenterSpinner,
  ErrorState,
  Input,
  Modal,
  Spinner,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { fmtRelative, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { TabularReviewCell } from "@/lib/types";

export default function TabularReviewDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const qc = useQueryClient();
  const { notify } = useToast();
  const [activeCell, setActiveCell] = useState<TabularReviewCell | null>(null);
  const [addColsOpen, setAddColsOpen] = useState(false);
  const [addContractsOpen, setAddContractsOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["tabular", id],
    queryFn: () => tabularApi.get(id),
    refetchInterval: 4000,
  });
  const { data: contracts } = useQuery({
    queryKey: ["contracts"],
    queryFn: contractsApi.list,
  });
  const { data: projects } = useQuery({
    queryKey: ["projects"],
    queryFn: projectsApi.list,
  });

  const contractTitle = useMemo(() => {
    const map = new Map<string, string>();
    for (const c of contracts ?? []) map.set(c.id, c.title);
    return (cid: string) => map.get(cid) ?? cid;
  }, [contracts]);

  if (isLoading) return <CenterSpinner label="Loading review…" />;
  if (error) return <ErrorState error={error} />;
  if (!data) return null;

  const { review, columns, cells } = data;
  const project = (projects ?? []).find((p) => p.id === review.project_id);
  const sortedColumns = [...columns].sort((a, b) => a.position - b.position);
  const cellAt = (contractId: string, columnId: string) =>
    cells.find(
      (c) => c.contract_id === contractId && c.column_id === columnId,
    ) ?? null;

  async function rerun(cellId: string) {
    try {
      await tabularApi.rerunCell(id, cellId);
      qc.invalidateQueries({ queryKey: ["tabular", id] });
      notify("Cell rerun queued", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Rerun failed", "error");
    }
  }

  return (
    <div className="space-y-6">
      <div>
        {project ? (
          <nav className="mb-3 flex items-center gap-1.5 text-sm text-slate-500">
            <Link href="/projects" className="hover:text-slate-800">
              Projects
            </Link>
            <ChevronRight className="h-3.5 w-3.5 text-slate-300" />
            <Link
              href={`/projects/${project.id}`}
              className="hover:text-slate-800"
            >
              {project.name}
            </Link>
            <ChevronRight className="h-3.5 w-3.5 text-slate-300" />
            <span className="font-medium text-slate-800">Tabular Reviews</span>
          </nav>
        ) : (
          <Link
            href="/tabular-reviews"
            className="mb-3 inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800"
          >
            <ArrowLeft className="h-4 w-4" />
            Tabular Reviews
          </Link>
        )}
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-slate-900">
              {review.name}
            </h1>
            <div className="mt-2 flex items-center gap-2">
              <Badge tone={statusTone(review.status)}>
                {titleCase(review.status)}
              </Badge>
              <span className="text-sm text-slate-500">
                {review.source_contract_ids.length} contracts ·{" "}
                {sortedColumns.length} columns
              </span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setAddColsOpen(true)}
            >
              <Columns3 className="h-4 w-4" />
              Add column
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setAddContractsOpen(true)}
            >
              <FileText className="h-4 w-4" />
              Add contracts
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => tabularApi.exportXlsx(id)}
            >
              <Download className="h-4 w-4" />
              Export XLSX
            </Button>
          </div>
        </div>
      </div>

      <Card>
        <Table>
          <THead>
            <tr>
              <TH className="sticky left-0 bg-slate-50">Contract</TH>
              {sortedColumns.map((col) => (
                <TH key={col.id} className="min-w-[200px]">
                  {col.name}
                </TH>
              ))}
              <TH className="w-12">
                <button
                  onClick={() => setAddColsOpen(true)}
                  title="Add column"
                  aria-label="Add column"
                  className="inline-flex h-6 w-6 items-center justify-center rounded-md text-slate-400 hover:bg-brand-50 hover:text-brand-600"
                >
                  <Plus className="h-4 w-4" />
                </button>
              </TH>
            </tr>
          </THead>
          <tbody>
            {review.source_contract_ids.map((cid) => (
              <TR key={cid}>
                <TD className="sticky left-0 bg-white font-medium text-slate-900">
                  {contractTitle(cid)}
                </TD>
                {sortedColumns.map((col) => {
                  const cell = cellAt(cid, col.id);
                  return (
                    <TD key={col.id} className="align-top">
                      <CellView
                        cell={cell}
                        onOpen={() => cell && setActiveCell(cell)}
                      />
                    </TD>
                  );
                })}
                <TD />
              </TR>
            ))}
            {review.source_contract_ids.length === 0 && (
              <TR>
                <TD
                  className="py-8 text-center text-slate-400"
                  colSpan={sortedColumns.length + 2}
                >
                  No contracts in this review.
                </TD>
              </TR>
            )}
          </tbody>
        </Table>
      </Card>

      <ChatPanel reviewId={id} />

      {activeCell && (
        <CellModal
          cell={activeCell}
          contractTitle={contractTitle(activeCell.contract_id)}
          columnName={
            sortedColumns.find((c) => c.id === activeCell.column_id)?.name ?? ""
          }
          onClose={() => setActiveCell(null)}
          onRerun={() => {
            rerun(activeCell.id);
            setActiveCell(null);
          }}
        />
      )}

      <AddColumnsModal
        open={addColsOpen}
        reviewId={id}
        onClose={() => setAddColsOpen(false)}
        onDone={() => {
          setAddColsOpen(false);
          qc.invalidateQueries({ queryKey: ["tabular", id] });
          qc.invalidateQueries({ queryKey: ["tabular-reviews"] });
          notify("Columns added — answers are generating", "success");
        }}
      />
      <AddContractsModal
        open={addContractsOpen}
        reviewId={id}
        existingIds={review.source_contract_ids}
        onClose={() => setAddContractsOpen(false)}
        onDone={() => {
          setAddContractsOpen(false);
          qc.invalidateQueries({ queryKey: ["tabular", id] });
          qc.invalidateQueries({ queryKey: ["tabular-reviews"] });
          notify("Contracts added — answers are generating", "success");
        }}
      />
    </div>
  );
}

type ColDraft = { name: string; prompt: string };

function AddColumnsModal({
  open,
  reviewId,
  onClose,
  onDone,
}: {
  open: boolean;
  reviewId: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const { notify } = useToast();
  const [cols, setCols] = useState<ColDraft[]>([{ name: "", prompt: "" }]);
  const [busy, setBusy] = useState(false);

  const valid = cols.filter((c) => c.name.trim() && c.prompt.trim());

  function set(i: number, patch: Partial<ColDraft>) {
    setCols((cs) => cs.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  }

  async function submit() {
    if (valid.length === 0 || busy) return;
    setBusy(true);
    try {
      await tabularApi.addColumns(
        reviewId,
        valid.map((c) => ({ name: c.name.trim(), prompt: c.prompt.trim() })),
      );
      setCols([{ name: "", prompt: "" }]);
      onDone();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Add columns failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add columns"
      size="lg"
      footer={
        <>
          {valid.length === 0 && (
            <span className="mr-auto text-xs text-slate-400">
              Add a name and prompt to continue
            </span>
          )}
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={valid.length === 0}>
            Add {valid.length || ""} column{valid.length === 1 ? "" : "s"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-slate-500">
          New columns run against every contract already in this review.
          Existing answers are kept — only the new cells are generated.
        </p>
        {cols.map((col, idx) => (
          <div
            key={idx}
            className="space-y-2 rounded-lg border border-slate-200 p-3"
          >
            <div className="flex items-center gap-2">
              <Input
                placeholder="Column name (e.g. Governing law)"
                value={col.name}
                onChange={(e) => set(idx, { name: e.target.value })}
              />
              <Button
                size="icon"
                variant="ghost"
                disabled={cols.length === 1}
                onClick={() =>
                  setCols((cs) => cs.filter((_, i) => i !== idx))
                }
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
            <Input
              placeholder="Prompt (e.g. What is the governing law clause?)"
              value={col.prompt}
              onChange={(e) => set(idx, { prompt: e.target.value })}
            />
          </div>
        ))}
        <Button
          size="sm"
          variant="outline"
          onClick={() => setCols((cs) => [...cs, { name: "", prompt: "" }])}
        >
          <Plus className="h-3.5 w-3.5" />
          Add another column
        </Button>
      </div>
    </Modal>
  );
}

function AddContractsModal({
  open,
  reviewId,
  existingIds,
  onClose,
  onDone,
}: {
  open: boolean;
  reviewId: string;
  existingIds: string[];
  onClose: () => void;
  onDone: () => void;
}) {
  const { notify } = useToast();
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const { data: contracts } = useQuery({
    queryKey: ["contracts"],
    queryFn: contractsApi.list,
    enabled: open,
  });

  const inReview = new Set(existingIds);
  const available = (contracts ?? []).filter((c) => !inReview.has(c.id));

  function toggle(cid: string) {
    setPicked((p) =>
      p.includes(cid) ? p.filter((x) => x !== cid) : [...p, cid],
    );
  }

  async function submit() {
    if (picked.length === 0 || busy) return;
    setBusy(true);
    try {
      await tabularApi.addContracts(reviewId, picked);
      setPicked([]);
      onDone();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Add contracts failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add contracts"
      size="lg"
      footer={
        <>
          {picked.length === 0 && (
            <span className="mr-auto text-xs text-slate-400">
              Select at least one contract
            </span>
          )}
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={picked.length === 0}>
            Add {picked.length || ""} contract
            {picked.length === 1 ? "" : "s"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-slate-500">
          Every existing column runs against the contracts you add. Existing
          answers are kept.
        </p>
        <div className="max-h-72 space-y-1 overflow-y-auto rounded-lg border border-slate-200 p-2">
          {available.length === 0 ? (
            <p className="px-2 py-3 text-sm text-slate-400">
              No more contracts available to add.
            </p>
          ) : (
            available.map((c) => (
              <label
                key={c.id}
                className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
              >
                <input
                  type="checkbox"
                  checked={picked.includes(c.id)}
                  onChange={() => toggle(c.id)}
                />
                <span className="flex-1 truncate">{c.title}</span>
                {c.counterparty_name && (
                  <span className="shrink-0 text-xs text-slate-400">
                    {c.counterparty_name}
                  </span>
                )}
              </label>
            ))
          )}
        </div>
      </div>
    </Modal>
  );
}

function CellView({
  cell,
  onOpen,
}: {
  cell: TabularReviewCell | null;
  onOpen: () => void;
}) {
  if (!cell) return <span className="text-sm text-slate-300">—</span>;

  if (cell.status === "running" || cell.status === "pending") {
    return (
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <Spinner className="h-3.5 w-3.5" />
        {titleCase(cell.status)}
      </div>
    );
  }

  if (cell.status === "failed") {
    return (
      <button onClick={onOpen} className="text-left">
        <Badge tone="red">Failed</Badge>
      </button>
    );
  }

  return (
    <button
      onClick={onOpen}
      className="group flex w-full flex-col gap-1.5 text-left"
    >
      <span className="line-clamp-3 text-sm text-slate-700 group-hover:text-brand-700">
        {cell.answer ?? "—"}
      </span>
      <span className="flex items-center gap-1.5">
        {cell.status === "needs_review" && (
          <Badge tone="amber">Needs review</Badge>
        )}
        {cell.confidence && (
          <Badge tone={statusTone(cell.confidence)}>
            {titleCase(cell.confidence)}
          </Badge>
        )}
      </span>
    </button>
  );
}

function CellModal({
  cell,
  contractTitle,
  columnName,
  onClose,
  onRerun,
}: {
  cell: TabularReviewCell;
  contractTitle: string;
  columnName: string;
  onClose: () => void;
  onRerun: () => void;
}) {
  return (
    <Modal
      open
      onClose={onClose}
      title={`${columnName} · ${contractTitle}`}
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
          <Button onClick={onRerun}>
            <RotateCcw className="h-4 w-4" />
            Rerun
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <Badge tone={statusTone(cell.status)}>
            {titleCase(cell.status)}
          </Badge>
          {cell.confidence && (
            <Badge tone={statusTone(cell.confidence)}>
              {titleCase(cell.confidence)} confidence
            </Badge>
          )}
        </div>

        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Answer
          </p>
          <p className="mt-1.5 whitespace-pre-wrap text-sm text-slate-800">
            {cell.answer ?? "—"}
          </p>
        </div>

        {cell.reasoning && (
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Reasoning
            </p>
            <p className="mt-1.5 whitespace-pre-wrap text-sm text-slate-600">
              {cell.reasoning}
            </p>
          </div>
        )}

        {cell.error_message && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {cell.error_message}
          </div>
        )}

        {cell.citations && cell.citations.length > 0 && (
          <div className="space-y-2 border-t border-slate-200 pt-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Citations
            </p>
            {cell.citations.map((c, i) => (
              <blockquote
                key={i}
                className="border-l-2 border-brand-400 pl-3 text-sm text-slate-600"
              >
                {c.quote ?? c.excerpt}
              </blockquote>
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
}

function ChatPanel({ reviewId }: { reviewId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const { data: messages, isLoading } = useQuery({
    queryKey: ["tabular", reviewId, "chat"],
    queryFn: () => tabularApi.chat(reviewId),
  });

  async function send() {
    if (message.trim().length < 2) return;
    setBusy(true);
    try {
      await tabularApi.sendChat(reviewId, message.trim());
      setMessage("");
      qc.invalidateQueries({ queryKey: ["tabular", reviewId, "chat"] });
    } catch (e) {
      notify(e instanceof Error ? e.message : "Message failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Chat with this review</CardTitle>
      </CardHeader>
      <CardBody className="space-y-4">
        {isLoading ? (
          <CenterSpinner />
        ) : !messages?.length ? (
          <p className="py-6 text-center text-sm text-slate-400">
            Ask follow-up questions about the extracted answers across all
            contracts.
          </p>
        ) : (
          <div className="space-y-3">
            {messages.map((m) => (
              <div
                key={m.id}
                className={
                  m.role === "user" ? "flex justify-end" : "flex justify-start"
                }
              >
                <div
                  className={
                    m.role === "user"
                      ? "max-w-[80%] rounded-lg bg-brand-600 px-3 py-2 text-sm text-white"
                      : "max-w-[80%] space-y-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2"
                  }
                >
                  <p
                    className={
                      m.role === "user"
                        ? "whitespace-pre-wrap text-sm"
                        : "whitespace-pre-wrap text-sm text-slate-800"
                    }
                  >
                    {m.content}
                  </p>
                  {m.role === "assistant" &&
                    m.citations &&
                    m.citations.length > 0 && (
                      <div className="space-y-1.5 border-t border-slate-200 pt-2">
                        {m.citations.map((c, i) => (
                          <blockquote
                            key={i}
                            className="border-l-2 border-brand-400 pl-2 text-xs text-slate-600"
                          >
                            {c.quote ?? c.excerpt}
                          </blockquote>
                        ))}
                      </div>
                    )}
                  <p
                    className={
                      m.role === "user"
                        ? "text-[10px] text-brand-100"
                        : "text-[10px] text-slate-400"
                    }
                  >
                    {fmtRelative(m.created_at)}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="flex gap-2 border-t border-slate-100 pt-4">
          <Input
            placeholder="Ask about the results…"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
          />
          <Button onClick={send} loading={busy} className="shrink-0">
            <Send className="h-4 w-4" />
            Send
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}
