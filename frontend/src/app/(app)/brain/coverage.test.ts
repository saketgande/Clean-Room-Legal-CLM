import { describe, expect, it } from "vitest";
import { coverageLine, hasCoverage, isReading, readingLine } from "./coverage";
import type { BrainQuery, PortfolioReading } from "@/lib/types";

type Meta = BrainQuery["retrieval_metadata"];

const meta = (over: Partial<Meta> = {}): Meta => ({
  scope: "portfolio",
  source_count: 32,
  graph_facts: 0,
  vector_chunks: 0,
  fulltext_clauses: 0,
  contract_ids: [],
  confidence: "high",
  citation_review: "",
  limitations: null,
  answer_mode: "database_filter",
  total: 32,
  checked: 30,
  matched: 12,
  ...over,
});
const reading = (over: Partial<PortfolioReading> = {}): PortfolioReading => ({
  tabular_review_id: "tr1",
  contracts: 20,
  done: 12,
  failed: 0,
  estimate_usd: 0.844,
  started: true,
  ...over,
});

describe("hasCoverage", () => {
  it("only for a computed which/how-many answer the viewer can see in full", () => {
    expect(hasCoverage(meta())).toBe(true);
    expect(hasCoverage(meta({ answer_mode: "search" }))).toBe(false);
    expect(hasCoverage(meta({ total: undefined }))).toBe(false);
    expect(hasCoverage(meta({ withheld_for_viewer: true }))).toBe(false);
  });
});

describe("isReading", () => {
  it("is true only while started and some contracts are unread", () => {
    expect(isReading(meta({ reading: reading() }))).toBe(true);
    expect(isReading(meta({ reading: reading({ done: 20 }) }))).toBe(false);
    expect(isReading(meta({ reading: reading({ started: false }) }))).toBe(false);
    expect(isReading(meta({ reading: null }))).toBe(false);
    expect(isReading(meta())).toBe(false);
  });

  it("reads `done` as already counting failed reads, as the server does", () => {
    // Guards stopping the poll while contracts are still being read: done 19 of 20 with
    // one of those failed is still one contract short.
    expect(isReading(meta({ reading: reading({ done: 19, failed: 1 }) }))).toBe(true);
    expect(isReading(meta({ reading: reading({ done: 20, failed: 1 }) }))).toBe(false);
  });
});

describe("coverageLine", () => {
  it("says what was checked out of everything, and whether that is the whole portfolio", () => {
    expect(coverageLine(meta({ checked: 32, complete: true }))).toBe("Checked 32 of 32 contracts · 12 match · complete");
  });

  it("names what is missing instead of calling the answer complete", () => {
    const m = meta({ unknown: [{ contract_id: "a", title: "A", reason: "no expiry date recorded" }], not_checked: ["value"] });
    expect(coverageLine(m)).toBe("Checked 30 of 32 contracts · 12 match · 1 could not be checked · part of the question was not checked");
  });

  it("says the AI is still reading rather than claiming completeness", () => {
    expect(coverageLine(meta({ reading: reading(), complete: false }))).toBe("Checked 30 of 32 contracts · 12 match · still being read");
  });

  it("falls back to the list length when matched is missing, and singularises", () => {
    const m = meta({ total: 1, checked: 1, matched: undefined, matches: [{ contract_id: "a", title: "A", counterparty_name: null, why: [] }] });
    expect(coverageLine(m)).toBe("Checked 1 of 1 contract · 1 match");
  });
});

describe("readingLine", () => {
  it("shows progress and the cost estimate while reading", () => {
    expect(readingLine(meta({ reading: reading() }))).toBe("The AI is reading 12 of 20 contracts (about $0.84)");
    expect(readingLine(meta({ reading: reading({ estimate_usd: 0.002 }) }))).toBe("The AI is reading 12 of 20 contracts (under $0.01)");
  });

  it("is null once finished or when nothing was read", () => {
    expect(readingLine(meta({ reading: reading({ done: 20 }) }))).toBeNull();
    expect(readingLine(meta())).toBeNull();
  });
});
