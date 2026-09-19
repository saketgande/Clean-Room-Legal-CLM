"use client";

// Docstudio — the document workspace.
//
//   outline  │        the document        │  clause
//   (tree)   │   (pages + clause layer)   │  (notes, risk, proposals)
//
// Selection is held here and flows three ways: clicking a clause on the page
// fills the right rail, clicking a row in the outline scrolls the page to it,
// and both light up the same clause.

import { useCallback, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, ArrowLeft, Info, PanelLeft, PanelRight } from "lucide-react";
import Link from "next/link";
import { loadVersion } from "./api";
import { OutlinePanel } from "./OutlinePanel";
import { ClausePanel } from "./ClausePanel";
import { DocumentView, type FocusRequest } from "./DocumentView";
import { CenterSpinner, ErrorState } from "@/components/ui";

export function DocstudioWorkspace({ slug }: { slug: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["docstudio-version", slug],
    queryFn: () => loadVersion(slug),
  });

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focus, setFocus] = useState<FocusRequest | null>(null);
  const [showOutline, setShowOutline] = useState(true);
  const [showPanel, setShowPanel] = useState(true);
  const nonce = useRef(0);

  // Selecting from the outline scrolls the page; selecting on the page does
  // not, or the document would jump under the click that caused it.
  const selectAndScroll = useCallback((clauseId: string) => {
    setSelectedId(clauseId);
    nonce.current += 1;
    setFocus({ clauseId, nonce: nonce.current });
  }, []);

  const selected = useMemo(
    () => data?.clauses.find((c) => c.clause_id === selectedId) ?? null,
    [data, selectedId],
  );

  if (isLoading) return <CenterSpinner />;
  if (error || !data) {
    return <ErrorState error={error ?? new Error("Could not load that document.")} />;
  }

  const { document: doc, version, extraction, findings, clauses, annotations } = data;

  return (
    <div className="flex h-[calc(100vh-3.5rem)] min-h-0 flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-slate-200 bg-slate-100 px-4 py-2.5">
        <Link
          href="/docstudio"
          className="flex h-7 w-7 items-center justify-center rounded text-slate-500 hover:bg-slate-200 hover:text-slate-900"
          aria-label="Back to documents"
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-sm font-semibold text-slate-900">{doc.title}</h1>
          <p className="truncate text-[11px] text-slate-500">
            Version {version.version_number} · {version.page_count} pages ·{" "}
            {extraction.clause_count} clauses · read by {version.parser_name}
          </p>
        </div>
        <div className="flex items-center gap-1">
          <Toggle on={showOutline} onClick={() => setShowOutline((v) => !v)} label="Toggle outline">
            <PanelLeft className="h-4 w-4" />
          </Toggle>
          <Toggle on={showPanel} onClick={() => setShowPanel((v) => !v)} label="Toggle clause panel">
            <PanelRight className="h-4 w-4" />
          </Toggle>
        </div>
      </header>

      <Banners findings={findings} coverage={extraction.coverage_audit} />

      <div className="flex min-h-0 flex-1">
        {showOutline ? (
          <aside className="w-[19rem] shrink-0">
            <OutlinePanel clauses={clauses} selectedId={selectedId} onSelect={selectAndScroll} />
          </aside>
        ) : null}

        <main className="flex min-w-0 flex-1 flex-col">
          <DocumentView
            clauses={clauses}
            annotations={annotations}
            pageCount={version.page_count}
            selectedId={selectedId}
            focus={focus}
            onSelect={setSelectedId}
          />
        </main>

        {showPanel ? (
          <aside className="w-[22rem] shrink-0">
            <ClausePanel clause={selected} annotations={annotations} onSelect={selectAndScroll} />
          </aside>
        ) : null}
      </div>
    </div>
  );
}

/**
 * What this version is, and what is wrong with it, before anyone reads a word.
 *
 * Rule 6 of the build plan: when the rendered file and the extracted text can
 * disagree, say so. Here they always can, because no file is being rendered
 * yet — so the first banner never goes away until pdf.js does.
 */
function Banners({
  findings,
  coverage,
}: {
  findings: { code: string; page: number | null; detail: string }[];
  coverage: string | null;
}) {
  return (
    <div className="shrink-0">
      <div className="flex items-start gap-2 border-b border-info/25 bg-info-subtle px-4 py-2 text-[11px] leading-relaxed text-slate-700">
        <Info className="mt-px h-3.5 w-3.5 shrink-0 text-info" />
        <p>
          <strong className="font-semibold">Reconstructed pages.</strong> The page
          image is drawn from each clause&apos;s stored box, not from the file — the
          geometry is real, the ink is not. Swap <code>PageSurface</code> for a
          pdf.js canvas and this banner goes away.
          {coverage ? <span className="text-slate-600"> Coverage audit: {coverage}.</span> : null}
        </p>
      </div>

      {findings.length > 0 ? (
        <details className="border-b border-warning/25 bg-warning-subtle px-4 py-2">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-[11px] font-medium text-slate-800">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-warning" />
            {findings.length} finding{findings.length === 1 ? "" : "s"} on this version
          </summary>
          <ul className="mt-1.5 space-y-1 pl-5">
            {findings.map((f, i) => (
              <li key={i} className="text-[11px] leading-relaxed text-slate-700">
                <span className="font-semibold">{f.code}</span>
                {f.page ? <span className="text-slate-500"> (p{f.page})</span> : null} — {f.detail}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function Toggle({
  on,
  onClick,
  label,
  children,
}: {
  on: boolean;
  onClick: () => void;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={on}
      className={clsx(
        "flex h-7 w-7 items-center justify-center rounded",
        on ? "bg-slate-200 text-slate-900" : "text-slate-500 hover:bg-slate-200 hover:text-slate-700",
      )}
    >
      {children}
    </button>
  );
}
