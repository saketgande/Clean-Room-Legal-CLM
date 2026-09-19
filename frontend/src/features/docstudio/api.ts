// The viewer's data source.
//
// Today it reads fixtures generated from real `report.py` pages (see
// `scripts/report-to-fixture.mjs`): the clause ids, page numbers and boxes
// below came off genuine client documents, so the screen is exercised by real
// shapes rather than invented ones.
//
// When `GET /docstudio/versions/{id}` exists, `loadVersion` is the only
// function that changes. Nothing else in the feature knows where data comes
// from.

import type { Annotation, VersionBundle } from "./types";

/** The fixtures on disk, in the order the picker lists them. */
export const MOCK_DOCUMENTS = [
  { slug: "basic-non-disclosure-agreement", label: "Basic NDA", note: "2 pages · clean parse" },
  { slug: "employment-agreement", label: "Employment Agreement", note: "13 pages · scanned, OCR" },
  { slug: "franklin-madison-msa-1-23-19", label: "Franklin Madison MSA", note: "14 pages · rejoined clauses" },
  { slug: "lockton-msa-04-01-2023", label: "Lockton MSA", note: "18 pages · tables in text" },
  { slug: "patent-license-agreement-1", label: "Patent Licence", note: "30 pages · 7 levels deep" },
] as const;

export type MockSlug = (typeof MOCK_DOCUMENTS)[number]["slug"];

export function isMockSlug(value: string): value is MockSlug {
  return MOCK_DOCUMENTS.some((d) => d.slug === value);
}

export async function loadVersion(slug: string): Promise<VersionBundle> {
  const res = await fetch(`/docstudio-mock/${slug}.json`);
  if (!res.ok) throw new Error(`No document called "${slug}".`);
  const bundle = (await res.json()) as Omit<VersionBundle, "annotations">;
  return { ...bundle, annotations: mockAnnotations(bundle.clauses) };
}

/**
 * Notes to draw, anchored to clauses this document actually has.
 *
 * Deterministic — same document, same notes — so a screenshot is comparable
 * across runs. One is deliberately orphaned: an annotation whose clause could
 * not be found is kept and shown, never dropped, and the screen has to prove
 * it handles that.
 */
function mockAnnotations(clauses: VersionBundle["clauses"]): Annotation[] {
  const body = clauses.filter((c) => c.text.length > 220);
  if (body.length === 0) return [];

  const pick = (n: number) => body[Math.floor((body.length - 1) * n)];
  const quote = (text: string) => text.slice(0, 64).trim();
  const out: Annotation[] = [];

  const first = pick(0.18);
  out.push({
    id: "an_1",
    kind: "comment",
    anchor_clause_id: first.clause_id,
    anchor_quote_exact: quote(first.text),
    anchor_state: "ok",
    anchor_rung: 1,
    body: "Counterparty asked to narrow this in the last round. Check it against the playbook before we send.",
    status: "open",
    author_kind: "human",
    author_name: "R. Mehta",
    created_at: "2026-09-16T09:12:00Z",
  });

  const second = pick(0.42);
  out.push({
    id: "an_2",
    kind: "risk",
    anchor_clause_id: second.clause_id,
    anchor_quote_exact: quote(second.text),
    anchor_state: "ok",
    anchor_rung: 1,
    body: "Uncapped on its face. Our standard position caps aggregate liability at fees paid in the preceding 12 months.",
    status: "open",
    author_kind: "ai",
    author_name: "Aegis",
    created_at: "2026-09-16T09:14:00Z",
  });

  const third = pick(0.66);
  out.push({
    id: "an_3",
    kind: "citation",
    anchor_clause_id: third.clause_id,
    anchor_quote_exact: quote(third.text),
    anchor_state: "moved",
    anchor_rung: 4,
    body: "Cited when answering “what notice is required to terminate?”.",
    status: "open",
    author_kind: "ai",
    author_name: "Aegis",
    created_at: "2026-09-17T14:02:00Z",
  });

  const fourth = pick(0.84);
  out.push({
    id: "an_4",
    kind: "proposal",
    anchor_clause_id: fourth.clause_id,
    anchor_quote_exact: quote(fourth.text),
    anchor_state: "ok",
    anchor_rung: 2,
    body: "Add a cure period before either party may terminate for breach.",
    proposed_text:
      "…provided that the breaching party shall have thirty (30) days from written notice to cure such breach before this Agreement may be terminated.",
    status: "open",
    author_kind: "ai",
    author_name: "Aegis",
    created_at: "2026-09-17T14:05:00Z",
  });

  out.push({
    id: "an_5",
    kind: "comment",
    anchor_clause_id: null,
    anchor_quote_exact: "the Supplier shall maintain insurance of not less than",
    anchor_state: "orphaned",
    anchor_rung: 5,
    body: "Finance wanted the insurance floor raised. The clause this sat on is not in this version.",
    status: "open",
    author_kind: "human",
    author_name: "P. Nair",
    created_at: "2026-09-15T11:40:00Z",
  });

  return out;
}
