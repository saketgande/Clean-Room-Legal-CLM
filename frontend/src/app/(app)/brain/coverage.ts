import type { BrainQuery } from "@/lib/types";

type Meta = BrainQuery["retrieval_metadata"];

/** A which/how-many answer the server computed over every contract, with the
 * counts to show. A viewer who can't see every contract in it gets the
 * ordinary answer text instead (withheld_for_viewer). */
export function hasCoverage(m: Meta): boolean {
  return m.answer_mode === "database_filter" && typeof m.total === "number" && !m.withheld_for_viewer;
}

/** True while the AI is still reading contract wording for this answer. `done`
 * already counts contracts whose read failed (the server's reading_finished uses
 * the same rule), so one bad read never keeps the page polling. */
export function isReading(m: Meta): boolean {
  const r = m.reading;
  return !!r && r.started && r.done < r.contracts;
}

const cost = (usd: number) => (usd < 0.01 ? "under $0.01" : `about $${usd.toFixed(2)}`);

/** "Checked 30 of 32 contracts · 12 match · 2 could not be checked". */
export function coverageLine(m: Meta): string {
  const total = m.total ?? 0;
  const parts = [
    `Checked ${m.checked ?? 0} of ${total} contract${total === 1 ? "" : "s"}`,
    `${m.matched ?? m.matches?.length ?? 0} match`,
  ];
  if (isReading(m)) parts.push("still being read");
  else if (m.complete) parts.push("complete");
  else {
    const unknown = m.unknown?.length ?? 0;
    if (unknown > 0) parts.push(`${unknown} could not be checked`);
    if (m.not_checked?.length) parts.push("part of the question was not checked");
  }
  return parts.join(" · ");
}

/** "The AI is reading 12 of 20 contracts (about $0.84)" — null when it isn't. */
export function readingLine(m: Meta): string | null {
  const r = m.reading;
  if (!r || !isReading(m)) return null;
  return `The AI is reading ${r.done} of ${r.contracts} contracts (${cost(r.estimate_usd)})`;
}
