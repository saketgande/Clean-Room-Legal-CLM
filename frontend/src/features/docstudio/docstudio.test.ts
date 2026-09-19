import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { buildTree, regionsOf, type Clause, type VersionBundle } from "./types";
import { MOCK_DOCUMENTS } from "./api";

const FIXTURE_DIR = path.resolve(__dirname, "../../../public/docstudio-mock");

function fixture(slug: string) {
  return JSON.parse(
    readFileSync(path.join(FIXTURE_DIR, `${slug}.json`), "utf8"),
  ) as Omit<VersionBundle, "annotations">;
}

function clause(over: Partial<Clause> = {}): Clause {
  return {
    seq: 0,
    clause_id: "cl_a",
    parent_clause_id: null,
    number_label: null,
    level: 1,
    clause_type: "paragraph",
    page_number: 1,
    page_end: 1,
    structure_source: "numbering",
    char_start: 0,
    char_end: 10,
    bbox: { x0: 0.1, x1: 0.9, y0: 0.2, y1: 0.4 },
    region_count: 1,
    regions_known: true,
    text: "text",
    ...over,
  };
}

describe("regionsOf", () => {
  it("gives a single-page clause one region, exactly its box", () => {
    const c = clause();
    expect(regionsOf(c)).toEqual([{ page: 1, bbox: c.bbox }]);
  });

  it("draws a rejoined clause exactly on its first page and marks the rest", () => {
    const c = clause({ page_number: 3, page_end: 4, region_count: 2, regions_known: false });
    const regions = regionsOf(c);
    expect(regions.map((r) => r.page)).toEqual([3, 4]);
    // The stored box locates the first piece only, so that is the one drawn
    // as measured.
    expect(regions[0].bbox).toEqual(c.bbox);
    // The continuation is a slim band at the head of its page — never a tall
    // rectangle, which would cover clauses that are not part of this one.
    expect(regions[1].bbox.y1 - regions[1].bbox.y0).toBeLessThan(0.08);
    expect(regions[1].bbox.y0).toBeLessThan(0.1);
  });

  it("never lets a continuation band overlap the clauses below it", () => {
    const [, band] = regionsOf(
      clause({ page_number: 1, page_end: 2, bbox: { x0: 0.1, x1: 0.9, y0: 0.7, y1: 0.95 } }),
    );
    expect(band.bbox.y1).toBeLessThan(0.15);
  });

  it("gives nothing to draw for a clause with no box", () => {
    expect(regionsOf(clause({ bbox: null }))).toEqual([]);
  });
});

describe("buildTree", () => {
  it("nests a child under its parent and keeps document order", () => {
    const roots = buildTree([
      clause({ seq: 0, clause_id: "cl_a" }),
      clause({ seq: 1, clause_id: "cl_b", parent_clause_id: "cl_a" }),
      clause({ seq: 2, clause_id: "cl_c" }),
    ]);
    expect(roots.map((r) => r.clause_id)).toEqual(["cl_a", "cl_c"]);
    expect(roots[0].children.map((c) => c.clause_id)).toEqual(["cl_b"]);
  });

  it("keeps a clause whose parent is missing rather than dropping it", () => {
    // A dangling parent must never make a clause vanish from the outline —
    // an invisible clause is worse than a misplaced one.
    const roots = buildTree([clause({ clause_id: "cl_x", parent_clause_id: "cl_gone" })]);
    expect(roots.map((r) => r.clause_id)).toEqual(["cl_x"]);
  });
});

describe("the fixtures", () => {
  it("has a file for every document the picker offers", () => {
    const onDisk = new Set(
      readdirSync(FIXTURE_DIR).map((f) => f.replace(/\.json$/, "")),
    );
    for (const doc of MOCK_DOCUMENTS) expect(onDisk.has(doc.slug)).toBe(true);
  });

  for (const doc of MOCK_DOCUMENTS) {
    describe(doc.slug, () => {
      const data = fixture(doc.slug);

      it("gives every clause a page and a box, so every clause is drawable", () => {
        const drawable = data.clauses.filter((c) => c.bbox && c.page_number != null);
        expect(drawable).toHaveLength(data.clauses.length);
      });

      it("keeps every box inside the page", () => {
        for (const c of data.clauses) {
          if (!c.bbox) continue;
          const { x0, x1, y0, y1 } = c.bbox;
          expect(x0).toBeGreaterThanOrEqual(0);
          expect(y0).toBeGreaterThanOrEqual(0);
          expect(x1).toBeLessThanOrEqual(1);
          expect(y1).toBeLessThanOrEqual(1);
          expect(x1).toBeGreaterThan(x0);
          expect(y1).toBeGreaterThan(y0);
        }
      });

      it("resolves every parent, and the tree holds every clause", () => {
        const ids = new Set(data.clauses.map((c) => c.clause_id));
        for (const c of data.clauses) {
          if (c.parent_clause_id) expect(ids.has(c.parent_clause_id)).toBe(true);
        }
        const count = (nodes: ReturnType<typeof buildTree>): number =>
          nodes.reduce((n, node) => n + 1 + count(node.children), 0);
        expect(count(buildTree(data.clauses))).toBe(data.clauses.length);
      });

      it("has no clause on a page past the end of the document", () => {
        for (const c of data.clauses) {
          expect(c.page_end ?? c.page_number ?? 1).toBeLessThanOrEqual(data.version.page_count);
        }
      });

      it("marks a multi-page clause as one whose pieces are approximated", () => {
        for (const c of data.clauses) {
          const spans = (c.page_end ?? c.page_number) !== c.page_number;
          if (spans) {
            expect(c.region_count).toBeGreaterThan(1);
            expect(c.regions_known).toBe(false);
          }
        }
      });
    });
  }
});
