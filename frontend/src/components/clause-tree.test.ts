import { describe, expect, it } from "vitest";
import { buildTree } from "./clause-tree";
import type { ContractClause } from "@/lib/types";

const c = (id: string, level: number, parent_id: string | null = null): ContractClause => ({
  id, parent_id, seq: 0, type: "clause", level, number: id, page: null, title: id, text: id,
});

// Guards the two ways the outline could mislead a reader: a clause shown under
// the wrong section, or clauses silently dropped from the tree.
describe("buildTree", () => {
  it("nests an older contract by depth: each clause under the last shallower one", () => {
    const roots = buildTree([c("1", 1), c("1.1", 2), c("(a)", 3), c("1.2", 2), c("2", 1), c("2.1", 2)], false);
    expect(roots.map((n) => n.id)).toEqual(["1", "2"]);
    expect(roots[0].children.map((n) => n.id)).toEqual(["1.1", "1.2"]);
    expect(roots[0].children[0].children.map((n) => n.id)).toEqual(["(a)"]);
    expect(roots[1].children.map((n) => n.id)).toEqual(["2.1"]);
  });

  it("follows the stored tree when the reader built one, and loses no clause", () => {
    const roots = buildTree([c("1", 1), c("1.1", 2, "1"), c("x", 2, "missing-parent"), c("2", 1)], true);
    expect(roots.map((n) => n.id)).toEqual(["1", "x", "2"]); // an unknown parent falls back to the top
    expect(roots[0].children.map((n) => n.id)).toEqual(["1.1"]);
  });
});
