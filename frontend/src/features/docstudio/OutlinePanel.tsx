"use client";

// The outline: the clause tree, as `tree.py` and `hierarchy.py` decided it.
//
// Worth saying plainly, because it is the part no competitor ships: every row
// can state WHO put it there. A clause placed by decimal numbering is a fact
// read out of the document; one placed by the model is a judgement that could
// be wrong. Showing the difference costs one dot and is the honest thing to do.

import { useMemo, useState } from "react";
import clsx from "clsx";
import { ChevronDown, ChevronRight, Search } from "lucide-react";
import type { Clause, ClauseNode, StructureSource } from "./types";
import { buildTree } from "./types";

const SOURCE_LABEL: Record<StructureSource, string> = {
  document: "the file's own levels",
  numbering: "decimal numbering",
  list: "list order",
  part: "an exhibit or schedule",
  top: "the top level",
  layout: "indentation",
  ai: "the model, from a closed list",
  undecided: "nothing — still undecided",
};

/** Only placements that were judged rather than read get a colour. */
function sourceDot(source: StructureSource | null) {
  if (source === "ai") return "bg-info";
  if (source === "undecided") return "bg-warning";
  return null;
}

export function OutlinePanel({
  clauses,
  selectedId,
  onSelect,
}: {
  clauses: Clause[];
  selectedId: string | null;
  onSelect: (clauseId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const tree = useMemo(() => buildTree(clauses), [clauses]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return null;
    const hit = new Set<string>();
    const byId = new Map(clauses.map((c) => [c.clause_id, c]));
    for (const c of clauses) {
      if (
        c.text.toLowerCase().includes(q) ||
        (c.number_label ?? "").toLowerCase().includes(q)
      ) {
        hit.add(c.clause_id);
        // Keep a match reachable by also showing everything above it.
        let parent = c.parent_clause_id;
        while (parent) {
          hit.add(parent);
          parent = byId.get(parent)?.parent_clause_id ?? null;
        }
      }
    }
    return hit;
  }, [clauses, query]);

  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="flex h-full min-h-0 flex-col border-r border-slate-200 bg-slate-100">
      <div className="border-b border-slate-200 px-3 py-2.5">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find a clause"
            aria-label="Find a clause"
            className="w-full rounded border border-slate-300 bg-slate-50 py-1.5 pl-8 pr-2 text-xs text-slate-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
        </div>
        {matches ? (
          <p className="mt-1.5 text-[11px] text-slate-500">
            {matches.size === 0 ? "Nothing matched" : `${matches.size} shown`}
          </p>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-auto py-1">
        {tree.map((node) => (
          <Row
            key={node.clause_id}
            node={node}
            depth={0}
            selectedId={selectedId}
            collapsed={collapsed}
            matches={matches}
            onSelect={onSelect}
            onToggle={toggle}
          />
        ))}
      </div>
    </div>
  );
}

function Row({
  node,
  depth,
  selectedId,
  collapsed,
  matches,
  onSelect,
  onToggle,
}: {
  node: ClauseNode;
  depth: number;
  selectedId: string | null;
  collapsed: Set<string>;
  matches: Set<string> | null;
  onSelect: (clauseId: string) => void;
  onToggle: (clauseId: string) => void;
}) {
  if (matches && !matches.has(node.clause_id)) return null;

  const isCollapsed = collapsed.has(node.clause_id);
  const hasChildren = node.children.length > 0;
  const selected = node.clause_id === selectedId;
  const dot = sourceDot(node.structure_source);
  const snippet = node.text.replace(/\s+/g, " ").slice(0, 90);

  return (
    <>
      <div
        className={clsx(
          "group flex items-start gap-1 pr-2",
          selected ? "bg-brand-50" : "hover:bg-slate-200/60",
        )}
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
      >
        <button
          type="button"
          onClick={() => onToggle(node.clause_id)}
          className={clsx(
            "mt-1.5 flex h-4 w-4 shrink-0 items-center justify-center rounded text-slate-400 hover:text-slate-700",
            !hasChildren && "invisible",
          )}
          aria-label={isCollapsed ? "Expand" : "Collapse"}
          aria-expanded={hasChildren ? !isCollapsed : undefined}
        >
          {isCollapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        </button>

        <button
          type="button"
          onClick={() => onSelect(node.clause_id)}
          className="flex min-w-0 flex-1 items-start gap-1.5 py-1 text-left"
        >
          {dot ? (
            <span
              className={clsx("mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full", dot)}
              title={`Placed by ${SOURCE_LABEL[node.structure_source as StructureSource]}`}
            />
          ) : (
            <span className="mt-[7px] h-1.5 w-1.5 shrink-0" />
          )}
          <span className="min-w-0 flex-1">
            <span
              className={clsx(
                "block truncate text-xs",
                selected ? "font-semibold text-brand-700" : "text-slate-700",
                node.clause_type === "heading" && !selected && "font-medium text-slate-900",
              )}
            >
              {node.number_label ? (
                <span className="tabular-nums text-slate-500">{node.number_label} </span>
              ) : null}
              {snippet}
            </span>
          </span>
          <span className="mt-0.5 shrink-0 text-[10px] tabular-nums text-slate-400">
            {node.page_number ?? "–"}
          </span>
        </button>
      </div>

      {!isCollapsed &&
        node.children.map((child) => (
          <Row
            key={child.clause_id}
            node={child}
            depth={depth + 1}
            selectedId={selectedId}
            collapsed={collapsed}
            matches={matches}
            onSelect={onSelect}
            onToggle={onToggle}
          />
        ))}
    </>
  );
}
