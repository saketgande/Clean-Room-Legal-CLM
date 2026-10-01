// Shapes the docstudio API returns. Mirrors backend/app/docstudio/schemas.py.

export interface DsClause {
  clause_id: string;
  seq: number;
  parent_clause_id: string | null;
  number_label: string | null;
  level: number;
  clause_type: string;
  text: string;
  char_start: number;
  char_end: number;
  page_number: number | null;
  /** Page fractions, origin top-left — what a viewer needs to draw a box. */
  bbox: { x0: number; y0: number; x1: number; y1: number } | null;
  regions: { page: number | null; bbox: Record<string, number> | null }[] | null;
  /** numbering | list | ai | undecided … — who decided where this clause sits. */
  structure_source: string | null;
}

export interface DsAnnotation {
  id: string;
  kind: string;
  body: string | null;
  status: string;
  anchor_clause_id: string | null;
  anchor_quote_exact: string | null;
  anchor_start: number | null;
  anchor_end: number | null;
  anchor_state: string;
  anchor_rung: number | null;
}

export interface DsVersionSummary {
  id: string;
  document_id: string;
  version_number: number;
  filename: string | null;
  mime_type: string;
  byte_size: number;
  page_count: number | null;
  /** False when the file itself was never kept: the Original view says so. */
  has_file: boolean;
  /** False for a scan nothing could be read from: the view opens on Original. */
  has_text: boolean;
  read_by: string;
  created_at: string | null;
}

export interface DsVersionDetail extends DsVersionSummary {
  flat_text: string;
  parse_warnings: string[] | null;
  clauses: DsClause[];
  annotations: DsAnnotation[];
}

export interface DsDocument {
  id: string;
  title: string | null;
  external_ref: string | null;
  version_count: number;
  current: DsVersionSummary | null;
}
