import { describe, it, expect } from "vitest";
import { splitBlocks, anchorEdit } from "./contract-blocks";

const DOC = `1. Term. This Agreement begins on the Effective Date.

2. Payment. Fees are due within thirty (30) days of invoice.

3. Confidentiality. Each party shall protect the other's Confidential Information.`;

describe("contract-blocks", () => {
  it("splits on blank lines", () => {
    expect(splitBlocks(DOC).map((b) => b.text.slice(0, 2))).toEqual([
      "1.",
      "2.",
      "3.",
    ]);
  });

  it("ids are content-derived, so editing one block leaves others stable", () => {
    const before = splitBlocks(DOC);
    const edited = DOC.replace("thirty (30)", "forty-five (45)");
    const after = splitBlocks(edited);
    expect(after[0].id).toBe(before[0].id); // Term block untouched
    expect(after[2].id).toBe(before[2].id); // Confidentiality untouched
    expect(after[1].id).not.toBe(before[1].id); // Payment changed
  });

  it("splits numbered clauses that OCR runs together without blank lines", () => {
    const ocr =
      '2.1 "Affiliate" means any entity under common control with a party.\n' +
      '2.2 "Agreement" means this document together with its schedules.\n' +
      '2.3 "Confidential Information" means non-public data of either party.';
    const blocks = splitBlocks(ocr);
    expect(blocks.length).toBe(3);
    expect(blocks[0].text.startsWith("2.1")).toBe(true);
    expect(blocks[2].text.startsWith("2.3")).toBe(true);
    const hit = anchorEdit(blocks, {
      original_text: "affiliate is an entity under common control with a party",
    });
    expect(hit).toBe(blocks[0].id); // right clause, not a merged blob
  });

  it("anchors by exact block_id when present", () => {
    const blocks = splitBlocks(DOC);
    expect(anchorEdit(blocks, { block_id: blocks[1].id })).toBe(blocks[1].id);
  });

  it("anchors a legacy quote despite smart quotes / whitespace drift", () => {
    const blocks = splitBlocks(DOC);
    // Model quote: curly apostrophe + collapsed spacing — findSpan would miss.
    const q = "Each party shall protect the other’s   Confidential Information";
    // norm() folds whitespace but not the smart quote, so containment fails and
    // it must fall through to the token-overlap path — still the right block.
    expect(anchorEdit(blocks, { original_text: q })).toBe(blocks[2].id);
  });

  it("refuses to place an unrelated quote", () => {
    const blocks = splitBlocks(DOC);
    expect(
      anchorEdit(blocks, { original_text: "governing law of the State of Delaware" }),
    ).toBeNull();
  });
});
