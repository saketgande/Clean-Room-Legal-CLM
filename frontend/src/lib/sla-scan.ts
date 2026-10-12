/** What a breach scan did, in a sentence for the toast. */
export function describeSlaScan(r: { escalated: number; breached: number; completed?: number }): string {
  const parts = [`${r.breached} newly overdue`, `${r.escalated} escalated`];
  if (r.completed) parts.push(`${r.completed} finished ${r.completed === 1 ? "request" : "requests"} closed`);
  return `Scan: ${parts.join(", ")}`;
}
