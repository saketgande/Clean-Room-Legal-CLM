"use client";

// The right rail: everything attached to the selected clause, plus the notes
// that could not be attached to anything.
//
// The orphan list is not a diagnostic — it is the rule from the build plan made
// visible. A note whose clause was deleted is kept, shown, and re-linkable. It
// is never dropped, because silently dropping it is the failure the whole
// rebuild exists to end.

import { useMemo, useState } from "react";
import clsx from "clsx";
import { AlertTriangle, FileText, Link2Off, MessageSquare, Quote, Scale, Wand2 } from "lucide-react";
import type { Annotation, AnnotationKind, Clause, StructureSource } from "./types";

const KIND_ICON: Record<AnnotationKind, typeof MessageSquare> = {
  comment: MessageSquare,
  citation: Quote,
  proposal: Wand2,
  risk: Scale,
};

const SOURCE_NOTE: Record<StructureSource, string> = {
  document: "Placed by the file's own outline levels.",
  numbering: "Placed by its number — 5.2 sits inside 5.",
  list: "Placed by list order — (b) follows (a).",
  part: "Placed at the top of an exhibit or schedule.",
  top: "Placed at the top level.",
  layout: "Placed by its indentation in the file.",
  ai: "Placed by the model, choosing from a closed list. The numbering could not confirm this.",
  undecided: "Nothing could place this. It sits where it was found.",
};

