// Pure, dependency-free org-unit tree helpers — no React, no HTTP.
//
// Why this exists: the org-structure admin screen (FR-26) needs to render a
// flat list of org units (as returned by GET /org-units) as a nested tree and
// as an indented flat list, and the re-parent picker (T010) needs to exclude
// a unit and its own descendants from the "new parent" choices so a cycle
// can't even be proposed client-side (the server's cycle check per FR-3 stays
// authoritative — this is UX only).
//
// NOTE on types: `OrgUnitResponse`/an org-unit tree-node type are expected to
// land in `src/lib/types.ts` via a concurrent task (T006). If that hasn't
// landed yet, `OrgUnitLike` below is a minimal local shape sufficient for
// these functions' signatures — swap the import once T006 lands.
export interface OrgUnitLike {
  id: string;
  parent_id: string | null;
  name: string;
  // Other OrgUnitResponse fields (org_id, is_root, depth, path_names,
  // child_count, active_grant_count, deleted_at, created_at, updated_at) are
  // irrelevant to these pure tree functions, so this shape only requires
  // what's used here; callers may pass the full OrgUnitResponse — this
  // interface is structurally compatible with it.
}

export interface OrgTreeNode<T extends OrgUnitLike = OrgUnitLike> {
  unit: T;
  children: OrgTreeNode<T>[];
}

// Matches the resolver's own depth-cap-at-64 convention (see plan.md, backend
// ancestry walk) so a corrupt/cyclic server response can't hang the browser
// tab walking a tree forever.
const MAX_DEPTH = 64;

// Build a nested tree from a flat list. Root nodes are units whose
// `parent_id` is null OR points to a unit not present in `units` (defensive:
// never silently drop a unit because its parent id is missing/stale).
// Cycle-safe: a node is only ever attached once, at its first-seen position
// in the top-down pass, and recursion below is depth-capped.
export function buildOrgTree<T extends OrgUnitLike>(units: T[]): OrgTreeNode<T>[] {
  const byId = new Map<string, T>();
  for (const u of units) byId.set(u.id, u);

  const childrenOf = new Map<string, T[]>();
  const roots: T[] = [];
  for (const u of units) {
    if (u.parent_id && byId.has(u.parent_id) && u.parent_id !== u.id) {
      const list = childrenOf.get(u.parent_id) ?? [];
      list.push(u);
      childrenOf.set(u.parent_id, list);
    } else {
      roots.push(u);
    }
  }

  const build = (unit: T, depth: number): OrgTreeNode<T> => {
    const children =
      depth >= MAX_DEPTH
        ? []
        : (childrenOf.get(unit.id) ?? []).map((c) => build(c, depth + 1));
    return { unit, children };
  };

  return roots.map((r) => build(r, 0));
}

// Flatten a unit list into depth-first tree order with a computed `depth`
// (0 = root), for rendering an indented flat table/list.
export function flattenWithIndent<T extends OrgUnitLike>(
  units: T[],
): Array<{ unit: T; depth: number }> {
  const tree = buildOrgTree(units);
  const out: Array<{ unit: T; depth: number }> = [];

  const walk = (nodes: OrgTreeNode<T>[], depth: number) => {
    if (depth > MAX_DEPTH) return;
    for (const node of nodes) {
      out.push({ unit: node.unit, depth });
      walk(node.children, depth + 1);
    }
  };

  walk(tree, 0);
  return out;
}

// True if `candidateId` is `ancestorId` itself, or anywhere in its descendant
// subtree. Used to exclude "self and all descendants" from a re-parent target
// picker. Cycle-safe via a visited set + depth cap, so a malformed input
// (parent_id pointing into its own descendant chain) terminates instead of
// looping forever.
export function isDescendantOf(
  units: OrgUnitLike[],
  candidateId: string,
  ancestorId: string,
): boolean {
  if (candidateId === ancestorId) return true;

  const byId = new Map(units.map((u) => [u.id, u]));
  const visited = new Set<string>();
  let current = byId.get(candidateId);
  let depth = 0;

  while (current && current.parent_id != null && depth < MAX_DEPTH) {
    if (visited.has(current.id)) return false; // cycle guard
    visited.add(current.id);

    if (current.parent_id === ancestorId) return true;
    current = byId.get(current.parent_id);
    depth++;
  }

  return false;
}
