// Convert a docstudio `report.py` markdown page into a viewer fixture.
//
// The report is the richest description of a parsed version we have outside the
// database, and it is regenerable, so it makes a better mock than invented data:
// every clause id, page number and bounding box below came off a real document.
// Delete this script once `GET /docstudio/versions/{id}` is live.

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { basename } from "node:path";

const num = (s) => Number(String(s).replace(/,/g, ""));

function parseKeyValueTable(block) {
  const out = {};
  for (const line of block.split("\n")) {
    const m = /^\|\s*(.+?)\s*\|\s*(.*?)\s*\|$/.exec(line.trim());
    if (!m || /^-+$/.test(m[1])) continue;
    if (!m[1]) continue;
    out[m[1]] = m[2].replace(/`/g, "");
  }
  return out;
}

function section(md, heading) {
  const re = new RegExp(`^## ${heading}.*$`, "m");
  const start = re.exec(md);
  if (!start) return "";
  const from = start.index + start[0].length;
  const next = /^## /m.exec(md.slice(from));
  return md.slice(from, next ? from + next.index : undefined).trim();
}

function parseClauses(md) {
  const body = section(md, "Clauses");
  // Entries start at a level-3 heading; split on it but keep the heading text.
  const chunks = body.split(/^### /m).slice(1);
  const clauses = [];

  for (const chunk of chunks) {
    const nl = chunk.indexOf("\n");
    const head = (nl === -1 ? chunk : chunk.slice(0, nl)).trim();
    const rest = nl === -1 ? "" : chunk.slice(nl + 1);

    // "#4 · 1.1. · level 2 · list_item · p1", or "· p3-4" where reflow.py
    // rejoined a sentence a page break cut in half.
    const h = /^#(\d+)\s*·\s*(.+?)\s*·\s*level\s+(\d+)\s*·\s*(\w+)(?:\s*·\s*p(\d+)(?:-(\d+))?)?$/.exec(head);
    if (!h) continue;

    const lines = rest.split("\n");
    const metaIdx = lines.findIndex((l) => l.trim().startsWith("`cl_"));
    if (metaIdx === -1) continue;
    const meta = lines[metaIdx].trim();
    const text = lines.slice(metaIdx + 1).join("\n").trim();

    const id = /`(cl_[0-9a-f]+)`/.exec(meta);
    const parent = /parent\s+`(cl_[0-9a-f]+)`/.exec(meta);
    const placed = /placed by\s+([a-z_]+)/.exec(meta);
    const chars = /chars\s+([\d,]+)-([\d,]+)/.exec(meta);
    const box = /box x\s+([\d.]+)-([\d.]+),\s*y\s+([\d.]+)-([\d.]+)/.exec(meta);
    const pieces = /(\d+)\s+pieces?, on pages/.exec(meta);
    if (!id) continue;

    // A clause the reflow step rejoined sits on more than one page, and the
    // report prints only the union of its boxes — which is not a rectangle on
    // any single page. The database holds the real per-page pieces in
    // `source_regions`; the report does not, so the fixture says how many
    // pieces there are and leaves the viewer to say it cannot draw them.
    const pageStart = h[5] ? num(h[5]) : null;
    const pageEnd = h[6] ? num(h[6]) : pageStart;

    clauses.push({
      seq: num(h[1]),
      clause_id: id[1],
      parent_clause_id: parent ? parent[1] : null,
      number_label: h[2] === "unnumbered" ? null : h[2],
      level: num(h[3]),
      clause_type: h[4],
      page_number: pageStart,
      page_end: pageEnd,
      region_count: pieces ? num(pieces[1]) : 1,
      regions_known: !pieces,
      structure_source: placed ? placed[1] : null,
      char_start: chars ? num(chars[1]) : null,
      char_end: chars ? num(chars[2]) : null,
      bbox: box
        ? { x0: Number(box[1]), x1: Number(box[2]), y0: Number(box[3]), y1: Number(box[4]) }
        : null,
      text,
    });
  }
  return clauses;
}

function parseFindings(md) {
  const body = section(md, "Findings");
  if (!body || /^None\./.test(body)) return [];
  return body
    .split("\n")
    .map((l) => /^\d+\.\s+\*\*(.+?)\*\*\s*(?:\(p(\d+)\))?\s*—\s*(.+)$/.exec(l.trim()))
    .filter(Boolean)
    .map((m) => ({ code: m[1], page: m[2] ? num(m[2]) : null, detail: m[3] }));
}

const [, , src, outDir] = process.argv;
const md = readFileSync(src, "utf8");
const file = parseKeyValueTable(section(md, "File"));
const extraction = parseKeyValueTable(section(md, "Extraction"));
const clauses = parseClauses(md);

const pagesFromClauses = clauses.reduce((n, c) => Math.max(n, c.page_number ?? 0), 0);

const fixture = {
  document: {
    id: file["Document id"],
    title: file.Title ?? file.Filename,
    filename: file.Filename,
  },
  version: {
    id: file["Version id"],
    version_number: num(file.Version ?? 1),
    mime_type: file.Type,
    sha256: file["SHA-256"],
    byte_size: num((file.Size ?? "0").split(" ")[0]),
    parser_name: file["Read by"],
    page_count: num(file.Pages ?? pagesFromClauses) || pagesFromClauses,
    ingested_at: file.Ingested ?? null,
  },
  extraction: {
    clause_count: clauses.length,
    characters: num(extraction.Characters ?? 0),
    with_page: clauses.filter((c) => c.page_number != null).length,
    with_bbox: clauses.filter((c) => c.bbox).length,
    coverage_audit: extraction["Coverage audit"] ?? null,
  },
  findings: parseFindings(md),
  clauses,
};

mkdirSync(outDir, { recursive: true });
const slug = basename(src).replace(/^[0-9a-f]+-/, "").replace(/\.report\.md$/, "")
  .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
writeFileSync(`${outDir}/${slug}.json`, JSON.stringify(fixture, null, 0));
console.log(
  `${slug}: ${clauses.length} clauses, ${fixture.extraction.with_bbox} with a box, ` +
  `${fixture.version.page_count} pages, ${fixture.findings.length} findings`,
);
