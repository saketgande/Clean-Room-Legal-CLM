"use client";

// A "which / how many contracts" answer: the server ran the question over every
// contract the viewer can see, so show how many it checked, the full list with
// links, and what it could not check. Nothing here is the model's opinion.

import Link from "next/link";
import { MessageBar, Spinner, Table, TD, TH, THead, TR } from "@/components/ui";
import type { BrainQuery } from "@/lib/types";
import { coverageLine, isReading, readingLine } from "./coverage";

const LINK = "font-medium text-brand-600 hover:underline";

export function PortfolioAnswer({ m }: { m: BrainQuery["retrieval_metadata"] }) {
  const matches = m.matches ?? [];
  const unknown = m.unknown ?? [];
  const reading = isReading(m);
  const tableLink = m.reading?.started ? (
    <Link href={`/tabular-reviews/${m.reading.tabular_review_id}`} className={LINK}>Open the table</Link>
  ) : null;
  const waiting = readingLine(m);

  return (
    <div className="space-y-3">
      <MessageBar intent={reading ? "info" : m.complete ? "success" : "warning"}>{coverageLine(m)}</MessageBar>
      {m.conditions?.length ? <p className="text-[12px] text-slate-500">Checked for: {m.conditions.join(" · ")}</p> : null}
      {waiting ? (
        <MessageBar intent="info">
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1"><Spinner className="h-3.5 w-3.5" />{waiting}{tableLink}</span>
        </MessageBar>
      ) : tableLink ? (
        <p className="text-[12px] text-slate-500">The AI read each of these contracts. {tableLink}</p>
      ) : null}

      {matches.length > 0 ? (
        <Table>
          <THead>
            <TR><TH>Contract</TH><TH>Counterparty</TH><TH>Why it matches</TH></TR>
          </THead>
          <tbody>
            {matches.map((c) => (
              <TR key={c.contract_id}>
                <TD className="align-top"><Link href={`/contracts/${c.contract_id}`} className={LINK}>{c.title}</Link></TD>
                <TD className="align-top">{c.counterparty_name ?? "—"}</TD>
                <TD className="align-top">{c.why.map((w, i) => <div key={i}>{w}</div>)}</TD>
              </TR>
            ))}
          </tbody>
        </Table>
      ) : reading ? null : (
        <p className="text-[13px] text-slate-500">No contracts match.</p>
      )}

      {unknown.length > 0 && (
        <details className="rounded-md border border-slate-200 text-[13px]">
          <summary className="cursor-pointer px-3 py-2 font-medium text-slate-700">Could not check ({unknown.length})</summary>
          <ul className="divide-y divide-slate-200 px-3">
            {unknown.map((u) => (
              <li key={u.contract_id} className="flex flex-wrap gap-x-3 py-1.5">
                <Link href={`/contracts/${u.contract_id}`} className={LINK}>{u.title}</Link>
                <span className="text-slate-500">{u.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {m.not_checked?.length ? <p className="text-[13px] text-slate-500">Not checked: {m.not_checked.join("; ")}</p> : null}
    </div>
  );
}
