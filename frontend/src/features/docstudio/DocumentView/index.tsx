"use client";

// The document view: a stack of pages, with the clause layer over each.
//
// Pages mount only when they come near the viewport. A 30-page licence holds
// 242 clause rectangles, and drawing all of them on every scroll frame is the
// difference between a viewer and a slideshow.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { ChevronLeft, ChevronRight, Minus, Plus } from "lucide-react";
import type { Annotation, Clause } from "../types";
import { regionsOf } from "../types";
import { PageSurface, type PlacedClause } from "./PageSurface";
import { ClauseLayer, type ClauseMarks } from "./ClauseLayer";

/** US Letter. Real page sizes arrive with the render endpoint. */
const PAGE_ASPECT = 8.5 / 11;
/** Pages this far outside the viewport stay mounted, so scrolling stays smooth. */
const MOUNT_MARGIN = "150% 0px";

export interface FocusRequest {
  clauseId: string;
  /** Changes on every request, so clicking the same clause twice re-scrolls. */
  nonce: number;
}

export function DocumentView({
  clauses,
  annotations,
  pageCount,
  selectedId,
  focus,
  onSelect,
}: {
  clauses: Clause[];
  annotations: Annotation[];
  /** From `ds_version`, NOT from the clauses. A page that produced no clauses
   *  is still a page of the document, and dropping it would quietly hide
   *  exactly what the report's EMPTY PAGE finding is trying to say. */
  pageCount: number;
  selectedId: string | null;
  focus: FocusRequest | null;
  onSelect: (clauseId: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pageWidth, setPageWidth] = useState(720);
  const [currentPage, setCurrentPage] = useState(1);

  // Which clause-pieces sit on which page.
  const byPage = useMemo(() => {
    const map = new Map<number, PlacedClause[]>();
    for (const clause of clauses) {
      for (const region of regionsOf(clause)) {
        const list = map.get(region.page);
        if (list) list.push({ clause, region });
        else map.set(region.page, [{ clause, region }]);
      }
    }
    return map;
  }, [clauses]);

  // Trust the version, but never render fewer pages than clauses were found
  // on — a disagreement means the ingest is inconsistent, and losing a clause
  // off the bottom of the document is the worse of the two failures.
  const pages = useMemo(
    () => Math.max(1, pageCount, ...Array.from(byPage.keys())),
    [byPage, pageCount],
  );

  const marks = useMemo(() => {
    const map = new Map<string, ClauseMarks>();
    for (const a of annotations) {
      if (!a.anchor_clause_id) continue;
      const found = map.get(a.anchor_clause_id);
      if (found) {
        found.count += 1;
        found.kinds.push(a.kind);
        if (a.anchor_state === "moved") found.state = "moved";
      } else {
        map.set(a.anchor_clause_id, { count: 1, state: a.anchor_state, kinds: [a.kind] });
      }
    }
    return map;
  }, [annotations]);

  // Fit the page to the scroller, leaving room for the margin markers.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const measure = () => setPageWidth(Math.max(320, el.clientWidth - 96));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Jumping to a clause on a page that is not mounted yet.
  //
  // The clause's own anchor only exists once its page has been drawn, so
  // looking for it straight away finds nothing and the document sits where it
  // was — which is what a click on a far-off outline row used to do. Scroll to
  // the page first (page frames are always in the DOM, mounted or not); that
  // brings the page into the observer's margin, it mounts, and the anchor
  // appears a frame or two later. Then settle on the clause itself.
  const scrollToClause = useCallback(
    (clauseId: string) => {
      const root = scrollRef.current;
      if (!root) return;

      const settle = () =>
        root
          .querySelector<HTMLElement>(`[data-clause-anchor="${clauseId}"]`)
          ?.scrollIntoView({ behavior: "smooth", block: "center" });

      if (root.querySelector(`[data-clause-anchor="${clauseId}"]`)) {
        settle();
        return;
      }

      const page = clauses.find((c) => c.clause_id === clauseId)?.page_number;
      if (page == null) return;
      root
        .querySelector<HTMLElement>(`[data-page="${page}"]`)
        ?.scrollIntoView({ behavior: "smooth", block: "start" });

      // Give the page a bounded number of frames to mount, then stop. Bounded
      // so a clause that never appears cannot spin forever.
      let attempts = 0;
      const tick = () => {
        if (root.querySelector(`[data-clause-anchor="${clauseId}"]`)) {
          settle();
          return;
        }
        if (attempts++ < 60) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    },
    [clauses],
  );

  useEffect(() => {
    if (focus) scrollToClause(focus.clauseId);
  }, [focus, scrollToClause]);

  const width = pageWidth * zoom;
  const height = width / PAGE_ASPECT;

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-slate-50">
      <Toolbar
        page={currentPage}
        pageCount={pages}
        zoom={zoom}
        onZoom={setZoom}
        onPage={(p) => {
          const el = scrollRef.current?.querySelector<HTMLElement>(`[data-page="${p}"]`);
          el?.scrollIntoView({ behavior: "smooth", block: "start" });
        }}
      />
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto px-12 py-6">
        <div className="mx-auto flex flex-col items-center gap-6" style={{ width }}>
          {Array.from({ length: pages }, (_, i) => i + 1).map((page) => (
            <Page
              key={page}
              page={page}
              placed={byPage.get(page) ?? []}
              width={width}
              height={height}
              selectedId={selectedId}
              hoveredId={hoveredId}
              marks={marks}
              onSelect={onSelect}
              onHover={setHoveredId}
              onVisible={setCurrentPage}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function Page({
  page,
  placed,
  width,
  height,
  selectedId,
  hoveredId,
  marks,
  onSelect,
  onHover,
  onVisible,
}: {
  page: number;
  placed: PlacedClause[];
  width: number;
  height: number;
  selectedId: string | null;
  hoveredId: string | null;
  marks: Map<string, ClauseMarks>;
  onSelect: (clauseId: string) => void;
  onHover: (clauseId: string | null) => void;
  onVisible: (page: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [mounted, setMounted] = useState(page <= 2);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setMounted(true);
          if (entry.intersectionRatio > 0.4) onVisible(page);
        }
      },
      { root: null, rootMargin: MOUNT_MARGIN, threshold: [0, 0.4] },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [page, onVisible]);

  return (
    <div className="relative w-full" style={{ height }} data-page={page} ref={ref}>
      <div className="absolute inset-0 rounded-sm bg-white shadow-[0_1px_3px_rgba(16,18,22,0.14),0_8px_24px_-12px_rgba(16,18,22,0.25)] ring-1 ring-slate-200">
        {mounted ? (
          <>
            <PageSurface pageNumber={page} placed={placed} width={width} height={height} />
            <ClauseLayer
              placed={placed}
              selectedId={selectedId}
              hoveredId={hoveredId}
              marks={marks}
              onSelect={onSelect}
              onHover={onHover}
            />
            {/* Scroll targets: one per clause-piece, so a clause split across a
                page break can be scrolled to at either half. */}
            {placed.length === 0 ? (
              // Not a rendering failure — the ingest found nothing here, and
              // the report says so as an EMPTY PAGE finding. Saying it on the
              // page is cheaper than letting someone wonder.
              <p className="absolute inset-x-0 top-1/2 -translate-y-1/2 px-8 text-center text-xs text-slate-400">
                No clauses were extracted from this page.
              </p>
            ) : null}
            {placed.map(({ clause, region }) => (
              <span
                key={`anchor-${clause.clause_id}-${region.page}`}
                data-clause-anchor={clause.clause_id}
                className="pointer-events-none absolute"
                style={{ left: 0, top: `${region.bbox.y0 * 100}%` }}
                aria-hidden
              />
            ))}
          </>
        ) : null}
      </div>
      <span className="absolute -bottom-5 right-0 text-[11px] tabular-nums text-slate-500">
        {page}
      </span>
    </div>
  );
}

function Toolbar({
  page,
  pageCount,
  zoom,
  onZoom,
  onPage,
}: {
  page: number;
  pageCount: number;
  zoom: number;
  onZoom: (z: number) => void;
  onPage: (page: number) => void;
}) {
  const btn =
    "inline-flex h-7 w-7 items-center justify-center rounded border border-slate-300 bg-slate-100 text-slate-600 hover:bg-slate-200 disabled:opacity-40 disabled:hover:bg-slate-100";
  return (
    <div className="flex items-center justify-between gap-4 border-b border-slate-200 bg-slate-100 px-4 py-2">
      <div className="flex items-center gap-1.5">
        <button type="button" className={btn} onClick={() => onPage(Math.max(1, page - 1))} disabled={page <= 1} aria-label="Previous page">
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>
        <span className="min-w-[5.5rem] text-center text-xs tabular-nums text-slate-600">
          Page {page} of {pageCount}
        </span>
        <button type="button" className={btn} onClick={() => onPage(Math.min(pageCount, page + 1))} disabled={page >= pageCount} aria-label="Next page">
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="flex items-center gap-1.5">
        <button type="button" className={btn} onClick={() => onZoom(Math.max(0.5, Math.round((zoom - 0.1) * 10) / 10))} aria-label="Zoom out">
          <Minus className="h-3.5 w-3.5" />
        </button>
        <span className="min-w-[3rem] text-center text-xs tabular-nums text-slate-600">
          {Math.round(zoom * 100)}%
        </span>
        <button type="button" className={btn} onClick={() => onZoom(Math.min(2.5, Math.round((zoom + 0.1) * 10) / 10))} aria-label="Zoom in">
          <Plus className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => onZoom(1)}
          className={clsx("ml-1 rounded px-2 py-1 text-xs text-slate-600 hover:bg-slate-200", zoom === 1 && "opacity-40")}
          disabled={zoom === 1}
        >
          Fit
        </button>
      </div>
    </div>
  );
}
