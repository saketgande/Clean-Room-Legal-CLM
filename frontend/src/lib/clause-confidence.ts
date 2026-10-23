import { titleCase } from "@/lib/utils";

/** How sure the reading of a clause is, as the server states it: a word, never a number.
 *  The clause-search page used to multiply it by 100 and printed "NaN%" on every row. */
export type ClauseConfidence = "high" | "medium" | "low";

/** The label to show beside a clause hit; nothing when the server stated no confidence. */
export function clauseConfidenceLabel(value: ClauseConfidence | string | null | undefined): string {
  return typeof value === "string" ? titleCase(value) : "";
}
