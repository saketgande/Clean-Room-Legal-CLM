"use client";

// The contract's clause tree (GET /contracts/{id}/clauses). New uploads carry
// the tree the Documents reader built (parent_id); older contracts only have a
// depth per clause, so there each clause nests under the last shallower one.
// Clicking a clause shows it in the document.

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { contractsApi } from "@/lib/endpoints";
import type { ContractClause } from "@/lib/types";

type Node = ContractClause & { children: Node[] };

export function buildTree(clauses: ContractClause[], stored: boolean): Node[] {
  const nodes = clauses.map((c) => ({ ...c, children: [] as Node[] }));
  const roots: Node[] = [];
  if (stored) {
    const byId = new Map(nodes.map((n) => [n.id, n]));
    for (const n of nodes) (n.parent_id && byId.get(n.parent_id) ? byId.get(n.parent_id)!.children : roots).push(n);
    return roots;
  }
  const stack: Node[] = [];
  for (const n of nodes) {
    while (stack.length && stack[stack.length - 1].level >= n.level) stack.pop();
    (stack.length ? stack[stack.length - 1].children : roots).push(n);
    stack.push(n);
  }
  return roots;
}

export function ClauseTree({ contractId, selected, onPick }: {
  contractId: string;
  selected: string | null;
  onPick: (clause: ContractClause) => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["contract-clauses", contractId],
    queryFn: () => contractsApi.clauses(contractId),
  });
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
  const roots = useMemo(() => buildTree(data?.clauses ?? [], !!data?.tree), [data]);
  const needle = q.trim().toLowerCase();

  if (isLoading) return <div className="dim ct-empty">Loading clauses…</div>;
  if (!data?.clauses.length) return <div className="dim ct-empty">No clauses found in this version yet.</div>;

  const matches = (n: Node): boolean =>
    !needle || n.text.toLowerCase().includes(needle) || n.children.some(matches);
  const toggle = (id: string) => setClosed((s) => {
    const next = new Set(s);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const render = (nodes: Node[], depth: number) => nodes.filter(matches).map((n) => {
    const open = needle ? true : !closed.has(n.id);
    return (
      <li key={n.id}>
        <div className={`ct-row${n.id === selected ? " on" : ""}`} style={{ paddingLeft: 6 + depth * 14 }}>
          {n.children.length ? (
            <button className="ct-tw" aria-label={open ? "Collapse" : "Expand"} onClick={() => toggle(n.id)}>{open ? "▾" : "▸"}</button>
          ) : <span className="ct-tw" />}
          <button className={`ct-item${n.type === "heading" || (depth === 0 && n.children.length) ? " head" : ""}`} onClick={() => onPick(n)} title={n.text.slice(0, 300)}>
            {n.number ? <span className="ct-num">{n.number}</span> : null}
            <span className="ct-title">{n.title}</span>
            {n.page ? <span className="ct-page">p{n.page}</span> : null}
          </button>
        </div>
        {open && n.children.length ? <ul>{render(n.children, depth + 1)}</ul> : null}
      </li>
    );
  });

  return (
    <div className="ct">
      <div className="ct-hd">
        <input className="ct-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a clause…" aria-label="Find a clause" />
        <span className="dim ct-sub">
          v{data.version_number} · {data.clauses.length} parts{data.tree ? "" : " · nested by numbering"}
        </span>
      </div>
      <ul className="ct-list">{render(roots, 0)}</ul>
    </div>
  );
}

export const CLAUSE_TREE_CSS = `
.clm .ct{display:flex;flex-direction:column;gap:8px;padding:4px 2px;min-height:0}
.clm .ct-hd{display:flex;flex-direction:column;gap:5px}
.clm .ct-q{width:100%;border:1px solid var(--border-strong);border-radius:8px;padding:6px 9px;background:var(--surface);color:var(--ink);font-size:12.5px}
.clm .ct-sub{font-size:11px}
.clm .ct-empty{font-size:12.5px;padding:8px}
.clm .ct ul{list-style:none;margin:0;padding:0}
.clm .ct-row{display:flex;align-items:flex-start;gap:2px;border-radius:7px}
.clm .ct-row:hover{background:var(--surface-2)}
.clm .ct-row.on{background:var(--accent-soft)}
.clm .ct-tw{width:16px;flex:none;color:var(--ink-3);font-size:11px;padding-top:5px;text-align:center}
.clm .ct-item{flex:1;min-width:0;display:flex;align-items:baseline;gap:6px;padding:4px 4px;text-align:left;font-size:12.5px;color:var(--ink-2)}
.clm .ct-item.head{color:var(--ink);font-weight:620}
.clm .ct-num{flex:none;font:600 11px var(--mono);color:var(--ink-3)}
.clm .ct-row.on .ct-num{color:var(--accent)}
.clm .ct-title{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.clm .ct-page{flex:none;font-size:10.5px;color:var(--ink-3)}
.clm .ct-flash{background:var(--warn-soft);border-radius:3px;transition:background .4s}
`;
