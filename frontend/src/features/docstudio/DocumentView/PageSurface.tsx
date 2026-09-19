"use client";

// The page layer: what the page LOOKS like.
//
// This is the one component that gets replaced when the render endpoint lands.
// Today there is no file to draw, so it reconstructs the page by placing each
// clause's text inside its own bounding box — real geometry, approximate ink.
// Tomorrow it is a pdf.js canvas plus pdf.js's transparent text layer, and
// every other component in this feature is untouched, because they all work in
// page fractions and never look at what is underneath them.
//
// It is labelled as a reconstruction in the UI on purpose. A viewer that lets
// someone believe they are looking at the signed page when they are not is
// worse than no viewer.

import type { Clause, Region } from "../types";

export interface PlacedClause {
  clause: Clause;
  region: Region;
}

/**
 * A font size that roughly fills the clause's box.
 *
 * Treats the box as a paragraph: at font size f, a line holds about
 * `boxWidth / (0.5f)` characters, so the text needs `len / charsPerLine`
 * lines, and those lines must fill `boxHeight` at 1.25 line-height. Solving
 * for f gives the expression below. Only ever used by this placeholder.
 */
function fitFontSize(text: string, region: Region, pageWidth: number, pageHeight: number) {
  const w = (region.bbox.x1 - region.bbox.x0) * pageWidth;
  const h = (region.bbox.y1 - region.bbox.y0) * pageHeight;
  const len = Math.max(text.length, 1);
  // The 0.9 is headroom. Justified text breaks at word boundaries, so a
  // paragraph needs a little more height than the arithmetic says, and being
  // a shade small reads as a dense page while being a shade large clips a
  // sentence mid-word.
  const size = Math.sqrt((w * h) / (0.625 * len)) * 0.9;
  return Math.max(4.5, Math.min(size, 18));
}

export function PageSurface({
  pageNumber,
  placed,
  width,
  height,
}: {
  pageNumber: number;
  placed: PlacedClause[];
  width: number;
  height: number;
}) {
  return (
    <div
      className="absolute inset-0 overflow-hidden bg-white"
      aria-label={`Page ${pageNumber}`}
    >
      {placed.map(({ clause, region }) => {
        // A clause the reflow step rejoined is drawn on each of its pages, but
        // its text belongs to the page it started on. Repeating it on the
        // continuation would show the same paragraph twice in a document that
        // only says it once.
        const isContinuation = region.page !== clause.page_number;
        if (isContinuation) {
          return (
            <div
              key={`${clause.clause_id}-${region.page}`}
              className="absolute flex items-start justify-start pt-1 text-[10px] italic text-slate-400"
              style={{
                left: `${region.bbox.x0 * 100}%`,
                top: `${region.bbox.y0 * 100}%`,
                width: `${(region.bbox.x1 - region.bbox.x0) * 100}%`,
                height: `${(region.bbox.y1 - region.bbox.y0) * 100}%`,
              }}
            >
              continues from page {clause.page_number}
            </div>
          );
        }

        const size = fitFontSize(clause.text, region, width, height);
        const isHeading = clause.clause_type === "heading";
        return (
          <div
            key={`${clause.clause_id}-${region.page}`}
            className="absolute overflow-hidden text-slate-900"
            style={{
              left: `${region.bbox.x0 * 100}%`,
              top: `${region.bbox.y0 * 100}%`,
              width: `${(region.bbox.x1 - region.bbox.x0) * 100}%`,
              height: `${(region.bbox.y1 - region.bbox.y0) * 100}%`,
              fontSize: `${size}px`,
              lineHeight: 1.25,
              fontFamily: "'Times New Roman', Times, serif",
              fontWeight: isHeading ? 700 : 400,
              textAlign: "justify",
              hyphens: "auto",
            }}
          >
            {clause.text}
          </div>
        );
      })}
    </div>
  );
}
