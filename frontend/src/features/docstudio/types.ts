// Docstudio viewer — the shapes the screen reads.
//
// These mirror `ds_clause` / `ds_annotation` as the build plan specifies them.
// Nothing here imports from the existing contract components or types: the
// viewer talks to Docstudio only, and Docstudio owns these names.

/** A rectangle on a page, as a FRACTION of the page in both axes. */
export interface BBox {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
}

/** One page-piece of a clause. A clause the reflow step rejoined has several. */
export interface Region {
  page: number;
  bbox: BBox;
}

/** Who decided where a clause sits — `ds_clause.structure_source`. */
export type StructureSource =
  | "document"
  | "numbering"
  | "list"
  | "part"
  | "top"
  | "layout"
  | "ai"
  | "undecided";

export interface Clause {
  seq: number;
  clause_id: string;
  parent_clause_id: string | null;
  number_label: string | null;
  level: number;
  clause_type: string;
  /** First page the clause appears on. */
  page_number: number | null;
  /** Last page — differs from `page_number` only where a page break split it. */
  page_end: number | null;
  structure_source: StructureSource | null;
  char_start: number | null;
  char_end: number | null;
  /** The union of the clause's boxes. On a multi-page clause this is NOT a
   *  drawable rectangle — read `regions` instead. */
  bbox: BBox | null;
  /** How many page-pieces the clause has. 1 for all but rejoined clauses. */
  region_count: number;
  /** False where the source could only give the union box, so the per-page
   *  pieces are approximated and the viewer must say so. */
  regions_known: boolean;
  text: string;
}

export type AnnotationKind = "comment" | "citation" | "proposal" | "risk";
export type AnchorState = "ok" | "moved" | "orphaned";

export interface Annotation {
  id: string;
  kind: AnnotationKind;
  /** Null where the anchor is orphaned — the clause could not be found. */
  anchor_clause_id: string | null;
  anchor_quote_exact: string;
  anchor_state: AnchorState;
  /** Which rung of the ladder resolved it, 1-5. */
  anchor_rung: number;
  body: string;
  proposed_text?: string | null;
  status: "open" | "accepted" | "rejected" | "resolved";
  author_kind: "human" | "ai";
  author_name: string;
  created_at: string;
}

/** A check `report.py` raised against this version. */
export interface Finding {
  code: string;
  page: number | null;
  detail: string;
}

export interface DocumentMeta {
  id: string;
  title: string;
  filename: string;
}

export interface VersionMeta {
  id: string;
  version_number: number;
  mime_type: string;
  sha256: string;
  byte_size: number;
  parser_name: string;
  page_count: number;
  ingested_at: string | null;
}

export interface Extraction {
  clause_count: number;
  characters: number;
  with_page: number;
  with_bbox: number;
  coverage_audit: string | null;
}

/** Everything one version of one document gives the viewer. */
export interface VersionBundle {
  document: DocumentMeta;
  version: VersionMeta;
  extraction: Extraction;
  findings: Finding[];
  clauses: Clause[];
  annotations: Annotation[];
}

/** A clause plus its children, for the outline. */
export interface ClauseNode extends Clause {
  children: ClauseNode[];
}

/**
 * The page-pieces of a clause, ready to draw.
 *
 * A single-page clause has one region and it is exact.
 *
 * A clause the reflow step rejoined is harder, and worth being careful about.
 * The stored box locates the piece on the clause's FIRST page — it does not
 * describe where the clause resumes overland the page break. So the first
 * region is drawn exactly, and each continuation gets a slim band at the head
 * of its page: enough to show that the clause carries on there and to click,
 * and not so much that it claims a rectangle nobody measured. An earlier draft
 * ran the continuation from the top of the page down to the stored `y1`, which
 * drew a box over most of page 4 of the Franklin MSA and visually swallowed
 * three clauses that have nothing to do with it.
 *
 * `ds_clause.source_regions` holds the real pieces. Once the API serves them
 * this function reads them instead, and `Clause.regions_known` is what tells
 * the UI which of the two it is looking at.
 */
export function regionsOf(clause: Clause): Region[] {
  const { bbox, page_number: start, page_end } = clause;
  if (!bbox || start == null) return [];
  const end = page_end ?? start;

  const regions: Region[] = [{ page: start, bbox }];
  for (let page = start + 1; page <= end; page += 1) {
    regions.push({
      page,
      bbox: { x0: bbox.x0, x1: bbox.x1, y0: 0.055, y1: 0.105 },
    });
  }
  return regions;
}

/** Build the outline tree from the flat, ordered clause list. */
export function buildTree(clauses: Clause[]): ClauseNode[] {
  const nodes = new Map<string, ClauseNode>();
  for (const c of clauses) nodes.set(c.clause_id, { ...c, children: [] });

  const roots: ClauseNode[] = [];
  for (const c of clauses) {
    const node = nodes.get(c.clause_id)!;
    const parent = c.parent_clause_id ? nodes.get(c.parent_clause_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}