export function ClausePanel({
  clause,
  annotations,
  onSelect,
}: {
  clause: Clause | null;
  annotations: Annotation[];
  onSelect: (clauseId: string) => void;
}) {
  const [tab, setTab] = useState<"clause" | "unlinked">("clause");

  const orphans = useMemo(
    () => annotations.filter((a) => a.anchor_state === "orphaned"),
    [annotations],
  );
  const attached = useMemo(
    () => (clause ? annotations.filter((a) => a.anchor_clause_id === clause.clause_id) : []),
    [annotations, clause],
  );

  return (
    <div className="flex h-full min-h-0 flex-col border-l border-slate-200 bg-slate-100">
      <div className="flex shrink-0 border-b border-slate-200">
        <TabButton active={tab === "clause"} onClick={() => setTab("clause")}>
          Clause
        </TabButton>
        <TabButton active={tab === "unlinked"} onClick={() => setTab("unlinked")}>
          Needs re-linking
          {orphans.length > 0 ? (
            <span className="ml-1.5 rounded-full bg-warning px-1.5 text-[10px] font-semibold text-white">
              {orphans.length}
            </span>
          ) : null}
        </TabButton>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {tab === "clause" ? (
          clause ? (
            <ClauseDetail clause={clause} annotations={attached} />
          ) : (
            <Empty
              icon={FileText}
              title="No clause selected"
              body="Click any clause in the document, or pick one from the outline."
            />
          )
        ) : orphans.length === 0 ? (
          <Empty
            icon={Link2Off}
            title="Nothing needs re-linking"
            body="Every note found its words in this version."
          />
        ) : (
          <div className="divide-y divide-slate-200">
            {orphans.map((a) => (
              <OrphanRow key={a.id} annotation={a} onSelect={onSelect} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ClauseDetail({ clause, annotations }: { clause: Clause; annotations: Annotation[] }) {
  const spansPages = clause.page_end != null && clause.page_end !== clause.page_number;

  return (
    <div className="p-4">
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        {clause.number_label ? (
          <span className="rounded bg-brand-600 px-1.5 py-0.5 text-xs font-semibold text-white">
            {clause.number_label}
          </span>
        ) : null}
        <span className="rounded bg-slate-200 px-1.5 py-0.5 text-[11px] text-slate-700">
          {clause.clause_type}
        </span>
        <span className="rounded bg-slate-200 px-1.5 py-0.5 text-[11px] tabular-nums text-slate-700">
          {spansPages ? `pages ${clause.page_number}–${clause.page_end}` : `page ${clause.page_number ?? "–"}`}
        </span>
        <span className="rounded bg-slate-200 px-1.5 py-0.5 text-[11px] text-slate-700">
          level {clause.level}
        </span>
      </div>

      <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-slate-900">
        {clause.text}
      </p>

      {spansPages ? (
        <p className="mt-3 flex gap-1.5 rounded border border-warning/30 bg-warning-subtle px-2.5 py-2 text-[11px] leading-relaxed text-slate-700">
          <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0 text-warning" />
          <span>
            A page break cut this clause in {clause.region_count} and it was rejoined.
            {clause.regions_known
              ? null
              : " The highlight is approximated from the union of its boxes — the exact pieces live in source_regions."}
          </span>
        </p>
      ) : null}

      <dl className="mt-4 space-y-1.5 border-t border-slate-200 pt-3 text-[11px]">
        <Row label="Clause id" value={<code className="text-slate-700">{clause.clause_id}</code>} />
        <Row label="Position" value={`#${clause.seq} · chars ${clause.char_start?.toLocaleString()}–${clause.char_end?.toLocaleString()}`} />
        {clause.bbox ? (
          <Row
            label="Box"
            value={`x ${clause.bbox.x0.toFixed(3)}–${clause.bbox.x1.toFixed(3)}, y ${clause.bbox.y0.toFixed(3)}–${clause.bbox.y1.toFixed(3)}`}
          />
        ) : null}
        {clause.structure_source ? (
          <Row label="Placed by" value={SOURCE_NOTE[clause.structure_source]} />
        ) : null}
      </dl>

      <div className="mt-5">
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          Notes on this clause{annotations.length ? ` · ${annotations.length}` : ""}
        </h3>
        {annotations.length === 0 ? (
          <p className="rounded border border-dashed border-slate-300 px-3 py-4 text-center text-[11px] text-slate-500">
            Nothing attached here yet.
          </p>
        ) : (
          <div className="space-y-2">
            {annotations.map((a) => (
              <AnnotationCard key={a.id} annotation={a} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function AnnotationCard({ annotation }: { annotation: Annotation }) {
  const Icon = KIND_ICON[annotation.kind];
  return (
    <div className="rounded border border-slate-200 bg-slate-50 p-2.5">
      <div className="mb-1.5 flex items-center gap-1.5">
        <Icon className="h-3.5 w-3.5 text-slate-500" />
        <span className="text-[11px] font-semibold capitalize text-slate-700">{annotation.kind}</span>
        <span className="text-[11px] text-slate-500">· {annotation.author_name}</span>
        {annotation.anchor_state === "moved" ? (
          <span
            className="ml-auto rounded bg-warning-subtle px-1.5 text-[10px] font-medium text-warning"
            title={`Re-found at rung ${annotation.anchor_rung} after the text changed`}
          >
            moved
          </span>
        ) : null}
      </div>
      <p className="text-[12px] leading-relaxed text-slate-800">{annotation.body}</p>
      {annotation.proposed_text ? (
        <p className="mt-2 border-l-2 border-success pl-2 text-[12px] leading-relaxed text-slate-700">
          {annotation.proposed_text}
        </p>
      ) : null}
      <p className="mt-1.5 text-[10px] text-slate-400">Found at rung {annotation.anchor_rung}</p>
    </div>
  );
}

function OrphanRow({
  annotation,
  onSelect,
}: {
  annotation: Annotation;
  onSelect: (clauseId: string) => void;
}) {
  void onSelect; // Re-linking picks a clause; wired when the API accepts it.
  return (
    <div className="p-4">
      <div className="mb-1.5 flex items-center gap-1.5">
        <Link2Off className="h-3.5 w-3.5 text-warning" />
        <span className="text-[11px] font-semibold capitalize text-slate-700">{annotation.kind}</span>
        <span className="text-[11px] text-slate-500">· {annotation.author_name}</span>
      </div>
      <p className="text-[12px] leading-relaxed text-slate-800">{annotation.body}</p>
      <p className="mt-2 border-l-2 border-slate-300 pl-2 text-[11px] italic leading-relaxed text-slate-600">
        “{annotation.anchor_quote_exact}”
      </p>
      <p className="mt-2 text-[11px] text-slate-500">
        Not found in this version — all five rungs failed. Kept and listed here.
      </p>
      <button
        type="button"
        className="mt-2 rounded border border-slate-300 bg-slate-100 px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-200"
      >
        Re-link to a clause
      </button>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="w-20 shrink-0 text-slate-500">{label}</dt>
      <dd className="min-w-0 flex-1 break-words text-slate-700">{value}</dd>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "flex flex-1 items-center justify-center gap-1 border-b-2 px-3 py-2 text-xs font-medium",
        active
          ? "border-brand-600 text-brand-700"
          : "border-transparent text-slate-600 hover:text-slate-900",
      )}
    >
      {children}
    </button>
  );
}

function Empty({
  icon: Icon,
  title,
  body,
}: {
  icon: typeof FileText;
  title: string;
  body: string;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
      <Icon className="h-6 w-6 text-slate-400" />
      <p className="text-xs font-medium text-slate-700">{title}</p>
      <p className="text-[11px] leading-relaxed text-slate-500">{body}</p>
    </div>
  );
}
