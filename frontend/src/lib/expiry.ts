import type { ContractResponse } from "./types";

/** How far ahead "expiring" looks. Contracts and Renewals both use this, so their counts can be compared. */
export const EXPIRY_WINDOW_DAYS = 90;

type Shape = Pick<ContractResponse, "expiration_date" | "lifecycle_stage">;

/** Whole days from now until the contract's end date (negative once it has passed), or null with no end date. */
export function daysToExpiry(c: Pick<ContractResponse, "expiration_date">, now: number = Date.now()): number | null {
  if (!c.expiration_date) return null;
  return Math.round((new Date(`${c.expiration_date}T00:00:00`).getTime() - now) / 86_400_000);
}

/** A signed, running agreement that ends within the window.
 *
 *  Only ACTIVE contracts count: renewal tracking starts when a contract becomes active, and a draft or
 *  a contract still in negotiation has not started, so it is not "expiring". The Contracts screen used
 *  to count every contract that was not closed, so it showed expiring contracts the Renewals screen
 *  (which only knows active ones) could never list. */
export function isExpiringSoon(c: Shape, now: number = Date.now()): boolean {
  if (c.lifecycle_stage !== "active") return false;
  const d = daysToExpiry(c, now);
  return d != null && d >= 0 && d <= EXPIRY_WINDOW_DAYS;
}

/** Running contracts that end within the window but have no row on the Renewals list, soonest first.
 *  A contract only gets a renewal record once its renewal terms have been read, so these are the ones
 *  the Contracts screen counts and the Renewals screen cannot show. */
export function expiringWithoutRenewal<C extends Shape & Pick<ContractResponse, "id">>(
  contracts: readonly C[],
  renewals: readonly { contract_id: string }[],
  now: number = Date.now(),
): C[] {
  const tracked = new Set(renewals.map((r) => r.contract_id));
  return contracts
    .filter((c) => isExpiringSoon(c, now) && !tracked.has(c.id))
    .sort((a, b) => (daysToExpiry(a, now) ?? 0) - (daysToExpiry(b, now) ?? 0));
}
