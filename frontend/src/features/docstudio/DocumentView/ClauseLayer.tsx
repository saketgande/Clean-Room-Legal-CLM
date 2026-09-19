"use client";

// The clause layer: what the page MEANS.
//
// One transparent rectangle per clause-piece, positioned from `ds_clause.bbox`.
// Because the boxes are stored as page fractions, placing them is percentage
// CSS rather than arithmetic, and it holds at any zoom and any rendered
// resolution.
//
// This layer is the whole difference between a document you look at and a
// document you can work with. It survives the swap to pdf.js unchanged.

import clsx from "clsx";
import type { AnchorState, Annotation } from "../types";
import type { PlacedClause } from "./PageSurface";

/** How many notes sit on each clause, and the worst anchor state among them. */
export interface ClauseMarks {
  count: number;
  state: AnchorState;
  kinds: Annotation["kind"][];
}

export function ClauseLayer({
  placed,
  selectedId,
  hoveredId,
  marks,
  onSelect,
  onHover,
}: {
  placed: PlacedClause[];
  selectedId: string | null;
  hoveredId: string | null;
  marks: Map<string, ClauseMarks>;
  onSelect: (clauseId: string) => void;
  onHover: (clauseId: string | null) => void;
}) {
  return (
    <div className="absolute inset-0">
      {placed.map(({ clause, region }) => {
        const selected = clause.clause_id === selectedId;
        const hovered = clause.clause_id === hoveredId;
        const mark = marks.get(clause.clause_id);
        const label = clause.number_label
          ? `Clause ${clause.number_label}`
          : `${clause.clause_type} at position ${clause.seq}`;

        return (
          <button
            key={`${clause.clause_id}-${region.page}`}
            type="button"
            aria-label={label}
            aria-pressed={selected}
            onClick={() => onSelect(clause.clause_id)}
            onMouseEnter={() => onHover(clause.clause_id)}
            onMouseLeave={() => onHover(null)}
            onFocus={() => onHover(clause.clause_id)}
            onBlur={() => onHover(null)}
            className={clsx(
              "absolute rounded-[2px] transition-colors duration-75",
              "focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500",
              selected
                ? "bg-brand-500/15 ring-2 ring-brand-600"
                : hovered
                  ? "bg-brand-500/8 ring-1 ring-brand-400"
                  : "ring-1 ring-transparent hover:bg-brand-500/8 hover:ring-brand-300",
            )}
            style={{
              left: `${region.bbox.x0 * 100}%`,
              top: `${region.bbox.y0 * 100}%`,
              width: `${(region.bbox.x1 - region.bbox.x0) * 100}%`,
              height: `${(region.bbox.y1 - region.bbox.y0) * 100}%`,
            }}
          >
            {mark ? <Marker count={mark.count} state={mark.state} /> : null}
          </button>
        );
      })}
    </div>
  );
}

/** A note count pinned to the outer margin of the clause, like Word's gutter. */
function Marker({ count, state }: { count: number; state: AnchorState }) {
  return (
    <span
      className={clsx(
        "absolute -left-6 top-0 flex h-5 w-5 items-center justify-center rounded-full",
        "text-[10px] font-semibold tabular-nums text-white shadow-sm",
        state === "moved" ? "bg-warning" : "bg-brand-600",
      )}
      title={
        state === "moved"
          ? `${count} note${count === 1 ? "" : "s"} — re-anchored after the text changed`
          : `${count} note${count === 1 ? "" : "s"}`
      }
    >
      {count}
    </span>
  );
}
