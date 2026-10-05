import { describe, it, expect } from "vitest";
import { buildOrgTree, flattenWithIndent, isDescendantOf, type OrgUnitLike } from "./org-tree";

// Fixture: Global -> Region A -> Entity 1 -> BU 1, plus sibling Region B.
const GLOBAL: OrgUnitLike = { id: "global", parent_id: null, name: "Global" };
const REGION_A: OrgUnitLike = { id: "region-a", parent_id: "global", name: "Region A" };
const REGION_B: OrgUnitLike = { id: "region-b", parent_id: "global", name: "Region B" };
const ENTITY_1: OrgUnitLike = { id: "entity-1", parent_id: "region-a", name: "Entity 1" };
const BU_1: OrgUnitLike = { id: "bu-1", parent_id: "entity-1", name: "BU 1" };

const UNITS = [GLOBAL, REGION_A, REGION_B, ENTITY_1, BU_1];

describe("buildOrgTree", () => {
  it("nests units correctly under a single root", () => {
    const tree = buildOrgTree(UNITS);
    expect(tree).toHaveLength(1);
    expect(tree[0].unit.id).toBe("global");
    expect(tree[0].children.map((c) => c.unit.id).sort()).toEqual(["region-a", "region-b"]);

    const regionANode = tree[0].children.find((c) => c.unit.id === "region-a")!;
    expect(regionANode.children).toHaveLength(1);
    expect(regionANode.children[0].unit.id).toBe("entity-1");
    expect(regionANode.children[0].children).toHaveLength(1);
    expect(regionANode.children[0].children[0].unit.id).toBe("bu-1");
    expect(regionANode.children[0].children[0].children).toEqual([]);

    const regionBNode = tree[0].children.find((c) => c.unit.id === "region-b")!;
    expect(regionBNode.children).toEqual([]);
  });

  it("treats a unit whose parent_id is missing from the list as a root", () => {
    const orphan: OrgUnitLike = { id: "orphan", parent_id: "does-not-exist", name: "Orphan" };
    const tree = buildOrgTree([...UNITS, orphan]);
    expect(tree.map((n) => n.unit.id)).toContain("orphan");
  });

  it("treats a unit whose parent_id is itself as a root (defensive)", () => {
    const selfParent: OrgUnitLike = { id: "self", parent_id: "self", name: "Self" };
    const tree = buildOrgTree([selfParent]);
    expect(tree).toHaveLength(1);
    expect(tree[0].unit.id).toBe("self");
    expect(tree[0].children).toEqual([]);
  });
});

describe("flattenWithIndent", () => {
  it("produces depth-first order with correct depths", () => {
    const flat = flattenWithIndent(UNITS);
    const byId = (id: string) => flat.find((f) => f.unit.id === id)!;

    expect(byId("global").depth).toBe(0);
    expect(byId("region-a").depth).toBe(1);
    expect(byId("region-b").depth).toBe(1);
    expect(byId("entity-1").depth).toBe(2);
    expect(byId("bu-1").depth).toBe(3);

    // depth-first: global, then all of region-a's subtree before region-b
    const ids = flat.map((f) => f.unit.id);
    expect(ids[0]).toBe("global");
    const regionAIdx = ids.indexOf("region-a");
    const entity1Idx = ids.indexOf("entity-1");
    const bu1Idx = ids.indexOf("bu-1");
    const regionBIdx = ids.indexOf("region-b");
    expect(regionAIdx).toBeLessThan(entity1Idx);
    expect(entity1Idx).toBeLessThan(bu1Idx);
    expect(bu1Idx).toBeLessThan(regionBIdx);
  });
});

describe("isDescendantOf", () => {
  it("is true for a unit and itself", () => {
    expect(isDescendantOf(UNITS, "entity-1", "entity-1")).toBe(true);
  });

  it("is true for a direct child", () => {
    expect(isDescendantOf(UNITS, "entity-1", "region-a")).toBe(true);
  });

  it("is true for a deep descendant", () => {
    expect(isDescendantOf(UNITS, "bu-1", "global")).toBe(true);
    expect(isDescendantOf(UNITS, "bu-1", "region-a")).toBe(true);
  });

  it("is false for an ancestor checked against its descendant (no reverse rollup)", () => {
    expect(isDescendantOf(UNITS, "global", "bu-1")).toBe(false);
    expect(isDescendantOf(UNITS, "region-a", "entity-1")).toBe(false);
  });

  it("is false for siblings and unrelated units", () => {
    expect(isDescendantOf(UNITS, "region-b", "region-a")).toBe(false);
    expect(isDescendantOf(UNITS, "region-a", "region-b")).toBe(false);
    expect(isDescendantOf(UNITS, "bu-1", "region-b")).toBe(false);
  });

  it("does not hang or crash on a malformed cyclic input", () => {
    // Deliberately corrupt: A's parent is B, B's parent is A (A is its own
    // descendant's descendant).
    const cyclic: OrgUnitLike[] = [
      { id: "a", parent_id: "b", name: "A" },
      { id: "b", parent_id: "a", name: "B" },
    ];
    expect(() => isDescendantOf(cyclic, "a", "b")).not.toThrow();
    expect(() => isDescendantOf(cyclic, "b", "a")).not.toThrow();
    // Both directions terminate — result value isn't the point, termination is.
    expect(typeof isDescendantOf(cyclic, "a", "b")).toBe("boolean");
  });

  it("buildOrgTree and flattenWithIndent also terminate on a cyclic input", () => {
    const cyclic: OrgUnitLike[] = [
      { id: "a", parent_id: "c", name: "A" },
      { id: "b", parent_id: "a", name: "B" },
      { id: "c", parent_id: "b", name: "C" },
    ];
    expect(() => buildOrgTree(cyclic)).not.toThrow();
    expect(() => flattenWithIndent(cyclic)).not.toThrow();
  });
});
